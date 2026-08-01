"""Core defence role for earl builders.

Late spawns (indices >= DEFENDER_MIN_INDEX) and mid-game converts (1, 5 from
ATTACK_TURN) stay near the ally core: heal friendlies, then fire adjacent
enemies. Gunner building is deferred.
"""

from __future__ import annotations

from attack import ATTACK_TURN
from fcode import Controller, Direction, EntityType, Position

DEFENDER_MIN_INDEX = 6  # indices 6..9
MIDGAME_DEFENDER_INDICES = frozenset({1, 5})

CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]


def is_defender(my_index: int, round_n: int | None = None) -> bool:
    if my_index >= DEFENDER_MIN_INDEX:
        return True
    return (
        my_index in MIDGAME_DEFENDER_INDICES
        and round_n is not None
        and round_n >= ATTACK_TURN
    )


def _core_footprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def _defence_hold_target(ct: Controller, core_pos: Position) -> Position:
    """Prefer an empty apron tile; fall back to core NW so mover walks adjacent."""
    pos = ct.get_position()
    footprint = {(t.x, t.y) for t in _core_footprint(core_pos)}
    best: Position | None = None
    best_dist = float("inf")
    w, h = ct.get_map_width(), ct.get_map_height()

    for tile in _core_footprint(core_pos):
        for d in CARDINALS:
            cand = tile.add(d)
            key = (cand.x, cand.y)
            if key in footprint:
                continue
            if not (0 <= cand.x < w and 0 <= cand.y < h):
                continue
            if ct.is_in_vision(cand) and not ct.is_tile_empty(cand):
                # Still a valid linger spot if we're already on it
                if pos.x != cand.x or pos.y != cand.y:
                    continue
            dist = pos.distance_squared(cand)
            if dist < best_dist:
                best_dist = dist
                best = cand
    return best if best is not None else core_pos


def _try_heal(ct: Controller) -> bool:
    if ct.get_action_cooldown() != 0:
        return False
    pos = ct.get_position()
    for d in Direction:
        if d == Direction.CENTRE:
            continue
        check = pos.add(d)
        # Heal is orthogonal-only for builders
        if d not in CARDINALS:
            continue
        if ct.can_heal(check):
            ct.heal(check)
            return True
    return False


def _try_adjacent_fire(ct: Controller) -> bool:
    """Fire orthogonal adjacent enemies: prefer builders, then any enemy building."""
    if ct.get_action_cooldown() != 0:
        return False
    pos = ct.get_position()
    my_team = ct.get_team()
    builder_tile: Position | None = None
    other_tile: Position | None = None

    for d in CARDINALS:
        tile = pos.add(d)
        if not ct.can_fire(tile):
            continue
        bot = ct.get_tile_builder_bot_id(tile)
        if bot is not None and ct.get_team(bot) != my_team:
            builder_tile = tile
            break
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            continue
        if ct.get_team(bid) == my_team:
            continue
        if other_tile is None:
            other_tile = tile

    target = builder_tile if builder_tile is not None else other_tile
    if target is None:
        return False
    ct.fire(target)
    return True


def run_defender(ct: Controller, *, core_pos: Position | None) -> Position | None:
    """Heal, then fire adjacent enemies. Returns move target near the core."""
    if not _try_heal(ct):
        _try_adjacent_fire(ct)

    if core_pos is None:
        return None
    return _defence_hold_target(ct, core_pos)
