"""Harrasser raid order for earl.

HARRASS task list (even my_index, enemy core known) — select via
ACTIVE_RAID_ORDER = "harrass" in main.py:
  - Top priority: siege uncovered enemy harvesters (reuse raider siege).
  - Else seek: hunt vision targets / push enemy core to find more harvesters.
"""

from __future__ import annotations

from fcode import Controller, Position

from attack import _run_raid_hunt, _run_siege_enemy_harvester

# Named phases for the harrass raid order (priority-gated in run_harrasser).
HARRASS = (
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
    """HARRASS: siege enemy harvester → seek more targets toward enemy core."""
    del ally_core  # siege/hunt helpers take enemy_core; signature matches peers
    site = _run_siege_enemy_harvester(
        ct,
        my_index=my_index,
        local_map=local_map,
        enemy_core=enemy_core,
    )
    if site is not None:
        return site
    return _run_raid_hunt(ct, enemy_core=enemy_core)
