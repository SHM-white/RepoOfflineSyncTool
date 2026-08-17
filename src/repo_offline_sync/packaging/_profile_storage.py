"""Private durable storage boundary for packaging profiles."""

from __future__ import annotations

import fcntl
import os
import secrets
import stat
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

from repo_offline_sync.packaging._storage_errors import (
    StorageFailure,
    StorageFormatError,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from repo_offline_sync.packaging._private_directories import PrivateDirectory

_PRIVATE_FILE_MODE: Final = 0o600


@contextmanager
def exclusive_lock(
    directory: PrivateDirectory,
    name: str,
) -> Generator[None, None, None]:
    """Hold an owner-only advisory lock for one complete profile transaction."""
    descriptor = os.open(
        name,
        os.O_CLOEXEC | os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR,
        0o600,
        dir_fd=directory.descriptor,
    )
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                directory.recheck()
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def read_private(directory: PrivateDirectory, name: str) -> str:
    """Read an owner-only regular file without following a final symlink."""
    descriptor = os.open(
        name,
        os.O_CLOEXEC | os.O_NOFOLLOW | os.O_RDONLY,
        dir_fd=directory.descriptor,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise StorageFormatError(StorageFailure.NOT_REGULAR)
        content = os.read(descriptor, metadata.st_size + 1).decode("utf-8")
    except UnicodeDecodeError as error:
        raise StorageFormatError(StorageFailure.NOT_UTF8) from error
    else:
        directory.recheck()
        return content
    finally:
        os.close(descriptor)


def private_exists(directory: PrivateDirectory, name: str) -> bool:
    """Check one no-follow file name beneath a held private directory."""
    try:
        _ = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
    except FileNotFoundError:
        exists = False
    else:
        exists = True
    directory.recheck()
    return exists


def list_private(directory: PrivateDirectory) -> tuple[str, ...]:
    """List names beneath a held private directory and recheck its pathname."""
    with os.scandir(directory.descriptor) as entries:
        names = tuple(entry.name for entry in entries)
    directory.recheck()
    return names


def unlink_private(
    directory: PrivateDirectory,
    name: str,
    *,
    missing_ok: bool = False,
) -> None:
    """Unlink exactly one name beneath a held private directory and sync it."""
    try:
        os.unlink(name, dir_fd=directory.descriptor)
    except FileNotFoundError:
        if not missing_ok:
            raise
    os.fsync(directory.descriptor)
    directory.recheck()


def atomic_write_private(
    directory: PrivateDirectory,
    name: str,
    content: str,
) -> None:
    """Atomically replace a private file after syncing data and its directory."""
    temporary_name = f".{name}.{secrets.token_hex(16)}.tmp"
    descriptor = os.open(
        temporary_name,
        os.O_CLOEXEC | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_RDWR,
        0o600,
        dir_fd=directory.descriptor,
    )
    descriptor_owned = True
    temporary_present = True
    try:
        os.fchmod(descriptor, 0o600)
        data = content.encode("utf-8")
        stream = os.fdopen(descriptor, "wb", closefd=True)
        descriptor_owned = False
        with stream:
            _ = stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(
            temporary_name,
            name,
            src_dir_fd=directory.descriptor,
            dst_dir_fd=directory.descriptor,
        )
        temporary_present = False
        final_descriptor = os.open(
            name,
            os.O_CLOEXEC | os.O_NOFOLLOW | os.O_RDONLY,
            dir_fd=directory.descriptor,
        )
        try:
            os.fchmod(final_descriptor, 0o600)
            metadata = os.fstat(final_descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != _PRIVATE_FILE_MODE
            ):
                raise StorageFormatError(StorageFailure.FILE_MODE)
        finally:
            os.close(final_descriptor)
        os.fsync(directory.descriptor)
        directory.recheck()
    finally:
        try:
            if descriptor_owned:
                os.close(descriptor)
        finally:
            if temporary_present:
                try:
                    os.unlink(temporary_name, dir_fd=directory.descriptor)
                except FileNotFoundError:
                    temporary_present = False
