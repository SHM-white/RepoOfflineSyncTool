from __future__ import annotations

from collections.abc import Callable

import pytest

from repo_offline_sync.domain.errors import IllegalTransition, UnknownVariant
from repo_offline_sync.domain.outcomes import TransitionApplied
from repo_offline_sync.domain.policies import (
    ActionPhase,
    BundleKind,
    FailurePolicy,
    Filesystem,
    ResultStatus,
    StrEnum,
    TransactionPhase,
    failure_terminal_phase,
    parse_action_phase,
    parse_bundle_kind,
    parse_failure_policy,
    parse_filesystem,
    parse_result_status,
    parse_transaction_phase,
    transition_transaction,
)

VariantParser = Callable[[str], StrEnum | UnknownVariant]


@pytest.mark.parametrize(
    ("parser", "raw", "expected"),
    [
        (parse_bundle_kind, "incremental", BundleKind.INCREMENTAL),
        (
            parse_transaction_phase,
            "snapshot-created",
            TransactionPhase.SNAPSHOT_CREATED,
        ),
        (parse_action_phase, "pre-activate", ActionPhase.PRE_ACTIVATE),
        (
            parse_failure_policy,
            "keep-failed-stopped",
            FailurePolicy.KEEP_FAILED_STOPPED,
        ),
        (parse_filesystem, "ntfs-3g", Filesystem.NTFS_3G),
        (parse_result_status, "failed-rolled-back", ResultStatus.FAILED_ROLLED_BACK),
    ],
)
def test_variant_parser_returns_expected_string_enum(
    parser: VariantParser,
    raw: str,
    expected: StrEnum,
) -> None:
    # Given a known serialized variant

    # When the variant is parsed
    parsed = parser(raw)

    # Then its exact typed enum member is returned
    assert parsed is expected
    assert str(parsed) == raw


@pytest.mark.parametrize(
    "parser",
    [
        parse_bundle_kind,
        parse_transaction_phase,
        parse_action_phase,
        parse_failure_policy,
        parse_filesystem,
        parse_result_status,
    ],
)
def test_variant_parser_returns_typed_failure_when_variant_is_unknown(
    parser: VariantParser,
) -> None:
    # Given an unknown serialized variant

    # When it crosses an enum boundary
    parsed = parser("unknown")

    # Then no enum member is yielded
    assert isinstance(parsed, UnknownVariant)
    assert parsed.value == "unknown"


@pytest.mark.parametrize(
    ("current", "requested"),
    [
        (TransactionPhase.DISCOVERED, TransactionPhase.COPIED),
        (TransactionPhase.COPIED, TransactionPhase.VERIFIED),
        (TransactionPhase.VERIFIED, TransactionPhase.IMPORTED),
        (TransactionPhase.IMPORTED, TransactionPhase.SNAPSHOT_CREATED),
        (TransactionPhase.SNAPSHOT_CREATED, TransactionPhase.STAGED),
        (TransactionPhase.STAGED, TransactionPhase.BUILT),
        (TransactionPhase.BUILT, TransactionPhase.ACTIVATING),
        (TransactionPhase.ACTIVATING, TransactionPhase.ACTIVATED),
        (TransactionPhase.ACTIVATED, TransactionPhase.HEALTH_CHECKED),
        (TransactionPhase.HEALTH_CHECKED, TransactionPhase.COMMITTED),
        (TransactionPhase.DISCOVERED, TransactionPhase.NO_OP),
        (TransactionPhase.VERIFIED, TransactionPhase.NEEDS_FULL),
        (TransactionPhase.ACTIVATED, TransactionPhase.ROLLED_BACK),
    ],
)
def test_transaction_transition_is_applied_when_edge_is_legal(
    current: TransactionPhase,
    requested: TransactionPhase,
) -> None:
    # Given a legal in-memory transaction edge

    # When the pure transition policy is evaluated
    result = transition_transaction(current, requested)

    # Then the transition is represented explicitly
    assert result == TransitionApplied(previous=current, current=requested)


@pytest.mark.parametrize(
    ("current", "requested"),
    [
        (TransactionPhase.DISCOVERED, TransactionPhase.VERIFIED),
        (TransactionPhase.COMMITTED, TransactionPhase.DISCOVERED),
        (TransactionPhase.NO_OP, TransactionPhase.COPIED),
    ],
)
def test_transaction_transition_returns_typed_failure_when_edge_is_illegal(
    current: TransactionPhase,
    requested: TransactionPhase,
) -> None:
    # Given a skipped, backward, or terminal-state transition

    # When the pure transition policy is evaluated
    result = transition_transaction(current, requested)

    # Then no successor state is yielded
    assert isinstance(result, IllegalTransition)
    assert result.current is current
    assert result.requested is requested


def test_transaction_transition_matrix_classifies_all_phase_pairs() -> None:
    # Given every current/requested phase pair
    results = tuple(
        transition_transaction(current, requested)
        for current in TransactionPhase
        for requested in TransactionPhase
    )

    # When successful and rejected transitions are classified
    applied = tuple(
        result for result in results if isinstance(result, TransitionApplied)
    )
    rejected = tuple(
        result for result in results if isinstance(result, IllegalTransition)
    )

    # Then all 289 pairs have exactly one established policy outcome
    assert len(results) == 289
    assert len(applied) == 32
    assert len(rejected) == 257


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (FailurePolicy.ROLLBACK, TransactionPhase.ROLLED_BACK),
        (FailurePolicy.KEEP_FAILED_STOPPED, TransactionPhase.FAILED_PRESERVED),
    ],
)
def test_failure_policy_selects_typed_terminal_phase(
    policy: FailurePolicy,
    expected: TransactionPhase,
) -> None:
    # Given a package failure policy

    # When its terminal transaction phase is selected
    terminal = failure_terminal_phase(policy)

    # Then the policy maps to the exact terminal state
    assert terminal is expected
