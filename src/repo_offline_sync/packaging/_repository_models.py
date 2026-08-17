"""Immutable public values for repository source preflight."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, ClassVar, Literal, TypeAlias

if TYPE_CHECKING:
    from pathlib import Path

    from repo_offline_sync.domain.identifiers import GitOid
    from repo_offline_sync.packaging.profiles import RepositorySource


class GitObjectFormat(str, Enum):
    """Git object storage formats supported by the domain OID parser."""

    SHA1 = "sha1"
    SHA256 = "sha256"


class RepositoryRejectionReason(str, Enum):
    """Stable source-preflight rejection variants."""

    COMMAND_CANCELLED = "command_cancelled"
    COMMAND_FAILED = "command_failed"
    COMMAND_INTERRUPTED = "command_interrupted"
    COMMAND_INVALID = "command_invalid"
    COMMAND_OUTPUT_OVERFLOW = "command_output_overflow"
    COMMAND_START_FAILED = "command_start_failed"
    COMMAND_TEARDOWN_FAILED = "command_teardown_failed"
    COMMAND_TIMEOUT = "command_timeout"
    DETACHED_HEAD = "detached_head"
    DIRTY_TRACKED = "dirty_tracked"
    DIRTY_UNTRACKED = "dirty_untracked"
    INVALID_TARGET = "invalid_target"
    MALFORMED_OUTPUT = "malformed_output"
    NOT_REPOSITORY = "not_repository"
    PROVENANCE_UNAVAILABLE = "provenance_unavailable"
    SHALLOW = "shallow"
    UNRESOLVED_OPERATION = "unresolved_operation"
    UNSAFE_OWNERSHIP = "unsafe_ownership"
    UNSUPPORTED_OBJECT_FORMAT = "unsupported_object_format"


@dataclass(frozen=True, slots=True)
class RepositoryPreflightRequest:
    """Source path plus an optional internal detached-target contract."""

    repository: Path
    explicit_target: str | None = None


@dataclass(frozen=True, slots=True)
class RepositoryFacts:
    """Exact immutable facts needed by later packaging graph discovery."""

    top_level: Path
    common_git_dir: Path
    current_branch: str | None
    object_format: GitObjectFormat
    target_commit: GitOid
    source: RepositorySource


@dataclass(frozen=True, slots=True)
class RepositoryRejected:
    """A side-effect-free source rejection suitable for process mapping."""

    reason: RepositoryRejectionReason
    operation: str
    remediation: str | None = None
    exit_code: ClassVar[Literal[3]] = 3


RepositoryPreflightResult: TypeAlias = RepositoryFacts | RepositoryRejected
