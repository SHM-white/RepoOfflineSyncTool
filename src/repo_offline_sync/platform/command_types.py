"""Typed subprocess requests, outcomes, and cancellation."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, TypeAlias, final

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from typing_extensions import Self


class CommandInvalidReason(Enum):
    """Describe a rejected command request field."""

    EMPTY_ARGV = "empty_argv"
    INVALID_TIMEOUT = "invalid_timeout"
    INVALID_OUTPUT_LIMIT = "invalid_output_limit"
    INVALID_GRACE_PERIOD = "invalid_grace_period"
    INVALID_ENVIRONMENT = "invalid_environment"


@dataclass(frozen=True, slots=True)
class CommandRequest:
    """Fully explicit subprocess execution inputs."""

    argv: tuple[str, ...]
    cwd: Path
    environment: tuple[tuple[str, str], ...]
    timeout_seconds: float
    output_limit_bytes: int
    termination_grace_seconds: float


@dataclass(frozen=True, slots=True)
class CommandCompleted:
    """Report a normally reaped command, including nonzero exits."""

    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True, slots=True)
class CommandTimedOut:
    """Report deadline-triggered process-group teardown."""

    stdout: bytes
    stderr: bytes


@dataclass(frozen=True, slots=True)
class CommandCancelled:
    """Report caller-triggered process-group teardown."""

    stdout: bytes
    stderr: bytes


@dataclass(frozen=True, slots=True)
class CommandOutputOverflow:
    """Report bounded capture termination."""

    stdout: bytes
    stderr: bytes
    limit: int


@dataclass(frozen=True, slots=True)
class CommandInvalid:
    """Report a malformed request before process creation."""

    reason: CommandInvalidReason


@dataclass(frozen=True, slots=True)
class CommandStartFailed:
    """Report an operating-system process creation failure."""

    errno: int | None


@dataclass(frozen=True, slots=True)
class CommandInterrupted:
    """Report an interrupted capture syscall without retrying."""

    stdout: bytes
    stderr: bytes
    errno: int | None


@dataclass(frozen=True, slots=True)
class CommandTeardownFailed:
    """Report a process group that resisted bounded escalation."""

    stdout: bytes
    stderr: bytes


CommandOutcome: TypeAlias = (
    CommandCompleted
    | CommandTimedOut
    | CommandCancelled
    | CommandOutputOverflow
    | CommandInvalid
    | CommandStartFailed
    | CommandInterrupted
    | CommandTeardownFailed
)


@final
class CancellationToken:
    """Wake a running command from another thread exactly once."""

    __slots__: tuple[str, ...] = (
        "_cancelled",
        "_lock",
        "_read_descriptor",
        "_write_descriptor",
    )
    _cancelled: bool
    _lock: threading.Lock
    _read_descriptor: int
    _write_descriptor: int

    def __init__(self) -> None:
        """Create the private wake pipe."""
        self._read_descriptor, self._write_descriptor = os.pipe()
        self._cancelled = False
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        """Report whether cancellation was requested."""
        with self._lock:
            return self._cancelled

    def fileno(self) -> int:
        """Return the selector-readable cancellation descriptor."""
        return self._read_descriptor

    def cancel(self) -> None:
        """Request cancellation idempotently."""
        with self._lock:
            if self._cancelled:
                return
            _ = os.write(self._write_descriptor, b"C")
            self._cancelled = True

    def close(self) -> None:
        """Close both wake-pipe descriptors."""
        with self._lock:
            os.close(self._read_descriptor)
            os.close(self._write_descriptor)

    def __enter__(self) -> Self:
        """Enter the cancellation-token context."""
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close cancellation resources."""
        self.close()
