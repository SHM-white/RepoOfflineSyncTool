from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from repo_offline_sync.domain import identifiers
from repo_offline_sync.domain.errors import InvalidIdentifier

PROJECT_ROOT: Final = Path(__file__).resolve().parents[3]
IDENTITY_TYPE_NAMES: Final = (
    "PackageId",
    "RepoId",
    "TargetId",
    "TransactionId",
    "MediaId",
    "SegmentId",
    "GitOid",
    "LfsOid",
)


def _run_basedpyright(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    fixture = tmp_path / "identity_consumer.py"
    _ = fixture.write_text(source, encoding="utf-8")
    executable = Path(sys.executable).parent / "basedpyright"
    return subprocess.run(
        (str(executable), str(fixture)),
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_identity_types_are_type_only_and_not_runtime_constructors() -> None:
    # Given the public runtime identity module

    # When each branded type name is inspected
    exposed_names = tuple(
        name for name in IDENTITY_TYPE_NAMES if hasattr(identifiers, name)
    )

    # Then no unchecked constructor is exposed at runtime
    assert exposed_names == ()


def test_parser_results_narrow_to_distinct_brands_for_strict_consumers(
    tmp_path: Path,
) -> None:
    # Given a strict consumer that imports brands only for type checking
    source = """
from __future__ import annotations

from typing import TYPE_CHECKING

from repo_offline_sync.domain.errors import InvalidIdentifier, InvalidOid
from repo_offline_sync.domain.identifiers import (
    parse_git_oid,
    parse_lfs_oid,
    parse_media_id,
    parse_package_id,
    parse_repo_id,
    parse_segment_id,
    parse_target_id,
    parse_transaction_id,
)

if TYPE_CHECKING:
    from repo_offline_sync.domain.identifiers import (
        GitOid,
        LfsOid,
        MediaId,
        PackageId,
        RepoId,
        SegmentId,
        TargetId,
        TransactionId,
    )

package_id = parse_package_id("package")
repo_id = parse_repo_id("repo")
target_id = parse_target_id("target")
transaction_id = parse_transaction_id("transaction")
media_id = parse_media_id("media")
segment_id = parse_segment_id("segment")
git_oid = parse_git_oid("a" * 40)
lfs_oid = parse_lfs_oid("b" * 64)

if isinstance(package_id, InvalidIdentifier):
    raise AssertionError(str(package_id))
if isinstance(repo_id, InvalidIdentifier):
    raise AssertionError(str(repo_id))
if isinstance(target_id, InvalidIdentifier):
    raise AssertionError(str(target_id))
if isinstance(transaction_id, InvalidIdentifier):
    raise AssertionError(str(transaction_id))
if isinstance(media_id, InvalidIdentifier):
    raise AssertionError(str(media_id))
if isinstance(segment_id, InvalidIdentifier):
    raise AssertionError(str(segment_id))
if isinstance(git_oid, InvalidOid):
    raise AssertionError(str(git_oid))
if isinstance(lfs_oid, InvalidOid):
    raise AssertionError(str(lfs_oid))

typed_package_id: PackageId = package_id
typed_repo_id: RepoId = repo_id
typed_target_id: TargetId = target_id
typed_transaction_id: TransactionId = transaction_id
typed_media_id: MediaId = media_id
typed_segment_id: SegmentId = segment_id
typed_git_oid: GitOid = git_oid
typed_lfs_oid: LfsOid = lfs_oid
"""

    # When basedpyright checks parser narrowing and all branded assignments
    result = _run_basedpyright(tmp_path, source)

    # Then the supported parser path is statically valid without casts
    assert result.returncode == 0, result.stdout + result.stderr


def test_direct_identity_construction_is_rejected_by_strict_typing(
    tmp_path: Path,
) -> None:
    # Given a strict consumer that attempts to call a public identity type
    source = """
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repo_offline_sync.domain.identifiers import PackageId

unchecked = PackageId("")
"""

    # When basedpyright checks the unsupported direct-construction path
    result = _run_basedpyright(tmp_path, source)

    # Then the type-only brand is not callable
    assert result.returncode != 0
    assert "reportCallIssue" in result.stdout


def test_distinct_identity_brands_reject_cross_assignment(tmp_path: Path) -> None:
    # Given a strict consumer with two independently branded parser results
    source = """
from __future__ import annotations

from typing import TYPE_CHECKING

from repo_offline_sync.domain.errors import InvalidIdentifier
from repo_offline_sync.domain.identifiers import parse_package_id, parse_repo_id

if TYPE_CHECKING:
    from repo_offline_sync.domain.identifiers import PackageId

package_id = parse_package_id("package")
repo_id = parse_repo_id("repo")
if isinstance(package_id, InvalidIdentifier):
    raise AssertionError(str(package_id))
if isinstance(repo_id, InvalidIdentifier):
    raise AssertionError(str(repo_id))

valid: PackageId = package_id
invalid: PackageId = repo_id
"""

    # When basedpyright checks a RepoId assigned as a PackageId
    result = _run_basedpyright(tmp_path, source)

    # Then the distinct NewType brands prevent cross-assignment
    assert result.returncode != 0
    assert "reportAssignmentType" in result.stdout


@pytest.mark.parametrize("raw", ["", "   ", " package", "package "])
def test_invalid_identity_parser_never_exposes_a_runtime_brand(
    raw: str,
) -> None:
    # Given malformed identity input and no runtime identity constructors

    # When the supported package identity parser is called
    result = identifiers.parse_package_id(raw)

    # Then only the typed failure is returned
    assert isinstance(result, InvalidIdentifier)
