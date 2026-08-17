"""Versioned durable transaction-state record contract for Task 6 consumers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from repo_offline_sync.domain.models import Transaction
from repo_offline_sync.protocol import _domain
from repo_offline_sync.protocol.json_boundary import (
    JsonObject,
    canonical_bytes,
    decode_json,
    require_schema,
)

if TYPE_CHECKING:
    from repo_offline_sync.domain.identifiers import Generation, GitOid


@dataclass(frozen=True, slots=True)
class StateDocument:
    """Minimal durable identity, generation, target, and legal phase checkpoint."""

    transaction: Transaction
    generation: Generation
    target_commit: GitOid


def parse_state(data: bytes) -> StateDocument:
    """Parse one complete durable state checkpoint."""
    document = decode_json(data)
    document.require_exact(
        frozenset(
            {
                "schema",
                "transaction_id",
                "package_id",
                "target_id",
                "phase",
                "generation",
                "target_commit",
            }
        )
    )
    require_schema(document, "state")
    transaction = Transaction(
        _domain.transaction_id(document),
        _domain.package_id(document),
        _domain.target_id(document),
        _domain.transaction_phase(document),
    )
    return StateDocument(
        transaction,
        _domain.generation(document),
        _domain.git_oid(document, "target_commit"),
    )


def encode_state(state: StateDocument) -> bytes:
    """Encode canonical durable state v1 bytes."""
    transaction = state.transaction
    return canonical_bytes(
        JsonObject(
            (
                ("schema", "state-v1"),
                ("transaction_id", transaction.transaction_id),
                ("package_id", transaction.package_id),
                ("target_id", transaction.target_id),
                ("phase", transaction.phase.value),
                ("generation", state.generation.value),
                ("target_commit", state.target_commit),
            )
        )
    )
