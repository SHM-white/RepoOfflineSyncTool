"""Single stdlib JSON trust boundary and canonical byte encoding."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum, unique
from typing import TYPE_CHECKING, Final, Protocol, TypeAlias

from repo_offline_sync._typing import override

if TYPE_CHECKING:
    from collections.abc import Callable


@unique
class ProtocolReason(str, Enum):
    """Stable machine-consumable protocol rejection reasons."""

    NON_UTF8 = "non-utf8"
    MALFORMED_JSON = "malformed-json"
    DUPLICATE_KEY = "duplicate-key"
    UNSUPPORTED_NUMBER = "unsupported-number"
    ROOT_TYPE = "root-type"
    UNKNOWN_KEY = "unknown-key"
    MISSING_KEY = "missing-key"
    WRONG_TYPE = "wrong-type"
    UNSUPPORTED_SCHEMA = "unsupported-schema"
    INVALID_VALUE = "invalid-value"
    NONCANONICAL_PATH = "noncanonical-path"
    DUPLICATE_VALUE = "duplicate-value"
    INCONSISTENT_TARGET = "inconsistent-target"
    MALFORMED_TOKEN = "malformed-token"  # noqa: S105


@dataclass(frozen=True, slots=True)
class ProtocolError(Exception):
    """Reject protocol bytes without retaining untrusted or sensitive values."""

    reason: ProtocolReason
    field: str | None = None

    @override
    def __str__(self) -> str:
        """Render only stable structural information."""
        suffix = "" if self.field is None else f": {self.field}"
        return f"invalid protocol: {self.reason.value}{suffix}"


@dataclass(frozen=True, slots=True)
class JsonObject:
    """Immutable duplicate-free JSON object preserving boundary structure."""

    pairs: tuple[tuple[str, JsonValue], ...]

    def required(self, name: str) -> JsonValue:
        """Return a required field or fail with its machine path."""
        for key, value in self.pairs:
            if key == name:
                return value
        raise ProtocolError(ProtocolReason.MISSING_KEY, name)

    def require_exact(self, expected: frozenset[str]) -> None:
        """Reject both ignored input and absent required fields."""
        actual = frozenset(key for key, _value in self.pairs)
        missing = expected - actual
        if missing:
            raise ProtocolError(ProtocolReason.MISSING_KEY, min(missing))
        unknown = actual - expected
        if unknown:
            raise ProtocolError(ProtocolReason.UNKNOWN_KEY, min(unknown))


@dataclass(frozen=True, slots=True)
class JsonArray:
    """Immutable JSON array."""

    values: tuple[JsonValue, ...]


JsonValue: TypeAlias = bool | int | str | JsonObject | JsonArray | None
_Loaded: TypeAlias = bool | int | str | JsonObject | list["_Loaded"] | None
_SCHEMA_SUFFIX: Final = "-v1"


class _JsonLoads(Protocol):
    def __call__(
        self,
        s: str,
        *,
        object_pairs_hook: Callable[[list[tuple[str, _Loaded]]], JsonObject],
        parse_float: Callable[[str], float],
        parse_constant: Callable[[str], float],
    ) -> _Loaded: ...


_loads: _JsonLoads = json.loads


def _pairs_hook(pairs: list[tuple[str, _Loaded]]) -> JsonObject:
    keys: set[str] = set()
    frozen: list[tuple[str, JsonValue]] = []
    for key, value in pairs:
        if key in keys:
            raise ProtocolError(ProtocolReason.DUPLICATE_KEY, key)
        keys.add(key)
        frozen.append((key, _freeze(value)))
    return JsonObject(tuple(frozen))


def _reject_number(_raw: str) -> float:
    raise ProtocolError(ProtocolReason.UNSUPPORTED_NUMBER)


def _freeze(value: _Loaded) -> JsonValue:
    match value:
        case None | bool() | int() | str() | JsonObject():
            return value
        case list():
            return JsonArray(tuple(_freeze(item) for item in value))


def decode_json(data: bytes) -> JsonObject:
    """Decode UTF-8 bytes exactly once into the narrow immutable JSON AST."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError(ProtocolReason.NON_UTF8) from error
    try:
        loaded = _loads(
            text,
            object_pairs_hook=_pairs_hook,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except json.JSONDecodeError as error:
        raise ProtocolError(ProtocolReason.MALFORMED_JSON) from error
    frozen = _freeze(loaded)
    match frozen:
        case JsonObject():
            return frozen
        case _:
            raise ProtocolError(ProtocolReason.ROOT_TYPE)


def _encode(value: JsonValue) -> str:
    match value:
        case None:
            return "null"
        case bool() as boolean:
            return "true" if boolean else "false"
        case int() as integer:
            return str(integer)
        case str() as text:
            return json.dumps(text, ensure_ascii=False, separators=(",", ":"))
        case JsonArray(values=values):
            return "[" + ",".join(_encode(item) for item in values) + "]"
        case JsonObject(pairs=pairs):
            fields = sorted(pairs, key=lambda pair: pair[0])
            return (
                "{"
                + ",".join(f"{_encode(key)}:{_encode(item)}" for key, item in fields)
                + "}"
            )


def canonical_bytes(document: JsonObject) -> bytes:
    """Encode canonical UTF-8 JSON with exactly one trailing newline."""
    return (_encode(document) + "\n").encode("utf-8")


def require_schema(document: JsonObject, name: str) -> None:
    """Require the one supported explicit schema major."""
    schema = require_string(document, "schema")
    if schema != name + _SCHEMA_SUFFIX:
        raise ProtocolError(ProtocolReason.UNSUPPORTED_SCHEMA, "schema")


def require_object(document: JsonObject, name: str) -> JsonObject:
    """Extract a required object field."""
    value = document.required(name)
    match value:
        case JsonObject():
            return value
        case _:
            raise ProtocolError(ProtocolReason.WRONG_TYPE, name)


def require_array(document: JsonObject, name: str) -> JsonArray:
    """Extract a required array field."""
    value = document.required(name)
    match value:
        case JsonArray():
            return value
        case _:
            raise ProtocolError(ProtocolReason.WRONG_TYPE, name)


def require_string(document: JsonObject, name: str) -> str:
    """Extract a required string field."""
    value = document.required(name)
    match value:
        case str():
            return value
        case _:
            raise ProtocolError(ProtocolReason.WRONG_TYPE, name)


def require_optional_string(document: JsonObject, name: str) -> str | None:
    """Extract a required nullable string field."""
    value = document.required(name)
    match value:
        case None | str():
            return value
        case _:
            raise ProtocolError(ProtocolReason.WRONG_TYPE, name)


def require_integer(document: JsonObject, name: str) -> int:
    """Extract a required integer while explicitly rejecting booleans."""
    value = document.required(name)
    match value:
        case bool():
            raise ProtocolError(ProtocolReason.WRONG_TYPE, name)
        case int():
            return value
        case _:
            raise ProtocolError(ProtocolReason.WRONG_TYPE, name)


def require_boolean(document: JsonObject, name: str) -> bool:
    """Extract a required boolean field."""
    value = document.required(name)
    match value:
        case bool():
            return value
        case _:
            raise ProtocolError(ProtocolReason.WRONG_TYPE, name)


def as_object(value: JsonValue, name: str) -> JsonObject:
    """Narrow one array member to an object."""
    match value:
        case JsonObject():
            return value
        case _:
            raise ProtocolError(ProtocolReason.WRONG_TYPE, name)


def as_string(value: JsonValue, name: str) -> str:
    """Narrow one array member to a string."""
    match value:
        case str():
            return value
        case _:
            raise ProtocolError(ProtocolReason.WRONG_TYPE, name)
