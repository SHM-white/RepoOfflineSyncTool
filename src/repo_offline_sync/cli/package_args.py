"""Pure packaging repository argument resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class RepositoryArgument:
    """One repository path resolved from caller context."""

    path: Path


@dataclass(frozen=True, slots=True)
class RepositoryArgumentUsageError:
    """More than one repository argument was supplied."""

    argument_count: int
    exit_code: ClassVar[Literal[2]] = 2


RepositoryArgumentResult: TypeAlias = RepositoryArgument | RepositoryArgumentUsageError


def resolve_repository_argument(
    arguments: Sequence[str],
    caller_cwd: Path,
) -> RepositoryArgumentResult:
    """Resolve zero or one path without consulting process working directory."""
    if len(arguments) > 1:
        return RepositoryArgumentUsageError(argument_count=len(arguments))
    candidate = caller_cwd if not arguments else Path(arguments[0])
    if not candidate.is_absolute():
        candidate = caller_cwd / candidate
    return RepositoryArgument(Path(os.path.normpath(candidate)))
