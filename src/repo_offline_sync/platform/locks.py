"""Process-scoped advisory file locking."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class LockAcquired:
    """Report ownership of the stable lock file."""

    path: Path


@dataclass(frozen=True, slots=True)
class LockBusy:
    """Report deterministic nonblocking contention."""

    path: Path


@dataclass(frozen=True, slots=True)
class LockFailure:
    """Report a lock-file OS failure."""

    path: Path
    operation: str
    errno: int | None


LockOutcome: TypeAlias = LockAcquired | LockBusy | LockFailure


@contextmanager
def exclusive_lock(path: Path) -> Generator[LockOutcome, None, None]:
    """Attempt one nonblocking exclusive flock for the context lifetime."""
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as error:
        yield LockFailure(path, "open", error.errno)
        return

    acquired = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            outcome: LockOutcome = LockAcquired(path)
        except BlockingIOError:
            outcome = LockBusy(path)
        except OSError as error:
            outcome = LockFailure(path, "flock", error.errno)
        yield outcome
    finally:
        try:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
