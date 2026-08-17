"""Context-managed files and stable Linux path identities."""

from __future__ import annotations

import errno
import os
import stat
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Final, TypeAlias, final

if TYPE_CHECKING:
    from types import TracebackType

    from typing_extensions import Self

_MOUNT_INFO: Final = Path("/proc/self/mountinfo")
_MOUNT_ESCAPES: Final = {
    "\\040": " ",
    "\\011": "\t",
    "\\012": "\n",
    "\\134": "\\",
}
_MINIMUM_MOUNT_FIELDS: Final = 6


class BinaryOpenMode(Enum):
    """Supported binary file ownership modes."""

    READ = "read"
    WRITE_EXCLUSIVE = "write_exclusive"
    WRITE_TRUNCATE = "write_truncate"


@final
class ManagedBinaryFile:
    """Own a binary stream and close it at context exit."""

    __slots__: tuple[str, ...] = ("_stream", "path")
    path: Path
    _stream: BinaryIO

    def __init__(self, path: Path, stream: BinaryIO) -> None:
        """Take ownership of an open binary stream."""
        self.path = path
        self._stream = stream

    @property
    def closed(self) -> bool:
        """Report whether the owned stream is closed."""
        return self._stream.closed

    def write_all(self, data: bytes) -> None:
        """Write every byte or raise the underlying OS failure."""
        position = 0
        while position < len(data):
            written = self._stream.write(data[position:])
            if written <= 0:
                raise OSError(errno.EIO, "binary write made no progress", self.path)
            position += written

    def flush(self) -> None:
        """Flush Python's buffered stream to the kernel."""
        self._stream.flush()

    def fileno(self) -> int:
        """Return the owned file descriptor."""
        return self._stream.fileno()

    def __enter__(self) -> Self:
        """Enter the owned stream context."""
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the owned stream."""
        self._stream.close()


def _cleanup_owned_file(descriptor: int, path: Path) -> OSError | None:
    cleanup_error: OSError | None = None
    try:
        os.close(descriptor)
    except OSError as close_error:
        cleanup_error = close_error
    try:
        path.unlink()
    except OSError as unlink_error:
        cleanup_error = unlink_error
    return cleanup_error


def managed_binary_file(path: Path, mode: BinaryOpenMode) -> ManagedBinaryFile:
    """Open a binary file with explicit ownership semantics."""
    match mode:
        case BinaryOpenMode.READ:
            stream = path.open("rb")
        case BinaryOpenMode.WRITE_EXCLUSIVE:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                stream = os.fdopen(descriptor, "wb")
            except OSError as setup_error:
                cleanup_error = _cleanup_owned_file(descriptor, path)
                if cleanup_error is not None:
                    raise cleanup_error from setup_error
                raise
        case BinaryOpenMode.WRITE_TRUNCATE:
            stream = path.open("wb")
    return ManagedBinaryFile(path, stream)


def create_temporary_binary_file(destination: Path, mode: int) -> ManagedBinaryFile:
    """Create an exclusive temporary file beside its final destination."""
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    try:
        os.fchmod(descriptor, mode)
        stream = os.fdopen(descriptor, "wb")
    except OSError as setup_error:
        cleanup_error = _cleanup_owned_file(descriptor, Path(raw_path))
        if cleanup_error is not None:
            raise cleanup_error from setup_error
        raise
    return ManagedBinaryFile(Path(raw_path), stream)


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """Stable file, device, inode, type, and mount identity."""

    path: Path
    device: int
    inode: int
    file_type: int
    mount_id: int


@dataclass(frozen=True, slots=True)
class IdentityRecorded:
    """Report a newly recorded identity."""

    identity: FileIdentity


@dataclass(frozen=True, slots=True)
class IdentityMatched:
    """Report a successful identity recheck."""

    identity: FileIdentity


@dataclass(frozen=True, slots=True)
class IdentityChanged:
    """Report replacement or disappearance since recording."""

    expected: FileIdentity
    observed: FileIdentity | None


@dataclass(frozen=True, slots=True)
class IdentityCheckFailure:
    """Report an OS or mount-table identity failure."""

    path: Path
    operation: str
    errno: int | None


IdentityRecordOutcome: TypeAlias = IdentityRecorded | IdentityCheckFailure
IdentityRecheckOutcome: TypeAlias = (
    IdentityMatched | IdentityChanged | IdentityCheckFailure
)


def _unescape_mount_path(value: str) -> Path:
    decoded = value
    for escaped, literal in _MOUNT_ESCAPES.items():
        decoded = decoded.replace(escaped, literal)
    return Path(decoded)


def _mount_id(path: Path, device: int) -> int:
    major_minor = f"{os.major(device)}:{os.minor(device)}"
    best: tuple[int, int] | None = None
    for line in _MOUNT_INFO.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < _MINIMUM_MOUNT_FIELDS or fields[2] != major_minor:
            continue
        mount_point = _unescape_mount_path(fields[4])
        if path != mount_point and mount_point not in path.parents:
            continue
        candidate = (len(mount_point.parts), int(fields[0]))
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        raise OSError(errno.ENODEV, "mount identity not found", path)
    return best[1]


def _read_identity(path: Path) -> FileIdentity:
    absolute_path = path.absolute()
    status = absolute_path.stat(follow_symlinks=False)
    return FileIdentity(
        path=absolute_path,
        device=status.st_dev,
        inode=status.st_ino,
        file_type=stat.S_IFMT(status.st_mode),
        mount_id=_mount_id(absolute_path, status.st_dev),
    )


def record_identity(path: Path) -> IdentityRecordOutcome:
    """Record identity dimensions used to guard later destructive operations."""
    try:
        return IdentityRecorded(_read_identity(path))
    except OSError as error:
        return IdentityCheckFailure(path, "record", error.errno)
    except ValueError:
        return IdentityCheckFailure(path, "parse_mountinfo", None)


def recheck_identity(expected: FileIdentity) -> IdentityRecheckOutcome:
    """Compare a fresh path identity with a previously recorded identity."""
    try:
        observed = _read_identity(expected.path)
    except FileNotFoundError:
        return IdentityChanged(expected=expected, observed=None)
    except OSError as error:
        return IdentityCheckFailure(expected.path, "recheck", error.errno)
    except ValueError:
        return IdentityCheckFailure(expected.path, "parse_mountinfo", None)
    if observed == expected:
        return IdentityMatched(observed)
    return IdentityChanged(expected=expected, observed=observed)
