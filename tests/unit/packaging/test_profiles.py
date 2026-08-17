from __future__ import annotations

import errno
import os
import re
import stat
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Protocol

import pytest

from repo_offline_sync.packaging._private_directories import (
    PrivateDirectory,
    open_private_directory,
)
from repo_offline_sync.packaging._profile_storage import atomic_write_private
from repo_offline_sync.packaging._storage_errors import StorageFormatError
from repo_offline_sync.packaging.profiles import (
    PairingToken,
    ProfileFormatError,
    ProfileSettings,
    ProfileStore,
    RepoId,
    RepositoryLinkError,
    RepositorySource,
    XdgRoots,
    redact_tokens,
)


class _ProfileFilesLike(Protocol):
    repos: PrivateDirectory
    identities: PrivateDirectory


def _roots(tmp_path: Path) -> XdgRoots:
    return XdgRoots(
        config=tmp_path / "config",
        cache=tmp_path / "cache",
        state=tmp_path / "state",
    )


def _source(tmp_path: Path, name: str = "repo") -> RepositorySource:
    common_git_dir = tmp_path / name / ".git"
    common_git_dir.mkdir(parents=True)
    return RepositorySource.create(common_git_dir, "ssh://git.example/project.git")


def _settings(destination: str = "/home/updater/app") -> ProfileSettings:
    return ProfileSettings(
        destination=destination,
        service_user="updater",
        danger_enabled=False,
    )


def _initialize_worker(root: str, common_git_dir: str) -> str:
    base = Path(root)
    store = ProfileStore(XdgRoots(base / "config", base / "cache", base / "state"))
    source = RepositorySource.create(
        Path(common_git_dir), "ssh://git.example/project.git"
    )
    return store.initialize(source, _settings()).repo_id.value


def test_initialize_reuses_stable_profile_and_secure_mode(tmp_path: Path) -> None:
    # Given an isolated XDG store and one canonical repository identity
    store = ProfileStore(_roots(tmp_path))
    source = _source(tmp_path)

    # When initialization is repeated with different proposed settings
    first = store.initialize(source, _settings())
    second = store.initialize(source, _settings("/home/updater/ignored"))

    # Then stable values are reused and the private profile is owner-only
    assert second.repo_id == first.repo_id
    assert second.settings == first.settings
    assert second.token.matches(first.token)
    document = store.profile_path(first.repo_id).read_text(encoding="utf-8")
    token_match = re.search(r'"target_token":"([0-9a-f]{32})"', document)
    assert token_match is not None
    assert stat.S_IMODE(store.profile_path(first.repo_id).stat().st_mode) == 0o600
    assert store.roots.cache.is_dir()
    assert store.roots.state.is_dir()


def test_moved_clone_requires_explicit_link(tmp_path: Path) -> None:
    # Given one initialized repository and a moved clone with the same remote
    store = ProfileStore(_roots(tmp_path))
    original = store.initialize(_source(tmp_path, "original"), _settings())
    moved = _source(tmp_path, "moved")

    # When the moved clone initializes normally and is then explicitly linked
    separate = store.initialize(moved, _settings())
    linked = store.link(moved, original.repo_id)

    # Then it was never silently conflated but explicit linking selects the profile
    assert separate.repo_id != original.repo_id
    assert linked.repo_id == original.repo_id
    reused = store.reuse(moved)
    assert reused is not None
    assert reused.repo_id == original.repo_id
    assert reused.token.matches(original.token)


def test_link_rejects_different_provenance_remote(tmp_path: Path) -> None:
    # Given an existing profile and a source from a different provenance remote
    store = ProfileStore(_roots(tmp_path))
    profile = store.initialize(_source(tmp_path), _settings())
    other_git_dir = tmp_path / "other" / ".git"
    other_git_dir.mkdir(parents=True)
    other = RepositorySource.create(other_git_dir, "ssh://git.example/other.git")

    # When explicit linking is attempted, then provenance mismatch is typed
    with pytest.raises(RepositoryLinkError):
        _ = store.link(other, profile.repo_id)


def test_edit_reset_and_rotation_preserve_repository_identity(tmp_path: Path) -> None:
    # Given an initialized reusable profile
    store = ProfileStore(_roots(tmp_path))
    profile = store.initialize(_source(tmp_path), _settings())
    old_token = profile.token

    # When settings are edited, reset, and the token is rotated manually
    edited = store.edit(
        profile.repo_id,
        ProfileSettings(
            destination="/opt/example",
            service_user="updater",
            danger_enabled=True,
        ),
    )
    reset = store.reset(profile.repo_id, _settings())
    manual = PairingToken.parse("0123456789abcdef0123456789abcdef")
    rotated = store.rotate_token(
        profile.repo_id,
        manual,
    )

    # Then identity is stable, edits are replaced, and rotation is explicit
    assert edited.repo_id == reset.repo_id == rotated.repo_id == profile.repo_id
    assert reset.settings == _settings()
    assert rotated.token.matches(manual)
    assert not rotated.token.matches(old_token)


@pytest.mark.parametrize(
    "raw_token",
    ["", "abc", "A" * 32, "g" * 32, "0" * 31, "0" * 33],
)
def test_manual_token_rejects_malformed_values(raw_token: str) -> None:
    # Given malformed manual pairing input
    # When it is parsed, then no token value can be constructed
    with pytest.raises(ProfileFormatError):
        _ = PairingToken.parse(raw_token)


def test_token_is_redacted_from_string_repr_and_arbitrary_text() -> None:
    # Given a valid plaintext pairing token embedded in output text
    raw = "0123456789abcdef0123456789abcdef"
    token = PairingToken.parse(raw)

    # When public rendering and universal redaction are used
    rendered = (str(token), repr(token), redact_tokens(f"token={raw}", (token,)))

    # Then the plaintext never appears
    assert all(raw not in value for value in rendered)
    assert rendered[2] == "token=<redacted>"


def test_pairing_token_public_api_cannot_return_plaintext() -> None:
    # Given a valid pairing token exposed to ordinary application code
    raw = "0123456789abcdef0123456789abcdef"
    token = PairingToken.parse(raw)

    # When its public surface is inspected
    public_names = frozenset(name for name in dir(token) if not name.startswith("_"))

    # Then no public storage escape exists or returns the plaintext
    assert not hasattr(token, "for_private_storage")
    assert public_names <= {"generate", "matches", "parse", "redact"}
    assert token.redact(raw) == "<redacted>"


def test_profile_parser_rejects_unknown_duplicate_and_malformed_fields(
    tmp_path: Path,
) -> None:
    # Given a valid profile path replaced by malformed private storage documents
    store = ProfileStore(_roots(tmp_path))
    profile = store.initialize(_source(tmp_path), _settings())
    path = store.profile_path(profile.repo_id)
    malformed_documents = (
        path.read_text(encoding="utf-8").replace("{", '{"unknown":1,', 1),
        path.read_text(encoding="utf-8").replace(
            '"repo_id":', '"repo_id":"duplicate","repo_id":', 1
        ),
        '{"repo_id":',
    )

    # When each malformed document crosses the storage boundary
    for document in malformed_documents:
        _ = path.write_text(document, encoding="utf-8")

        # Then parsing fails with a typed error and no raw mapping escapes
        with pytest.raises(ProfileFormatError):
            _ = store.load(profile.repo_id)


def test_xdg_environment_defaults_are_predictable(tmp_path: Path) -> None:
    # Given explicit XDG environment roots
    environment = {
        "XDG_CONFIG_HOME": str(tmp_path / "c"),
        "XDG_CACHE_HOME": str(tmp_path / "k"),
        "XDG_STATE_HOME": str(tmp_path / "s"),
        "HOME": str(tmp_path / "home"),
    }

    # When roots are resolved, then every explicit location is honored
    assert XdgRoots.from_environment(environment) == XdgRoots(
        tmp_path / "c" / "repo-offline-sync",
        tmp_path / "k" / "repo-offline-sync",
        tmp_path / "s" / "repo-offline-sync",
    )


def test_profile_store_rejects_symlink_ancestor_before_creating_child(
    tmp_path: Path,
) -> None:
    # Given an XDG application root whose ancestor redirects outside it
    outside = tmp_path / "outside"
    outside.mkdir()
    xdg_parent = tmp_path / "xdg"
    xdg_parent.mkdir()
    symlink = xdg_parent / "redirect"
    symlink.symlink_to(outside, target_is_directory=True)
    requested_child = outside / "requested-child"
    roots = XdgRoots(
        symlink / requested_child.name,
        tmp_path / "cache",
        tmp_path / "state",
    )

    # When profile storage prepares its private XDG directories
    with pytest.raises(StorageFormatError):
        _ = ProfileStore(roots)

    # Then rejection happens before anything is created through the symlink
    assert not requested_child.exists()


def test_profile_store_rejects_relative_and_non_directory_xdg_components(
    tmp_path: Path,
) -> None:
    # Given one relative root and one absolute root beneath a regular file
    non_directory = tmp_path / "not-a-directory"
    _ = non_directory.write_text("content", encoding="utf-8")
    invalid_roots = (
        XdgRoots(Path("relative"), tmp_path / "cache-a", tmp_path / "state-a"),
        XdgRoots(
            non_directory / "child",
            tmp_path / "cache-b",
            tmp_path / "state-b",
        ),
    )

    # When each root is prepared, then it produces the same typed storage boundary
    for roots in invalid_roots:
        with pytest.raises(StorageFormatError):
            _ = ProfileStore(roots)

    # Then the regular component was not replaced or traversed
    assert non_directory.read_text(encoding="utf-8") == "content"
    assert not (non_directory / "child").exists()


def test_profile_edit_rejects_repos_swap_without_writing_outside(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a loaded profile whose repos directory is swapped before publication
    roots = _roots(tmp_path)
    store = ProfileStore(roots)
    profile = store.initialize(_source(tmp_path), _settings())
    repos = roots.config / "repos"
    held = roots.config / "repos-held"
    outside = tmp_path / "outside"
    outside.mkdir()
    real_open = os.open
    swapped = False

    def swap_before_temp_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        exclusive_create = flags & os.O_CREAT and flags & os.O_EXCL
        if exclusive_create and not swapped:
            _ = repos.rename(held)
            repos.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_before_temp_open)

    # When edit publishes after the active pathname identity changed
    with pytest.raises(StorageFormatError):
        _ = store.edit(profile.repo_id, _settings("/home/updater/changed"))

    # Then no profile or temporary file is written through the outside symlink
    assert not tuple(outside.iterdir())


def test_initialize_rolls_back_profile_when_identity_publication_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a fresh store whose second publication will fail
    roots = _roots(tmp_path)
    store = ProfileStore(roots)
    source = _source(tmp_path)

    def fail_identity(
        _files: _ProfileFilesLike,
        _source: RepositorySource,
        _repo_id: RepoId,
    ) -> None:
        raise OSError(errno.EIO, "injected identity publication failure")

    monkeypatch.setattr(store, "_write_identity", fail_identity)

    # When initialization publishes the profile but cannot publish its index
    with pytest.raises(OSError, match="injected identity publication failure"):
        _ = store.initialize(source, _settings())

    # Then rollback leaves neither a discoverable index nor an orphan profile
    assert not tuple((roots.config / "repos").iterdir())
    assert not tuple((roots.config / "identities").iterdir())


def test_reuse_repairs_profile_missing_identity_index(tmp_path: Path) -> None:
    # Given a crash-style durable profile whose identity index is missing
    roots = _roots(tmp_path)
    store = ProfileStore(roots)
    source = _source(tmp_path)
    profile = store.initialize(source, _settings())
    identity_path = roots.config / "identities" / f"{source.identity_key()}.json"
    identity_path.unlink()

    # When reuse reconciles storage under the profile lock
    recovered = store.reuse(source)

    # Then it repairs the index and returns the original complete profile
    assert recovered is not None
    assert recovered.repo_id == profile.repo_id
    assert recovered.settings == profile.settings
    assert recovered.token.matches(profile.token)
    assert identity_path.is_file()


@pytest.mark.parametrize("identity_present", [True, False])
def test_recovery_rejects_multiple_profiles_for_same_source(
    tmp_path: Path,
    *,
    identity_present: bool,
) -> None:
    # Given indexed or interrupted storage with two profiles for one exact source
    roots = _roots(tmp_path)
    store = ProfileStore(roots)
    source = _source(tmp_path)
    profile = store.initialize(source, _settings())
    identity_path = roots.config / "identities" / f"{source.identity_key()}.json"
    if not identity_present:
        identity_path.unlink()
    original_path = store.profile_path(profile.repo_id)
    duplicate_id = RepoId.generate()
    duplicate = original_path.read_text(encoding="utf-8").replace(
        profile.repo_id.value,
        duplicate_id.value,
    )
    duplicate_path = store.profile_path(duplicate_id)
    _ = duplicate_path.write_text(duplicate, encoding="utf-8")
    duplicate_path.chmod(0o600)

    # When recovery scans the incomplete initialization state
    # Then it rejects ambiguity instead of choosing a profile
    with pytest.raises(ProfileFormatError):
        _ = store.reuse(source)


def test_atomic_profile_never_exposes_partial_json(tmp_path: Path) -> None:
    # Given repeated profile replacement under one repository lock
    store = ProfileStore(_roots(tmp_path))
    profile = store.initialize(_source(tmp_path), _settings())

    # When many edits atomically replace the same profile
    for index in range(30):
        _ = store.edit(
            profile.repo_id,
            ProfileSettings(
                destination=f"/home/updater/app-{index}",
                service_user="updater",
                danger_enabled=False,
            ),
        )
        document = store.profile_path(profile.repo_id).read_text(encoding="utf-8")
        assert f'"destination":"/home/updater/app-{index}"' in document

    # Then no temporary writer artifact or stale lock payload is exposed
    assert not tuple(store.profile_path(profile.repo_id).parent.glob("*.tmp"))
    assert os.access(store.profile_path(profile.repo_id), os.R_OK)


def test_atomic_write_closes_raw_descriptor_when_setup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a real owned temporary file and an unrelated temp-like neighbor
    destination = tmp_path / "profile.json"
    unrelated = tmp_path / ".profile.json.unrelated.tmp"
    _ = unrelated.write_text("keep", encoding="utf-8")
    initial_inventory = frozenset(tmp_path.iterdir())
    created: list[tuple[int, Path]] = []
    close_calls: list[int] = []
    real_fchmod = os.fchmod
    real_close = os.close

    def fail_owned_fchmod(descriptor: int, mode: int) -> None:
        descriptor_path = Path(f"/proc/self/fd/{descriptor}")
        if descriptor_path.exists() and descriptor_path.resolve().parent == tmp_path:
            created.append((descriptor, descriptor_path.resolve()))
            raise OSError(errno.EIO, "injected post-mkstemp setup failure")
        real_fchmod(descriptor, mode)

    def track_owned_close(descriptor: int) -> None:
        if created and descriptor == created[0][0]:
            close_calls.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(os, "fchmod", fail_owned_fchmod)
    monkeypatch.setattr(os, "close", track_owned_close)

    # When setup fails after mkstemp but before fdopen takes ownership
    with (
        pytest.raises(OSError, match="injected post-mkstemp setup failure"),
        open_private_directory(tmp_path) as directory,
    ):
        atomic_write_private(directory, destination.name, "content\n")

    # Then the exact descriptor is closed once and only its owned path is removed
    descriptor, owned_path = created[0]
    with pytest.raises(OSError, match="Bad file descriptor") as closed:
        _ = os.fstat(descriptor)
    assert closed.value.errno == errno.EBADF
    assert close_calls == [descriptor]
    assert not owned_path.exists()
    assert frozenset(tmp_path.iterdir()) == initial_inventory
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_atomic_write_exposes_cleanup_failure_after_closing_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given setup and exact owned-path cleanup failures after a real mkstemp
    destination = tmp_path / "profile.json"
    created: list[tuple[int, Path]] = []
    real_fchmod = os.fchmod
    real_unlink = os.unlink

    def fail_owned_fchmod(descriptor: int, mode: int) -> None:
        descriptor_path = Path(f"/proc/self/fd/{descriptor}")
        if descriptor_path.exists() and descriptor_path.resolve().parent == tmp_path:
            created.append((descriptor, descriptor_path.resolve()))
            raise OSError(errno.EIO, "injected setup failure")
        real_fchmod(descriptor, mode)

    def fail_owned_unlink(
        path: str,
        *,
        dir_fd: int | None = None,
    ) -> None:
        if created and path == created[0][1].name:
            raise OSError(errno.EIO, "injected cleanup failure")
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "fchmod", fail_owned_fchmod)
    monkeypatch.setattr(os, "unlink", fail_owned_unlink)

    # When cleanup cannot remove the exact owned temporary path
    with (
        pytest.raises(OSError, match="injected cleanup failure") as cleanup,
        open_private_directory(tmp_path) as directory,
    ):
        atomic_write_private(directory, destination.name, "content\n")

    # Then cleanup failure is explicit, setup remains contextual, and fd is closed
    descriptor, owned_path = created[0]
    assert cleanup.value.__context__ is not None
    assert "injected setup failure" in str(cleanup.value.__context__)
    with pytest.raises(OSError, match="Bad file descriptor") as closed:
        _ = os.fstat(descriptor)
    assert closed.value.errno == errno.EBADF
    assert owned_path.exists()
    real_unlink(owned_path)


def test_concurrent_initializers_share_one_complete_profile(tmp_path: Path) -> None:
    # Given one repository identity and multiple independent profile writers
    source = _source(tmp_path)
    roots = tmp_path / "xdg"

    # When independent processes initialize it concurrently
    with ProcessPoolExecutor(max_workers=4) as executor:
        repo_ids = tuple(
            executor.map(
                _initialize_worker,
                (str(roots),) * 8,
                (str(source.common_git_dir),) * 8,
            )
        )

    # Then locking serialized one stable, complete identity
    assert len(frozenset(repo_ids)) == 1
    store = ProfileStore(XdgRoots(roots / "config", roots / "cache", roots / "state"))
    profile = store.load(RepoId.parse(repo_ids[0]))
    assert profile.source == source


def test_repo_id_parser_rejects_non_uuid_storage_name() -> None:
    # Given a non-UUID repository identifier
    # When parsed, then traversal-like profile names are rejected
    with pytest.raises(ProfileFormatError):
        _ = RepoId.parse("../profile")
