"""Core builder spawn — schedule knobs come from main."""

from __future__ import annotations

from dataclasses import dataclass

from fcode import Controller, Direction, Position

from pathing import CARDINALS, inBounds


@dataclass(frozen=True)
class SpawnSchedule:
    max_builders_early: int
    max_builders: int
    max_builders_threat: int
    first_interval_spawn: int
    spawn_interval: int
    late_spawn_interval: int
    fallback_spawn_interval: int
    replenish_start_turn: int
    spawn_extra_under_threat: bool


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


class Respawn:
    def __init__(self) -> None:
        self.num_spawned = 0

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
        del map_id
        if ct.get_action_cooldown() != 0:
            return
        if ct.get_global_resources() < ct.get_builder_bot_cost():
            return

        round_n = ct.get_current_round()
        living = ct.read_store(4)
        # Slot 4 is HiveMind builder registry (spawn increments).
        target = schedule.max_builders_early
        if round_n >= schedule.first_interval_spawn:
            grown = 1 + max(
                0,
                (round_n - schedule.first_interval_spawn)
                // schedule.spawn_interval,
            )
            target = min(
                schedule.max_builders,
                schedule.max_builders_early - 1 + grown,
            )
        if living >= schedule.max_builders:
            if round_n >= schedule.first_interval_spawn + (
                schedule.max_builders - schedule.max_builders_early
            ) * schedule.spawn_interval:
                late = (
                    round_n
                    - (
                        schedule.first_interval_spawn
                        + (schedule.max_builders - schedule.max_builders_early)
                        * schedule.spawn_interval
                    )
                ) // schedule.late_spawn_interval
                target = min(
                    schedule.max_builders_threat,
                    schedule.max_builders + max(0, late),
                )

        if threat and schedule.spawn_extra_under_threat:
            target = max(target, schedule.max_builders_threat)

        if living >= target and self.num_spawned >= target:
            return
        if living >= target:
            return

        core_pos = ct.get_position()
        sites = _apronSites(ct, core_pos)
        sites.sort(
            key=lambda p: abs(p.x - goal.x) + abs(p.y - goal.y)
        )
        for site in sites:
            if hivemind.spawn_builder(ct, site):
                self.num_spawned += 1
                return
        # Fallback: any cardinal from NW tile
        for d in CARDINALS:
            site = core_pos.add(d)
            if hivemind.spawn_builder(ct, site):
                self.num_spawned += 1
                return
