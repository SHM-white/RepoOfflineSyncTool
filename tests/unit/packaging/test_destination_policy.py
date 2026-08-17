from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from repo_offline_sync._typing import override
from repo_offline_sync.packaging.destination_policy import (
    Acknowledgement,
    DestinationPolicy,
    DestinationRejectedError,
    ParentIdentityChangedError,
    acknowledge_destination,
    inspect_destination,
    recheck_parent_identity,
)
from repo_offline_sync.packaging.profiles import PairingToken
from repo_offline_sync.target.identity import (
    TargetIdentityMismatchError,
    verify_target_identity,
)


class _Input(io.StringIO):
    _tty: bool

    def __init__(self, value: str, *, tty: bool) -> None:
        super().__init__(value)
        self._tty = tty

    @override
    def isatty(self) -> bool:
        return self._tty


class _Output(io.StringIO):
    @override
    def isatty(self) -> bool:
        return True


def _policy(tmp_path: Path) -> DestinationPolicy:
    return DestinationPolicy(
        service_user="updater",
        updater_roots=(tmp_path / "updater-owned",),
        pseudo_roots=(Path("/proc"), Path("/sys"), Path("/dev")),
        mount_roots=(tmp_path / "mount",),
    )


def test_safe_home_destination_needs_no_danger_acknowledgement(tmp_path: Path) -> None:
    # Given a manageable destination under the service user's home
    destination = tmp_path / "home" / "updater" / "app"
    destination.parent.mkdir(parents=True)
    policy = _policy(tmp_path).with_service_home(tmp_path / "home" / "updater")

    # When it is inspected and acknowledged without danger mode
    assessment = inspect_destination(str(destination), policy)
    approved = acknowledge_destination(
        assessment,
        Acknowledgement(
            danger_enabled=False,
            input_stream=_Input("", tty=False),
            output_stream=_Output(),
        ),
    )

    # Then it is structurally approved without claiming dangerous confirmation
    assert not assessment.high_risk
    assert not approved.dangerous_confirmed


@pytest.mark.parametrize("answer", ["Yes\n", "YES\n", "yes \n", "\n"])
def test_high_risk_requires_exact_lowercase_yes(tmp_path: Path, answer: str) -> None:
    # Given a high-risk destination and an interactive but inexact answer
    destination = tmp_path / "opt" / "example"
    destination.parent.mkdir(parents=True)
    assessment = inspect_destination(str(destination), _policy(tmp_path))

    # When acknowledgement is attempted, then package authorization is rejected
    with pytest.raises(DestinationRejectedError):
        _ = acknowledge_destination(
            assessment,
            Acknowledgement(
                danger_enabled=True,
                input_stream=_Input(answer, tty=True),
                output_stream=_Output(),
            ),
        )


def test_high_risk_rejects_non_tty_even_with_exact_yes(tmp_path: Path) -> None:
    # Given high-risk destination input piped as exact lowercase yes
    destination = tmp_path / "opt" / "example"
    destination.parent.mkdir(parents=True)
    assessment = inspect_destination(str(destination), _policy(tmp_path))

    # When danger acknowledgement has no real TTY, then it is rejected
    with pytest.raises(DestinationRejectedError):
        _ = acknowledge_destination(
            assessment,
            Acknowledgement(
                danger_enabled=True,
                input_stream=_Input("yes\n", tty=False),
                output_stream=_Output(),
            ),
        )


def test_high_risk_exact_tty_yes_records_confirmation(tmp_path: Path) -> None:
    # Given danger enabled for a high-risk destination and a real TTY response
    destination = tmp_path / "opt" / "example"
    destination.parent.mkdir(parents=True)
    assessment = inspect_destination(str(destination), _policy(tmp_path))

    # When exact lowercase yes is entered
    approved = acknowledge_destination(
        assessment,
        Acknowledgement(
            danger_enabled=True,
            input_stream=_Input("yes\n", tty=True),
            output_stream=_Output(),
        ),
    )

    # Then package-time confirmation is recorded
    assert approved.dangerous_confirmed


def test_sensitive_home_directories_are_high_risk(tmp_path: Path) -> None:
    # Given destinations beneath sensitive service-home directories
    service_home = tmp_path / "home" / "updater"
    destinations = (service_home / ".ssh" / "keys", service_home / ".gnupg" / "data")
    for destination in destinations:
        destination.parent.mkdir(parents=True)
    policy = _policy(tmp_path).with_service_home(service_home)

    # When classified, then both require high-risk handling
    assert all(
        inspect_destination(str(path), policy).high_risk for path in destinations
    )


@pytest.mark.parametrize("raw", ["relative/path", "/", "/proc", "/proc/self"])
def test_structural_rejections_ignore_danger_mode(tmp_path: Path, raw: str) -> None:
    # Given an intrinsically unmanageable destination
    # When structurally inspected, then danger mode cannot make it valid
    with pytest.raises(DestinationRejectedError):
        _ = inspect_destination(raw, _policy(tmp_path))


def test_rejects_nul_mount_updater_owned_and_nonleaf_paths(tmp_path: Path) -> None:
    # Given structurally forbidden destination classes
    mount = tmp_path / "mount"
    updater = tmp_path / "updater-owned"
    mount.mkdir()
    updater.mkdir()
    regular_file = tmp_path / "file"
    _ = regular_file.write_text("content", encoding="utf-8")
    nul_path = str(tmp_path / "nul") + "\x00b"
    candidates = (
        nul_path,
        str(mount),
        str(updater / "data"),
        str(regular_file / "leaf"),
    )

    # When inspected, then each class is rejected before risk acknowledgement
    for candidate in candidates:
        with pytest.raises(DestinationRejectedError):
            _ = inspect_destination(candidate, _policy(tmp_path))


def test_symlink_parent_and_parent_swap_are_rejected(tmp_path: Path) -> None:
    # Given one symlinked parent and one initially stable real parent
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(DestinationRejectedError):
        _ = inspect_destination(str(link / "app"), _policy(tmp_path))
    stable = tmp_path / "stable"
    stable.mkdir()
    assessment = inspect_destination(str(stable / "app"), _policy(tmp_path))

    # When the captured parent is swapped after inspection
    _ = stable.rename(tmp_path / "old")
    stable.mkdir()

    # Then device/inode rechecking rejects the stale decision
    with pytest.raises(ParentIdentityChangedError):
        recheck_parent_identity(assessment)


def test_target_identity_only_checks_token_and_structure(
    tmp_path: Path,
) -> None:
    # Given a structurally captured high-risk destination and matching tokens
    destination = tmp_path / "opt" / "example"
    destination.parent.mkdir(parents=True)
    assessment = inspect_destination(str(destination), _policy(tmp_path))
    token = PairingToken.generate()

    # When target identity is verified without packaging risk configuration
    verify_target_identity(token, token, assessment)

    # Then a mismatch is rejected without authentication or risk claims
    with pytest.raises(TargetIdentityMismatchError):
        verify_target_identity(token, PairingToken.generate(), assessment)


def test_target_rechecks_parent_identity(tmp_path: Path) -> None:
    # Given a captured destination whose parent changes before target verification
    parent = tmp_path / "parent"
    parent.mkdir()
    assessment = inspect_destination(str(parent / "app"), _policy(tmp_path))
    token = PairingToken.generate()
    _ = parent.rename(tmp_path / "old-parent")
    parent.mkdir()

    # When target identity is checked, then structural staleness is rejected
    with pytest.raises(ParentIdentityChangedError):
        verify_target_identity(token, token, assessment)


def test_existing_destination_symlink_is_rejected(tmp_path: Path) -> None:
    # Given a destination leaf that is itself a symlink
    parent = tmp_path / "parent"
    parent.mkdir()
    target = parent / "real"
    target.mkdir()
    destination = parent / "app"
    destination.symlink_to(target, target_is_directory=True)

    # When inspected, then untrusted leaf following is rejected
    with pytest.raises(DestinationRejectedError):
        _ = inspect_destination(os.fspath(destination), _policy(tmp_path))
