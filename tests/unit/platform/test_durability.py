from __future__ import annotations

import errno
from typing import TYPE_CHECKING

import pytest

from repo_offline_sync.platform.durability import (
    AtomicWriteCommitted,
    AtomicWriteFailed,
    AtomicWriteRequest,
    DurabilityBoundary,
    DurabilityMethod,
    FailureStage,
    atomic_replace,
)
from repo_offline_sync.platform.filesystem import (
    FileIdentity,
    IdentityChanged,
    IdentityCheckFailure,
    IdentityMatched,
    IdentityRecorded,
    ManagedBinaryFile,
    record_identity,
)
from repo_offline_sync.platform.syscalls import PosixDurabilityOperations

if TYPE_CHECKING:
    from pathlib import Path


class FaultOperations:
    """Inject one syscall-boundary failure while retaining real temporary files."""

    __slots__: tuple[str, ...] = (
        "_delegate",
        "calls",
        "fault",
        "fault_errno",
        "syncfs_errno",
    )
    _delegate: PosixDurabilityOperations
    calls: list[str]
    fault: str
    fault_errno: int
    syncfs_errno: int | None

    def __init__(
        self,
        fault: str,
        fault_errno: int,
        *,
        syncfs_errno: int | None = None,
    ) -> None:
        self.fault = fault
        self.fault_errno = fault_errno
        self.syncfs_errno = syncfs_errno
        self.calls = []
        self._delegate = PosixDurabilityOperations()

    def _record(self, operation: str) -> None:
        self.calls.append(operation)
        if self.fault == operation:
            raise OSError(self.fault_errno, operation)

    def create_temp(self, destination: Path, mode: int) -> ManagedBinaryFile:
        return self._delegate.create_temp(destination, mode)

    def write_all(self, handle: ManagedBinaryFile, data: bytes) -> None:
        self._record("write")
        self._delegate.write_all(handle, data)

    def flush(self, handle: ManagedBinaryFile) -> None:
        self._record("flush")
        self._delegate.flush(handle)

    def fsync_file(self, handle: ManagedBinaryFile) -> None:
        self._record("file_fsync")
        self._delegate.fsync_file(handle)

    def replace(self, source: Path, destination: Path) -> None:
        self._record("rename")
        self._delegate.replace(source, destination)

    def open_directory(self, path: Path) -> int:
        return self._delegate.open_directory(path)

    def fsync_directory(self, descriptor: int) -> None:
        self._record("directory_fsync")
        self._delegate.fsync_directory(descriptor)

    def sync_filesystem(self, descriptor: int) -> None:
        _ = descriptor
        self.calls.append("syncfs")
        if self.syncfs_errno is not None:
            raise OSError(self.syncfs_errno, "syncfs")

    def close_directory(self, descriptor: int) -> None:
        self._delegate.close_directory(descriptor)

    def unlink(self, path: Path) -> None:
        self._delegate.unlink(path)

    def recheck(
        self,
        expected: FileIdentity,
    ) -> IdentityMatched | IdentityChanged | IdentityCheckFailure:
        self.calls.append("identity")
        if self.fault == "identity_changed":
            return IdentityChanged(expected=expected, observed=None)
        return self._delegate.recheck(expected)


@pytest.mark.parametrize(
    ("fault", "fault_errno", "expected_stage", "expected_boundaries"),
    [
        ("write", errno.ENOSPC, FailureStage.WRITE, (DurabilityBoundary.TEMP_CREATED,)),
        (
            "file_fsync",
            errno.EIO,
            FailureStage.FILE_FSYNC,
            (
                DurabilityBoundary.TEMP_CREATED,
                DurabilityBoundary.DATA_WRITTEN,
                DurabilityBoundary.DATA_FLUSHED,
            ),
        ),
        (
            "rename",
            errno.EIO,
            FailureStage.RENAME,
            (
                DurabilityBoundary.TEMP_CREATED,
                DurabilityBoundary.DATA_WRITTEN,
                DurabilityBoundary.DATA_FLUSHED,
                DurabilityBoundary.FILE_SYNCED,
            ),
        ),
    ],
)
def test_precommit_failures_preserve_old_final_and_report_only_completed_steps(
    tmp_path: Path,
    fault: str,
    fault_errno: int,
    expected_stage: FailureStage,
    expected_boundaries: tuple[DurabilityBoundary, ...],
) -> None:
    # Given an existing final file and one injected pre-rename OS failure
    destination = tmp_path / "state"
    _ = destination.write_bytes(b"old")
    operations = FaultOperations(fault, fault_errno)

    # When atomic replacement reaches that syscall boundary
    outcome = atomic_replace(
        AtomicWriteRequest(destination=destination, data=b"new", mode=0o600),
        operations=operations,
    )

    # Then old bytes survive and reports do not overclaim completed boundaries
    assert isinstance(outcome, AtomicWriteFailed)
    assert outcome.stage is expected_stage
    assert outcome.errno == fault_errno
    assert outcome.completed == expected_boundaries
    assert destination.read_bytes() == b"old"
    assert list(tmp_path.glob(".state.*.tmp")) == []


def test_atomic_replace_reports_file_rename_and_parent_fsync_boundaries(
    tmp_path: Path,
) -> None:
    # Given an existing final file
    destination = tmp_path / "state"
    _ = destination.write_bytes(b"old")

    # When new bytes are atomically and durably published
    outcome = atomic_replace(
        AtomicWriteRequest(destination=destination, data=b"new", mode=0o600)
    )

    # Then final bytes and every completed boundary are exact
    assert isinstance(outcome, AtomicWriteCommitted)
    assert outcome.method is DurabilityMethod.DIRECTORY_FSYNC
    assert outcome.completed == (
        DurabilityBoundary.TEMP_CREATED,
        DurabilityBoundary.DATA_WRITTEN,
        DurabilityBoundary.DATA_FLUSHED,
        DurabilityBoundary.FILE_SYNCED,
        DurabilityBoundary.RENAMED,
        DurabilityBoundary.DIRECTORY_SYNCED,
    )
    assert destination.read_bytes() == b"new"
    assert destination.stat().st_mode & 0o777 == 0o600


def test_unsupported_directory_fsync_has_explicit_syncfs_fallback(
    tmp_path: Path,
) -> None:
    # Given a filesystem adapter that rejects directory fsync with EINVAL
    destination = tmp_path / "state"
    operations = FaultOperations("directory_fsync", errno.EINVAL)

    # When atomic publication synchronizes the parent
    outcome = atomic_replace(
        AtomicWriteRequest(destination=destination, data=b"new", mode=0o600),
        operations=operations,
    )

    # Then successful syncfs is explicit and directory fsync is not overreported
    assert isinstance(outcome, AtomicWriteCommitted)
    assert outcome.method is DurabilityMethod.SYNCFS_FALLBACK
    assert DurabilityBoundary.DIRECTORY_SYNCED not in outcome.completed
    assert outcome.completed[-1] is DurabilityBoundary.FILESYSTEM_SYNCED


def test_syncfs_failure_after_rename_never_returns_committed_marker(
    tmp_path: Path,
) -> None:
    # Given directory fsync rejection followed by injected syncfs EIO
    destination = tmp_path / "state"
    operations = FaultOperations(
        "directory_fsync",
        errno.EINVAL,
        syncfs_errno=errno.EIO,
    )

    # When the fallback durability boundary fails after rename
    outcome = atomic_replace(
        AtomicWriteRequest(destination=destination, data=b"new", mode=0o600),
        operations=operations,
    )

    # Then visibility is reported but committed durability is not
    assert isinstance(outcome, AtomicWriteFailed)
    assert outcome.stage is FailureStage.SYNCFS
    assert outcome.errno == errno.EIO
    assert outcome.completed[-1] is DurabilityBoundary.RENAMED
    assert destination.read_bytes() == b"new"


def test_identity_change_aborts_before_rename_and_preserves_final(
    tmp_path: Path,
) -> None:
    # Given a recorded final identity and an injected mismatch at destructive recheck
    destination = tmp_path / "state"
    _ = destination.write_bytes(b"old")
    recorded = record_identity(destination)
    assert isinstance(recorded, IdentityRecorded)
    operations = FaultOperations("identity_changed", errno.EIO)

    # When atomic replacement rechecks immediately before rename
    outcome = atomic_replace(
        AtomicWriteRequest(
            destination=destination,
            data=b"new",
            mode=0o600,
            expected_identity=recorded.identity,
        ),
        operations=operations,
    )

    # Then the identity failure is typed and no destructive mutation occurs
    assert isinstance(outcome, AtomicWriteFailed)
    assert outcome.stage is FailureStage.IDENTITY_RECHECK
    assert destination.read_bytes() == b"old"
    assert "rename" not in operations.calls


def test_interrupted_fsync_is_reported_once_without_hidden_retry(
    tmp_path: Path,
) -> None:
    # Given an injected EINTR at the file durability boundary
    destination = tmp_path / "state"
    _ = destination.write_bytes(b"old")
    operations = FaultOperations("file_fsync", errno.EINTR)

    # When atomic replacement reaches fsync
    outcome = atomic_replace(
        AtomicWriteRequest(destination=destination, data=b"new", mode=0o600),
        operations=operations,
    )

    # Then interruption is explicit, attempted once, and old bytes survive
    assert isinstance(outcome, AtomicWriteFailed)
    assert outcome.errno == errno.EINTR
    assert operations.calls.count("file_fsync") == 1
    assert destination.read_bytes() == b"old"


def test_atomic_replace_preserves_unrelated_preexisting_temp_name(
    tmp_path: Path,
) -> None:
    # Given stale state whose name resembles an atomic temporary file
    destination = tmp_path / "state"
    stale = tmp_path / ".state.preexisting.tmp"
    _ = stale.write_bytes(b"unrelated")

    # When a fresh unique temporary file is committed
    outcome = atomic_replace(
        AtomicWriteRequest(destination=destination, data=b"new", mode=0o600)
    )

    # Then only the newly owned temporary path is consumed
    assert isinstance(outcome, AtomicWriteCommitted)
    assert stale.read_bytes() == b"unrelated"
