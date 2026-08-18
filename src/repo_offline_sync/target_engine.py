"""Target-side import, release materialization, activation and recovery."""

from __future__ import annotations

import hmac
import os
import shutil
import pwd
import tarfile
import time
import uuid
from pathlib import Path
from typing import Any

from repo_offline_sync.configuration import load_target_config, target_state_root
from repo_offline_sync.core import SyncError, atomic_json, hash_file, inside, load_json, run, safe_relative


TERMINAL = {"committed", "no-op", "rolled-back", "failed-preserved", "rejected", "recovery-failed"}


def _state_dirs() -> None:
    root = target_state_root()
    for name in ("repos", "releases", "persistent", "staging", "transactions", "results", "pending-results", "backups"):
        (root / name).mkdir(parents=True, exist_ok=True)


def _transaction_path(transaction_id: str) -> Path:
    return target_state_root() / "transactions" / f"{transaction_id}.json"


def _write_state(state: dict[str, Any], phase: str) -> None:
    state["phase"] = phase
    state["updated_at"] = int(time.time())
    atomic_json(_transaction_path(str(state["transaction_id"])), state)


def _result_path(transaction_id: str) -> Path:
    return target_state_root() / "results" / f"{transaction_id}.json"


def _bare(repo_id: str) -> Path:
    return target_state_root() / "repos" / f"{repo_id}.git"


def _ensure_bare(repo_id: str, object_format: str = "sha1") -> Path:
    path = _bare(repo_id)
    if object_format not in {"sha1", "sha256"}:
        raise SyncError(f"unsupported Git object format: {object_format}")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "init", "--bare", f"--object-format={object_format}", str(path)])
    observed = run(["git", "--git-dir", str(path), "rev-parse", "--show-object-format"]).stdout.strip()
    if observed != object_format:
        raise SyncError(f"managed repository object format mismatch for {repo_id}: {observed} != {object_format}")
    return path


def _has_commit(bare: Path, commit: str) -> bool:
    return run(["git", "--git-dir", str(bare), "cat-file", "-e", f"{commit}^{{commit}}"], check=False).returncode == 0


def _select_bundle(node: dict[str, Any], bare: Path) -> dict[str, Any] | None:
    target = str(node["target_commit"])
    if _has_commit(bare, target):
        return None
    bundles = node.get("bundles") or []
    candidates: list[dict[str, Any]] = []
    full: list[dict[str, Any]] = []
    for item in bundles:
        if not isinstance(item, dict):
            continue
        if item.get("kind") == "full":
            full.append(item)
            continue
        base = item.get("base_commit")
        if isinstance(base, str) and _has_commit(bare, base):
            candidates.append(item)
    if candidates:
        return min(candidates, key=lambda item: int(item.get("size", 1 << 62)))
    if full:
        return min(full, key=lambda item: int(item.get("size", 1 << 62)))
    raise SyncError(f"repository {node['repo_id']} needs a full bundle", 4)


def _import_repositories(package: Path, manifest: dict[str, Any]) -> dict[str, str]:
    installed: dict[str, str] = {}
    for node in manifest.get("repositories") or []:
        if not isinstance(node, dict):
            raise SyncError("invalid repository entry")
        repo_id = str(node["repo_id"])
        target = str(node["target_commit"])
        bare = _ensure_bare(repo_id, str(node.get("object_format") or "sha1"))
        bundle = _select_bundle(node, bare)
        if bundle is not None:
            bundle_path = inside(package, str(bundle["path"]))
            digest = hash_file(bundle_path)
            if digest["sha256"] != bundle.get("sha256") or digest["size"] != bundle.get("size"):
                raise SyncError(f"bundle hash mismatch: {bundle_path}")
            verify = run(["git", "-C", str(bare), "bundle", "verify", str(bundle_path)], check=False)
            if verify.returncode != 0 and bundle.get("kind") != "full":
                full = [item for item in node.get("bundles") or [] if isinstance(item, dict) and item.get("kind") == "full"]
                if full:
                    bundle = min(full, key=lambda item: int(item.get("size", 1 << 62)))
                    bundle_path = inside(package, str(bundle["path"]))
                    verify = run(["git", "-C", str(bare), "bundle", "verify", str(bundle_path)], check=False)
            if verify.returncode != 0:
                raise SyncError(f"repository {repo_id} needs a full bundle", 4)
            heads = run(["git", "bundle", "list-heads", str(bundle_path)]).stdout.splitlines()
            source_ref = None
            for line in heads:
                fields = line.split(maxsplit=1)
                if len(fields) == 2 and fields[0] == target:
                    source_ref = fields[1]
                    break
            if source_ref is None:
                raise SyncError(f"bundle does not advertise target commit {target}")
            ref = f"refs/offline/packages/{manifest['package_id']}/{repo_id}"
            run(["git", "--git-dir", str(bare), "fetch", str(bundle_path), f"{source_ref}:{ref}"])
        if not _has_commit(bare, target):
            raise SyncError(f"bundle did not provide target commit {target}")
        installed[repo_id] = target
    return installed


def _install_lfs(package: Path, manifest: dict[str, Any]) -> list[str]:
    cached: set[str] = set()
    for item in manifest.get("lfs_files") or []:
        if not isinstance(item, dict):
            raise SyncError("invalid LFS entry")
        oid = str(item["oid"])
        source = inside(package, str(item["path"]))
        digest = hash_file(source)
        if digest["sha256"] != oid or digest["size"] != int(item["size"]):
            raise SyncError(f"invalid LFS object {oid}")
        formats = {str(node["repo_id"]): str(node.get("object_format") or "sha1") for node in manifest.get("repositories") or []}
        bare = _ensure_bare(str(item["repo_id"]), formats.get(str(item["repo_id"]), "sha1"))
        target = bare / "lfs" / "objects" / oid[:2] / oid[2:4] / oid
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        cached.add(oid)
    return sorted(cached)


def _tracked_and_untracked(repo: Path) -> list[str]:
    result = run(
        ["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=False,
    )
    if result.returncode != 0:
        return []
    return [item for item in result.stdout.split("\0") if item]


def _snapshot_existing(destination: Path, manifest: dict[str, Any]) -> tuple[str | None, str | None]:
    """Preserve an unmanaged recursive repository graph without touching indexes."""
    if not destination.exists() and not destination.is_symlink():
        return None, None
    if destination.is_symlink():
        return os.readlink(destination), None
    root_repo_id = str(manifest["root_repo_id"])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_root = target_state_root() / "backups" / root_repo_id / stamp
    backup_root.mkdir(parents=True, exist_ok=True)
    refs: dict[str, str] = {}
    for node in manifest.get("repositories") or []:
        rel = str(node.get("relative_path") or ".")
        repo = destination if rel == "." else destination / rel
        if not repo.exists():
            continue
        probe = run(["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"], check=False)
        if probe.returncode != 0 or probe.stdout.strip() != "true":
            continue
        repo_id = str(node["repo_id"])
        bare = _ensure_bare(repo_id, str(node.get("object_format") or "sha1"))
        head = run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=False)
        if head.returncode == 0:
            backup_ref = f"refs/heads/offline-backup/{stamp}"
            fetched = run(["git", "--git-dir", str(bare), "fetch", str(repo), f"HEAD:{backup_ref}"], check=False)
            if fetched.returncode == 0:
                refs[repo_id] = backup_ref
    if refs:
        atomic_json(backup_root / "backup-refs.json", refs)
    files = _tracked_and_untracked(destination) if (destination / ".git").exists() else []
    if files:
        with tarfile.open(backup_root / "working-tree.tar.gz", "w:gz") as archive:
            for raw in files:
                source = destination / raw
                if source.exists() and not source.is_symlink():
                    archive.add(source, arcname=raw, recursive=True)
    adopted = destination.with_name(f"{destination.name}.pre-offline-sync-{stamp}")
    if adopted.exists():
        raise SyncError(f"backup destination already exists: {adopted}")
    atomic_json(backup_root / "adoption.json", {"original": str(destination), "preserved_as": str(adopted)})
    return None, str(adopted)


def _remove_worktree_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _materialize(package: Path, manifest: dict[str, Any]) -> Path:
    root_repo_id = str(manifest["root_repo_id"])
    target = str(manifest["target_commit"])
    release = target_state_root() / "releases" / root_repo_id / target
    repositories = list(manifest.get("repositories") or [])
    ready_marker = release / ".repo-offline-sync-ready"
    if ready_marker.is_file():
        return release
    if release.exists():
        shutil.rmtree(release, ignore_errors=True)
        for node in repositories:
            run(["git", "--git-dir", str(_ensure_bare(str(node["repo_id"]), str(node.get("object_format") or "sha1"))), "worktree", "prune"], check=False)
    release.parent.mkdir(parents=True, exist_ok=True)
    roots = [item for item in repositories if item.get("parent_repo_id") is None]
    if len(roots) != 1:
        raise SyncError("manifest must contain one root repository")
    root = roots[0]
    run(["git", "--git-dir", str(_ensure_bare(str(root["repo_id"]), str(root.get("object_format") or "sha1"))), "worktree", "add", "--detach", str(release), str(root["target_commit"])])
    try:
        for node in repositories:
            if node is root:
                continue
            rel = str(node["relative_path"])
            child = inside(release, rel)
            if child.exists() or child.is_symlink():
                _remove_worktree_path(child)
            child.parent.mkdir(parents=True, exist_ok=True)
            run(["git", "--git-dir", str(_ensure_bare(str(node["repo_id"]), str(node.get("object_format") or "sha1"))), "worktree", "add", "--detach", str(child), str(node["target_commit"])])
        _materialize_lfs(package, manifest, release)
        _attach_persistent(manifest, release)
        ready_marker.write_text(str(manifest["package_id"]) + "\n", encoding="utf-8")
    except Exception:
        shutil.rmtree(release, ignore_errors=True)
        raise
    return release


def _materialize_lfs(package: Path, manifest: dict[str, Any], release: Path) -> None:
    for item in manifest.get("lfs_files") or []:
        repo_path = str(item["repo_path"])
        repo_root = release if repo_path == "." else inside(release, repo_path)
        output = inside(repo_root, str(item["file"]))
        source = inside(package, str(item["path"]))
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output)


def _attach_persistent(manifest: dict[str, Any], release: Path) -> None:
    root_repo_id = str(manifest["root_repo_id"])
    persistent_root = target_state_root() / "persistent" / root_repo_id
    for raw in manifest.get("persistent_paths") or []:
        rel = safe_relative(str(raw))
        source = release / rel
        persistent = persistent_root / rel
        persistent.parent.mkdir(parents=True, exist_ok=True)
        if not persistent.exists():
            if source.is_dir():
                shutil.copytree(source, persistent)
            elif source.exists():
                shutil.copy2(source, persistent)
            else:
                persistent.mkdir(parents=True, exist_ok=True)
        if source.exists() or source.is_symlink():
            _remove_worktree_path(source)
        source.parent.mkdir(parents=True, exist_ok=True)
        source.symlink_to(persistent)


def _action_argv(item: dict[str, Any]) -> list[str]:
    argv = [str(value) for value in item.get("argv") or []]
    if not argv:
        raise SyncError("action argv must not be empty")
    user = str(item.get("user") or "root")
    if os.geteuid() == 0 and user not in {"", "root"}:
        return ["runuser", "-u", user, "--", *argv]
    return argv


def _run_phase(manifest: dict[str, Any], release: Path, phase: str) -> None:
    actions = manifest.get("actions") or {}
    for item in actions.get(phase, []) if isinstance(actions, dict) else []:
        if not isinstance(item, dict):
            raise SyncError(f"invalid {phase} action")
        cwd_raw = str(item.get("cwd") or ".")
        cwd = release if cwd_raw == "." else inside(release, cwd_raw)
        env = {str(k): str(v) for k, v in dict(item.get("env") or {}).items()}
        user = str(item.get("user") or "root")
        try:
            account = pwd.getpwnam(user)
        except KeyError as exc:
            raise SyncError(f"action user does not exist: {user}") from exc
        env.setdefault("HOME", account.pw_dir)
        env.setdefault("USER", user)
        env.setdefault("LOGNAME", user)
        timeout = max(1, int(item.get("timeout") or 300))
        run(_action_argv(item), cwd=cwd, env=env, timeout=timeout, clean_env=True)


def _systemctl(action: str, unit: str) -> None:
    if unit:
        result = run(["systemctl", action, unit], check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise SyncError(f"systemctl {action} {unit} failed: {detail}")


def _prepare_destination_for_activation(state: dict[str, Any]) -> None:
    destination = Path(str(state["destination"]))
    adopted = state.get("adopted_original")
    if adopted and destination.exists() and not destination.is_symlink():
        os.replace(destination, Path(str(adopted)))


def _activate(destination: Path, release: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f".{destination.name}.offline-sync-{uuid.uuid4().hex}.tmp")
    tmp.symlink_to(release)
    os.replace(tmp, destination)


def _restore_previous(state: dict[str, Any]) -> None:
    destination = Path(str(state["destination"]))
    unit = str(state.get("service_unit") or "")
    _systemctl("stop", unit)
    previous = state.get("previous_symlink")
    adopted = state.get("adopted_original")
    if previous:
        tmp = destination.with_name(f".{destination.name}.rollback-{uuid.uuid4().hex}")
        tmp.symlink_to(str(previous))
        os.replace(tmp, destination)
    elif adopted:
        adopted_path = Path(str(adopted))
        if adopted_path.exists():
            if destination.is_symlink() or destination.exists():
                _remove_worktree_path(destination)
            os.replace(adopted_path, destination)
    else:
        if destination.is_symlink():
            destination.unlink()
    _systemctl("start", unit)


def _make_result(manifest: dict[str, Any], transaction_id: str, status: str, installed: dict[str, str], cached_lfs: list[str], release: Path | None, message: str = "") -> dict[str, Any]:
    return {
        "schema": "result-v1",
        "transaction_id": transaction_id,
        "package_id": manifest["package_id"],
        "target_id": manifest["target_id"],
        "media_id": manifest["media_id"],
        "root_repo_id": manifest["root_repo_id"],
        "generation": manifest["generation"],
        "status": status,
        "installed_repositories": installed,
        "cached_lfs_oids": cached_lfs,
        "active_release": None if release is None else str(release),
        "message": message,
        "finished_at": int(time.time()),
    }


def _validate_manifest(package: Path) -> dict[str, Any]:
    path = package / "manifest.json"
    value = load_json(path)
    if not isinstance(value, dict) or value.get("schema") != "manifest-v1":
        raise SyncError("invalid package manifest")
    config = load_target_config()
    if value.get("target_id") != config.get("target_id"):
        raise SyncError("package target_id does not match this target")
    expected = str(config.get("pairing_token") or "")
    presented = str(value.get("pairing_token") or "")
    if not expected or not hmac.compare_digest(expected, presented):
        raise SyncError("package pairing token does not match this target")
    destination = Path(str(value.get("destination") or ""))
    if not destination.is_absolute() or destination == Path("/"):
        raise SyncError("invalid target destination")
    resolved = destination.resolve(strict=False)
    forbidden = (Path("/proc"), Path("/sys"), Path("/dev"), Path("/run"))
    if any(resolved == item or item in resolved.parents for item in forbidden):
        raise SyncError("target destination is a pseudo/runtime filesystem")
    managed = target_state_root().resolve(strict=False)
    if resolved == managed or managed in resolved.parents:
        raise SyncError("target destination overlaps updater state")
    for raw in value.get("persistent_paths") or []:
        safe_relative(str(raw))
    return value


def _existing_success(manifest: dict[str, Any]) -> dict[str, Any] | None:
    results = target_state_root() / "results"
    for path in results.glob("*.json"):
        try:
            value = load_json(path)
        except OSError:
            continue
        if isinstance(value, dict) and value.get("package_id") == manifest.get("package_id") and value.get("status") in {"success", "no-op"}:
            return value
    return None


def apply_package(package: Path) -> dict[str, Any]:
    """Apply one already-local, verified package transaction."""
    _state_dirs()
    manifest = _validate_manifest(package)
    existing = _existing_success(manifest)
    if existing is not None:
        return existing
    transaction_id = uuid.uuid4().hex
    destination = Path(str(manifest["destination"]))
    state = {
        "schema": "transaction-v1",
        "transaction_id": transaction_id,
        "package_id": manifest["package_id"],
        "target_id": manifest["target_id"],
        "generation": manifest["generation"],
        "destination": str(destination),
        "service_unit": manifest.get("service_unit", ""),
        "failure_policy": manifest.get("failure_policy", "rollback"),
        "previous_symlink": None,
        "adopted_original": None,
        "phase": "verified",
    }
    _write_state(state, "verified")
    installed: dict[str, str] = {}
    cached_lfs: list[str] = []
    release: Path | None = None
    try:
        installed = _import_repositories(package, manifest)
        cached_lfs = _install_lfs(package, manifest)
        _write_state(state, "imported")
        previous, adopted = _snapshot_existing(destination, manifest)
        state["previous_symlink"] = previous
        state["adopted_original"] = adopted
        _write_state(state, "snapshot-created")
        release = _materialize(package, manifest)
        _write_state(state, "staged")
        _run_phase(manifest, release, "preflight")
        _run_phase(manifest, release, "build")
        _write_state(state, "built")
        _run_phase(manifest, release, "pre_activate")
        _systemctl("stop", str(manifest.get("service_unit") or ""))
        _write_state(state, "activating")
        _prepare_destination_for_activation(state)
        _activate(destination, release)
        _write_state(state, "activated")
        _run_phase(manifest, release, "post_activate")
        _systemctl("start", str(manifest.get("service_unit") or ""))
        _run_phase(manifest, release, "health")
        _write_state(state, "health-checked")
        result = _make_result(manifest, transaction_id, "success", installed, cached_lfs, release)
        atomic_json(_result_path(transaction_id), result)
        _write_state(state, "committed")
        return result
    except Exception as raw_exc:
        exc = raw_exc if isinstance(raw_exc, SyncError) else SyncError(str(raw_exc), 8)
        if exc.exit_code == 4:
            result = _make_result(manifest, transaction_id, "needs-full-bundle", installed, cached_lfs, release, str(exc))
            atomic_json(_result_path(transaction_id), result)
            _write_state(state, "rejected")
            return result
        policy = str(manifest.get("failure_policy") or "rollback")
        if state.get("phase") in {"activating", "activated", "health-checked"} and policy == "rollback":
            try:
                _restore_previous(state)
            except Exception as recovery_exc:
                result = _make_result(manifest, transaction_id, "recovery-failed", installed, cached_lfs, release, f"{exc}; rollback failed: {recovery_exc}")
                atomic_json(_result_path(transaction_id), result)
                _write_state(state, "recovery-failed")
                return result
            result = _make_result(manifest, transaction_id, "failed-rolled-back", installed, cached_lfs, release, str(exc))
            atomic_json(_result_path(transaction_id), result)
            _write_state(state, "rolled-back")
            return result
        try:
            _systemctl("stop", str(manifest.get("service_unit") or ""))
        except SyncError:
            pass
        result = _make_result(manifest, transaction_id, "failed-preserved", installed, cached_lfs, release, str(exc))
        atomic_json(_result_path(transaction_id), result)
        _write_state(state, "failed-preserved")
        return result


def recover_transactions() -> list[str]:
    """Resolve interrupted transactions deterministically at scanner startup."""
    _state_dirs()
    recovered: list[str] = []
    for path in sorted((target_state_root() / "transactions").glob("*.json")):
        try:
            state = load_json(path)
        except OSError:
            continue
        if not isinstance(state, dict) or state.get("phase") in TERMINAL:
            continue
        transaction = str(state.get("transaction_id") or path.stem)
        phase = str(state.get("phase") or "")
        if phase in {"activating", "activated", "health-checked"} and state.get("failure_policy") == "rollback":
            try:
                _restore_previous(state)
                _write_state(state, "rolled-back")
                recovered.append(f"{transaction}: rolled back")
                continue
            except Exception as exc:
                state["recovery_error"] = str(exc)
                _write_state(state, "recovery-failed")
                recovered.append(f"{transaction}: recovery failed")
                continue
        _write_state(state, "rejected")
        recovered.append(f"{transaction}: aborted before activation")
    return recovered
