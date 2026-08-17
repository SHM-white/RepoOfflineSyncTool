from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Final, TypeAlias

import pytest

from repo_offline_sync.domain.models import Result
from repo_offline_sync.packaging.profiles import RepoProfile
from repo_offline_sync.protocol.json_boundary import ProtocolError, ProtocolReason
from repo_offline_sync.protocol.manifest import (
    Manifest,
    encode_manifest,
    parse_manifest,
)
from repo_offline_sync.protocol.media import (
    MediaMarker,
    ReadyMarker,
    encode_media,
    encode_ready,
    parse_media,
    parse_ready,
)
from repo_offline_sync.protocol.profile import encode_profile, parse_profile
from repo_offline_sync.protocol.result import encode_result, parse_result
from repo_offline_sync.protocol.state import StateDocument, encode_state, parse_state

Parser: TypeAlias = Callable[
    [bytes], Manifest | RepoProfile | StateDocument | Result | MediaMarker | ReadyMarker
]
FIXTURES: Final = Path(__file__).resolve().parents[2] / "fixtures" / "protocol"


def _manifest_roundtrip(data: bytes) -> bytes:
    return encode_manifest(parse_manifest(data))


def _profile_roundtrip(data: bytes) -> bytes:
    return encode_profile(parse_profile(data))


def _state_roundtrip(data: bytes) -> bytes:
    return encode_state(parse_state(data))


def _result_roundtrip(data: bytes) -> bytes:
    return encode_result(parse_result(data))


def _media_roundtrip(data: bytes) -> bytes:
    return encode_media(parse_media(data))


def _ready_roundtrip(data: bytes) -> bytes:
    return encode_ready(parse_ready(data))


@pytest.mark.parametrize(
    ("name", "roundtrip"),
    [
        ("manifest-v1.json", _manifest_roundtrip),
        ("profile-v1.json", _profile_roundtrip),
        ("state-v1.json", _state_roundtrip),
        ("result-v1.json", _result_roundtrip),
        ("media-v1.json", _media_roundtrip),
        ("ready-v1.json", _ready_roundtrip),
    ],
)
def test_valid_corpus_is_exact_canonical_bytes(
    name: str,
    roundtrip: Callable[[bytes], bytes],
) -> None:
    # Given one checked-in canonical v1 document
    expected = (FIXTURES / "valid" / name).read_bytes()

    # When parsed and encoded, then the bytes are identical
    assert roundtrip(expected) == expected


@pytest.mark.parametrize(
    ("name", "parser", "reason"),
    [
        ("duplicate-key.json", parse_media, ProtocolReason.DUPLICATE_KEY),
        ("unknown-key.json", parse_media, ProtocolReason.UNKNOWN_KEY),
        ("missing-key.json", parse_media, ProtocolReason.MISSING_KEY),
        ("schema-v2.json", parse_media, ProtocolReason.UNSUPPORTED_SCHEMA),
        ("truncated.json", parse_media, ProtocolReason.MALFORMED_JSON),
        ("boolean-generation.json", parse_state, ProtocolReason.WRONG_TYPE),
        ("traversal-ready.json", parse_ready, ProtocolReason.NONCANONICAL_PATH),
        ("duplicate-artifact.json", parse_ready, ProtocolReason.DUPLICATE_VALUE),
        ("malformed-token.json", parse_profile, ProtocolReason.MALFORMED_TOKEN),
    ],
)
def test_invalid_corpus_has_stable_machine_reason(
    name: str,
    parser: Parser,
    reason: ProtocolReason,
) -> None:
    # Given one deterministic invalid corpus entry
    payload = (FIXTURES / "invalid" / name).read_bytes()

    # When parsed, then only its stable reason class is observed
    with pytest.raises(ProtocolError) as captured:
        _ = parser(payload)
    assert captured.value.reason is reason


def test_manifest_corpus_rejects_duplicate_lfs_oid() -> None:
    # Given a valid manifest whose complete LFS entry is duplicated
    payload = (FIXTURES / "valid" / "manifest-v1.json").read_bytes()
    entry = b'{"byte_size":1024,"oid":"' + b"b" * 64 + b'","repo_ids":["repo-1"]}'
    duplicated = payload.replace(entry, entry + b"," + entry)

    # When parsed, then duplicate OID identity is rejected before a model exists
    with pytest.raises(ProtocolError) as captured:
        _ = parse_manifest(duplicated)
    assert captured.value.reason is ProtocolReason.DUPLICATE_VALUE


def test_manifest_corpus_rejects_submodule_traversal() -> None:
    # Given a repository graph whose lexical child path escapes its root
    payload = (FIXTURES / "valid" / "manifest-v1.json").read_bytes()
    traversal = payload.replace(
        b'"relative_path":"."', b'"relative_path":"../child"', 1
    )

    # When parsed, then path rejection precedes graph or publication behavior
    with pytest.raises(ProtocolError) as captured:
        _ = parse_manifest(traversal)
    assert captured.value.reason is ProtocolReason.NONCANONICAL_PATH
