"""baron — multi-module bot. Policy at top; dispatch only below.

Store slots (HiveMind v2):
  0  SLOT_RELAY_COMPLETE   1 after first inbound relay finishes
  1  SLOT_THREAT           TBD
  2  SLOT_HARVESTER_COUNT  total harvesters built
  3  SLOT_SENTINEL_COUNT   bookkeeping TBD
  4–15 HiveMind-owned — never write from Player
       4 builder registry · 5–12 delta ledger · 13 metronome
       14 map id · 15 packed cores
"""

from __future__ import annotations

from fcode import Controller, EntityType, Position

# --- Builder spawn policy ---
# Rush MAX_BUILDERS_EARLY ASAP, then +1 every effective interval turns (cap MAX_BUILDERS).
MAX_BUILDERS_EARLY = 5
MAX_BUILDERS = 15000
SPAWN_INTERVAL = 50  # base when below all Ti tiers

# Richer bank → shorter interval. Empty list = time-only (base interval).
# (min_ti, interval); highest matching min_ti wins.
SPAWN_INTERVAL_BY_TI: list[tuple[int, int]] = [
    (2000, 25),
    (1000, 35),
]

# --- Wave attack (≤0 / None = unset; positive = active gate) ---
WAVE_ATTACK_TURN: int | None = -1  # e.g. 300 → all builders harass from that turn
WAVE_ATTACK_TI: int | None = 500  # e.g. 2000 → all harass once bank ≥ this

# --- Default early defend (age after birth_round; ≤0 disables) ---
# Only applies inside runDefault — harassers never see this.
BUILDER_HOME_TURNS = 80


# --- Harvester caps (built count, store slot 2) ---
# Turn-based ceilings (very high = effectively open). Effective cap = min(turn, ti).
HARVESTER_CAP_BEFORE_50 = 1000
HARVESTER_CAP_BEFORE_100 = 3000
HARVESTER_CAP_BEFORE_200 = 4000
HARVESTER_CAP_BEFORE_250 = 5000
HARVESTER_CAP_BEFORE_400 = 6000

# Ti bank → max harvesters. Base when below all tiers; None + empty BY_TI = turn caps only.
HARVESTER_CAP: int | None = 5
HARVESTER_CAP_BY_TI: list[tuple[int, int]] = [
    (2000, 120),
    (1000, 80),
    (500, 50),
]

# --- Gun placement (≤0 disables) ---
GUN_PLACE_MIN_TI = 100  # need bank ≥ this to place gunner/sentinel

# --- Ammo policy (guns.top_up_ammo reads these) ---
AMMO_BASE = 12
AMMO_PER_GUNNER = 3
AMMO_PER_SENTINEL = 10
AMMO_CONVERT_CHUNK = 10
AMMO_MIN_POOL = 20
DEFAULT_AMMO_START_TURN = 1
RAIDER_AMMO_START_TURN = 30
MAX_AMMO_POOL: int | None = None

# --- Store slots (Player 0–3 only) ---
SLOT_RELAY_COMPLETE = 0
SLOT_THREAT = 1
SLOT_HARVESTER_COUNT = 2
SLOT_SENTINEL_COUNT = 3
SLOT_PACKED_CORES = 15

DEBUG = False

from guns import gunnerLoop, sentinelLoop, top_up_ammo
from hivemind import CoreState, HiveMind, unpack_cores
from respawn import Respawn, SpawnSchedule
from builder import runBuilderRoles

SPAWN_SCHEDULE = SpawnSchedule(
    max_builders_early=MAX_BUILDERS_EARLY,
    max_builders=MAX_BUILDERS,
    spawn_interval=SPAWN_INTERVAL,
    spawn_interval_by_ti=tuple(SPAWN_INTERVAL_BY_TI),
)


class Player:
    def __init__(self) -> None:
        self.hivemind = HiveMind()
        self.respawn = Respawn()

        self.corePos: Position | None = None
        self.enemyCorePos: Position | None = None
        self.oreSite: Position | None = None
        self.inRelay = False
        self.relayOrigin: Position | None = None
        self.relayPath: list[Position] | None = None
        self.relayBuiltTo = -1
        self.relayStuck = 0
        self.cachedPath: list[Position] | None = None
        self.cachedPathTarget: Position | None = None
        self.lastPos: Position | None = None
        self.stuck = 0
        self.recheckingOre = False
        self.skippedOres: set[tuple[int, int]] = set()
        self.lastOreRecheckRound = -999
        self.harassTarget: tuple[int, int] | None = None

    def run(self, ct: Controller) -> None:
        try:
            self.hivemind.tick(ct)
            etype = ct.get_entity_type()
            if etype == EntityType.CORE:
                self._run_core(ct)
            elif etype == EntityType.BUILDER_BOT:
                self._run_builder(ct)
            elif etype == EntityType.GUNNER:
                gunnerLoop(ct)
            elif etype == EntityType.SENTINEL:
                sentinelLoop(ct)
        except Exception:
            return

    def _run_core(self, ct: Controller) -> None:
        round_n = ct.get_current_round()
        min_turn = (
            DEFAULT_AMMO_START_TURN
            if round_n < RAIDER_AMMO_START_TURN
            else RAIDER_AMMO_START_TURN
        )
        top_up_ammo(ct, local_map=self.hivemind.local_map, min_turn=min_turn)

        enemy = self._resolveEnemyCore(ct)
        if enemy is not None and self.hivemind.map_id != 0:
            goal = enemy
        else:
            goal = Position(ct.get_map_width() // 2, ct.get_map_height() // 2)
        self.respawn.run_core_spawn(
            ct,
            schedule=SPAWN_SCHEDULE,
            goal=goal,
            hivemind=self.hivemind,
        )

    def _resolveEnemyCore(self, ct: Controller) -> Position | None:
        _, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
        if enemy[0] != CoreState.UNKNOWN:
            return Position(enemy[1], enemy[2])
        if self.hivemind.enemy_core_pos is not None:
            return self.hivemind.enemy_core_pos
        return None

    def _run_builder(self, ct: Controller) -> None:
        pos = ct.get_position()
        if self.lastPos == pos:
            self.stuck += 1
        else:
            self.stuck = 0
        self.lastPos = pos

        if self.corePos is None:
            ally, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
            if ally[0] != CoreState.UNKNOWN:
                self.corePos = Position(ally[1], ally[2])
            if enemy[0] != CoreState.UNKNOWN:
                self.enemyCorePos = Position(enemy[1], enemy[2])
        if self.enemyCorePos is None:
            self.enemyCorePos = self._resolveEnemyCore(ct)

        runBuilderRoles(self, ct)
