"""Manifest component codecs backed by authoritative domain values."""

from __future__ import annotations

from repo_offline_sync.domain.models import (
    Action,
    BundleSegment,
    LfsObject,
    RepositoryNode,
    SegmentRoute,
)
from repo_offline_sync.protocol import _domain
from repo_offline_sync.protocol._paths import parse_relative_path
from repo_offline_sync.protocol.json_boundary import (
    JsonArray,
    JsonObject,
    ProtocolError,
    ProtocolReason,
    as_object,
    as_string,
    require_array,
    require_boolean,
    require_optional_string,
    require_string,
)


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ProtocolError(ProtocolReason.DUPLICATE_VALUE, field)


def _strings(document: JsonObject, name: str) -> tuple[str, ...]:
    return tuple(
        as_string(value, name) for value in require_array(document, name).values
    )


def parse_repositories(document: JsonObject) -> tuple[RepositoryNode, ...]:
    repositories: list[RepositoryNode] = []
    for value in require_array(document, "repositories").values:
        item = as_object(value, "repositories")
        item.require_exact(
            frozenset(
                {
                    "repo_id",
                    "parent_repo_id",
                    "relative_path",
                    "target_commit",
                    "provenance_url",
                }
            )
        )
        parent_raw = require_optional_string(item, "parent_repo_id")
        parent = None
        if parent_raw is not None:
            parent_doc = JsonObject((("repo_id", parent_raw),))
            parent = _domain.repo_id(parent_doc)
        repositories.append(
            RepositoryNode(
                _domain.repo_id(item),
                parent,
                parse_relative_path(
                    require_string(item, "relative_path"), allow_root=True
                ),
                _domain.git_oid(item, "target_commit"),
                require_optional_string(item, "provenance_url"),
            )
        )
    _unique(
        tuple(repository.repo_id for repository in repositories), "repositories.repo_id"
    )
    return tuple(repositories)


def parse_routes(document: JsonObject) -> tuple[SegmentRoute, ...]:
    routes: list[SegmentRoute] = []
    for value in require_array(document, "routes").values:
        item = as_object(value, "routes")
        item.require_exact(
            frozenset(
                {
                    "repo_id",
                    "segment_ids",
                    "initial_prerequisites",
                    "final_target_commit",
                }
            )
        )
        segment_ids = tuple(
            _domain.segment_id(JsonObject((("segment_id", raw),)))
            for raw in _strings(item, "segment_ids")
        )
        prerequisites = tuple(
            _domain.git_oid(JsonObject((("oid", raw),)), "oid")
            for raw in _strings(item, "initial_prerequisites")
        )
        _unique(tuple(segment_ids), "routes.segment_ids")
        _unique(tuple(prerequisites), "routes.initial_prerequisites")
        routes.append(
            SegmentRoute(
                _domain.repo_id(item),
                segment_ids,
                prerequisites,
                _domain.git_oid(item, "final_target_commit"),
            )
        )
    identities = tuple((route.repo_id, route.segment_ids) for route in routes)
    if len(identities) != len(set(identities)):
        raise ProtocolError(ProtocolReason.DUPLICATE_VALUE, "routes")
    return tuple(routes)


def parse_segments(document: JsonObject) -> tuple[BundleSegment, ...]:
    segments: list[BundleSegment] = []
    fields = frozenset(
        {
            "segment_id",
            "repo_id",
            "bundle_kind",
            "generation",
            "base_commit",
            "target_commit",
            "byte_size",
            "oversize",
        }
    )
    for value in require_array(document, "segments").values:
        item = as_object(value, "segments")
        item.require_exact(fields)
        segments.append(
            BundleSegment(
                _domain.segment_id(item),
                _domain.repo_id(item),
                _domain.bundle_kind(item),
                _domain.generation(item),
                _domain.optional_git_oid(item, "base_commit"),
                _domain.git_oid(item, "target_commit"),
                _domain.positive_bytes(item),
                require_boolean(item, "oversize"),
            )
        )
    _unique(tuple(segment.segment_id for segment in segments), "segments.segment_id")
    return tuple(segments)


def parse_lfs_objects(document: JsonObject) -> tuple[LfsObject, ...]:
    objects: list[LfsObject] = []
    for value in require_array(document, "lfs_objects").values:
        item = as_object(value, "lfs_objects")
        item.require_exact(frozenset({"oid", "byte_size", "repo_ids"}))
        repo_ids = tuple(
            _domain.repo_id(JsonObject((("repo_id", raw),)))
            for raw in _strings(item, "repo_ids")
        )
        _unique(tuple(repo_ids), "lfs_objects.repo_ids")
        objects.append(
            LfsObject(_domain.lfs_oid(item), _domain.positive_bytes(item), repo_ids)
        )
    _unique(tuple(item.oid for item in objects), "lfs_objects.oid")
    return tuple(objects)


def parse_actions(document: JsonObject) -> tuple[Action, ...]:
    actions: list[Action] = []
    for value in require_array(document, "actions").values:
        item = as_object(value, "actions")
        item.require_exact(frozenset({"name", "phase", "argv", "timeout_seconds"}))
        name = require_string(item, "name")
        argv = _strings(item, "argv")
        if not name or not argv or any("\x00" in argument for argument in argv):
            raise ProtocolError(ProtocolReason.INVALID_VALUE, "actions")
        actions.append(
            Action(
                name, _domain.action_phase(item), argv, _domain.positive_seconds(item)
            )
        )
    _unique(tuple(action.name for action in actions), "actions.name")
    return tuple(actions)


def repository_json(item: RepositoryNode) -> JsonObject:
    return JsonObject(
        (
            ("repo_id", item.repo_id),
            ("parent_repo_id", item.parent_repo_id),
            ("relative_path", item.relative_path),
            ("target_commit", item.target_commit),
            ("provenance_url", item.provenance_url),
        )
    )


def route_json(item: SegmentRoute) -> JsonObject:
    return JsonObject(
        (
            ("repo_id", item.repo_id),
            ("segment_ids", JsonArray(tuple(item.segment_ids))),
            ("initial_prerequisites", JsonArray(tuple(item.initial_prerequisites))),
            ("final_target_commit", item.final_target_commit),
        )
    )


def segment_json(item: BundleSegment) -> JsonObject:
    return JsonObject(
        (
            ("segment_id", item.segment_id),
            ("repo_id", item.repo_id),
            ("bundle_kind", item.bundle_kind.value),
            ("generation", item.generation.value),
            ("base_commit", item.base_commit),
            ("target_commit", item.target_commit),
            ("byte_size", item.byte_size.value),
            ("oversize", item.oversize),
        )
    )


def lfs_json(item: LfsObject) -> JsonObject:
    return JsonObject(
        (
            ("oid", item.oid),
            ("byte_size", item.byte_size.value),
            ("repo_ids", JsonArray(tuple(item.repo_ids))),
        )
    )


def action_json(item: Action) -> JsonObject:
    return JsonObject(
        (
            ("name", item.name),
            ("phase", item.phase.value),
            ("argv", JsonArray(item.argv)),
            ("timeout_seconds", item.timeout.value),
        )
    )
