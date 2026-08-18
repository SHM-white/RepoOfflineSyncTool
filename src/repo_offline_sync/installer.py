"""No-argument interactive target installer/repair/uninstall implementation."""

from __future__ import annotations

import os
import secrets
import shutil
import sys
from pathlib import Path
from typing import Any

from repo_offline_sync.configuration import create_target_config, load_target_config, target_config_path, target_state_root
from repo_offline_sync.core import SyncError, atomic_json, run

LIB = Path("/usr/lib/repo-offline-sync/repo_offline_sync")
LIBEXEC = Path("/usr/libexec/repo-offline-sync")
SYSTEMD = Path("/etc/systemd/system")
UDEV = Path("/etc/udev/rules.d")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _require_root() -> None:
    if os.geteuid() != 0:
        raise SyncError("target installation requires root; run: sudo ./install_target.sh", 2)


def _wrapper(module: str) -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"exec env PYTHONPATH=/usr/lib/repo-offline-sync /usr/bin/python3 -m repo_offline_sync.{module} \"$@\"\n"
    )


def _copy_runtime() -> None:
    source = _project_root() / "src" / "repo_offline_sync"
    shutil.rmtree(LIB, ignore_errors=True)
    LIB.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, LIB, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    LIBEXEC.mkdir(parents=True, exist_ok=True)
    for name, module in (("scan", "scan_target"), ("status", "status")):
        path = LIBEXEC / name
        path.write_text(_wrapper(module), encoding="utf-8")
        path.chmod(0o755)


def _copy_integration_assets() -> None:
    required = ("git", "lsblk", "findmnt", "mount", "umount", "systemctl", "udevadm")
    missing = [name for name in required if shutil.which(name) is None]
    if missing:
        raise SyncError("missing required Ubuntu system tools: " + ", ".join(missing), 2)
    root = _project_root()
    for name in ("repo-offline-sync-boot.service", "repo-offline-sync-scan.service"):
        shutil.copy2(root / "systemd" / name, SYSTEMD / name)
    shutil.copy2(root / "udev" / "99-repo-offline-sync.rules", UDEV / "99-repo-offline-sync.rules")
    run(["systemctl", "daemon-reload"])
    run(["udevadm", "control", "--reload-rules"], check=False)
    run(["systemctl", "enable", "repo-offline-sync-boot.service"])


def _ensure_state() -> None:
    root = target_state_root()
    for name in ("repos", "releases", "persistent", "staging", "transactions", "results", "pending-results", "backups"):
        (root / name).mkdir(parents=True, exist_ok=True)
        os.chmod(root / name, 0o700)


def _show_credentials(config: dict[str, Any]) -> None:
    print("\nCopy these values into the packaging host profile:")
    print(f"target_id={config['target_id']}")
    print(f"pairing_token={config['pairing_token']}")
    print("The token is only a mismatch guard, not package authentication.\n")


def install_or_repair(*, regenerate: bool = False) -> None:
    _copy_runtime()
    _ensure_state()
    config_path = target_config_path()
    if regenerate or not config_path.exists():
        config = create_target_config(config_path)
    else:
        config = load_target_config(config_path)
    os.chmod(config_path, 0o600)
    _copy_integration_assets()
    run(["systemctl", "start", "repo-offline-sync-boot.service"], check=False)
    print("Repo Offline Sync target installed/repaired.")
    _show_credentials(config)


def rotate_token() -> None:
    config = load_target_config()
    config["pairing_token"] = secrets.token_hex(16)
    atomic_json(target_config_path(), config)
    os.chmod(target_config_path(), 0o600)
    _show_credentials(config)


def uninstall() -> None:
    run(["systemctl", "disable", "--now", "repo-offline-sync-boot.service"], check=False)
    run(["systemctl", "stop", "repo-offline-sync-scan.service"], check=False)
    for path in (
        SYSTEMD / "repo-offline-sync-boot.service",
        SYSTEMD / "repo-offline-sync-scan.service",
        UDEV / "99-repo-offline-sync.rules",
    ):
        path.unlink(missing_ok=True)
    shutil.rmtree(LIB.parent, ignore_errors=True)
    shutil.rmtree(LIBEXEC, ignore_errors=True)
    config_dir = target_config_path().parent
    shutil.rmtree(config_dir, ignore_errors=True)
    run(["systemctl", "daemon-reload"], check=False)
    run(["udevadm", "control", "--reload-rules"], check=False)
    answer = input(f"Delete updater state at {target_state_root()}? [y/N]: ").strip().lower()
    if answer in {"y", "yes"}:
        shutil.rmtree(target_state_root(), ignore_errors=True)
    print("Repo Offline Sync target uninstalled.")


def interactive() -> None:
    _require_root()
    if not target_config_path().exists():
        install_or_repair()
        return
    print("Repo Offline Sync is already installed.")
    print("  [r] repair/reinstall runtime (default)")
    print("  [t] rotate pairing token")
    print("  [u] uninstall")
    print("  [q] quit")
    choice = input("Select: ").strip().lower() or "r"
    if choice == "r":
        install_or_repair()
    elif choice == "t":
        rotate_token()
    elif choice == "u":
        uninstall()
    elif choice == "q":
        return
    else:
        raise SyncError("invalid selection", 2)
