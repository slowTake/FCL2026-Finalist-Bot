"""Magnum Grokus — map-aware turtle fork.

League/CI bot under bots/. Gitignored dev copy: local_bots/magnum_grokus/.

Map detection (slots 14–15), profile-driven policy (slot 4), and obstacle-aware
L-path movement via florent_lib.make_path_ct.
"""

from __future__ import annotations

import random
import sys
from enum import IntEnum
from pathlib import Path

from fcode import Controller, Direction, EntityType, Environment, Position

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_MAP_FEATURES = _REPO_ROOT / "bots" / "map_detector_bot"
if str(_MAP_FEATURES) not in sys.path:
    sys.path.insert(0, str(_MAP_FEATURES))

from features import MAP_ID_TO_NAME, ORE_LOCATIONS  # noqa: E402
from florent_lib import bfs_next_step, make_path_ct  # noqa: E402
from hivemind import HiveMind  # noqa: E402

# All directions except CENTRE — useful for movement and spawning
DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]

# Cardinal directions only — conveyors and splitters can only face these
CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]

# --- Communication store slot assignments ---
SLOT_CORE_X = 0
SLOT_CORE_Y = 1
SLOT_HARVESTER_COUNT = 2
SLOT_ORE_LOCATION = 3
SLOT_MAP_PROFILE = 4
SLOT_STAGE = 5
SLOT_GUNNER_COUNT = 6
SLOT_MAP_ID = 14
SLOT_PACKED_CORES = 15

MAX_BUILDERS = 5
TARGET_HARVESTERS = 3
TARGET_HARVESTERS_CLOSE = 1
MAX_GUNNERS_CLOSE = 8


class CoreState(IntEnum):
    UNKNOWN = 0
    ASSUMED = 1
    KNOWN = 2


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


def pack_cores(
    ally_state: int,
    ally_x: int,
    ally_y: int,
    enemy_state: int,
    enemy_x: int,
    enemy_y: int,
) -> int:
    ally_bits = (ally_state << 14) | (ally_x << 7) | ally_y
    enemy_bits = (enemy_state << 14) | (enemy_x << 7) | enemy_y
    return (enemy_bits << 16) | ally_bits


def unpack_cores(val: int):
    ally_bits = val & 0xFFFF
    enemy_bits = (val >> 16) & 0xFFFF
    ally = (ally_bits >> 14, (ally_bits >> 7) & 0x7F, ally_bits & 0x7F)
    enemy = (enemy_bits >> 14, (enemy_bits >> 7) & 0x7F, enemy_bits & 0x7F)
    return ally, enemy


def profile_for_map_id(map_id: int) -> MapProfile:
    if map_id in CLOSE_QUARTER_MAPS:
        return MapProfile.CLOSE_QUARTER
    if map_id in CHOKE_MAPS:
        return MapProfile.CHOKE
    if map_id in OPEN_LARGE_MAPS:
        return MapProfile.OPEN_LARGE
    return MapProfile.DEFAULT


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


def _can_lay_infrastructure(ct: Controller, pos: Position) -> bool:
    """True only on plain empty terrain — not ore, walls, or occupied tiles."""
    if not in_bounds(ct, pos):
        return False
    return (
        ct.is_tile_empty(pos)
        and ct.get_tile_env(pos) == Environment.EMPTY
    )


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
        self.relay_home: set[int] = set()

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

        if self.num_spawned >= MAX_BUILDERS:
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

        if ct.get_action_cooldown() == 0:
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
                self._try_build_conveyor_toward_core(ct, build_pos)
                if profile == MapProfile.CLOSE_QUARTER:
                    self._try_build_harvester_flank_gunner(ct, build_pos)
                self.relay_home.add(ct.get_id())
                return True
        return False

    def _try_build_harvester_flank_gunner(self, ct: Controller, harvester_pos: Position) -> bool:
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

    def _try_build_conveyor_toward_core(self, ct: Controller, harvester_pos: Position) -> None:
        if self.core_pos is None:
            return
        if ct.get_global_resources() < ct.get_conveyor_cost():
            return

        best: tuple[int, Position, Direction] | None = None
        for d in CARDINALS:
            conv_pos = harvester_pos.add(d)
            if not _can_lay_infrastructure(ct, conv_pos):
                continue
            facing = nearest_cardinal(conv_pos.direction_to(self.core_pos))
            if facing == Direction.CENTRE:
                continue
            if not ct.can_build_conveyor(conv_pos, facing):
                continue
            dist = conv_pos.distance_squared(self.core_pos)
            if best is None or dist < best[0]:
                best = (dist, conv_pos, facing)

        if best is not None:
            _, conv_pos, facing = best
            ct.build_conveyor(conv_pos, facing)

    def _adjacent_to_core(self, ct: Controller, pos: Position) -> bool:
        for d in DIRECTIONS:
            neighbor = pos.add(d)
            if not in_bounds(ct, neighbor):
                continue
            bid = ct.get_tile_building_id(neighbor)
            if bid is not None and ct.get_entity_type(bid) == EntityType.CORE:
                return True
        return False

    def _try_ensure_core_splitter(self, ct: Controller) -> bool:
        if self.core_pos is None:
            return False
        pos = ct.get_position()
        if not self._adjacent_to_core(ct, pos):
            return False
        if not _can_lay_infrastructure(ct, pos):
            return False
        toward = nearest_cardinal(pos.direction_to(self.core_pos))
        if toward == Direction.CENTRE:
            return False
        if ct.can_build_splitter(pos, toward):
            ct.build_splitter(pos, toward)
            return True
        return False

    def _try_build_gunner(self, ct: Controller, profile: int) -> bool:
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
        if ct.get_move_cooldown() != 0:
            return

        pos = ct.get_position()

        bid = ct.get_id()
        if bid in self.relay_home and self.core_pos is not None:
            if pos.distance_squared(self.core_pos) <= 8:
                self.relay_home.discard(bid)
                self.target = None
            else:
                for d in self._direction_candidates(ct, pos, self.core_pos):
                    if self._try_move(ct, d):
                        return
                return

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

    def _try_move(self, ct: Controller, d: Direction) -> bool:
        if d == Direction.CENTRE:
            return False
        pos = ct.get_position()
        next_pos = pos.add(d)
        if not in_bounds(ct, next_pos):
            return False

        if _can_lay_infrastructure(ct, next_pos) and self.core_pos is not None:
            toward_core = next_pos.direction_to(self.core_pos)
            cardinal = nearest_cardinal(toward_core)
            if self._adjacent_to_core(ct, next_pos):
                if ct.can_build_splitter(next_pos, cardinal):
                    ct.build_splitter(next_pos, cardinal)
                elif ct.can_build_conveyor(next_pos, cardinal):
                    ct.build_conveyor(next_pos, cardinal)
            elif ct.can_build_conveyor(next_pos, cardinal):
                ct.build_conveyor(next_pos, cardinal)

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

    def _pick_target(self, ct: Controller) -> Position:
        pos = ct.get_position()
        profile = ct.read_store(SLOT_MAP_PROFILE)
        harvester_count = ct.read_store(SLOT_HARVESTER_COUNT)
        target_harvesters = target_harvesters_for(profile)

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

        if profile == MapProfile.CLOSE_QUARTER and harvester_count >= TARGET_HARVESTERS_CLOSE:
            if self.core_pos is not None and pos.distance_squared(self.core_pos) > 8:
                return self.core_pos

        if harvester_count >= target_harvesters and self.core_pos is not None:
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
