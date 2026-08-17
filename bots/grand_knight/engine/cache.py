"""Grand_knight — Turn-Scoped Engine Query Cache.

Memoizes expensive engine queries per tick (get_nearby_*, get_tile_*, etc.)
to prevent redundant C-extension calls and preserve the 10ms CPU safety budget.
"""

from __future__ import annotations

from typing import Any, Callable, Optional
from fcode import Controller


class TurnCache:
    """Memoizes expensive engine queries per tick to save CPU budget."""

    def __init__(self, ct: Optional[Controller] = None) -> None:
        self.ct = ct
        self.current_round: int = -1
        self._cache: dict[str, Any] = {}

    def reset(self, ct: Controller) -> None:
        """Explicitly reset and bind controller for the new turn."""
        self.ct = ct
        current_round = ct.get_current_round()
        if current_round != self.current_round:
            self.current_round = current_round
            self._cache.clear()

    def get(self, key: str, factory: Callable[[], Any]) -> Any:
        """Retrieve cached value or compute and store using factory callable."""
        if self.ct is not None:
            current_round = self.ct.get_current_round()
            if current_round != self.current_round:
                self.current_round = current_round
                self._cache.clear()

        if key not in self._cache:
            self._cache[key] = factory()
        return self._cache[key]

    def clear(self) -> None:
        """Manually clear the cache buffer."""
        self._cache.clear()
