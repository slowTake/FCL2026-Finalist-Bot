"""knight — Exploration Domain: Central Symmetry & Soft Resource Inference.

Projects map symmetry from observed friendly Core and ore tiles to infer
enemy Core positions and high-value mirrored ore clusters early.
"""

from __future__ import annotations

import sys
from typing import Optional, Tuple
from fcode import Controller, Position

from config import Config
from exploration.spread_zone import SpreadZone, zone_score
from utils.geometry import get_core_footprint, is_in_bounds, is_inside_core_area, is_valid_ore_target


class SymmetryHints:
    """Soft guidance cache predicting enemy base and mirrored ore clusters."""

    def __init__(self) -> None:
        self.enabled: bool = Config.SYMMETRY_ENABLED
        self.enemy_core_hint: Optional[Position] = None
        self.ore_hints: list[tuple[Position, int, int]] = []  # (position, confidence, created_round)

    def initialize(self, ct: Controller, core_pos: Position) -> None:
        """Estimate enemy Core position via 180-degree central inversion."""
        if not self.enabled:
            return
        map_w = ct.get_map_width()
        map_h = ct.get_map_height()
        self.enemy_core_hint = Position(map_w - 1 - core_pos.x, map_h - 1 - core_pos.y)

    def mirror_position(self, ct: Controller, pos: Position) -> Position:
        """Compute centrally mirrored coordinate."""
        map_w = ct.get_map_width()
        map_h = ct.get_map_height()
        return Position(map_w - 1 - pos.x, map_h - 1 - pos.y)

    def add_ore_hint(self, ct: Controller, ore_pos: Position) -> None:
        """Add mirrored candidate position when a friendly local ore tile is discovered."""
        if not self.enabled:
            return

        core_footprint = get_core_footprint(ct)
        mirrored = self.mirror_position(ct, ore_pos)
        if not is_in_bounds(ct, mirrored) or is_inside_core_area(mirrored, core_footprint, margin=0):
            return

        # Check if already present
        for p, _, _ in self.ore_hints:
            if p == mirrored:
                return

        curr_rnd = ct.get_current_round()
        confidence = Config.SYMMETRY_CONFIDENCE_INITIAL

        if len(self.ore_hints) >= Config.SYMMETRY_MAX_HINTS:
            # Drop lowest confidence or oldest entry
            self.ore_hints.sort(key=lambda item: (item[1], -item[2]))
            self.ore_hints.pop(0)

        self.ore_hints.append((mirrored, confidence, curr_rnd))

        if Config.DEBUG_ECONOMY:
            print(f"SYMMETRY_HINT_ADDED hint={mirrored} confidence={confidence} round={curr_rnd}", file=sys.stderr)

    def best_ore_hint(
        self,
        ct: Controller,
        builder_pos: Position,
        zone: Optional[SpreadZone] = None,
        core_pos: Optional[Position] = None,
    ) -> Optional[Position]:
        """Return highest-value valid symmetry ore hint."""
        if not self.enabled or not self.ore_hints:
            return None

        self.prune_expired(ct)
        if not self.ore_hints:
            return None

        core_footprint = get_core_footprint(ct)
        curr_rnd = ct.get_current_round()
        candidates: list[tuple[int, Position]] = []

        for p, conf, created_rnd in self.ore_hints:
            if not is_valid_ore_target(ct, p, core_footprint):
                continue

            # Decay confidence over time
            age = curr_rnd - created_rnd
            eff_conf = conf - (age // Config.SYMMETRY_CONFIDENCE_DECAY_TURNS)
            if eff_conf <= 0:
                continue

            dist_to_builder = builder_pos.distance_squared(p)
            z_score = zone_score(p, core_pos, zone) if (zone is not None and core_pos is not None) else 50

            score = (eff_conf * 100) + z_score - (dist_to_builder // 4)
            candidates.append((score, p))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def mark_hint_failed(self, ct: Controller, pos: Position) -> None:
        """Penalize or remove an ore hint upon discovering wall/obstruction/void."""
        curr_rnd = ct.get_current_round()
        updated_hints = []
        for p, conf, created_rnd in self.ore_hints:
            if p == pos or p.distance_squared(pos) <= 2:
                new_conf = conf - 2
                if new_conf > 0:
                    updated_hints.append((p, new_conf, created_rnd))
                if Config.DEBUG_ECONOMY:
                    print(f"SYMMETRY_HINT_FAILED hint={p} round={curr_rnd}", file=sys.stderr)
            else:
                updated_hints.append((p, conf, created_rnd))
        self.ore_hints = updated_hints

    def prune_expired(self, ct: Controller) -> None:
        """Discard symmetry hints older than SYMMETRY_HINT_TTL_TURNS."""
        curr_rnd = ct.get_current_round()
        self.ore_hints = [
            (p, c, r)
            for p, c, r in self.ore_hints
            if (curr_rnd - r) <= Config.SYMMETRY_HINT_TTL_TURNS
        ]
