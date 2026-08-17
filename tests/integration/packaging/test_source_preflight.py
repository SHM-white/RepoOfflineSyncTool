from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from repo_offline_sync.cli.package_args import (
    RepositoryArgument,
    resolve_repository_argument,
)
from repo_offline_sync.packaging.repository import (
    GitObjectFormat,
    RepositoryFacts,
    RepositoryPreflightRequest,
    RepositoryRejected,
    RepositoryRejectionReason,
    preflight_repository,
)

if TYPE_CHECKING:
    from pathlib import Path

_GIT_ENVIRONMENT = {
    "GIT_MASTER": "1",
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}


def _git(
    cwd: Path, *arguments: str, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ("/usr/bin/git", *arguments),
        cwd=cwd,
        env=_GIT_ENVIRONMENT,
        check=check,
        capture_output=True,
    )


def _commit(cwd: Path, message: str) -> None:
    _ = _git(cwd, "add", "--all")
    _ = _git(
        cwd,
        "-c",
        "user.name=Task Seven",
        "-c",
        "user.email=task7@example.invalid",
        "commit",
        "-m",
        message,
    )


def _create_remote_with_history(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    _ = _git(tmp_path, "init", "--bare", str(remote))
    _ = _git(tmp_path, "init", str(seed))
    _ = (seed / "tracked.txt").write_text("first\n", encoding="utf-8")
    _commit(seed, "first")
    _ = (seed / "tracked.txt").write_text("second\n", encoding="utf-8")
    _commit(seed, "second")
    _ = _git(seed, "branch", "-M", "main")
    _ = _git(seed, "remote", "add", "origin", str(remote))
    _ = _git(seed, "push", "origin", "main")
    return remote


def _clone_clean(tmp_path: Path, name: str = "source") -> Path:
    remote = _create_remote_with_history(tmp_path)
    source = tmp_path / name
    _ = _git(tmp_path, "clone", "--branch", "main", str(remote), str(source))
    return source


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _assert_rejected(
    result: RepositoryFacts | RepositoryRejected,
) -> RepositoryRejected:
    assert isinstance(result, RepositoryRejected)
    assert result.exit_code == 3
    return result


def test_omitted_and_explicit_paths_return_equal_immutable_facts(
    tmp_path: Path,
) -> None:
    # Given a clean full clone and two distinct caller contexts
    source = _clone_clean(tmp_path)
    omitted = resolve_repository_argument((), source)
    explicit = resolve_repository_argument((str(source),), tmp_path)
    assert isinstance(omitted, RepositoryArgument)
    assert isinstance(explicit, RepositoryArgument)
    before = _tree_bytes(source)

    # When both resolved paths are preflighted
    omitted_result = preflight_repository(RepositoryPreflightRequest(omitted.path))
    explicit_result = preflight_repository(RepositoryPreflightRequest(explicit.path))

    # Then both flows return the same immutable repository facts without mutation
    assert isinstance(omitted_result, RepositoryFacts)
    assert explicit_result == omitted_result
    assert omitted_result.top_level == source.resolve(strict=True)
    assert omitted_result.common_git_dir == (source / ".git").resolve(strict=True)
    assert omitted_result.current_branch == "main"
    assert omitted_result.object_format is GitObjectFormat.SHA1
    assert omitted_result.source.common_git_dir == omitted_result.common_git_dir
    assert _tree_bytes(source) == before


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("tracked", RepositoryRejectionReason.DIRTY_TRACKED),
        ("untracked", RepositoryRejectionReason.DIRTY_UNTRACKED),
    ],
)
def test_dirty_repository_rejects_without_mutation(
    tmp_path: Path,
    mutation: str,
    reason: RepositoryRejectionReason,
) -> None:
    # Given tracked or nonignored-untracked source changes
    source = _clone_clean(tmp_path)
    target = source / ("tracked.txt" if mutation == "tracked" else "untracked.txt")
    _ = target.write_text("dirty\n", encoding="utf-8")
    before = _tree_bytes(source)

    # When source preflight runs
    result = preflight_repository(RepositoryPreflightRequest(source))

    # Then the exact dirty class is rejected and no bytes change
    assert _assert_rejected(result).reason is reason
    assert _tree_bytes(source) == before


def test_ignored_untracked_file_alone_is_clean(tmp_path: Path) -> None:
    # Given a committed ignore rule and an ignored-only untracked file
    source = _clone_clean(tmp_path)
    _ = (source / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    _commit(source, "ignore generated log")
    _ = (source / "ignored.log").write_text("generated\n", encoding="utf-8")

    # When source preflight runs
    result = preflight_repository(RepositoryPreflightRequest(source))

    # Then ignored-only state is accepted
    assert isinstance(result, RepositoryFacts)


def test_linked_worktree_uses_canonical_shared_common_git_dir(tmp_path: Path) -> None:
    # Given a clean linked worktree whose administrative Git dir is private
    source = _clone_clean(tmp_path)
    linked = tmp_path / "linked"
    _ = _git(source, "worktree", "add", "-b", "linked", str(linked))

    # When source preflight discovers the linked worktree
    result = preflight_repository(RepositoryPreflightRequest(linked))

    # Then top-level stays worktree-specific while common Git identity is shared
    assert isinstance(result, RepositoryFacts)
    assert result.top_level == linked.resolve(strict=True)
    assert result.common_git_dir == (source / ".git").resolve(strict=True)
    assert result.current_branch == "linked"


def test_shallow_clone_is_rejected_without_mutation(tmp_path: Path) -> None:
    # Given a local file-protocol shallow clone
    remote = _create_remote_with_history(tmp_path)
    source = tmp_path / "shallow"
    _ = _git(
        tmp_path,
        "clone",
        "--depth",
        "1",
        "--branch",
        "main",
        f"file://{remote}",
        str(source),
    )
    before = _tree_bytes(source)

    # When source preflight runs
    result = preflight_repository(RepositoryPreflightRequest(source))

    # Then shallow history is rejected without changing the clone
    assert _assert_rejected(result).reason is RepositoryRejectionReason.SHALLOW
    assert _tree_bytes(source) == before


def test_detached_head_requires_explicit_target_contract(tmp_path: Path) -> None:
    # Given a clean detached source and its exact commit
    source = _clone_clean(tmp_path)
    target = _git(source, "rev-parse", "HEAD").stdout.decode().strip()
    _ = _git(source, "checkout", "--detach", target)

    # When preflight runs without and with an explicit target contract
    implicit = preflight_repository(RepositoryPreflightRequest(source))
    explicit = preflight_repository(RepositoryPreflightRequest(source, target))

    # Then only the explicit validated target permits detached HEAD
    assert _assert_rejected(implicit).reason is RepositoryRejectionReason.DETACHED_HEAD
    assert isinstance(explicit, RepositoryFacts)
    assert explicit.current_branch is None
    assert str(explicit.target_commit) == target


@pytest.mark.parametrize("operation", ["merge", "rebase"])
def test_unresolved_operation_is_rejected_without_mutation(
    tmp_path: Path,
    operation: str,
) -> None:
    # Given a real conflicting merge or rebase state
    source = _clone_clean(tmp_path)
    _ = _git(source, "checkout", "-b", "topic", "HEAD~1")
    _ = (source / "tracked.txt").write_text("topic\n", encoding="utf-8")
    _commit(source, "topic conflict")
    if operation == "merge":
        completed = _git(
            source,
            "-c",
            "user.name=Task Seven",
            "-c",
            "user.email=task7@example.invalid",
            "merge",
            "main",
            check=False,
        )
    else:
        completed = _git(source, "rebase", "main", check=False)
    assert completed.returncode != 0
    before = _tree_bytes(source)

    # When source preflight runs
    result = preflight_repository(RepositoryPreflightRequest(source))

    # Then operation state is rejected before cleanup or other mutation
    assert (
        _assert_rejected(result).reason
        is RepositoryRejectionReason.UNRESOLVED_OPERATION
    )
    assert _tree_bytes(source) == before


def test_non_repository_is_typed_and_side_effect_free(tmp_path: Path) -> None:
    # Given an ordinary directory
    source = tmp_path / "ordinary"
    source.mkdir()
    marker = source / "marker.txt"
    _ = marker.write_text("unchanged\n", encoding="utf-8")
    before = _tree_bytes(source)

    # When source preflight runs
    result = preflight_repository(RepositoryPreflightRequest(source))

    # Then rejection creates no repository, cache, profile, or package artifact
    assert _assert_rejected(result).reason is RepositoryRejectionReason.NOT_REPOSITORY
    assert _tree_bytes(source) == before


def test_unsafe_ownership_is_rejected_when_host_permits_chown(tmp_path: Path) -> None:
    # Given a clean source owned by a different uid on a privileged host
    if os.geteuid() != 0:
        pytest.skip("ownership change requires a disposable root test host")
    source = _clone_clean(tmp_path)
    for path in (source, *source.rglob("*")):
        _ = os.chown(path, 65534, 65534, follow_symlinks=False)

    # When source preflight runs
    result = preflight_repository(RepositoryPreflightRequest(source))

    # Then Git's ownership boundary is surfaced without bypass or config mutation
    rejected = _assert_rejected(result)
    assert rejected.reason is RepositoryRejectionReason.UNSAFE_OWNERSHIP
    assert rejected.remediation is not None
