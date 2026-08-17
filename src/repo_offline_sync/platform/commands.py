"""Typed no-shell subprocess boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from repo_offline_sync.platform._command_runner import run_command as _run_command
from repo_offline_sync.platform.command_types import (
    CancellationToken,
    CommandCancelled,
    CommandCompleted,
    CommandInterrupted,
    CommandInvalid,
    CommandInvalidReason,
    CommandOutcome,
    CommandOutputOverflow,
    CommandRequest,
    CommandStartFailed,
    CommandTeardownFailed,
    CommandTimedOut,
)

__all__ = (
    "CancellationToken",
    "CommandCancelled",
    "CommandCompleted",
    "CommandInterrupted",
    "CommandInvalid",
    "CommandInvalidReason",
    "CommandOutcome",
    "CommandOutputOverflow",
    "CommandRequest",
    "CommandStartFailed",
    "CommandTeardownFailed",
    "CommandTimedOut",
    "run_command",
)

if TYPE_CHECKING:
    from repo_offline_sync.platform.runtime import Clock


def run_command(
    request: CommandRequest,
    *,
    cancellation: CancellationToken | None = None,
    clock: Clock | None = None,
) -> CommandOutcome:
    """Run one argv command with bounded output and deterministic group teardown."""
    return _run_command(request, cancellation=cancellation, clock=clock)
