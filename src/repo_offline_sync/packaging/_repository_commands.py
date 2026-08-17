"""Bounded read-only Git command execution for source preflight."""

from __future__ import annotations

import errno
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from repo_offline_sync.platform.commands import (
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
    run_command,
)

if TYPE_CHECKING:
    from pathlib import Path


class CommandRunner(Protocol):
    """Run one fully explicit command request."""

    def __call__(self, request: CommandRequest) -> CommandOutcome:
        """Return one typed command outcome."""
        ...


class GitFailureReason(Enum):
    """Internal command failures translated by repository preflight."""

    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    INVALID = "invalid"
    NOT_REPOSITORY = "not_repository"
    OUTPUT_OVERFLOW = "output_overflow"
    START_FAILED = "start_failed"
    TEARDOWN_FAILED = "teardown_failed"
    TIMEOUT = "timeout"
    UNSAFE_OWNERSHIP = "unsafe_ownership"


@dataclass(frozen=True, slots=True)
class GitInvocation:
    """One named Git operation and its accepted return codes."""

    operation: str
    arguments: tuple[str, ...]
    accepted_returncodes: tuple[int, ...] = (0,)


@dataclass(frozen=True, slots=True)
class GitOutput:
    """Captured bytes from an accepted Git completion."""

    returncode: int
    stdout: bytes


@dataclass(frozen=True, slots=True)
class GitFailure:
    """A command failure without raw output exposure."""

    reason: GitFailureReason
    operation: str


def _request(repository: Path, arguments: tuple[str, ...]) -> CommandRequest:
    return CommandRequest(
        argv=("/usr/bin/git", "--no-optional-locks", *arguments),
        cwd=repository,
        environment=(
            ("GIT_CONFIG_NOSYSTEM", "1"),
            ("GIT_MASTER", "1"),
            ("GIT_OPTIONAL_LOCKS", "0"),
            ("GIT_TERMINAL_PROMPT", "0"),
            ("HOME", "/nonexistent"),
            ("LANG", "C"),
            ("LC_ALL", "C"),
            ("PATH", "/usr/bin:/bin"),
        ),
        timeout_seconds=10.0,
        output_limit_bytes=256 * 1024,
        termination_grace_seconds=1.0,
    )


def _completed_result(
    outcome: CommandCompleted,
    invocation: GitInvocation,
) -> GitOutput | GitFailureReason:
    lower_stderr = outcome.stderr.lower()
    if b"detected dubious ownership" in lower_stderr:
        return GitFailureReason.UNSAFE_OWNERSHIP
    if b"not a git repository" in lower_stderr:
        return GitFailureReason.NOT_REPOSITORY
    if outcome.returncode not in invocation.accepted_returncodes:
        return GitFailureReason.FAILED
    return GitOutput(outcome.returncode, outcome.stdout)


def execute_git(
    repository: Path,
    invocation: GitInvocation,
    runner: CommandRunner = run_command,
) -> GitOutput | GitFailure:
    """Execute Git and erase captured bytes from every failure variant."""
    outcome = runner(_request(repository, invocation.arguments))
    match outcome:
        case CommandCompleted():
            completed = _completed_result(outcome, invocation)
            if isinstance(completed, GitOutput):
                return completed
            reason = completed
        case CommandTimedOut():
            reason = GitFailureReason.TIMEOUT
        case CommandCancelled():
            reason = GitFailureReason.CANCELLED
        case CommandOutputOverflow():
            reason = GitFailureReason.OUTPUT_OVERFLOW
        case CommandInvalid():
            reason = GitFailureReason.INVALID
        case CommandStartFailed(errno=error_number):
            reason = (
                GitFailureReason.NOT_REPOSITORY
                if error_number == errno.ENOENT
                else GitFailureReason.START_FAILED
            )
        case CommandInterrupted():
            reason = GitFailureReason.INTERRUPTED
        case CommandTeardownFailed():
            reason = GitFailureReason.TEARDOWN_FAILED
    return GitFailure(reason, invocation.operation)
