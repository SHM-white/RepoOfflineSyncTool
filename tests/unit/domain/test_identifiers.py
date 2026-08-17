from __future__ import annotations

from collections.abc import Callable

import pytest

from repo_offline_sync.domain.errors import (
    InvalidIdentifier,
    InvalidOid,
    InvalidQuantity,
    InvalidQuantityError,
)
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

IdentifierParser = Callable[[str], str | InvalidIdentifier]


@pytest.mark.parametrize(
    ("parser", "raw"),
    [
        (parse_package_id, "package-1"),
        (parse_repo_id, "repo-1"),
        (parse_target_id, "target-1"),
        (parse_transaction_id, "transaction-1"),
        (parse_media_id, "media-1"),
        (parse_segment_id, "segment-1"),
    ],
)
def test_identifier_parser_returns_branded_value_when_input_is_nonempty(
    parser: IdentifierParser,
    raw: str,
) -> None:
    # Given a nonempty canonical identity string

    # When it crosses the typed identity boundary
    parsed = parser(raw)

    # Then the exact value is retained without a failure variant
    assert parsed == raw
    assert not isinstance(parsed, InvalidIdentifier)


@pytest.mark.parametrize("raw", ["", "   ", " package-1", "package-1 "])
def test_identifier_parser_returns_typed_failure_when_input_is_invalid(
    raw: str,
) -> None:
    # Given an empty or noncanonical package identity

    # When it crosses the typed identity boundary
    parsed = parse_package_id(raw)

    # Then no branded value is yielded
    assert isinstance(parsed, InvalidIdentifier)
    assert parsed.value == raw


@pytest.mark.parametrize("length", [40, 64])
def test_git_oid_parser_accepts_supported_git_object_formats(length: int) -> None:
    # Given a hexadecimal SHA-1 or SHA-256 Git object identity
    raw = "A" * length

    # When the Git OID is parsed
    parsed = parse_git_oid(raw)

    # Then it is accepted and canonicalized to lowercase
    assert parsed == raw.lower()
    assert not isinstance(parsed, InvalidOid)


@pytest.mark.parametrize("raw", ["a" * 39, "a" * 41, "a" * 63, "a" * 65, "g" * 40])
def test_git_oid_parser_returns_typed_failure_when_oid_is_malformed(raw: str) -> None:
    # Given a nonhex or unsupported-length Git object identity

    # When the Git OID is parsed
    parsed = parse_git_oid(raw)

    # Then no GitOid is yielded
    assert isinstance(parsed, InvalidOid)
    assert parsed.value == raw


def test_lfs_oid_parser_accepts_only_sha256() -> None:
    # Given one SHA-256 OID and one Git SHA-1 OID
    sha256 = "b" * 64
    sha1 = "b" * 40

    # When both values cross the LFS boundary
    parsed_sha256 = parse_lfs_oid(sha256)
    parsed_sha1 = parse_lfs_oid(sha1)

    # Then only the SHA-256 identity is valid
    assert parsed_sha256 == sha256
    assert isinstance(parsed_sha1, InvalidOid)


@pytest.mark.parametrize(
    ("parser", "value", "expected_type"),
    [
        (parse_generation, 0, Generation),
        (parse_positive_bytes, 1, PositiveBytes),
        (parse_positive_seconds, 1, PositiveSeconds),
    ],
)
def test_quantity_parser_returns_validated_value(
    parser: Callable[
        [int], Generation | PositiveBytes | PositiveSeconds | InvalidQuantity
    ],
    value: int,
    expected_type: type[Generation | PositiveBytes | PositiveSeconds],
) -> None:
    # Given a number at the valid boundary for its quantity

    # When the quantity is parsed
    parsed = parser(value)

    # Then a validated immutable quantity is yielded
    assert isinstance(parsed, expected_type)
    assert parsed.value == value


@pytest.mark.parametrize(
    ("parser", "value"),
    [
        (parse_generation, -1),
        (parse_positive_bytes, 0),
        (parse_positive_bytes, -1),
        (parse_positive_seconds, 0),
        (parse_positive_seconds, -1),
        (parse_generation, True),
    ],
)
def test_quantity_parser_returns_typed_failure_when_value_is_out_of_range(
    parser: Callable[
        [int], Generation | PositiveBytes | PositiveSeconds | InvalidQuantity
    ],
    value: int,
) -> None:
    # Given a negative, zero-positive, or boolean quantity

    # When the quantity is parsed
    parsed = parser(value)

    # Then no validated quantity is yielded
    assert isinstance(parsed, InvalidQuantity)
    assert parsed.value == value


def test_invalid_direct_quantity_construction_raises_typed_error() -> None:
    # Given values that violate direct-construction invariants

    # When each public value type is constructed directly
    with pytest.raises(InvalidQuantityError):
        _ = Generation(-1)
    with pytest.raises(InvalidQuantityError):
        _ = PositiveBytes(0)
    with pytest.raises(InvalidQuantityError):
        _ = PositiveSeconds(0)

    # Then no invalid value object survives construction
