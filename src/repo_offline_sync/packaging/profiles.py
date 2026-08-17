"""Reusable repository profile lifecycle backed by private XDG storage."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final, final

if TYPE_CHECKING:
    from collections.abc import Generator

from repo_offline_sync.packaging._private_directories import (
    PrivateDirectory,
    ensure_private_directory,
    open_private_directory,
)
from repo_offline_sync.packaging._profile_codec import encode_identity, encode_profile
from repo_offline_sync.packaging._profile_json import (
    decode_object,
    require_bool,
    require_string,
)
from repo_offline_sync.packaging._profile_models import (
    PairingToken,
    ProfileFailure,
    ProfileFormatError,
    ProfileSettings,
    RepoId,
    RepoProfile,
    RepositoryLinkError,
    RepositorySource,
    XdgRoots,
    redact_tokens,
)
from repo_offline_sync.packaging._profile_storage import (
    atomic_write_private,
    exclusive_lock,
    list_private,
    private_exists,
    read_private,
    unlink_private,
)
from repo_offline_sync.packaging._storage_errors import StorageFormatError

__all__ = [
    "PairingToken",
    "ProfileFormatError",
    "ProfileSettings",
    "ProfileStore",
    "RepoId",
    "RepoProfile",
    "RepositoryLinkError",
    "RepositorySource",
    "XdgRoots",
    "redact_tokens",
]

_PROFILE_FIELDS: Final = frozenset(
    {
        "repo_id",
        "common_git_dir",
        "provenance_remote",
        "destination",
        "service_user",
        "danger_enabled",
        "target_token",
    }
)
_IDENTITY_FIELDS: Final = frozenset({"repo_id", "common_git_dir", "provenance_remote"})


@dataclass(frozen=True, slots=True)
class _ProfileFiles:
    repos: PrivateDirectory
    identities: PrivateDirectory


@final
class ProfileStore:
    """Serialize complete profile lifecycle operations beneath one process lock."""

    def __init__(self, roots: XdgRoots) -> None:
        """Prepare private application directories beneath injected XDG roots."""
        self.roots = roots
        for root in (roots.config, roots.cache, roots.state):
            ensure_private_directory(root)
        self._repos = roots.config / "repos"
        self._identities = roots.config / "identities"
        ensure_private_directory(self._repos)
        ensure_private_directory(self._identities)
        self._state = roots.state

    def profile_path(self, repo_id: RepoId) -> Path:
        """Return the private JSON path for a typed repository identifier."""
        return self._repos / f"{repo_id.value}.json"

    def initialize(
        self,
        source: RepositorySource,
        settings: ProfileSettings,
        manual_token: PairingToken | None = None,
    ) -> RepoProfile:
        """Create once or reuse stable fields for an exact clone identity."""
        with self._locked_files() as files:
            existing = self._resolve(files, source)
            if existing is not None:
                return existing
            token = manual_token or PairingToken.generate()
            profile = RepoProfile(RepoId.generate(), source, settings, token)
            self._write_profile(files, profile)
            try:
                self._write_identity(files, source, profile.repo_id)
            except (OSError, StorageFormatError):
                unlink_private(
                    files.repos,
                    self.profile_path(profile.repo_id).name,
                    missing_ok=True,
                )
                raise
            return profile

    def reuse(self, source: RepositorySource) -> RepoProfile | None:
        """Load a profile for an exact clone identity when one exists."""
        with self._locked_files() as files:
            return self._resolve(files, source)

    def load(self, repo_id: RepoId) -> RepoProfile:
        """Load one typed profile under the lifecycle lock."""
        with self._locked_files() as files:
            return self._load(files, repo_id)

    def edit(self, repo_id: RepoId, settings: ProfileSettings) -> RepoProfile:
        """Atomically replace editable settings while preserving stable identity."""
        with self._locked_files() as files:
            updated = replace(self._load(files, repo_id), settings=settings)
            self._write_profile(files, updated)
            return updated

    def reset(self, repo_id: RepoId, defaults: ProfileSettings) -> RepoProfile:
        """Atomically reset editable settings while preserving identity and token."""
        return self.edit(repo_id, defaults)

    def rotate_token(
        self,
        repo_id: RepoId,
        manual_token: PairingToken | None = None,
    ) -> RepoProfile:
        """Explicitly rotate to a generated or manually supplied mismatch token."""
        with self._locked_files() as files:
            token = manual_token or PairingToken.generate()
            updated = replace(self._load(files, repo_id), token=token)
            self._write_profile(files, updated)
            return updated

    def link(self, source: RepositorySource, repo_id: RepoId) -> RepoProfile:
        """Explicitly associate a moved clone having the same provenance remote."""
        with self._locked_files() as files:
            profile = self._load(files, repo_id)
            if source.provenance_remote != profile.source.provenance_remote:
                raise RepositoryLinkError
            self._write_identity(files, source, repo_id)
            return profile

    @contextmanager
    def _locked_files(self) -> Generator[_ProfileFiles, None, None]:
        with (
            open_private_directory(self._state) as state,
            exclusive_lock(state, "profiles.lock"),
            open_private_directory(self._repos) as repos,
            open_private_directory(self._identities) as identities,
        ):
            files = _ProfileFiles(repos, identities)
            try:
                yield files
            finally:
                try:
                    repos.recheck()
                finally:
                    identities.recheck()

    def _identity_repo_id(
        self,
        files: _ProfileFiles,
        source: RepositorySource,
    ) -> RepoId | None:
        name = f"{source.identity_key()}.json"
        if not private_exists(files.identities, name):
            return None
        document = decode_object(read_private(files.identities, name))
        document.require_exact_fields(_IDENTITY_FIELDS)
        stored_source = RepositorySource.create(
            Path(require_string(document, "common_git_dir")),
            require_string(document, "provenance_remote"),
        )
        if stored_source != source:
            raise ProfileFormatError(ProfileFailure.STORAGE_IDENTITY)
        return RepoId.parse(require_string(document, "repo_id"))

    def _load(self, files: _ProfileFiles, repo_id: RepoId) -> RepoProfile:
        try:
            document = decode_object(
                read_private(files.repos, self.profile_path(repo_id).name)
            )
            document.require_exact_fields(_PROFILE_FIELDS)
            stored_id = RepoId.parse(require_string(document, "repo_id"))
            if stored_id != repo_id:
                raise ProfileFormatError(ProfileFailure.STORAGE_ID)
            source = RepositorySource.create(
                Path(require_string(document, "common_git_dir")),
                require_string(document, "provenance_remote"),
            )
            settings = ProfileSettings(
                require_string(document, "destination"),
                require_string(document, "service_user"),
                require_bool(document, "danger_enabled"),
            )
            token = PairingToken.parse(require_string(document, "target_token"))
        except StorageFormatError as error:
            detail = error.reason.value
            raise ProfileFormatError(ProfileFailure.STORAGE, detail) from error
        return RepoProfile(stored_id, source, settings, token)

    def _matching_profiles(
        self,
        files: _ProfileFiles,
        source: RepositorySource,
    ) -> tuple[RepoProfile, ...]:
        matches: list[RepoProfile] = []
        for name in list_private(files.repos):
            if not name.endswith(".json"):
                continue
            profile = self._load(files, RepoId.parse(name.removesuffix(".json")))
            if profile.source == source:
                matches.append(profile)
        return tuple(matches)

    def _resolve(
        self,
        files: _ProfileFiles,
        source: RepositorySource,
    ) -> RepoProfile | None:
        mapped_id = self._identity_repo_id(files, source)
        if mapped_id is not None:
            mapped = self._load(files, mapped_id)
            if mapped.source != source:
                return mapped
            matches = self._matching_profiles(files, source)
            if len(matches) != 1 or matches[0].repo_id != mapped_id:
                raise ProfileFormatError(ProfileFailure.STORAGE_CONFLICT)
            return mapped
        matches = self._matching_profiles(files, source)
        if not matches:
            return None
        if len(matches) != 1:
            raise ProfileFormatError(ProfileFailure.STORAGE_CONFLICT)
        recovered = matches[0]
        self._write_identity(files, source, recovered.repo_id)
        return recovered

    def _write_profile(self, files: _ProfileFiles, profile: RepoProfile) -> None:
        name = self.profile_path(profile.repo_id).name
        atomic_write_private(files.repos, name, encode_profile(profile))

    def _write_identity(
        self,
        files: _ProfileFiles,
        source: RepositorySource,
        repo_id: RepoId,
    ) -> None:
        atomic_write_private(
            files.identities,
            f"{source.identity_key()}.json",
            encode_identity(source, repo_id),
        )
