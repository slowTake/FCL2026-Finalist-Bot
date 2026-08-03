"""Harrasser raid order for earl.

HARRASS task list — economy graduates via run_economy, or
ACTIVE_RAID_ORDER = "harrass" in main.py:
  - Until HARRASS_START_TURN: run home phalanx duties.
  - Then: siege uncovered enemy harvesters (reuse raider harvester siege).
  - Else: raider core gunner/sentinel siege (no near-core gate).
  - Else push enemy core.
"""

from __future__ import annotations

from fcode import Controller, Position

from attack import (
    _run_raid_core_siege,
    _run_siege_enemy_harvester,
)
from defence import run_phalanx

HARRASS_START_TURN = 50

# Named phases for the harrass raid order (priority-gated in run_harrasser).
HARRASS = (
    "phalanx_home",
    "siege_enemy_harvester",
    "siege_enemy_core",
    "push_enemy_core",
)


def run_harrasser(
    ct: Controller,
    *,
    my_index: int,
    local_map: dict,
    enemy_core: Position,
    ally_core: Position | None,
) -> Position | None:
    """HARRASS: phalanx home until 50, then harvester → core siege → push core."""
    if ct.get_current_round() < HARRASS_START_TURN:
        return run_phalanx(
            ct,
            my_index=my_index,
            local_map=local_map,
            enemy_core=enemy_core,
            ally_core=ally_core,
        )

    site = _run_siege_enemy_harvester(
        ct,
        my_index=my_index,
        local_map=local_map,
        enemy_core=enemy_core,
    )
    if site is not None:
        return site

    site = _run_raid_core_siege(
        ct,
        my_index=my_index,
        local_map=local_map,
        enemy_core=enemy_core,
        ally_core=ally_core,
    )
    if site is not None:
        return site
    return enemy_core
