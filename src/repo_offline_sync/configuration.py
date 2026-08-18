"""Lightweight XDG host profiles and target configuration."""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from pathlib import Path
from typing import Any

from repo_offline_sync.core import SyncError, atomic_json, load_json

APP = "repo-offline-sync"


def _xdg(name: str, fallback: Path) -> Path:
    return Path(os.environ.get(name, str(fallback))).expanduser() / APP


def config_root() -> Path:
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config")


def cache_root() -> Path:
    return _xdg("XDG_CACHE_HOME", Path.home() / ".cache")


def state_root() -> Path:
    return _xdg("XDG_STATE_HOME", Path.home() / ".local/state")


def target_state_root() -> Path:
    return Path(os.environ.get("REPO_OFFLINE_SYNC_STATE", "/var/lib/repo-offline-sync"))


def target_config_path() -> Path:
    return Path(os.environ.get("REPO_OFFLINE_SYNC_TARGET_CONFIG", "/etc/repo-offline-sync/target.json"))


def pairing_token() -> str:
    return secrets.token_hex(16)


def repository_identity(common_git_dir: Path, remote: str) -> str:
    raw = f"{common_git_dir.resolve()}\0{remote}".encode()
    return hashlib.sha256(raw).hexdigest()


def profile_path(identity: str) -> Path:
    return config_root() / "repos" / f"{identity}.json"


def default_profile(identity: str, common_git_dir: Path, remote: str) -> dict[str, Any]:
    return {
        "schema": "host-profile-v1",
        "identity": identity,
        "repo_id": uuid.uuid4().hex,
        "common_git_dir": str(common_git_dir.resolve()),
        "remote": remote,
        "target_id": "default",
        "pairing_token": pairing_token(),
        "destination": str(Path.home() / "offline-app"),
        "service_user": os.environ.get("USER", "root"),
        "service_unit": "",
        "persistent_paths": [],
        "failure_policy": "rollback",
        "danger_enabled": False,
        "generation": 0,
        "actions": {
            "preflight": [],
            "build": [],
            "pre_activate": [],
            "post_activate": [],
            "health": [],
        },
    }


def load_or_create_profile(common_git_dir: Path, remote: str) -> tuple[Path, dict[str, Any], bool]:
    identity = repository_identity(common_git_dir, remote)
    path = profile_path(identity)
    if path.exists():
        value = load_json(path)
        if not isinstance(value, dict):
            raise SyncError(f"invalid host profile: {path}", 2)
        return path, value, False
    value = default_profile(identity, common_git_dir, remote)
    atomic_json(path, value)
    return path, value, True


def save_profile(path: Path, profile: dict[str, Any]) -> None:
    atomic_json(path, profile)


def create_target_config(path: Path | None = None) -> dict[str, Any]:
    value = {
        "schema": "target-v1",
        "target_id": uuid.uuid4().hex,
        "pairing_token": pairing_token(),
    }
    atomic_json(path or target_config_path(), value)
    return value


def load_target_config(path: Path | None = None) -> dict[str, Any]:
    config = path or target_config_path()
    if not config.exists():
        raise SyncError(f"target is not configured: {config}", 2)
    value = load_json(config)
    if not isinstance(value, dict) or value.get("schema") != "target-v1":
        raise SyncError(f"invalid target config: {config}", 2)
    return value
