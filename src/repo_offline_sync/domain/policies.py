"""Closed updater variants and pure transition policies."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum, unique
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias, TypeVar

from repo_offline_sync.domain.errors import (
    IllegalTransition,
    UnknownVariant,
    VariantName,
)
from repo_offline_sync.domain.outcomes import TransitionApplied

if TYPE_CHECKING:
    from typing_extensions import override
else:
    _Method = TypeVar("_Method", bound=Callable[..., str])

    def override(method: _Method) -> _Method:
        """Return a runtime identity decorator for Python 3.10."""
        return method


def assert_never(unreachable: NoReturn) -> NoReturn:
    """Fail if static exhaustiveness permits an impossible value at runtime."""
    message = f"unhandled variant: {unreachable!r}"
    raise AssertionError(message)


class StrEnum(str, Enum):
    """Python 3.10-compatible string enum base."""

    @override
    def __str__(self) -> str:
        """Return the serialized string value."""
        return str.__str__(self)


@unique
class BundleKind(StrEnum):
    """Supported Git bundle shapes."""

    FULL = "full"
    INCREMENTAL = "incremental"


@unique
class TransactionPhase(StrEnum):
    """Durable update transaction phases consumed by the later state machine."""

    DISCOVERED = "discovered"
    COPIED = "copied"
    VERIFIED = "verified"
    IMPORTED = "imported"
    SNAPSHOT_CREATED = "snapshot-created"
    STAGED = "staged"
    BUILT = "built"
    ACTIVATING = "activating"
    ACTIVATED = "activated"
    HEALTH_CHECKED = "health-checked"
    COMMITTED = "committed"
    REJECTED = "rejected"
    NO_OP = "no-op"
    NEEDS_FULL = "needs-full"
    ROLLED_BACK = "rolled-back"
    FAILED_PRESERVED = "failed-preserved"
    RECOVERY_FAILED = "recovery-failed"


@unique
class ActionPhase(StrEnum):
    """Finite package action execution points."""

    PREFLIGHT = "preflight"
    BUILD = "build"
    PRE_ACTIVATE = "pre-activate"
    POST_ACTIVATE = "post-activate"
    HEALTH = "health"


@unique
class FailurePolicy(StrEnum):
    """Allowed behavior after activation or health failure."""

    ROLLBACK = "rollback"
    KEEP_FAILED_STOPPED = "keep-failed-stopped"


@unique
class Filesystem(StrEnum):
    """Supported removable-media filesystem implementations."""

    EXT4 = "ext4"
    EXFAT = "exfat"
    NTFS3 = "ntfs3"
    NTFS_3G = "ntfs-3g"


@unique
class ResultStatus(StrEnum):
    """Machine-readable updater result classes."""

    SUCCESS = "success"
    NO_OP = "no-op"
    NEEDS_FULL_BUNDLE = "needs-full-bundle"
    REJECTED = "rejected"
    FAILED_ROLLED_BACK = "failed-rolled-back"
    FAILED_PRESERVED = "failed-preserved"
    RECOVERY_FAILED = "recovery-failed"
    MEDIA_IO_FAILURE = "media-io-failure"


_Variant = TypeVar("_Variant", bound=StrEnum)
_EarlyPhase: TypeAlias = Literal[
    TransactionPhase.DISCOVERED,
    TransactionPhase.COPIED,
    TransactionPhase.VERIFIED,
]
_MiddlePhase: TypeAlias = Literal[
    TransactionPhase.IMPORTED,
    TransactionPhase.SNAPSHOT_CREATED,
    TransactionPhase.STAGED,
    TransactionPhase.BUILT,
]
_ActivationPhase: TypeAlias = Literal[
    TransactionPhase.ACTIVATING,
    TransactionPhase.ACTIVATED,
    TransactionPhase.HEALTH_CHECKED,
]


def _parse_variant(
    raw: str,
    variant: VariantName,
    enum_type: type[_Variant],
) -> _Variant | UnknownVariant:
    try:
        return enum_type(raw)
    except ValueError:
        return UnknownVariant(variant=variant, value=raw)


def parse_bundle_kind(raw: str) -> BundleKind | UnknownVariant:
    """Parse a bundle kind."""
    return _parse_variant(raw, "BundleKind", BundleKind)


def parse_transaction_phase(raw: str) -> TransactionPhase | UnknownVariant:
    """Parse a transaction phase."""
    return _parse_variant(raw, "TransactionPhase", TransactionPhase)


def parse_action_phase(raw: str) -> ActionPhase | UnknownVariant:
    """Parse an action phase."""
    return _parse_variant(raw, "ActionPhase", ActionPhase)


def parse_failure_policy(raw: str) -> FailurePolicy | UnknownVariant:
    """Parse a package failure policy."""
    return _parse_variant(raw, "FailurePolicy", FailurePolicy)


def parse_filesystem(raw: str) -> Filesystem | UnknownVariant:
    """Parse a supported filesystem implementation."""
    return _parse_variant(raw, "Filesystem", Filesystem)


def parse_result_status(raw: str) -> ResultStatus | UnknownVariant:
    """Parse a machine-readable result status."""
    return _parse_variant(raw, "ResultStatus", ResultStatus)


def _early_transaction_phases(current: _EarlyPhase) -> tuple[TransactionPhase, ...]:
    recovery_failed = TransactionPhase.RECOVERY_FAILED
    match current:
        case TransactionPhase.DISCOVERED:
            return (
                TransactionPhase.COPIED,
                TransactionPhase.REJECTED,
                TransactionPhase.NO_OP,
                TransactionPhase.NEEDS_FULL,
                recovery_failed,
            )
        case TransactionPhase.COPIED:
            return TransactionPhase.VERIFIED, TransactionPhase.REJECTED, recovery_failed
        case TransactionPhase.VERIFIED:
            return (
                TransactionPhase.IMPORTED,
                TransactionPhase.REJECTED,
                TransactionPhase.NEEDS_FULL,
                recovery_failed,
            )
    assert_never(current)


def _middle_transaction_phases(current: _MiddlePhase) -> tuple[TransactionPhase, ...]:
    recovery_failed = TransactionPhase.RECOVERY_FAILED
    match current:
        case TransactionPhase.IMPORTED:
            return TransactionPhase.SNAPSHOT_CREATED, recovery_failed
        case TransactionPhase.SNAPSHOT_CREATED:
            return TransactionPhase.STAGED, recovery_failed
        case TransactionPhase.STAGED:
            return TransactionPhase.BUILT, recovery_failed
        case TransactionPhase.BUILT:
            return TransactionPhase.ACTIVATING, recovery_failed
    assert_never(current)


def _activation_transaction_phases(
    current: _ActivationPhase,
) -> tuple[TransactionPhase, ...]:
    recovery_failed = TransactionPhase.RECOVERY_FAILED
    match current:
        case TransactionPhase.ACTIVATING:
            return (
                TransactionPhase.ACTIVATED,
                TransactionPhase.ROLLED_BACK,
                TransactionPhase.FAILED_PRESERVED,
                recovery_failed,
            )
        case TransactionPhase.ACTIVATED:
            return (
                TransactionPhase.HEALTH_CHECKED,
                TransactionPhase.ROLLED_BACK,
                TransactionPhase.FAILED_PRESERVED,
                recovery_failed,
            )
        case TransactionPhase.HEALTH_CHECKED:
            return (
                TransactionPhase.COMMITTED,
                TransactionPhase.ROLLED_BACK,
                TransactionPhase.FAILED_PRESERVED,
                recovery_failed,
            )
    assert_never(current)


def _allowed_transaction_phases(
    current: TransactionPhase,
) -> tuple[TransactionPhase, ...]:
    match current:
        case (
            TransactionPhase.DISCOVERED
            | TransactionPhase.COPIED
            | TransactionPhase.VERIFIED
        ):
            return _early_transaction_phases(current)
        case (
            TransactionPhase.IMPORTED
            | TransactionPhase.SNAPSHOT_CREATED
            | TransactionPhase.STAGED
            | TransactionPhase.BUILT
        ):
            return _middle_transaction_phases(current)
        case (
            TransactionPhase.ACTIVATING
            | TransactionPhase.ACTIVATED
            | TransactionPhase.HEALTH_CHECKED
        ):
            return _activation_transaction_phases(current)
        case (
            TransactionPhase.COMMITTED
            | TransactionPhase.REJECTED
            | TransactionPhase.NO_OP
            | TransactionPhase.NEEDS_FULL
            | TransactionPhase.ROLLED_BACK
            | TransactionPhase.FAILED_PRESERVED
            | TransactionPhase.RECOVERY_FAILED
        ):
            return ()
    assert_never(current)


def transition_transaction(
    current: TransactionPhase,
    requested: TransactionPhase,
) -> TransitionApplied[TransactionPhase] | IllegalTransition[TransactionPhase]:
    """Apply a legal in-memory phase transition or return its typed rejection."""
    if requested in _allowed_transaction_phases(current):
        return TransitionApplied(previous=current, current=requested)
    return IllegalTransition(current=current, requested=requested)


def failure_terminal_phase(policy: FailurePolicy) -> TransactionPhase:
    """Select the terminal phase required by a package failure policy."""
    match policy:
        case FailurePolicy.ROLLBACK:
            return TransactionPhase.ROLLED_BACK
        case FailurePolicy.KEEP_FAILED_STOPPED:
            return TransactionPhase.FAILED_PRESERVED
    assert_never(policy)
