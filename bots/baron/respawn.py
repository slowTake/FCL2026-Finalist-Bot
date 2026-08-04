"""Core builder spawn — schedule knobs come from main."""

from __future__ import annotations

from dataclasses import dataclass

from fcode import Controller, Direction, GameConstants, Position

from pathing import CARDINALS, inBounds


@dataclass(frozen=True)
class SpawnSchedule:
    max_builders_early: int
    max_builders: int
    spawn_interval: int
    spawn_interval_by_ti: tuple[tuple[int, int], ...] = ()


def _coreFootprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def _apronSites(ct: Controller, core_pos: Position) -> list[Position]:
    footprint = _coreFootprint(core_pos)
    keys = {(t.x, t.y) for t in footprint}
    sites: list[Position] = []
    seen: set[tuple[int, int]] = set()
    for tile in footprint:
        for d in Direction:
            if d == Direction.CENTRE:
                continue
            cand = tile.add(d)
            key = (cand.x, cand.y)
            if key in seen or key in keys:
                continue
            if not inBounds(ct, cand):
                continue
            seen.add(key)
            sites.append(cand)
    return sites


def _effectiveSpawnInterval(ct: Controller, schedule: SpawnSchedule) -> int:
    """Shorter interval when Ti bank is above configured tiers."""
    ti = ct.get_global_resources()
    best = schedule.spawn_interval
    for min_ti, interval in sorted(
        schedule.spawn_interval_by_ti, key=lambda t: t[0], reverse=True
    ):
        if ti >= min_ti:
            return max(1, interval)
    return max(1, best)


class Respawn:
    def __init__(self) -> None:
        self.num_spawned = 0

    def run_core_spawn(
        self,
        ct: Controller,
        *,
        schedule: SpawnSchedule,
        goal: Position,
        hivemind,
    ) -> None:
        """Rush early builders, then +1 every effective spawn_interval turns."""
        if ct.get_action_cooldown() != 0:
            return
        if ct.get_unit_count() >= GameConstants.MAX_TEAM_UNITS:
            return
        if ct.get_global_resources() < ct.get_builder_bot_cost():
            return

        round_n = ct.get_current_round()
        spawned = ct.read_store(4)  # HiveMind builder registry (lifetime spawned)
        interval = _effectiveSpawnInterval(ct, schedule)
        target = min(
            schedule.max_builders,
            schedule.max_builders_early + round_n // interval,
        )
        if spawned >= target:
            return

        core_pos = ct.get_position()
        sites = _apronSites(ct, core_pos)
        sites.sort(key=lambda p: abs(p.x - goal.x) + abs(p.y - goal.y))
        for site in sites:
            if hivemind.spawn_builder(ct, site):
                self.num_spawned += 1
                return
        for d in CARDINALS:
            site = core_pos.add(d)
            if hivemind.spawn_builder(ct, site):
                self.num_spawned += 1
                return
