"""Versioned result protocol and exhaustive stable process exit classes."""

from __future__ import annotations

from enum import IntEnum, unique

from repo_offline_sync.domain.models import Result
from repo_offline_sync.domain.policies import ResultStatus
from repo_offline_sync.protocol import _domain
from repo_offline_sync.protocol.json_boundary import (
    JsonObject,
    canonical_bytes,
    decode_json,
    require_schema,
)


@unique
class ExitCode(IntEnum):
    """Stable process exit-code classes shared by both entrypoints."""

    SUCCESS = 0
    USAGE_CONFIG = 2
    REJECTED = 3
    NEEDS_FULL = 4
    ROLLED_BACK = 5
    PRESERVED = 6
    RECOVERY_FAILED = 7
    MEDIA_IO = 8


def exit_code_for_status(status: ResultStatus) -> ExitCode:
    """Map every machine result status to exactly one process class."""
    match status:
        case ResultStatus.SUCCESS | ResultStatus.NO_OP:
            code = ExitCode.SUCCESS
        case ResultStatus.NEEDS_FULL_BUNDLE:
            code = ExitCode.NEEDS_FULL
        case ResultStatus.REJECTED:
            code = ExitCode.REJECTED
        case ResultStatus.FAILED_ROLLED_BACK:
            code = ExitCode.ROLLED_BACK
        case ResultStatus.FAILED_PRESERVED:
            code = ExitCode.PRESERVED
        case ResultStatus.RECOVERY_FAILED:
            code = ExitCode.RECOVERY_FAILED
        case ResultStatus.MEDIA_IO_FAILURE:
            code = ExitCode.MEDIA_IO
    return code


def parse_result(data: bytes) -> Result:
    """Parse one specific typed result status."""
    document = decode_json(data)
    document.require_exact(
        frozenset({"schema", "transaction_id", "package_id", "target_id", "status"})
    )
    require_schema(document, "result")
    return Result(
        _domain.transaction_id(document),
        _domain.package_id(document),
        _domain.target_id(document),
        _domain.result_status(document),
    )


def encode_result(result: Result) -> bytes:
    """Encode canonical result v1 bytes."""
    return canonical_bytes(
        JsonObject(
            (
                ("schema", "result-v1"),
                ("transaction_id", result.transaction_id),
                ("package_id", result.package_id),
                ("target_id", result.target_id),
                ("status", result.status.value),
            )
        )
    )
