"""Crash-aware atomic file replacement and durability reporting."""

from __future__ import annotations

import errno
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Final, TypeAlias

from repo_offline_sync.platform.filesystem import (
    FileIdentity,
    IdentityChanged,
    IdentityCheckFailure,
    IdentityMatched,
)
from repo_offline_sync.platform.syscalls import (
    DurabilityOperations,
    PosixDurabilityOperations,
)

if TYPE_CHECKING:
    from pathlib import Path

_DIRECTORY_FSYNC_UNSUPPORTED: Final = frozenset(
    {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}
)


class DurabilityBoundary(Enum):
    """Name one successfully completed publication boundary."""

    TEMP_CREATED = "temp_created"
    DATA_WRITTEN = "data_written"
    DATA_FLUSHED = "data_flushed"
    FILE_SYNCED = "file_synced"
    RENAMED = "renamed"
    DIRECTORY_SYNCED = "directory_synced"
    FILESYSTEM_SYNCED = "filesystem_synced"


class DurabilityMethod(Enum):
    """Identify how rename durability was established."""

    DIRECTORY_FSYNC = "directory_fsync"
    SYNCFS_FALLBACK = "syncfs_fallback"


class FailureStage(Enum):
    """Identify the syscall boundary that failed."""

    CREATE_TEMP = "create_temp"
    WRITE = "write"
    FLUSH = "flush"
    FILE_FSYNC = "file_fsync"
    CLOSE_TEMP = "close_temp"
    IDENTITY_RECHECK = "identity_recheck"
    RENAME = "rename"
    OPEN_DIRECTORY = "open_directory"
    DIRECTORY_FSYNC = "directory_fsync"
    SYNCFS = "syncfs"
    CLOSE_DIRECTORY = "close_directory"


@dataclass(frozen=True, slots=True)
class AtomicWriteRequest:
    """Describe one same-directory atomic replacement."""

    destination: Path
    data: bytes
    mode: int
    expected_identity: FileIdentity | None = None


@dataclass(frozen=True, slots=True)
class AtomicWriteCommitted:
    """Report a replacement that reached a durable parent boundary."""

    destination: Path
    completed: tuple[DurabilityBoundary, ...]
    method: DurabilityMethod


@dataclass(frozen=True, slots=True)
class AtomicWriteFailed:
    """Report a failed boundary without implying commit durability."""

    destination: Path
    stage: FailureStage
    errno: int | None
    completed: tuple[DurabilityBoundary, ...]
    temporary_cleaned: bool
    cleanup_errno: int | None


AtomicWriteOutcome: TypeAlias = AtomicWriteCommitted | AtomicWriteFailed


@dataclass(slots=True)
class _WriteState:
    """Track mutable progress for one atomic write."""

    request: AtomicWriteRequest
    operations: DurabilityOperations
    completed: list[DurabilityBoundary] = field(default_factory=list)
    temporary_path: Path | None = None
    renamed: bool = False


def _failed(
    state: _WriteState,
    stage: FailureStage,
    detected_errno: int | None,
) -> AtomicWriteFailed:
    cleaned = True
    cleanup_errno: int | None = None
    if state.temporary_path is not None and not state.renamed:
        try:
            state.operations.unlink(state.temporary_path)
        except FileNotFoundError:
            cleaned = True
        except OSError as error:
            cleaned = False
            cleanup_errno = error.errno
    return AtomicWriteFailed(
        state.request.destination,
        stage,
        detected_errno,
        tuple(state.completed),
        cleaned,
        cleanup_errno,
    )


def _prepare_temporary(state: _WriteState) -> AtomicWriteFailed | None:
    stage = FailureStage.CREATE_TEMP
    try:
        temporary = state.operations.create_temp(
            state.request.destination, state.request.mode
        )
        state.temporary_path = temporary.path
        state.completed.append(DurabilityBoundary.TEMP_CREATED)
        with temporary:
            stage = FailureStage.WRITE
            state.operations.write_all(temporary, state.request.data)
            state.completed.append(DurabilityBoundary.DATA_WRITTEN)
            stage = FailureStage.FLUSH
            state.operations.flush(temporary)
            state.completed.append(DurabilityBoundary.DATA_FLUSHED)
            stage = FailureStage.FILE_FSYNC
            state.operations.fsync_file(temporary)
            state.completed.append(DurabilityBoundary.FILE_SYNCED)
            stage = FailureStage.CLOSE_TEMP
    except OSError as error:
        return _failed(state, stage, error.errno)
    return None


def _check_identity(state: _WriteState) -> AtomicWriteFailed | None:
    expected = state.request.expected_identity
    if expected is None:
        return None
    match state.operations.recheck(expected):
        case IdentityMatched():
            return None
        case IdentityChanged():
            return _failed(state, FailureStage.IDENTITY_RECHECK, None)
        case IdentityCheckFailure(errno=detected_errno):
            return _failed(state, FailureStage.IDENTITY_RECHECK, detected_errno)


def _sync_parent(state: _WriteState) -> AtomicWriteOutcome:
    try:
        descriptor = state.operations.open_directory(state.request.destination.parent)
    except OSError as error:
        return _failed(state, FailureStage.OPEN_DIRECTORY, error.errno)
    method = DurabilityMethod.DIRECTORY_FSYNC
    failure: AtomicWriteFailed | None = None
    try:
        state.operations.fsync_directory(descriptor)
        state.completed.append(DurabilityBoundary.DIRECTORY_SYNCED)
    except OSError as error:
        if error.errno not in _DIRECTORY_FSYNC_UNSUPPORTED:
            failure = _failed(state, FailureStage.DIRECTORY_FSYNC, error.errno)
        else:
            try:
                state.operations.sync_filesystem(descriptor)
                state.completed.append(DurabilityBoundary.FILESYSTEM_SYNCED)
                method = DurabilityMethod.SYNCFS_FALLBACK
            except OSError as sync_error:
                failure = _failed(state, FailureStage.SYNCFS, sync_error.errno)
    try:
        state.operations.close_directory(descriptor)
    except OSError as error:
        if failure is None:
            failure = _failed(state, FailureStage.CLOSE_DIRECTORY, error.errno)
    if failure is not None:
        return failure
    return AtomicWriteCommitted(
        state.request.destination, tuple(state.completed), method
    )


def _publish(state: _WriteState) -> AtomicWriteOutcome:
    temporary_path = state.temporary_path
    if temporary_path is None:
        return _failed(state, FailureStage.CREATE_TEMP, None)
    try:
        state.operations.replace(temporary_path, state.request.destination)
    except OSError as error:
        return _failed(state, FailureStage.RENAME, error.errno)
    state.renamed = True
    state.completed.append(DurabilityBoundary.RENAMED)
    return _sync_parent(state)


def atomic_replace(
    request: AtomicWriteRequest,
    *,
    operations: DurabilityOperations | None = None,
) -> AtomicWriteOutcome:
    """Replace a file and report only durability syscalls that completed."""
    active_operations = (
        PosixDurabilityOperations() if operations is None else operations
    )
    state = _WriteState(request, active_operations)
    preparation_failure = _prepare_temporary(state)
    if preparation_failure is not None:
        return preparation_failure
    identity_failure = _check_identity(state)
    if identity_failure is not None:
        return identity_failure
    return _publish(state)
