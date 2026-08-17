"""Injectable Linux durability syscalls."""

from __future__ import annotations

import ctypes
import os
from typing import TYPE_CHECKING, Protocol

from repo_offline_sync.platform.filesystem import (
    FileIdentity,
    IdentityRecheckOutcome,
    ManagedBinaryFile,
    create_temporary_binary_file,
    recheck_identity,
)

if TYPE_CHECKING:
    from pathlib import Path


class DurabilityOperations(Protocol):
    """Inject filesystem syscalls for deterministic fault testing."""

    def create_temp(self, destination: Path, mode: int) -> ManagedBinaryFile:
        """Create an exclusive same-directory temporary file."""
        ...

    def write_all(self, handle: ManagedBinaryFile, data: bytes) -> None:
        """Write all requested bytes."""
        ...

    def flush(self, handle: ManagedBinaryFile) -> None:
        """Flush buffered bytes."""
        ...

    def fsync_file(self, handle: ManagedBinaryFile) -> None:
        """Synchronize temporary-file bytes and metadata."""
        ...

    def replace(self, source: Path, destination: Path) -> None:
        """Atomically rename the temporary file."""
        ...

    def open_directory(self, path: Path) -> int:
        """Open a parent directory descriptor."""
        ...

    def fsync_directory(self, descriptor: int) -> None:
        """Synchronize a directory descriptor."""
        ...

    def sync_filesystem(self, descriptor: int) -> None:
        """Synchronize the descriptor's filesystem."""
        ...

    def close_directory(self, descriptor: int) -> None:
        """Close a parent directory descriptor."""
        ...

    def unlink(self, path: Path) -> None:
        """Remove an uncommitted temporary path."""
        ...

    def recheck(self, expected: FileIdentity) -> IdentityRecheckOutcome:
        """Recheck a recorded destructive-operation identity."""
        ...


class PosixDurabilityOperations:
    """Perform Linux file publication syscalls."""

    def create_temp(self, destination: Path, mode: int) -> ManagedBinaryFile:
        """Create an exclusive same-directory temporary file."""
        return create_temporary_binary_file(destination, mode)

    def write_all(self, handle: ManagedBinaryFile, data: bytes) -> None:
        """Write all requested bytes."""
        handle.write_all(data)

    def flush(self, handle: ManagedBinaryFile) -> None:
        """Flush buffered bytes."""
        handle.flush()

    def fsync_file(self, handle: ManagedBinaryFile) -> None:
        """Synchronize temporary-file bytes and metadata."""
        os.fsync(handle.fileno())

    def replace(self, source: Path, destination: Path) -> None:
        """Atomically rename the temporary file."""
        _ = source.replace(destination)

    def open_directory(self, path: Path) -> int:
        """Open a parent directory descriptor."""
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY)

    def fsync_directory(self, descriptor: int) -> None:
        """Synchronize a directory descriptor."""
        os.fsync(descriptor)

    def sync_filesystem(self, descriptor: int) -> None:
        """Call Linux syncfs through the process C library."""
        libc = ctypes.CDLL(None, use_errno=True)
        syncfs = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)(("syncfs", libc))
        if syncfs(descriptor) != 0:
            detected_errno = ctypes.get_errno()
            raise OSError(detected_errno, os.strerror(detected_errno))

    def close_directory(self, descriptor: int) -> None:
        """Close a parent directory descriptor."""
        os.close(descriptor)

    def unlink(self, path: Path) -> None:
        """Remove an uncommitted temporary path."""
        path.unlink()

    def recheck(self, expected: FileIdentity) -> IdentityRecheckOutcome:
        """Recheck a recorded destructive-operation identity."""
        return recheck_identity(expected)
