from __future__ import annotations

import queue
import socket
import sys
import threading
from pathlib import Path
from typing import final

import pytest

from repo_offline_sync.platform.commands import (
    CancellationToken,
    CommandCancelled,
    CommandCompleted,
    CommandInvalid,
    CommandInvalidReason,
    CommandOutcome,
    CommandOutputOverflow,
    CommandRequest,
    CommandStartFailed,
    CommandTimedOut,
    run_command,
)


@final
class ImmediateDeadlineClock:
    """Advance beyond the deadline after the runner records its start time."""

    __slots__: tuple[str, ...] = ("_sample",)
    _sample: int

    def __init__(self) -> None:
        self._sample = 0

    def monotonic(self) -> float:
        self._sample += 1
        return 0.0 if self._sample == 1 else 2.0

    def wall_time(self) -> float:
        return 0.0


def _request(
    tmp_path: Path,
    script: str,
    *,
    arguments: tuple[str, ...] = (),
    timeout: float = 5.0,
    output_limit: int = 64 * 1024,
) -> CommandRequest:
    return CommandRequest(
        argv=(sys.executable, "-c", script, *arguments),
        cwd=tmp_path,
        environment=(("MARKER", "exact value"),),
        timeout_seconds=timeout,
        output_limit_bytes=output_limit,
        termination_grace_seconds=1.0,
    )


def _pid_is_live(pid: int) -> bool:
    status_path = Path(f"/proc/{pid}/status")
    if not status_path.exists():
        return False
    state_line = next(
        line
        for line in status_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("State:")
    )
    return "Z" not in state_line


def test_command_preserves_argv_cwd_environment_and_captured_streams(
    tmp_path: Path,
) -> None:
    # Given arguments containing shell metacharacters and an explicit environment
    script = (
        "import os,sys;"
        "os.write(1,('\\0'.join([sys.argv[1],os.getcwd(),os.environ['MARKER']])).encode());"
        "os.write(2,b'stderr\\n')"
    )
    argument = "; printf interpolated"

    # When the typed argv boundary runs the command
    outcome = run_command(_request(tmp_path, script, arguments=(argument,)))

    # Then no shell parses the argument and both byte streams are captured
    assert isinstance(outcome, CommandCompleted)
    assert outcome.stdout == f"{argument}\0{tmp_path}\0exact value".encode()
    assert outcome.stderr == b"stderr\n"
    assert outcome.returncode == 0


def test_nonzero_command_cannot_claim_success_by_output_text(tmp_path: Path) -> None:
    # Given a child that prints success but exits nonzero
    request = _request(tmp_path, "print('success');raise SystemExit(7)")

    # When the command completes
    outcome = run_command(request)

    # Then the typed return code remains authoritative
    assert isinstance(outcome, CommandCompleted)
    assert outcome.returncode == 7
    assert outcome.stdout == b"success\n"


def test_fake_monotonic_deadline_terminates_child_group(tmp_path: Path) -> None:
    # Given a child blocked indefinitely and a clock that crosses its deadline
    request = _request(tmp_path, "import signal;signal.pause()", timeout=1.0)

    # When the injected monotonic clock expires
    outcome = run_command(request, clock=ImmediateDeadlineClock())

    # Then timeout is a typed outcome rather than an exception
    assert isinstance(outcome, CommandTimedOut)


def test_timeout_terminates_child_and_descendant_by_pid(tmp_path: Path) -> None:
    # Given a child that reaps its descendant when their process group is terminated
    script = """
import signal
import subprocess
import sys

descendant = subprocess.Popen([sys.executable, "-c", "import signal;signal.pause()"])
def terminate(_signum, _frame):
    descendant.wait()
    raise SystemExit(143)
signal.signal(signal.SIGTERM, terminate)
print(f"{os.getpid()} {descendant.pid}", flush=True)
signal.pause()
"""
    script = "import os\n" + script

    # When the deadline expires
    outcome = run_command(_request(tmp_path, script, timeout=0.2))

    # Then the result is timed out and neither recorded PID remains live
    assert isinstance(outcome, CommandTimedOut)
    child_pid, descendant_pid = (int(value) for value in outcome.stdout.split())
    assert not _pid_is_live(child_pid)
    assert not _pid_is_live(descendant_pid)


def test_cross_thread_cancellation_terminates_ready_process_group(
    tmp_path: Path,
) -> None:
    # Given a child with a descendant that signals readiness over a Unix socket
    socket_path = tmp_path / "ready.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    script = """
import os
import signal
import socket
import subprocess
import sys

descendant = subprocess.Popen([sys.executable, "-c", "import signal;signal.pause()"])
def terminate(_signum, _frame):
    descendant.wait()
    raise SystemExit(143)
signal.signal(signal.SIGTERM, terminate)
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as ready:
    ready.connect(sys.argv[1])
    ready.sendall(b"R")
print(f"{os.getpid()} {descendant.pid}", flush=True)
signal.pause()
"""
    outcomes: queue.Queue[CommandOutcome] = queue.Queue()

    with CancellationToken() as cancellation:
        worker = threading.Thread(
            target=lambda: outcomes.put(
                run_command(
                    _request(tmp_path, script, arguments=(str(socket_path),)),
                    cancellation=cancellation,
                )
            )
        )
        worker.start()
        connection = listener.accept()[0]
        with connection:
            assert connection.recv(1) == b"R"

        # When cancellation is requested after process-group readiness
        cancellation.cancel()
        cancellation.cancel()
        worker.join(timeout=5)

    listener.close()

    # Then cancellation is typed and both PIDs are no longer live
    assert not worker.is_alive()
    outcome = outcomes.get_nowait()
    assert isinstance(outcome, CommandCancelled)
    child_pid, descendant_pid = (int(value) for value in outcome.stdout.split())
    assert not _pid_is_live(child_pid)
    assert not _pid_is_live(descendant_pid)


def test_noisy_child_returns_bounded_overflow_and_is_terminated(tmp_path: Path) -> None:
    # Given a child that writes without bound to both captured streams
    script = "import os\nwhile True:\n os.write(1,b'x'*4096)\n os.write(2,b'y'*4096)"

    # When captured output reaches the configured combined cap
    outcome = run_command(_request(tmp_path, script, output_limit=1024))

    # Then overflow is typed and retained bytes never exceed the cap
    assert isinstance(outcome, CommandOutputOverflow)
    assert len(outcome.stdout) + len(outcome.stderr) == 1024


@pytest.mark.parametrize(
    ("command_request", "reason"),
    [
        (
            CommandRequest((), Path("/"), (), 1.0, 1, 1.0),
            CommandInvalidReason.EMPTY_ARGV,
        ),
        (
            CommandRequest(("echo",), Path("/"), (), 0.0, 1, 1.0),
            CommandInvalidReason.INVALID_TIMEOUT,
        ),
        (
            CommandRequest(("echo",), Path("/"), (), 1.0, 0, 1.0),
            CommandInvalidReason.INVALID_OUTPUT_LIMIT,
        ),
        (
            CommandRequest(("echo",), Path("/"), (("BAD=KEY", "x"),), 1.0, 1, 1.0),
            CommandInvalidReason.INVALID_ENVIRONMENT,
        ),
    ],
)
def test_malformed_command_request_returns_typed_invalid_outcome(
    command_request: CommandRequest,
    reason: CommandInvalidReason,
) -> None:
    # Given a malformed command boundary value
    # When execution is requested
    outcome = run_command(command_request)

    # Then no process starts and the exact invalid field is typed
    assert isinstance(outcome, CommandInvalid)
    assert outcome.reason is reason


def test_invalid_working_directory_returns_typed_start_failure(tmp_path: Path) -> None:
    # Given an otherwise valid request with a missing working directory
    request = _request(tmp_path / "missing", "print('unreachable')")

    # When process creation is attempted
    outcome = run_command(request)

    # Then the OS failure is represented without a broad exception
    assert isinstance(outcome, CommandStartFailed)
    assert outcome.errno is not None
