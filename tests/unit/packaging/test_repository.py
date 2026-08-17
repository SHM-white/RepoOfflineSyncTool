from __future__ import annotations

from pathlib import Path
from typing import final

import pytest

from repo_offline_sync.cli.package_args import (
    RepositoryArgument,
    RepositoryArgumentUsageError,
    resolve_repository_argument,
)
from repo_offline_sync.packaging.repository import (
    RepositoryPreflightRequest,
    RepositoryRejected,
    RepositoryRejectionReason,
    preflight_repository,
)
from repo_offline_sync.platform.commands import (
    CommandCompleted,
    CommandOutcome,
    CommandRequest,
    CommandTimedOut,
)


@final
class RecordingRunner:
    def __init__(self, outcomes: tuple[CommandOutcome, ...]) -> None:
        self._outcomes = iter(outcomes)
        self.requests: list[CommandRequest] = []

    def __call__(self, request: CommandRequest) -> CommandOutcome:
        self.requests.append(request)
        return next(self._outcomes)


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ((), Path("/caller/project")),
        (("nested/repo",), Path("/caller/project/nested/repo")),
        (("/other/repo",), Path("/other/repo")),
    ],
)
def test_repository_argument_resolves_from_explicit_caller_context(
    arguments: tuple[str, ...],
    expected: Path,
) -> None:
    # Given an explicit caller working directory
    caller_cwd = Path("/caller/project")

    # When zero or one repository argument is resolved
    result = resolve_repository_argument(arguments, caller_cwd)

    # Then the result is a normalized path rooted in caller context
    assert result == RepositoryArgument(expected)


def test_many_repository_arguments_return_usage_without_commands() -> None:
    # Given more than one repository argument and an unused command boundary
    runner = RecordingRunner(())

    # When arguments are resolved
    result = resolve_repository_argument(("first", "second"), Path("/caller"))

    # Then the stable usage class is returned before any repository command
    assert result == RepositoryArgumentUsageError(argument_count=2)
    assert isinstance(result, RepositoryArgumentUsageError)
    assert result.exit_code == 2
    assert runner.requests == []


def test_command_timeout_returns_typed_source_rejection(tmp_path: Path) -> None:
    # Given repository discovery whose first Git command times out
    runner = RecordingRunner((CommandTimedOut(b"", b""),))
    request = RepositoryPreflightRequest(repository=tmp_path)

    # When source preflight runs
    result = preflight_repository(request, runner=runner)

    # Then timeout is machine-consumable and every command input is explicit
    assert result == RepositoryRejected(
        reason=RepositoryRejectionReason.COMMAND_TIMEOUT,
        operation="discover_repository",
    )
    assert isinstance(result, RepositoryRejected)
    assert result.exit_code == 3
    assert len(runner.requests) == 1
    command = runner.requests[0]
    assert command.argv[0] == "/usr/bin/git"
    assert command.cwd == tmp_path
    assert command.timeout_seconds > 0
    assert command.output_limit_bytes > 0
    assert command.termination_grace_seconds > 0
    assert ("LC_ALL", "C") in command.environment


def test_malformed_discovery_output_returns_typed_rejection(tmp_path: Path) -> None:
    # Given successful Git execution with an incomplete discovery record
    runner = RecordingRunner((CommandCompleted(0, b"only-one-field\n", b""),))

    # When source preflight parses the output
    result = preflight_repository(
        RepositoryPreflightRequest(repository=tmp_path),
        runner=runner,
    )

    # Then malformed bytes do not escape the boundary
    assert result == RepositoryRejected(
        reason=RepositoryRejectionReason.MALFORMED_OUTPUT,
        operation="discover_repository",
    )


def test_dubious_ownership_failure_has_scoped_remediation(tmp_path: Path) -> None:
    # Given Git's deterministic dubious-ownership failure signature
    runner = RecordingRunner(
        (
            CommandCompleted(
                128,
                b"",
                b"fatal: detected dubious ownership in repository at '/fixture'\n",
            ),
        )
    )

    # When source preflight attempts discovery
    result = preflight_repository(
        RepositoryPreflightRequest(repository=tmp_path),
        runner=runner,
    )

    # Then ownership is distinct and remediation scopes one canonical path
    assert isinstance(result, RepositoryRejected)
    assert result.reason is RepositoryRejectionReason.UNSAFE_OWNERSHIP
    assert result.remediation is not None
    assert "safe.directory" in result.remediation
    assert "*" not in result.remediation


def test_nonzero_git_failure_returns_typed_rejection(tmp_path: Path) -> None:
    # Given a non-ownership Git failure
    runner = RecordingRunner((CommandCompleted(128, b"", b"fatal\n"),))

    # When source preflight runs
    result = preflight_repository(
        RepositoryPreflightRequest(repository=tmp_path),
        runner=runner,
    )

    # Then command failure is typed without exposing raw command bytes
    assert result == RepositoryRejected(
        reason=RepositoryRejectionReason.COMMAND_FAILED,
        operation="discover_repository",
    )
