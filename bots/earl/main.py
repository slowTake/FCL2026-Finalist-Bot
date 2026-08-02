"""earl — starter + HiveMind + inbound steal-relay.

Economy-first builders: fingerprint map via HiveMind, claim ore (build or
steal any harvester), then pave a committed conveyor path back to the core.
Wrong-facing allied belts are destroyed and rebuilt; enemy belts are cleared
via stand-and-fire.

Builder roles (see role.md): economy, phalanx, raiders, defender. Even
builders abort economy once the enemy core is known. Active order is
ACTIVE_RAID_ORDER: "phalanx" (home apron + wing gunners; pre-600 odds →
raiders at 300, pre-600 evens at 600 fire-and-forget; post-600 odds →
economy, post-600 evens → phalanx) or "raiders" (belt raid → gunner/sentinel
siege loop in attack.py). Economy builders (odd indices while off the raid
path) mine under a staged harvester cap (3 → 5 → unlimited) in logistics.py;
idle ore recheck every 50 turns. Indices 6+ defend the core from spawn.

Store slots:
  0–1                  Free
  2  SLOT_HARVESTER_COUNT Total harvesters built by the team (logistics.py)
  3  SLOT_SENTINEL_COUNT  Living phalanx wing gunners or siege sentinels (ammo)
  4–6                  Free
  7–15                 HiveMind-owned — never write from Player

Ally core from slot 15 via unpack_cores. Ore from ORE_LOCATIONS / local_map.
Core spawns builders toward enemy core (else map center).
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
    is_defender,
    run_defender,
    run_phalanx,
    core_under_turret_threat,
)
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

DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]
CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]

# "phalanx" = home apron + wing gunners; "raiders" = offensive siege task list.
ACTIVE_RAID_ORDER = "phalanx"
PHALANX_ODD_TO_RAIDERS_TURN = 300
PHALANX_EVEN_TO_RAIDERS_TURN = 600

SLOT_PACKED_CORES = 15

MAX_BUILDERS_EARLY = 5
MAX_BUILDERS = 9
MAX_BUILDERS_THREAT = 15
FIRST_INTERVAL_SPAWN = 10
SPAWN_INTERVAL = 25


def effective_raid_order(
    round_n: int, my_index: int = 0, spawn_round: int | None = None
) -> str:
    """Under phalanx: pre-600 cohort raids; post-600 odds economy, evens phalanx."""
    if ACTIVE_RAID_ORDER != "phalanx":
        return ACTIVE_RAID_ORDER
    spawned_before_600 = (
        spawn_round is None or spawn_round < PHALANX_EVEN_TO_RAIDERS_TURN
    )
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
    """Who runs phalanx or raiders this turn (enemy core must be known)."""
    if enemy_core is None or my_index <= 0:
        return False
    if ACTIVE_RAID_ORDER != "phalanx":
        return is_raider(my_index, enemy_core)
    spawned_before_600 = (
        spawn_round is None or spawn_round < PHALANX_EVEN_TO_RAIDERS_TURN
    )
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
        self.relay_merging = False
        self.spawn_round: int | None = None

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
        round_n = ct.get_current_round()
        phalanx_home = home_phalanx_active(round_n)
        ammo_turn = (
            PHALANX_GUNNER_TURN if phalanx_home else GUNNER_TURN
        )
        top_up_ammo(ct, min_turn=ammo_turn)

        living = self._count_living_builders(ct)
        threat = phalanx_home and core_under_turret_threat(ct)
        # Lifetime safety net — living can undercount raiders out of vision.
        if self.num_spawned >= MAX_BUILDERS_THREAT:
            return
        if living >= MAX_BUILDERS_THREAT:
            return
        if living >= MAX_BUILDERS and not threat:
            return
        if ct.get_unit_count() >= GameConstants.MAX_TEAM_UNITS:
            return

        can_rush = self.num_spawned < MAX_BUILDERS_EARLY
        can_interval = (
            self.num_spawned >= MAX_BUILDERS_EARLY
            and self.num_spawned < MAX_BUILDERS
            and round_n >= FIRST_INTERVAL_SPAWN
            and (round_n - FIRST_INTERVAL_SPAWN) % SPAWN_INTERVAL == 0
        )
        # Replace deaths back up to 9 living (after the initial 9 have been spawned).
        can_replenish = (
            self.num_spawned >= MAX_BUILDERS and living < MAX_BUILDERS
        )
        # Turret threat: ASAP up to 15 living for home defence.
        can_threat_spawn = threat and living < MAX_BUILDERS_THREAT
        if not can_rush and not can_interval and not can_replenish and not can_threat_spawn:
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

    def _count_living_builders(self, ct: Controller) -> int:
        """Ally builders known via local_map, plus any in core vision."""
        my_team = ct.get_team()
        seen: set[tuple[int, int]] = set()
        for (x, y), info in self.hivemind.local_map.items():
            ent = info.get("entity")
            if not ent:
                continue
            if ent.get("type") != EntityType.BUILDER_BOT:
                continue
            if ent.get("team") != my_team:
                continue
            seen.add((x, y))
        for eid in ct.get_nearby_entities():
            if ct.get_entity_type(eid) != EntityType.BUILDER_BOT:
                continue
            if ct.get_team(eid) != my_team:
                continue
            p = ct.get_position(eid)
            seen.add((p.x, p.y))
        return len(seen)

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

        # Raid path: pre-600 odds → raiders from 300; pre-600 evens phalanx
        # then raiders at 600 (fire-and-forget). Post-600: odds economy, evens phalanx.
        round_n = ct.get_current_round()
        if self.spawn_round is None:
            self.spawn_round = round_n
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
