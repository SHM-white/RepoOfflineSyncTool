"""Expose the package-disabled source tree to focused domain tests."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

SOURCE_ROOT: Final = Path(__file__).resolve().parents[3] / "src"
sys.path.insert(0, str(SOURCE_ROOT))
