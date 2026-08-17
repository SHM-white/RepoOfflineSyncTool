from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from repo_offline_sync.platform.runtime import RuntimeSources


@dataclass(frozen=True, slots=True)
class FixedClock:
    monotonic_value: float
    wall_value: float

    def monotonic(self) -> float:
        return self.monotonic_value

    def wall_time(self) -> float:
        return self.wall_value


@dataclass(frozen=True, slots=True)
class FixedUuidSource:
    value: UUID

    def new_uuid(self) -> UUID:
        return self.value


def test_runtime_sources_use_injected_clock_and_uuid_without_global_state() -> None:
    # Given deterministic clock and UUID providers
    expected_uuid = UUID("12345678-1234-5678-1234-567812345678")
    runtime = RuntimeSources(
        clock=FixedClock(monotonic_value=12.5, wall_value=42.0),
        uuids=FixedUuidSource(expected_uuid),
    )

    # When every runtime source is sampled
    observed = (runtime.monotonic(), runtime.wall_time(), runtime.new_uuid())

    # Then only the injected values are returned
    assert observed == (12.5, 42.0, expected_uuid)
