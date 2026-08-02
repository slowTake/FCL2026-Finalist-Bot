"""Harrasser raid order for earl.

HARRASS task list — select via index 5 under phalanx order, or
ACTIVE_RAID_ORDER = "harrass" in main.py:
  - Until HARRASS_START_TURN: run home phalanx duties.
  - Then: siege uncovered enemy harvesters (reuse raider siege).
  - Else seek: hunt vision targets / push enemy core to find more harvesters.
"""

from __future__ import annotations

from fcode import Controller, Position

from attack import _run_raid_hunt, _run_siege_enemy_harvester
from defence import run_phalanx

HARRASS_START_TURN = 50

# Named phases for the harrass raid order (priority-gated in run_harrasser).
HARRASS = (
    "phalanx_home",
    "siege_enemy_harvester",
    "seek_harvesters",
)


def run_harrasser(
    ct: Controller,
    *,
    my_index: int,
    local_map: dict,
    enemy_core: Position,
    ally_core: Position | None,
) -> Position | None:
    """HARRASS: phalanx home until turn 50, then siege harvester → seek."""
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
    return _run_raid_hunt(ct, enemy_core=enemy_core)
