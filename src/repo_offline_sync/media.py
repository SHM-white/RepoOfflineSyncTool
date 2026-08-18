"""Removable-media layout, discovery, durable publication and verification."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from repo_offline_sync.configuration import target_state_root
from repo_offline_sync.core import (
    SyncError,
    atomic_json,
    canonical_json,
    copy_verified,
    fsync_dir,
    hash_file,
    iter_files,
    load_json,
    run,
)

LAYOUT = "offline-update"


def filesystem_for(path: Path) -> str:
    result = run(["findmnt", "-n", "-o", "FSTYPE", "-T", str(path)], check=False)
    raw = result.stdout.strip().lower()
    if raw == "ext4":
        return "ext4"
    if raw == "exfat":
        return "exfat"
    if raw in {"ntfs", "ntfs3", "fuseblk"}:
        return "ntfs3" if raw == "ntfs3" else "ntfs-3g"
    return raw or "unknown"


def media_root(mountpoint: Path) -> Path:
    return mountpoint / LAYOUT


def initialize_media(mountpoint: Path) -> dict[str, Any]:
    mountpoint = mountpoint.expanduser().resolve()
    if mountpoint == Path("/") or not mountpoint.is_dir():
        raise SyncError(f"invalid media mountpoint: {mountpoint}", 2)
    fs = filesystem_for(mountpoint)
    if fs not in {"ext4", "exfat", "ntfs3", "ntfs-3g"}:
        raise SyncError(f"unsupported media filesystem: {fs}", 2)
    root = media_root(mountpoint)
    root.mkdir(parents=True, exist_ok=True)
    for name in ("inbox", "staging", "results"):
        (root / name).mkdir(exist_ok=True)
    marker_path = root / "media.json"
    if marker_path.exists():
        marker = load_json(marker_path)
        if isinstance(marker, dict) and marker.get("schema") == "media-v1":
            return marker
        raise SyncError(f"invalid media marker: {marker_path}")
    marker = {"schema": "media-v1", "media_id": uuid.uuid4().hex, "filesystem": fs}
    atomic_json(marker_path, marker)
    fsync_dir(root)
    return marker


def read_media_marker(mountpoint: Path) -> dict[str, Any]:
    marker_path = media_root(mountpoint) / "media.json"
    try:
        value = load_json(marker_path)
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(f"invalid media marker: {marker_path}") from exc
    if not isinstance(value, dict) or value.get("schema") != "media-v1" or not value.get("media_id"):
        raise SyncError(f"invalid media marker: {marker_path}")
    return value


def discover_media() -> list[Path]:
    """Discover mounted marked partitions; never treats an unmarked disk as update media."""
    result = run(
        [
            "lsblk",
            "--json",
            "--output",
            "NAME,PATH,FSTYPE,LABEL,UUID,PARTUUID,MOUNTPOINTS,PARTTYPE,TYPE",
        ],
        check=False,
    )
    if result.returncode != 0:
        return []
    try:
        doc = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    found: list[Path] = []

    def visit(node: dict[str, Any]) -> None:
        label = str(node.get("label") or "")
        fstype = str(node.get("fstype") or "").lower()
        parttype = str(node.get("parttype") or "").lower()
        skip = label.upper() == "VTOYEFI" or fstype == "iso9660" or parttype in {
            "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
        }
        if not skip:
            mounts = node.get("mountpoints") or []
            if isinstance(mounts, list):
                for raw in mounts:
                    if not raw:
                        continue
                    mount = Path(str(raw))
                    if (media_root(mount) / "media.json").is_file():
                        found.append(mount)
        for child in node.get("children") or []:
            if isinstance(child, dict):
                visit(child)

    for node in doc.get("blockdevices") or []:
        if isinstance(node, dict):
            visit(node)
    return sorted(set(found))


def inventory(root: Path, *, include_ready: bool = False) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    excluded = () if include_ready else ("READY.json", "READY.tmp")
    for path in iter_files(root, exclude_names=excluded):
        rel = path.relative_to(root).as_posix()
        digest = hash_file(path)
        items.append({"path": rel, **digest})
    return items


def verify_inventory(root: Path, expected: list[dict[str, Any]]) -> None:
    actual = inventory(root)
    if len(actual) != len(expected):
        raise SyncError(f"package inventory mismatch: {root}", 8)
    by_path = {item["path"]: item for item in actual}
    for item in expected:
        observed = by_path.get(item.get("path"))
        if observed != item:
            raise SyncError(f"package verification failed: {item.get('path')}", 8)


def publish_package(package_dir: Path, mountpoint: Path, package_id: str) -> Path:
    marker = read_media_marker(mountpoint)
    root = media_root(mountpoint)
    staging = root / "staging" / f"pkg-{package_id}.partial"
    final = root / "inbox" / f"pkg-{package_id}"
    if final.exists():
        raise SyncError(f"package already exists on media: {package_id}", 8)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for source in iter_files(package_dir):
        rel = source.relative_to(package_dir)
        copy_verified(source, staging / rel)
    fsync_dir(staging)
    first_inventory = inventory(staging)
    os.replace(staging, final)
    fsync_dir(final.parent)
    verify_inventory(final, first_inventory)
    manifest = hash_file(final / "manifest.json")
    ready = {
        "schema": "ready-v1",
        "package_id": package_id,
        "media_id": marker["media_id"],
        "manifest_sha256": manifest["sha256"],
        "artifacts": first_inventory,
    }
    atomic_json(final / "READY.tmp", ready)
    os.replace(final / "READY.tmp", final / "READY.json")
    fsync_dir(final)
    try:
        os.sync()
    except AttributeError:
        pass
    return final


def verify_ready_package(package_dir: Path) -> dict[str, Any]:
    ready_path = package_dir / "READY.json"
    if not ready_path.is_file():
        raise SyncError(f"package is not READY: {package_dir}")
    ready = load_json(ready_path)
    if not isinstance(ready, dict) or ready.get("schema") != "ready-v1":
        raise SyncError(f"invalid READY marker: {ready_path}")
    artifacts = ready.get("artifacts")
    if not isinstance(artifacts, list):
        raise SyncError(f"invalid READY inventory: {ready_path}")
    verify_inventory(package_dir, artifacts)
    if hash_file(package_dir / "manifest.json")["sha256"] != ready.get("manifest_sha256"):
        raise SyncError("manifest hash does not match READY")
    return ready


def local_copy(package_dir: Path, transaction_id: str) -> Path:
    root = target_state_root() / "staging" / transaction_id
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    for source in iter_files(package_dir):
        rel = source.relative_to(package_dir)
        copy_verified(source, root / rel)
    verify_ready_package(root)
    return root


def receipts(mountpoint: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    directory = media_root(mountpoint) / "results"
    if not directory.is_dir():
        return result
    for path in sorted(directory.glob("*.json")):
        try:
            value = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("schema") == "result-v1":
            result.append(value)
    return result


def pending_results_dir(media_id: str) -> Path:
    return target_state_root() / "pending-results" / media_id


def queue_result(media_id: str, result: dict[str, Any]) -> Path:
    path = pending_results_dir(media_id) / f"{result['transaction_id']}.json"
    atomic_json(path, result)
    return path


def replay_pending(mountpoint: Path) -> int:
    marker = read_media_marker(mountpoint)
    pending = pending_results_dir(str(marker["media_id"]))
    if not pending.is_dir():
        return 0
    destination = media_root(mountpoint) / "results"
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in sorted(pending.glob("*.json")):
        target = destination / path.name
        try:
            copy_verified(path, target)
        except (OSError, SyncError):
            continue
        path.unlink(missing_ok=True)
        count += 1
    return count


def normal_unmount(mountpoint: Path) -> bool:
    """Attempt a normal unmount only; never force or lazy unmount."""
    result = run(["umount", str(mountpoint)], check=False)
    if result.returncode != 0:
        return False
    check = run(["findmnt", "-n", "-o", "TARGET", "-T", str(mountpoint)], check=False)
    if check.returncode != 0:
        return True
    observed = check.stdout.strip()
    return observed != str(mountpoint)


def discover_target_media() -> list[tuple[Path, bool]]:
    """Discover marked media and, as root, privately mount unmounted candidates."""
    result = run(
        [
            "lsblk",
            "--json",
            "--output",
            "NAME,PATH,FSTYPE,LABEL,UUID,PARTUUID,MOUNTPOINTS,PARTTYPE,TYPE",
        ],
        check=False,
    )
    if result.returncode != 0:
        return [(path, False) for path in discover_media()]
    try:
        doc = json.loads(result.stdout)
    except json.JSONDecodeError:
        return [(path, False) for path in discover_media()]
    found: list[tuple[Path, bool]] = []
    private_root = Path("/run/repo-offline-sync/media")

    def visit(node: dict[str, Any]) -> None:
        label = str(node.get("label") or "")
        fstype = str(node.get("fstype") or "").lower()
        parttype = str(node.get("parttype") or "").lower()
        device = str(node.get("path") or "")
        supported = fstype in {"ext4", "exfat", "ntfs", "ntfs3"}
        skip = label.upper() == "VTOYEFI" or fstype == "iso9660" or parttype == "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
        mounts = [Path(str(raw)) for raw in (node.get("mountpoints") or []) if raw]
        if supported and not skip:
            marked = [mount for mount in mounts if (media_root(mount) / "media.json").is_file()]
            found.extend((mount, False) for mount in marked)
            if not mounts and os.geteuid() == 0 and device:
                key = str(node.get("uuid") or node.get("partuuid") or node.get("name") or uuid.uuid4().hex)
                key = "".join(ch for ch in key if ch.isalnum() or ch in "-_")[:80]
                mount = private_root / key
                mount.mkdir(parents=True, exist_ok=True)
                mounted = run(["mount", "-o", "ro,nosuid,nodev,noexec", device, str(mount)], check=False)
                if mounted.returncode == 0:
                    if (media_root(mount) / "media.json").is_file():
                        run(["umount", str(mount)], check=False)
                        writable = run(["mount", "-o", "nosuid,nodev,noexec", device, str(mount)], check=False)
                        if writable.returncode != 0:
                            readonly = run(["mount", "-o", "ro,nosuid,nodev,noexec", device, str(mount)], check=False)
                            if readonly.returncode != 0:
                                try:
                                    mount.rmdir()
                                except OSError:
                                    pass
                            else:
                                found.append((mount, True))
                        else:
                            found.append((mount, True))
                    else:
                        run(["umount", str(mount)], check=False)
                        try:
                            mount.rmdir()
                        except OSError:
                            pass
        for child in node.get("children") or []:
            if isinstance(child, dict):
                visit(child)

    for node in doc.get("blockdevices") or []:
        if isinstance(node, dict):
            visit(node)
    unique: dict[str, tuple[Path, bool]] = {}
    for path, owned in found:
        unique[str(path)] = (path, owned)
    return sorted(unique.values(), key=lambda item: str(item[0]))
