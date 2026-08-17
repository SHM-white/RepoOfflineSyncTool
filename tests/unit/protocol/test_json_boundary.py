from __future__ import annotations

import pytest

from repo_offline_sync.protocol.json_boundary import (
    ProtocolError,
    ProtocolReason,
    canonical_bytes,
    decode_json,
)


def test_canonical_boundary_sorts_keys_and_writes_one_newline() -> None:
    # Given one noncanonical but valid UTF-8 document
    document = decode_json(b'{ "z": [2, 1], "a": true }')

    # When it is encoded through the shared boundary
    encoded = canonical_bytes(document)

    # Then bytes are sorted, compact, UTF-8, and newline terminated
    assert encoded == b'{"a":true,"z":[2,1]}\n'


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"\xff", ProtocolReason.NON_UTF8),
        (b'{"a":1,"a":2}', ProtocolReason.DUPLICATE_KEY),
        (b'{"a":', ProtocolReason.MALFORMED_JSON),
        (b'{"a":1.5}', ProtocolReason.UNSUPPORTED_NUMBER),
    ],
)
def test_boundary_returns_machine_reason_for_invalid_bytes(
    payload: bytes,
    reason: ProtocolReason,
) -> None:
    # Given invalid untrusted JSON bytes
    # When they cross the boundary, then a stable typed reason is raised
    with pytest.raises(ProtocolError) as captured:
        _ = decode_json(payload)
    assert captured.value.reason is reason
