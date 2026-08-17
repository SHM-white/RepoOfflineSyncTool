"""Versioned reusable packaging-profile protocol."""

from __future__ import annotations

from pathlib import Path

from repo_offline_sync.packaging.profiles import (
    PairingToken,
    ProfileFormatError,
    ProfileSettings,
    RepoId,
    RepoProfile,
    RepositorySource,
)
from repo_offline_sync.protocol._paths import parse_absolute_path
from repo_offline_sync.protocol._sensitive import token_plaintext
from repo_offline_sync.protocol.json_boundary import (
    JsonObject,
    ProtocolError,
    ProtocolReason,
    canonical_bytes,
    decode_json,
    require_boolean,
    require_schema,
    require_string,
)

_FIELDS = frozenset(
    {
        "schema",
        "repo_id",
        "common_git_dir",
        "provenance_remote",
        "destination",
        "service_user",
        "danger_enabled",
        "target_token",
    }
)


def parse_profile(data: bytes) -> RepoProfile:
    """Parse profile bytes without exposing pairing plaintext."""
    document = decode_json(data)
    document.require_exact(_FIELDS)
    require_schema(document, "profile")
    try:
        token = PairingToken.parse(require_string(document, "target_token"))
    except ProfileFormatError as error:
        raise ProtocolError(ProtocolReason.MALFORMED_TOKEN, "target_token") from error
    try:
        repo_id = RepoId.parse(require_string(document, "repo_id"))
        settings = ProfileSettings(
            parse_absolute_path(require_string(document, "destination")),
            require_string(document, "service_user"),
            require_boolean(document, "danger_enabled"),
        )
    except ProfileFormatError as error:
        raise ProtocolError(ProtocolReason.INVALID_VALUE) from error
    common_git_dir = parse_absolute_path(require_string(document, "common_git_dir"))
    provenance = require_string(document, "provenance_remote")
    if not provenance or "\x00" in provenance:
        raise ProtocolError(ProtocolReason.INVALID_VALUE, "provenance_remote")
    return RepoProfile(
        repo_id,
        RepositorySource(Path(common_git_dir), provenance),
        settings,
        token,
    )


def encode_profile(profile: RepoProfile) -> bytes:
    """Encode profile v1 through the narrow token capability."""
    return canonical_bytes(
        JsonObject(
            (
                ("schema", "profile-v1"),
                ("repo_id", profile.repo_id.value),
                (
                    "common_git_dir",
                    parse_absolute_path(profile.source.common_git_dir.as_posix()),
                ),
                ("provenance_remote", profile.source.provenance_remote),
                ("destination", parse_absolute_path(profile.settings.destination)),
                ("service_user", profile.settings.service_user),
                ("danger_enabled", profile.settings.danger_enabled),
                ("target_token", token_plaintext(profile.token)),
            )
        )
    )
