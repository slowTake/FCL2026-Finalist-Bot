"""Magnum Grokus — self-contained league bot.

Map-aware turtle fork with obstacle-aware L-path movement, relay belt
logistics, and profile-driven policy.

Inlined from: features.py, vision.py, florent_lib.py,
conveyor_pathing.py, hivemind.py (see section headers below).
"""

from __future__ import annotations

import random
from collections import deque
from enum import IntEnum

# fcode imports — provided by engine at runtime, not bundled in league zip
from fcode import Controller, Direction, EntityType, Environment, Position, Team

# ---------------------------------------------------------------------------
# Inlined from bots/map_detector_bot/features.py
# Map fingerprint lookup, ore coordinates, and static wall data.
# ---------------------------------------------------------------------------


# (width, height, team, core_x, core_y) -> (map_id, name, enemy_x, enemy_y); used by HiveMind
KNOWN_MAPS = {
    (18, 18, Team.A, 2, 14): (1, 'atoll', 14, 2),
    (18, 18, Team.B, 14, 2): (1, 'atoll', 2, 14),
    (26, 26, Team.A, 3, 22): (2, 'aurora', 21, 2),
    (26, 26, Team.B, 21, 2): (2, 'aurora', 3, 22),
    (16, 16, Team.A, 2, 11): (3, 'crossfire', 12, 3),
    (16, 16, Team.B, 12, 3): (3, 'crossfire', 2, 11),
    (12, 12, Team.A, 1, 8): (4, 'duel', 9, 2),
    (12, 12, Team.B, 9, 2): (4, 'duel', 1, 8),
    (20, 20, Team.A, 2, 15): (5, 'fjord', 16, 3),
    (20, 20, Team.B, 16, 3): (5, 'fjord', 2, 15),
    (25, 25, Team.A, 2, 20): (6, 'hive', 21, 3),
    (25, 25, Team.B, 21, 3): (6, 'hive', 2, 20),
    (28, 20, Team.A, 2, 8): (7, 'longship', 24, 8),
    (28, 20, Team.B, 24, 8): (7, 'longship', 2, 8),
    (14, 18, Team.A, 2, 2): (8, 'pinch', 2, 14),
    (14, 18, Team.B, 2, 14): (8, 'pinch', 2, 2),
    (24, 24, Team.A, 2, 2): (9, 'quarry', 20, 20),
    (24, 24, Team.B, 20, 20): (9, 'quarry', 2, 2),
    (24, 24, Team.A, 2, 11): (10, 'runestone', 20, 11),
    (24, 24, Team.B, 20, 11): (10, 'runestone', 2, 11),
    (22, 22, Team.A, 2, 17): (11, 'skerry', 18, 3),
    (22, 22, Team.B, 18, 3): (11, 'skerry', 2, 17),
    (10, 10, Team.A, 1, 1): (12, 'sprint', 7, 7),
    (10, 10, Team.B, 7, 7): (12, 'sprint', 1, 1),
    (20, 26, Team.A, 2, 2): (13, 'strait', 2, 22),
    (20, 26, Team.B, 2, 22): (13, 'strait', 2, 2),
    (21, 21, Team.A, 2, 2): (14, 'twins', 2, 17),
    (21, 21, Team.B, 2, 17): (14, 'twins', 2, 2),
    (24, 24, Team.A, 2, 19): (15, 'vault', 20, 3),
    (24, 24, Team.B, 20, 3): (15, 'vault', 2, 19),
}

# map_id -> display name; used for debug logging in Player
MAP_ID_TO_NAME = {
    1: 'atoll',
    2: 'aurora',
    3: 'crossfire',
    4: 'duel',
    5: 'fjord',
    6: 'hive',
    7: 'longship',
    8: 'pinch',
    9: 'quarry',
    10: 'runestone',
    11: 'skerry',
    12: 'sprint',
    13: 'strait',
    14: 'twins',
    15: 'vault',
}

# map_id -> list of (x, y) ore tiles; used for relay pathing and ore targeting
ORE_LOCATIONS = {
    1: [(16, 0), (12, 1), (8, 8), (9, 8), (8, 9), (9, 9), (5, 16), (1, 17)],
    2: [(13, 0), (24, 0), (18, 1), (4, 9), (12, 9), (20, 9), (1, 10), (24, 15), (5, 16), (13, 16), (21, 16), (7, 24), (1, 25), (12, 25)],
    3: [(14, 1), (2, 2), (10, 2), (4, 3), (7, 7), (8, 8), (11, 12), (5, 13), (13, 13), (1, 14)],
    4: [(10, 0), (7, 1), (5, 5), (6, 6), (4, 10), (1, 11)],
    5: [(17, 1), (18, 1), (4, 2), (14, 2), (8, 8), (9, 9), (10, 10), (11, 11), (5, 17), (15, 17), (1, 18), (2, 18)],
    6: [(11, 3), (7, 7), (15, 7), (23, 7), (11, 11), (19, 11), (5, 13), (13, 13), (1, 17), (9, 17), (17, 17), (13, 21)],
    7: [(6, 1), (21, 1), (1, 5), (26, 5), (10, 9), (13, 9), (14, 9), (17, 9), (9, 10), (12, 10), (15, 10), (18, 10), (1, 12), (26, 12), (6, 18), (21, 18)],
    8: [(6, 1), (11, 2), (6, 6), (7, 6), (12, 7), (12, 10), (6, 11), (7, 11), (11, 15), (6, 16)],
    9: [(6, 6), (7, 6), (17, 6), (18, 6), (6, 7), (7, 7), (16, 7), (17, 7), (18, 7), (11, 11), (12, 11), (11, 12), (12, 12), (5, 16), (6, 16), (7, 16), (16, 16), (17, 16), (5, 17), (6, 17), (16, 17), (17, 17)],
    10: [(5, 2), (18, 2), (10, 6), (13, 6), (1, 7), (22, 7), (9, 9), (14, 9), (9, 14), (14, 14), (1, 16), (22, 16), (10, 17), (13, 17), (5, 21), (18, 21)],
    11: [(18, 1), (20, 1), (8, 5), (17, 7), (13, 9), (10, 10), (11, 11), (8, 12), (4, 14), (13, 16), (1, 20), (3, 20)],
    12: [(6, 1), (8, 3), (4, 4), (5, 5), (1, 6), (3, 8)],
    13: [(6, 1), (17, 4), (1, 6), (4, 9), (15, 9), (9, 10), (9, 15), (4, 16), (15, 16), (1, 19), (17, 21), (6, 24)],
    14: [(6, 1), (19, 4), (1, 6), (3, 8), (10, 8), (17, 8), (10, 10), (3, 12), (10, 12), (17, 12), (1, 14), (19, 16), (6, 19)],
    15: [(19, 1), (22, 1), (10, 10), (11, 10), (12, 10), (11, 13), (12, 13), (13, 13), (1, 22), (4, 22)],
}

# map_id -> static wall tiles; seeded into HiveMind local_map at detection
WALL_LOCATIONS = {
    1: [(6, 6), (7, 6), (8, 6), (9, 6), (10, 6), (11, 6), (6, 7), (11, 7), (6, 8), (11, 9), (6, 10), (11, 10), (6, 11), (7, 11), (8, 11), (9, 11), (10, 11), (11, 11)],
    2: [(0, 6), (1, 6), (2, 6), (3, 6), (4, 6), (5, 6), (6, 6), (7, 6), (8, 6), (9, 6), (10, 6), (11, 6), (12, 6), (13, 6), (14, 6), (15, 6), (8, 12), (9, 12), (10, 12), (11, 12), (12, 12), (13, 12), (14, 12), (15, 12), (16, 12), (17, 12), (18, 12), (19, 12), (20, 12), (21, 12), (22, 12), (23, 12), (2, 13), (3, 13), (4, 13), (5, 13), (6, 13), (7, 13), (8, 13), (9, 13), (10, 13), (11, 13), (12, 13), (13, 13), (14, 13), (15, 13), (16, 13), (17, 13), (10, 19), (11, 19), (12, 19), (13, 19), (14, 19), (15, 19), (16, 19), (17, 19), (18, 19), (19, 19), (20, 19), (21, 19), (22, 19), (23, 19), (24, 19), (25, 19)],
    3: [(7, 0), (7, 1), (7, 2), (7, 3), (7, 4), (7, 5), (0, 7), (1, 7), (2, 7), (3, 7), (4, 7), (5, 7), (10, 8), (11, 8), (12, 8), (13, 8), (14, 8), (15, 8), (8, 10), (8, 11), (8, 12), (8, 13), (8, 14), (8, 15)],
    4: [(6, 5), (5, 6)],
    5: [(8, 4), (9, 5), (10, 6), (11, 7), (4, 8), (12, 8), (0, 9), (1, 9), (5, 9), (13, 9), (6, 10), (14, 10), (18, 10), (19, 10), (7, 11), (15, 11), (8, 12), (9, 13), (10, 14), (11, 15)],
    6: [(1, 1), (5, 1), (9, 1), (13, 1), (17, 1), (21, 1), (1, 5), (5, 5), (9, 5), (13, 5), (17, 5), (1, 9), (5, 9), (9, 9), (13, 9), (17, 9), (21, 9), (3, 15), (7, 15), (11, 15), (15, 15), (19, 15), (23, 15), (7, 19), (11, 19), (15, 19), (19, 19), (23, 19), (3, 23), (7, 23), (11, 23), (15, 23), (19, 23), (23, 23)],
    7: [(6, 3), (7, 3), (8, 3), (9, 3), (10, 3), (11, 3), (12, 3), (13, 3), (14, 3), (15, 3), (16, 3), (17, 3), (18, 3), (19, 3), (20, 3), (21, 3), (4, 5), (23, 5), (3, 6), (24, 6), (3, 13), (24, 13), (4, 14), (23, 14), (6, 16), (7, 16), (8, 16), (9, 16), (10, 16), (11, 16), (12, 16), (13, 16), (14, 16), (15, 16), (16, 16), (17, 16), (18, 16), (19, 16), (20, 16), (21, 16)],
    8: [(0, 8), (1, 8), (2, 8), (3, 8), (4, 8), (5, 8), (8, 8), (9, 8), (10, 8), (11, 8), (12, 8), (13, 8), (0, 9), (1, 9), (2, 9), (3, 9), (4, 9), (5, 9), (8, 9), (9, 9), (10, 9), (11, 9), (12, 9), (13, 9)],
    9: [(11, 5), (12, 5), (5, 11), (18, 11), (5, 12), (18, 12), (11, 18), (12, 18)],
    10: [(11, 8), (12, 8), (11, 9), (12, 9), (11, 10), (12, 10), (11, 11), (12, 11), (11, 12), (12, 12), (11, 13), (12, 13), (11, 14), (12, 14), (11, 15), (12, 15)],
    11: [(9, 4), (10, 4), (9, 5), (10, 5), (15, 6), (16, 6), (15, 7), (16, 7), (14, 8), (15, 8), (14, 9), (15, 9), (6, 12), (7, 12), (6, 13), (7, 13), (5, 14), (6, 14), (5, 15), (6, 15), (11, 16), (12, 16), (11, 17), (12, 17)],
    12: [],
    13: [(0, 11), (1, 11), (2, 11), (3, 11), (6, 11), (7, 11), (8, 11), (9, 11), (10, 11), (11, 11), (12, 11), (13, 11), (16, 11), (17, 11), (18, 11), (19, 11), (0, 12), (1, 12), (2, 12), (3, 12), (6, 12), (7, 12), (8, 12), (9, 12), (10, 12), (11, 12), (12, 12), (13, 12), (16, 12), (17, 12), (18, 12), (19, 12), (0, 13), (1, 13), (2, 13), (3, 13), (6, 13), (7, 13), (8, 13), (9, 13), (10, 13), (11, 13), (12, 13), (13, 13), (16, 13), (17, 13), (18, 13), (19, 13), (0, 14), (1, 14), (2, 14), (3, 14), (6, 14), (7, 14), (8, 14), (9, 14), (10, 14), (11, 14), (12, 14), (13, 14), (16, 14), (17, 14), (18, 14), (19, 14)],
    14: [(0, 10), (1, 10), (2, 10), (5, 10), (6, 10), (7, 10), (8, 10), (9, 10), (11, 10), (12, 10), (13, 10), (14, 10), (15, 10), (18, 10), (19, 10), (20, 10)],
    15: [(8, 8), (9, 8), (10, 8), (11, 8), (12, 8), (13, 8), (14, 8), (15, 8), (8, 9), (15, 9), (8, 10), (15, 10), (8, 11), (15, 12), (8, 13), (15, 13), (8, 14), (15, 14), (8, 15), (9, 15), (10, 15), (11, 15), (12, 15), (13, 15), (14, 15), (15, 15)],
}

# ---------------------------------------------------------------------------
# Inlined from bots/map_detector_bot/vision.py
# Pack/unpack team vision grid and enemy intel into communication store slots.
# ---------------------------------------------------------------------------

def pack_vision_grid(x: int, y: int, grid_81: int) -> tuple[int, int, int]:
    """
    Packs X (5 bits), Y (5 bits), and an 81-bit grid into three 32-bit slots.
    Slot 10: 32 bits
    Slot 11: 32 bits
    Slot 12: 27 bits
    """
    payload = (x << 86) | (y << 81) | (grid_81 & ((1 << 81) - 1))
    
    slot_10 = (payload >> 59) & 0xFFFFFFFF
    slot_11 = (payload >> 27) & 0xFFFFFFFF
    slot_12 = payload & 0x7FFFFFF  
    
    return slot_10, slot_11, slot_12

def unpack_vision_grid(slot_10: int, slot_11: int, slot_12: int) -> tuple[int, int, int]:
    """Unpacks the 3 slots back into X, Y, and the 81-bit grid."""
    payload = (slot_10 << 59) | (slot_11 << 27) | (slot_12 & 0x7FFFFFF)
    
    x = (payload >> 86) & 0x1F
    y = (payload >> 81) & 0x1F
    grid_81 = payload & ((1 << 81) - 1)
    
    return x, y, grid_81

def pack_intel_slot(target0: tuple[int, int, int] | None, target1: tuple[int, int, int] | None) -> int:
    """
    Packs two targets into a single 32-bit slot.
    Each target: X (5 bits), Y (5 bits), Type (4 bits) = 14 bits.
    Total used per slot: 28 bits.
    """
    def pack_target(t):
        if not t: return 0
        return (t[0] << 9) | (t[1] << 4) | (t[2] & 0xF)
        
    val0 = pack_target(target0)
    val1 = pack_target(target1)
    
    return (val0 << 14) | val1

def unpack_intel_slot(slot_val: int):
    """Unpacks a slot back into two target tuples (x, y, type) or None."""
    def unpack_target(val):
        if val == 0: return None
        return ((val >> 9) & 0x1F, (val >> 4) & 0x1F, val & 0xF)
        
    t0 = unpack_target((slot_val >> 14) & 0x3FFF)
    t1 = unpack_target(slot_val & 0x3FFF)
    return t0, t1


# ---------------------------------------------------------------------------
# Inlined from florent_lib.py
# Obstacle-aware L-path and BFS movement helpers.
# ---------------------------------------------------------------------------

def is_tile_passable_ct(ct: Controller, pos: Position) -> bool:
    """Return True if a friendly builder bot could stand on this tile."""
    return ct.is_tile_passable(pos)


def make_path_ct(
    ct: Controller,
    start: Position,
    end: Position,
    y_first: bool = False,
) -> Direction:
    """Return the next cardinal step on an L-shaped path toward end.

    Skips a cardinal step when the candidate tile is not passable for a
    friendly builder. Falls back to the other axis or Direction.CENTRE.
    """
    dx = end.x - start.x
    dy = end.y - start.y
    width = ct.get_map_width()
    height = ct.get_map_height()

    if dx == 0 and dy == 0:
        return Direction.CENTRE

    def step_x() -> Direction | None:
        if dx == 0:
            return None
        if dx > 0:
            next_pos = Position(start.x + 1, start.y)
            if (
                0 <= next_pos.x < width
                and 0 <= next_pos.y < height
                and is_tile_passable_ct(ct, next_pos)
            ):
                return Direction.EAST
        else:
            next_pos = Position(start.x - 1, start.y)
            if (
                0 <= next_pos.x < width
                and 0 <= next_pos.y < height
                and is_tile_passable_ct(ct, next_pos)
            ):
                return Direction.WEST
        return None

    def step_y() -> Direction | None:
        if dy == 0:
            return None
        if dy > 0:
            next_pos = Position(start.x, start.y + 1)
            if (
                0 <= next_pos.x < width
                and 0 <= next_pos.y < height
                and is_tile_passable_ct(ct, next_pos)
            ):
                return Direction.SOUTH
        else:
            next_pos = Position(start.x, start.y - 1)
            if (
                0 <= next_pos.x < width
                and 0 <= next_pos.y < height
                and is_tile_passable_ct(ct, next_pos)
            ):
                return Direction.NORTH
        return None

    if y_first:
        return step_y() or step_x() or Direction.CENTRE
    if abs(dx) >= abs(dy):
        return step_x() or step_y() or Direction.CENTRE
    return step_y() or step_x() or Direction.CENTRE


_CARDINAL_DELTAS = (
    (Direction.NORTH, 0, -1),
    (Direction.SOUTH, 0, 1),
    (Direction.EAST, 1, 0),
    (Direction.WEST, -1, 0),
)


def is_walkable_in_local_map(local_map: dict, x: int, y: int) -> bool:
    """Return True if a tile is traversable according to HiveMind local_map."""
    tile = local_map.get((x, y))
    if tile is None:
        return True
    return tile.get("passable", False)


def bfs_next_step(
    local_map: dict,
    start: Position,
    goal: Position,
    width: int,
    height: int,
) -> Direction | None:
    """Return the first cardinal step toward goal using BFS over local_map."""
    if start.x == goal.x and start.y == goal.y:
        return None

    start_key = (start.x, start.y)
    goal_key = (goal.x, goal.y)
    parent: dict[tuple[int, int], tuple[int, int] | None] = {start_key: None}
    queue: deque[tuple[int, int]] = deque([start_key])

    while queue:
        cx, cy = queue.popleft()
        if (cx, cy) == goal_key:
            cur = goal_key
            while parent[cur] not in (None, start_key):
                nxt = parent[cur]
                if nxt is None:
                    return None
                cur = nxt
            if parent[cur] is None:
                return None
            dx = cur[0] - start.x
            dy = cur[1] - start.y
            for direction, ddx, ddy in _CARDINAL_DELTAS:
                if ddx == dx and ddy == dy:
                    return direction
            return None

        for _direction, dx, dy in _CARDINAL_DELTAS:
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            if (nx, ny) in parent:
                continue
            if not is_walkable_in_local_map(local_map, nx, ny):
                continue
            parent[(nx, ny)] = (cx, cy)
            queue.append((nx, ny))

    return None

# ---------------------------------------------------------------------------
# Shared helpers (used by HiveMind, conveyor pathing, and Player)
# ---------------------------------------------------------------------------

# Cardinal directions only — conveyors and splitters can only face these
CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]


class CoreState(IntEnum):
    UNKNOWN = 0
    ASSUMED = 1
    KNOWN = 2


def pack_cores(
    ally_state: int,
    ally_x: int,
    ally_y: int,
    enemy_state: int,
    enemy_x: int,
    enemy_y: int,
) -> int:
    """Pack ally/enemy core state and coordinates into store slot 15."""
    ally_bits = (ally_state << 14) | (ally_x << 7) | ally_y
    enemy_bits = (enemy_state << 14) | (enemy_x << 7) | enemy_y
    return (enemy_bits << 16) | ally_bits


def unpack_cores(val: int):
    """Unpack store slot 15 into ((ally_state, x, y), (enemy_state, x, y))."""
    ally_bits = val & 0xFFFF
    enemy_bits = (val >> 16) & 0xFFFF
    ally = (ally_bits >> 14, (ally_bits >> 7) & 0x7F, ally_bits & 0x7F)
    enemy = (enemy_bits >> 14, (enemy_bits >> 7) & 0x7F, enemy_bits & 0x7F)
    return ally, enemy


def nearest_cardinal(d: Direction) -> Direction:
    """Snap any direction to the nearest cardinal (for belts and builder moves)."""
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
    """Return True if pos is inside the map grid."""
    return 0 <= pos.x < ct.get_map_width() and 0 <= pos.y < ct.get_map_height()


# Inlined from bots/map_detector_bot/hivemind.py
# Shared vision map, map detection, and builder coordination.
# ---------------------------------------------------------------------------

_INTEL_ENTITY_TO_ID = {
    EntityType.CORE: 1,
    EntityType.SENTINEL: 2,
    EntityType.GUNNER: 3,
    EntityType.HARVESTER: 4,
}
_INTEL_ID_TO_ENTITY = {value: key for key, value in _INTEL_ENTITY_TO_ID.items()}


class HiveMind:
    def __init__(self):
        self.local_map = {}
        self.global_turn = 0
        self.my_index = 0
        self.map_id = 0
        self.map_name = "UNKNOWN"
        self._map_detected = False
        self._terrain_seeded = False

    def _seed_static_terrain(self) -> None:
        if self.map_id not in WALL_LOCATIONS:
            return
        for x, y in WALL_LOCATIONS[self.map_id]:
            tile = self.local_map.setdefault((x, y), {})
            tile["passable"] = False
            tile["static"] = True

    def _ensure_terrain_seeded(self) -> None:
        if self._terrain_seeded or self.map_id == 0:
            return
        self._seed_static_terrain()
        self._terrain_seeded = True

    def _detect_and_store_map(self, ct: Controller) -> None:
        if self._map_detected: return

        w = ct.get_map_width()
        h = ct.get_map_height()
        team = ct.get_team()
        pos = ct.get_position()

        fingerprint = (w, h, team, pos.x, pos.y)
        enemy_state = CoreState.UNKNOWN
        enemy_x = 0
        enemy_y = 0

        if fingerprint in KNOWN_MAPS:
            self.map_id, self.map_name, enemy_x, enemy_y = KNOWN_MAPS[fingerprint]
            enemy_state = CoreState.ASSUMED

        ct.write_store(14, self.map_id)

        packed = pack_cores(
            CoreState.KNOWN, pos.x, pos.y,
            enemy_state, enemy_x, enemy_y
        )
        ct.write_store(15, packed)
        self._map_detected = True
        self._ensure_terrain_seeded()

    def _update_local_vision(self, ct: Controller):
        pos = ct.get_position()
        w = ct.get_map_width()
        h = ct.get_map_height()

        entity_positions = {}
        for eid in ct.get_nearby_entities():
            epos = ct.get_position(eid)
            etype = ct.get_entity_type(eid)
            eteam = ct.get_team(eid)
            entity_positions[(epos.x, epos.y)] = {"type": etype, "team": eteam}

        for dy in range(-4, 5):
            for dx in range(-4, 5):
                if dx*dx + dy*dy <= 16:
                    px = pos.x + dx
                    py = pos.y + dy
                    if 0 <= px < w and 0 <= py < h:
                        if (px, py) not in self.local_map:
                            self.local_map[(px, py)] = {}
                        self.local_map[(px, py)]["last_seen_turn"] = self.global_turn
                        if (px, py) in entity_positions:
                            self.local_map[(px, py)]["passable"] = False
                            self.local_map[(px, py)]["entity"] = entity_positions[(px, py)]
                        else:
                            if not self.local_map[(px, py)].get("static"):
                                self.local_map[(px, py)]["passable"] = True
                            if "entity" in self.local_map[(px, py)]:
                                del self.local_map[(px, py)]["entity"]

    def _broadcast_vision(self, ct: Controller):
        pos = ct.get_position()
        grid_81 = 0
        intel_targets = []

        for dy in range(-4, 5):
            for dx in range(-4, 5):
                px = pos.x + dx
                py = pos.y + dy
                idx = (dy + 4) * 9 + (dx + 4)

                if (px, py) in self.local_map and not self.local_map[(px, py)].get("passable", True):
                    grid_81 |= (1 << idx)

                entity = self.local_map.get((px, py), {}).get("entity")
                if entity:
                    etype = entity.get("type")
                    eteam = entity.get("team")
                    if etype in _INTEL_ENTITY_TO_ID and (
                        eteam is None or eteam != ct.get_team()
                    ):
                        if len(intel_targets) < 4:
                            intel_targets.append((px, py, _INTEL_ENTITY_TO_ID[etype]))

        s10, s11, s12 = pack_vision_grid(pos.x, pos.y, grid_81)
        ct.write_store(10, s10)
        ct.write_store(11, s11)
        ct.write_store(12, s12)

        t0 = intel_targets[0] if len(intel_targets) > 0 else None
        t1 = intel_targets[1] if len(intel_targets) > 1 else None
        t2 = intel_targets[2] if len(intel_targets) > 2 else None
        t3 = intel_targets[3] if len(intel_targets) > 3 else None

        ct.write_store(8, pack_intel_slot(t0, t1))
        ct.write_store(9, pack_intel_slot(t2, t3))

    def _sync_vision(self, ct: Controller):
        s10 = ct.read_store(10)
        s11 = ct.read_store(11)
        s12 = ct.read_store(12)

        if s10 == 0 and s11 == 0 and s12 == 0:
            return

        bx, by, grid_81 = unpack_vision_grid(s10, s11, s12)
        w = ct.get_map_width()
        h = ct.get_map_height()

        for dy in range(9):
            for dx in range(9):
                idx = dy * 9 + dx
                is_blocked = bool((grid_81 >> idx) & 1)
                px = bx - 4 + dx
                py = by - 4 + dy

                if 0 <= px < w and 0 <= py < h:
                    if (px, py) not in self.local_map or self.local_map[(px, py)].get("last_seen_turn", -1) < self.global_turn:
                        if (px, py) not in self.local_map:
                            self.local_map[(px, py)] = {}
                        self.local_map[(px, py)]["passable"] = not is_blocked
                        self.local_map[(px, py)]["last_seen_turn"] = self.global_turn

        i8 = ct.read_store(8)
        i9 = ct.read_store(9)
        t0, t1 = unpack_intel_slot(i8)
        t2, t3 = unpack_intel_slot(i9)
        for t in (t0, t1, t2, t3):
            if t:
                tx, ty, ttype = t
                etype = _INTEL_ID_TO_ENTITY.get(ttype)
                if etype is None:
                    continue
                if (tx, ty) not in self.local_map:
                    self.local_map[(tx, ty)] = {}
                self.local_map[(tx, ty)]["entity"] = {"type": etype}

    def spawn_builder(self, ct: Controller, pos) -> bool:
        """Helper to spawn a builder and instantly register it with the HiveMind registry."""
        if ct.can_spawn(pos):
            ct.spawn_builder(pos)
            total = ct.read_store(7)
            ct.write_store(7, total + 1)
            return True
        return False

    def tick(self, ct: Controller):
        """Must be called at the very top of every unit's run() method."""
        is_core = (ct.get_entity_type() == EntityType.CORE)

        # 1. Sync global turn natively from the engine
        self.global_turn = ct.get_current_round()

        # 2. Assign Builder Index on first turn alive
        if ct.get_entity_type() == EntityType.BUILDER_BOT and self.my_index == 0:
            self.my_index = ct.read_store(7)

        # 3. Process Map Detection (Core Only)
        if is_core and self.global_turn == 1:
            self._detect_and_store_map(ct)

        # 4. Read static map knowledge if we haven't
        if self.map_id == 0 and self.global_turn > 1:
            self.map_id = ct.read_store(14)
            self._ensure_terrain_seeded()

        # 5. Always update personal vision first
        self._update_local_vision(ct)

        # 6. Execute Metronome & Sync Protocol
        if self.global_turn % 2 == 0:
            if is_core:
                total_builders = ct.read_store(7)
                if total_builders > 0:
                    current = ct.read_store(13)
                    next_broadcaster = (current % total_builders) + 1
                    ct.write_store(13, next_broadcaster)
            elif ct.get_entity_type() == EntityType.BUILDER_BOT:
                if self.my_index == ct.read_store(13):
                    self._broadcast_vision(ct)
        else:
            self._sync_vision(ct)

# ---------------------------------------------------------------------------
# Inlined from bots/magnum_grokus/conveyor_pathing.py
# Belt path planning and lay/join helpers for harvester-to-core relays.
# ---------------------------------------------------------------------------

def travel_facing(from_pos: Position, to_pos: Position) -> Direction:
    """Cardinal facing from from_pos toward to_pos."""
    return nearest_cardinal(from_pos.direction_to(to_pos))


def can_lay_infrastructure(ct: Controller, pos: Position) -> bool:
    """True only on plain empty terrain — not ore, walls, or occupied tiles."""
    if not in_bounds(ct, pos):
        return False
    if not ct.is_in_vision(pos):
        return False
    return (
        ct.is_tile_empty(pos)
        and ct.get_tile_env(pos) == Environment.EMPTY
    )


def _core_footprint(core_pos: Position) -> list[Position]:
    """Core is a 2x2; core_pos is treated as the footprint origin (min x/y)."""
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


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


def is_layable_or_join(
    ct: Controller,
    pos: Position,
    *,
    ore_blocked: set[tuple[int, int]] | None = None,
) -> bool:
    """Empty non-ore tile, or allied conveyor/splitter. Optimistic out of vision."""
    if not in_bounds(ct, pos):
        return False
    if ore_blocked is not None and (pos.x, pos.y) in ore_blocked:
        return False
    if not ct.is_in_vision(pos):
        return True
    if _allied_belt_id(ct, pos) is not None:
        return True
    return can_lay_infrastructure(ct, pos)


def facing_into_core(pos: Position, core_pos: Position) -> Direction:
    """Travel facing from a Core-adjacent tile into the Core footprint."""
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
    """BFS over layable/join tiles from start (or its neighbors) to a Core-adj tile.

    Returns the path of belt tiles including the goal, or None if unreachable.
    If start itself is not layable (e.g. harvester), seeds from layable cardinal
    neighbors of start.
    """
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
        if is_core_adjacent(ct, cur, core_pos):
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
    """Facing for path[index]: toward next hop, or into Core on the last tile."""
    tile = path[index]
    if index + 1 < len(path):
        return travel_facing(tile, path[index + 1])
    return facing_into_core(tile, core_pos)


def ensure_belt_on_path(
    ct: Controller, tile: Position, travel_dir: Direction
) -> str:
    """Join if allied belt already faces travel_dir; else destroy+rebuild; else build.

    Returns: 'joined' | 'built' | 'failed'
    Builder must be orthogonally adjacent to tile. Does not move.
    """
    if travel_dir == Direction.CENTRE:
        return "failed"

    bid = _allied_belt_id(ct, tile)
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
    """True if tile has an allied belt facing expected."""
    bid = _allied_belt_id(ct, tile)
    if bid is None:
        return False
    return ct.get_direction(bid) == expected


def path_is_complete(
    ct: Controller, path: list[Position], core_pos: Position
) -> bool:
    """True when every path tile has an allied belt with correct travel facing."""
    if not path:
        return False
    for i, tile in enumerate(path):
        want = path_travel_dir(path, i, core_pos)
        if not belt_faces(ct, tile, want):
            return False
    return True


def first_incomplete_index(
    ct: Controller, path: list[Position], core_pos: Position
) -> int | None:
    """Index of first path tile missing or wrong-facing; None if complete."""
    for i, tile in enumerate(path):
        want = path_travel_dir(path, i, core_pos)
        if not belt_faces(ct, tile, want):
            return i
    return None

# ---------------------------------------------------------------------------
# Magnum Grokus — strategy, store slots, and Player entrypoint
# ---------------------------------------------------------------------------

# All directions except CENTRE — useful for movement and spawning
DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]

# --- Communication store slot assignments ---
SLOT_CORE_X = 0
SLOT_CORE_Y = 1
SLOT_HARVESTER_COUNT = 2
SLOT_ORE_LOCATION = 3
SLOT_MAP_PROFILE = 4
SLOT_STAGE = 5
SLOT_GUNNER_COUNT = 6
SLOT_SPAWN_CREDITS = 7
SLOT_ORPHAN_RELAY = 8
SLOT_MAP_ID = 14
SLOT_PACKED_CORES = 15

BUILDER_FLOOR = 5
BUILDER_CAP = 8
TARGET_HARVESTERS = 10
TARGET_HARVESTERS_CLOSE = 1
MAX_GUNNERS_CLOSE = 8
BUILD_GUNNERS = False  # disable while debugging belt logistics


class MapProfile(IntEnum):
    DEFAULT = 0
    CLOSE_QUARTER = 1
    CHOKE = 2
    OPEN_LARGE = 3


class Stage(IntEnum):
    OPENING = 0
    EXPAND = 1
    DEFEND = 2
    PUSH = 3


CLOSE_QUARTER_MAPS = {4, 12}  # duel, sprint
CHOKE_MAPS = {2, 3, 8, 13}  # aurora, crossfire, pinch, strait
OPEN_LARGE_MAPS = {6, 7, 9, 11}  # hive, longship, quarry, skerry


def pack_pos(pos: Position) -> int:
    return ((pos.x + 1) << 16) | (pos.y + 1)


def unpack_pos(val: int) -> Position | None:
    if val == 0:
        return None
    return Position((val >> 16) - 1, (val & 0xFFFF) - 1)


def profile_for_map_id(map_id: int) -> MapProfile:
    if map_id in CLOSE_QUARTER_MAPS:
        return MapProfile.CLOSE_QUARTER
    if map_id in CHOKE_MAPS:
        return MapProfile.CHOKE
    if map_id in OPEN_LARGE_MAPS:
        return MapProfile.OPEN_LARGE
    return MapProfile.DEFAULT


def splitter_output_dirs(facing: Direction) -> list[Direction]:
    facing = nearest_cardinal(facing)
    left = facing.rotate_left().rotate_left()
    right = facing.rotate_right().rotate_right()
    return [facing, left, right]


def target_harvesters_for(profile: int) -> int:
    if profile == MapProfile.CLOSE_QUARTER:
        return TARGET_HARVESTERS_CLOSE
    return TARGET_HARVESTERS

class Player:
    def __init__(self):
        self.hivemind = HiveMind()
        self.num_spawned = 0
        self.target: Position | None = None
        self.last_pos: Position | None = None
        self.stuck = 0
        self.core_pos: Position | None = None
        self.map_detected = False
        self.l_path_y_first = False
        self.in_relay = False
        self.relay_origin: Position | None = None
        self.relay_stuck = 0

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

    def _sync_map_policy(self, ct: Controller) -> None:
        map_id = self.hivemind.map_id or ct.read_store(SLOT_MAP_ID)
        if map_id == 0:
            return

        profile = profile_for_map_id(map_id)
        ct.write_store(SLOT_MAP_PROFILE, profile)

        harvesters = ct.read_store(SLOT_HARVESTER_COUNT)
        if profile == MapProfile.CLOSE_QUARTER:
            stage = Stage.DEFEND if harvesters >= TARGET_HARVESTERS_CLOSE else Stage.OPENING
        elif harvesters >= TARGET_HARVESTERS:
            stage = Stage.EXPAND
        else:
            stage = Stage.OPENING
        ct.write_store(SLOT_STAGE, stage)

        if not self.map_detected:
            ally, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
            print(
                f"DETECTED MAP {MAP_ID_TO_NAME.get(map_id, 'UNKNOWN')} "
                f"(ID: {map_id}, profile={profile.name}) | "
                f"ALLY: {ally[1]},{ally[2]} | ENEMY: {enemy[1]},{enemy[2]}"
            )
            self.map_detected = True

    def _run_core(self, ct: Controller) -> None:
        pos = ct.get_position()
        ct.write_store(SLOT_CORE_X, pos.x)
        ct.write_store(SLOT_CORE_Y, pos.y)
        self._sync_map_policy(ct)

        living = max(0, ct.get_unit_count() - 1)
        credits = ct.read_store(SLOT_SPAWN_CREDITS)
        if living < BUILDER_FLOOR:
            spend_credit = False
        elif credits > 0 and living < BUILDER_CAP:
            spend_credit = True
        else:
            return

        ti = ct.get_global_resources()
        cost = ct.get_builder_bot_cost()
        if ti < cost:
            return

        dirs = list(DIRECTIONS)
        random.shuffle(dirs)
        for d in dirs:
            spawn_pos = pos.add(d)
            if self.hivemind.spawn_builder(ct, spawn_pos):
                self.num_spawned += 1
                if spend_credit:
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

        profile = ct.read_store(SLOT_MAP_PROFILE)
        harvester_count = ct.read_store(SLOT_HARVESTER_COUNT)
        target_harvesters = target_harvesters_for(profile)
        in_defend = (
            profile == MapProfile.CLOSE_QUARTER
            and harvester_count >= TARGET_HARVESTERS_CLOSE
        )

        if ct.get_action_cooldown() == 0 and not self.in_relay:
            if not self._try_contest_enemy_harvester(ct):
                if not self._try_build_harvester(ct):
                    if in_defend or harvester_count >= target_harvesters:
                        if not self._try_ensure_core_splitter(ct):
                            self._try_build_gunner(ct, profile)

        if ct.get_action_cooldown() == 0:
            self._try_heal(ct)

        self._move_toward_target(ct)
        self._share_ore(ct)

    def _read_core_pos(self, ct: Controller) -> None:
        x = ct.read_store(SLOT_CORE_X)
        y = ct.read_store(SLOT_CORE_Y)
        if x > 0 or y > 0:
            self.core_pos = Position(x, y)

    def _enemy_core_pos(self, ct: Controller) -> Position | None:
        _, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
        if enemy[0] == CoreState.UNKNOWN:
            return None
        return Position(enemy[1], enemy[2])

    def _gunner_cap_reached(self, ct: Controller, profile: int) -> bool:
        if profile != MapProfile.CLOSE_QUARTER:
            return False
        return ct.read_store(SLOT_GUNNER_COUNT) >= MAX_GUNNERS_CLOSE

    def _record_gunner_built(self, ct: Controller) -> None:
        ct.write_store(SLOT_GUNNER_COUNT, ct.read_store(SLOT_GUNNER_COUNT) + 1)

    def _enter_relay(self, ct: Controller, harvester_pos: Position) -> None:
        self.in_relay = True
        self.relay_origin = harvester_pos
        self.relay_stuck = 0
        self.target = self.core_pos
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
        profile = ct.read_store(SLOT_MAP_PROFILE)
        for d in Direction:
            build_pos = pos.add(d)
            if ct.can_build_harvester(build_pos):
                ct.build_harvester(build_pos)
                count = ct.read_store(SLOT_HARVESTER_COUNT)
                ct.write_store(SLOT_HARVESTER_COUNT, count + 1)
                self._enter_relay(ct, build_pos)
                if profile == MapProfile.CLOSE_QUARTER:
                    self._try_build_harvester_flank_gunner(ct, build_pos)
                return True
        return False

    def _try_contest_enemy_harvester(self, ct: Controller) -> bool:
        """Contest: fire at enemy harvester; replace with ours when the tile clears."""
        pos = ct.get_position()
        enemy_tile: Position | None = None
        for tile in ct.get_nearby_tiles():
            bid = ct.get_tile_building_id(tile)
            if bid is None:
                continue
            if ct.get_entity_type(bid) != EntityType.HARVESTER:
                continue
            if ct.get_team(bid) == ct.get_team():
                continue
            enemy_tile = tile
            break

        if enemy_tile is None:
            return False

        self.target = enemy_tile
        adjacent = abs(pos.x - enemy_tile.x) + abs(pos.y - enemy_tile.y) == 1
        if not adjacent:
            return False

        bid = ct.get_tile_building_id(enemy_tile)
        if bid is not None and ct.get_team(bid) != ct.get_team():
            if ct.can_fire(enemy_tile):
                ct.fire(enemy_tile)
                return True
            return False

        # Tile cleared — place our harvester on the ore.
        if (
            ct.get_tile_env(enemy_tile) == Environment.ORE_TITANIUM
            and ct.can_build_harvester(enemy_tile)
        ):
            ct.build_harvester(enemy_tile)
            count = ct.read_store(SLOT_HARVESTER_COUNT)
            ct.write_store(SLOT_HARVESTER_COUNT, count + 1)
            self._enter_relay(ct, enemy_tile)
            return True
        return False

    def _try_build_harvester_flank_gunner(self, ct: Controller, harvester_pos: Position) -> bool:
        if not BUILD_GUNNERS:
            return False
        if self._gunner_cap_reached(ct, MapProfile.CLOSE_QUARTER):
            return False
        enemy_core = self._enemy_core_pos(ct)
        if enemy_core is None:
            return False
        ti = ct.get_global_resources()
        if ti < ct.get_gunner_cost():
            return False

        for d in CARDINALS:
            build_pos = harvester_pos.add(d)
            facing = nearest_cardinal(build_pos.direction_to(enemy_core))
            if facing == Direction.CENTRE:
                continue
            if ct.can_build_gunner(build_pos, facing):
                ct.build_gunner(build_pos, facing)
                self._record_gunner_built(ct)
                return True
        return False

    def _try_ensure_core_splitter(self, ct: Controller) -> bool:
        """No splitters while debugging belts / gunners off."""
        return False

    def _try_build_gunner(self, ct: Controller, profile: int) -> bool:
        if not BUILD_GUNNERS:
            return False
        if self._gunner_cap_reached(ct, profile):
            return False
        ti = ct.get_global_resources()
        if ti < ct.get_gunner_cost():
            return False

        pos = ct.get_position()
        if self.core_pos is None or pos.distance_squared(self.core_pos) > 18:
            return False

        enemy_core = self._enemy_core_pos(ct)

        for tile in ct.get_nearby_tiles(dist_sq=2):
            bid = ct.get_tile_building_id(tile)
            if bid is None or ct.get_entity_type(bid) != EntityType.SPLITTER:
                continue
            facing = ct.get_direction(bid)
            for out_dir in splitter_output_dirs(facing):
                build_pos = tile.add(out_dir)
                if not in_bounds(ct, build_pos):
                    continue
                core_bid = ct.get_tile_building_id(build_pos)
                if core_bid is not None and ct.get_entity_type(core_bid) == EntityType.CORE:
                    continue
                if ct.can_build_gunner(build_pos, out_dir):
                    ct.build_gunner(build_pos, out_dir)
                    self._record_gunner_built(ct)
                    return True

        if enemy_core is not None:
            facing = nearest_cardinal(pos.direction_to(enemy_core))
            if facing != Direction.CENTRE:
                for d in CARDINALS:
                    build_pos = pos.add(d)
                    if not in_bounds(ct, build_pos):
                        continue
                    if ct.can_build_gunner(build_pos, facing):
                        ct.build_gunner(build_pos, facing)
                        self._record_gunner_built(ct)
                        return True

        facing = pos.direction_to(self.core_pos).opposite()
        if facing == Direction.CENTRE:
            facing = random.choice(CARDINALS)
        for d in Direction:
            build_pos = pos.add(d)
            if not in_bounds(ct, build_pos):
                continue
            if ct.can_build_gunner(build_pos, facing):
                ct.build_gunner(build_pos, facing)
                self._record_gunner_built(ct)
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
    # Builder bot — movement
    # ------------------------------------------------------------------

    def _direction_candidates(
        self, ct: Controller, pos: Position, target: Position
    ) -> list[Direction]:
        w = ct.get_map_width()
        h = ct.get_map_height()
        desired = bfs_next_step(
            self.hivemind.local_map, pos, target, w, h
        )
        if desired is None:
            desired = make_path_ct(
                ct, pos, target, y_first=self.l_path_y_first
            )
        if desired == Direction.CENTRE:
            desired = nearest_cardinal(pos.direction_to(target))
        if desired == Direction.CENTRE:
            return []

        primary = [desired, desired.rotate_left(), desired.rotate_right()]
        remaining = [d for d in DIRECTIONS if d not in primary]
        random.shuffle(remaining)
        return primary + remaining

    def _move_toward_target(self, ct: Controller) -> None:
        if not self.in_relay:
            self._try_claim_orphan(ct)

        if self.in_relay:
            self._relay_toward_core(ct)
            return

        if ct.get_move_cooldown() != 0:
            return

        pos = ct.get_position()

        if self.target is None or pos == self.target or self.stuck >= 3:
            if self.stuck >= 3:
                self.l_path_y_first = not self.l_path_y_first
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
        map_id = self.hivemind.map_id or ct.read_store(SLOT_MAP_ID)
        if map_id <= 0 or map_id not in ORE_LOCATIONS:
            return set()
        return set(ORE_LOCATIONS[map_id])

    def _clear_relay(self) -> None:
        self.in_relay = False
        self.relay_origin = None
        self.relay_stuck = 0

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
        if self.relay_origin is not None:
            ct.write_store(SLOT_ORPHAN_RELAY, pack_pos(self.relay_origin))
        self._clear_relay()

    def _relay_toward_core(self, ct: Controller) -> None:
        """Walk the layable BFS path, laying travel-facing belts until Core-adj."""
        if self.core_pos is None or self.relay_origin is None:
            self._abort_relay(ct)
            return

        ore_blocked = self._ore_blocked_set(ct)
        path = plan_conveyor_path(
            ct, self.relay_origin, self.core_pos, ore_blocked=ore_blocked
        )
        if path is None:
            self._abort_relay(ct)
            return
        if path_is_complete(ct, path, self.core_pos):
            self._complete_relay(ct)
            return

        idx = first_incomplete_index(ct, path, self.core_pos)
        if idx is None:
            self._complete_relay(ct)
            return

        tile = path[idx]
        travel_dir = path_travel_dir(path, idx, self.core_pos)
        pos = ct.get_position()

        # Cannot build on our own tile — step off first.
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

    def _try_move(self, ct: Controller, d: Direction) -> bool:
        """Move one step (no opportunistic belt paving)."""
        if d == Direction.CENTRE:
            return False
        pos = ct.get_position()
        next_pos = pos.add(d)
        if not in_bounds(ct, next_pos):
            return False
        if ct.can_move(d):
            ct.move(d)
            return True
        return False

    def _ore_has_harvester(self, ct: Controller, ore_pos: Position) -> bool:
        if not ct.is_in_vision(ore_pos):
            return False
        bid = ct.get_tile_building_id(ore_pos)
        if bid is None:
            return False
        return ct.get_entity_type(bid) == EntityType.HARVESTER

    def _nearest_known_ore(self, ct: Controller, pos: Position) -> Position | None:
        map_id = self.hivemind.map_id or ct.read_store(SLOT_MAP_ID)
        if map_id <= 0 or map_id not in ORE_LOCATIONS:
            return None

        w = ct.get_map_width()
        h = ct.get_map_height()
        candidates: list[tuple[int, Position]] = []
        for x, y in ORE_LOCATIONS[map_id]:
            ore_pos = Position(x, y)
            if not in_bounds(ct, ore_pos):
                continue
            if self._ore_has_harvester(ct, ore_pos):
                continue
            candidates.append((pos.distance_squared(ore_pos), ore_pos))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0])
        for _dist, ore_pos in candidates:
            if bfs_next_step(self.hivemind.local_map, pos, ore_pos, w, h) is not None:
                return ore_pos
        return candidates[0][1]

    def _scout_target(self, ct: Controller) -> Position:
        """Scout toward enemy Core, else opposite side of the map from ally Core."""
        enemy = self._enemy_core_pos(ct)
        if enemy is not None:
            return enemy
        w, h = ct.get_map_width(), ct.get_map_height()
        if self.core_pos is not None:
            return Position(w - 1 - self.core_pos.x, h - 1 - self.core_pos.y)
        return Position(w // 2, h // 2)

    def _pick_target(self, ct: Controller) -> Position:
        pos = ct.get_position()

        orphan = unpack_pos(ct.read_store(SLOT_ORPHAN_RELAY))
        if orphan is not None:
            return orphan

        best_ore = None
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

        known_ore = self._nearest_known_ore(ct, pos)
        if known_ore is not None and pos.distance_squared(known_ore) > 0:
            return known_ore

        shared = unpack_pos(ct.read_store(SLOT_ORE_LOCATION))
        if shared is not None and pos.distance_squared(shared) > 4:
            return shared

        # Prefer Contest target if an enemy harvester is visible.
        for tile in ct.get_nearby_tiles():
            bid = ct.get_tile_building_id(tile)
            if bid is None:
                continue
            if ct.get_entity_type(bid) != EntityType.HARVESTER:
                continue
            if ct.get_team(bid) == ct.get_team():
                continue
            return tile

        return self._scout_target(ct)

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
