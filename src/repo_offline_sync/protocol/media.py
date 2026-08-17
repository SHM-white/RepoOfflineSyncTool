"""Versioned removable-media marker and READY publication contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from repo_offline_sync.protocol import _domain
from repo_offline_sync.protocol._paths import parse_relative_path
from repo_offline_sync.protocol.json_boundary import (
    JsonArray,
    JsonObject,
    ProtocolError,
    ProtocolReason,
    as_object,
    canonical_bytes,
    decode_json,
    require_array,
    require_schema,
    require_string,
)

if TYPE_CHECKING:
    from repo_offline_sync.domain.identifiers import MediaId, PackageId, PositiveBytes
    from repo_offline_sync.domain.policies import Filesystem


@dataclass(frozen=True, slots=True)
class MediaMarker:
    """Identity and supported filesystem recorded on initialized media."""

    media_id: MediaId
    filesystem: Filesystem


@dataclass(frozen=True, slots=True)
class ReadyArtifact:
    """One final package file bound by READY publication."""

    path: str
    sha256: str
    crc32: str
    byte_size: PositiveBytes


@dataclass(frozen=True, slots=True)
class ReadyMarker:
    """Final package publication marker written only after complete verification."""

    package_id: PackageId
    manifest_sha256: str
    artifacts: tuple[ReadyArtifact, ...]


def parse_media(data: bytes) -> MediaMarker:
    """Parse one initialized removable-media marker."""
    document = decode_json(data)
    document.require_exact(frozenset({"schema", "media_id", "filesystem"}))
    require_schema(document, "media")
    return MediaMarker(_domain.media_id(document), _domain.filesystem(document))


def encode_media(marker: MediaMarker) -> bytes:
    """Encode canonical media marker v1 bytes."""
    return canonical_bytes(
        JsonObject(
            (
                ("schema", "media-v1"),
                ("media_id", marker.media_id),
                ("filesystem", marker.filesystem.value),
            )
        )
    )


def _parse_ready_artifacts(document: JsonObject) -> tuple[ReadyArtifact, ...]:
    artifacts: list[ReadyArtifact] = []
    for value in require_array(document, "artifacts").values:
        item = as_object(value, "artifacts")
        item.require_exact(frozenset({"path", "sha256", "crc32", "byte_size"}))
        artifacts.append(
            ReadyArtifact(
                parse_relative_path(require_string(item, "path")),
                _domain.sha256(item, "sha256"),
                _domain.crc32(item),
                _domain.positive_bytes(item),
            )
        )
    paths = tuple(item.path for item in artifacts)
    if not artifacts or len(paths) != len(set(paths)):
        raise ProtocolError(ProtocolReason.DUPLICATE_VALUE, "artifacts.path")
    return tuple(artifacts)


def parse_ready(data: bytes) -> ReadyMarker:
    """Parse a final publication marker and its exact inventory."""
    document = decode_json(data)
    document.require_exact(
        frozenset({"schema", "package_id", "manifest_sha256", "artifacts"})
    )
    require_schema(document, "ready")
    return ReadyMarker(
        _domain.package_id(document),
        _domain.sha256(document, "manifest_sha256"),
        _parse_ready_artifacts(document),
    )


def _artifact_json(item: ReadyArtifact) -> JsonObject:
    return JsonObject(
        (
            ("path", item.path),
            ("sha256", item.sha256),
            ("crc32", item.crc32),
            ("byte_size", item.byte_size.value),
        )
    )


def encode_ready(marker: ReadyMarker) -> bytes:
    """Encode canonical READY v1 bytes."""
    if not marker.artifacts:
        raise ProtocolError(ProtocolReason.INVALID_VALUE, "artifacts")
    paths = tuple(parse_relative_path(item.path) for item in marker.artifacts)
    if len(paths) != len(set(paths)):
        raise ProtocolError(ProtocolReason.DUPLICATE_VALUE, "artifacts.path")
    _ = _domain.sha256(
        JsonObject((("manifest_sha256", marker.manifest_sha256),)),
        "manifest_sha256",
    )
    for item in marker.artifacts:
        _ = _domain.sha256(JsonObject((("sha256", item.sha256),)), "sha256")
        _ = _domain.crc32(JsonObject((("crc32", item.crc32),)))
    return canonical_bytes(
        JsonObject(
            (
                ("schema", "ready-v1"),
                ("package_id", marker.package_id),
                ("manifest_sha256", marker.manifest_sha256),
                (
                    "artifacts",
                    JsonArray(tuple(_artifact_json(item) for item in marker.artifacts)),
                ),
            )
        )
    )
