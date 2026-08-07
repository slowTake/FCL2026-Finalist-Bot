"""Core builder spawn / replenish for earl.

Owns apron site pick (toward goal), death observe, living estimate, and the
growth/replenish turn. Caps and timing knobs live in main and are passed in.
"""

from __future__ import annotations

from dataclasses import dataclass

from fcode import (
    Controller,
    Direction,
    EntityType,
    GameConstants,
    Position,
)
from features import WALL_LOCATIONS
from pathing import astar

DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]
CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]


@dataclass(frozen=True)
class SpawnSchedule:
    """Caps and timing knobs — constructed in main from module-level constants."""

    max_builders_early: int
    max_builders: int
    max_builders_threat: int
    first_interval_spawn: int
    spawn_interval: int
    late_spawn_interval: int
    fallback_spawn_interval: int
    replenish_start_turn: int
    spawn_extra_under_threat: bool


def in_bounds(ct: Controller, pos: Position) -> bool:
    return 0 <= pos.x < ct.get_map_width() and 0 <= pos.y < ct.get_map_height()


def _core_footprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def spawn_sites_toward(
    ct: Controller, core_pos: Position, goal: Position, map_id: int
) -> list[Position]:
    """Apron tiles around the full 2x2 core, closest to goal first using A*."""
    footprint = _core_footprint(core_pos)
    footprint_keys = {(t.x, t.y) for t in footprint}
    sites: list[Position] = []
    seen: set[tuple[int, int]] = set()
    for tile in footprint:
        for d in DIRECTIONS:
            cand = tile.add(d)
            key = (cand.x, cand.y)
            if key in seen or key in footprint_keys:
                continue
            if not in_bounds(ct, cand):
                continue
            seen.add(key)
            sites.append(cand)

    walls = set(WALL_LOCATIONS.get(map_id, []))

    def is_passable(p: Position) -> bool:
        if not in_bounds(ct, p):
            return False
        if (p.x, p.y) in walls:
            return False
        return True

    def is_goal_fn(cand: Position) -> bool:
        return (cand.x, cand.y) in footprint_keys

    path = astar(goal, is_passable=is_passable, is_goal=is_goal_fn, max_iters=1000)

    if path and len(path) >= 2:
        # path[0] = goal; path[-1] inside footprint; path[-2] = preferred apron.
        best_apron = path[-2]
        sites.sort(key=lambda p: (0 if p == best_apron else 1, p.distance_squared(goal)))
    else:
        sites.sort(key=lambda p: p.distance_squared(goal))

    return sites


def _is_deep_interior(ct: Controller, pos: Position) -> bool:
    """True if pos is in vision and cannot leave vision in one orthogonal step."""
    if not ct.is_in_vision(pos):
        return False
    for d in CARDINALS:
        n = pos.add(d)
        if not in_bounds(ct, n):
            continue
        if not ct.is_in_vision(n):
            return False
    return True


class Respawn:
    """Per-core spawn state and turn runner."""

    def __init__(self) -> None:
        self.num_spawned = 0
        # Intentional living target (growth only); replenish does not raise it.
        self.spawn_target = 0
        self.last_growth_round = -10**9
        self.replace_debt = 0
        self.living_est = 0
        # eid -> last Position seen in core vision (death detection).
        self._prev_vision_builders: dict[int, Position] = {}

    def observe_builder_deaths(self, ct: Controller) -> int:
        """Count ally builders that vanished from deep-interior core vision.

        Fog walk-outs do not count — only tiles where every in-bounds cardinal
        neighbor is still in vision (cannot leave vision in one step).
        """
        my_team = ct.get_team()
        current: dict[int, Position] = {}
        for eid in ct.get_nearby_entities():
            if ct.get_entity_type(eid) != EntityType.BUILDER_BOT:
                continue
            if ct.get_team(eid) != my_team:
                continue
            current[eid] = ct.get_position(eid)

        deaths = 0
        for eid, prev_pos in self._prev_vision_builders.items():
            if eid in current:
                continue
            if _is_deep_interior(ct, prev_pos):
                deaths += 1
        self._prev_vision_builders = current
        return deaths

    def run_core_spawn(
        self,
        ct: Controller,
        *,
        schedule: SpawnSchedule,
        threat: bool,
        map_id: int,
        goal: Position,
        hivemind,
    ) -> None:
        """One core turn: observe deaths, maybe spawn one builder."""
        round_n = ct.get_current_round()
        deaths = self.observe_builder_deaths(ct)
        if deaths:
            self.living_est = max(0, self.living_est - deaths)

        if round_n >= schedule.replenish_start_turn:
            self.replace_debt += deaths
            shortfall = max(0, self.spawn_target - self.living_est)
            self.replace_debt = min(self.replace_debt, shortfall)
        else:
            self.replace_debt = 0

        if ct.get_unit_count() >= GameConstants.MAX_TEAM_UNITS:
            return

        can_rush = (
            self.spawn_target < schedule.max_builders_early
            and self.living_est <= self.spawn_target
        )
        can_interval = (
            self.spawn_target >= schedule.max_builders_early
            and self.spawn_target < schedule.max_builders
            and self.living_est <= self.spawn_target
            and round_n >= schedule.first_interval_spawn
            and (round_n - schedule.first_interval_spawn) % schedule.spawn_interval
            == 0
        )
        can_late = (
            self.spawn_target >= schedule.max_builders
            and self.spawn_target < schedule.max_builders_threat
            and self.living_est <= self.spawn_target
            and round_n - self.last_growth_round >= schedule.late_spawn_interval
        )
        can_fallback = (
            self.spawn_target >= schedule.max_builders_threat
            and self.living_est <= self.spawn_target
            and round_n - self.last_growth_round >= schedule.fallback_spawn_interval
        )
        can_replenish = (
            self.replace_debt > 0
            and round_n >= schedule.replenish_start_turn
            and self.living_est < self.spawn_target
        )
        can_threat_spawn = (
            threat
            and schedule.spawn_extra_under_threat
            and self.spawn_target < schedule.max_builders_threat
            and self.living_est <= self.spawn_target
        )
        if (
            not can_rush
            and not can_interval
            and not can_late
            and not can_fallback
            and not can_replenish
            and not can_threat_spawn
        ):
            return

        if ct.get_global_resources() < ct.get_builder_bot_cost():
            return

        # Prefer replacing observed deaths over scheduled growth this turn.
        is_replenish = can_replenish
        core_pos = ct.get_position()
        for spawn_pos in spawn_sites_toward(ct, core_pos, goal, map_id=map_id):
            if hivemind.spawn_builder(ct, spawn_pos):
                self.num_spawned += 1
                self.living_est += 1
                if is_replenish:
                    self.replace_debt = max(0, self.replace_debt - 1)
                else:
                    self.spawn_target += 1
                    self.last_growth_round = round_n
                return
