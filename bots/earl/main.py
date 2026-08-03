"""earl — starter + HiveMind + inbound steal-relay.

Economy-first builders: fingerprint map via HiveMind, claim ore (build or
steal any harvester), then pave a committed conveyor path back to the core.
Wrong-facing allied belts are destroyed and rebuilt; enemy belts are cleared
via stand-and-fire.

Builder roles (see role.md / roles.py): economy, phalanx; optional
raiders/harrass via ACTIVE_RAID_ORDER in roles.py. Default **"phalanx"**:
even builders → home phalanx once the enemy core is known; fire-and-forget
even waves leave at 300 / 600 by spawn cohort (later evens refill phalanx);
odd builders → economy (mine + snipe; graduate to harrass on relay+100 or
age 200). Overrides: "raiders" (belt raid → gunner/sentinel siege in
attack.py), "harrass" (harrass.py). Economy harvester caps
(2 → 3 → 4 → 5 → 6 → unlimited) in logistics.py; idle ore recheck every
50 turns.

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
50 turns up to 9, then one every 200 turns as a late fallback. Caps and
timings live here; spawn/replenish logic is in respawn.py. From turn 75,
deaths are replaced ASAP only when a builder vanishes from deep-interior
core vision (cannot leave vision in one step). Threat-spawn extras are
off (`SPAWN_EXTRA_UNDER_THREAT`).
"""

from __future__ import annotations

import random

from attack import (
    GUNNER_TURN,
    run_gunner,
    run_sentinel,
    top_up_ammo,
)
from defence import (
    PHALANX_GUNNER_TURN,
    clear_phalanx_builder_threat,
    core_under_turret_threat,
    update_phalanx_builder_threat,
)
from fcode import (
    Controller,
    Direction,
    EntityType,
    Position,
)
from hivemind import CoreState, HiveMind, unpack_cores
from logistics import (
    PATH_MAX_ITERS,
    pick_economy_target,
)
from pathing import astar, manhattan
from respawn import Respawn, SpawnSchedule
from roles import home_phalanx_active, run_builder_roles

DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]
CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]

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

SPAWN_SCHEDULE = SpawnSchedule(
    max_builders_early=MAX_BUILDERS_EARLY,
    max_builders=MAX_BUILDERS,
    max_builders_threat=MAX_BUILDERS_THREAT,
    first_interval_spawn=FIRST_INTERVAL_SPAWN,
    spawn_interval=SPAWN_INTERVAL,
    late_spawn_interval=LATE_SPAWN_INTERVAL,
    fallback_spawn_interval=FALLBACK_SPAWN_INTERVAL,
    replenish_start_turn=REPLENISH_START_TURN,
    spawn_extra_under_threat=SPAWN_EXTRA_UNDER_THREAT,
)


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


class Player:
    def __init__(self):
        self.hivemind = HiveMind()
        self.respawn = Respawn()

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
        self.relay_finished_round: int | None = None
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

        threat = phalanx_home and core_under_turret_threat(ct)
        enemy_pos = self._resolve_enemy_core(ct)
        if enemy_pos is not None:
            goal = enemy_pos
        else:
            goal = Position(ct.get_map_width() // 2, ct.get_map_height() // 2)
        self.respawn.run_core_spawn(
            ct,
            schedule=SPAWN_SCHEDULE,
            threat=threat,
            map_id=self.hivemind.map_id,
            goal=goal,
            hivemind=self.hivemind,
        )

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

        run_builder_roles(self, ct)

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
