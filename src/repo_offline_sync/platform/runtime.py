"""Injectable time and UUID sources."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class Clock(Protocol):
    """Provide wall and monotonic time without global substitution."""

    def monotonic(self) -> float:
        """Return a monotonic timestamp in seconds."""
        ...

    def wall_time(self) -> float:
        """Return a Unix wall-clock timestamp in seconds."""
        ...


class UuidSource(Protocol):
    """Create UUID values."""

    def new_uuid(self) -> UUID:
        """Return a new UUID."""
        ...


@dataclass(frozen=True, slots=True)
class SystemClock:
    """Read time from the operating system."""

    def monotonic(self) -> float:
        """Return the system monotonic timestamp."""
        return time.monotonic()

    def wall_time(self) -> float:
        """Return the system wall-clock timestamp."""
        return time.time()


@dataclass(frozen=True, slots=True)
class SystemUuidSource:
    """Create random UUID version 4 values."""

    def new_uuid(self) -> UUID:
        """Return a random UUID version 4."""
        return uuid.uuid4()


@dataclass(frozen=True, slots=True)
class RuntimeSources:
    """Bundle deterministic runtime providers for dependency injection."""

    clock: Clock
    uuids: UuidSource

    def monotonic(self) -> float:
        """Sample the injected monotonic clock."""
        return self.clock.monotonic()

    def wall_time(self) -> float:
        """Sample the injected wall clock."""
        return self.clock.wall_time()

    def new_uuid(self) -> UUID:
        """Sample the injected UUID source."""
        return self.uuids.new_uuid()
