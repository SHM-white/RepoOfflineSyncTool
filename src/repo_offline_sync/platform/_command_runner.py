"""Subprocess process-group execution mechanics."""

from __future__ import annotations

import os
import select
import signal
import subprocess
from dataclasses import dataclass, field
from enum import Enum

from repo_offline_sync.platform._command_start import invalid_reason, spawn
from repo_offline_sync.platform.command_types import (
    CancellationToken,
    CommandCancelled,
    CommandCompleted,
    CommandInterrupted,
    CommandInvalid,
    CommandOutcome,
    CommandOutputOverflow,
    CommandRequest,
    CommandStartFailed,
    CommandTeardownFailed,
    CommandTimedOut,
)
from repo_offline_sync.platform.runtime import Clock, SystemClock


class _StopReason(Enum):
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    OVERFLOW = "overflow"
    INTERRUPTED = "interrupted"


@dataclass(slots=True)
class _OutputCapture:
    """Accumulate bounded bytes while a command runs."""

    stdout_descriptor: int
    stderr_descriptor: int
    limit: int
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    retained: int = 0

    def read(self, descriptor: int) -> tuple[bool, bool]:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            return True, False
        remaining = self.limit - self.retained
        target = self.stdout if descriptor == self.stdout_descriptor else self.stderr
        target.extend(chunk[:remaining])
        self.retained += min(len(chunk), remaining)
        return False, len(chunk) > remaining


@dataclass(frozen=True, slots=True)
class _Captured:
    reason: _StopReason | None
    stdout: bytes
    stderr: bytes
    interrupt_errno: int | None
    teardown_complete: bool


@dataclass(slots=True)
class _WaitState:
    """Wait for process exit and output EOF under one deadline."""

    process: subprocess.Popen[bytes]
    cancellation: CancellationToken | None
    clock: Clock
    output: _OutputCapture
    active: set[int]
    process_descriptor: int
    deadline: float
    interrupt_errno: int | None = None

    def await_completion(self) -> _StopReason | None:
        while self.active or self.process.poll() is None:
            process_running = self.process.poll() is None
            if self.cancellation is not None and self.cancellation.cancelled:
                return _StopReason.CANCELLED
            remaining = self.deadline - self.clock.monotonic()
            if remaining <= 0:
                return _StopReason.TIMEOUT
            descriptors = [*self.active]
            if process_running:
                descriptors.append(self.process_descriptor)
            if self.cancellation is not None:
                descriptors.append(self.cancellation.fileno())
            try:
                ready, _, _ = select.select(descriptors, (), (), remaining)
            except InterruptedError as error:
                self.interrupt_errno = error.errno
                return _StopReason.INTERRUPTED
            if self.process_descriptor in ready:
                ready.remove(self.process_descriptor)
            cancellation_descriptor = (
                None if self.cancellation is None else self.cancellation.fileno()
            )
            reason = _consume_ready(
                ready, self.output, self.active, cancellation_descriptor
            )
            if reason is not None:
                return reason
        return None


def _group_exists(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    return True


def _terminate_group(process: subprocess.Popen[bytes], grace_seconds: float) -> bool:
    group_id = process.pid
    try:
        os.killpg(group_id, signal.SIGTERM)
    except ProcessLookupError:
        return True
    try:
        _ = process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
    else:
        timed_out = False
    if timed_out or _group_exists(group_id):
        try:
            os.killpg(group_id, signal.SIGKILL)
        except ProcessLookupError:
            return True
    try:
        _ = process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        return False
    return not _group_exists(group_id)


def _drain(output: _OutputCapture, active: set[int]) -> None:
    while active:
        ready, _, _ = select.select([*active], (), (), 0)
        if not ready:
            return
        for descriptor in ready:
            end_of_file, _ = output.read(descriptor)
            if end_of_file:
                active.remove(descriptor)


def _consume_ready(
    ready: list[int],
    output: _OutputCapture,
    active: set[int],
    cancellation_descriptor: int | None,
) -> _StopReason | None:
    for descriptor in ready:
        if descriptor == cancellation_descriptor:
            return _StopReason.CANCELLED
        end_of_file, overflow = output.read(descriptor)
        if end_of_file:
            active.remove(descriptor)
        if overflow:
            return _StopReason.OVERFLOW
    return None


def _capture(
    request: CommandRequest,
    process: subprocess.Popen[bytes],
    cancellation: CancellationToken | None,
    clock: Clock,
) -> _Captured:
    stdout_pipe = process.stdout
    stderr_pipe = process.stderr
    if stdout_pipe is None or stderr_pipe is None:
        complete = _terminate_group(process, request.termination_grace_seconds)
        return _Captured(None, b"", b"", None, complete)
    output = _OutputCapture(
        stdout_pipe.fileno(), stderr_pipe.fileno(), request.output_limit_bytes
    )
    try:
        process_descriptor = os.pidfd_open(process.pid)
    except OSError as error:
        complete = _terminate_group(process, request.termination_grace_seconds)
        stdout_pipe.close()
        stderr_pipe.close()
        return _Captured(_StopReason.INTERRUPTED, b"", b"", error.errno, complete)
    active = {output.stdout_descriptor, output.stderr_descriptor}
    wait_state = _WaitState(
        process,
        cancellation,
        clock,
        output,
        active,
        process_descriptor,
        clock.monotonic() + request.timeout_seconds,
    )
    try:
        reason = wait_state.await_completion()
    finally:
        os.close(process_descriptor)
    complete = True
    if reason is not None:
        complete = _terminate_group(process, request.termination_grace_seconds)
        _drain(output, active)
    stdout_pipe.close()
    stderr_pipe.close()
    return _Captured(
        reason,
        bytes(output.stdout),
        bytes(output.stderr),
        wait_state.interrupt_errno,
        complete,
    )


def _outcome(
    process: subprocess.Popen[bytes],
    captured: _Captured,
    limit: int,
) -> CommandOutcome:
    if not captured.teardown_complete:
        return CommandTeardownFailed(captured.stdout, captured.stderr)
    match captured.reason:
        case None:
            returncode = process.poll()
            return (
                CommandTeardownFailed(captured.stdout, captured.stderr)
                if returncode is None
                else CommandCompleted(returncode, captured.stdout, captured.stderr)
            )
        case _StopReason.TIMEOUT:
            return CommandTimedOut(captured.stdout, captured.stderr)
        case _StopReason.CANCELLED:
            return CommandCancelled(captured.stdout, captured.stderr)
        case _StopReason.OVERFLOW:
            return CommandOutputOverflow(captured.stdout, captured.stderr, limit)
        case _StopReason.INTERRUPTED:
            return CommandInterrupted(
                captured.stdout, captured.stderr, captured.interrupt_errno
            )


def run_command(
    request: CommandRequest,
    *,
    cancellation: CancellationToken | None,
    clock: Clock | None,
) -> CommandOutcome:
    """Execute a validated request and own its process group to completion."""
    invalid = invalid_reason(request)
    if invalid is not None:
        return CommandInvalid(invalid)
    if cancellation is not None and cancellation.cancelled:
        return CommandCancelled(b"", b"")
    process, failure = spawn(request)
    if failure is not None:
        return failure
    if process is None:
        return CommandStartFailed(None)
    active_clock = SystemClock() if clock is None else clock
    return _outcome(
        process,
        _capture(request, process, cancellation, active_clock),
        request.output_limit_bytes,
    )
