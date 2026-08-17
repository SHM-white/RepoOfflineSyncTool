"""Typed successful outcomes from pure domain policies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from typing_extensions import override
else:
    _Method = TypeVar("_Method", bound=Callable[..., str])

    def override(method: _Method) -> _Method:
        """Return a runtime identity decorator for Python 3.10."""
        return method


_Phase = TypeVar("_Phase")


@dataclass(frozen=True, slots=True)
class TransitionApplied(Generic[_Phase]):
    """A legal in-memory transaction phase change."""

    previous: _Phase
    current: _Phase

    @override
    def __str__(self) -> str:
        """Render the accepted transition edge."""
        return f"transaction transition applied: {self.previous} -> {self.current}"
