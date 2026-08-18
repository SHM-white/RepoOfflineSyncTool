"""Installed target scanner used by boot and udev systemd services."""

from __future__ import annotations

import fcntl
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from repo_offline_sync.configuration import load_target_config, target_state_root
from repo_offline_sync.core import SyncError, load_json
from repo_offline_sync.media import (
    discover_target_media,
    local_copy,
    media_root,
    normal_unmount,
    queue_result,
    read_media_marker,
    replay_pending,
    verify_ready_package,
)
from repo_offline_sync.notifier import NoopNotifier
from repo_offline_sync.target_engine import apply_package, recover_transactions


EXIT_FOR_STATUS = {
    "success": 0,
    "no-op": 0,
    "rejected": 3,
    "needs-full-bundle": 4,
    "failed-rolled-back": 5,
    "failed-preserved": 6,
    "recovery-failed": 7,
    "media-io-failure": 8,
}


def _lock() -> Any:
    lock_path = Path("/run/repo-offline-sync/lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def _copy_candidates(mount: Path) -> list[tuple[Path, str]]:
    marker = read_media_marker(mount)
    inbox = media_root(mount) / "inbox"
    copied: list[tuple[Path, str]] = []
    if not inbox.is_dir():
        return copied
    installed = _installed_generations()
    for package in sorted(inbox.glob("pkg-*")):
        if not package.is_dir() or not (package / "READY.json").is_file():
            continue
        try:
            verify_ready_package(package)
            manifest = load_json(package / "manifest.json")
            if not isinstance(manifest, dict):
                continue
            root = str(manifest.get("root_repo_id") or "")
            generation = int(manifest.get("generation") or 0)
            if root and generation <= installed.get(root, 0):
                continue
            transaction = uuid.uuid4().hex
            copied.append((local_copy(package, transaction), str(marker["media_id"])))
        except (SyncError, OSError) as exc:
            print(f"skip invalid package {package}: {exc}", file=sys.stderr)
    return copied


def _installed_generations() -> dict[str, int]:
    """Return the highest successfully committed generation per root repo."""
    highest: dict[str, int] = {}
    results = target_state_root() / "results"
    if not results.is_dir():
        return highest
    for path in results.glob("*.json"):
        try:
            value = load_json(path)
        except OSError:
            continue
        if not isinstance(value, dict) or value.get("status") not in {"success", "no-op"}:
            continue
        root = str(value.get("root_repo_id") or "")
        if not root:
            continue
        generation = int(value.get("generation") or 0)
        highest[root] = max(highest.get(root, 0), generation)
    return highest


def _choose(copied: list[tuple[Path, str]]) -> list[tuple[Path, str]]:
    """Choose only newer-than-installed packages, newest per root repo."""
    installed = _installed_generations()
    best: dict[str, tuple[int, Path, str]] = {}
    for package, media_id in copied:
        try:
            manifest = load_json(package / "manifest.json")
        except OSError:
            continue
        if not isinstance(manifest, dict):
            continue
        root = str(manifest.get("root_repo_id") or "")
        generation = int(manifest.get("generation") or 0)
        if not root or generation <= installed.get(root, 0):
            continue
        current = best.get(root)
        if current is None or generation > current[0]:
            best[root] = (generation, package, media_id)
    return [(value[1], value[2]) for _, value in sorted(best.items())]


def scan_once() -> int:
    load_target_config()
    recovered = recover_transactions()
    for message in recovered:
        print(f"recovery: {message}")
    copied: list[tuple[Path, str]] = []
    notifier = NoopNotifier()
    media = discover_target_media()
    for mount, owned in media:
        try:
            replay_pending(mount)
            copied.extend(_copy_candidates(mount))
        finally:
            if not normal_unmount(mount):
                print(f"media remains mounted; copied packages will not be applied: {mount}", file=sys.stderr)
                copied = [item for item in copied if item[1] != str(read_media_marker(mount).get("media_id"))]
            else:
                notifier.media_unmounted(str(mount))
            if owned:
                try:
                    mount.rmdir()
                except OSError:
                    pass
    worst = 0
    for package, media_id in _choose(copied):
        try:
            result = apply_package(package)
        except (SyncError, OSError) as exc:
            print(f"update failed before result creation: {exc}", file=sys.stderr)
            worst = max(worst, getattr(exc, "exit_code", 8))
            continue
        queue_result(media_id, result)
        status = str(result.get("status") or "rejected")
        print(f"package {result.get('package_id')}: {status}")
        worst = max(worst, EXIT_FOR_STATUS.get(status, 3))
    return worst


def main() -> int:
    try:
        with _lock():
            return scan_once()
    except (SyncError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return getattr(exc, "exit_code", 8)


if __name__ == "__main__":
    raise SystemExit(main())
