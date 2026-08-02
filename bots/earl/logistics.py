"""Economy logistics for earl: ore claim, harvester caps, inbound relays.

Named role phases live in ECONOMY. main.py gates builders onto this module via
run_economy / clear_relay (see role.md).
"""

from __future__ import annotations

from attack import _run_siege_enemy_harvester
from defence import available_sides, mark_relay_complete, side_depth1_tiles
from fcode import Controller, Direction, EntityType, Environment, Position
from features import ORE_LOCATIONS
from harrass import run_harrasser
from pathing import astar_from_seeds, manhattan

CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]
DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]

SLOT_HARVESTER_COUNT = 2
PATH_MAX_ITERS = 2000
ORE_RECHECK_INTERVAL = 50
MERGE_SAVE_MIN = 8
# Do not join onto allied belts this close to the core (apron / near-core funnel).
MERGE_MIN_CORE_CHEB = 4

# --- Settings ---
# If True, economy bots pause belt laying to leave titanium for early core spawns
USE_EARLY_TITANIUM_BUFFER = True
# Buffer amount on top of builder cost (e.g., 10 means wait for 60 total)
EARLY_TITANIUM_BUFFER_AMOUNT = 10
# Round number after which the buffer is ignored
EARLY_TITANIUM_BUFFER_END_TURN = 50
# ----------------

# Economy harvester caps by round (None = unlimited). Built count from slot 2.
HARVESTER_CAP_1 = 2
HARVESTER_CAP_2 = 3
HARVESTER_CAP_3 = 4
HARVESTER_CAP_4 = 5
HARVESTER_CAP_5 = 6
HARVESTER_CAP_TO_2_TURN = 50
HARVESTER_CAP_TO_3_TURN = 100
HARVESTER_CAP_TO_4_TURN = 200
HARVESTER_CAP_TO_5_TURN = 250
HARVESTER_CAP_UNLIMITED_TURN = 400
# After first inbound relay completes, wait this many turns then run harrass.
ECONOMY_TO_HARRASS_DELAY = 100

# Named phases for the economy role (priority-gated in run_economy).
ECONOMY = (
    "finish_relay",
    "harrass_after_relay",
    "snipe_enemy_harvester",
    "idle_ore_recheck",
    "ore_site",
    "heal",
    "move",
)


def _nearest_cardinal(d: Direction) -> Direction:
    return {
        Direction.NORTH: Direction.NORTH,
        Direction.NORTHEAST: Direction.NORTH,
        Direction.EAST: Direction.EAST,
        Direction.SOUTHEAST: Direction.EAST,
        Direction.SOUTH: Direction.SOUTH,
        Direction.SOUTHWEST: Direction.SOUTH,
        Direction.WEST: Direction.WEST,
        Direction.NORTHWEST: Direction.WEST,
        Direction.CENTRE: Direction.NORTH,
    }[d]


def _in_bounds(ct: Controller, pos: Position) -> bool:
    return 0 <= pos.x < ct.get_map_width() and 0 <= pos.y < ct.get_map_height()


def _core_footprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def _chebyshev_to_core_footprint(pos: Position, core_pos: Position) -> int:
    return min(
        max(abs(pos.x - t.x), abs(pos.y - t.y)) for t in _core_footprint(core_pos)
    )


def _ore_sort_key(bot, pos: Position, ore: Position) -> tuple[int, int]:
    """Prefer nearer ally core, then nearer builder (builder-only if no core)."""
    builder_d = pos.distance_squared(ore)
    if bot.core_pos is not None:
        return (_chebyshev_to_core_footprint(ore, bot.core_pos), builder_d)
    return (builder_d, 0)


def harvester_cap(round_n: int) -> int | None:
    """Max harvesters economy may seek this turn; None = no limit."""
    if round_n < HARVESTER_CAP_TO_2_TURN:
        return HARVESTER_CAP_1
    if round_n < HARVESTER_CAP_TO_3_TURN:
        return HARVESTER_CAP_2
    if round_n < HARVESTER_CAP_TO_4_TURN:
        return HARVESTER_CAP_3
    if round_n < HARVESTER_CAP_TO_5_TURN:
        return HARVESTER_CAP_4
    if round_n < HARVESTER_CAP_UNLIMITED_TURN:
        return HARVESTER_CAP_5
    return None


def economy_at_harvester_cap(ct: Controller, round_n: int) -> bool:
    """True when built harvester count has met the current staged cap."""
    cap = harvester_cap(round_n)
    if cap is None:
        return False
    return ct.read_store(SLOT_HARVESTER_COUNT) >= cap


def clear_relay(bot) -> None:
    """Drop inbound-relay state (raid / defender abort)."""
    bot.in_relay = False
    bot.relay_origin = None
    bot.relay_path = None
    bot.relay_stuck = 0
    bot.relay_built_to = -1
    bot.relay_merging = False


def enter_relay(bot, harvester_pos: Position) -> None:
    bot.in_relay = True
    bot.relay_origin = harvester_pos
    bot.relay_stuck = 0
    bot.relay_path = None
    bot.relay_built_to = -1
    bot.relay_merging = False
    bot.target = bot.core_pos
    bot.ore_site = None
    bot.skipped_ores.add((harvester_pos.x, harvester_pos.y))


def abort_relay(bot) -> None:
    clear_relay(bot)


def run_economy(bot, ct: Controller) -> None:
    """ECONOMY role turn: finish relays, graduate to harrass, snipe, ore, heal, move."""
    round_n = ct.get_current_round()
    at_cap = economy_at_harvester_cap(ct, round_n)

    if bot.in_relay:
        relay_toward_core(bot, ct)
        return

    if (
        bot.relay_finished_round is not None
        and round_n >= bot.relay_finished_round + ECONOMY_TO_HARRASS_DELAY
        and bot.enemy_core_pos is not None
    ):
        site = run_harrasser(
            ct,
            my_index=bot.hivemind.my_index,
            local_map=bot.hivemind.local_map,
            enemy_core=bot.enemy_core_pos,
            ally_core=bot.core_pos,
        )
        if site is not None:
            bot.ore_site = None
            bot.target = site
        bot._move_toward_target(ct, retarget=False)
        return

    if bot.enemy_core_pos is not None:
        site = _run_siege_enemy_harvester(
            ct,
            my_index=bot.hivemind.my_index,
            local_map=bot.hivemind.local_map,
            enemy_core=bot.enemy_core_pos,
        )
        if site is not None:
            bot.ore_site = None
            bot.target = site
            bot._move_toward_target(ct, retarget=False)
            return

    if round_n - bot.last_ore_recheck_round >= ORE_RECHECK_INTERVAL:
        # At cap, clear skips so ally chain repair can run; still no
        # empty/steal picks while capped (pick_ore_site).
        bot.skipped_ores.clear()
        bot.ore_site = None
        bot.last_ore_recheck_round = round_n
        bot.ore_recheck_allow_ally = True

    if not bot.in_relay:
        try_ore_site_task(bot, ct, at_cap=at_cap)

    if bot.in_relay:
        relay_toward_core(bot, ct)
        return

    if ct.get_action_cooldown() == 0:
        bot._try_heal(ct)

    bot._move_toward_target(ct)


def pick_economy_target(bot, ct: Controller) -> Position:
    """Nearest known empty/steal ore (to ally core); hold at core if capped."""
    pos = ct.get_position()
    at_cap = economy_at_harvester_cap(ct, ct.get_current_round())

    if not at_cap:
        site = pick_ore_site(bot, ct, pos, at_cap=False)
        if site is not None:
            bot.ore_site = site
            return site

        # Pre-fingerprint: visible uncovered ore
        if bot.hivemind.map_id == 0:
            best_ore = None
            best_dist = float("inf")
            for tile in ct.get_nearby_tiles():
                if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
                    continue
                if ct.get_tile_building_id(tile) is not None:
                    continue
                d = _ore_sort_key(bot, pos, tile)
                if d < best_dist:
                    best_dist = d
                    best_ore = tile
            if best_ore is not None:
                bot.ore_site = best_ore
                return best_ore

    if bot.core_pos is not None:
        return bot.core_pos
    return pos


# ------------------------------------------------------------------
# Ore site: build / steal / clear belts
# ------------------------------------------------------------------


def skip_ore(bot, ore: Position) -> None:
    bot.skipped_ores.add((ore.x, ore.y))
    if (
        bot.ore_site is not None
        and bot.ore_site.x == ore.x
        and bot.ore_site.y == ore.y
    ):
        bot.ore_site = None


def classify_ore_site(ct: Controller, ore: Position) -> str:
    bid = ct.get_tile_building_id(ore)
    if bid is None:
        return "empty"
    etype = ct.get_entity_type(bid)
    team = ct.get_team(bid)
    my_team = ct.get_team()
    if etype == EntityType.HARVESTER:
        return "harvester"  # ally or enemy — treat as ours, relay
    if etype in (EntityType.CONVEYOR, EntityType.SPLITTER):
        if team == my_team:
            return "allied_belt"
        return "enemy_belt"
    return "blocked"


def try_ore_site_task(bot, ct: Controller, *, at_cap: bool = False) -> None:
    pos = ct.get_position()
    if bot.ore_site is None:
        bot.ore_site = pick_ore_site(bot, ct, pos, at_cap=at_cap)
    if bot.ore_site is None:
        return

    bot.target = bot.ore_site
    ore = bot.ore_site
    if not ct.is_in_vision(ore):
        return

    kind = classify_ore_site(ct, ore)

    # At cap: only finish / repair existing harvester chains — drop seeks.
    if at_cap and kind != "harvester":
        bot.ore_site = None
        bot.target = None
        return

    if kind == "harvester":
        enter_relay(bot, ore)
        return

    if kind == "blocked":
        skip_ore(bot, ore)
        bot.ore_site = pick_ore_site(bot, ct, pos, at_cap=at_cap)
        if bot.ore_site is not None:
            bot.target = bot.ore_site
        return

    if kind == "allied_belt":
        if pos == ore:
            if ct.get_move_cooldown() == 0:
                for d in CARDINALS:
                    if ct.can_move(d):
                        ct.move(d)
                        return
            return
        if abs(pos.x - ore.x) + abs(pos.y - ore.y) == 1:
            if ct.can_destroy(ore):
                ct.destroy(ore)
        return

    if kind == "enemy_belt":
        if pos == ore:
            if ct.get_action_cooldown() == 0 and ct.can_fire(ore):
                ct.fire(ore)
        return

    if kind == "empty":
        if pos == ore:
            if ct.get_move_cooldown() == 0:
                for d in CARDINALS:
                    if ct.can_move(d):
                        ct.move(d)
                        return
            return
        if abs(pos.x - ore.x) + abs(pos.y - ore.y) == 1:
            if ct.get_action_cooldown() == 0 and ct.can_build_harvester(ore):
                ct.build_harvester(ore)
                count = ct.read_store(SLOT_HARVESTER_COUNT)
                ct.write_store(SLOT_HARVESTER_COUNT, count + 1)
                enter_relay(bot, ore)
        return


def pick_ore_site(
    bot, ct: Controller, pos: Position, *, at_cap: bool = False
) -> Position | None:
    """Nearest known ore: prefer empty, then steal; vision before fog.

    Sorted by (Chebyshev to ally core, builder distance). In-vision candidates
    beat fogged map ores. On idle recheck, fall back to ally harvesters so
    inbound belts can be rebuilt. At harvester cap, only ally (chain repair)
    candidates.
    """
    map_id = bot.hivemind.map_id
    if map_id <= 0:
        bot.ore_recheck_allow_ally = False
        return None
    my_team = ct.get_team()
    allow_ally = bot.ore_recheck_allow_ally
    bot.ore_recheck_allow_ally = False
    empty_vis: list[tuple[tuple[int, int], Position]] = []
    empty_fog: list[tuple[tuple[int, int], Position]] = []
    steal_vis: list[tuple[tuple[int, int], Position]] = []
    steal_fog: list[tuple[tuple[int, int], Position]] = []
    ally_vis: list[tuple[tuple[int, int], Position]] = []
    ally_fog: list[tuple[tuple[int, int], Position]] = []

    for ox, oy in ORE_LOCATIONS.get(map_id, []):
        if (ox, oy) in bot.skipped_ores:
            continue
        ore_pos = Position(ox, oy)
        in_vis = ct.is_in_vision(ore_pos)
        tile_info = bot.hivemind.local_map.get((ox, oy), {})
        ent = tile_info.get("entity")
        if (
            ent
            and ent.get("type") == EntityType.HARVESTER
            and ent.get("team") == my_team
        ):
            if allow_ally:
                bucket = ally_vis if in_vis else ally_fog
                bucket.append((_ore_sort_key(bot, pos, ore_pos), ore_pos))
            continue
        # Synced blocked tile with no entity — treat as already claimed
        if not ent and tile_info.get("passable") is False:
            continue

        d = _ore_sort_key(bot, pos, ore_pos)
        if ent and ent.get("type") == EntityType.HARVESTER:
            bucket = steal_vis if in_vis else steal_fog
        else:
            bucket = empty_vis if in_vis else empty_fog
        bucket.append((d, ore_pos))

    def _best(pool: list[tuple[tuple[int, int], Position]]) -> Position | None:
        if not pool:
            return None
        pool.sort(key=lambda t: t[0])
        return pool[0][1]

    if at_cap:
        if not allow_ally:
            return None
        return _best(ally_vis) or _best(ally_fog)

    site = _best(empty_vis) or _best(empty_fog)
    if site is not None:
        return site
    site = _best(steal_vis) or _best(steal_fog)
    if site is not None:
        return site
    if allow_ally:
        return _best(ally_vis) or _best(ally_fog)
    return None


# ------------------------------------------------------------------
# Conveyor path planning / belt helpers
# ------------------------------------------------------------------


def is_core_adjacent(
    ct: Controller, pos: Position, core_pos: Position | None
) -> bool:
    for d in CARDINALS:
        neighbor = pos.add(d)
        if not _in_bounds(ct, neighbor):
            continue
        if ct.is_in_vision(neighbor):
            bid = ct.get_tile_building_id(neighbor)
            if (
                bid is not None
                and ct.get_entity_type(bid) == EntityType.CORE
                and ct.get_team(bid) == ct.get_team()
            ):
                return True
    if core_pos is None:
        return False
    for tile in _core_footprint(core_pos):
        if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
            return True
    return False


def travel_facing(from_pos: Position, to_pos: Position) -> Direction:
    return _nearest_cardinal(from_pos.direction_to(to_pos))


def can_lay_infrastructure(ct: Controller, pos: Position) -> bool:
    if not _in_bounds(ct, pos) or not ct.is_in_vision(pos):
        return False
    return ct.is_tile_empty(pos) and ct.get_tile_env(pos) == Environment.EMPTY


def allied_belt_id(ct: Controller, pos: Position) -> int | None:
    if not ct.is_in_vision(pos):
        return None
    bid = ct.get_tile_building_id(pos)
    if bid is None:
        return None
    etype = ct.get_entity_type(bid)
    if etype not in (EntityType.CONVEYOR, EntityType.SPLITTER):
        return None
    if ct.get_team(bid) != ct.get_team():
        return None
    return bid


def enemy_belt_id(ct: Controller, pos: Position) -> int | None:
    if not ct.is_in_vision(pos):
        return None
    bid = ct.get_tile_building_id(pos)
    if bid is None:
        return None
    etype = ct.get_entity_type(bid)
    if etype not in (EntityType.CONVEYOR, EntityType.SPLITTER):
        return None
    if ct.get_team(bid) == ct.get_team():
        return None
    return bid


def is_layable_or_join(
    ct: Controller,
    pos: Position,
    *,
    ore_blocked: set[tuple[int, int]] | None = None,
) -> bool:
    if not _in_bounds(ct, pos):
        return False
    if ore_blocked is not None and (pos.x, pos.y) in ore_blocked:
        return False
    if not ct.is_in_vision(pos):
        return True
    if allied_belt_id(ct, pos) is not None:
        return True
    if enemy_belt_id(ct, pos) is not None:
        return True
    return can_lay_infrastructure(ct, pos)


def facing_into_core(pos: Position, core_pos: Position) -> Direction:
    best: Direction | None = None
    best_dist = 10**9
    for tile in _core_footprint(core_pos):
        dist = abs(pos.x - tile.x) + abs(pos.y - tile.y)
        if dist != 1:
            continue
        if dist < best_dist:
            best_dist = dist
            best = travel_facing(pos, tile)
    if best is not None and best != Direction.CENTRE:
        return best
    return _nearest_cardinal(pos.direction_to(core_pos))


def plan_conveyor_path(
    ct: Controller,
    start: Position,
    *,
    is_goal,
    ore_blocked: set[tuple[int, int]] | None = None,
    heuristic_to: Position | None = None,
) -> list[Position] | None:
    def ok(p: Position) -> bool:
        return is_layable_or_join(ct, p, ore_blocked=ore_blocked)

    seeds: list[Position] = []
    if ok(start):
        seeds.append(start)
    else:
        for d in CARDINALS:
            n = start.add(d)
            if ok(n):
                seeds.append(n)
    if not seeds:
        return None

    def h(p: Position) -> int:
        if heuristic_to is None:
            return 0
        return manhattan(p, heuristic_to)

    return astar_from_seeds(
        seeds,
        is_passable=ok,
        is_goal=is_goal,
        heuristic=h,
        max_iters=PATH_MAX_ITERS,
    )


def preferred_entry_tiles(
    ct: Controller, core_pos: Position, my_index: int
) -> list[Position]:
    """Depth-1 apron tiles on a side staggered by builder index."""
    sides = available_sides(ct, core_pos)
    if not sides:
        return []
    idx = max(my_index, 1) - 1
    side = sides[idx % len(sides)]
    return [t for t in side_depth1_tiles(core_pos, side) if _in_bounds(ct, t)]


def plan_conveyor_path_to_core(
    ct: Controller,
    start: Position,
    core_pos: Position,
    *,
    ore_blocked: set[tuple[int, int]] | None = None,
    my_index: int = 0,
) -> list[Position] | None:
    """Path to a core-adjacent tile; prefer nearest face, preferred side on ties.

    Preferred entry side (staggered by builder index) is only used when it is
    the same length as the shortest any-adjacent path — avoids routing around
    the core and destroying inbound belts that already go straight in.
    """
    nearest = plan_conveyor_path(
        ct,
        start,
        is_goal=lambda p: is_core_adjacent(ct, p, core_pos),
        ore_blocked=ore_blocked,
        heuristic_to=core_pos,
    )
    preferred = preferred_entry_tiles(ct, core_pos, my_index)
    if not preferred:
        return nearest
    want = {(t.x, t.y) for t in preferred}

    def preferred_goal(p: Position) -> bool:
        return (p.x, p.y) in want and is_core_adjacent(ct, p, core_pos)

    preferred_path = plan_conveyor_path(
        ct,
        start,
        is_goal=preferred_goal,
        ore_blocked=ore_blocked,
        heuristic_to=core_pos,
    )
    if preferred_path is None:
        return nearest
    if nearest is None:
        return preferred_path
    # Tiebreak only — never take a longer detour to the assigned face.
    if len(preferred_path) <= len(nearest):
        return preferred_path
    return nearest


def is_merge_goal(
    ct: Controller,
    pos: Position,
    *,
    origin: Position,
    core_pos: Position,
) -> bool:
    """Visible allied belt far enough from core; not origin or its neighbors."""
    if allied_belt_id(ct, pos) is None:
        return False
    if pos.x == origin.x and pos.y == origin.y:
        return False
    for d in CARDINALS:
        n = origin.add(d)
        if pos.x == n.x and pos.y == n.y:
            return False
    if _chebyshev_to_core_footprint(pos, core_pos) < MERGE_MIN_CORE_CHEB:
        return False
    return True


def plan_conveyor_path_to_merge(
    ct: Controller,
    start: Position,
    core_pos: Position,
    *,
    origin: Position,
    ore_blocked: set[tuple[int, int]] | None = None,
) -> list[Position] | None:
    """Shortest layable path onto a visible allied belt (join target inclusive)."""
    return plan_conveyor_path(
        ct,
        start,
        is_goal=lambda p: is_merge_goal(
            ct, p, origin=origin, core_pos=core_pos
        ),
        ore_blocked=ore_blocked,
        heuristic_to=core_pos,
    )


def pick_relay_path(
    ct: Controller,
    start: Position,
    core_pos: Position,
    *,
    origin: Position,
    ore_blocked: set[tuple[int, int]] | None = None,
    my_index: int = 0,
) -> tuple[list[Position] | None, bool]:
    """Prefer a merge spur when it saves at least MERGE_SAVE_MIN tiles vs core."""
    core_path = plan_conveyor_path_to_core(
        ct,
        start,
        core_pos,
        ore_blocked=ore_blocked,
        my_index=my_index,
    )
    merge_path = plan_conveyor_path_to_merge(
        ct, start, core_pos, origin=origin, ore_blocked=ore_blocked
    )
    if merge_path is not None and core_path is not None:
        if len(merge_path) <= len(core_path) - MERGE_SAVE_MIN:
            return merge_path, True
        return core_path, False
    if merge_path is not None:
        return merge_path, True
    return core_path, False


def path_travel_dir(
    path: list[Position], index: int, core_pos: Position
) -> Direction:
    """Inbound path faces next hop toward core."""
    tile = path[index]
    if index + 1 < len(path):
        return travel_facing(tile, path[index + 1])
    return facing_into_core(tile, core_pos)


def ensure_belt_on_path(
    ct: Controller,
    tile: Position,
    travel_dir: Direction,
) -> str:
    """Join correct allied facing; destroy+rebuild wrong-facing; else build."""
    if travel_dir == Direction.CENTRE:
        return "failed"

    bid = allied_belt_id(ct, tile)
    if bid is not None:
        if ct.get_direction(bid) == travel_dir:
            return "joined"
        if ct.can_destroy(tile):
            ct.destroy(tile)
        else:
            return "failed"
        if ct.can_build_conveyor(tile, travel_dir):
            ct.build_conveyor(tile, travel_dir)
            return "built"
        return "failed"

    if not can_lay_infrastructure(ct, tile):
        return "failed"
    if ct.get_global_resources() < ct.get_conveyor_cost():
        return "failed"
    if ct.can_build_conveyor(tile, travel_dir):
        ct.build_conveyor(tile, travel_dir)
        return "built"
    return "failed"


def belt_faces(ct: Controller, tile: Position, expected: Direction) -> bool:
    bid = allied_belt_id(ct, tile)
    if bid is None:
        return False
    return ct.get_direction(bid) == expected


def first_incomplete_index(
    ct: Controller,
    path: list[Position],
    core_pos: Position,
    *,
    start: int = 0,
    end: int | None = None,
) -> int | None:
    """First incomplete index in [start, end). end=None means full path."""
    limit = len(path) if end is None else min(end, len(path))
    for i in range(max(0, start), limit):
        want = path_travel_dir(path, i, core_pos)
        if not belt_faces(ct, path[i], want):
            return i
    return None


def extend_relay_built_to(
    ct: Controller,
    path: list[Position],
    core_pos: Position,
    built_to: int,
    *,
    end: int | None = None,
) -> int:
    """Advance watermark only through contiguous correct belts in vision.

    Never skips fog or gaps — a later visible correct belt must not mark
    unfinished mid-path tiles complete. end caps the scan (merge spurs omit
    the join belt).
    """
    limit = len(path) if end is None else min(end, len(path))
    i = max(-1, built_to) + 1
    while i < limit:
        tile = path[i]
        if not ct.is_in_vision(tile):
            break
        want = path_travel_dir(path, i, core_pos)
        if not belt_faces(ct, tile, want):
            break
        built_to = i
        i += 1
    return built_to


# ------------------------------------------------------------------
# Inbound relay turn
# ------------------------------------------------------------------


def _ore_blocked_set(bot) -> set[tuple[int, int]]:
    map_id = bot.hivemind.map_id
    if map_id <= 0 or map_id not in ORE_LOCATIONS:
        return set()
    return set(ORE_LOCATIONS[map_id])


def _relay_path_ok(
    ct: Controller,
    path: list[Position],
    ore_blocked: set[tuple[int, int]],
) -> bool:
    for tile in path:
        if allied_belt_id(ct, tile) is not None:
            continue
        if not is_layable_or_join(ct, tile, ore_blocked=ore_blocked):
            return False
    return True


def relay_toward_core(bot, ct: Controller) -> None:
    if bot.core_pos is None or bot.relay_origin is None:
        abort_relay(bot)
        return

    ore_blocked = _ore_blocked_set(bot)
    pos = ct.get_position()
    origin_visible = ct.is_in_vision(bot.relay_origin)
    path = bot.relay_path
    # Merge paths: build spur only; last tile is the existing join belt.
    spur_end = (
        len(path) - 1
        if path is not None and bot.relay_merging and len(path) > 0
        else None
    )

    # Raise watermark only through contiguous visible correct belts.
    if path is not None:
        bot.relay_built_to = extend_relay_built_to(
            ct,
            path,
            bot.core_pos,
            bot.relay_built_to,
            end=spur_end,
        )

    if not origin_visible:
        if path:
            if bot.relay_built_to >= 0:
                bot.relay_origin = path[bot.relay_built_to]
            else:
                # Stay anchored at the builder; do not invent watermark
                # progress from nearest path tile (false-completes relays).
                bot.relay_origin = pos
        else:
            bot.relay_origin = pos
    else:
        if path is not None and not _relay_path_ok(ct, path, ore_blocked):
            # Reseed mid-route from last known belt before discarding indices.
            if bot.relay_built_to >= 0:
                bot.relay_origin = path[bot.relay_built_to]
            path = None
            bot.relay_path = None
            bot.relay_built_to = -1
            bot.relay_merging = False

    if path is None:
        path, merging = pick_relay_path(
            ct,
            bot.relay_origin,
            bot.core_pos,
            origin=bot.relay_origin,
            ore_blocked=ore_blocked,
            my_index=bot.hivemind.my_index,
        )
        bot.relay_path = path
        bot.relay_merging = merging
        if path is None:
            abort_relay(bot)
            return
        spur_end = len(path) - 1 if merging and len(path) > 0 else None
        # New path indices: re-derive contiguous prefix (e.g. reseed belt).
        bot.relay_built_to = extend_relay_built_to(
            ct, path, bot.core_pos, -1, end=spur_end
        )

    # Never scan behind the high-water mark (fogged early tiles look incomplete).
    work_from = bot.relay_built_to + 1
    idx = first_incomplete_index(
        ct, path, bot.core_pos, start=work_from, end=spur_end
    )
    if idx is None:
        if bot.relay_merging:
            join = path[-1]
            if ct.is_in_vision(join) and allied_belt_id(ct, join) is None:
                # Join target gone — replan next turn.
                bot.relay_path = None
                bot.relay_built_to = -1
                bot.relay_merging = False
                return
        mark_relay_complete(ct)
        if bot.relay_finished_round is None:
            bot.relay_finished_round = ct.get_current_round()
        clear_relay(bot)
        return

    tile = path[idx]
    travel_dir = path_travel_dir(path, idx, bot.core_pos)

    # Enemy belts: stand-and-fire, then lay ours next turn
    if enemy_belt_id(ct, tile) is not None:
        if pos == tile:
            if ct.get_action_cooldown() == 0 and ct.can_fire(tile):
                ct.fire(tile)
                bot.relay_stuck = 0
            else:
                bot.relay_stuck += 1
                if bot.relay_stuck >= 3:
                    abort_relay(bot)
            return
        if ct.get_move_cooldown() != 0:
            return
        if bot._move_toward_pos(ct, tile):
            bot.relay_stuck = 0
        else:
            bot.relay_stuck += 1
            if bot.relay_stuck >= 3:
                abort_relay(bot)
        return

    # Standing on the work tile — step off so we can destroy/build adjacent
    if pos == tile:
        if ct.get_move_cooldown() != 0:
            return
        for d in CARDINALS:
            if ct.can_move(d):
                ct.move(d)
                bot.relay_stuck = 0
                return
        bot.relay_stuck += 1
        if bot.relay_stuck >= 3:
            abort_relay(bot)
        return

    adjacent = abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1
    if adjacent:
        # Prevent early belt building from starving the core's initial rush
        if USE_EARLY_TITANIUM_BUFFER and ct.get_current_round() < EARLY_TITANIUM_BUFFER_END_TURN:
            if ct.get_global_resources() < ct.get_builder_bot_cost() + EARLY_TITANIUM_BUFFER_AMOUNT:
                return
        result = ensure_belt_on_path(ct, tile, travel_dir)
        if result == "failed":
            bot.relay_stuck += 1
            if bot.relay_stuck >= 3:
                abort_relay(bot)
            return
        bot.relay_stuck = 0
        bot.relay_built_to = max(bot.relay_built_to, idx)
        if ct.get_move_cooldown() == 0:
            step = _nearest_cardinal(pos.direction_to(tile))
            if step != Direction.CENTRE and ct.can_move(step):
                ct.move(step)
        return

    if ct.get_move_cooldown() != 0:
        return
    if bot._move_toward_pos(ct, tile):
        bot.relay_stuck = 0
    else:
        bot.relay_stuck += 1
        if bot.relay_stuck >= 3:
            abort_relay(bot)
