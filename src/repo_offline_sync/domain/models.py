"""Immutable updater model graph assembled from validated domain values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repo_offline_sync.domain.identifiers import (
        Generation,
        GitOid,
        LfsOid,
        MediaId,
        PackageId,
        PositiveBytes,
        PositiveSeconds,
        RepoId,
        SegmentId,
        TargetId,
        TransactionId,
    )
    from repo_offline_sync.domain.policies import (
        ActionPhase,
        BundleKind,
        FailurePolicy,
        Filesystem,
        ResultStatus,
        TransactionPhase,
    )


@dataclass(frozen=True, slots=True)
class RepositoryNode:
    """One exact repository revision in deterministic parent-first order."""

    repo_id: RepoId
    parent_repo_id: RepoId | None
    relative_path: str
    target_commit: GitOid
    provenance_url: str | None


@dataclass(frozen=True, slots=True)
class BundleSegment:
    """One indivisible logical Git bundle segment."""

    segment_id: SegmentId
    repo_id: RepoId
    bundle_kind: BundleKind
    generation: Generation
    base_commit: GitOid | None
    target_commit: GitOid
    byte_size: PositiveBytes
    oversize: bool


@dataclass(frozen=True, slots=True)
class SegmentRoute:
    """Ordered segments that advance a repository to its target commit."""

    repo_id: RepoId
    segment_ids: tuple[SegmentId, ...]
    initial_prerequisites: tuple[GitOid, ...]
    final_target_commit: GitOid


@dataclass(frozen=True, slots=True)
class LfsObject:
    """One exact-target Git LFS object referenced by repositories."""

    oid: LfsOid
    byte_size: PositiveBytes
    repo_ids: tuple[RepoId, ...]


@dataclass(frozen=True, slots=True)
class Action:
    """One finite package action with an explicit execution phase and timeout."""

    name: str
    phase: ActionPhase
    argv: tuple[str, ...]
    timeout: PositiveSeconds


@dataclass(frozen=True, slots=True)
class Package:
    """Complete immutable package policy and artifact graph."""

    package_id: PackageId
    target_id: TargetId
    media_id: MediaId
    generation: Generation
    target_commit: GitOid
    repositories: tuple[RepositoryNode, ...]
    routes: tuple[SegmentRoute, ...]
    segments: tuple[BundleSegment, ...]
    lfs_objects: tuple[LfsObject, ...]
    actions: tuple[Action, ...]
    failure_policy: FailurePolicy
    filesystem: Filesystem
    full_fallback_included: bool


@dataclass(frozen=True, slots=True)
class Transaction:
    """Current in-memory transaction identity and phase."""

    transaction_id: TransactionId
    package_id: PackageId
    target_id: TargetId
    phase: TransactionPhase


@dataclass(frozen=True, slots=True)
class Result:
    """Minimal typed transaction result identity and status."""

    transaction_id: TransactionId
    package_id: PackageId
    target_id: TargetId
    status: ResultStatus
