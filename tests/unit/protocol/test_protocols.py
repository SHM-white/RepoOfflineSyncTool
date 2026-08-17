from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from repo_offline_sync.domain.identifiers import (
    Generation,
    PositiveBytes,
    PositiveSeconds,
    parse_generation,
    parse_git_oid,
    parse_lfs_oid,
    parse_media_id,
    parse_package_id,
    parse_positive_bytes,
    parse_positive_seconds,
    parse_repo_id,
    parse_segment_id,
    parse_target_id,
    parse_transaction_id,
)
from repo_offline_sync.domain.models import (
    Action,
    BundleSegment,
    LfsObject,
    Package,
    RepositoryNode,
    Result,
    SegmentRoute,
    Transaction,
)
from repo_offline_sync.domain.policies import (
    ActionPhase,
    BundleKind,
    FailurePolicy,
    Filesystem,
    ResultStatus,
    TransactionPhase,
)
from repo_offline_sync.packaging.profiles import (
    PairingToken,
    ProfileSettings,
    RepoId,
    RepoProfile,
    RepositorySource,
)
from repo_offline_sync.protocol.json_boundary import ProtocolError, ProtocolReason
from repo_offline_sync.protocol.manifest import (
    Artifact,
    Manifest,
    encode_manifest,
    parse_manifest,
)
from repo_offline_sync.protocol.media import (
    MediaMarker,
    ReadyArtifact,
    ReadyMarker,
    encode_media,
    encode_ready,
    parse_media,
    parse_ready,
)
from repo_offline_sync.protocol.profile import encode_profile, parse_profile
from repo_offline_sync.protocol.result import (
    ExitCode,
    encode_result,
    exit_code_for_status,
    parse_result,
)
from repo_offline_sync.protocol.state import StateDocument, encode_state, parse_state


def _domain_graph() -> tuple[Package, Transaction, Result]:
    package_id = parse_package_id("package-1")
    repo_id = parse_repo_id("repo-1")
    target_id = parse_target_id("target-1")
    transaction_id = parse_transaction_id("transaction-1")
    media_id = parse_media_id("media-1")
    segment_id = parse_segment_id("segment-1")
    generation = parse_generation(1)
    byte_size = parse_positive_bytes(1024)
    timeout = parse_positive_seconds(30)
    target_commit = parse_git_oid("a" * 40)
    lfs_oid = parse_lfs_oid("b" * 64)
    assert isinstance(package_id, str)
    assert isinstance(repo_id, str)
    assert isinstance(target_id, str)
    assert isinstance(transaction_id, str)
    assert isinstance(media_id, str)
    assert isinstance(segment_id, str)
    assert isinstance(generation, Generation)
    assert isinstance(byte_size, PositiveBytes)
    assert isinstance(timeout, PositiveSeconds)
    assert isinstance(target_commit, str)
    assert isinstance(lfs_oid, str)
    package = Package(
        package_id=package_id,
        target_id=target_id,
        media_id=media_id,
        generation=generation,
        target_commit=target_commit,
        repositories=(RepositoryNode(repo_id, None, ".", target_commit, None),),
        routes=(SegmentRoute(repo_id, (segment_id,), (), target_commit),),
        segments=(
            BundleSegment(
                segment_id,
                repo_id,
                BundleKind.FULL,
                generation,
                None,
                target_commit,
                byte_size,
                oversize=False,
            ),
        ),
        lfs_objects=(LfsObject(lfs_oid, byte_size, (repo_id,)),),
        actions=(Action("build", ActionPhase.BUILD, ("make",), timeout),),
        failure_policy=FailurePolicy.ROLLBACK,
        filesystem=Filesystem.EXT4,
        full_fallback_included=True,
    )
    transaction = Transaction(
        transaction_id, package_id, target_id, TransactionPhase.DISCOVERED
    )
    result = Result(transaction_id, package_id, target_id, ResultStatus.SUCCESS)
    return package, transaction, result


def test_manifest_canonical_round_trip_locks_complete_graph() -> None:
    # Given a complete manifest with one segment, LFS object, and artifact
    package, _, _ = _domain_graph()
    token = PairingToken.parse("0123456789abcdef0123456789abcdef")
    manifest = Manifest(
        package=package,
        destination="/home/updater/app",
        persistent_paths=("var/data",),
        pairing_token=token,
        dangerous_confirmed=False,
        artifacts=(
            Artifact(
                "bundles/segment-1.bundle", "c" * 64, package.segments[0].byte_size
            ),
        ),
    )

    # When encoded and reparsed through the byte boundary
    encoded = encode_manifest(manifest)
    reparsed = parse_manifest(encoded)

    # Then canonical bytes and every typed value are stable without token rendering
    assert encode_manifest(reparsed) == encoded
    assert reparsed.package == package
    assert reparsed.pairing_token.matches(token)
    assert encoded.endswith(b"\n")
    assert not encoded.endswith(b"\n\n")


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        (
            b'"schema":"manifest-v1"',
            b'"schema":"manifest-v2"',
            ProtocolReason.UNSUPPORTED_SCHEMA,
        ),
        (
            b'"persistent_paths":["var/data"]',
            b'"persistent_paths":["../data"]',
            ProtocolReason.NONCANONICAL_PATH,
        ),
        (
            b'"target_commit":"' + b"a" * 40 + b'"',
            b'"target_commit":"' + b"d" * 40 + b'"',
            ProtocolReason.INCONSISTENT_TARGET,
        ),
        (
            b'"pairing_token":"0123456789abcdef0123456789abcdef"',
            b'"pairing_token":"secret"',
            ProtocolReason.MALFORMED_TOKEN,
        ),
    ],
)
def test_manifest_rejects_invalid_corpus_with_typed_reason(
    old: bytes,
    new: bytes,
    reason: ProtocolReason,
) -> None:
    # Given one canonical manifest changed into a prohibited representation
    package, _, _ = _domain_graph()
    manifest = Manifest(
        package,
        "/home/updater/app",
        ("var/data",),
        PairingToken.parse("0123456789abcdef0123456789abcdef"),
        dangerous_confirmed=False,
        artifacts=(
            Artifact(
                "bundles/segment-1.bundle",
                "c" * 64,
                package.segments[0].byte_size,
            ),
        ),
    )
    payload = encode_manifest(manifest).replace(old, new, 1)

    # When parsed, then publication callers receive only a stable reason
    with pytest.raises(ProtocolError) as captured:
        _ = parse_manifest(payload)
    assert captured.value.reason is reason


def test_profile_state_result_media_and_ready_round_trip(tmp_path: Path) -> None:
    # Given every remaining v1 protocol model
    package, transaction, result = _domain_graph()
    git_dir = tmp_path / "repo.git"
    git_dir.mkdir()
    profile = RepoProfile(
        RepoId.parse("0123456789abcdef0123456789abcdef"),
        RepositorySource.create(git_dir, "ssh://git.example/repo.git"),
        ProfileSettings(
            destination="/home/updater/app",
            service_user="updater",
            danger_enabled=False,
        ),
        PairingToken.parse("0123456789abcdef0123456789abcdef"),
    )
    state = StateDocument(transaction, package.generation, package.target_commit)
    media = MediaMarker(package.media_id, package.filesystem)
    ready = ReadyMarker(
        package.package_id,
        "d" * 64,
        (
            ReadyArtifact(
                "manifest.json",
                "e" * 64,
                "1234abcd",
                package.segments[0].byte_size,
            ),
        ),
    )

    # When each model is encoded and parsed
    # Then every typed protocol is immutable and round-trips canonically
    assert encode_profile(parse_profile(encode_profile(profile))) == encode_profile(
        profile
    )
    assert parse_state(encode_state(state)) == state
    assert parse_result(encode_result(result)) == result
    assert parse_media(encode_media(media)) == media
    assert parse_ready(encode_ready(ready)) == ready
    with pytest.raises(FrozenInstanceError):
        state.__setattr__("target_commit", "f" * 40)


def test_every_result_status_maps_to_one_stable_exit_class() -> None:
    # Given the complete closed result-status family
    expected = {
        ResultStatus.SUCCESS: ExitCode.SUCCESS,
        ResultStatus.NO_OP: ExitCode.SUCCESS,
        ResultStatus.NEEDS_FULL_BUNDLE: ExitCode.NEEDS_FULL,
        ResultStatus.REJECTED: ExitCode.REJECTED,
        ResultStatus.FAILED_ROLLED_BACK: ExitCode.ROLLED_BACK,
        ResultStatus.FAILED_PRESERVED: ExitCode.PRESERVED,
        ResultStatus.RECOVERY_FAILED: ExitCode.RECOVERY_FAILED,
        ResultStatus.MEDIA_IO_FAILURE: ExitCode.MEDIA_IO,
    }

    # When each status is mapped, then no status is missing or ambiguous
    assert {status: exit_code_for_status(status) for status in ResultStatus} == expected
