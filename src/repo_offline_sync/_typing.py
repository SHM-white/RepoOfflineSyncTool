"""Python 3.10 runtime bridge for strict override checking."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from typing_extensions import override
else:
    _Method = TypeVar("_Method")

    def override(method: _Method) -> _Method:
        """Preserve a method while Python 3.10 lacks typing.override."""
        return method


__all__ = ["override"]
