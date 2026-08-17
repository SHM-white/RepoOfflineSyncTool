"""Narrow duplicate-safe JSON parser for private profile storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, TypeAlias, final

from repo_offline_sync.packaging._storage_errors import (
    StorageFailure,
    StorageFormatError,
)

_ESCAPES: Final = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_FIRST_CONTROL_CODE: Final = 0x20


@dataclass(frozen=True, slots=True)
class JsonObject:
    """Represent a decoded JSON object without exposing a mutable mapping."""

    pairs: tuple[tuple[str, JsonValue], ...]

    def required(self, name: str) -> JsonValue:
        for key, value in self.pairs:
            if key == name:
                return value
        raise StorageFormatError(StorageFailure.MISSING_FIELD, name)

    def require_exact_fields(self, expected: frozenset[str]) -> None:
        actual = frozenset(key for key, _value in self.pairs)
        if actual != expected:
            raise StorageFormatError(StorageFailure.UNKNOWN_FIELDS)


JsonValue: TypeAlias = bool | str


@final
class _ProfileJsonParser:
    """Parse the deliberately narrow object/string/boolean profile JSON subset."""

    __slots__ = ("_index", "_text")
    _text: str
    _index: int

    def __init__(self, text: str) -> None:
        self._text = text
        self._index = 0

    def parse(self) -> JsonObject:
        self._space()
        self._expect("{")
        pairs: list[tuple[str, JsonValue]] = []
        self._space()
        if self._peek() != "}":
            while True:
                key = self._string()
                self._space()
                self._expect(":")
                self._space()
                value = self._value()
                if any(existing == key for existing, _value in pairs):
                    raise StorageFormatError(StorageFailure.DUPLICATE_FIELD)
                pairs.append((key, value))
                self._space()
                if self._peek() != ",":
                    break
                self._index += 1
                self._space()
        self._expect("}")
        self._space()
        if self._index != len(self._text):
            raise StorageFormatError(StorageFailure.MALFORMED_JSON)
        return JsonObject(tuple(pairs))

    def _value(self) -> JsonValue:
        if self._peek() == '"':
            return self._string()
        for literal, value in (("true", True), ("false", False)):
            if self._text.startswith(literal, self._index):
                self._index += len(literal)
                return value
        raise StorageFormatError(StorageFailure.MALFORMED_JSON)

    def _string(self) -> str:
        self._expect('"')
        characters: list[str] = []
        while self._index < len(self._text):
            character = self._text[self._index]
            self._index += 1
            if character == '"':
                return "".join(characters)
            if character == "\\":
                characters.append(self._escape())
            elif ord(character) < _FIRST_CONTROL_CODE:
                raise StorageFormatError(StorageFailure.MALFORMED_JSON)
            else:
                characters.append(character)
        raise StorageFormatError(StorageFailure.MALFORMED_JSON)

    def _escape(self) -> str:
        if self._index >= len(self._text):
            raise StorageFormatError(StorageFailure.MALFORMED_JSON)
        escape = self._text[self._index]
        self._index += 1
        if escape in _ESCAPES:
            return _ESCAPES[escape]
        if escape != "u" or self._index + 4 > len(self._text):
            raise StorageFormatError(StorageFailure.MALFORMED_JSON)
        hexadecimal = self._text[self._index : self._index + 4]
        self._index += 4
        try:
            return chr(int(hexadecimal, 16))
        except ValueError as error:
            raise StorageFormatError(StorageFailure.MALFORMED_JSON) from error

    def _space(self) -> None:
        while self._index < len(self._text) and self._text[self._index] in " \t\r\n":
            self._index += 1

    def _peek(self) -> str:
        if self._index >= len(self._text):
            raise StorageFormatError(StorageFailure.MALFORMED_JSON)
        return self._text[self._index]

    def _expect(self, expected: str) -> None:
        if not self._text.startswith(expected, self._index):
            raise StorageFormatError(StorageFailure.MALFORMED_JSON)
        self._index += len(expected)


def decode_object(text: str) -> JsonObject:
    """Decode one JSON object while rejecting duplicate keys."""
    return _ProfileJsonParser(text).parse()


def require_string(document: JsonObject, name: str) -> str:
    """Extract a required string field from private storage."""
    value = document.required(name)
    match value:
        case str():
            return value
        case _:
            raise StorageFormatError(StorageFailure.NOT_STRING, name)


def require_bool(document: JsonObject, name: str) -> bool:
    """Extract a required boolean field from private storage."""
    value = document.required(name)
    match value:
        case bool():
            return value
        case _:
            raise StorageFormatError(StorageFailure.NOT_BOOLEAN, name)
