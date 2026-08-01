"""earl — starter + HiveMind + inbound steal-relay.

Economy-first builders: fingerprint map via HiveMind, claim ore (build or
steal any harvester), then pave a committed conveyor path back to the core.
Wrong-facing allied belts are destroyed and rebuilt; enemy belts are cleared
via stand-and-fire.

Even builders (raiders) abort economy once the enemy core is known (see
attack.py): push the enemy, stand-fire belts on the way, gunners from turn 30
facing into the enemy core, then a 40-turn gunner/sentinel loop. Odd economy builders (1, 3, 5)
keep mining; idle ore recheck every 50 turns. Indices 6+ defend the core from spawn.

Store slots:
  0–1                  Free
  2  SLOT_HARVESTER_COUNT Total harvesters built by the team
  3  SLOT_SENTINEL_COUNT  Living siege sentinels (attack.py)
  4–6                  Free
  7–15                 HiveMind-owned — never write from Player

Ally core from slot 15 via unpack_cores. Ore from ORE_LOCATIONS / local_map.
Core spawns builders toward enemy core (else map center).
"""

from __future__ import annotations

import random
from collections import deque

from attack import (
    is_raider,
    run_attack_builder,
    run_gunner,
    run_sentinel,
    top_up_ammo,
)
from defence import is_defender, run_defender
from fcode import Controller, Direction, EntityType, Environment, Position
from features import ORE_LOCATIONS
from hivemind import CoreState, HiveMind, unpack_cores

DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]
CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]

SLOT_HARVESTER_COUNT = 2
SLOT_PACKED_CORES = 15

MAX_BUILDERS_EARLY = 5
MAX_BUILDERS = 9
FIRST_INTERVAL_SPAWN = 20
SPAWN_INTERVAL = 25
ORE_RECHECK_INTERVAL = 50
BFS_MAX_ITERS = 400


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


def spawn_sites_toward(
    ct: Controller, core_pos: Position, goal: Position
) -> list[Position]:
    """Apron tiles around the full 2x2 core, closest to goal first."""
    footprint = _core_footprint(core_pos)
    footprint_keys = {(t.x, t.y) for t in footprint}
    sites: list[Position] = []
    seen: set[tuple[int, int]] = set()
    for tile in footprint:
        for d in DIRECTIONS:
            cand = tile.add(d)
            key = (cand.x, cand.y)
            if key in seen or key in footprint_keys:
                continue
            if not in_bounds(ct, cand):
                continue
            seen.add(key)
            sites.append(cand)
    sites.sort(key=lambda p: p.distance_squared(goal))
    return sites


def _core_footprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def is_core_adjacent(
    ct: Controller, pos: Position, core_pos: Position | None
) -> bool:
    for d in CARDINALS:
        neighbor = pos.add(d)
        if not in_bounds(ct, neighbor):
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
    return nearest_cardinal(from_pos.direction_to(to_pos))


def can_lay_infrastructure(ct: Controller, pos: Position) -> bool:
    if not in_bounds(ct, pos) or not ct.is_in_vision(pos):
        return False
    return ct.is_tile_empty(pos) and ct.get_tile_env(pos) == Environment.EMPTY


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
    if _enemy_belt_id(ct, pos) is not None:
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
    return nearest_cardinal(pos.direction_to(core_pos))


def plan_conveyor_path(
    ct: Controller,
    start: Position,
    *,
    is_goal,
    ore_blocked: set[tuple[int, int]] | None = None,
) -> list[Position] | None:
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
    return plan_conveyor_path(
        ct,
        start,
        is_goal=lambda p: is_core_adjacent(ct, p, core_pos),
        ore_blocked=ore_blocked,
    )


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
    bid = _allied_belt_id(ct, tile)
    if bid is None:
        return False
    return ct.get_direction(bid) == expected


def first_incomplete_index(
    ct: Controller,
    path: list[Position],
    core_pos: Position,
    *,
    start: int = 0,
) -> int | None:
    for i in range(max(0, start), len(path)):
        want = path_travel_dir(path, i, core_pos)
        if not belt_faces(ct, path[i], want):
            return i
    return None


def last_built_path_index(
    ct: Controller,
    path: list[Position],
    core_pos: Position,
) -> int | None:
    last: int | None = None
    for i, tile in enumerate(path):
        want = path_travel_dir(path, i, core_pos)
        if belt_faces(ct, tile, want):
            last = i
    return last


class Player:
    def __init__(self):
        self.hivemind = HiveMind()
        self.num_spawned = 0

        self.target: Position | None = None
        self.last_pos: Position | None = None
        self.stuck = 0
        self.core_pos: Position | None = None
        self.enemy_core_pos: Position | None = None

        self.ore_site: Position | None = None
        self.skipped_ores: set[tuple[int, int]] = set()
        self.last_ore_recheck_round = 0
        self.ore_recheck_allow_ally = False

        self.in_relay = False
        self.relay_origin: Position | None = None
        self.relay_path: list[Position] | None = None
        self.relay_stuck = 0
        self.relay_built_to = -1  # highest path index known complete this relay

    def run(self, ct: Controller) -> None:
        try:
            self.hivemind.tick(ct)
            etype = ct.get_entity_type()
            if etype == EntityType.CORE:
                self._run_core(ct)
            elif etype == EntityType.BUILDER_BOT:
                self._run_builder(ct)
            elif etype == EntityType.GUNNER:
                self._run_gunner(ct)
            elif etype == EntityType.SENTINEL:
                run_sentinel(ct)
        except Exception:
            return

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------

    def _run_core(self, ct: Controller) -> None:
        # Ammo before spawn early-return so top-up continues after builder cap.
        top_up_ammo(ct)

        if self.num_spawned >= MAX_BUILDERS:
            return

        round_n = ct.get_current_round()
        can_rush = self.num_spawned < MAX_BUILDERS_EARLY
        can_interval = (
            self.num_spawned >= MAX_BUILDERS_EARLY
            and round_n >= FIRST_INTERVAL_SPAWN
            and (round_n - FIRST_INTERVAL_SPAWN) % SPAWN_INTERVAL == 0
        )
        if not can_rush and not can_interval:
            return

        ti = ct.get_global_resources()
        if ti < ct.get_builder_bot_cost():
            return
        pos = ct.get_position()
        _, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
        if enemy[0] != CoreState.UNKNOWN:
            goal = Position(enemy[1], enemy[2])
        else:
            goal = Position(ct.get_map_width() // 2, ct.get_map_height() // 2)
        for spawn_pos in spawn_sites_toward(ct, pos, goal):
            if self.hivemind.spawn_builder(ct, spawn_pos):
                self.num_spawned += 1
                return

    # ------------------------------------------------------------------
    # Builder
    # ------------------------------------------------------------------

    def _run_builder(self, ct: Controller) -> None:
        pos = ct.get_position()
        if self.core_pos is None:
            self._read_core_pos(ct)
        if self.enemy_core_pos is None:
            self._read_enemy_core(ct)

        if self.last_pos == pos:
            self.stuck += 1
        else:
            self.stuck = 0
        self.last_pos = pos

        if is_defender(self.hivemind.my_index):
            self._clear_relay()
            self.ore_site = None
            hold = run_defender(
                ct,
                core_pos=self.core_pos,
                my_index=self.hivemind.my_index,
            )
            if hold is not None:
                self.target = hold
                self._move_toward_target(ct, retarget=False)
            return

        if is_raider(self.hivemind.my_index, self.enemy_core_pos):
            enemy = self.enemy_core_pos
            assert enemy is not None
            self._clear_relay()
            self.ore_site = None
            site = run_attack_builder(
                ct,
                my_index=self.hivemind.my_index,
                local_map=self.hivemind.local_map,
                enemy_core=enemy,
                ally_core=self.core_pos,
            )
            if site is not None:
                self.target = site
            self._move_toward_target(ct, retarget=False)
            return

        if self.in_relay:
            self._relay_toward_core(ct)
            return

        round_n = ct.get_current_round()
        if round_n - self.last_ore_recheck_round >= ORE_RECHECK_INTERVAL:
            self.skipped_ores.clear()
            self.ore_site = None
            self.last_ore_recheck_round = round_n
            self.ore_recheck_allow_ally = True

        if not self.in_relay:
            self._try_ore_site_task(ct)

        if self.in_relay:
            self._relay_toward_core(ct)
            return

        if ct.get_action_cooldown() == 0:
            self._try_heal(ct)

        self._move_toward_target(ct)

    def _read_core_pos(self, ct: Controller) -> None:
        ally, _ = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
        if ally[0] != CoreState.UNKNOWN:
            self.core_pos = Position(ally[1], ally[2])

    def _read_enemy_core(self, ct: Controller) -> None:
        _, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
        if enemy[0] != CoreState.UNKNOWN:
            self.enemy_core_pos = Position(enemy[1], enemy[2])

    def _try_heal(self, ct: Controller) -> None:
        pos = ct.get_position()
        for d in Direction:
            check = pos.add(d)
            if ct.can_heal(check):
                ct.heal(check)
                return

    # ------------------------------------------------------------------
    # Ore site: build / steal / clear belts
    # ------------------------------------------------------------------

    def _skip_ore(self, ore: Position) -> None:
        self.skipped_ores.add((ore.x, ore.y))
        if (
            self.ore_site is not None
            and self.ore_site.x == ore.x
            and self.ore_site.y == ore.y
        ):
            self.ore_site = None

    def _classify_ore_site(self, ct: Controller, ore: Position) -> str:
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

    def _try_ore_site_task(self, ct: Controller) -> None:
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

        if kind == "harvester":
            self._enter_relay(ore)
            return

        if kind == "blocked":
            self._skip_ore(ore)
            self.ore_site = self._pick_ore_site(ct, pos)
            if self.ore_site is not None:
                self.target = self.ore_site
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
                    self._enter_relay(ore)
            return

    def _pick_ore_site(self, ct: Controller, pos: Position) -> Position | None:
        """Nth-nearest known ore by builder index: prefer empty, then steal.

        On idle recheck, fall back to ally harvesters so inbound belts can be
        rebuilt. Builder my_index N takes candidates[(N-1) % len]; 0 → nearest.
        """
        map_id = self.hivemind.map_id
        if map_id <= 0:
            self.ore_recheck_allow_ally = False
            return None
        my_team = ct.get_team()
        allow_ally = self.ore_recheck_allow_ally
        self.ore_recheck_allow_ally = False
        empty: list[tuple[int, Position]] = []
        steal: list[tuple[int, Position]] = []
        ally: list[tuple[int, Position]] = []

        for ox, oy in ORE_LOCATIONS.get(map_id, []):
            if (ox, oy) in self.skipped_ores:
                continue
            tile_info = self.hivemind.local_map.get((ox, oy), {})
            ent = tile_info.get("entity")
            if (
                ent
                and ent.get("type") == EntityType.HARVESTER
                and ent.get("team") == my_team
            ):
                if allow_ally:
                    ore_pos = Position(ox, oy)
                    ally.append((pos.distance_squared(ore_pos), ore_pos))
                continue
            # Synced blocked tile with no entity — treat as already claimed
            if not ent and tile_info.get("passable") is False:
                continue

            ore_pos = Position(ox, oy)
            d = pos.distance_squared(ore_pos)
            if ent and ent.get("type") == EntityType.HARVESTER:
                steal.append((d, ore_pos))
            else:
                empty.append((d, ore_pos))

        candidates = empty if empty else steal
        if not candidates and allow_ally:
            candidates = ally
        if not candidates:
            return None
        candidates.sort(key=lambda t: t[0])
        idx = self.hivemind.my_index
        pick = 0 if idx <= 0 else (idx - 1) % len(candidates)
        return candidates[pick][1]

    # ------------------------------------------------------------------
    # Inbound relay
    # ------------------------------------------------------------------

    def _enter_relay(self, harvester_pos: Position) -> None:
        self.in_relay = True
        self.relay_origin = harvester_pos
        self.relay_stuck = 0
        self.relay_path = None
        self.relay_built_to = -1
        self.target = self.core_pos
        self.ore_site = None
        self.skipped_ores.add((harvester_pos.x, harvester_pos.y))

    def _clear_relay(self) -> None:
        self.in_relay = False
        self.relay_origin = None
        self.relay_path = None
        self.relay_stuck = 0
        self.relay_built_to = -1

    def _abort_relay(self) -> None:
        self._clear_relay()

    def _ore_blocked_set(self) -> set[tuple[int, int]]:
        map_id = self.hivemind.map_id
        if map_id <= 0 or map_id not in ORE_LOCATIONS:
            return set()
        return set(ORE_LOCATIONS[map_id])

    def _relay_path_ok(
        self,
        ct: Controller,
        path: list[Position],
        ore_blocked: set[tuple[int, int]],
    ) -> bool:
        for tile in path:
            if _allied_belt_id(ct, tile) is not None:
                continue
            if not is_layable_or_join(ct, tile, ore_blocked=ore_blocked):
                return False
        return True

    def _adjacent_to_ore(self, pos: Position, ore: Position) -> bool:
        return abs(pos.x - ore.x) + abs(pos.y - ore.y) <= 1

    def _direction_candidates(
        self, ct: Controller, pos: Position, target: Position
    ) -> list[Direction]:
        desired = nearest_cardinal(pos.direction_to(target))
        if desired == Direction.CENTRE:
            return []
        primary = [desired, desired.rotate_left(), desired.rotate_right()]
        remaining = [d for d in DIRECTIONS if d not in primary]
        random.shuffle(remaining)
        return primary + remaining

    def _relay_toward_core(self, ct: Controller) -> None:
        if self.core_pos is None or self.relay_origin is None:
            self._abort_relay()
            return

        ore_blocked = self._ore_blocked_set()
        pos = ct.get_position()
        origin_visible = ct.is_in_vision(self.relay_origin)
        path = self.relay_path

        # Raise watermark from currently visible correctly facing belts.
        if path is not None:
            vis = last_built_path_index(ct, path, self.core_pos)
            if vis is not None:
                self.relay_built_to = max(self.relay_built_to, vis)

        if not origin_visible:
            if path:
                if self.relay_built_to >= 0:
                    self.relay_origin = path[self.relay_built_to]
                else:
                    self.relay_origin = pos
                    best_i = 0
                    best_d = float("inf")
                    for i, tile in enumerate(path):
                        d = pos.distance_squared(tile)
                        if d < best_d:
                            best_d = d
                            best_i = i
                    # Nearest tile is a progress hint when we have no watermark yet.
                    self.relay_built_to = max(self.relay_built_to, best_i - 1)
            else:
                self.relay_origin = pos
        else:
            if path is not None and not self._relay_path_ok(ct, path, ore_blocked):
                # Reseed mid-route from last known belt before discarding indices.
                if self.relay_built_to >= 0:
                    self.relay_origin = path[self.relay_built_to]
                path = None
                self.relay_path = None
                self.relay_built_to = -1

        if path is None:
            path = plan_conveyor_path_to_core(
                ct, self.relay_origin, self.core_pos, ore_blocked=ore_blocked
            )
            self.relay_path = path
            self.relay_built_to = -1
        if path is None:
            self._abort_relay()
            return

        # Never scan behind the high-water mark (fogged early tiles look incomplete).
        work_from = self.relay_built_to + 1
        idx = first_incomplete_index(ct, path, self.core_pos, start=work_from)
        if idx is None:
            self._clear_relay()
            return

        tile = path[idx]
        travel_dir = path_travel_dir(path, idx, self.core_pos)

        # Enemy belts: stand-and-fire, then lay ours next turn
        if _enemy_belt_id(ct, tile) is not None:
            if pos == tile:
                if ct.get_action_cooldown() == 0 and ct.can_fire(tile):
                    ct.fire(tile)
                    self.relay_stuck = 0
                else:
                    self.relay_stuck += 1
                    if self.relay_stuck >= 3:
                        self._abort_relay()
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
                    self._abort_relay()
            return

        # Standing on the work tile — step off so we can destroy/build adjacent
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
                self._abort_relay()
            return

        adjacent = abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1
        if adjacent:
            result = ensure_belt_on_path(ct, tile, travel_dir)
            if result == "failed":
                self.relay_stuck += 1
                if self.relay_stuck >= 3:
                    self._abort_relay()
                return
            self.relay_stuck = 0
            self.relay_built_to = max(self.relay_built_to, idx)
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
                self._abort_relay()

    # ------------------------------------------------------------------
    # Movement / targeting
    # ------------------------------------------------------------------

    def _move_toward_target(self, ct: Controller, *, retarget: bool = True) -> None:
        if ct.get_move_cooldown() != 0:
            return

        pos = ct.get_position()
        if retarget:
            if self.ore_site is not None:
                self.target = self.ore_site
            elif self.target is None or pos == self.target or self.stuck >= 3:
                self.target = self._pick_target(ct)
                self.stuck = 0
        if self.target is None:
            return

        desired = pos.direction_to(self.target)
        if desired == Direction.CENTRE:
            return

        primary = [desired, desired.rotate_left(), desired.rotate_right()]
        for d in primary:
            if self._try_move(ct, d):
                return
        remaining = [d for d in DIRECTIONS if d not in primary]
        random.shuffle(remaining)
        for d in remaining:
            if self._try_move(ct, d):
                return

    def _try_move(self, ct: Controller, d: Direction) -> bool:
        if d == Direction.CENTRE:
            return False
        next_pos = ct.get_position().add(d)
        if not in_bounds(ct, next_pos):
            return False
        if ct.can_move(d):
            ct.move(d)
            return True
        return False

    def _pick_target(self, ct: Controller) -> Position:
        """Nearest known empty/steal ore; hold if nothing left."""
        pos = ct.get_position()

        site = self._pick_ore_site(ct, pos)
        if site is not None:
            self.ore_site = site
            return site

        # Pre-fingerprint: visible uncovered ore
        if self.hivemind.map_id == 0:
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
                self.ore_site = best_ore
                return best_ore

        if self.core_pos is not None:
            return self.core_pos
        return pos

    # ------------------------------------------------------------------
    # Gunner
    # ------------------------------------------------------------------

    def _run_gunner(self, ct: Controller) -> None:
        run_gunner(ct)
