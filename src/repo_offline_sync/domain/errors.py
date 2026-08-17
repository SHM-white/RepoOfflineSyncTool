"""Structured domain failures returned by parsing and policy boundaries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Literal, TypeAlias, TypeVar

if TYPE_CHECKING:
    from typing_extensions import override
else:
    _Method = TypeVar("_Method", bound=Callable[..., str])

    def override(method: _Method) -> _Method:
        """Return a runtime identity decorator for Python 3.10."""
        return method


IdentityName: TypeAlias = Literal[
    "PackageId",
    "RepoId",
    "TargetId",
    "TransactionId",
    "MediaId",
    "SegmentId",
]
IdentifierFailureReason: TypeAlias = Literal["empty", "surrounding-whitespace"]
OidName: TypeAlias = Literal["GitOid", "LfsOid"]
OidFailureReason: TypeAlias = Literal["nonhex", "wrong-length"]
QuantityName: TypeAlias = Literal["Generation", "PositiveBytes", "PositiveSeconds"]
VariantName: TypeAlias = Literal[
    "BundleKind",
    "TransactionPhase",
    "ActionPhase",
    "FailurePolicy",
    "Filesystem",
    "ResultStatus",
]


@dataclass(frozen=True, slots=True)
class InvalidIdentifier:
    """A raw identity did not satisfy its canonical string boundary."""

    identity: IdentityName
    value: str
    reason: IdentifierFailureReason

    @override
    def __str__(self) -> str:
        """Render the rejected identity and reason."""
        return f"invalid {self.identity} {self.value!r}: {self.reason}"


@dataclass(frozen=True, slots=True)
class InvalidOid:
    """A raw object identity was not valid for the requested object family."""

    oid: OidName
    value: str
    expected_lengths: tuple[int, ...]
    reason: OidFailureReason

    @override
    def __str__(self) -> str:
        """Render the rejected OID and its required lengths."""
        lengths = ", ".join(str(length) for length in self.expected_lengths)
        return f"invalid {self.oid} {self.value!r}: {self.reason}; lengths={lengths}"


@dataclass(frozen=True, slots=True)
class InvalidQuantity:
    """A numeric value was below the minimum for its domain quantity."""

    quantity: QuantityName
    value: int
    minimum: int

    @override
    def __str__(self) -> str:
        """Render the rejected quantity and inclusive minimum."""
        return f"invalid {self.quantity} {self.value!r}: minimum is {self.minimum}"


@dataclass(frozen=True, slots=True)
class InvalidQuantityError(ValueError):
    """Raised when direct value construction violates a quantity invariant."""

    failure: InvalidQuantity

    @override
    def __str__(self) -> str:
        """Render the structured quantity failure."""
        return str(self.failure)


@dataclass(frozen=True, slots=True)
class UnknownVariant:
    """A serialized value was not a member of a closed domain variant."""

    variant: VariantName
    value: str

    @override
    def __str__(self) -> str:
        """Render the closed variant family and rejected value."""
        return f"unknown {self.variant} variant {self.value!r}"


_Phase = TypeVar("_Phase")


@dataclass(frozen=True, slots=True)
class IllegalTransition(Generic[_Phase]):
    """A requested transaction phase is not reachable from the current phase."""

    current: _Phase
    requested: _Phase

    @override
    def __str__(self) -> str:
        """Render the rejected transition edge."""
        return f"illegal transaction transition: {self.current} -> {self.requested}"
