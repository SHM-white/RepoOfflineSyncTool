"""Typed immutable values used by reusable packaging profiles."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Final

from repo_offline_sync._typing import override

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_TOKEN_PATTERN: Final = re.compile(r"[0-9a-f]{32}", re.ASCII)
_USER_PATTERN: Final = re.compile(r"[a-z_][a-z0-9_-]{0,31}", re.ASCII)


class ProfileFailure(str, Enum):
    """Closed reasons for invalid profile boundary input."""

    DESTINATION = "destination is malformed"
    REPO_ID = "repository ID is not a UUID"
    REPO_ID_CANONICAL = "repository ID is not canonical lowercase hex"
    SOURCE = "repository source is malformed"
    STORAGE = "private profile storage is malformed"
    STORAGE_ID = "profile ID does not match filename"
    STORAGE_IDENTITY = "repository identity record does not match"
    STORAGE_CONFLICT = "private profile storage has conflicting repository identities"
    PAIRING_FORMAT = "pairing token must be 128-bit lowercase hex"
    USER = "service user is malformed"


@dataclass(frozen=True, slots=True)
class ProfileFormatError(Exception):
    """Report invalid profile input without retaining sensitive values."""

    reason: ProfileFailure
    detail: str | None = None

    @override
    def __str__(self) -> str:
        """Render a safe structured profile failure."""
        suffix = "" if self.detail is None else f": {self.detail}"
        return f"invalid profile: {self.reason.value}{suffix}"


@dataclass(frozen=True, slots=True)
class RepositoryLinkError(Exception):
    """Report an explicit link that violates repository provenance."""

    @override
    def __str__(self) -> str:
        """Render a safe link failure."""
        return "repository link rejected: provenance remote differs"


@dataclass(frozen=True, slots=True)
class RepoId:
    """Canonical stable repository UUID."""

    value: str

    @classmethod
    def generate(cls) -> RepoId:
        """Generate a random UUID for first repository initialization."""
        return cls(uuid.uuid4().hex)

    @classmethod
    def parse(cls, raw: str) -> RepoId:
        """Parse a canonical lowercase UUID storage identifier."""
        try:
            parsed = uuid.UUID(raw)
        except (ValueError, AttributeError) as error:
            raise ProfileFormatError(ProfileFailure.REPO_ID) from error
        canonical = parsed.hex
        if raw != canonical:
            raise ProfileFormatError(ProfileFailure.REPO_ID_CANONICAL)
        return cls(canonical)

    @override
    def __str__(self) -> str:
        """Return the non-sensitive canonical identifier."""
        return self.value


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class PairingToken:
    """Plaintext mismatch guard whose public rendering is always redacted."""

    _value: str

    @classmethod
    def generate(cls) -> PairingToken:
        """Generate 128 random bits encoded as lowercase hexadecimal."""
        return cls(secrets.token_hex(16))

    @classmethod
    def parse(cls, raw: str) -> PairingToken:
        """Parse an explicitly supplied manual or preserved token."""
        if _TOKEN_PATTERN.fullmatch(raw) is None:
            raise ProfileFormatError(ProfileFailure.PAIRING_FORMAT)
        return cls(raw)

    def matches(self, presented: PairingToken) -> bool:
        """Compare mismatch guards in constant time."""
        return hmac.compare_digest(self._value, presented._value)

    def redact(self, text: str) -> str:
        """Remove this token's plaintext from arbitrary text."""
        return text.replace(self._value, "<redacted>")

    @override
    def __repr__(self) -> str:
        """Redact debugger and container representations."""
        return "PairingToken(<redacted>)"

    @override
    def __str__(self) -> str:
        """Redact ordinary string rendering."""
        return "<redacted>"


def redact_tokens(text: str, tokens: Sequence[PairingToken]) -> str:
    """Remove every supplied plaintext pairing token from arbitrary text."""
    redacted = text
    for token in tokens:
        redacted = token.redact(redacted)
    return redacted


@dataclass(frozen=True, slots=True)
class RepositorySource:
    """Canonical local Git identity plus its provenance remote."""

    common_git_dir: Path
    provenance_remote: str

    @classmethod
    def create(cls, common_git_dir: Path, provenance_remote: str) -> RepositorySource:
        """Capture a canonical existing common Git directory and provenance."""
        canonical = common_git_dir.expanduser().resolve(strict=True)
        remote = provenance_remote.strip()
        if not canonical.is_dir() or not remote or "\x00" in remote:
            raise ProfileFormatError(ProfileFailure.SOURCE)
        return cls(canonical, remote)

    def identity_key(self) -> str:
        """Key an exact clone location and provenance without conflating moves."""
        material = f"{self.common_git_dir}\0{self.provenance_remote}".encode()
        return hashlib.sha256(material).hexdigest()


@dataclass(frozen=True, slots=True)
class ProfileSettings:
    """Editable packaging settings retained for one repository."""

    destination: str
    service_user: str
    danger_enabled: bool

    def __post_init__(self) -> None:
        """Reject malformed stable profile settings at construction."""
        if not self.destination or "\x00" in self.destination:
            raise ProfileFormatError(ProfileFailure.DESTINATION)
        if _USER_PATTERN.fullmatch(self.service_user) is None:
            raise ProfileFormatError(ProfileFailure.USER)


@dataclass(frozen=True, slots=True)
class RepoProfile:
    """Typed reusable packaging profile."""

    repo_id: RepoId
    source: RepositorySource
    settings: ProfileSettings
    token: PairingToken


@dataclass(frozen=True, slots=True)
class XdgRoots:
    """Application-specific XDG roots, injectable for isolated tests."""

    config: Path
    cache: Path
    state: Path

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> XdgRoots:
        """Resolve application roots from XDG variables with standard defaults."""
        values = os.environ if environment is None else environment
        home = Path(values.get("HOME", str(Path.home())))
        config = Path(values.get("XDG_CONFIG_HOME", str(home / ".config")))
        cache = Path(values.get("XDG_CACHE_HOME", str(home / ".cache")))
        state = Path(values.get("XDG_STATE_HOME", str(home / ".local/state")))
        application = "repo-offline-sync"
        return cls(*(root / application for root in (config, cache, state)))
