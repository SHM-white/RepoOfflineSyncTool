"""Translation from command outcomes to public repository rejections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from repo_offline_sync.packaging._repository_commands import (
    CommandRunner,
    GitFailure,
    GitFailureReason,
    GitInvocation,
    GitOutput,
    execute_git,
)
from repo_offline_sync.packaging._repository_models import (
    RepositoryRejected,
    RepositoryRejectionReason,
)

if TYPE_CHECKING:
    from pathlib import Path


_FAILURE_REASONS = {
    GitFailureReason.CANCELLED: RepositoryRejectionReason.COMMAND_CANCELLED,
    GitFailureReason.FAILED: RepositoryRejectionReason.COMMAND_FAILED,
    GitFailureReason.INTERRUPTED: RepositoryRejectionReason.COMMAND_INTERRUPTED,
    GitFailureReason.INVALID: RepositoryRejectionReason.COMMAND_INVALID,
    GitFailureReason.NOT_REPOSITORY: RepositoryRejectionReason.NOT_REPOSITORY,
    GitFailureReason.OUTPUT_OVERFLOW: RepositoryRejectionReason.COMMAND_OUTPUT_OVERFLOW,
    GitFailureReason.START_FAILED: RepositoryRejectionReason.COMMAND_START_FAILED,
    GitFailureReason.TEARDOWN_FAILED: RepositoryRejectionReason.COMMAND_TEARDOWN_FAILED,
    GitFailureReason.TIMEOUT: RepositoryRejectionReason.COMMAND_TIMEOUT,
    GitFailureReason.UNSAFE_OWNERSHIP: RepositoryRejectionReason.UNSAFE_OWNERSHIP,
}


@dataclass(frozen=True, slots=True)
class RepositoryInvoker:
    """Run Git and erase raw bytes from every rejected outcome."""

    runner: CommandRunner

    def run(
        self,
        repository: Path,
        invocation: GitInvocation,
    ) -> GitOutput | RepositoryRejected:
        """Translate one bounded Git command to output or source rejection."""
        result = execute_git(repository, invocation, self.runner)
        match result:
            case GitOutput():
                return result
            case GitFailure():
                remediation = None
                if result.reason is GitFailureReason.UNSAFE_OWNERSHIP:
                    remediation = (
                        "Verify repository ownership, then explicitly trust only this "
                        "path with git config --global --add safe.directory "
                        f"{repository.absolute()}"
                    )
                return RepositoryRejected(
                    _FAILURE_REASONS[result.reason],
                    result.operation,
                    remediation,
                )
