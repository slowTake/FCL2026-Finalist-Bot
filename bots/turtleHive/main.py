"""turtleHive — turtle + HiveMind + committed relays + 2-deep core apron.

Lean map-aware baseline: HiveMind from components/, BFS movement, magnum-style
committed harvester→core relay belts, and a 2-deep inward-facing conveyor apron
around the core (N/E/S/W only — no splitters).

Store slots:
  0  SLOT_SPAWN_CREDITS   Earned by completing a relay (Player)
  1  SLOT_ORPHAN_RELAY    Packed harvester origin needing a relay (Player)
  2  SLOT_HARVESTER_COUNT Total harvesters (Player)
  3  SLOT_ORE_LOCATION    Packed uncovered ore (Player)
  4–6                     Free
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
SLOT_PACKED_CORES = 15

MAX_BUILDERS = 5
TARGET_HARVESTERS = 3
TARGET_HARVESTERS_APRON = 1  # start core apron after first harvester
BFS_MAX_ITERS = 400

# NOTE: gunners off while debugging belt logistics / core apron.
# Re-enable once relays + 2-deep apron reliably deliver mined Ti.
BUILD_GUNNERS = False

# NOTE: orphan relay claim off for a test — builders won't pick up aborted relays.
CLAIM_ORPHAN_RELAY = False

# NOTE: relay abort off for a test — stuck / no-path / guard won't orphan or clear relay.
ABORT_RELAY = False

# NOTE: pave-while-move on, but _try_move must not destroy allied belts (join/build only).
PAVE_WHILE_MOVE = True


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
    core_pos: Position,
    *,
    ore_blocked: set[tuple[int, int]] | None = None,
) -> list[Position] | None:
    """BFS over layable/join tiles from start to a core-adjacent (depth-1) tile.

    Goal is the inner apron ring that feeds the core. The outer (depth-2) apron
    is joinable mid-path so relays plug into it, then continue one hop inward.
    Ending on the outer ring alone would leave a gap before the core.
    """
    w = ct.get_map_width()
    h = ct.get_map_height()

    def ok(p: Position) -> bool:
        return is_layable_or_join(ct, p, ore_blocked=ore_blocked)

    def is_goal(p: Position) -> bool:
        return is_core_adjacent(ct, p, core_pos)

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


def path_travel_dir(
    path: list[Position], index: int, core_pos: Position
) -> Direction:
    tile = path[index]
    if index + 1 < len(path):
        return travel_facing(tile, path[index + 1])
    return facing_into_core(tile, core_pos)


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
    ct: Controller, path: list[Position], core_pos: Position
) -> bool:
    if not path:
        return False
    for i, tile in enumerate(path):
        want = path_travel_dir(path, i, core_pos)
        if not belt_faces(ct, tile, want):
            return False
    return True


def first_incomplete_index(
    ct: Controller, path: list[Position], core_pos: Position, *, start: int = 0
) -> int | None:
    """First path index >= start missing the correct allied belt facing."""
    for i in range(max(0, start), len(path)):
        tile = path[i]
        want = path_travel_dir(path, i, core_pos)
        if not belt_faces(ct, tile, want):
            return i
    return None


def last_built_path_index(
    ct: Controller, path: list[Position], core_pos: Position
) -> int | None:
    """Highest path index with correct allied belt facing, or None if none built."""
    last: int | None = None
    for i, tile in enumerate(path):
        want = path_travel_dir(path, i, core_pos)
        if belt_faces(ct, tile, want):
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
        self.relay_origin: Position | None = None
        self.relay_stuck = 0
        self.relay_path: list[Position] | None = None

        # Clear-ore task: known ore from ORE_LOCATIONS we're claiming/clearing
        self.ore_site: Position | None = None
        self.skipped_ores: set[tuple[int, int]] = set()

    def run(self, ct: Controller) -> None:
        self.hivemind.tick(ct)

        etype = ct.get_entity_type()
        if etype == EntityType.CORE:
            self._run_core(ct)
        elif etype == EntityType.BUILDER_BOT:
            self._run_builder(ct)
        elif etype == EntityType.GUNNER:
            self._run_gunner(ct)

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------

    def _run_core(self, ct: Controller) -> None:
        # HiveMind packs ally core into slot 15 on tick; do not write slots 7–15.
        credits = ct.read_store(SLOT_SPAWN_CREDITS)
        can_credit_spawn = credits > 0
        if self.num_spawned >= MAX_BUILDERS and not can_credit_spawn:
            return

        ti = ct.get_global_resources()
        cost = ct.get_builder_bot_cost()
        if ti < cost:
            return

        pos = ct.get_position()
        dirs = list(DIRECTIONS)
        random.shuffle(dirs)
        for d in dirs:
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

        if self.core_pos is None:
            self._read_core_pos(ct)

        if self.last_pos == pos:
            self.stuck += 1
        else:
            self.stuck = 0
        self.last_pos = pos

        if ct.get_action_cooldown() == 0 and not self.in_relay:
            if not self._try_build_harvester(ct):
                harvester_count = ct.read_store(SLOT_HARVESTER_COUNT)
                if harvester_count >= TARGET_HARVESTERS_APRON:
                    if not self._try_ensure_core_apron(ct):
                        if (
                            BUILD_GUNNERS
                            and harvester_count >= TARGET_HARVESTERS
                        ):
                            self._try_build_gunner(ct)

        if ct.get_action_cooldown() == 0:
            self._try_heal(ct)

        if not self.in_relay:
            self._try_ore_site_task(ct)

        self._move_toward_target(ct)
        self._share_ore(ct)

    def _read_core_pos(self, ct: Controller) -> None:
        ally, _ = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
        if ally[0] != CoreState.UNKNOWN:
            self.core_pos = Position(ally[1], ally[2])

    def _enter_relay(self, ct: Controller, harvester_pos: Position) -> None:
        self.in_relay = True
        self.relay_origin = harvester_pos
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
        self.relay_origin = None
        self.relay_stuck = 0
        self.relay_path = None

    def _complete_relay(self, ct: Controller) -> None:
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
        if self.relay_origin is not None:
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

    def _relay_toward_core(self, ct: Controller) -> None:
        """Walk a committed BFS belt path until it joins the core apron.

        Enemy belts on the path are cleared via stand-and-fire, then we lay ours.
        If relay_origin leaves vision, advance it to the last built path tile and
        keep finishing the path toward the core.
        """
        if self.core_pos is None or self.relay_origin is None:
            self._abort_relay(ct)
            return

        ore_blocked = self._ore_blocked_set(ct)
        pos = ct.get_position()
        origin_visible = ct.is_in_vision(self.relay_origin)
        path = self.relay_path
        work_from = 0

        # Fog fallback: harvester (or prior origin) left vision — advance origin
        # to last successfully built belt so we don't walk back into the fog.
        if not origin_visible:
            if path:
                last_i = last_built_path_index(ct, path, self.core_pos)
                if last_i is not None:
                    self.relay_origin = path[last_i]
                    work_from = last_i + 1
                else:
                    self.relay_origin = pos
                    # Continue from nearest path tile at/behind us if possible
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
            # Keep existing path when origin is in fog — don't drop for path_ok
            # failures on early fog tiles near the old harvester.
        else:
            if path is not None and not self._relay_path_ok(ct, path, ore_blocked):
                path = None
                self.relay_path = None

        if path is None:
            path = plan_conveyor_path(
                ct, self.relay_origin, self.core_pos, ore_blocked=ore_blocked
            )
            self.relay_path = path
            work_from = 0
        if path is None:
            self._abort_relay(ct)
            return

        # Complete if everything from work_from onward is laid
        idx = first_incomplete_index(ct, path, self.core_pos, start=work_from)
        if idx is None:
            self._complete_relay(ct)
            return

        tile = path[idx]
        travel_dir = path_travel_dir(path, idx, self.core_pos)

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
            result = ensure_belt_on_path(ct, tile, travel_dir)
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
        if target is not None and ct.can_fire(target):
            ct.fire(target)
