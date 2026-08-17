"""Private serialization capability for local profile storage."""

from __future__ import annotations

import json

from repo_offline_sync.packaging._profile_models import (
    PairingToken,
    RepoId,
    RepoProfile,
    RepositorySource,
)


def _token_plaintext(token: PairingToken) -> str:
    match token:
        case PairingToken(value):
            return value


def encode_profile(profile: RepoProfile) -> str:
    """Encode one profile through this module-private token capability."""
    return (
        json.dumps(
            {
                "common_git_dir": str(profile.source.common_git_dir),
                "danger_enabled": profile.settings.danger_enabled,
                "destination": profile.settings.destination,
                "provenance_remote": profile.source.provenance_remote,
                "repo_id": profile.repo_id.value,
                "service_user": profile.settings.service_user,
                "target_token": _token_plaintext(profile.token),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def encode_identity(source: RepositorySource, repo_id: RepoId) -> str:
    """Encode one repository identity index record."""
    return (
        json.dumps(
            {
                "common_git_dir": str(source.common_git_dir),
                "provenance_remote": source.provenance_remote,
                "repo_id": repo_id.value,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
