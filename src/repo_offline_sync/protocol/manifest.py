"""Versioned package-manifest protocol and repository-graph consistency rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from repo_offline_sync.domain.models import (
    BundleSegment,
    Package,
    RepositoryNode,
    SegmentRoute,
)
from repo_offline_sync.domain.policies import BundleKind
from repo_offline_sync.packaging.profiles import PairingToken, ProfileFormatError
from repo_offline_sync.protocol import _domain
from repo_offline_sync.protocol._manifest_encode import encode_manifest_value
from repo_offline_sync.protocol._manifest_parts import (
    parse_actions,
    parse_lfs_objects,
    parse_repositories,
    parse_routes,
    parse_segments,
)
from repo_offline_sync.protocol._paths import parse_absolute_path, parse_relative_path
from repo_offline_sync.protocol.json_boundary import (
    JsonObject,
    ProtocolError,
    ProtocolReason,
    as_object,
    decode_json,
    require_array,
    require_boolean,
    require_schema,
    require_string,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from repo_offline_sync.domain.identifiers import PositiveBytes, SegmentId

_FIELDS = frozenset(
    {
        "schema",
        "package_id",
        "target_id",
        "media_id",
        "generation",
        "target_commit",
        "repositories",
        "routes",
        "segments",
        "lfs_objects",
        "actions",
        "failure_policy",
        "filesystem",
        "full_fallback_included",
        "destination",
        "persistent_paths",
        "pairing_token",
        "dangerous_confirmed",
        "artifacts",
    }
)


@dataclass(frozen=True, slots=True)
class Artifact:
    """One content-addressed file required by a package."""

    path: str
    sha256: str
    byte_size: PositiveBytes


@dataclass(frozen=True, slots=True)
class Manifest:
    """Complete immutable package boundary including destination policy."""

    package: Package
    destination: str
    persistent_paths: tuple[str, ...]
    pairing_token: PairingToken
    dangerous_confirmed: bool
    artifacts: tuple[Artifact, ...]


def _parse_artifacts(document: JsonObject) -> tuple[Artifact, ...]:
    artifacts: list[Artifact] = []
    for value in require_array(document, "artifacts").values:
        item = as_object(value, "artifacts")
        item.require_exact(frozenset({"path", "sha256", "byte_size"}))
        artifacts.append(
            Artifact(
                parse_relative_path(require_string(item, "path")),
                _domain.sha256(item, "sha256"),
                _domain.positive_bytes(item),
            )
        )
    paths = tuple(artifact.path for artifact in artifacts)
    if len(paths) != len(set(paths)):
        raise ProtocolError(ProtocolReason.DUPLICATE_VALUE, "artifacts.path")
    return tuple(artifacts)


def _validate_repositories(package: Package) -> set[str]:
    repositories = package.repositories
    roots = tuple(
        repository for repository in repositories if repository.parent_repo_id is None
    )
    if len(roots) != 1 or roots[0].relative_path != ".":
        raise ProtocolError(ProtocolReason.INCONSISTENT_TARGET, "repositories")
    if roots[0].target_commit != package.target_commit:
        raise ProtocolError(ProtocolReason.INCONSISTENT_TARGET, "target_commit")
    known: set[str] = set()
    paths: set[str] = set()
    for repository in package.repositories:
        if (
            repository.parent_repo_id is not None
            and repository.parent_repo_id not in known
        ):
            raise ProtocolError(ProtocolReason.INCONSISTENT_TARGET, "parent_repo_id")
        if repository.relative_path in paths:
            raise ProtocolError(ProtocolReason.DUPLICATE_VALUE, "relative_path")
        known.add(repository.repo_id)
        paths.add(repository.relative_path)
    return known


def _validate_segment_kind(segment: BundleSegment) -> bool:
    match segment.bundle_kind:
        case BundleKind.FULL:
            if segment.base_commit is not None:
                raise ProtocolError(ProtocolReason.INCONSISTENT_TARGET, "segments")
            is_full = True
        case BundleKind.INCREMENTAL:
            if segment.base_commit is None:
                raise ProtocolError(ProtocolReason.INCONSISTENT_TARGET, "segments")
            is_full = False
    return is_full


def _validate_route(
    route: SegmentRoute,
    repository: RepositoryNode,
    segments: Mapping[SegmentId, BundleSegment],
) -> tuple[set[SegmentId], bool]:
    if not route.segment_ids or route.final_target_commit != repository.target_commit:
        raise ProtocolError(ProtocolReason.INCONSISTENT_TARGET, "routes")
    previous = None
    used: set[SegmentId] = set()
    starts_full = False
    for segment_id in route.segment_ids:
        segment = segments.get(segment_id)
        if segment is None or segment.repo_id != repository.repo_id:
            raise ProtocolError(ProtocolReason.INCONSISTENT_TARGET, "segments")
        is_full = _validate_segment_kind(segment)
        if previous is not None and segment.base_commit != previous:
            raise ProtocolError(ProtocolReason.INCONSISTENT_TARGET, "segments")
        if (
            previous is None
            and segment.base_commit is not None
            and segment.base_commit not in route.initial_prerequisites
        ):
            raise ProtocolError(ProtocolReason.INCONSISTENT_TARGET, "segments")
        previous = segment.target_commit
        used.add(segment_id)
        if segment_id == route.segment_ids[0] and is_full:
            starts_full = True
    if previous != route.final_target_commit:
        raise ProtocolError(ProtocolReason.INCONSISTENT_TARGET, "segments")
    return used, starts_full


def _validate_routes(package: Package, known: set[str]) -> None:
    repositories = {
        repository.repo_id: repository for repository in package.repositories
    }
    segments = {segment.segment_id: segment for segment in package.segments}
    if frozenset(route.repo_id for route in package.routes) != frozenset(known):
        raise ProtocolError(ProtocolReason.INCONSISTENT_TARGET, "routes")
    used_segments: set[str] = set()
    full_repositories: set[str] = set()
    for route in package.routes:
        repository = repositories[route.repo_id]
        route_segments, starts_full = _validate_route(route, repository, segments)
        used_segments.update(route_segments)
        if starts_full:
            full_repositories.add(route.repo_id)
    if used_segments != set(segments):
        raise ProtocolError(ProtocolReason.INCONSISTENT_TARGET, "segments")
    if package.full_fallback_included and full_repositories != known:
        raise ProtocolError(
            ProtocolReason.INCONSISTENT_TARGET, "full_fallback_included"
        )


def _validate_graph(package: Package) -> None:
    known = _validate_repositories(package)
    _validate_routes(package, known)
    for item in package.lfs_objects:
        if not item.repo_ids or any(repo_id not in known for repo_id in item.repo_ids):
            raise ProtocolError(ProtocolReason.INCONSISTENT_TARGET, "lfs_objects")


def parse_manifest(data: bytes) -> Manifest:
    """Parse untrusted manifest bytes into the complete typed package boundary."""
    document = decode_json(data)
    document.require_exact(_FIELDS)
    require_schema(document, "manifest")
    repositories = parse_repositories(document)
    routes = parse_routes(document)
    segments = parse_segments(document)
    package = Package(
        _domain.package_id(document),
        _domain.target_id(document),
        _domain.media_id(document),
        _domain.generation(document),
        _domain.git_oid(document, "target_commit"),
        repositories,
        routes,
        segments,
        parse_lfs_objects(document),
        parse_actions(document),
        _domain.failure_policy(document),
        _domain.filesystem(document),
        require_boolean(document, "full_fallback_included"),
    )
    _validate_graph(package)
    try:
        token = PairingToken.parse(require_string(document, "pairing_token"))
    except ProfileFormatError as error:
        raise ProtocolError(ProtocolReason.MALFORMED_TOKEN, "pairing_token") from error
    persistent = tuple(
        parse_relative_path(require_string(JsonObject((("path", value),)), "path"))
        for value in require_array(document, "persistent_paths").values
    )
    if len(persistent) != len(set(persistent)):
        raise ProtocolError(ProtocolReason.DUPLICATE_VALUE, "persistent_paths")
    return Manifest(
        package,
        parse_absolute_path(require_string(document, "destination")),
        persistent,
        token,
        require_boolean(document, "dangerous_confirmed"),
        _parse_artifacts(document),
    )


def encode_manifest(manifest: Manifest) -> bytes:
    """Encode a validated manifest into its canonical v1 representation."""
    _validate_graph(manifest.package)
    return encode_manifest_value(manifest)
