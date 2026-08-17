"""Descriptor-relative creation of private application directories."""

from __future__ import annotations

import errno
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Generator

from repo_offline_sync.packaging._storage_errors import (
    StorageFailure,
    StorageFormatError,
)

_DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_PRIVATE_MODE: Final = 0o700


@dataclass(frozen=True, slots=True)
class PrivateDirectory:
    """Held no-follow directory identity for fd-relative operations."""

    path: Path
    descriptor: int
    device: int
    inode: int

    def recheck(self) -> None:
        """Require the active pathname to still resolve to this held directory."""
        try:
            current = _traverse(self.path, create=False)
        except (FileNotFoundError, StorageFormatError) as error:
            raise StorageFormatError(StorageFailure.XDG_CHANGED) from error
        try:
            metadata = os.fstat(current)
            if metadata.st_dev != self.device or metadata.st_ino != self.inode:
                raise StorageFormatError(StorageFailure.XDG_CHANGED)
        finally:
            os.close(current)


def _open_directory(name: str, parent_descriptor: int) -> int:
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise StorageFormatError(StorageFailure.XDG_COMPONENT) from error
        raise


def _open_or_create_directory(name: str, parent_descriptor: int) -> tuple[int, bool]:
    try:
        return _open_directory(name, parent_descriptor), False
    except FileNotFoundError:
        try:
            os.mkdir(name, mode=_PRIVATE_MODE, dir_fd=parent_descriptor)
            created = True
        except FileExistsError:
            created = False
        descriptor = _open_directory(name, parent_descriptor)
        return descriptor, created


def _traverse(path: Path, *, create: bool) -> int:
    if not path.is_absolute():
        raise StorageFormatError(StorageFailure.XDG_RELATIVE)
    if path == Path("/"):
        raise StorageFormatError(StorageFailure.XDG_ROOT)
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        components = path.parts[1:]
        for index, component in enumerate(components):
            if create:
                child_descriptor, created = _open_or_create_directory(
                    component, descriptor
                )
            else:
                child_descriptor = _open_directory(component, descriptor)
                created = False
            parent_descriptor = descriptor
            descriptor = child_descriptor
            try:
                if create and (created or index == len(components) - 1):
                    os.fchmod(descriptor, _PRIVATE_MODE)
            finally:
                os.close(parent_descriptor)
    except (OSError, StorageFormatError):
        os.close(descriptor)
        raise
    else:
        return descriptor


def ensure_private_directory(path: Path) -> None:
    """Create an absolute path without following any non-root component."""
    descriptor = _traverse(path, create=True)
    os.close(descriptor)


@contextmanager
def open_private_directory(path: Path) -> Generator[PrivateDirectory, None, None]:
    """Hold an existing no-follow directory and its captured identity."""
    descriptor = _traverse(path, create=False)
    metadata = os.fstat(descriptor)
    directory = PrivateDirectory(path, descriptor, metadata.st_dev, metadata.st_ino)
    try:
        yield directory
    finally:
        os.close(descriptor)
