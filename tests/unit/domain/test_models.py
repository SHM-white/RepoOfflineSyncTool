from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

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


def _complete_graph() -> tuple[Package, Transaction, Result]:
    package_id = parse_package_id("package-1")
    repo_id = parse_repo_id("repo-1")
    target_id = parse_target_id("target-1")
    transaction_id = parse_transaction_id("transaction-1")
    media_id = parse_media_id("media-1")
    segment_id = parse_segment_id("segment-1")
    generation = parse_generation(0)
    byte_size = parse_positive_bytes(1024)
    timeout = parse_positive_seconds(30)
    target_commit = parse_git_oid("a" * 40)
    lfs_oid = parse_lfs_oid("b" * 64)
    assert isinstance(generation, Generation)
    assert isinstance(byte_size, PositiveBytes)
    assert isinstance(timeout, PositiveSeconds)
    assert isinstance(package_id, str)
    assert isinstance(repo_id, str)
    assert isinstance(target_id, str)
    assert isinstance(transaction_id, str)
    assert isinstance(media_id, str)
    assert isinstance(segment_id, str)
    assert isinstance(target_commit, str)
    assert isinstance(lfs_oid, str)

    repository = RepositoryNode(
        repo_id=repo_id,
        parent_repo_id=None,
        relative_path=".",
        target_commit=target_commit,
        provenance_url=None,
    )
    segment = BundleSegment(
        segment_id=segment_id,
        repo_id=repo_id,
        bundle_kind=BundleKind.FULL,
        generation=generation,
        base_commit=None,
        target_commit=target_commit,
        byte_size=byte_size,
        oversize=False,
    )
    route = SegmentRoute(
        repo_id=repo_id,
        segment_ids=(segment_id,),
        initial_prerequisites=(),
        final_target_commit=target_commit,
    )
    lfs_object = LfsObject(oid=lfs_oid, byte_size=byte_size, repo_ids=(repo_id,))
    action = Action(
        name="build",
        phase=ActionPhase.BUILD,
        argv=("make",),
        timeout=timeout,
    )
    package = Package(
        package_id=package_id,
        target_id=target_id,
        media_id=media_id,
        generation=generation,
        target_commit=target_commit,
        repositories=(repository,),
        routes=(route,),
        segments=(segment,),
        lfs_objects=(lfs_object,),
        actions=(action,),
        failure_policy=FailurePolicy.ROLLBACK,
        filesystem=Filesystem.EXT4,
        full_fallback_included=True,
    )
    transaction = Transaction(
        transaction_id=transaction_id,
        package_id=package_id,
        target_id=target_id,
        phase=TransactionPhase.DISCOVERED,
    )
    result = Result(
        transaction_id=transaction_id,
        package_id=package_id,
        target_id=target_id,
        status=ResultStatus.SUCCESS,
    )
    return package, transaction, result


def test_complete_model_graph_constructs_from_validated_domain_values() -> None:
    # Given parsed identities, quantities, OIDs, and closed policy variants

    # When a complete package, transaction, and result graph is constructed
    package, transaction, result = _complete_graph()

    # Then every relationship retains its exact typed value
    assert package.routes[0].segment_ids == (package.segments[0].segment_id,)
    assert package.repositories[0].target_commit == package.target_commit
    assert transaction.package_id == package.package_id
    assert result.transaction_id == transaction.transaction_id


def test_domain_models_are_frozen_and_slotted() -> None:
    # Given a complete valid package model
    package, _, _ = _complete_graph()

    # When mutation and dynamic attribute storage are attempted
    with pytest.raises(FrozenInstanceError):
        package.__setattr__("full_fallback_included", False)

    # Then the model remains immutable and has no instance dictionary
    assert package.full_fallback_included is True
    assert not hasattr(package, "__dict__")
