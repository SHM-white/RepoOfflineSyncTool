"""Canonical manifest encoding kept separate from graph parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from repo_offline_sync.protocol import _domain
from repo_offline_sync.protocol._manifest_parts import (
    action_json,
    lfs_json,
    repository_json,
    route_json,
    segment_json,
)
from repo_offline_sync.protocol._paths import parse_absolute_path, parse_relative_path
from repo_offline_sync.protocol._sensitive import token_plaintext
from repo_offline_sync.protocol.json_boundary import (
    JsonArray,
    JsonObject,
    ProtocolError,
    ProtocolReason,
    canonical_bytes,
)

if TYPE_CHECKING:
    from repo_offline_sync.domain.identifiers import PositiveBytes
    from repo_offline_sync.domain.models import Package
    from repo_offline_sync.packaging.profiles import PairingToken


class _ArtifactLike(Protocol):
    @property
    def path(self) -> str: ...

    @property
    def sha256(self) -> str: ...

    @property
    def byte_size(self) -> PositiveBytes: ...


class _ManifestLike(Protocol):
    @property
    def package(self) -> Package: ...

    @property
    def destination(self) -> str: ...

    @property
    def persistent_paths(self) -> tuple[str, ...]: ...

    @property
    def pairing_token(self) -> PairingToken: ...

    @property
    def dangerous_confirmed(self) -> bool: ...

    @property
    def artifacts(self) -> tuple[_ArtifactLike, ...]: ...


def _artifact_json(artifact: _ArtifactLike) -> JsonObject:
    return JsonObject(
        (
            ("path", artifact.path),
            ("sha256", artifact.sha256),
            ("byte_size", artifact.byte_size.value),
        )
    )


def _validated_paths(manifest: _ManifestLike) -> tuple[str, ...]:
    persistent = tuple(parse_relative_path(path) for path in manifest.persistent_paths)
    if len(persistent) != len(set(persistent)):
        raise ProtocolError(ProtocolReason.DUPLICATE_VALUE, "persistent_paths")
    artifact_paths = tuple(
        parse_relative_path(artifact.path) for artifact in manifest.artifacts
    )
    if len(artifact_paths) != len(set(artifact_paths)):
        raise ProtocolError(ProtocolReason.DUPLICATE_VALUE, "artifacts.path")
    for artifact in manifest.artifacts:
        _ = _domain.sha256(JsonObject((("sha256", artifact.sha256),)), "sha256")
    return persistent


def encode_manifest_value(manifest: _ManifestLike) -> bytes:
    """Encode an already graph-validated manifest."""
    package = manifest.package
    persistent_paths = _validated_paths(manifest)
    return canonical_bytes(
        JsonObject(
            (
                ("schema", "manifest-v1"),
                ("package_id", package.package_id),
                ("target_id", package.target_id),
                ("media_id", package.media_id),
                ("generation", package.generation.value),
                ("target_commit", package.target_commit),
                (
                    "repositories",
                    JsonArray(
                        tuple(repository_json(item) for item in package.repositories)
                    ),
                ),
                (
                    "routes",
                    JsonArray(tuple(route_json(item) for item in package.routes)),
                ),
                (
                    "segments",
                    JsonArray(tuple(segment_json(item) for item in package.segments)),
                ),
                (
                    "lfs_objects",
                    JsonArray(tuple(lfs_json(item) for item in package.lfs_objects)),
                ),
                (
                    "actions",
                    JsonArray(tuple(action_json(item) for item in package.actions)),
                ),
                ("failure_policy", package.failure_policy.value),
                ("filesystem", package.filesystem.value),
                ("full_fallback_included", package.full_fallback_included),
                ("destination", parse_absolute_path(manifest.destination)),
                ("persistent_paths", JsonArray(persistent_paths)),
                ("pairing_token", token_plaintext(manifest.pairing_token)),
                ("dangerous_confirmed", manifest.dangerous_confirmed),
                (
                    "artifacts",
                    JsonArray(
                        tuple(_artifact_json(item) for item in manifest.artifacts)
                    ),
                ),
            )
        )
    )
