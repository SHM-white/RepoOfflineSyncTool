"""Target-side mismatch checking without packaging risk classification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from repo_offline_sync._typing import override
from repo_offline_sync.packaging.destination_policy import (
    DestinationAssessment,
    recheck_parent_identity,
)

if TYPE_CHECKING:
    from repo_offline_sync.packaging.profiles import PairingToken


@dataclass(frozen=True, slots=True)
class TargetIdentityMismatchError(Exception):
    """Report a plaintext target-token mismatch without exposing either token."""

    @override
    def __str__(self) -> str:
        """Render a mismatch without exposing either plaintext token."""
        return "target pairing token mismatch"


def verify_target_identity(
    expected: PairingToken,
    presented: PairingToken,
    destination: DestinationAssessment,
) -> None:
    """Compare mismatch tokens and recheck structural destination invariants."""
    if not expected.matches(presented):
        raise TargetIdentityMismatchError
    recheck_parent_identity(destination)
