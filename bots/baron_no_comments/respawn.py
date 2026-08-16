
from __future__ import annotations

from dataclasses import dataclass

from fcode import (
    Controller,
    Direction,
    EntityType,
    GameConstants,
    Position,
)

from geometry import coreFootprint, manhattanToCore
from pathing import CARDINALS, inBounds


@dataclass(frozen=True)
class SpawnSchedule:
    max_builders_early: int
    max_builders: int
    builder_target_by_ti: tuple[tuple[int, int], ...] = ()
    growth_delay_turns: int = 0


def _apronSites(ct: Controller, core_pos: Position) -> list[Position]:
    footprint = coreFootprint(core_pos)
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


def _builderTarget(ct: Controller, schedule: SpawnSchedule) -> int:
    ti = ct.get_global_resources()
    target = schedule.max_builders_early
    for min_ti, count in sorted(
        schedule.builder_target_by_ti, key=lambda t: t[0], reverse=True
    ):
        if ti >= min_ti:
            target = count
            break

    if schedule.growth_delay_turns and schedule.growth_delay_turns > 0:
        if ct.get_current_round() < schedule.growth_delay_turns:
            target = min(target, schedule.max_builders_early)
    return max(schedule.max_builders_early, min(target, schedule.max_builders))


def _safeSites(
    ct: Controller, sites: list[Position]
) -> tuple[list[Position], list[Position]]:
    from offense import _enemyGunners, _tileUnderGunnerFire

    gunners = _enemyGunners(ct)
    if not gunners:
        return sites, sites
    safe = [s for s in sites if not _tileUnderGunnerFire(ct, s, gunners)]
    return safe, sites


def _aliveBuilders(hivemind, ct: Controller) -> int:
    me = ct.get_team()
    alive = 0
    for cell in hivemind.local_map.values():
        ent = cell.get("entity")
        if not ent:
            continue
        if ent.get("type") == EntityType.BUILDER_BOT and ent.get("team") == me:
            alive += 1
    return min(alive, ct.read_store(4))


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
        if ct.get_action_cooldown() != 0:
            return
        if ct.get_unit_count() >= GameConstants.MAX_TEAM_UNITS:
            return
        if ct.get_global_resources() < ct.get_builder_bot_cost():
            return

        if _aliveBuilders(hivemind, ct) >= _builderTarget(ct, schedule):
            return

        core_pos = ct.get_position()

        predicted_idx = ct.read_store(4) + 1
        spawn_goal = goal
        if predicted_idx % 5 in (3, 4) and hivemind.map_id:
            from features import oreSideBuckets

            left, right = oreSideBuckets(hivemind.map_id, core_pos, goal)

            def _nearestInPool(pool: list[Position]) -> int:
                return min(
                    (manhattanToCore(o, core_pos) for o in pool), default=10**9
                )

            near_pool, far_pool = (
                (left, right) if _nearestInPool(left) <= _nearestInPool(right)
                else (right, left)
            )
            side_pool = near_pool if (predicted_idx // 5) % 2 == 0 else far_pool
            if not side_pool:
                side_pool = far_pool or near_pool
            if side_pool:
                spawn_goal = min(side_pool, key=lambda o: manhattanToCore(o, core_pos))

        sites = _apronSites(ct, core_pos)
        sites.sort(key=lambda p: abs(p.x - spawn_goal.x) + abs(p.y - spawn_goal.y))
        safe_sites, all_sites = _safeSites(ct, sites)

        cardinal_sites = [core_pos.add(d) for d in CARDINALS]
        safe_cardinals, all_cardinals = _safeSites(ct, cardinal_sites)

        for site in safe_sites:
            if hivemind.spawn_builder(ct, site):
                self.num_spawned += 1
                return
        for site in safe_cardinals:
            if hivemind.spawn_builder(ct, site):
                self.num_spawned += 1
                return
        for site in all_sites:
            if hivemind.spawn_builder(ct, site):
                self.num_spawned += 1
                return
        for site in all_cardinals:
            if hivemind.spawn_builder(ct, site):
                self.num_spawned += 1
                return