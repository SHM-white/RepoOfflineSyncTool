"""Narrow protocol-only capability for required pairing-token serialization."""

from __future__ import annotations

from repo_offline_sync.packaging.profiles import PairingToken


def token_plaintext(token: PairingToken) -> str:
    """Extract plaintext only while constructing required protocol bytes."""
    match token:
        case PairingToken(value):
            return value
