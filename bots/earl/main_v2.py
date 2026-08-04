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

import builtins
import random

DEBUG = True
_original_print = builtins.print
def _custom_print(*args, **kwargs):
    if DEBUG:
        _original_print(*args, **kwargs)
builtins.print = _custom_print

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
    Environment,
    Position,
)
from hivemind_v2 import CoreState, HiveMind, unpack_cores
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


class LoggingController:
    def __init__(self, ct: Controller):
        self._ct = ct
        self.action_log = "idle()"
        
    def move(self, d: Direction):
        self.action_log = f"move({d.name})"
        return self._ct.move(d)

    def build(self, t: EntityType, d: Direction):
        self.action_log = f"build({t.name}, {d.name})"
        return self._ct.build(t, d)
        
    def fire(self, p: Position):
        self.action_log = f"fire({p.x}, {p.y})"
        return self._ct.fire(p)
        
    def heal(self, p: Position):
        self.action_log = f"heal({p.x}, {p.y})"
        return self._ct.heal(p)
        
    def rotate(self, d: Direction):
        self.action_log = f"rotate({d.name})"
        return self._ct.rotate(d)
        
    def spawn(self, t: EntityType, p: Position):
        self.action_log = f"spawn({t.name}, {p.x}, {p.y})"
        return self._ct.spawn(t, p)

    def mine(self, p: Position):
        self.action_log = f"mine({p.x}, {p.y})"
        return self._ct.mine(p)
        
    def repair(self, p: Position):
        self.action_log = f"repair({p.x}, {p.y})"
        return self._ct.repair(p)

    def __getattr__(self, name):
        return getattr(self._ct, name)


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

        self.cached_path: list[Position] | None = None
        self.cached_path_target: Position | None = None
        self.stuck_on_path = 0

    def _print_ascii_map(self, ct: Controller):
        w = ct.get_map_width()
        h = ct.get_map_height()
        my_team = ct.get_team()
        
        lines = []
        for y in range(h):
            row = []
            for x in range(w):
                cell = self.hivemind.local_map.get((x, y))
                if not cell:
                    row.append(' ')
                    continue
                
                if "entity" in cell:
                    ent = cell["entity"]
                    is_ally = ent.get("team") == my_team
                    etype = ent.get("type")
                    
                    if etype == EntityType.CORE: char = 'C'
                    elif etype == EntityType.BUILDER_BOT: char = 'B'
                    elif etype == EntityType.HARVESTER: char = 'H'
                    elif etype == EntityType.GUNNER: char = 'G'
                    elif etype == EntityType.SENTINEL: char = 'S'
                    elif etype == EntityType.CONVEYOR:
                        d = ent.get("direction", Direction.NORTH)
                        char = {Direction.NORTH: '^', Direction.SOUTH: 'v', Direction.EAST: '>', Direction.WEST: '<'}.get(d, '^')
                    elif etype == EntityType.SPLITTER: char = '+'
                    else: char = '?'
                    
                    if not is_ally and char.isalpha():
                        char = char.lower()
                    row.append(char)
                elif cell.get("passable") is False:
                    row.append('#')
                elif cell.get("ore"):
                    row.append('o')
                else:
                    row.append('.')
            lines.append("".join(row))
            
        print(f"[{ct.get_current_round()}] Bot {ct.get_id()} local_map:")
        for line in lines:
            print(line)

    def run(self, ct: Controller) -> None:
        try:
            self.hivemind.tick(ct)
            my_id = ct.get_id()
            self._print_ascii_map(ct)
            etype = ct.get_entity_type(my_id)
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
        if enemy_pos is not None and self.hivemind.map_id != 0:
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
        logging_ct = LoggingController(ct)
        pos = logging_ct.get_position()
        if self.core_pos is None:
            self._read_core_pos(logging_ct)
        if self.enemy_core_pos is None:
            self._read_enemy_core(logging_ct)

        if self.last_pos == pos:
            self.stuck += 1
        else:
            self.stuck = 0
        self.last_pos = pos

        run_builder_roles(self, logging_ct)
        print(f"[{logging_ct.get_current_round()}] Builder {logging_ct.get_id()} at {pos}: {logging_ct.action_log}")

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

    def _walk_passable(self, ct: Controller, pos: Position, current_pos: Position | None = None, treat_builders_as_blockers: bool = False) -> bool:
        if not in_bounds(ct, pos):
            return False
            
        if ct.is_in_vision(pos):
            if ct.get_tile_env(pos) == Environment.WALL:
                return False
                
            b_id = ct.get_tile_building_id(pos)
            if b_id is not None:
                b_type = ct.get_entity_type(b_id)
                if b_type not in (EntityType.CONVEYOR, EntityType.SPLITTER):
                    return False
                    
            if treat_builders_as_blockers:
                for eid in ct.get_nearby_entities():
                    if ct.get_position(eid) == pos:
                        return False
            else:
                if current_pos is not None and manhattan(pos, current_pos) == 1:
                    for eid in ct.get_nearby_entities():
                        if ct.get_position(eid) == pos:
                            return False
            return True
            
        entry = self.hivemind.local_map.get((pos.x, pos.y))
        if entry is None:
            return True
            
        if "entity" in entry:
            etype = entry["entity"]["type"]
            if etype in (EntityType.CORE, EntityType.HARVESTER, EntityType.GUNNER, EntityType.SENTINEL):
                return False
            return True
        else:
            return bool(entry.get("passable", True))

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
        
    def _validate_cached_path(self, ct: Controller, target: Position) -> bool:
        if not self.cached_path or self.cached_path_target != target:
            return False
        pos = ct.get_position()
        try:
            idx = self.cached_path.index(pos)
        except ValueError:
            return False
        
        # Path must have at least a next step
        if idx + 1 >= len(self.cached_path):
            return False
            
        # Check all steps ahead in vision to ensure they are passable
        for step in self.cached_path[idx + 1:]:
            if ct.is_in_vision(step):
                # Don't treat builders as blockers here unless they are adjacent to our current pos
                if not self._walk_passable(ct, step, current_pos=pos, treat_builders_as_blockers=False):
                    return False
        return True

    def _astar_move_toward(self, ct: Controller, target: Position) -> bool:
        """A* step toward target with caching and anti-stall fallback."""
        pos = ct.get_position()
        if manhattan(pos, target) < 2:
            return False

        if not self._validate_cached_path(ct, target):
            self.cached_path = None
            treat_blockers = (self.stuck_on_path >= 2)
            
            def walk_ok(p: Position) -> bool:
                if p.x == target.x and p.y == target.y:
                    return True
                return self._walk_passable(ct, p, current_pos=pos, treat_builders_as_blockers=treat_blockers)

            path = astar(
                pos,
                target,
                is_passable=walk_ok,
                max_iters=800,
            )
            if path is not None and len(path) >= 2:
                self.cached_path = path
                self.cached_path_target = target

        if self.cached_path is None:
            return False

        idx = self.cached_path.index(pos)
        step = self.cached_path[idx + 1]
        
        # Don't step onto an impassable goal — stop adjacent via greedy instead
        if step.x == target.x and step.y == target.y and not self._walk_passable(
            ct, step, current_pos=pos
        ):
            self.cached_path = None
            return self._greedy_move_toward(ct, target)
            
        d = nearest_cardinal(pos.direction_to(step))
        if self._try_move(ct, d):
            self.stuck_on_path = 0
            return True
        else:
            self.stuck_on_path += 1
            if self.stuck_on_path >= 2:
                self.cached_path = None
            return False

    def _move_toward_pos(self, ct: Controller, target: Position) -> bool:
        """Prefer A* when far; greedy fallback."""
        if self._astar_move_toward(ct, target):
            return True
        if self.cached_path is not None:
            return False
        return self._greedy_move_toward(ct, target)

    # ------------------------------------------------------------------
    # Gunner
    # ------------------------------------------------------------------

    def _run_gunner(self, ct: Controller) -> None:
        run_gunner(ct)
