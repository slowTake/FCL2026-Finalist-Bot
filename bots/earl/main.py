"""earl — starter + HiveMind + inbound steal-relay.

Economy-first builders: fingerprint map via HiveMind, claim ore (build or
steal any harvester), then pave a committed conveyor path back to the core.
Wrong-facing allied belts are destroyed and rebuilt; enemy belts are cleared
via stand-and-fire.

Builder roles (see role.md): economy, phalanx, raiders, harrass, defender.
Even builders abort economy once the enemy core is known. Active order is
ACTIVE_RAID_ORDER: "phalanx" (home apron + wing gunners; pre-600 odds →
raiders at 300, pre-600 evens at 600 fire-and-forget; post-600 odds →
economy, post-600 evens → phalanx; index 5 harrasses (phalanx home until 50)
until 300 then joins raiders), "raiders" (belt raid → gunner/sentinel siege
loop in attack.py),
or "harrass" (full raid-path order in harrass.py). Economy builders (odd
indices while off the raid path) mine under a staged harvester cap
(1 → 2 → 3 → 4 → 5 → unlimited) in logistics.py; idle ore recheck every 50 turns.
Indices 6+ defend the core from spawn.

Store slots:
  0  SLOT_RELAY_COMPLETE  1 once any inbound relay finished
  1  SLOT_PHALANX_BUILDER_THREAT  0 off; 1+side = wings/apron armed (sticky)
  2  SLOT_HARVESTER_COUNT Total harvesters built by the team (logistics.py)
  3  SLOT_SENTINEL_COUNT  Living phalanx wing/guard gunners or siege sentinels
                          (bookkeeping; ammo uses local_map counts)
  4–6                  Free
  7–15                 HiveMind-owned — never write from Player

Ally core from slot 15 via unpack_cores; enemy core from fingerprint /
KNOWN_MAPS (same-turn cache) or slot 15. Ore from ORE_LOCATIONS / local_map.
Core rushes 5 builders, then interval-spawns to 7, then grows one every
50 turns up to 9, then one every 200 turns as a late fallback. From turn
75, deaths are replaced ASAP only when a builder vanishes from interior
core vision (walking out of fog does not count). Threat-spawn extras are
off (`SPAWN_EXTRA_UNDER_THREAT`).
"""

from __future__ import annotations

import random

from attack import (
    GUNNER_TURN,
    is_raider,
    run_attack_builder,
    run_gunner,
    run_sentinel,
    top_up_ammo,
)
from defence import (
    PHALANX_GUNNER_TURN,
    clear_phalanx_builder_threat,
    is_defender,
    run_defender,
    run_phalanx,
    core_under_turret_threat,
    update_phalanx_builder_threat,
)
from harrass import run_harrasser
from fcode import (
    Controller,
    Direction,
    EntityType,
    GameConstants,
    Position,
)
from hivemind import CoreState, HiveMind, unpack_cores
from logistics import (
    PATH_MAX_ITERS,
    clear_relay,
    pick_economy_target,
    run_economy,
)
from pathing import astar, manhattan
from features import WALL_LOCATIONS

DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]
CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]

# "harrass" = harvester siege + seek; "phalanx" = home apron + wing gunners;
# "raiders" = offensive siege task list.
ACTIVE_RAID_ORDER = "phalanx"
PHALANX_ODD_TO_RAIDERS_TURN = 300
PHALANX_EVEN_TO_RAIDERS_TURN = 600
HARRASS_INDEX = 5

SLOT_PACKED_CORES = 15

MAX_BUILDERS_EARLY = 5
MAX_BUILDERS = 7
MAX_BUILDERS_THREAT = 9
# When True, spawn ASAP up to MAX_BUILDERS_THREAT if enemy turret in core vision.
SPAWN_EXTRA_UNDER_THREAT = False
FIRST_INTERVAL_SPAWN = 10
SPAWN_INTERVAL = 25
LATE_SPAWN_INTERVAL = 50
FALLBACK_SPAWN_INTERVAL = 200
# Death-replace (replace_debt) stays off until this turn so early growth
# is only rush / interval / late / fallback.
REPLENISH_START_TURN = 75
# Core vision radius; builders vanishing on the outer ring are treated as
# walk-outs, not deaths (avoids replenish spam once raiders leave home).
CORE_VISION_RADIUS = 6
# Same-tile turns before destroying an adjacent ally gunner/sentinel that
# corks an exit (small-map cornering after home defence placement).
BLOCKING_TURRET_STUCK = 100

_BLOCKING_TURRET_TYPES = (EntityType.GUNNER, EntityType.SENTINEL)


def effective_raid_order(
    round_n: int, my_index: int = 0, spawn_round: int | None = None
) -> str:
    """Under phalanx: pre-600 cohort raids; post-600 odds economy, evens phalanx.

    Index HARRASS_INDEX runs harrass until PHALANX_ODD_TO_RAIDERS_TURN, then raiders.
    """
    if ACTIVE_RAID_ORDER != "phalanx":
        return ACTIVE_RAID_ORDER
    spawned_before_600 = (
        spawn_round is None or spawn_round < PHALANX_EVEN_TO_RAIDERS_TURN
    )
    if my_index == HARRASS_INDEX:
        if round_n >= PHALANX_ODD_TO_RAIDERS_TURN:
            return "raiders"
        return "harrass"
    if my_index % 2 == 1:
        if spawned_before_600 and round_n >= PHALANX_ODD_TO_RAIDERS_TURN:
            return "raiders"
        return "phalanx"
    if not spawned_before_600:
        return "phalanx"
    if round_n >= PHALANX_EVEN_TO_RAIDERS_TURN:
        return "raiders"
    return "phalanx"


def home_phalanx_active(round_n: int) -> bool:
    """True while home phalanx role is active (new evens refill after 600)."""
    del round_n
    return ACTIVE_RAID_ORDER == "phalanx"


def on_raid_tasklist(
    my_index: int,
    enemy_core: Position | None,
    round_n: int,
    spawn_round: int | None = None,
) -> bool:
    """Who runs phalanx / harrass / raiders this turn (enemy core must be known)."""
    if enemy_core is None or my_index <= 0:
        return False
    if ACTIVE_RAID_ORDER != "phalanx":
        return is_raider(my_index, enemy_core)
    spawned_before_600 = (
        spawn_round is None or spawn_round < PHALANX_EVEN_TO_RAIDERS_TURN
    )
    # Index 5: harrass early, then join odd raider pivot at 300 (pre-600 only).
    if my_index == HARRASS_INDEX:
        return spawned_before_600
    # Pre-600 odds raid from 300 (sticky). Post-600 odds stay off (economy).
    if my_index % 2 == 1:
        return spawned_before_600 and round_n >= PHALANX_ODD_TO_RAIDERS_TURN
    # Evens always on the raid path (phalanx or raiders by spawn cohort).
    return True


def is_economy(
    my_index: int,
    enemy_core: Position | None,
    round_n: int,
    spawn_round: int | None = None,
) -> bool:
    """Odd (or unraiding) builders on the mine/relay loop — not defender/raid."""
    if my_index <= 0 or is_defender(my_index):
        return False
    return not on_raid_tasklist(my_index, enemy_core, round_n, spawn_round)


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
    ct: Controller, core_pos: Position, goal: Position, map_id: int, is_enemy_core: bool = False
) -> list[Position]:
    """Apron tiles around the full 2x2 core, closest to goal first using A*."""
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
            
    walls = set(WALL_LOCATIONS.get(map_id, []))
    def is_passable(p: Position) -> bool:
        if not in_bounds(ct, p):
            return False
        if (p.x, p.y) in walls:
            return False
        return True
        
    def is_goal_fn(cand: Position) -> bool:
        return (cand.x, cand.y) in footprint_keys

    # Run ONE A* from goal to our core footprint
    path = astar(goal, is_passable=is_passable, is_goal=is_goal_fn, max_iters=1000)
    
    if path and len(path) >= 2:
        # path[0] is the goal. path[-1] is the tile inside our core footprint.
        # path[-2] is the apron tile the path took right before hitting the core.
        best_apron = path[-2]
        sites.sort(key=lambda p: (0 if p == best_apron else 1, p.distance_squared(goal)))
    else:
        # Fallback if no path is found (e.g. fully blocked)
        sites.sort(key=lambda p: p.distance_squared(goal))
        
    return sites


def _core_footprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


class Player:
    def __init__(self):
        self.hivemind = HiveMind()
        self.num_spawned = 0
        # Intentional living target (growth only); replenish does not raise it.
        self.spawn_target = 0
        self.last_growth_round = -10**9
        self.replace_debt = 0
        # eid -> last Position seen in core vision (death detection).
        self._prev_vision_builders: dict[int, Position] = {}

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
        self.relay_merging = False
        self.spawn_round: int | None = None
        self.phalanx_threat_until = -1
        self.phalanx_threat_side = 0

    def run(self, ct: Controller) -> None:
        try:
            self.hivemind.tick(ct)
            my_id = ct.get_id()
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
        round_n = ct.get_current_round()
        phalanx_home = home_phalanx_active(round_n)
        if phalanx_home:
            self.phalanx_threat_until, self.phalanx_threat_side = (
                update_phalanx_builder_threat(
                    ct,
                    sticky_until=self.phalanx_threat_until,
                    last_side=self.phalanx_threat_side,
                )
            )
        else:
            clear_phalanx_builder_threat(ct)
            self.phalanx_threat_until = -1

        ammo_turn = (
            PHALANX_GUNNER_TURN if phalanx_home else GUNNER_TURN
        )
        top_up_ammo(ct, local_map=self.hivemind.local_map, min_turn=ammo_turn)

        living_debt = self._observe_builder_deaths(ct)
        if round_n >= REPLENISH_START_TURN:
            self.replace_debt += living_debt
        else:
            self.replace_debt = 0
        threat = phalanx_home and core_under_turret_threat(ct)
        if ct.get_unit_count() >= GameConstants.MAX_TEAM_UNITS:
            return

        can_rush = self.spawn_target < MAX_BUILDERS_EARLY
        can_interval = (
            self.spawn_target >= MAX_BUILDERS_EARLY
            and self.spawn_target < MAX_BUILDERS
            and round_n >= FIRST_INTERVAL_SPAWN
            and (round_n - FIRST_INTERVAL_SPAWN) % SPAWN_INTERVAL == 0
        )
        # After 7: grow one every LATE_SPAWN_INTERVAL turns (up to 9).
        can_late = (
            self.spawn_target >= MAX_BUILDERS
            and self.spawn_target < MAX_BUILDERS_THREAT
            and round_n - self.last_growth_round >= LATE_SPAWN_INTERVAL
        )
        # After 9: slow trickle every FALLBACK_SPAWN_INTERVAL turns.
        can_fallback = (
            self.spawn_target >= MAX_BUILDERS_THREAT
            and round_n - self.last_growth_round >= FALLBACK_SPAWN_INTERVAL
        )
        # Replace only vision-confirmed deaths from REPLENISH_START_TURN on.
        can_replenish = self.replace_debt > 0 and round_n >= REPLENISH_START_TURN
        can_threat_spawn = (
            threat
            and SPAWN_EXTRA_UNDER_THREAT
            and self.spawn_target < MAX_BUILDERS_THREAT
        )
        if (
            not can_rush
            and not can_interval
            and not can_late
            and not can_fallback
            and not can_replenish
            and not can_threat_spawn
        ):
            return

        ti = ct.get_global_resources()
        if ti < ct.get_builder_bot_cost():
            return

        pos = ct.get_position()
        enemy_pos = self._resolve_enemy_core(ct)
        if enemy_pos is not None:
            goal = enemy_pos
            is_enemy = True
        else:
            goal = Position(ct.get_map_width() // 2, ct.get_map_height() // 2)
            is_enemy = False

        # Prefer replacing observed deaths over scheduled growth this turn.
        is_replenish = can_replenish
        for spawn_pos in spawn_sites_toward(
            ct, pos, goal, map_id=self.hivemind.map_id, is_enemy_core=is_enemy
        ):
            if self.hivemind.spawn_builder(ct, spawn_pos):
                self.num_spawned += 1
                if is_replenish:
                    self.replace_debt = max(0, self.replace_debt - 1)
                else:
                    self.spawn_target += 1
                    self.last_growth_round = round_n
                return

    def _observe_builder_deaths(self, ct: Controller) -> int:
        """Count ally builders that vanished from interior core vision.

        Leaving the fog edge does not count — that was spamming replenish
        whenever raiders walked out of home vision.
        """
        my_team = ct.get_team()
        core = ct.get_position()
        current: dict[int, Position] = {}
        for eid in ct.get_nearby_entities():
            if ct.get_entity_type(eid) != EntityType.BUILDER_BOT:
                continue
            if ct.get_team(eid) != my_team:
                continue
            current[eid] = ct.get_position(eid)

        deaths = 0
        for eid, prev_pos in self._prev_vision_builders.items():
            if eid in current:
                continue
            dx = abs(prev_pos.x - core.x)
            dy = abs(prev_pos.y - core.y)
            # Outer ring vanish ≈ walked into fog; interior vanish ≈ died.
            if max(dx, dy) < CORE_VISION_RADIUS and ct.is_in_vision(prev_pos):
                deaths += 1
        self._prev_vision_builders = current
        return deaths

    def _resolve_enemy_core(self, ct: Controller) -> Position | None:
        """Enemy core from slot 15, else same-turn HiveMind fingerprint cache."""
        _, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
        if enemy[0] != CoreState.UNKNOWN:
            return Position(enemy[1], enemy[2])
        if self.hivemind.enemy_core_pos is not None:
            return self.hivemind.enemy_core_pos
        return None

    # ------------------------------------------------------------------
    # Builder — role menu
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

        if self.stuck >= BLOCKING_TURRET_STUCK:
            self._try_clear_blocking_ally_turret(ct)

        # Raid path: pre-600 odds → raiders from 300; pre-600 evens phalanx
        # then raiders at 600 (fire-and-forget). Post-600: odds economy, evens phalanx.
        round_n = ct.get_current_round()
        if self.spawn_round is None:
            self.spawn_round = round_n
        
        my_id = ct.get_id()
        if on_raid_tasklist(
            self.hivemind.my_index,
            self.enemy_core_pos,
            round_n,
            self.spawn_round,
        ):
            enemy = self.enemy_core_pos
            assert enemy is not None
            clear_relay(self)
            self.ore_site = None
            order = effective_raid_order(
                round_n, self.hivemind.my_index, self.spawn_round
            )
            if order == "phalanx":
                site = run_phalanx(
                    ct,
                    my_index=self.hivemind.my_index,
                    local_map=self.hivemind.local_map,
                    enemy_core=enemy,
                    ally_core=self.core_pos,
                )
            elif order == "harrass":
                site = run_harrasser(
                    ct,
                    my_index=self.hivemind.my_index,
                    local_map=self.hivemind.local_map,
                    enemy_core=enemy,
                    ally_core=self.core_pos,
                )
            else:
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

        if is_defender(self.hivemind.my_index):
            clear_relay(self)
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

        run_economy(self, ct)

    def _read_core_pos(self, ct: Controller) -> None:
        ally, _ = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
        if ally[0] != CoreState.UNKNOWN:
            self.core_pos = Position(ally[1], ally[2])

    def _read_enemy_core(self, ct: Controller) -> None:
        enemy_pos = self._resolve_enemy_core(ct)
        if enemy_pos is not None:
            self.enemy_core_pos = enemy_pos

    def _try_heal(self, ct: Controller) -> None:
        pos = ct.get_position()
        for d in Direction:
            check = pos.add(d)
            if ct.can_heal(check):
                ct.heal(check)
                return

    # ------------------------------------------------------------------
    # Movement / targeting
    # ------------------------------------------------------------------

    def _try_clear_blocking_ally_turret(self, ct: Controller) -> bool:
        """Destroy one adjacent ally gunner/sentinel after long stuck.

        Prefer the neighbour that best advances toward self.target.
        """
        if self.stuck < BLOCKING_TURRET_STUCK:
            return False
        pos = ct.get_position()
        my_team = ct.get_team()
        target = self.target

        cands: list[tuple[int, Position]] = []
        for d in CARDINALS:
            tile = pos.add(d)
            if not ct.is_in_vision(tile):
                continue
            bid = ct.get_tile_building_id(tile)
            if bid is None:
                continue
            if ct.get_team(bid) != my_team:
                continue
            if ct.get_entity_type(bid) not in _BLOCKING_TURRET_TYPES:
                continue
            if not ct.can_destroy(tile):
                continue
            if target is not None:
                score = tile.distance_squared(target)
            else:
                score = 0
            cands.append((score, tile))

        if not cands:
            return False
        cands.sort(key=lambda item: item[0])
        ct.destroy(cands[0][1])
        self.stuck = 0
        return True

    def _move_toward_target(self, ct: Controller, *, retarget: bool = True) -> None:
        if ct.get_move_cooldown() != 0:
            return

        pos = ct.get_position()
        if retarget:
            if self.ore_site is not None:
                self.target = self.ore_site
            elif self.target is None or pos == self.target or self.stuck >= 3:
                self.target = pick_economy_target(self, ct)
                self.stuck = 0
        if self.target is None:
            return

        if pos.x == self.target.x and pos.y == self.target.y:
            return

        self._move_toward_pos(ct, self.target)

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

    def _walk_passable(self, ct: Controller, pos: Position) -> bool:
        if not in_bounds(ct, pos):
            return False
        if ct.is_in_vision(pos):
            return ct.is_tile_passable(pos)
        entry = self.hivemind.local_map.get((pos.x, pos.y))
        if entry is None:
            return True
        if "passable" not in entry:
            return True
        return bool(entry["passable"])

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

    def _greedy_move_toward(self, ct: Controller, target: Position) -> bool:
        pos = ct.get_position()
        for d in self._direction_candidates(ct, pos, target):
            if self._try_move(ct, d):
                return True
        return False

    def _astar_move_toward(self, ct: Controller, target: Position) -> bool:
        """A* first step toward target; False if no path or move failed."""
        pos = ct.get_position()
        if manhattan(pos, target) < 2:
            return False

        def walk_ok(p: Position) -> bool:
            if p.x == target.x and p.y == target.y:
                return True
            return self._walk_passable(ct, p)

        path = astar(
            pos,
            target,
            is_passable=walk_ok,
            max_iters=PATH_MAX_ITERS,
        )
        if path is None or len(path) < 2:
            return False
        step = path[1]
        # Don't step onto an impassable goal — stop adjacent via greedy instead
        if step.x == target.x and step.y == target.y and not self._walk_passable(
            ct, step
        ):
            return self._greedy_move_toward(ct, target)
        d = nearest_cardinal(pos.direction_to(step))
        return self._try_move(ct, d)

    def _move_toward_pos(self, ct: Controller, target: Position) -> bool:
        """Prefer A* when far; greedy fallback."""
        if self._astar_move_toward(ct, target):
            return True
        return self._greedy_move_toward(ct, target)

    # ------------------------------------------------------------------
    # Gunner
    # ------------------------------------------------------------------

    def _run_gunner(self, ct: Controller) -> None:
        run_gunner(ct)
