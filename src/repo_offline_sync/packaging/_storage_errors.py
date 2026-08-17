"""Typed failures shared by private profile storage components."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from repo_offline_sync._typing import override


class StorageFailure(str, Enum):
    """Closed private-storage parse and filesystem failure reasons."""

    DUPLICATE_FIELD = "duplicate field"
    MALFORMED_JSON = "malformed JSON"
    MISSING_FIELD = "missing field"
    NOT_BOOLEAN = "field is not a boolean"
    NOT_OBJECT = "top level is not an object"
    NOT_REGULAR = "profile is not a regular file"
    NOT_STRING = "field is not a string"
    NOT_UTF8 = "profile is not UTF-8"
    UNKNOWN_FIELDS = "unknown or missing fields"
    XDG_COMPONENT = "XDG component is a symlink or non-directory"
    XDG_CHANGED = "XDG directory pathname identity changed"
    FILE_MODE = "private file mode is not owner-only"
    XDG_RELATIVE = "XDG root is not absolute"
    XDG_ROOT = "XDG root cannot be the filesystem root"


@dataclass(frozen=True, slots=True)
class StorageFormatError(Exception):
    """Report malformed private profile storage without exposing its contents."""

    reason: StorageFailure
    field: str | None = None

    @override
    def __str__(self) -> str:
        """Render a safe private-storage failure."""
        detail = (
            self.reason.value
            if self.field is None
            else f"{self.reason.value}: {self.field}"
        )
        return f"invalid private profile storage: {detail}"
