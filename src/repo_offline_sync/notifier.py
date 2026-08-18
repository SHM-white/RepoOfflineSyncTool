"""Optional physical-notification boundary; v1 intentionally does nothing."""

from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    def media_unmounted(self, mountpoint: str) -> None:
        """Called after a verified normal unmount."""
        ...


class NoopNotifier:
    def media_unmounted(self, mountpoint: str) -> None:
        """No LED/GPIO/buzzer implementation in the lightweight version."""
        return None
