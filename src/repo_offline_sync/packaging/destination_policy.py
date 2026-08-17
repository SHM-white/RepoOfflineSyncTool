"""Structural destination validation and packaging-only risk acknowledgement."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Final, TextIO

from repo_offline_sync._typing import override

_DEFAULT_PSEUDO_ROOTS: Final = (
    Path("/proc"),
    Path("/sys"),
    Path("/dev"),
)
_DEFAULT_UPDATER_ROOTS: Final = (
    Path("/var/lib/repo-offline-sync"),
    Path("/run/repo-offline-sync"),
)
_SENSITIVE_COMPONENTS: Final = frozenset({".ssh", ".gnupg"})
_MOUNT_POINT_INDEX: Final = 4


class DestinationRejection(str, Enum):
    """Closed machine-consumable structural and acknowledgement rejections."""

    DANGER_DISABLED = "danger mode is disabled"
    INEXACT = "exact lowercase yes was not entered"
    LEAF = "existing destination is not a manageable directory"
    MOUNT = "path is a mount root"
    NUL = "path contains NUL"
    PARENT_MISSING = "destination parent does not exist"
    PARENT_NOT_DIRECTORY = "destination parent is not a directory"
    PARENT_SYMLINK = "destination parent contains a symlink"
    PSEUDO = "path is in a pseudo-filesystem"
    RELATIVE = "path is relative"
    ROOT = "filesystem root is never manageable"
    SERVICE_HOME = "service home root is not a manageable leaf"
    TRAVERSAL = "path is not lexically canonical"
    TTY = "danger acknowledgement requires a real TTY"
    UPDATER = "path is updater-owned"


@dataclass(frozen=True, slots=True)
class DestinationRejectedError(Exception):
    """Report a package-time path rejection without authorizing mutation."""

    reason: DestinationRejection

    @override
    def __str__(self) -> str:
        """Render the stable rejection reason without sensitive values."""
        return f"destination rejected: {self.reason.value}"


@dataclass(frozen=True, slots=True)
class ParentIdentityChangedError(Exception):
    """Report a parent device/inode change after a safety decision."""

    path: Path

    @override
    def __str__(self) -> str:
        """Render the parent path whose captured identity became stale."""
        return f"destination parent identity changed: {self.path}"


@dataclass(frozen=True, slots=True)
class ParentIdentity:
    """Captured identity of the manageable destination parent."""

    path: Path
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class DestinationPolicy:
    """Packaging policy inputs, with roots injectable for deterministic tests."""

    service_user: str
    updater_roots: tuple[Path, ...] = _DEFAULT_UPDATER_ROOTS
    pseudo_roots: tuple[Path, ...] = _DEFAULT_PSEUDO_ROOTS
    mount_roots: tuple[Path, ...] = ()
    service_home: Path | None = None

    @classmethod
    def for_service_user(cls, service_user: str) -> DestinationPolicy:
        """Build production policy roots for a conventional service account."""
        return cls(
            service_user=service_user,
            mount_roots=_read_mount_roots(),
            service_home=Path("/home") / service_user,
        )

    def with_service_home(self, service_home: Path) -> DestinationPolicy:
        """Replace the conventional home for an isolated test or deployment."""
        return replace(self, service_home=service_home)


@dataclass(frozen=True, slots=True)
class DestinationAssessment:
    """Structurally valid leaf plus packaging-side risk classification."""

    path: Path
    parent_identity: ParentIdentity
    high_risk: bool


@dataclass(frozen=True, slots=True)
class Acknowledgement:
    """Danger configuration and terminal streams for package-time consent."""

    danger_enabled: bool
    input_stream: TextIO
    output_stream: TextIO


@dataclass(frozen=True, slots=True)
class ApprovedDestination:
    """Destination decision safe to record in a package manifest later."""

    assessment: DestinationAssessment
    dangerous_confirmed: bool


def _read_mount_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    with Path("/proc/self/mountinfo").open(encoding="utf-8") as mountinfo:
        for line in mountinfo:
            before_separator, _separator, _after_separator = line.partition(" - ")
            fields = before_separator.split()
            if len(fields) > _MOUNT_POINT_INDEX:
                roots.append(Path(fields[_MOUNT_POINT_INDEX].replace("\\040", " ")))
    return tuple(roots)


def _is_at_or_below(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _reject_symlink_components(parent: Path) -> None:
    current = Path("/")
    for component in parent.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except FileNotFoundError as error:
            raise DestinationRejectedError(
                DestinationRejection.PARENT_MISSING
            ) from error
        if stat.S_ISLNK(metadata.st_mode):
            raise DestinationRejectedError(DestinationRejection.PARENT_SYMLINK)
        if not stat.S_ISDIR(metadata.st_mode):
            raise DestinationRejectedError(DestinationRejection.PARENT_NOT_DIRECTORY)


def _parse_absolute_path(raw: str) -> Path:
    if "\x00" in raw:
        raise DestinationRejectedError(DestinationRejection.NUL)
    pure_path = PurePosixPath(raw)
    if not pure_path.is_absolute():
        raise DestinationRejectedError(DestinationRejection.RELATIVE)
    if str(pure_path) != raw or any(part in {".", ".."} for part in pure_path.parts):
        raise DestinationRejectedError(DestinationRejection.TRAVERSAL)
    path = Path(pure_path)
    if path == Path("/"):
        raise DestinationRejectedError(DestinationRejection.ROOT)
    return path


def _reject_protected_roots(path: Path, policy: DestinationPolicy) -> None:
    if any(_is_at_or_below(path, root) for root in policy.pseudo_roots):
        raise DestinationRejectedError(DestinationRejection.PSEUDO)
    if any(_is_at_or_below(path, root) for root in policy.updater_roots):
        raise DestinationRejectedError(DestinationRejection.UPDATER)
    if path in policy.mount_roots or os.path.ismount(path):
        raise DestinationRejectedError(DestinationRejection.MOUNT)


def inspect_destination(raw: str, policy: DestinationPolicy) -> DestinationAssessment:
    """Parse a destination and capture its parent before any package decision."""
    path = _parse_absolute_path(raw)
    _reject_protected_roots(path, policy)
    _reject_symlink_components(path.parent)
    try:
        leaf_metadata = path.lstat()
    except FileNotFoundError:
        leaf_metadata = None
    if leaf_metadata is not None and not stat.S_ISDIR(leaf_metadata.st_mode):
        raise DestinationRejectedError(DestinationRejection.LEAF)
    parent_metadata = path.parent.lstat()
    parent_identity = ParentIdentity(
        path.parent,
        parent_metadata.st_dev,
        parent_metadata.st_ino,
    )
    service_home = policy.service_home or Path("/home") / policy.service_user
    if path == service_home:
        raise DestinationRejectedError(DestinationRejection.SERVICE_HOME)
    sensitive = any(part in _SENSITIVE_COMPONENTS for part in path.parts)
    high_risk = not path.is_relative_to(service_home) or sensitive
    return DestinationAssessment(path, parent_identity, high_risk)


def recheck_parent_identity(assessment: DestinationAssessment) -> None:
    """Reject a stale decision if its parent was swapped or became a symlink."""
    try:
        current = assessment.parent_identity.path.lstat()
    except FileNotFoundError as error:
        raise ParentIdentityChangedError(assessment.parent_identity.path) from error
    expected = assessment.parent_identity
    if (
        stat.S_ISLNK(current.st_mode)
        or current.st_dev != expected.device
        or current.st_ino != expected.inode
    ):
        raise ParentIdentityChangedError(expected.path)


def acknowledge_destination(
    assessment: DestinationAssessment,
    acknowledgement: Acknowledgement,
) -> ApprovedDestination:
    """Require danger configuration and exact interactive consent for high risk."""
    recheck_parent_identity(assessment)
    if not assessment.high_risk:
        return ApprovedDestination(assessment, dangerous_confirmed=False)
    if not acknowledgement.danger_enabled:
        raise DestinationRejectedError(DestinationRejection.DANGER_DISABLED)
    if not (
        acknowledgement.input_stream.isatty() and acknowledgement.output_stream.isatty()
    ):
        raise DestinationRejectedError(DestinationRejection.TTY)
    _ = acknowledgement.output_stream.write(
        "\x1b[31mHigh-risk destination. Type exact lowercase yes to continue: \x1b[0m"
    )
    acknowledgement.output_stream.flush()
    if acknowledgement.input_stream.readline().removesuffix("\n") != "yes":
        raise DestinationRejectedError(DestinationRejection.INEXACT)
    recheck_parent_identity(assessment)
    return ApprovedDestination(assessment, dangerous_confirmed=True)
