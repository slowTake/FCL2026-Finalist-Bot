"""knight — Engine Domain: Cached WorldView / Turn Context.

Provides centralized, per-turn memoized views of game state, blackboard slots,
and spatial layout to minimize redundant computations and CPU time.
"""

from __future__ import annotations

from typing import Optional
from fcode import Controller, Position

from config import Config
from engine.safe_controller import SafeController
from utils.geometry import get_core_footprint


class WorldView:
    """Per-turn cached worldview shared across unit role handlers."""

    def __init__(self, safe: SafeController) -> None:
        self.safe = safe
        self.turn: int = -1
        self._core_pos: Optional[Position] = None
        self._core_footprint: list[Position] = []
        self._expansion_policy: int = Config.EXPANSION_FREE
        self._tactical_mode: int = 0
        self._global_flags: int = 0
        self._ammo_reserve: int = 0
        self._turret_count: int = 0
        self._builder_count: int = 0
        self._core_hp_pct: int = 100
        self._current_round: int = 0

    def refresh(self, ct: Controller) -> None:
        """Refresh cached properties on tick activation."""
        rnd = ct.get_current_round()
        if self.turn == rnd and self._core_footprint:
            return
        self.turn = rnd
        self._current_round = rnd

        # Core Footprint (memoized)
        self._core_footprint = get_core_footprint(ct)
        self._core_pos = self._core_footprint[0] if self._core_footprint else ct.get_position()

        # Blackboard Store Reads (memoized per tick)
        self._expansion_policy = ct.read_store(Config.SLOT_EXPANSION_POLICY)
        self._tactical_mode = ct.read_store(Config.SLOT_TACTICAL_MODE)
        self._global_flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        self._ammo_reserve = ct.read_store(Config.SLOT_AMMO_RESERVE)
        self._turret_count = ct.read_store(Config.SLOT_TURRET_COUNT)
        self._builder_count = ct.read_store(Config.SLOT_BUILDER_COUNT)
        self._core_hp_pct = ct.read_store(Config.SLOT_BASE_INTEGRITY)
        if self._core_hp_pct == 0:
            self._core_hp_pct = 100

    @property
    def core_pos(self) -> Position:
        return self._core_pos or Position(0, 0)

    @property
    def core_footprint(self) -> list[Position]:
        return self._core_footprint

    @property
    def expansion_policy(self) -> int:
        return self._expansion_policy

    @property
    def tactical_mode(self) -> int:
        return self._tactical_mode

    @property
    def global_flags(self) -> int:
        return self._global_flags

    @property
    def need_scouts(self) -> bool:
        return bool(self._global_flags & Config.FLAG_NEED_SCOUTS)

    @property
    def emergency_exploration(self) -> bool:
        return bool(self._global_flags & Config.FLAG_EMERGENCY_EXPLORATION)

    @property
    def ammo_reserve(self) -> int:
        return self._ammo_reserve

    @property
    def turret_count(self) -> int:
        return self._turret_count

    @property
    def builder_count(self) -> int:
        return self._builder_count

    @property
    def core_hp_pct(self) -> int:
        return self._core_hp_pct

    @property
    def core_threat(self) -> bool:
        return bool(self._global_flags & Config.FLAG_CORE_THREAT)

    @property
    def current_round(self) -> int:
        return self._current_round
