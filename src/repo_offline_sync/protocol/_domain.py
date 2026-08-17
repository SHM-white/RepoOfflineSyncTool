"""Adapters from narrow JSON fields to authoritative domain parsers."""

from __future__ import annotations

from string import hexdigits
from typing import TYPE_CHECKING

from repo_offline_sync.domain.identifiers import (
    Generation,
    PositiveBytes,
    PositiveSeconds,
    parse_generation,
    parse_git_oid,
    parse_lfs_oid,
    parse_media_id,
    parse_package_id,
    parse_positive_bytes,
    parse_positive_seconds,
    parse_repo_id,
    parse_segment_id,
    parse_target_id,
    parse_transaction_id,
)
from repo_offline_sync.domain.policies import (
    ActionPhase,
    BundleKind,
    FailurePolicy,
    Filesystem,
    ResultStatus,
    TransactionPhase,
    parse_action_phase,
    parse_bundle_kind,
    parse_failure_policy,
    parse_filesystem,
    parse_result_status,
    parse_transaction_phase,
)
from repo_offline_sync.protocol.json_boundary import (
    JsonObject,
    ProtocolError,
    ProtocolReason,
    require_integer,
    require_string,
)

_SHA256_HEX_LENGTH = 64
_CRC32_HEX_LENGTH = 8

if TYPE_CHECKING:
    from repo_offline_sync.domain.identifiers import (
        GitOid,
        LfsOid,
        MediaId,
        PackageId,
        RepoId,
        SegmentId,
        TargetId,
        TransactionId,
    )


def _invalid(name: str) -> ProtocolError:
    return ProtocolError(ProtocolReason.INVALID_VALUE, name)


def package_id(document: JsonObject, name: str = "package_id") -> PackageId:
    parsed = parse_package_id(require_string(document, name))
    if isinstance(parsed, str):
        return parsed
    raise _invalid(name)


def repo_id(document: JsonObject, name: str = "repo_id") -> RepoId:
    parsed = parse_repo_id(require_string(document, name))
    if isinstance(parsed, str):
        return parsed
    raise _invalid(name)


def target_id(document: JsonObject, name: str = "target_id") -> TargetId:
    parsed = parse_target_id(require_string(document, name))
    if isinstance(parsed, str):
        return parsed
    raise _invalid(name)


def transaction_id(document: JsonObject, name: str = "transaction_id") -> TransactionId:
    parsed = parse_transaction_id(require_string(document, name))
    if isinstance(parsed, str):
        return parsed
    raise _invalid(name)


def media_id(document: JsonObject, name: str = "media_id") -> MediaId:
    parsed = parse_media_id(require_string(document, name))
    if isinstance(parsed, str):
        return parsed
    raise _invalid(name)


def segment_id(document: JsonObject, name: str = "segment_id") -> SegmentId:
    parsed = parse_segment_id(require_string(document, name))
    if isinstance(parsed, str):
        return parsed
    raise _invalid(name)


def git_oid(document: JsonObject, name: str) -> GitOid:
    raw = require_string(document, name)
    parsed = parse_git_oid(raw)
    if isinstance(parsed, str) and parsed == raw:
        return parsed
    raise _invalid(name)


def optional_git_oid(document: JsonObject, name: str) -> GitOid | None:
    value = document.required(name)
    if value is None:
        return None
    if isinstance(value, str):
        parsed = parse_git_oid(value)
        if isinstance(parsed, str) and parsed == value:
            return parsed
    raise _invalid(name)


def lfs_oid(document: JsonObject, name: str = "oid") -> LfsOid:
    raw = require_string(document, name)
    parsed = parse_lfs_oid(raw)
    if isinstance(parsed, str) and parsed == raw:
        return parsed
    raise _invalid(name)


def sha256(document: JsonObject, name: str) -> str:
    raw = require_string(document, name)
    if len(raw) != _SHA256_HEX_LENGTH or any(
        character not in hexdigits for character in raw
    ):
        raise _invalid(name)
    canonical = raw.lower()
    if canonical != raw:
        raise _invalid(name)
    return canonical


def crc32(document: JsonObject, name: str = "crc32") -> str:
    raw = require_string(document, name)
    if len(raw) != _CRC32_HEX_LENGTH or any(
        character not in hexdigits for character in raw
    ):
        raise _invalid(name)
    canonical = raw.lower()
    if canonical != raw:
        raise _invalid(name)
    return canonical


def generation(document: JsonObject, name: str = "generation") -> Generation:
    parsed = parse_generation(require_integer(document, name))
    if isinstance(parsed, Generation):
        return parsed
    raise _invalid(name)


def positive_bytes(document: JsonObject, name: str = "byte_size") -> PositiveBytes:
    parsed = parse_positive_bytes(require_integer(document, name))
    if isinstance(parsed, PositiveBytes):
        return parsed
    raise _invalid(name)


def positive_seconds(
    document: JsonObject, name: str = "timeout_seconds"
) -> PositiveSeconds:
    parsed = parse_positive_seconds(require_integer(document, name))
    if isinstance(parsed, PositiveSeconds):
        return parsed
    raise _invalid(name)


def bundle_kind(document: JsonObject, name: str = "bundle_kind") -> BundleKind:
    parsed = parse_bundle_kind(require_string(document, name))
    if isinstance(parsed, BundleKind):
        return parsed
    raise _invalid(name)


def action_phase(document: JsonObject, name: str = "phase") -> ActionPhase:
    parsed = parse_action_phase(require_string(document, name))
    if isinstance(parsed, ActionPhase):
        return parsed
    raise _invalid(name)


def failure_policy(document: JsonObject, name: str = "failure_policy") -> FailurePolicy:
    parsed = parse_failure_policy(require_string(document, name))
    if isinstance(parsed, FailurePolicy):
        return parsed
    raise _invalid(name)


def filesystem(document: JsonObject, name: str = "filesystem") -> Filesystem:
    parsed = parse_filesystem(require_string(document, name))
    if isinstance(parsed, Filesystem):
        return parsed
    raise _invalid(name)


def transaction_phase(document: JsonObject, name: str = "phase") -> TransactionPhase:
    parsed = parse_transaction_phase(require_string(document, name))
    if isinstance(parsed, TransactionPhase):
        return parsed
    raise _invalid(name)


def result_status(document: JsonObject, name: str = "status") -> ResultStatus:
    parsed = parse_result_status(require_string(document, name))
    if isinstance(parsed, ResultStatus):
        return parsed
    raise _invalid(name)
