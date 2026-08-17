from __future__ import annotations

from repo_offline_sync.domain.errors import (
    IllegalTransition,
    InvalidIdentifier,
    InvalidOid,
    InvalidQuantity,
    UnknownVariant,
)
from repo_offline_sync.domain.policies import TransactionPhase


def test_typed_failures_expose_fields_and_meaningful_text() -> None:
    # Given one instance of each expected domain failure variant
    failures = (
        InvalidIdentifier(identity="PackageId", value="", reason="empty"),
        InvalidOid(
            oid="GitOid", value="xyz", expected_lengths=(40, 64), reason="nonhex"
        ),
        InvalidQuantity(quantity="PositiveBytes", value=0, minimum=1),
        UnknownVariant(variant="Filesystem", value="fat32"),
        IllegalTransition(
            current=TransactionPhase.COMMITTED,
            requested=TransactionPhase.DISCOVERED,
        ),
    )

    # When each failure is rendered for diagnostics
    rendered = tuple(str(failure) for failure in failures)

    # Then every message is nonempty and includes the rejected value or state
    assert all(rendered)
    assert "PackageId" in rendered[0]
    assert "xyz" in rendered[1]
    assert "0" in rendered[2]
    assert "fat32" in rendered[3]
    assert "committed" in rendered[4]
