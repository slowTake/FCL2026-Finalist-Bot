"""Core builder spawn and replenish — schedule knobs come from main."""

from __future__ import annotations

from dataclasses import dataclass

from fcode import Controller, Position


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


class Respawn:
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
        del ct, schedule, threat, map_id, goal, hivemind
