"""Staged read-only validation of one packaging source repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from repo_offline_sync.domain.errors import InvalidOid
from repo_offline_sync.domain.identifiers import parse_git_oid
from repo_offline_sync.packaging._repository_checks import (
    check_operations,
    check_status,
    resolve_source,
)
from repo_offline_sync.packaging._repository_commands import (
    CommandRunner,
    GitInvocation,
)
from repo_offline_sync.packaging._repository_invocation import RepositoryInvoker
from repo_offline_sync.packaging._repository_models import (
    GitObjectFormat,
    RepositoryFacts,
    RepositoryPreflightRequest,
    RepositoryPreflightResult,
    RepositoryRejected,
    RepositoryRejectionReason,
)
from repo_offline_sync.packaging._repository_parsing import (
    DiscoveryOutput,
    parse_discovery,
    parse_single_text,
)
from repo_offline_sync.platform.commands import run_command

if TYPE_CHECKING:
    from pathlib import Path

    from repo_offline_sync.domain.identifiers import GitOid


@dataclass(frozen=True, slots=True)
class _Discovered:
    discovery: DiscoveryOutput
    object_format: GitObjectFormat


@dataclass(frozen=True, slots=True)
class _ResolvedTarget:
    current_branch: str | None
    target_commit: GitOid


def _discover(
    repository: Path,
    invoker: RepositoryInvoker,
) -> _Discovered | RepositoryRejected:
    operation = "discover_repository"
    result = invoker.run(
        repository,
        GitInvocation(
            operation,
            (
                "rev-parse",
                "--path-format=absolute",
                "--show-toplevel",
                "--git-common-dir",
                "--is-shallow-repository",
                "--show-object-format=storage",
            ),
        ),
    )
    if isinstance(result, RepositoryRejected):
        return result
    discovery = parse_discovery(result.stdout, repository)
    if discovery is None:
        return RepositoryRejected(RepositoryRejectionReason.MALFORMED_OUTPUT, operation)
    try:
        object_format = GitObjectFormat(discovery.object_format)
    except ValueError:
        return RepositoryRejected(
            RepositoryRejectionReason.UNSUPPORTED_OBJECT_FORMAT,
            operation,
        )
    if discovery.shallow:
        return RepositoryRejected(RepositoryRejectionReason.SHALLOW, operation)
    return _Discovered(discovery, object_format)


def _resolve_branch(
    request: RepositoryPreflightRequest,
    discovered: _Discovered,
    invoker: RepositoryInvoker,
) -> str | RepositoryRejected | None:
    operation = "resolve_branch"
    result = invoker.run(
        discovered.discovery.top_level,
        GitInvocation(
            operation,
            ("symbolic-ref", "--quiet", "--short", "HEAD"),
            (0, 1),
        ),
    )
    if isinstance(result, RepositoryRejected):
        return result
    if result.returncode == 1:
        if request.explicit_target is None:
            return RepositoryRejected(
                RepositoryRejectionReason.DETACHED_HEAD, operation
            )
        return None
    branch = parse_single_text(result.stdout)
    return branch or RepositoryRejected(
        RepositoryRejectionReason.MALFORMED_OUTPUT,
        operation,
    )


def _resolve_target(
    request: RepositoryPreflightRequest,
    discovered: _Discovered,
    invoker: RepositoryInvoker,
) -> _ResolvedTarget | RepositoryRejected:
    branch = _resolve_branch(request, discovered, invoker)
    if isinstance(branch, RepositoryRejected):
        return branch
    expression = request.explicit_target or "HEAD"
    result = invoker.run(
        discovered.discovery.top_level,
        GitInvocation(
            "resolve_target",
            (
                "rev-parse",
                "--verify",
                "--quiet",
                "--end-of-options",
                f"{expression}^{{commit}}",
            ),
            (0, 1, 128),
        ),
    )
    if isinstance(result, RepositoryRejected):
        return result
    text = parse_single_text(result.stdout) if result.returncode == 0 else None
    parsed_oid = parse_git_oid(text) if text is not None else None
    expected_length = 40 if discovered.object_format is GitObjectFormat.SHA1 else 64
    if (
        parsed_oid is None
        or isinstance(parsed_oid, InvalidOid)
        or len(parsed_oid) != expected_length
    ):
        return RepositoryRejected(
            RepositoryRejectionReason.INVALID_TARGET,
            "resolve_target",
        )
    return _ResolvedTarget(branch, parsed_oid)


def preflight_repository(
    request: RepositoryPreflightRequest,
    *,
    runner: CommandRunner = run_command,
) -> RepositoryPreflightResult:
    """Discover one clean full source repository without mutating it."""
    invoker = RepositoryInvoker(runner)
    discovered = _discover(request.repository, invoker)
    if isinstance(discovered, RepositoryRejected):
        return discovered
    operation_failure = check_operations(discovered.discovery.top_level, invoker)
    if operation_failure is not None:
        return operation_failure
    target = _resolve_target(request, discovered, invoker)
    if isinstance(target, RepositoryRejected):
        return target
    status_failure = check_status(discovered.discovery.top_level, invoker)
    if status_failure is not None:
        return status_failure
    source = resolve_source(discovered.discovery, invoker)
    if isinstance(source, RepositoryRejected):
        return source
    return RepositoryFacts(
        discovered.discovery.top_level,
        discovered.discovery.common_git_dir,
        target.current_branch,
        discovered.object_format,
        target.target_commit,
        source,
    )
