"""Strict parsers for Git source-preflight output."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

_DISCOVERY_FIELD_COUNT = 4
_OPERATION_PATH_COUNT = 6


@dataclass(frozen=True, slots=True)
class DiscoveryOutput:
    """Canonical repository paths and storage properties."""

    top_level: Path
    common_git_dir: Path
    shallow: bool
    object_format: str


class WorktreeStatus(Enum):
    """Relevant porcelain-v2 source state."""

    CLEAN = "clean"
    MALFORMED = "malformed"
    TRACKED = "tracked"
    UNTRACKED = "untracked"


def _canonical_directory(raw: str, cwd: Path) -> Path | None:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    try:
        canonical = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return canonical if canonical.is_dir() else None


def parse_discovery(data: bytes, cwd: Path) -> DiscoveryOutput | None:
    """Parse four newline-delimited rev-parse discovery fields."""
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    lines = text.splitlines()
    if len(lines) != _DISCOVERY_FIELD_COUNT or not text.endswith("\n"):
        return None
    top_level = _canonical_directory(lines[0], cwd)
    common_git_dir = _canonical_directory(lines[1], cwd)
    if top_level is None or common_git_dir is None:
        return None
    if lines[2] not in {"true", "false"}:
        return None
    return DiscoveryOutput(top_level, common_git_dir, lines[2] == "true", lines[3])


def parse_single_text(data: bytes) -> str | None:
    """Parse one nonempty UTF-8 line with an exact trailing newline."""
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    if not text.endswith("\n") or text.count("\n") != 1:
        return None
    value = text[:-1]
    return value if value and "\x00" not in value else None


def parse_operation_paths(data: bytes, cwd: Path) -> tuple[Path, ...] | None:
    """Parse absolute-or-cwd-relative Git-resolved operation paths."""
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    if not text.endswith("\n"):
        return None
    lines = text[:-1].split("\n")
    if len(lines) != _OPERATION_PATH_COUNT or not all(lines):
        return None
    paths: list[Path] = []
    for line in lines:
        candidate = Path(line)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        try:
            paths.append(candidate.resolve(strict=False))
        except (OSError, RuntimeError):
            return None
    return tuple(paths)


def parse_porcelain_v2(data: bytes) -> WorktreeStatus:
    """Classify NUL-delimited porcelain-v2 records without decoding paths."""
    if not data:
        return WorktreeStatus.CLEAN
    if not data.endswith(b"\0"):
        return WorktreeStatus.MALFORMED
    records = data[:-1].split(b"\0")
    tracked = False
    untracked = False
    index = 0
    while index < len(records):
        record = records[index]
        if record.startswith((b"1 ", b"u ")):
            tracked = True
        elif record.startswith(b"2 "):
            tracked = True
            index += 1
            if index >= len(records) or not records[index]:
                return WorktreeStatus.MALFORMED
        elif record.startswith(b"? "):
            untracked = True
        elif record.startswith(b"! "):
            pass
        else:
            return WorktreeStatus.MALFORMED
        index += 1
    if tracked:
        return WorktreeStatus.TRACKED
    return WorktreeStatus.UNTRACKED if untracked else WorktreeStatus.CLEAN
