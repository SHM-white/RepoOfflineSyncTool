"""Runtime contract for Repo Offline Sync."""

from __future__ import annotations

import sys
from typing import Final

__version__: Final = "0.1.0"
_SUPPORTED_RUNTIME: Final = (3, 10)


class UnsupportedPythonError(RuntimeError):
    """Raised when the updater is loaded by an unsupported Python runtime."""

    detected: tuple[int, int]

    def __init__(self, detected: tuple[int, int]) -> None:
        """Record the unsupported major and minor interpreter version."""
        self.detected = detected
        detected_text = f"{detected[0]}.{detected[1]}"
        message = f"Repo Offline Sync requires Python 3.10; detected {detected_text}"
        super().__init__(message)


if sys.version_info[:2] != _SUPPORTED_RUNTIME:
    raise UnsupportedPythonError(sys.version_info[:2])
