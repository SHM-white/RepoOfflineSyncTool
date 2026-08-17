"""Branded identities and validated numeric values.

Runtime consumers import parser functions. Consumers needing identity annotations
import their branded names inside ``if TYPE_CHECKING`` blocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from string import hexdigits
from typing import TYPE_CHECKING

from repo_offline_sync.domain.errors import (
    IdentifierFailureReason,
    IdentityName,
    InvalidIdentifier,
    InvalidOid,
    InvalidQuantity,
    InvalidQuantityError,
    OidName,
    QuantityName,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Annotated, NewType, TypeAlias, TypeVar

    _PackageId = NewType("_PackageId", str)
    _RepoId = NewType("_RepoId", str)
    _TargetId = NewType("_TargetId", str)
    _TransactionId = NewType("_TransactionId", str)
    _MediaId = NewType("_MediaId", str)
    _SegmentId = NewType("_SegmentId", str)
    _GitOid = NewType("_GitOid", str)
    _LfsOid = NewType("_LfsOid", str)

    PackageId: TypeAlias = Annotated[_PackageId, "validated"]
    RepoId: TypeAlias = Annotated[_RepoId, "validated"]
    TargetId: TypeAlias = Annotated[_TargetId, "validated"]
    TransactionId: TypeAlias = Annotated[_TransactionId, "validated"]
    MediaId: TypeAlias = Annotated[_MediaId, "validated"]
    SegmentId: TypeAlias = Annotated[_SegmentId, "validated"]
    GitOid: TypeAlias = Annotated[_GitOid, "validated"]
    LfsOid: TypeAlias = Annotated[_LfsOid, "validated"]

    _Identity = TypeVar("_Identity")
    _Oid = TypeVar("_Oid")

    def _make_package_id(raw: str) -> PackageId:
        return _PackageId(raw)

    def _make_repo_id(raw: str) -> RepoId:
        return _RepoId(raw)

    def _make_target_id(raw: str) -> TargetId:
        return _TargetId(raw)

    def _make_transaction_id(raw: str) -> TransactionId:
        return _TransactionId(raw)

    def _make_media_id(raw: str) -> MediaId:
        return _MediaId(raw)

    def _make_segment_id(raw: str) -> SegmentId:
        return _SegmentId(raw)

    def _make_git_oid(raw: str) -> GitOid:
        return _GitOid(raw)

    def _make_lfs_oid(raw: str) -> LfsOid:
        return _LfsOid(raw)
else:

    def _validated_string(raw: str) -> str:
        return raw

    _make_package_id = _validated_string
    _make_repo_id = _validated_string
    _make_target_id = _validated_string
    _make_transaction_id = _validated_string
    _make_media_id = _validated_string
    _make_segment_id = _validated_string
    _make_git_oid = _validated_string
    _make_lfs_oid = _validated_string


def _parse_identifier(
    raw: str,
    identity: IdentityName,
    constructor: Callable[[str], _Identity],
) -> _Identity | InvalidIdentifier:
    stripped = raw.strip()
    reason: IdentifierFailureReason | None = None
    if not stripped:
        reason = "empty"
    elif stripped != raw:
        reason = "surrounding-whitespace"
    if reason is not None:
        return InvalidIdentifier(identity=identity, value=raw, reason=reason)
    return constructor(raw)


def parse_package_id(raw: str) -> PackageId | InvalidIdentifier:
    """Parse a package identity."""
    return _parse_identifier(raw, "PackageId", _make_package_id)


def parse_repo_id(raw: str) -> RepoId | InvalidIdentifier:
    """Parse a repository identity."""
    return _parse_identifier(raw, "RepoId", _make_repo_id)


def parse_target_id(raw: str) -> TargetId | InvalidIdentifier:
    """Parse a target identity."""
    return _parse_identifier(raw, "TargetId", _make_target_id)


def parse_transaction_id(raw: str) -> TransactionId | InvalidIdentifier:
    """Parse a transaction identity."""
    return _parse_identifier(raw, "TransactionId", _make_transaction_id)


def parse_media_id(raw: str) -> MediaId | InvalidIdentifier:
    """Parse a removable-media identity."""
    return _parse_identifier(raw, "MediaId", _make_media_id)


def parse_segment_id(raw: str) -> SegmentId | InvalidIdentifier:
    """Parse a bundle-segment identity."""
    return _parse_identifier(raw, "SegmentId", _make_segment_id)


def _parse_oid(
    raw: str,
    oid: OidName,
    expected_lengths: tuple[int, ...],
    constructor: Callable[[str], _Oid],
) -> _Oid | InvalidOid:
    if len(raw) not in expected_lengths:
        return InvalidOid(oid, raw, expected_lengths, "wrong-length")
    if not all(character in hexdigits for character in raw):
        return InvalidOid(oid, raw, expected_lengths, "nonhex")
    return constructor(raw.lower())


def parse_git_oid(raw: str) -> GitOid | InvalidOid:
    """Parse a SHA-1 or SHA-256 Git object identity."""
    return _parse_oid(raw, "GitOid", (40, 64), _make_git_oid)


def parse_lfs_oid(raw: str) -> LfsOid | InvalidOid:
    """Parse a SHA-256 Git LFS object identity."""
    return _parse_oid(raw, "LfsOid", (64,), _make_lfs_oid)


def _raise_below_minimum(
    quantity: QuantityName,
    value: int,
    minimum: int,
) -> None:
    if value is True or value is False or value < minimum:
        failure = InvalidQuantity(quantity=quantity, value=value, minimum=minimum)
        raise InvalidQuantityError(failure=failure)


@dataclass(frozen=True, slots=True)
class Generation:
    """A nonnegative package generation."""

    value: int

    def __post_init__(self) -> None:
        """Reject negative generations before an instance can exist."""
        _raise_below_minimum("Generation", self.value, 0)


@dataclass(frozen=True, slots=True)
class PositiveBytes:
    """A byte count greater than zero."""

    value: int

    def __post_init__(self) -> None:
        """Reject nonpositive byte counts before an instance can exist."""
        _raise_below_minimum("PositiveBytes", self.value, 1)


@dataclass(frozen=True, slots=True)
class PositiveSeconds:
    """A duration in whole seconds greater than zero."""

    value: int

    def __post_init__(self) -> None:
        """Reject nonpositive durations before an instance can exist."""
        _raise_below_minimum("PositiveSeconds", self.value, 1)


def parse_generation(raw: int) -> Generation | InvalidQuantity:
    """Parse a nonnegative package generation."""
    try:
        return Generation(raw)
    except InvalidQuantityError as error:
        return error.failure


def parse_positive_bytes(raw: int) -> PositiveBytes | InvalidQuantity:
    """Parse a positive byte count."""
    try:
        return PositiveBytes(raw)
    except InvalidQuantityError as error:
        return error.failure


def parse_positive_seconds(raw: int) -> PositiveSeconds | InvalidQuantity:
    """Parse a positive whole-second duration."""
    try:
        return PositiveSeconds(raw)
    except InvalidQuantityError as error:
        return error.failure
