"""Subprocess request validation and process creation."""

from __future__ import annotations

import math
import subprocess

from repo_offline_sync.platform.command_types import (
    CommandInvalidReason,
    CommandRequest,
    CommandStartFailed,
)

_PROCESS_FACTORY = subprocess.Popen


def invalid_reason(request: CommandRequest) -> CommandInvalidReason | None:
    """Return the first malformed request dimension."""
    if not request.argv:
        return CommandInvalidReason.EMPTY_ARGV
    if request.timeout_seconds <= 0 or not math.isfinite(request.timeout_seconds):
        return CommandInvalidReason.INVALID_TIMEOUT
    if request.output_limit_bytes <= 0:
        return CommandInvalidReason.INVALID_OUTPUT_LIMIT
    if request.termination_grace_seconds <= 0 or not math.isfinite(
        request.termination_grace_seconds
    ):
        return CommandInvalidReason.INVALID_GRACE_PERIOD
    names: set[str] = set()
    for name, value in request.environment:
        malformed = not name or "=" in name or "\0" in name or "\0" in value
        if malformed or name in names:
            return CommandInvalidReason.INVALID_ENVIRONMENT
        names.add(name)
    return None


def spawn(
    request: CommandRequest,
) -> tuple[subprocess.Popen[bytes] | None, CommandStartFailed | None]:
    """Create a process in its own session without shell interpolation."""
    try:
        process = _PROCESS_FACTORY(
            request.argv,
            cwd=request.cwd,
            env=dict(request.environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        return None, CommandStartFailed(error.errno)
    except ValueError:
        return None, CommandStartFailed(None)
    return process, None
