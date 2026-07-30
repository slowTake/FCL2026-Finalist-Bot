"""turtleHive — turtle + HiveMind + committed relays + 2-deep core apron.

Lean map-aware baseline: HiveMind from components/, BFS movement, magnum-style
committed relays (outbound core→ore join/build-only, inbound harvester→core with
facing fix-up), and a 2-deep inward-facing conveyor apron around the core
(N/E/S/W only — no splitters).

Store slots:
  0  SLOT_SPAWN_CREDITS   Earned by completing an inbound relay (Player)
  1  SLOT_ORPHAN_RELAY    Packed harvester origin needing a relay (Player)
  2  SLOT_HARVESTER_COUNT Total harvesters (Player)
  3  SLOT_ORE_LOCATION    Packed uncovered ore (Player)
  4  SLOT_FAR_ORE         One-off start: packed far ore for builder 1/2 (Player)
  5  SLOT_SIEGE_A         Packed siege sentinel A (Player, attack phase)
  6  SLOT_SIEGE_B         Packed siege sentinel B (Player, attack phase)
  7–15                    HiveMind-owned — never write from Player

Ally core position is read from HiveMind slot 15 via unpack_cores.
"""

from __future__ import annotations

import random
from collections import deque

from fcode import Controller, Direction, EntityType, Environment, Position
from features import ORE_LOCATIONS
from hivemind import CoreState, HiveMind, unpack_cores

DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]
CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]

SLOT_SPAWN_CREDITS = 0
SLOT_ORPHAN_RELAY = 1
SLOT_HARVESTER_COUNT = 2
SLOT_ORE_LOCATION = 3
SLOT_FAR_ORE = 4
SLOT_SIEGE_A = 5
SLOT_SIEGE_B = 6
SLOT_PACKED_CORES = 15

MAX_BUILDERS = 5
TARGET_HARVESTERS = 3
TARGET_HARVESTERS_APRON = 1  # start core apron after first harvester
BFS_MAX_ITERS = 400
FAR_ORE_MIN_DIST = 5  # one-off start: claimer picks nearest ore at least this far
SPAWN_CREDIT_INTERVAL = 50  # +1 spawn credit every N turns

ATTACK_TURN = 100
MAX_SIEGE_SENTINELS = 10
SENTINEL_RANGE_SQ = 32
SENTINEL_PLACE_MIN_SQ = 16  # d=4 → 16; enough outer-face slots for 10 turrets

# NOTE: gunners off while debugging belt logistics / core apron.
# Re-enable once relays + 2-deep apron reliably deliver mined Ti.
BUILD_GUNNERS = False

# NOTE: orphan relay claim off for a test — builders won't pick up aborted relays.
CLAIM_ORPHAN_RELAY = False

# NOTE: relay abort off for a test — stuck / no-path / guard won't orphan or clear relay.
ABORT_RELAY = False

# NOTE: pave-while-move off — outbound/inbound relays own belts toward ore/core.
PAVE_WHILE_MOVE = False

# NOTE: builder 1 far-ore job walks only — no outbound pave / apron en route.
BUILDER1_PAVE_EN_ROUTE = False


def pack_pos(pos: Position) -> int:
    return ((pos.x + 1) << 16) | (pos.y + 1)


def unpack_pos(val: int) -> Position | None:
    if val == 0:
        return None
    return Position((val >> 16) - 1, (val & 0xFFFF) - 1)


def nearest_cardinal(d: Direction) -> Direction:
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


def in_bounds(ct: Controller, pos: Position) -> bool:
    return 0 <= pos.x < ct.get_map_width() and 0 <= pos.y < ct.get_map_height()


# ---------------------------------------------------------------------------
# Core footprint + 2-deep inward apron (N/E/S/W only, no corners, no splitters)
# ---------------------------------------------------------------------------

def _core_footprint(core_pos: Position) -> list[Position]:
    """Core is a 2x2; core_pos is the footprint origin (min x/y)."""
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def core_min_manhattan(pos: Position, core_pos: Position) -> int:
    """Min Manhattan distance from pos to any tile of the core 2x2 footprint."""
    return min(
        abs(pos.x - tile.x) + abs(pos.y - tile.y)
        for tile in _core_footprint(core_pos)
    )


def spawn_dirs_toward_center(ct: Controller, core_pos: Position) -> list[Direction]:
    """Prefer spawn tiles closer to map center, then fall back to farther dirs."""
    center = Position(ct.get_map_width() // 2, ct.get_map_height() // 2)
    return sorted(
        DIRECTIONS,
        key=lambda d: core_pos.add(d).distance_squared(center),
    )


def core_apron_specs(core_pos: Position) -> list[tuple[Position, Direction]]:
    """2-deep N/E/S/W apron tiles with inward facing.

    Depth 1 touches the 2x2 core; depth 2 is one tile further out — the easy
    connect ring for incoming relays. Corners are left empty on purpose.
    """
    cx, cy = core_pos.x, core_pos.y
    tiles: list[tuple[Position, Direction]] = []
    for depth in (1, 2):
        for dx in (0, 1):
            tiles.append((Position(cx + dx, cy - depth), Direction.SOUTH))
            tiles.append((Position(cx + dx, cy + 1 + depth), Direction.NORTH))
        for dy in (0, 1):
            tiles.append((Position(cx - depth, cy + dy), Direction.EAST))
            tiles.append((Position(cx + 1 + depth, cy + dy), Direction.WEST))
    return tiles


def apron_facing(pos: Position, core_pos: Position) -> Direction | None:
    for tile, facing in core_apron_specs(core_pos):
        if tile.x == pos.x and tile.y == pos.y:
            return facing
    return None


def is_apron_tile(pos: Position, core_pos: Position | None) -> bool:
    if core_pos is None:
        return False
    return apron_facing(pos, core_pos) is not None


def is_core_adjacent(
    ct: Controller, pos: Position, core_pos: Position | None
) -> bool:
    """True if pos orthogonally touches any Core footprint tile."""
    for d in CARDINALS:
        neighbor = pos.add(d)
        if not in_bounds(ct, neighbor):
            continue
        if ct.is_in_vision(neighbor):
            bid = ct.get_tile_building_id(neighbor)
            if bid is not None and ct.get_entity_type(bid) == EntityType.CORE:
                return True
    if core_pos is None:
        return False
    for tile in _core_footprint(core_pos):
        if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
            return True
    return False


# ---------------------------------------------------------------------------
# Magnum-style belt path planning / lay helpers
# ---------------------------------------------------------------------------

def travel_facing(from_pos: Position, to_pos: Position) -> Direction:
    return nearest_cardinal(from_pos.direction_to(to_pos))


def can_lay_infrastructure(ct: Controller, pos: Position) -> bool:
    if not in_bounds(ct, pos):
        return False
    if not ct.is_in_vision(pos):
        return False
    return (
        ct.is_tile_empty(pos)
        and ct.get_tile_env(pos) == Environment.EMPTY
    )


def _allied_belt_id(ct: Controller, pos: Position) -> int | None:
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


def _enemy_belt_id(ct: Controller, pos: Position) -> int | None:
    """Enemy conveyor/splitter on pos, or None."""
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
    if not in_bounds(ct, pos):
        return False
    if ore_blocked is not None and (pos.x, pos.y) in ore_blocked:
        return False
    if not ct.is_in_vision(pos):
        return True
    if _allied_belt_id(ct, pos) is not None:
        return True
    # Enemy belts are clearable (stand-and-fire) then we lay ours
    if _enemy_belt_id(ct, pos) is not None:
        return True
    return can_lay_infrastructure(ct, pos)


def facing_into_core(pos: Position, core_pos: Position) -> Direction:
    """Travel facing from a Core-adjacent / apron tile into the Core."""
    apron = apron_facing(pos, core_pos)
    if apron is not None:
        return apron
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
    return nearest_cardinal(pos.direction_to(core_pos))


def plan_conveyor_path(
    ct: Controller,
    start: Position,
    *,
    is_goal,
    ore_blocked: set[tuple[int, int]] | None = None,
) -> list[Position] | None:
    """BFS over layable/join tiles from start until is_goal(pos) is true."""
    w = ct.get_map_width()
    h = ct.get_map_height()

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

    parent: dict[tuple[int, int], tuple[int, int] | None] = {}
    q: deque[Position] = deque()
    for s in seeds:
        key = (s.x, s.y)
        if key in parent:
            continue
        parent[key] = None
        q.append(s)

    goal: Position | None = None
    while q:
        cur = q.popleft()
        if is_goal(cur):
            goal = cur
            break
        for d in CARDINALS:
            nxt = cur.add(d)
            if not (0 <= nxt.x < w and 0 <= nxt.y < h):
                continue
            nkey = (nxt.x, nxt.y)
            if nkey in parent:
                continue
            if not ok(nxt):
                continue
            parent[nkey] = (cur.x, cur.y)
            q.append(nxt)

    if goal is None:
        return None

    path_rev: list[Position] = []
    key: tuple[int, int] | None = (goal.x, goal.y)
    while key is not None:
        path_rev.append(Position(key[0], key[1]))
        key = parent[key]
    path_rev.reverse()
    return path_rev


def plan_conveyor_path_to_core(
    ct: Controller,
    start: Position,
    core_pos: Position,
    *,
    ore_blocked: set[tuple[int, int]] | None = None,
) -> list[Position] | None:
    """BFS from start to a core-adjacent (depth-1) tile.

    Goal is the inner apron ring that feeds the core. The outer (depth-2) apron
    is joinable mid-path so relays plug into it, then continue one hop inward.
    """
    return plan_conveyor_path(
        ct,
        start,
        is_goal=lambda p: is_core_adjacent(ct, p, core_pos),
        ore_blocked=ore_blocked,
    )


def plan_conveyor_path_to_ore(
    ct: Controller,
    start: Position,
    ore_pos: Position,
    *,
    ore_blocked: set[tuple[int, int]] | None = None,
) -> list[Position] | None:
    """BFS from start (core) to a layable/join cardinal neighbor of ore_pos."""

    def is_goal(p: Position) -> bool:
        if abs(p.x - ore_pos.x) + abs(p.y - ore_pos.y) != 1:
            return False
        return is_layable_or_join(ct, p, ore_blocked=ore_blocked)

    return plan_conveyor_path(
        ct, start, is_goal=is_goal, ore_blocked=ore_blocked
    )


def sentinel_feed_neighbors(
    ct: Controller,
    sentinel_pos: Position,
    facing: Direction,
    *,
    ore_blocked: set[tuple[int, int]] | None = None,
) -> list[Position]:
    """Cardinal neighbors of a sentinel excluding the facing-side tile (no Ti input there)."""
    face_tile = sentinel_pos.add(facing)
    out: list[Position] = []
    for d in CARDINALS:
        n = sentinel_pos.add(d)
        if n.x == face_tile.x and n.y == face_tile.y:
            continue
        if not in_bounds(ct, n):
            continue
        if is_layable_or_join(ct, n, ore_blocked=ore_blocked):
            out.append(n)
    return out


def sentinel_has_inbound_belt(
    ct: Controller, sentinel_pos: Position, facing: Direction
) -> bool:
    """True if a feed-side allied belt already faces into the sentinel."""
    face_tile = sentinel_pos.add(facing)
    for d in CARDINALS:
        n = sentinel_pos.add(d)
        if n.x == face_tile.x and n.y == face_tile.y:
            continue
        if not in_bounds(ct, n):
            continue
        if not ct.is_in_vision(n):
            continue
        want = facing_toward(n, sentinel_pos)
        if belt_faces(ct, n, want):
            return True
    return False


def plan_conveyor_path_to_sentinel(
    ct: Controller,
    start: Position,
    sentinel_pos: Position,
    facing: Direction,
    *,
    ore_blocked: set[tuple[int, int]] | None = None,
) -> list[Position] | None:
    """BFS harvester → feed-side neighbor of sentinel (inbound_to_sentinel)."""
    goals = {
        (p.x, p.y)
        for p in sentinel_feed_neighbors(
            ct, sentinel_pos, facing, ore_blocked=ore_blocked
        )
    }
    if not goals:
        return None

    def is_goal(p: Position) -> bool:
        return (p.x, p.y) in goals

    return plan_conveyor_path(
        ct, start, is_goal=is_goal, ore_blocked=ore_blocked
    )


def plan_conveyor_path_from_sentinel(
    ct: Controller,
    sentinel_pos: Position,
    harvester_pos: Position,
    facing: Direction,
    *,
    ore_blocked: set[tuple[int, int]] | None = None,
) -> list[Position] | None:
    """BFS from a sentinel feed-side neighbor toward a harvester neighbor (outbound)."""
    starts = sentinel_feed_neighbors(
        ct, sentinel_pos, facing, ore_blocked=ore_blocked
    )
    if not starts:
        return None

    def is_goal(p: Position) -> bool:
        if abs(p.x - harvester_pos.x) + abs(p.y - harvester_pos.y) != 1:
            return False
        return is_layable_or_join(ct, p, ore_blocked=ore_blocked)

    best: list[Position] | None = None
    for start in starts:
        path = plan_conveyor_path(
            ct, start, is_goal=is_goal, ore_blocked=ore_blocked
        )
        if path is None:
            continue
        if best is None or len(path) < len(best):
            best = path
    return best


def facing_toward(tile: Position, target: Position) -> Direction:
    """Cardinal facing (for conveyor travel dirs)."""
    d = nearest_cardinal(tile.direction_to(target))
    return d if d != Direction.CENTRE else Direction.NORTH


def facing_8way(tile: Position, target: Position) -> Direction:
    """Full 8-direction facing (for sentinels / gunners — includes diagonals)."""
    d = tile.direction_to(target)
    return d if d != Direction.CENTRE else Direction.NORTH


def path_travel_dir(
    path: list[Position],
    index: int,
    face_target: Position,
    *,
    reverse: bool = False,
    into_core: bool = False,
) -> Direction:
    """Belt facing for path[index].

    Inbound paths face the next hop (toward face_target).
    Outbound paths (reverse=True) face the previous hop (still toward face_target).
    """
    tile = path[index]
    if reverse:
        if index > 0:
            return travel_facing(tile, path[index - 1])
        if into_core:
            return facing_into_core(tile, face_target)
        return facing_toward(tile, face_target)
    if index + 1 < len(path):
        return travel_facing(tile, path[index + 1])
    if into_core:
        return facing_into_core(tile, face_target)
    return facing_toward(tile, face_target)


def ensure_belt_on_path(
    ct: Controller,
    tile: Position,
    travel_dir: Direction,
    *,
    allow_destroy: bool = True,
) -> str:
    """Join if allied belt faces travel_dir; else optionally destroy+rebuild; else build."""
    if travel_dir == Direction.CENTRE:
        return "failed"

    bid = _allied_belt_id(ct, tile)
    if bid is not None:
        if ct.get_direction(bid) == travel_dir:
            return "joined"
        if not allow_destroy:
            return "failed"
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
    bid = _allied_belt_id(ct, tile)
    if bid is None:
        return False
    return ct.get_direction(bid) == expected


def path_is_complete(
    ct: Controller,
    path: list[Position],
    face_target: Position,
    *,
    reverse: bool = False,
    require_facing: bool = True,
    into_core: bool = False,
) -> bool:
    if not path:
        return False
    for i, tile in enumerate(path):
        if require_facing:
            want = path_travel_dir(
                path, i, face_target, reverse=reverse, into_core=into_core
            )
            if not belt_faces(ct, tile, want):
                return False
        elif _allied_belt_id(ct, tile) is None:
            return False
    return True


def first_incomplete_index(
    ct: Controller,
    path: list[Position],
    face_target: Position,
    *,
    start: int = 0,
    reverse: bool = False,
    require_facing: bool = True,
    into_core: bool = False,
) -> int | None:
    """First path index >= start missing a satisfactory allied belt.

    When require_facing is True, the belt must face the planned travel dir.
    When False (outbound join-only), any allied belt counts — no replace.
    """
    for i in range(max(0, start), len(path)):
        tile = path[i]
        if require_facing:
            want = path_travel_dir(
                path, i, face_target, reverse=reverse, into_core=into_core
            )
            if not belt_faces(ct, tile, want):
                return i
        elif _allied_belt_id(ct, tile) is None:
            return i
    return None


def last_built_path_index(
    ct: Controller,
    path: list[Position],
    face_target: Position,
    *,
    reverse: bool = False,
    require_facing: bool = True,
    into_core: bool = False,
) -> int | None:
    """Highest path index with a satisfactory allied belt, or None if none."""
    last: int | None = None
    for i, tile in enumerate(path):
        if require_facing:
            want = path_travel_dir(
                path, i, face_target, reverse=reverse, into_core=into_core
            )
            if belt_faces(ct, tile, want):
                last = i
        elif _allied_belt_id(ct, tile) is not None:
            last = i
    return last


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

class Player:
    def __init__(self):
        self.hivemind = HiveMind()
        self.num_spawned = 0

        self.target: Position | None = None
        self.last_pos: Position | None = None
        self.stuck = 0
        self.core_pos: Position | None = None

        self.in_relay = False
        # to_core | to_ore | to_sentinel | from_sentinel
        self.relay_mode: str = "to_core"
        self.relay_origin: Position | None = None
        self.relay_ore: Position | None = None  # goal ore / harvester for outbound modes
        self.relay_sentinel: Position | None = None
        self.relay_stuck = 0
        self.relay_path: list[Position] | None = None
        self.enemy_core_pos: Position | None = None
        self.siege_done = False  # odd builders: stop placing after cap / no sites

        # Clear-ore task: known ore from ORE_LOCATIONS we're claiming/clearing
        self.ore_site: Position | None = None
        self.skipped_ores: set[tuple[int, int]] = set()

        # One-off game-start bootstrap (builder 1 claim / builder 2 outbound)
        self.far_job_done = False
        self.far_support_done = False

    def run(self, ct: Controller) -> None:
        self.hivemind.tick(ct)

        etype = ct.get_entity_type()
        if etype == EntityType.CORE:
            self._run_core(ct)
        elif etype == EntityType.BUILDER_BOT:
            self._run_builder(ct)
        elif etype == EntityType.GUNNER:
            self._run_gunner(ct)
        elif etype == EntityType.SENTINEL:
            self._run_sentinel(ct)

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------

    def _run_core(self, ct: Controller) -> None:
        # HiveMind packs ally core into slot 15 on tick; do not write slots 7–15.
        round_n = ct.get_current_round()
        if round_n > 0 and round_n % SPAWN_CREDIT_INTERVAL == 0:
            ct.write_store(
                SLOT_SPAWN_CREDITS,
                ct.read_store(SLOT_SPAWN_CREDITS) + 1,
            )

        credits = ct.read_store(SLOT_SPAWN_CREDITS)
        can_credit_spawn = credits > 0
        if self.num_spawned >= MAX_BUILDERS and not can_credit_spawn:
            return

        ti = ct.get_global_resources()
        cost = ct.get_builder_bot_cost()
        if ti < cost:
            return

        pos = ct.get_position()
        for d in spawn_dirs_toward_center(ct, pos):
            spawn_pos = pos.add(d)
            if self.hivemind.spawn_builder(ct, spawn_pos):
                self.num_spawned += 1
                if can_credit_spawn and self.num_spawned > MAX_BUILDERS:
                    ct.write_store(SLOT_SPAWN_CREDITS, max(0, credits - 1))
                return

    # ------------------------------------------------------------------
    # Builder bot
    # ------------------------------------------------------------------

    def _run_builder(self, ct: Controller) -> None:
        pos = ct.get_position()
        idx = self.hivemind.my_index

        if self.core_pos is None:
            self._read_core_pos(ct)

        if self.last_pos == pos:
            self.stuck += 1
        else:
            self.stuck = 0
        self.last_pos = pos

        # One-off start bootstrap before normal ore work
        if idx == 1 and not self.far_job_done:
            self._bootstrap_claimer(ct)
        elif idx == 2 and not self.far_support_done:
            self._bootstrap_supporter(ct)

        bootstrapping = (idx == 1 and not self.far_job_done) or (
            idx == 2 and not self.far_support_done
        )

        self._read_enemy_core(ct)
        attack_time = (
            ct.get_current_round() >= ATTACK_TURN
            and self.enemy_core_pos is not None
            and idx > 0
        )
        if attack_time:
            # Drop start bootstrap and abandon ore/core relays for siege/feed
            self.far_job_done = True
            self.far_support_done = True
            self.ore_site = None
            if self.in_relay and self.relay_mode in ("to_core", "to_ore"):
                self._clear_relay()

        attacking = attack_time
        siege_builder = attacking and idx % 2 == 1
        feed_builder = attacking and idx % 2 == 0

        # Odd builders place until cap; drop siege-feed relays so they don't stall at 1
        if (
            siege_builder
            and self.in_relay
            and self.relay_mode in ("to_sentinel", "from_sentinel")
            and self._count_our_sentinels(ct) < MAX_SIEGE_SENTINELS
        ):
            self._clear_relay()

        if siege_builder and not self.in_relay:
            self._run_siege_builder(ct)
            # Feed only after the sentinel cap — placing comes first
            if (
                not self.in_relay
                and self._count_our_sentinels(ct) >= MAX_SIEGE_SENTINELS
            ):
                self._run_feed_builder(ct)
        elif feed_builder and not self.in_relay:
            self._run_feed_builder(ct)
        elif (
            not attacking
            and ct.get_action_cooldown() == 0
            and not self.in_relay
            and not bootstrapping
        ):
            if not self._try_build_harvester(ct):
                harvester_count = ct.read_store(SLOT_HARVESTER_COUNT)
                if harvester_count >= TARGET_HARVESTERS_APRON:
                    if not self._try_ensure_core_apron(ct):
                        if (
                            BUILD_GUNNERS
                            and harvester_count >= TARGET_HARVESTERS
                        ):
                            self._try_build_gunner(ct)
        elif (
            not attacking
            and BUILDER1_PAVE_EN_ROUTE
            and ct.get_action_cooldown() == 0
            and not self.in_relay
            and bootstrapping
            and idx == 1
        ):
            # Optional: claimer may lay apron once a harvester exists elsewhere
            harvester_count = ct.read_store(SLOT_HARVESTER_COUNT)
            if harvester_count >= TARGET_HARVESTERS_APRON:
                self._try_ensure_core_apron(ct)

        if ct.get_action_cooldown() == 0:
            self._try_heal(ct)

        # Supporter waits/paves only — no ore-site claim during bootstrap / attack
        if (
            not attacking
            and not self.in_relay
            and not (idx == 2 and not self.far_support_done)
        ):
            self._try_ore_site_task(ct)

        self._move_toward_target(ct)
        self._share_ore(ct)

    def _read_core_pos(self, ct: Controller) -> None:
        ally, _ = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
        if ally[0] != CoreState.UNKNOWN:
            self.core_pos = Position(ally[1], ally[2])

    def _read_enemy_core(self, ct: Controller) -> None:
        _, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
        if enemy[0] != CoreState.UNKNOWN:
            self.enemy_core_pos = Position(enemy[1], enemy[2])

    def _enter_relay(self, ct: Controller, harvester_pos: Position) -> None:
        self.in_relay = True
        self.relay_mode = "to_core"
        self.relay_origin = harvester_pos
        self.relay_ore = None
        self.relay_sentinel = None
        self.relay_stuck = 0
        self.relay_path = None
        self.target = self.core_pos
        self.ore_site = None
        orphan = unpack_pos(ct.read_store(SLOT_ORPHAN_RELAY))
        if (
            orphan is not None
            and orphan.x == harvester_pos.x
            and orphan.y == harvester_pos.y
        ):
            ct.write_store(SLOT_ORPHAN_RELAY, 0)
        # One-off claimer: finishing build/steal at the published far ore
        if not self.far_job_done and self.hivemind.my_index == 1:
            far = unpack_pos(ct.read_store(SLOT_FAR_ORE))
            if (
                far is not None
                and far.x == harvester_pos.x
                and far.y == harvester_pos.y
            ):
                self.far_job_done = True

    def _enter_relay_to_ore(self, ore_site: Position) -> None:
        """Committed outbound belt path from core toward a known ore neighbor."""
        self.in_relay = True
        self.relay_mode = "to_ore"
        self.relay_origin = self.core_pos
        self.relay_ore = ore_site
        self.relay_sentinel = None
        self.relay_stuck = 0
        self.relay_path = None
        self.target = ore_site

    def _enter_relay_to_sentinel(
        self, harvester_pos: Position, sentinel_pos: Position
    ) -> None:
        """Inbound feed: harvester → sentinel (destroy/replace wrong facing)."""
        self.in_relay = True
        self.relay_mode = "to_sentinel"
        self.relay_origin = harvester_pos
        self.relay_ore = None
        self.relay_sentinel = sentinel_pos
        self.relay_stuck = 0
        self.relay_path = None
        self.target = sentinel_pos
        self.ore_site = None

    def _enter_relay_from_sentinel(
        self, sentinel_pos: Position, harvester_pos: Position
    ) -> None:
        """Outbound feed: walk sentinel → harvester; belts face toward sentinel."""
        self.in_relay = True
        self.relay_mode = "from_sentinel"
        self.relay_origin = sentinel_pos
        self.relay_ore = harvester_pos
        self.relay_sentinel = sentinel_pos
        self.relay_stuck = 0
        self.relay_path = None
        self.target = harvester_pos
        self.ore_site = None

    def _read_far_ore(self, ct: Controller) -> Position | None:
        return unpack_pos(ct.read_store(SLOT_FAR_ORE))

    def _publish_far_ore(self, ct: Controller, ore: Position) -> None:
        ct.write_store(SLOT_FAR_ORE, pack_pos(ore))

    def _pick_far_ore(self, ct: Controller, pos: Position) -> Position | None:
        """Nearest known ore at least FAR_ORE_MIN_DIST from the core footprint."""
        if self.core_pos is None:
            return None
        map_id = self.hivemind.map_id
        if map_id <= 0:
            return None
        my_team = ct.get_team()
        best: Position | None = None
        best_dist = float("inf")
        for ox, oy in ORE_LOCATIONS.get(map_id, []):
            if (ox, oy) in self.skipped_ores:
                continue
            ore_pos = Position(ox, oy)
            if core_min_manhattan(ore_pos, self.core_pos) < FAR_ORE_MIN_DIST:
                continue
            ent = self.hivemind.local_map.get((ox, oy), {}).get("entity")
            if (
                ent
                and ent.get("type") == EntityType.HARVESTER
                and ent.get("team") == my_team
            ):
                continue
            d = pos.distance_squared(ore_pos)
            if d < best_dist:
                best_dist = d
                best = ore_pos
        return best

    def _bootstrap_claimer(self, ct: Controller) -> None:
        """Builder 1 one-off: sticky far ore claim + publish for supporter."""
        if self.core_pos is None or self.hivemind.map_id <= 0:
            return
        if self.ore_site is not None:
            return
        far = self._pick_far_ore(ct, ct.get_position())
        if far is None:
            self.far_job_done = True
            return
        self.ore_site = far
        self._publish_far_ore(ct, far)

    def _bootstrap_supporter(self, ct: Controller) -> None:
        """Builder 2 one-off: outbound pave toward published far ore (no claim)."""
        far = self._read_far_ore(ct)
        if far is None:
            # No publish yet — if no eligible far ore exists, skip bootstrap
            if self.core_pos is not None and self.hivemind.map_id > 0:
                if self._pick_far_ore(ct, ct.get_position()) is None:
                    self.far_support_done = True
            return
        if not self.in_relay and self.core_pos is not None:
            self._enter_relay_to_ore(far)

    def _finish_far_support_if_needed(self) -> None:
        if self.hivemind.my_index == 2 and not self.far_support_done:
            self.far_support_done = True

    # ------------------------------------------------------------------
    # Attack phase (turn 400+): siege sentinels + harvester feed belts
    # ------------------------------------------------------------------

    def _sentinel_facing(self, ct: Controller, sentinel_pos: Position) -> Direction:
        if self.enemy_core_pos is None:
            self._read_enemy_core(ct)
        if self.enemy_core_pos is None:
            return Direction.EAST
        return facing_8way(sentinel_pos, self.enemy_core_pos)

    def _count_our_sentinels(self, ct: Controller) -> int:
        my_team = ct.get_team()
        count = 0
        for info in self.hivemind.local_map.values():
            ent = info.get("entity")
            if (
                ent
                and ent.get("type") == EntityType.SENTINEL
                and ent.get("team") == my_team
            ):
                count += 1
        return count

    def _published_siege_sites(self, ct: Controller) -> list[Position]:
        sites: list[Position] = []
        for slot in (SLOT_SIEGE_A, SLOT_SIEGE_B):
            p = unpack_pos(ct.read_store(slot))
            if p is not None:
                sites.append(p)
        return sites

    def _publish_siege_site(self, ct: Controller, pos: Position) -> None:
        for slot in (SLOT_SIEGE_A, SLOT_SIEGE_B):
            if unpack_pos(ct.read_store(slot)) is None:
                ct.write_store(slot, pack_pos(pos))
                return
        # Both filled — overwrite slot chosen by builder index
        slot = SLOT_SIEGE_A if self.hivemind.my_index % 4 == 1 else SLOT_SIEGE_B
        ct.write_store(slot, pack_pos(pos))

    def _siege_preferred_side(self) -> int:
        """0=N 1=NE 2=E 3=SE 4=S 5=SW 6=W 7=NW — staggered by odd builder index."""
        idx = self.hivemind.my_index
        if idx <= 0:
            return 0
        return ((idx - 1) // 2) % 8

    def _siege_cand_side(self, cand: Position, enemy: Position) -> int:
        """Classify candidate into one of 8 sides around the 2x2 core center."""
        center_x = enemy.x + 0.5
        center_y = enemy.y + 0.5
        dx = cand.x - center_x
        dy = cand.y - center_y
        # atan2-like octant: y down is south in grid
        ax, ay = abs(dx), abs(dy)
        if ay * 2 < ax:
            return 2 if dx > 0 else 6  # E / W
        if ax * 2 < ay:
            return 4 if dy > 0 else 0  # S / N
        if dx > 0 and dy < 0:
            return 1  # NE
        if dx > 0 and dy > 0:
            return 3  # SE
        if dx < 0 and dy > 0:
            return 5  # SW
        return 7  # NW

    def _pick_siege_site(self, ct: Controller) -> Position | None:
        """Siege tile in sentinel range; each builder prefers a different side."""
        if self.enemy_core_pos is None:
            return None
        enemy = self.enemy_core_pos
        cx, cy = enemy.x, enemy.y
        footprint = _core_footprint(enemy)
        published = {(p.x, p.y) for p in self._published_siege_sites(ct)}
        best: Position | None = None
        best_score = float("inf")
        ally = self.core_pos
        my_team = ct.get_team()
        prefer = self._siege_preferred_side()

        ring: list[Position] = []
        for d in (5, 4, 3):
            for dx in (0, 1):
                ring.append(Position(cx + dx, cy - d))
                ring.append(Position(cx + dx, cy + 1 + d))
            for dy in (0, 1):
                ring.append(Position(cx - d, cy + dy))
                ring.append(Position(cx + 1 + d, cy + dy))
            ring.append(Position(cx - d, cy - d))
            ring.append(Position(cx + 1 + d, cy - d))
            ring.append(Position(cx - d, cy + 1 + d))
            ring.append(Position(cx + 1 + d, cy + 1 + d))

        seen: set[tuple[int, int]] = set()
        for cand in ring:
            key = (cand.x, cand.y)
            if key in seen:
                continue
            seen.add(key)
            if not in_bounds(ct, cand):
                continue
            if key in published:
                continue
            dist = min(cand.distance_squared(t) for t in footprint)
            if dist < SENTINEL_PLACE_MIN_SQ or dist > SENTINEL_RANGE_SQ:
                continue
            ent = self.hivemind.local_map.get(key, {}).get("entity")
            if (
                ent
                and ent.get("type") == EntityType.SENTINEL
                and ent.get("team") == my_team
            ):
                continue
            if ct.is_in_vision(cand):
                if not ct.is_tile_empty(cand):
                    continue
                if ct.get_tile_env(cand) != Environment.EMPTY:
                    continue
            side = self._siege_cand_side(cand, enemy)
            # Prefer own side strongly; fall back to other sides if blocked
            side_pen = 0 if side == prefer else (1000 + 50 * min((side - prefer) % 8, (prefer - side) % 8))
            ally_d = cand.distance_squared(ally) if ally is not None else 0
            score = side_pen + ally_d + (SENTINEL_RANGE_SQ - dist)
            if score < best_score:
                best_score = score
                best = cand
        return best

    def _run_siege_builder(self, ct: Controller) -> None:
        """Odd builders: place sentinels facing enemy core near max range."""
        if self.enemy_core_pos is None:
            return
        if self._count_our_sentinels(ct) >= MAX_SIEGE_SENTINELS:
            self.siege_done = True
            return

        site = self._pick_siege_site(ct)
        if site is None:
            # Stay put / hold last target — do not swarm the enemy core
            return

        self.target = site
        pos = ct.get_position()
        if abs(pos.x - site.x) + abs(pos.y - site.y) != 1:
            return
        if ct.get_action_cooldown() != 0:
            return

        facing = facing_8way(site, self.enemy_core_pos)
        if ct.can_build_sentinel(site, facing):
            ct.build_sentinel(site, facing)
            self._publish_siege_site(ct, site)
            # Keep placing until cap; even builders handle feed in the meantime
            if self._count_our_sentinels(ct) >= MAX_SIEGE_SENTINELS:
                self._start_sentinel_feed(ct, site)

    def _nearest_feed_harvester(
        self, ct: Controller, near: Position | None = None
    ) -> Position | None:
        """Closest harvester on a known ore (any team — enemy steal-feed is fine)."""
        origin = near if near is not None else ct.get_position()
        best: Position | None = None
        best_dist = float("inf")

        map_id = self.hivemind.map_id
        if map_id > 0:
            for ox, oy in ORE_LOCATIONS.get(map_id, []):
                ent = self.hivemind.local_map.get((ox, oy), {}).get("entity")
                if not ent or ent.get("type") != EntityType.HARVESTER:
                    continue
                hpos = Position(ox, oy)
                d = origin.distance_squared(hpos)
                if d < best_dist:
                    best_dist = d
                    best = hpos

        for tile in ct.get_nearby_tiles():
            if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
                continue
            bid = ct.get_tile_building_id(tile)
            if bid is None:
                continue
            if ct.get_entity_type(bid) != EntityType.HARVESTER:
                continue
            d = origin.distance_squared(tile)
            if d < best_dist:
                best_dist = d
                best = tile

        return best

    def _known_siege_sentinels(self, ct: Controller) -> list[Position]:
        """Published sites plus allied sentinels from HiveMind vision."""
        sites: list[Position] = []
        seen: set[tuple[int, int]] = set()
        for p in self._published_siege_sites(ct):
            key = (p.x, p.y)
            if key not in seen:
                seen.add(key)
                sites.append(p)
        my_team = ct.get_team()
        for (x, y), info in self.hivemind.local_map.items():
            ent = info.get("entity")
            if (
                not ent
                or ent.get("type") != EntityType.SENTINEL
                or ent.get("team") != my_team
            ):
                continue
            key = (x, y)
            if key in seen:
                continue
            seen.add(key)
            sites.append(Position(x, y))
        return sites

    def _start_sentinel_feed(self, ct: Controller, sentinel: Position) -> None:
        """Inbound feed from nearest ore harvester unless sentinel already fed."""
        facing = self._sentinel_facing(ct, sentinel)
        if sentinel_has_inbound_belt(ct, sentinel, facing):
            return
        harvester = self._nearest_feed_harvester(ct, near=sentinel)
        if harvester is None:
            harvester = self._nearest_feed_harvester(ct)
        if harvester is None:
            return
        # Prefer inbound (destroy/replace) so Ti actually enters the turret
        self._enter_relay_to_sentinel(harvester, sentinel)

    def _run_feed_builder(self, ct: Controller) -> None:
        """Even builders: inbound belts to sentinels that lack a feed-facing belt."""
        sites = self._known_siege_sentinels(ct)
        if not sites:
            site = self._pick_siege_site(ct)
            if site is not None:
                self.target = site
            return

        pos = ct.get_position()
        unfed: list[Position] = []
        for s in sites:
            facing = self._sentinel_facing(ct, s)
            # Out of vision: treat as needing a check — still candidate
            if ct.is_in_vision(s) and sentinel_has_inbound_belt(ct, s, facing):
                continue
            unfed.append(s)

        if not unfed:
            return

        sentinel = min(unfed, key=lambda s: pos.distance_squared(s))
        self.target = sentinel
        self._start_sentinel_feed(ct, sentinel)

    def _try_build_harvester(self, ct: Controller) -> bool:
        ti = ct.get_global_resources()
        cost = ct.get_harvester_cost()
        if ti < cost:
            return False

        pos = ct.get_position()
        for d in Direction:
            build_pos = pos.add(d)
            if ct.can_build_harvester(build_pos):
                ct.build_harvester(build_pos)
                count = ct.read_store(SLOT_HARVESTER_COUNT)
                ct.write_store(SLOT_HARVESTER_COUNT, count + 1)
                self._enter_relay(ct, build_pos)
                self.target = None
                return True
        return False

    def _try_ensure_core_apron(self, ct: Controller) -> bool:
        """Lay one missing inward-facing apron belt adjacent to this builder.

        No splitters — only conveyors facing into the core on N/E/S/W sides,
        two tiles deep so relays can plug into the outer ring.
        """
        if self.core_pos is None:
            return False
        pos = ct.get_position()

        # Prefer tiles we can touch this turn
        candidates: list[tuple[Position, Direction]] = []
        for tile, facing in core_apron_specs(self.core_pos):
            if not in_bounds(ct, tile):
                continue
            if abs(pos.x - tile.x) + abs(pos.y - tile.y) != 1:
                continue
            if belt_faces(ct, tile, facing):
                continue
            candidates.append((tile, facing))

        if not candidates:
            # If we're standing on a missing apron tile, step off next move;
            # nothing to build this action.
            return False

        # Prefer outer ring (depth 2) first — that's the connect surface
        def depth_key(item: tuple[Position, Direction]) -> int:
            tile, _ = item
            # Manhattan distance from nearest footprint tile
            return min(
                abs(tile.x - t.x) + abs(tile.y - t.y)
                for t in _core_footprint(self.core_pos)
            )

        candidates.sort(key=depth_key, reverse=True)
        tile, facing = candidates[0]
        result = ensure_belt_on_path(ct, tile, facing)
        return result in ("built", "joined")

    def _try_build_gunner(self, ct: Controller) -> bool:
        """Place a gunner near the core (ammo is convert_ammo — no splitter feed).

        Gated by BUILD_GUNNERS (currently False — belt/apron debugging).
        """
        if not BUILD_GUNNERS:
            return False
        ti = ct.get_global_resources()
        cost = ct.get_gunner_cost()
        if ti < cost:
            return False

        pos = ct.get_position()
        if self.core_pos is None or pos.distance_squared(self.core_pos) > 18:
            return False

        # Prefer empty tiles just outside the apron so we don't block connect ring
        facing = pos.direction_to(self.core_pos).opposite()
        if facing == Direction.CENTRE:
            facing = random.choice(CARDINALS)
        for d in Direction:
            build_pos = pos.add(d)
            if not in_bounds(ct, build_pos):
                continue
            if is_apron_tile(build_pos, self.core_pos):
                continue
            if ct.can_build_gunner(build_pos, facing):
                ct.build_gunner(build_pos, facing)
                return True
        return False

    def _try_heal(self, ct: Controller) -> None:
        pos = ct.get_position()
        for d in Direction:
            check = pos.add(d)
            if ct.can_heal(check):
                ct.heal(check)
                return

    # ------------------------------------------------------------------
    # Clear-ore site task (between heal and orphan claim)
    # ------------------------------------------------------------------

    def _skip_ore(self, ore: Position) -> None:
        self.skipped_ores.add((ore.x, ore.y))
        if (
            self.ore_site is not None
            and self.ore_site.x == ore.x
            and self.ore_site.y == ore.y
        ):
            self.ore_site = None

    def _pick_ore_site(self, ct: Controller, pos: Position) -> Position | None:
        """Nearest known ore from ORE_LOCATIONS, skipping blacklisted / our harvesters.

        Enemy harvesters are allowed — we steal-relay their output to our core.
        """
        map_id = self.hivemind.map_id
        if map_id <= 0:
            return None
        my_team = ct.get_team()
        best: Position | None = None
        best_dist = float("inf")
        for ox, oy in ORE_LOCATIONS.get(map_id, []):
            if (ox, oy) in self.skipped_ores:
                continue
            ent = self.hivemind.local_map.get((ox, oy), {}).get("entity")
            if (
                ent
                and ent.get("type") == EntityType.HARVESTER
                and ent.get("team") == my_team
            ):
                continue
            ore_pos = Position(ox, oy)
            d = pos.distance_squared(ore_pos)
            if d < best_dist:
                best_dist = d
                best = ore_pos
        return best

    def _classify_ore_site(self, ct: Controller, ore: Position) -> str:
        """Classify a visible ore tile. Caller must ensure vision."""
        bid = ct.get_tile_building_id(ore)
        if bid is None:
            return "empty"
        etype = ct.get_entity_type(bid)
        team = ct.get_team(bid)
        my_team = ct.get_team()
        if etype == EntityType.HARVESTER:
            if team == my_team:
                return "our_harvester"
            return "enemy_harvester"
        if etype in (EntityType.CONVEYOR, EntityType.SPLITTER):
            if team == my_team:
                return "allied_belt"
            return "enemy_belt"
        return "blocked"

    def _try_ore_site_task(self, ct: Controller) -> None:
        """Visit known ore: clear belts, steal enemy harvesters, harvest if empty."""
        pos = ct.get_position()

        if self.ore_site is None:
            self.ore_site = self._pick_ore_site(ct, pos)
        if self.ore_site is None:
            return

        self.target = self.ore_site
        ore = self.ore_site

        if not ct.is_in_vision(ore):
            return

        kind = self._classify_ore_site(ct, ore)

        if kind == "enemy_harvester":
            # Steal: relay from this harvester so Ti flows onto our belts → core
            self._enter_relay(ct, ore)
            return

        if kind in ("our_harvester", "blocked"):
            self._skip_ore(ore)
            if self.hivemind.my_index == 1 and not self.far_job_done:
                self.ore_site = self._pick_far_ore(ct, pos)
                if self.ore_site is None:
                    self.far_job_done = True
                else:
                    self._publish_far_ore(ct, self.ore_site)
                    self.target = self.ore_site
            else:
                self.ore_site = self._pick_ore_site(ct, pos)
                if self.ore_site is not None:
                    self.target = self.ore_site
            return

        if kind == "allied_belt":
            # destroy is adjacent-only, not own tile
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
            # builders may only fire their own tile — stand on the belt first
            if pos == ore:
                if ct.get_action_cooldown() == 0 and ct.can_fire(ore):
                    ct.fire(ore)
            return

        if kind == "empty":
            # Build requires an orthogonally adjacent tile, not standing on the ore
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
                    self._enter_relay(ct, ore)
            return

    # ------------------------------------------------------------------
    # Movement + relay
    # ------------------------------------------------------------------

    def _bfs_next_step(
        self, ct: Controller, start: Position, goal: Position
    ) -> Direction | None:
        if start == goal:
            return None

        w, h = ct.get_map_width(), ct.get_map_height()
        queue: deque[tuple[Position, list[Direction]]] = deque([(start, [])])
        visited = {(start.x, start.y)}
        iterations = 0

        while queue and iterations < BFS_MAX_ITERS:
            iterations += 1
            curr, path = queue.popleft()
            for d in DIRECTIONS:
                nxt = curr.add(d)
                nx, ny = nxt.x, nxt.y
                if not (0 <= nx < w and 0 <= ny < h):
                    continue
                if (nx, ny) in visited:
                    continue
                visited.add((nx, ny))
                if nxt == goal:
                    return path[0] if path else d
                if self.hivemind.local_map.get((nx, ny), {}).get("passable", True):
                    queue.append((nxt, path + [d]))
        return None

    def _direction_candidates(
        self, ct: Controller, pos: Position, target: Position
    ) -> list[Direction]:
        desired = self._bfs_next_step(ct, pos, target)
        if desired is None:
            desired = nearest_cardinal(pos.direction_to(target))
        if desired == Direction.CENTRE:
            return []
        primary = [desired, desired.rotate_left(), desired.rotate_right()]
        remaining = [d for d in DIRECTIONS if d not in primary]
        random.shuffle(remaining)
        return primary + remaining

    def _move_toward_target(self, ct: Controller) -> None:
        if not self.in_relay and CLAIM_ORPHAN_RELAY:
            self._try_claim_orphan(ct)

        if self.in_relay:
            self._relay_toward_core(ct)
            return

        # Outbound relay toward known ore when still traveling (not on/adjacent).
        # Builder 1 far-job may walk only when BUILDER1_PAVE_EN_ROUTE is False.
        builder1_walk_only = (
            self.hivemind.my_index == 1
            and not self.far_job_done
            and not BUILDER1_PAVE_EN_ROUTE
        )
        if (
            not builder1_walk_only
            and self.ore_site is not None
            and self.core_pos is not None
            and not self._adjacent_to_ore(ct.get_position(), self.ore_site)
        ):
            self._enter_relay_to_ore(self.ore_site)
            self._relay_toward_core(ct)
            return

        if ct.get_move_cooldown() != 0:
            return

        pos = ct.get_position()
        # Prefer active clear-ore site over generic _pick_target
        if self.ore_site is not None:
            self.target = self.ore_site
        elif self.target is None or pos == self.target or self.stuck >= 3:
            self.target = self._pick_target(ct)
            self.stuck = 0
        if self.target is None:
            return

        for d in self._direction_candidates(ct, pos, self.target):
            if self._try_move(ct, d):
                return

    def _try_claim_orphan(self, ct: Controller) -> None:
        orphan = unpack_pos(ct.read_store(SLOT_ORPHAN_RELAY))
        if orphan is None:
            return
        ct.write_store(SLOT_ORPHAN_RELAY, 0)
        self._enter_relay(ct, orphan)

    def _ore_blocked_set(self, ct: Controller) -> set[tuple[int, int]]:
        map_id = self.hivemind.map_id
        if map_id <= 0 or map_id not in ORE_LOCATIONS:
            return set()
        return set(ORE_LOCATIONS[map_id])

    def _clear_relay(self) -> None:
        self.in_relay = False
        self.relay_mode = "to_core"
        self.relay_origin = None
        self.relay_ore = None
        self.relay_sentinel = None
        self.relay_stuck = 0
        self.relay_path = None

    def _complete_relay(self, ct: Controller) -> None:
        # Spawn credits only for classic harvester→core inbound
        if self.relay_mode == "to_core":
            credits = ct.read_store(SLOT_SPAWN_CREDITS)
            ct.write_store(SLOT_SPAWN_CREDITS, credits + 1)
        if self.relay_origin is not None:
            orphan = unpack_pos(ct.read_store(SLOT_ORPHAN_RELAY))
            if (
                orphan is not None
                and orphan.x == self.relay_origin.x
                and orphan.y == self.relay_origin.y
            ):
                ct.write_store(SLOT_ORPHAN_RELAY, 0)
        self._clear_relay()

    def _abort_relay(self, ct: Controller) -> None:
        if not ABORT_RELAY:
            return
        if self.relay_mode == "to_core" and self.relay_origin is not None:
            ct.write_store(SLOT_ORPHAN_RELAY, pack_pos(self.relay_origin))
        self._clear_relay()

    def _relay_path_ok(
        self,
        ct: Controller,
        path: list[Position],
        ore_blocked: set[tuple[int, int]] | None,
    ) -> bool:
        for tile in path:
            if _allied_belt_id(ct, tile) is not None:
                continue
            if not is_layable_or_join(ct, tile, ore_blocked=ore_blocked):
                return False
        return True

    def _adjacent_to_ore(self, pos: Position, ore: Position) -> bool:
        return abs(pos.x - ore.x) + abs(pos.y - ore.y) <= 1

    def _relay_toward_core(self, ct: Controller) -> None:
        """Walk a committed BFS belt path (core/ore/sentinel feed modes).

        Enemy belts on the path are cleared via stand-and-fire, then we lay ours.
        If relay_origin leaves vision, advance it to the last built path tile and
        keep finishing the path. Outbound modes never destroy/replace allied belts.
        Inbound to_core / to_sentinel may destroy+rebuild wrong facing.
        """
        if self.relay_origin is None:
            self._abort_relay(ct)
            return

        to_ore = self.relay_mode == "to_ore"
        to_sentinel = self.relay_mode == "to_sentinel"
        from_sentinel = self.relay_mode == "from_sentinel"
        to_core = self.relay_mode == "to_core"

        if to_core and self.core_pos is None:
            self._abort_relay(ct)
            return
        if to_ore and self.relay_ore is None:
            self._abort_relay(ct)
            return
        if (to_sentinel or from_sentinel) and self.relay_sentinel is None:
            self._abort_relay(ct)
            return
        if from_sentinel and self.relay_ore is None:
            self._abort_relay(ct)
            return

        ore_blocked = self._ore_blocked_set(ct)
        pos = ct.get_position()
        origin_visible = ct.is_in_vision(self.relay_origin)
        path = self.relay_path
        work_from = 0

        # Inbound (to_core / to_sentinel): require facing + allow destroy.
        # Outbound (to_ore / from_sentinel): join/build only.
        require_facing = to_core or to_sentinel
        allow_destroy = to_core or to_sentinel
        face_reverse = to_ore or from_sentinel
        into_core = to_core or to_ore
        if to_sentinel or from_sentinel:
            assert self.relay_sentinel is not None
            face_target = self.relay_sentinel
            facing = self._sentinel_facing(ct, self.relay_sentinel)
        else:
            assert self.core_pos is not None
            face_target = self.core_pos
            facing = Direction.NORTH  # unused

        # Fog fallback: origin left vision — advance to last successfully built belt.
        if not origin_visible:
            if path:
                last_i = last_built_path_index(
                    ct,
                    path,
                    face_target,
                    reverse=face_reverse,
                    require_facing=require_facing,
                    into_core=into_core,
                )
                if last_i is not None:
                    self.relay_origin = path[last_i]
                    work_from = last_i + 1
                else:
                    self.relay_origin = pos
                    best_i = 0
                    best_d = float("inf")
                    for i, tile in enumerate(path):
                        d = pos.distance_squared(tile)
                        if d < best_d:
                            best_d = d
                            best_i = i
                    work_from = best_i
            else:
                self.relay_origin = pos
        else:
            if path is not None and not self._relay_path_ok(ct, path, ore_blocked):
                path = None
                self.relay_path = None

        if path is None:
            if to_ore:
                assert self.relay_ore is not None
                path = plan_conveyor_path_to_ore(
                    ct,
                    self.relay_origin,
                    self.relay_ore,
                    ore_blocked=ore_blocked,
                )
            elif to_sentinel:
                assert self.relay_sentinel is not None
                path = plan_conveyor_path_to_sentinel(
                    ct,
                    self.relay_origin,
                    self.relay_sentinel,
                    facing,
                    ore_blocked=ore_blocked,
                )
            elif from_sentinel:
                assert self.relay_sentinel is not None
                assert self.relay_ore is not None
                path = plan_conveyor_path_from_sentinel(
                    ct,
                    self.relay_sentinel,
                    self.relay_ore,
                    facing,
                    ore_blocked=ore_blocked,
                )
                if path:
                    # Origin for fog tracking is first path tile (feed neighbor)
                    self.relay_origin = path[0]
            else:
                assert self.core_pos is not None
                path = plan_conveyor_path_to_core(
                    ct, self.relay_origin, self.core_pos, ore_blocked=ore_blocked
                )
            self.relay_path = path
            work_from = 0
        if path is None:
            self._abort_relay(ct)
            return

        idx = first_incomplete_index(
            ct,
            path,
            face_target,
            start=work_from,
            reverse=face_reverse,
            require_facing=require_facing,
            into_core=into_core,
        )
        if idx is None:
            if to_ore:
                assert self.relay_ore is not None
                # Belts present — stay in outbound until adjacent for site task.
                if self._adjacent_to_ore(pos, self.relay_ore):
                    self._finish_far_support_if_needed()
                    self._clear_relay()
                    return
                if ct.get_move_cooldown() != 0:
                    return
                progressed = False
                for d in self._direction_candidates(ct, pos, self.relay_ore):
                    if self._try_move(ct, d):
                        progressed = True
                        break
                if progressed:
                    self.relay_stuck = 0
                else:
                    self.relay_stuck += 1
                    if self.relay_stuck >= 3:
                        self._abort_relay(ct)
                return
            self._complete_relay(ct)
            return

        tile = path[idx]
        travel_dir = path_travel_dir(
            path,
            idx,
            face_target,
            reverse=face_reverse,
            into_core=into_core,
        )

        # Clear enemy belts before we can join/lay our own
        if _enemy_belt_id(ct, tile) is not None:
            if pos == tile:
                if ct.get_action_cooldown() == 0 and ct.can_fire(tile):
                    ct.fire(tile)
                    self.relay_stuck = 0
                else:
                    self.relay_stuck += 1
                    if self.relay_stuck >= 3:
                        self._abort_relay(ct)
                return
            if ct.get_move_cooldown() != 0:
                return
            progressed = False
            for d in self._direction_candidates(ct, pos, tile):
                if self._try_move(ct, d):
                    progressed = True
                    break
            if progressed:
                self.relay_stuck = 0
            else:
                self.relay_stuck += 1
                if self.relay_stuck >= 3:
                    self._abort_relay(ct)
            return

        if pos == tile:
            if ct.get_move_cooldown() != 0:
                return
            for d in CARDINALS:
                if ct.can_move(d):
                    ct.move(d)
                    self.relay_stuck = 0
                    return
            self.relay_stuck += 1
            if self.relay_stuck >= 3:
                self._abort_relay(ct)
            return

        adjacent = abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1
        if adjacent:
            # Outbound: join/build only. Inbound may destroy+rebuild wrong facing.
            result = ensure_belt_on_path(
                ct, tile, travel_dir, allow_destroy=allow_destroy
            )
            if result == "failed":
                self.relay_stuck += 1
                if self.relay_stuck >= 3:
                    self._abort_relay(ct)
                return
            self.relay_stuck = 0
            if ct.get_move_cooldown() == 0:
                step = nearest_cardinal(pos.direction_to(tile))
                if step != Direction.CENTRE and ct.can_move(step):
                    ct.move(step)
            return

        if ct.get_move_cooldown() != 0:
            return

        progressed = False
        for d in self._direction_candidates(ct, pos, tile):
            if self._try_move(ct, d):
                progressed = True
                break
        if progressed:
            self.relay_stuck = 0
        else:
            self.relay_stuck += 1
            if self.relay_stuck >= 3:
                self._abort_relay(ct)

    def _is_known_ore(self, pos: Position) -> bool:
        """True if pos is in static ORE_LOCATIONS for the fingerprinted map."""
        map_id = self.hivemind.map_id
        if map_id <= 0:
            return False
        return (pos.x, pos.y) in ORE_LOCATIONS.get(map_id, [])

    def _heading_toward_known_ore(self, pos: Position, next_pos: Position) -> bool:
        """True when navigating to a known ore and this step moves closer to it."""
        if self.target is None or not self._is_known_ore(self.target):
            return False
        return next_pos.distance_squared(self.target) < pos.distance_squared(self.target)

    def _try_move(self, ct: Controller, d: Direction) -> bool:
        """Move one step; optionally pave toward core when heading to a known ore.

        Paving gated by PAVE_WHILE_MOVE. Uses ensure_belt_on_path with
        allow_destroy=False (join/build only). Relays may still destroy+rebuild.
        """
        if d == Direction.CENTRE:
            return False
        pos = ct.get_position()
        next_pos = pos.add(d)
        if not in_bounds(ct, next_pos):
            return False

        if (
            PAVE_WHILE_MOVE
            and not self.in_relay
            and self.core_pos is not None
            and self._heading_toward_known_ore(pos, next_pos)
        ):
            facing = facing_into_core(next_pos, self.core_pos)
            # Join/build only — never destroy allied belts while walking
            ensure_belt_on_path(ct, next_pos, facing, allow_destroy=False)

        if ct.can_move(d):
            ct.move(d)
            return True
        return False

    def _pick_target(self, ct: Controller) -> Position:
        pos = ct.get_position()
        best_ore = None
        best_dist = float("inf")

        if self.hivemind.map_id != 0:
            for ox, oy in ORE_LOCATIONS.get(self.hivemind.map_id, []):
                tile_info = self.hivemind.local_map.get((ox, oy), {})
                ent = tile_info.get("entity")
                if ent and ent.get("type") == EntityType.HARVESTER:
                    continue
                ore_pos = Position(ox, oy)
                d = pos.distance_squared(ore_pos)
                if d < best_dist:
                    best_dist = d
                    best_ore = ore_pos
            if best_ore is not None:
                return best_ore

        best_dist = float("inf")
        for tile in ct.get_nearby_tiles():
            if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
                continue
            if ct.get_tile_building_id(tile) is not None:
                continue
            d = pos.distance_squared(tile)
            if d < best_dist:
                best_dist = d
                best_ore = tile
        if best_ore is not None:
            return best_ore

        harvester_count = ct.read_store(SLOT_HARVESTER_COUNT)
        if harvester_count >= TARGET_HARVESTERS_APRON and self.core_pos is not None:
            # Head near core to finish the 2-deep apron / place gunners
            if pos.distance_squared(self.core_pos) > 8:
                return self.core_pos

        shared = unpack_pos(ct.read_store(SLOT_ORE_LOCATION))
        if shared is not None and pos.distance_squared(shared) > 4:
            return shared

        w, h = ct.get_map_width(), ct.get_map_height()
        return Position(random.randrange(w), random.randrange(h))

    def _share_ore(self, ct: Controller) -> None:
        for tile in ct.get_nearby_tiles():
            if ct.get_tile_env(tile) == Environment.ORE_TITANIUM:
                if ct.get_tile_building_id(tile) is None:
                    ct.write_store(SLOT_ORE_LOCATION, pack_pos(tile))
                    return

    # ------------------------------------------------------------------
    # Gunner
    # ------------------------------------------------------------------

    def _run_gunner(self, ct: Controller) -> None:
        target = ct.get_gunner_target()
        if target is None or not ct.can_fire(target):
            return
        bot = ct.get_tile_builder_bot_id(target)
        victim = bot if bot is not None else ct.get_tile_building_id(target)
        if victim is None or ct.get_team(victim) == ct.get_team():
            return
        ct.fire(target)

    def _run_sentinel(self, ct: Controller) -> None:
        """Fire along facing line at the first enemy (skip friendlies)."""
        for tile in ct.get_attackable_tiles():
            if not ct.can_fire(tile):
                continue
            bot = ct.get_tile_builder_bot_id(tile)
            victim = bot if bot is not None else ct.get_tile_building_id(tile)
            if victim is None:
                continue
            if ct.get_team(victim) == ct.get_team():
                continue
            ct.fire(tile)
            return
