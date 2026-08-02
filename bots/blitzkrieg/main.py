"""blitzkrieg — hyper-aggressive rush bot focused on sub-150 turn victories.

Strategy:
  1. HiveMind fingerprints map on turn 0 to get exact enemy core coordinates.
  2. Core spawns builders non-stop towards enemy core footprint using A*.
  3. Core maintains continuous ammo supply (convert_ammo).
  4. Builders sprint straight to the 2x2 enemy core:
     - Stand orthogonally adjacent and fire directly at the enemy core (2 Ti -> 2 dmg).
     - Build Gunners on empty tiles in range (Chebyshev 1-3) facing enemy core (10 dmg/turn).
     - Destroy any enemy obstacles blocking the rush lane.
     - Heal damaged allies.
"""

from __future__ import annotations

from heapq import heappop, heappush
from typing import Callable

from fcode import (
    Controller,
    Direction,
    EntityType,
    Environment,
    GameConstants,
    Position,
)
from hivemind import CoreState, HiveMind, unpack_cores
from features import WALL_LOCATIONS

CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]
DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]
SLOT_PACKED_CORES = 15


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


def _core_footprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def facing_8way(tile: Position, target: Position) -> Direction:
    d = tile.direction_to(target)
    return d if d != Direction.CENTRE else Direction.NORTH


def facing_toward_core(site: Position, enemy_core: Position) -> Direction:
    footprint = _core_footprint(enemy_core)
    best_tile = min(footprint, key=lambda t: site.distance_squared(t))
    return facing_8way(site, best_tile)


def manhattan(a: Position, b: Position) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)


def astar(
    start: Position,
    goal: Position,
    *,
    is_passable: Callable[[Position], bool],
    max_iters: int = 400,
) -> list[Position] | None:
    start_p = (start.x, start.y)
    goal_p = (goal.x, goal.y)
    if start_p == goal_p:
        return [start]

    open_heap = [(manhattan(start, goal), 0, start_p)]
    came_from: dict[tuple[int, int], tuple[int, int] | None] = {start_p: None}
    g_score: dict[tuple[int, int], int] = {start_p: 0}
    closed: set[tuple[int, int]] = set()
    counter = 1
    iters = 0

    while open_heap and iters < max_iters:
        iters += 1
        _, _, cur = heappop(open_heap)
        if cur in closed:
            continue
        closed.add(cur)

        if cur == goal_p:
            path = [cur]
            while came_from[cur] is not None:
                cur = came_from[cur]  # type: ignore
                path.append(cur)
            path.reverse()
            return [Position(p[0], p[1]) for p in path]

        for d in CARDINALS:
            delta = d.delta()
            nxt = (cur[0] + delta[0], cur[1] + delta[1])
            if nxt in closed:
                continue
            nxt_pos = Position(nxt[0], nxt[1])
            if nxt != goal_p and not is_passable(nxt_pos):
                continue
            tentative = g_score[cur] + 1
            if tentative >= g_score.get(nxt, 1 << 30):
                continue
            came_from[nxt] = cur
            g_score[nxt] = tentative
            heappush(open_heap, (tentative + manhattan(nxt_pos, goal), counter, nxt))
            counter += 1

    return None


def best_gunner_facing(ct: Controller, site: Position, enemy_core: Position) -> Direction | None:
    footprint = _core_footprint(enemy_core)
    cardinals = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]
    diagonals = [Direction.NORTHEAST, Direction.SOUTHEAST, Direction.SOUTHWEST, Direction.NORTHWEST]
    for d in cardinals:
        for t in footprint:
            if ct.can_fire_from(site, d, EntityType.GUNNER, t):
                return d
    for d in diagonals:
        for t in footprint:
            if ct.can_fire_from(site, d, EntityType.GUNNER, t):
                return d
    return None


class Player:
    def __init__(self):
        self.hivemind = HiveMind()
        self.core_pos: Position | None = None
        self.enemy_core_pos: Position | None = None
        self.target: Position | None = None
        self.stuck = 0
        self.last_pos: Position | None = None

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
                self._run_sentinel(ct)
        except Exception:
            return

    # ------------------------------------------------------------------
    # Core Strategy: Hyper-Rush Spawning & Ammo Supply
    # ------------------------------------------------------------------

    def _run_core(self, ct: Controller) -> None:
        pos = ct.get_position()
        self._read_enemy_core(ct)

        # 1. Spawn builder first if under cap and can afford
        if ct.get_action_cooldown() == 0 and ct.get_unit_count() < 5:
            if ct.get_global_resources() >= ct.get_builder_bot_cost():
                if self.enemy_core_pos is not None:
                    goal = self.enemy_core_pos
                else:
                    goal = Position(ct.get_map_width() // 2, ct.get_map_height() // 2)

                spawn_sites = self._spawn_sites_toward(ct, pos, goal)
                for s in spawn_sites:
                    if self.hivemind.spawn_builder(ct, s):
                        break

        # 2. Convert ammo after spawn attempt
        ammo = ct.get_global_ammo()
        ti = ct.get_global_resources()
        if ammo < 50 and ti > 0 and ct.can_convert_ammo(min(20, ti)):
            ct.convert_ammo(min(20, ti))

    def _spawn_sites_toward(
        self, ct: Controller, core_pos: Position, goal: Position
    ) -> list[Position]:
        footprint = _core_footprint(core_pos)
        fp_keys = {(t.x, t.y) for t in footprint}
        sites: list[Position] = []
        w, h = ct.get_map_width(), ct.get_map_height()

        for tile in footprint:
            for d in DIRECTIONS:
                cand = tile.add(d)
                if (cand.x, cand.y) in fp_keys:
                    continue
                if not (0 <= cand.x < w and 0 <= cand.y < h):
                    continue
                if cand not in sites:
                    sites.append(cand)

        walls = set(WALL_LOCATIONS.get(self.hivemind.map_id, []))

        def is_passable(p: Position) -> bool:
            if (p.x, p.y) in fp_keys or (p.x, p.y) in walls:
                return False
            return 0 <= p.x < w and 0 <= p.y < h

        def path_len(p: Position) -> int:
            path = astar(p, goal, is_passable=is_passable, max_iters=200)
            if path is not None:
                return len(path)
            return p.distance_squared(goal) * 100

        sites.sort(key=path_len)
        return sites

    # ------------------------------------------------------------------
    # Builder Bot: Sprint -> Siege -> Core Kill
    # ------------------------------------------------------------------

    def _run_builder(self, ct: Controller) -> None:
        pos = ct.get_position()
        if self.last_pos == pos:
            self.stuck += 1
        else:
            self.stuck = 0
        self.last_pos = pos

        self._read_enemy_core(ct)
        enemy = self.enemy_core_pos
        if enemy is None:
            enemy = Position(ct.get_map_width() // 2, ct.get_map_height() // 2)

        footprint = _core_footprint(enemy)
        fp_set = {(t.x, t.y) for t in footprint}

        # Set target first so all priorities have current target
        gunner_site = self._pick_blitz_gunner_site(ct, enemy, footprint)
        if gunner_site is not None:
            self.target = gunner_site
        else:
            self.target = min(footprint, key=lambda t: pos.distance_squared(t))

        # Priority 1: Build Gunner facing enemy core on adjacent tile
        if ct.get_action_cooldown() == 0 and ct.get_global_resources() >= ct.get_gunner_cost():
            for d in CARDINALS:
                adj = pos.add(d)
                if (adj.x, adj.y) in fp_set:
                    continue
                if ct.is_in_vision(adj) and ct.is_tile_empty(adj) and ct.get_tile_env(adj) == Environment.EMPTY:
                    facing = best_gunner_facing(ct, adj, enemy)
                    if facing is not None and ct.can_build_gunner(adj, facing):
                        ct.build_gunner(adj, facing)
                        return

        # Priority 2: Direct Attack on Enemy Core
        if ct.get_action_cooldown() == 0:
            for tile in footprint:
                if manhattan(pos, tile) == 1 and ct.can_fire(tile):
                    ct.fire(tile)
                    return

        # Priority 3: Clear enemy building/unit on path step to target
        if ct.get_action_cooldown() == 0:
            my_team = ct.get_team()
            desired = nearest_cardinal(pos.direction_to(self.target)) if self.target is not None else Direction.CENTRE
            if desired != Direction.CENTRE:
                adj = pos.add(desired)
                if ct.is_in_vision(adj):
                    bid = ct.get_tile_building_id(adj)
                    bot_id = ct.get_tile_builder_bot_id(adj)
                    target_id = bot_id if bot_id is not None else bid
                    if target_id is not None and ct.get_team(target_id) != my_team:
                        if ct.can_fire(adj):
                            ct.fire(adj)
                            return

        # Priority 4: Heal adjacent friendlies
        if ct.get_action_cooldown() == 0:
            for d in CARDINALS:
                check = pos.add(d)
                if ct.can_heal(check):
                    ct.heal(check)
                    return

        # Priority 5: Move toward target
        self._move_toward_target(ct)

    def _pick_blitz_gunner_site(
        self, ct: Controller, enemy_core: Position, footprint: list[Position]
    ) -> Position | None:
        fp_keys = {(t.x, t.y) for t in footprint}
        pos = ct.get_position()
        w, h = ct.get_map_width(), ct.get_map_height()
        cx, cy = enemy_core.x, enemy_core.y
        best: Position | None = None
        best_dist = float("inf")

        for x in range(cx - 4, cx + 5):
            for y in range(cy - 4, cy + 5):
                if not (0 <= x < w and 0 <= y < h):
                    continue
                key = (x, y)
                if key in fp_keys:
                    continue
                cand = Position(x, y)
                cheb = min(max(abs(cand.x - t.x), abs(cand.y - t.y)) for t in footprint)
                if cheb < 1 or cheb > 3:
                    continue
                if self.hivemind.local_map.get(key, {}).get("passable") is False:
                    continue
                if ct.is_in_vision(cand):
                    if not ct.is_tile_empty(cand) or ct.get_tile_env(cand) != Environment.EMPTY:
                        continue
                facing = best_gunner_facing(ct, cand, enemy_core)
                if facing is None:
                    continue
                diag_pen = 0 if facing in CARDINALS else 200
                score = pos.distance_squared(cand) * 20 + cheb * 100 + diag_pen
                if score < best_dist:
                    best_dist = score
                    best = cand
        return best

    # ------------------------------------------------------------------
    # Movement & Gunner Execution
    # ------------------------------------------------------------------

    def _move_toward_target(self, ct: Controller) -> None:
        if ct.get_move_cooldown() != 0 or self.target is None:
            return
        pos = ct.get_position()
        if pos == self.target:
            return

        w, h = ct.get_map_width(), ct.get_map_height()

        def is_passable(p: Position) -> bool:
            if not (0 <= p.x < w and 0 <= p.y < h):
                return False
            if ct.is_in_vision(p):
                return ct.is_tile_passable(p)
            cell = self.hivemind.local_map.get((p.x, p.y))
            if cell is None:
                return True
            return bool(cell.get("passable", True))

        path = astar(pos, self.target, is_passable=is_passable, max_iters=200)
        if path is not None and len(path) >= 2:
            step = path[1]
            d = nearest_cardinal(pos.direction_to(step))
            if d != Direction.CENTRE and ct.can_move(d):
                ct.move(d)
                return

        # Greedy fallback
        desired = nearest_cardinal(pos.direction_to(self.target))
        if desired != Direction.CENTRE and ct.can_move(desired):
            ct.move(desired)
            return
        primary = [desired.rotate_left(), desired.rotate_right()]
        for d in primary:
            if d != Direction.CENTRE and ct.can_move(d):
                ct.move(d)
                return

    def _run_gunner(self, ct: Controller) -> None:
        my_team = ct.get_team()
        pos = ct.get_position()

        # Fire Core first if on line
        for tile in ct.get_attackable_tiles():
            if not ct.can_fire(tile):
                continue
            bid = ct.get_tile_building_id(tile)
            if bid is not None and ct.get_entity_type(bid) == EntityType.CORE and ct.get_team(bid) != my_team:
                ct.fire(tile)
                return

        # Fire any enemy unit/building on line
        for tile in ct.get_attackable_tiles():
            if not ct.can_fire(tile):
                continue
            bot_id = ct.get_tile_builder_bot_id(tile)
            bid = ct.get_tile_building_id(tile)
            victim = bot_id if bot_id is not None else bid
            if victim is not None and ct.get_team(victim) != my_team:
                ct.fire(tile)
                return

        # Rotate onto enemy core if current facing cannot hit core
        if self.enemy_core_pos is not None:
            footprint = _core_footprint(self.enemy_core_pos)
            cur_dir = ct.get_direction()
            # If current facing can hit ANY core tile, DO NOT ROTATE!
            for t in footprint:
                if ct.can_fire_from(pos, cur_dir, EntityType.GUNNER, t):
                    return

            facing = best_gunner_facing(ct, pos, self.enemy_core_pos)
            if facing is not None and facing != cur_dir and ct.can_rotate(facing):
                ct.rotate(facing)
                return

    def _run_sentinel(self, ct: Controller) -> None:
        my_team = ct.get_team()
        for tile in ct.get_attackable_tiles():
            if not ct.can_fire(tile):
                continue
            bot_id = ct.get_tile_builder_bot_id(tile)
            bid = ct.get_tile_building_id(tile)
            victim = bot_id if bot_id is not None else bid
            if victim is not None and ct.get_team(victim) != my_team:
                ct.fire(tile)
                return

    def _read_enemy_core(self, ct: Controller) -> None:
        _, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
        if enemy[0] != CoreState.UNKNOWN:
            self.enemy_core_pos = Position(enemy[1], enemy[2])
