"""Lexical canonical path contracts shared by protocol codecs."""

from __future__ import annotations

from pathlib import PurePosixPath

from repo_offline_sync.protocol.json_boundary import ProtocolError, ProtocolReason


def parse_relative_path(raw: str, *, allow_root: bool = False) -> str:
    """Parse a canonical POSIX path without touching a filesystem."""
    if "\x00" in raw or "\\" in raw or raw.startswith("/"):
        raise ProtocolError(ProtocolReason.NONCANONICAL_PATH)
    if allow_root and raw == ".":
        return raw
    parts = raw.split("/")
    if not raw or any(part in {"", ".", ".."} for part in parts):
        raise ProtocolError(ProtocolReason.NONCANONICAL_PATH)
    if PurePosixPath(raw).as_posix() != raw:
        raise ProtocolError(ProtocolReason.NONCANONICAL_PATH)
    return raw


def parse_absolute_path(raw: str) -> str:
    """Parse a canonical non-root absolute POSIX destination."""
    if "\x00" in raw or "\\" in raw or not raw.startswith("/") or raw == "/":
        raise ProtocolError(ProtocolReason.NONCANONICAL_PATH)
    parts = raw[1:].split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ProtocolError(ProtocolReason.NONCANONICAL_PATH)
    if PurePosixPath(raw).as_posix() != raw:
        raise ProtocolError(ProtocolReason.NONCANONICAL_PATH)
    return raw
