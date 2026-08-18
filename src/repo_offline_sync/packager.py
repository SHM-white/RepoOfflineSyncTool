"""Interactive host-side packaging orchestration."""

from __future__ import annotations

import getpass
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from repo_offline_sync.configuration import cache_root, load_or_create_profile, save_profile, state_root
from repo_offline_sync.core import SyncError, atomic_json, hash_file, load_json
from repo_offline_sync.gitrepo import create_bundle, create_full_bundle, discover_graph, discover_lfs, is_ancestor, repository_facts
from repo_offline_sync.media import discover_media, initialize_media, media_root, publish_package, read_media_marker, receipts


_DANGEROUS_PREFIXES = (Path("/etc"), Path("/opt"), Path("/root"), Path("/usr"), Path("/var"), Path("/boot"))


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def _yes_no(label: str, *, default: bool = False) -> bool:
    marker = "Y/n" if default else "y/N"
    raw = input(f"{label} [{marker}]: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def _configure_new_profile(path: Path, profile: dict[str, Any]) -> None:
    print(f"Created host profile: {path}")
    print("Target installer prints target_id and pairing_token; copy them here.")
    profile["target_id"] = _prompt("Target ID", str(profile["target_id"]))
    token = _prompt("Pairing token", str(profile["pairing_token"]))
    if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        raise SyncError("pairing token must be 32 lowercase hexadecimal characters", 2)
    profile["pairing_token"] = token
    profile["destination"] = _prompt("Target destination", str(profile["destination"]))
    profile["service_user"] = _prompt("Service user", str(profile["service_user"]))
    profile["service_unit"] = _prompt("systemd service unit (blank for none)", "")
    persistent = _prompt("Persistent relative paths, comma separated", "")
    profile["persistent_paths"] = [part.strip() for part in persistent.split(",") if part.strip()]
    policy = _prompt("Failure policy: rollback / keep-failed-stopped", "rollback")
    if policy not in {"rollback", "keep-failed-stopped"}:
        raise SyncError("invalid failure policy", 2)
    profile["failure_policy"] = policy
    save_profile(path, profile)


def _is_dangerous(destination: Path, service_user: str) -> bool:
    resolved = destination.resolve(strict=False)
    safe_home = Path("/home") / service_user
    if resolved == safe_home or safe_home in resolved.parents:
        return False
    return resolved == Path("/") or any(resolved == prefix or prefix in resolved.parents for prefix in _DANGEROUS_PREFIXES) or not str(resolved).startswith("/home/")


def _confirm_destination(profile: dict[str, Any]) -> bool:
    destination = Path(str(profile["destination"]))
    if not destination.is_absolute() or destination == Path("/"):
        raise SyncError("destination must be an absolute non-root path", 2)
    dangerous = _is_dangerous(destination, str(profile["service_user"]))
    if not dangerous:
        return False
    if not bool(profile.get("danger_enabled")):
        print(f"Destination {destination} is outside the normal service-user home tree.")
        if not _yes_no("Enable dangerous destination for this saved profile?", default=False):
            raise SyncError("dangerous destination was not enabled", 2)
        profile["danger_enabled"] = True
    if not sys.stdin.isatty():
        raise SyncError("dangerous destination requires an interactive TTY", 2)
    typed = input(f"Type exactly 'yes' to package for dangerous destination {destination}: ")
    if typed != "yes":
        raise SyncError("dangerous destination confirmation rejected", 2)
    return True


def _choose_media() -> Path:
    explicit = os.environ.get("REPO_OFFLINE_SYNC_MEDIA")
    if explicit:
        mount = Path(explicit).expanduser().resolve()
    else:
        choices = discover_media()
        if len(choices) == 1:
            mount = choices[0]
        elif choices:
            print("Available initialized media:")
            for index, item in enumerate(choices, 1):
                print(f"  {index}. {item}")
            raw = _prompt("Select media number", "1")
            try:
                mount = choices[int(raw) - 1]
            except (ValueError, IndexError) as exc:
                raise SyncError("invalid media selection", 2) from exc
        else:
            mount = Path(_prompt("Mounted removable-media path")).expanduser().resolve()
    marker = media_root(mount) / "media.json"
    if not marker.exists():
        if not _yes_no(f"Initialize {mount} for Repo Offline Sync?", default=False):
            raise SyncError("media is not initialized", 2)
        initialize_media(mount)
    read_media_marker(mount)
    return mount


def _ingest_receipts(mount: Path, target_id: str) -> list[dict[str, Any]]:
    values = [item for item in receipts(mount) if item.get("target_id") == target_id]
    destination = state_root() / "receipts" / target_id
    destination.mkdir(parents=True, exist_ok=True)
    for value in values:
        transaction = str(value.get("transaction_id") or uuid.uuid4().hex)
        atomic_json(destination / f"{transaction}.json", value)
    for path in sorted(destination.glob("*.json")):
        try:
            value = load_json(path)
        except OSError:
            continue
        if isinstance(value, dict) and value.get("schema") == "result-v1" and value not in values:
            values.append(value)
    return values


def _latest_bases(receipt_values: list[dict[str, Any]]) -> dict[str, str]:
    best: tuple[int, dict[str, str]] | None = None
    for item in receipt_values:
        if item.get("status") not in {"success", "no-op"}:
            continue
        repos = item.get("installed_repositories")
        if not isinstance(repos, dict):
            continue
        generation = int(item.get("generation", -1))
        clean = {str(key): str(value) for key, value in repos.items() if isinstance(key, str) and isinstance(value, str)}
        if best is None or generation > best[0]:
            best = (generation, clean)
    return {} if best is None else best[1]


def _actions(profile: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    raw = profile.get("actions")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for phase in ("preflight", "build", "pre_activate", "post_activate", "health"):
        phase_items = raw.get(phase, [])
        if not isinstance(phase_items, list):
            raise SyncError(f"profile actions.{phase} must be a list", 2)
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(phase_items):
            if not isinstance(item, dict) or not isinstance(item.get("argv"), list) or not item["argv"]:
                raise SyncError(f"profile actions.{phase}[{index}] needs non-empty argv[]", 2)
            argv = [str(value) for value in item["argv"]]
            executable = Path(argv[0]).name
            if executable in {"sh", "bash", "dash", "zsh", "fish"} and any(value in {"-c", "-lc"} for value in argv[1:]):
                raise SyncError(f"profile actions.{phase}[{index}] may not execute shell command strings", 2)
            normalized.append(
                {
                    "name": str(item.get("name") or f"{phase}-{index + 1}"),
                    "argv": argv,
                    "cwd": str(item.get("cwd") or "."),
                    "env": {str(k): str(v) for k, v in dict(item.get("env") or {}).items()},
                    "user": str(item.get("user") or profile.get("service_user") or "root"),
                    "timeout": int(item.get("timeout") or 300),
                }
            )
        result[phase] = normalized
    return result


def _segment_target(generation: int, base_generation: int | None) -> int:
    age = generation if base_generation is None else max(0, generation - base_generation)
    if age <= 3:
        return 25 * 1024 * 1024
    if age <= 10:
        return 50 * 1024 * 1024
    return 95 * 1024 * 1024


def build_package(repo: Path, mount: Path, profile_path: Path, profile: dict[str, Any], full_fallback: bool) -> tuple[Path, dict[str, Any]]:
    facts = repository_facts(repo)
    graph = discover_graph(repo, str(profile["repo_id"]))
    lfs = discover_lfs(graph)
    marker = read_media_marker(mount)
    receipt_values = _ingest_receipts(mount, str(profile["target_id"]))
    bases = _latest_bases(receipt_values)
    if not bases and not full_fallback:
        print("warning: no successful target receipt is known; without full fallback this package will report needs-full-bundle on a fresh target")
    generation = int(profile.get("generation", 0)) + 1
    package_id = uuid.uuid4().hex
    work = cache_root() / "packages" / package_id
    shutil.rmtree(work, ignore_errors=True)
    (work / "bundles").mkdir(parents=True)
    (work / "lfs").mkdir(parents=True)

    repositories: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    for node in graph:
        source = Path(node["source_path"])
        repo_id = str(node["repo_id"])
        base = bases.get(repo_id)
        bundle_entries: list[dict[str, Any]] = []
        incremental_path = work / "bundles" / f"{repo_id}-incremental.bundle"
        info = None
        if base is not None and (base == str(node["target_commit"]) or is_ancestor(source, base, str(node["target_commit"]))):
            info = create_bundle(source, str(node["target_commit"]), incremental_path, base)
        if info is not None:
            rel = incremental_path.relative_to(work).as_posix()
            bundle_entry = {
                "path": rel,
                "kind": info["kind"],
                "base_commit": info["base_commit"],
                "target_commit": info["target_commit"],
                "size": info["size"],
                "sha256": info["sha256"],
                "tier_target_bytes": _segment_target(generation, None),
                "oversize": info["size"] > _segment_target(generation, None),
            }
            bundle_entries.append(bundle_entry)
            artifacts.append({"path": rel, "sha256": info["sha256"], "size": info["size"]})
        if full_fallback and (info is None or info["kind"] != "full"):
            full_path = work / "bundles" / f"{repo_id}-full.bundle"
            full = create_full_bundle(source, str(node["target_commit"]), full_path)
            rel = full_path.relative_to(work).as_posix()
            bundle_entries.append(
                {
                    "path": rel,
                    "kind": "full",
                    "base_commit": None,
                    "target_commit": full["target_commit"],
                    "size": full["size"],
                    "sha256": full["sha256"],
                    "tier_target_bytes": None,
                    "oversize": False,
                }
            )
            artifacts.append({"path": rel, "sha256": full["sha256"], "size": full["size"]})
        repositories.append(
            {
                "repo_id": repo_id,
                "parent_repo_id": node["parent_repo_id"],
                "relative_path": node["relative_path"],
                "target_commit": node["target_commit"],
                "remote": node["remote"],
                "object_format": node["object_format"],
                "bundles": bundle_entries,
            }
        )

    seen_lfs: set[str] = set()
    manifest_lfs: list[dict[str, Any]] = []
    for item in lfs:
        oid = str(item["oid"])
        rel = f"lfs/{oid}"
        target = work / rel
        if oid not in seen_lfs:
            shutil.copy2(Path(item["source_object"]), target)
            digest = hash_file(target)
            artifacts.append({"path": rel, "sha256": digest["sha256"], "size": digest["size"]})
            seen_lfs.add(oid)
        manifest_lfs.append({key: item[key] for key in ("repo_id", "repo_path", "file", "oid", "size")} | {"path": rel})

    manifest = {
        "schema": "manifest-v1",
        "package_id": package_id,
        "target_id": profile["target_id"],
        "media_id": marker["media_id"],
        "generation": generation,
        "root_repo_id": profile["repo_id"],
        "target_commit": facts["commit"],
        "destination": profile["destination"],
        "service_user": profile["service_user"],
        "service_unit": profile.get("service_unit", ""),
        "failure_policy": profile.get("failure_policy", "rollback"),
        "persistent_paths": list(profile.get("persistent_paths") or []),
        "actions": _actions(profile),
        "pairing_token": profile["pairing_token"],
        "dangerous_confirmed": _is_dangerous(Path(str(profile["destination"])), str(profile["service_user"])),
        "full_fallback_included": full_fallback,
        "repositories": repositories,
        "lfs_files": manifest_lfs,
        "artifacts": artifacts,
    }
    atomic_json(work / "manifest.json", manifest)
    return work, manifest


def package_repository(repo: Path) -> Path:
    facts = repository_facts(repo)
    profile_path, profile, created = load_or_create_profile(facts["common_git_dir"], facts["remote"])
    if created:
        _configure_new_profile(profile_path, profile)
    dangerous = _confirm_destination(profile)
    profile["danger_enabled"] = bool(profile.get("danger_enabled") or dangerous)
    save_profile(profile_path, profile)
    mount = _choose_media()
    full = _yes_no("Include full fallback bundle?", default=False)
    work, manifest = build_package(repo, mount, profile_path, profile, full)
    final = publish_package(work, mount, str(manifest["package_id"]))
    profile["generation"] = int(manifest["generation"])
    save_profile(profile_path, profile)
    print(f"Published package {manifest['package_id']} -> {final}")
    return final
