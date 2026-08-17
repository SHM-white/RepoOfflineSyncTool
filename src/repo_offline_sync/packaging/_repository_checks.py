"""Operation, worktree, and provenance checks for repository preflight."""

from __future__ import annotations

from typing import TYPE_CHECKING

from repo_offline_sync.packaging._profile_models import ProfileFormatError
from repo_offline_sync.packaging._repository_commands import GitInvocation
from repo_offline_sync.packaging._repository_models import (
    RepositoryRejected,
    RepositoryRejectionReason,
)
from repo_offline_sync.packaging._repository_parsing import (
    DiscoveryOutput,
    WorktreeStatus,
    parse_operation_paths,
    parse_porcelain_v2,
    parse_single_text,
)
from repo_offline_sync.packaging.profiles import RepositorySource

if TYPE_CHECKING:
    from pathlib import Path

    from repo_offline_sync.packaging._repository_invocation import RepositoryInvoker


def check_operations(
    top_level: Path,
    invoker: RepositoryInvoker,
) -> RepositoryRejected | None:
    """Reject Git-resolved merge, rebase, and sequencer state."""
    operation = "resolve_operation_state"
    result = invoker.run(
        top_level,
        GitInvocation(
            operation,
            (
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "MERGE_HEAD",
                "--git-path",
                "rebase-merge",
                "--git-path",
                "rebase-apply",
                "--git-path",
                "sequencer",
                "--git-path",
                "CHERRY_PICK_HEAD",
                "--git-path",
                "REVERT_HEAD",
            ),
        ),
    )
    if isinstance(result, RepositoryRejected):
        return result
    paths = parse_operation_paths(result.stdout, top_level)
    if paths is None:
        return RepositoryRejected(RepositoryRejectionReason.MALFORMED_OUTPUT, operation)
    if any(path.exists() for path in paths):
        return RepositoryRejected(
            RepositoryRejectionReason.UNRESOLVED_OPERATION,
            operation,
        )
    return None


def check_status(
    top_level: Path,
    invoker: RepositoryInvoker,
) -> RepositoryRejected | None:
    """Reject tracked and nonignored-untracked porcelain records."""
    operation = "inspect_status"
    result = invoker.run(
        top_level,
        GitInvocation(
            operation,
            ("status", "--porcelain=v2", "-z", "--ignored", "--untracked-files=all"),
        ),
    )
    if isinstance(result, RepositoryRejected):
        return result
    status = parse_porcelain_v2(result.stdout)
    match status:
        case WorktreeStatus.CLEAN:
            return None
        case WorktreeStatus.MALFORMED:
            reason = RepositoryRejectionReason.MALFORMED_OUTPUT
        case WorktreeStatus.TRACKED:
            reason = RepositoryRejectionReason.DIRTY_TRACKED
        case WorktreeStatus.UNTRACKED:
            reason = RepositoryRejectionReason.DIRTY_UNTRACKED
    return RepositoryRejected(reason, operation)


def resolve_source(
    discovery: DiscoveryOutput,
    invoker: RepositoryInvoker,
) -> RepositorySource | RepositoryRejected:
    """Resolve origin provenance and construct Task 4 source identity."""
    operation = "resolve_provenance"
    result = invoker.run(
        discovery.top_level,
        GitInvocation(operation, ("config", "--get", "remote.origin.url"), (0, 1)),
    )
    if isinstance(result, RepositoryRejected):
        return result
    text = parse_single_text(result.stdout) if result.returncode == 0 else None
    if text is not None:
        try:
            return RepositorySource.create(discovery.common_git_dir, text)
        except ProfileFormatError:
            pass
    return RepositoryRejected(
        RepositoryRejectionReason.PROVENANCE_UNAVAILABLE,
        operation,
    )
