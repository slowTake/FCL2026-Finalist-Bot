"""Magnum Grokus — mid-ladder STRATEGY.md bot.

Portable under local_bots/ (gitignored). Lift the whole magnum_grokus/ folder out.

Scope (STRATEGY mid-ladder):
  - Full Harvester → Core chains with Splitter last-mile
  - Conveyor/splitter-fed Gunner ammo (fcode 2.2 — no convert_ammo)
  - Store roles + ore claims
  - Bounded BFS pathfinding
  - Heal + logistics sabotage (Raider)

Roles: Scout → Miner → Router → Defender → Raider (late)
"""

from __future__ import annotations

import random
from collections import deque

from fcode import Controller, Direction, EntityType, Environment, Position

DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]
CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]

# --- Store ---
SLOT_CORE_X = 0
SLOT_CORE_Y = 1
SLOT_STAGE = 2
SLOT_HARVESTER_COUNT = 3
SLOT_ORE_PACKED = 4
SLOT_THREAT = 5
SLOT_NEXT_ROLE = 6
# 7-10 reserved for explicit role slots if needed later
SLOT_CLAIMED_ORE = 11
SLOT_RAID_PACKED = 12

STAGE_OPENING = 0
STAGE_EXPAND = 1
STAGE_DEFEND = 2
STAGE_PUSH = 3

ROLE_SCOUT = 0
ROLE_MINER = 1
ROLE_ROUTER = 2
ROLE_DEFENDER = 3
ROLE_RAIDER = 4

MAX_BUILDERS = 5
TARGET_HARVESTERS = 3
BFS_LIMIT = 100


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


def splitter_output_dirs(facing: Direction) -> list[Direction]:
    facing = nearest_cardinal(facing)
    left = facing.rotate_left().rotate_left()
    right = facing.rotate_right().rotate_right()
    return [facing, left, right]


class Player:
    def __init__(self) -> None:
        self.num_spawned = 0
        self.core_pos: Position | None = None
        self.role: int | None = None
        self.role_claimed = False

        self.target: Position | None = None
        self.path_goal: Position | None = None
        self.path_step: Direction | None = None
        self.last_pos: Position | None = None
        self.stuck = 0

        self.harvester_pos: Position | None = None
        self.routing = False
        self.scan_cursor = 0

    def run(self, ct: Controller) -> None:
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
        pos = ct.get_position()
        ct.write_store(SLOT_CORE_X, pos.x)
        ct.write_store(SLOT_CORE_Y, pos.y)

        harvesters = ct.read_store(SLOT_HARVESTER_COUNT)
        rnd = ct.get_current_round()
        threat = ct.read_store(SLOT_THREAT)
        if threat or rnd >= 400:
            stage = STAGE_PUSH
        elif harvesters >= TARGET_HARVESTERS:
            stage = STAGE_DEFEND
        elif harvesters >= 1:
            stage = STAGE_EXPAND
        else:
            stage = STAGE_OPENING
        ct.write_store(SLOT_STAGE, stage)

        if self.num_spawned >= MAX_BUILDERS:
            return
        if ct.get_global_resources() < ct.get_builder_bot_cost():
            return

        dirs = list(DIRECTIONS)
        random.shuffle(dirs)
        for d in dirs:
            spawn_pos = pos.add(d)
            if not in_bounds(ct, spawn_pos):
                continue
            if ct.can_spawn(spawn_pos):
                ct.spawn_builder(spawn_pos)
                self.num_spawned += 1
                return

    # ------------------------------------------------------------------
    # Builder dispatch
    # ------------------------------------------------------------------

    def _run_builder(self, ct: Controller) -> None:
        self._ensure_core(ct)
        self._claim_role(ct)
        self._update_stuck(ct.get_position())
        self._sense_threat(ct)
        self._share_ore(ct)

        role = self._effective_role(ct)

        # Action phase
        if ct.get_action_cooldown() == 0:
            healed = role != ROLE_RAIDER and self._try_heal(ct)
            if not healed:
                if role == ROLE_RAIDER:
                    self._run_raider(ct)
                elif role == ROLE_DEFENDER:
                    self._run_defender(ct)
                elif role == ROLE_ROUTER or self.routing:
                    self._run_router(ct)
                elif role in (ROLE_MINER, ROLE_SCOUT):
                    # Scouts mine opportunistically; dedicated miners always try
                    self._run_miner(ct)
                elif role == ROLE_ROUTER:
                    # Routers also grab ore if economy is behind
                    if ct.read_store(SLOT_HARVESTER_COUNT) < TARGET_HARVESTERS:
                        self._run_miner(ct)
                    else:
                        self._run_router(ct)

        # Move phase — non-raiders lay toward Core (turtle-style logistics)
        if ct.get_move_cooldown() != 0:
            return
        lay = role != ROLE_RAIDER
        if role == ROLE_RAIDER:
            self._run_raider_move(ct)
        elif role == ROLE_DEFENDER:
            self._run_defender_move(ct, lay_chain=lay)
        elif role == ROLE_ROUTER or self.routing:
            self._run_router_move(ct)
        elif role == ROLE_MINER:
            self._run_miner_move(ct, lay_chain=lay)
        else:
            self._run_scout_move(ct, lay_chain=lay)

    def _effective_role(self, ct: Controller) -> int:
        role = self.role if self.role is not None else ROLE_SCOUT
        stage = ct.read_store(SLOT_STAGE)
        # Promote idle defenders/scouts into raid once we're pushing
        if stage >= STAGE_PUSH and role in (ROLE_SCOUT, ROLE_DEFENDER):
            if ct.read_store(SLOT_HARVESTER_COUNT) >= TARGET_HARVESTERS:
                return ROLE_RAIDER
        # Early: scouts become miners when ore is known
        if role == ROLE_SCOUT and (
            unpack_pos(ct.read_store(SLOT_ORE_PACKED)) is not None
            or unpack_pos(ct.read_store(SLOT_CLAIMED_ORE)) is not None
            or self._visible_unclaimed_ore(ct)
        ):
            return ROLE_MINER
        return role

    def _claim_role(self, ct: Controller) -> None:
        if self.role_claimed:
            return
        nxt = ct.read_store(SLOT_NEXT_ROLE)
        # Hand out Scout, Miner, Router, Defender, then Router forever
        assignment = [ROLE_SCOUT, ROLE_MINER, ROLE_ROUTER, ROLE_DEFENDER]
        self.role = assignment[nxt] if nxt < len(assignment) else ROLE_ROUTER
        ct.write_store(SLOT_NEXT_ROLE, nxt + 1)
        self.role_claimed = True

    def _ensure_core(self, ct: Controller) -> None:
        if self.core_pos is not None:
            return
        x, y = ct.read_store(SLOT_CORE_X), ct.read_store(SLOT_CORE_Y)
        if x > 0 or y > 0:
            self.core_pos = Position(x, y)
            return
        for tile in ct.get_nearby_tiles():
            bid = ct.get_tile_building_id(tile)
            if bid is not None and ct.get_entity_type(bid) == EntityType.CORE:
                self.core_pos = tile
                return

    def _update_stuck(self, pos: Position) -> None:
        if self.last_pos == pos:
            self.stuck += 1
        else:
            self.stuck = 0
        self.last_pos = pos
        if self.stuck >= 3:
            self.target = None
            self.path_goal = None
            self.path_step = None
            self.stuck = 0

    # ------------------------------------------------------------------
    # Sensing / comms
    # ------------------------------------------------------------------

    def _share_ore(self, ct: Controller) -> None:
        for tile in ct.get_nearby_tiles():
            if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
                continue
            if ct.get_tile_building_id(tile) is not None:
                continue
            ct.write_store(SLOT_ORE_PACKED, pack_pos(tile))
            return

    def _sense_threat(self, ct: Controller) -> None:
        my = ct.get_team()
        for eid in ct.get_nearby_entities():
            if ct.get_team(eid) != my:
                ct.write_store(SLOT_THREAT, 1)
                # Prefer enemy logistics as raid targets
                et = ct.get_entity_type(eid)
                if et in (EntityType.CONVEYOR, EntityType.SPLITTER, EntityType.HARVESTER):
                    ct.write_store(SLOT_RAID_PACKED, pack_pos(ct.get_position(eid)))
                return

    def _visible_unclaimed_ore(self, ct: Controller) -> Position | None:
        claimed = unpack_pos(ct.read_store(SLOT_CLAIMED_ORE))
        best = None
        best_d = 10**9
        pos = ct.get_position()
        for tile in ct.get_nearby_tiles():
            if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
                continue
            if ct.get_tile_building_id(tile) is not None:
                continue
            if claimed is not None and tile == claimed:
                # still ok for the claimer
                pass
            d = pos.distance_squared(tile)
            if d < best_d:
                best_d = d
                best = tile
        return best

    def _try_heal(self, ct: Controller) -> bool:
        pos = ct.get_position()
        # Prefer healing core / critical infra nearby
        for d in DIRECTIONS:
            check = pos.add(d)
            if not in_bounds(ct, check):
                continue
            if ct.can_heal(check):
                ct.heal(check)
                return True
        return False

    # ------------------------------------------------------------------
    # Scout
    # ------------------------------------------------------------------

    def _run_scout_move(self, ct: Controller, lay_chain: bool = True) -> None:
        if ct.get_move_cooldown() != 0:
            return
        pos = ct.get_position()
        ore = self._visible_unclaimed_ore(ct)
        if ore is not None:
            self._step_toward(ct, ore, lay_chain=lay_chain)
            return
        shared = unpack_pos(ct.read_store(SLOT_ORE_PACKED))
        if shared is not None and pos.distance_squared(shared) > 2:
            self._step_toward(ct, shared, lay_chain=lay_chain)
            return
        if self.target is None or pos == self.target or self.stuck >= 2:
            self.target = self._next_scan_target(ct)
        if self.target is not None:
            self._step_toward(ct, self.target, lay_chain=lay_chain)

    def _next_scan_target(self, ct: Controller) -> Position:
        w, h = ct.get_map_width(), ct.get_map_height()
        self.scan_cursor = (self.scan_cursor + 7) % max(1, w * h)
        x = self.scan_cursor % w
        y = (self.scan_cursor // w) % h
        # Bias away from core so we cover map
        if self.core_pos is not None:
            cx, cy = self.core_pos.x, self.core_pos.y
            x = (x + (w // 2) + cx) % w
            y = (y + (h // 2) + cy) % h
        return Position(x, y)

    # ------------------------------------------------------------------
    # Miner
    # ------------------------------------------------------------------

    def _run_miner(self, ct: Controller) -> None:
        if self._try_build_harvester_adjacent(ct):
            self.routing = True
            self.role = ROLE_ROUTER
            return
        # Claim shared / visible ore
        ore = self._visible_unclaimed_ore(ct)
        if ore is None:
            ore = unpack_pos(ct.read_store(SLOT_CLAIMED_ORE)) or unpack_pos(
                ct.read_store(SLOT_ORE_PACKED)
            )
        if ore is not None:
            claimed = unpack_pos(ct.read_store(SLOT_CLAIMED_ORE))
            if claimed is None:
                ct.write_store(SLOT_CLAIMED_ORE, pack_pos(ore))
            self.target = ore

    def _run_miner_move(self, ct: Controller, lay_chain: bool = True) -> None:
        if ct.get_move_cooldown() != 0:
            return
        # After placing a harvester, immediately route home laying belts
        if self.routing and self.core_pos is not None:
            self._step_toward(ct, self.core_pos, lay_chain=True)
            return
        if self.target is not None:
            self._step_toward(ct, self.target, lay_chain=lay_chain)
            return
        shared = unpack_pos(ct.read_store(SLOT_ORE_PACKED))
        if shared is not None:
            self._step_toward(ct, shared, lay_chain=lay_chain)
        else:
            self._run_scout_move(ct, lay_chain=lay_chain)

    def _try_build_harvester_adjacent(self, ct: Controller) -> bool:
        if ct.get_action_cooldown() != 0:
            return False
        if ct.get_global_resources() < ct.get_harvester_cost():
            return False
        pos = ct.get_position()
        for d in CARDINALS:
            build_pos = pos.add(d)
            if not in_bounds(ct, build_pos):
                continue
            if ct.can_build_harvester(build_pos):
                ct.build_harvester(build_pos)
                self.harvester_pos = build_pos
                count = ct.read_store(SLOT_HARVESTER_COUNT)
                ct.write_store(SLOT_HARVESTER_COUNT, count + 1)
                # Clear claim if it was this tile
                claimed = unpack_pos(ct.read_store(SLOT_CLAIMED_ORE))
                if claimed == build_pos:
                    ct.write_store(SLOT_CLAIMED_ORE, 0)
                # Start first hop toward core immediately if possible
                self._try_first_conveyor(ct, build_pos)
                self.target = None
                return True
        return False

    def _try_first_conveyor(self, ct: Controller, harvester_pos: Position) -> None:
        if self.core_pos is None:
            return
        if ct.get_global_resources() < ct.get_conveyor_cost():
            return
        toward = nearest_cardinal(harvester_pos.direction_to(self.core_pos))
        if toward == Direction.CENTRE:
            return
        conv_pos = harvester_pos.add(toward)
        if not in_bounds(ct, conv_pos):
            return
        if self._adjacent_to_core(ct, conv_pos):
            if ct.can_build_splitter(conv_pos, toward):
                ct.build_splitter(conv_pos, toward)
        elif ct.can_build_conveyor(conv_pos, toward):
            ct.build_conveyor(conv_pos, toward)

    # ------------------------------------------------------------------
    # Router — complete chains
    # ------------------------------------------------------------------

    def _run_router(self, ct: Controller) -> None:
        # Still grab ore if we see it and economy is thin
        if ct.read_store(SLOT_HARVESTER_COUNT) < TARGET_HARVESTERS:
            if self._try_build_harvester_adjacent(ct):
                self.routing = True
                return
        # If standing on last mile, place splitter underfoot
        self._try_ensure_core_splitter(ct)

    def _run_router_move(self, ct: Controller) -> None:
        if ct.get_move_cooldown() != 0:
            return
        if self.core_pos is None:
            return
        pos = ct.get_position()

        # If we just built a harvester, head home laying belts
        goal = self.core_pos
        if self.harvester_pos is not None and not self.routing:
            self.routing = True

        # Prefer walking toward core; lay infra on the tile we leave/enter
        self._step_toward(ct, goal, lay_chain=True)

        # After move, if now adjacent to core on empty tile, splitter
        # (handled next round via _try_ensure_core_splitter / lay on enter)

    def _try_ensure_core_splitter(self, ct: Controller) -> bool:
        if self.core_pos is None or ct.get_action_cooldown() != 0:
            return False
        pos = ct.get_position()
        if not self._adjacent_to_core(ct, pos):
            return False
        if ct.get_tile_building_id(pos) is not None:
            return False
        toward = nearest_cardinal(pos.direction_to(self.core_pos))
        if toward == Direction.CENTRE:
            return False
        if ct.can_build_splitter(pos, toward):
            ct.build_splitter(pos, toward)
            self.routing = False
            return True
        return False

    # ------------------------------------------------------------------
    # Defender
    # ------------------------------------------------------------------

    def _run_defender(self, ct: Controller) -> None:
        if self._try_ensure_core_splitter(ct):
            return
        if ct.read_store(SLOT_HARVESTER_COUNT) >= TARGET_HARVESTERS:
            self._try_build_gunner(ct)

    def _run_defender_move(self, ct: Controller, lay_chain: bool = True) -> None:
        if ct.get_move_cooldown() != 0 or self.core_pos is None:
            return
        pos = ct.get_position()
        if pos.distance_squared(self.core_pos) > 8:
            self._step_toward(ct, self.core_pos, lay_chain=lay_chain)
        else:
            self._step_toward(ct, self.core_pos, lay_chain=False)

    def _try_build_gunner(self, ct: Controller) -> bool:
        if ct.get_action_cooldown() != 0:
            return False
        if ct.get_global_resources() < ct.get_gunner_cost():
            return False
        pos = ct.get_position()
        if self.core_pos is None or pos.distance_squared(self.core_pos) > 18:
            return False

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
                    return True

        facing = pos.direction_to(self.core_pos).opposite()
        if facing == Direction.CENTRE:
            facing = random.choice(DIRECTIONS)
        for d in DIRECTIONS:
            build_pos = pos.add(d)
            if not in_bounds(ct, build_pos):
                continue
            if ct.can_build_gunner(build_pos, facing):
                ct.build_gunner(build_pos, facing)
                return True
        return False

    # ------------------------------------------------------------------
    # Raider
    # ------------------------------------------------------------------

    def _run_raider(self, ct: Controller) -> None:
        pos = ct.get_position()
        # Sabotage building we're standing on (enemy conveyor/splitter)
        bid = ct.get_tile_building_id(pos)
        if bid is not None and ct.get_team(bid) != ct.get_team():
            if ct.can_fire(pos):
                ct.fire(pos)
                return
        # Also try adjacent enemy logistics if somehow actionable — fire is own-tile only

    def _run_raider_move(self, ct: Controller) -> None:
        if ct.get_move_cooldown() != 0:
            return
        pos = ct.get_position()
        target = self._find_raid_target(ct)
        if target is None:
            target = unpack_pos(ct.read_store(SLOT_RAID_PACKED))
        if target is None and self.core_pos is not None:
            # Mirror heuristic: enemy core ≈ map symmetry of our core
            w, h = ct.get_map_width(), ct.get_map_height()
            target = Position(w - 1 - self.core_pos.x, h - 1 - self.core_pos.y)
        if target is not None:
            self._step_toward(ct, target, lay_chain=False)

    def _find_raid_target(self, ct: Controller) -> Position | None:
        my = ct.get_team()
        pos = ct.get_position()
        best = None
        best_d = 10**9
        for eid in ct.get_nearby_entities():
            if ct.get_team(eid) == my:
                continue
            et = ct.get_entity_type(eid)
            if et not in (EntityType.CONVEYOR, EntityType.SPLITTER, EntityType.HARVESTER):
                continue
            p = ct.get_position(eid)
            d = pos.distance_squared(p)
            if d < best_d:
                best_d = d
                best = p
        return best

    # ------------------------------------------------------------------
    # Movement + BFS + chain laying
    # ------------------------------------------------------------------

    def _adjacent_to_core(self, ct: Controller, pos: Position) -> bool:
        for d in DIRECTIONS:
            n = pos.add(d)
            if not in_bounds(ct, n):
                continue
            bid = ct.get_tile_building_id(n)
            if bid is not None and ct.get_entity_type(bid) == EntityType.CORE:
                return True
        return False

    def _step_toward(self, ct: Controller, goal: Position, lay_chain: bool = False) -> bool:
        if ct.get_move_cooldown() != 0:
            return False
        pos = ct.get_position()
        if pos == goal:
            return False

        step = self._bfs_first_step(ct, pos, goal)
        candidates: list[Direction] = []
        if step is not None and step != Direction.CENTRE:
            candidates.append(nearest_cardinal(step) if lay_chain else step)
            if step not in candidates:
                candidates.append(step)
        desired = pos.direction_to(goal)
        if desired != Direction.CENTRE:
            card = nearest_cardinal(desired)
            for d in (card, desired, desired.rotate_left(), desired.rotate_right()):
                if d != Direction.CENTRE and d not in candidates:
                    candidates.append(d)
        for d in CARDINALS:
            if d not in candidates:
                candidates.append(d)

        for d in candidates:
            # Chain laying: stick to cardinals
            move_dir = nearest_cardinal(d) if lay_chain and d not in CARDINALS else d
            if move_dir not in CARDINALS and lay_chain:
                continue
            if self._try_move(ct, move_dir, lay_chain=lay_chain):
                return True
        # Escape hatch: any cardinal
        dirs = list(CARDINALS)
        random.shuffle(dirs)
        for d in dirs:
            if self._try_move(ct, d, lay_chain=lay_chain):
                return True
        return False

    def _bfs_first_step(self, ct: Controller, start: Position, goal: Position) -> Direction | None:
        """Return first Direction along a short BFS path toward goal."""
        if start == goal:
            return None

        q: deque[Position] = deque([start])
        came_from: dict[tuple[int, int], tuple[int, int] | None] = {(start.x, start.y): None}
        expanded = 0
        found: Position | None = None

        while q and expanded < BFS_LIMIT:
            cur = q.popleft()
            expanded += 1
            if cur == goal or cur.distance_squared(goal) <= 2:
                found = cur
                break
            for d in CARDINALS:
                nxt = cur.add(d)
                key = (nxt.x, nxt.y)
                if key in came_from or not in_bounds(ct, nxt):
                    continue
                if ct.is_in_vision(nxt):
                    env = ct.get_tile_env(nxt)
                    if env == Environment.WALL:
                        continue
                    if ct.get_tile_builder_bot_id(nxt) is not None:
                        continue
                    bid = ct.get_tile_building_id(nxt)
                    if bid is not None:
                        et = ct.get_entity_type(bid)
                        if et not in (
                            EntityType.CONVEYOR,
                            EntityType.SPLITTER,
                            EntityType.CORE,
                        ):
                            continue
                came_from[key] = (cur.x, cur.y)
                q.append(nxt)

        if found is None:
            if len(came_from) <= 1:
                return None
            found = min(
                (Position(x, y) for x, y in came_from),
                key=lambda p: p.distance_squared(goal),
            )
            if found == start:
                return None

        cur_key: tuple[int, int] | None = (found.x, found.y)
        path = [cur_key]
        prev = came_from.get(cur_key)  # type: ignore[arg-type]
        while prev is not None:
            path.append(prev)
            prev = came_from.get(prev)
        path.reverse()
        if len(path) < 2:
            return None
        a, b = path[0], path[1]
        assert a is not None and b is not None
        return Position(a[0], a[1]).direction_to(Position(b[0], b[1]))

    def _try_move(self, ct: Controller, d: Direction, lay_chain: bool = False) -> bool:
        if d == Direction.CENTRE:
            return False
        pos = ct.get_position()
        next_pos = pos.add(d)
        if not in_bounds(ct, next_pos):
            return False

        if lay_chain and self.core_pos is not None and ct.is_in_vision(next_pos):
            if ct.is_tile_empty(next_pos):
                toward = nearest_cardinal(next_pos.direction_to(self.core_pos))
                if self._adjacent_to_core(ct, next_pos):
                    if ct.can_build_splitter(next_pos, toward):
                        ct.build_splitter(next_pos, toward)
                    elif ct.can_build_conveyor(next_pos, toward):
                        ct.build_conveyor(next_pos, toward)
                elif ct.can_build_conveyor(next_pos, toward):
                    ct.build_conveyor(next_pos, toward)

        if ct.can_move(d):
            ct.move(d)
            return True
        return False

    # ------------------------------------------------------------------
    # Gunner
    # ------------------------------------------------------------------

    def _run_gunner(self, ct: Controller) -> None:
        target = ct.get_gunner_target()
        if target is not None and ct.can_fire(target):
            ct.fire(target)
