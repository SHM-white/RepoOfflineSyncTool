from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import NoReturn

import pytest

from repo_offline_sync.platform.filesystem import (
    BinaryOpenMode,
    IdentityChanged,
    IdentityMatched,
    IdentityRecorded,
    create_temporary_binary_file,
    managed_binary_file,
    recheck_identity,
    record_identity,
)


def test_managed_binary_file_closes_its_descriptor_after_context_exit(
    tmp_path: Path,
) -> None:
    # Given a destination for a context-managed binary file
    destination = tmp_path / "payload"

    # When bytes are written through the managed adapter
    with managed_binary_file(destination, BinaryOpenMode.WRITE_EXCLUSIVE) as handle:
        handle.write_all(b"payload")
        handle.flush()
        descriptor = handle.fileno()

    # Then the file is complete and the descriptor is closed
    assert destination.read_bytes() == b"payload"
    assert handle.closed
    assert descriptor >= 0


def test_managed_read_file_transfers_and_closes_descriptor(tmp_path: Path) -> None:
    # Given an existing file opened for managed reading
    destination = tmp_path / "payload"
    _ = destination.write_bytes(b"payload")

    # When the read handle context exits
    with managed_binary_file(destination, BinaryOpenMode.READ) as handle:
        descriptor = handle.fileno()
        assert Path(f"/proc/self/fd/{descriptor}").exists()

    # Then ownership closed the descriptor without changing bytes
    assert handle.closed
    assert destination.read_bytes() == b"payload"


def test_managed_truncate_file_transfers_and_closes_descriptor(tmp_path: Path) -> None:
    # Given an existing file opened for managed truncation
    destination = tmp_path / "payload"
    _ = destination.write_bytes(b"old")

    # When replacement bytes are written and the context exits
    with managed_binary_file(destination, BinaryOpenMode.WRITE_TRUNCATE) as handle:
        handle.write_all(b"new")
        descriptor = handle.fileno()

    # Then ownership closed the descriptor and truncation is complete
    assert handle.closed
    assert descriptor >= 0
    assert destination.read_bytes() == b"new"


def test_exclusive_fdopen_failure_closes_once_and_removes_owned_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given real exclusive open with injected fdopen failure
    destination = tmp_path / "owned"
    unrelated = tmp_path / "unrelated"
    _ = unrelated.write_bytes(b"keep")
    original_close = os.close
    opened_descriptors: list[int] = []
    closed_descriptors: list[int] = []

    def fail_fdopen(descriptor: int, mode: str) -> NoReturn:
        _ = mode
        opened_descriptors.append(descriptor)
        raise OSError(errno.EIO, "injected fdopen")

    def record_close(descriptor: int) -> None:
        closed_descriptors.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(os, "fdopen", fail_fdopen)
    monkeypatch.setattr(os, "close", record_close)

    # When stream ownership transfer fails
    with pytest.raises(OSError, match="injected fdopen") as raised:
        _ = managed_binary_file(destination, BinaryOpenMode.WRITE_EXCLUSIVE)

    descriptor = opened_descriptors[0]
    descriptor_was_live = Path(f"/proc/self/fd/{descriptor}").exists()
    artifact_existed = destination.exists()
    if descriptor_was_live:
        original_close(descriptor)
    if artifact_existed:
        destination.unlink()

    # Then the primary error survives and only locally owned resources are cleaned
    assert raised.value.errno == errno.EIO
    assert not descriptor_was_live
    assert closed_descriptors == opened_descriptors
    assert not artifact_existed
    assert unrelated.read_bytes() == b"keep"


def test_exclusive_close_cleanup_failure_chains_fdopen_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given fdopen failure followed by one injected close cleanup failure
    destination = tmp_path / "owned"
    original_close = os.close
    opened_descriptors: list[int] = []
    close_attempts: list[int] = []

    def fail_fdopen(descriptor: int, mode: str) -> NoReturn:
        _ = mode
        opened_descriptors.append(descriptor)
        raise OSError(errno.EIO, "injected fdopen")

    def fail_close(descriptor: int) -> NoReturn:
        close_attempts.append(descriptor)
        raise OSError(errno.EBADF, "injected close cleanup")

    monkeypatch.setattr(os, "fdopen", fail_fdopen)
    monkeypatch.setattr(os, "close", fail_close)

    # When both transfer and descriptor cleanup fail
    try:
        with pytest.raises(OSError, match="injected close cleanup") as raised:
            _ = managed_binary_file(destination, BinaryOpenMode.WRITE_EXCLUSIVE)
    finally:
        for descriptor in opened_descriptors:
            if Path(f"/proc/self/fd/{descriptor}").exists():
                original_close(descriptor)
        if destination.exists():
            destination.unlink()

    # Then cleanup failure is explicit, caused by fdopen, and close was attempted once
    assert isinstance(raised.value.__cause__, OSError)
    assert "injected fdopen" in str(raised.value.__cause__)
    assert close_attempts == opened_descriptors


def test_identity_recheck_detects_inode_replacement_before_destructive_use(
    tmp_path: Path,
) -> None:
    # Given a recorded file identity
    destination = tmp_path / "state"
    _ = destination.write_bytes(b"old")
    recorded = record_identity(destination)
    assert isinstance(recorded, IdentityRecorded)

    # When another inode replaces the path
    replacement = tmp_path / "replacement"
    _ = replacement.write_bytes(b"new")
    _ = replacement.replace(destination)
    outcome = recheck_identity(recorded.identity)

    # Then the changed device/inode/mount identity is explicit
    assert isinstance(outcome, IdentityChanged)
    assert outcome.expected == recorded.identity


def test_identity_record_includes_file_device_inode_and_mount(
    tmp_path: Path,
) -> None:
    # Given an existing file on a mounted filesystem
    destination = tmp_path / "state"
    _ = destination.write_bytes(b"state")

    # When its identity is recorded and immediately rechecked
    recorded = record_identity(destination)
    assert isinstance(recorded, IdentityRecorded)
    checked = recheck_identity(recorded.identity)

    # Then all stable identity dimensions are present and match
    assert recorded.identity.device >= 0
    assert recorded.identity.inode > 0
    assert recorded.identity.mount_id > 0
    assert isinstance(checked, IdentityMatched)


def test_temporary_setup_failure_closes_and_unlinks_only_owned_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an unrelated temp-like file and an injected fchmod failure after mkstemp
    destination = tmp_path / "state"
    unrelated = tmp_path / ".state.unrelated.tmp"
    _ = unrelated.write_bytes(b"keep")
    original_close = os.close
    closed_descriptors: list[int] = []
    created_descriptor: list[int] = []

    def fail_fchmod(descriptor: int, mode: int) -> None:
        _ = mode
        created_descriptor.append(descriptor)
        raise OSError(errno.EIO, "injected fchmod")

    def record_close(descriptor: int) -> None:
        closed_descriptors.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(os, "fchmod", fail_fchmod)
    monkeypatch.setattr(os, "close", record_close)

    # When temporary-file setup fails after the owned path exists
    with pytest.raises(OSError, match="injected fchmod") as raised:
        _ = create_temporary_binary_file(destination, 0o600)

    # Then the primary error survives and only the owned temp is removed
    assert raised.value.errno == errno.EIO
    assert closed_descriptors == created_descriptor
    assert unrelated.read_bytes() == b"keep"
    assert list(tmp_path.glob(".state.*.tmp")) == [unrelated]
