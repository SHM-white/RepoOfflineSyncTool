"""Public immutable repository source-preflight API."""

from repo_offline_sync.packaging._repository_models import (
    GitObjectFormat,
    RepositoryFacts,
    RepositoryPreflightRequest,
    RepositoryPreflightResult,
    RepositoryRejected,
    RepositoryRejectionReason,
)
from repo_offline_sync.packaging._repository_preflight import preflight_repository

__all__ = (
    "GitObjectFormat",
    "RepositoryFacts",
    "RepositoryPreflightRequest",
    "RepositoryPreflightResult",
    "RepositoryRejected",
    "RepositoryRejectionReason",
    "preflight_repository",
)
