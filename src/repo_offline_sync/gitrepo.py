"""Git, recursive submodule, LFS and bundle operations."""

from __future__ import annotations

import configparser
import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable

from repo_offline_sync.core import SyncError, hash_file, run, safe_relative

_LFS = re.compile(
    rb"^version https://git-lfs.github.com/spec/v1\n"
    rb"oid sha256:([0-9a-f]{64})\n"
    rb"size ([0-9]+)\n?$"
)


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = run(["git", "-C", str(repo), *args], check=check)
    return result.stdout.strip()


def repository_facts(repo: Path) -> dict[str, Any]:
    repo = repo.expanduser().resolve()
    if not repo.exists():
        raise SyncError(f"repository does not exist: {repo}", 2)
    if git(repo, "rev-parse", "--is-inside-work-tree", check=False) != "true":
        raise SyncError(f"not a Git work tree: {repo}", 2)
    if git(repo, "rev-parse", "--is-shallow-repository") == "true":
        raise SyncError("shallow repositories are not supported")
    dirty = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if dirty:
        raise SyncError("source repository is dirty; commit/stash/remove local changes before packaging")
    commit = git(repo, "rev-parse", "HEAD")
    object_format = git(repo, "rev-parse", "--show-object-format")
    if object_format not in {"sha1", "sha256"}:
        raise SyncError(f"unsupported Git object format: {object_format}")
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    remote = git(repo, "remote", "get-url", "origin", check=False)
    if not remote:
        remote = f"local:{common}"
    return {"path": repo, "commit": commit, "common_git_dir": common, "remote": remote, "object_format": object_format}


def _gitmodules(repo: Path, commit: str) -> list[tuple[str, str]]:
    raw = git(repo, "show", f"{commit}:.gitmodules", check=False)
    if not raw:
        return []
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(raw)
    except configparser.Error as exc:
        raise SyncError(f"invalid .gitmodules in {repo}") from exc
    result: list[tuple[str, str]] = []
    for section in parser.sections():
        if not section.startswith('submodule "'):
            continue
        path = parser.get(section, "path", fallback="")
        url = parser.get(section, "url", fallback="")
        safe_relative(path)
        result.append((path, url))
    return result


def _gitlink(repo: Path, commit: str, subpath: str) -> str:
    line = git(repo, "ls-tree", commit, "--", subpath)
    if not line:
        raise SyncError(f"missing committed submodule gitlink: {subpath}")
    meta = line.split("\t", 1)[0].split()
    if len(meta) < 3 or meta[0] != "160000" or meta[1] != "commit":
        raise SyncError(f"not a submodule gitlink: {subpath}")
    return meta[2]


def _child_id(root_repo_id: str, relative_path: str) -> str:
    return hashlib.sha256(f"{root_repo_id}\0{relative_path}".encode()).hexdigest()[:32]


def discover_graph(root: Path, root_repo_id: str) -> list[dict[str, Any]]:
    """Return parent-first exact repository graph; local submodules must be present."""
    root_facts = repository_facts(root)
    graph: list[dict[str, Any]] = []

    def visit(repo: Path, relative: str, parent_id: str | None, expected: str, url: str) -> None:
        facts = repository_facts(repo)
        if facts["commit"] != expected:
            raise SyncError(
                f"submodule {relative} is not checked out at committed gitlink "
                f"{expected} (found {facts['commit']})"
            )
        repo_id = root_repo_id if parent_id is None else _child_id(root_repo_id, relative)
        graph.append(
            {
                "repo_id": repo_id,
                "parent_repo_id": parent_id,
                "relative_path": relative,
                "target_commit": expected,
                "remote": url or facts["remote"],
                "source_path": str(repo),
                "common_git_dir": str(facts["common_git_dir"]),
                "object_format": facts["object_format"],
            }
        )
        for child_path, child_url in _gitmodules(repo, expected):
            child_rel = child_path if relative == "." else f"{relative}/{child_path}"
            child_commit = _gitlink(repo, expected, child_path)
            child = (repo / child_path).resolve()
            if not child.exists():
                raise SyncError(f"required submodule is not initialized locally: {child_rel}")
            visit(child, child_rel, repo_id, child_commit, child_url)

    visit(root_facts["path"], ".", None, root_facts["commit"], root_facts["remote"])
    return graph


def _tree_blobs(repo: Path, commit: str) -> Iterable[tuple[str, str]]:
    raw = run(["git", "-C", str(repo), "ls-tree", "-r", "-z", commit]).stdout
    for entry in raw.split("\0"):
        if not entry or "\t" not in entry:
            continue
        left, path = entry.split("\t", 1)
        fields = left.split()
        if len(fields) == 3 and fields[1] == "blob":
            yield fields[2], path


def discover_lfs(graph: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find exact-target LFS pointers and verify local object bytes."""
    found: list[dict[str, Any]] = []
    for node in graph:
        repo = Path(node["source_path"])
        common = Path(node["common_git_dir"])
        for blob, file_path in _tree_blobs(repo, node["target_commit"]):
            size_text = git(repo, "cat-file", "-s", blob)
            try:
                blob_size = int(size_text)
            except ValueError:
                continue
            if blob_size > 1024:
                continue
            raw = subprocess.run(
                ["git", "-C", str(repo), "cat-file", "blob", blob],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if raw.returncode != 0:
                continue
            match = _LFS.fullmatch(raw.stdout)
            if match is None:
                continue
            oid = match.group(1).decode()
            size = int(match.group(2))
            object_path = common / "lfs" / "objects" / oid[:2] / oid[2:4] / oid
            if not object_path.is_file():
                raise SyncError(f"missing Git LFS object {oid} for {node['relative_path']}/{file_path}")
            digest = hash_file(object_path)
            if digest["sha256"] != oid or digest["size"] != size:
                raise SyncError(f"invalid Git LFS object {oid}")
            found.append(
                {
                    "repo_id": node["repo_id"],
                    "repo_path": node["relative_path"],
                    "file": file_path,
                    "oid": oid,
                    "size": size,
                    "source_object": str(object_path),
                }
            )
    return found


def is_ancestor(repo: Path, base: str, target: str) -> bool:
    result = run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", base, target],
        check=False,
    )
    return result.returncode == 0


def create_bundle(repo: Path, target: str, destination: Path, base: str | None) -> dict[str, Any] | None:
    """Create one indivisible bundle. HEAD must equal the exact target revision."""
    head = git(repo, "rev-parse", "HEAD")
    if head != target:
        raise SyncError(f"bundle source HEAD changed while packaging: {repo}")
    if base == target:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    args = ["git", "-C", str(repo), "bundle", "create", str(destination)]
    kind = "full"
    if base and is_ancestor(repo, base, target):
        args.extend([f"^{base}", "HEAD"])
        kind = "incremental"
    else:
        args.append("HEAD")
        base = None
    run(args)
    run(["git", "-C", str(repo), "bundle", "verify", str(destination)])
    info = hash_file(destination)
    info.update({"kind": kind, "base_commit": base, "target_commit": target})
    return info


def create_full_bundle(repo: Path, target: str, destination: Path) -> dict[str, Any]:
    result = create_bundle(repo, target, destination, None)
    if result is None:
        raise SyncError("internal error creating full bundle")
    return result
