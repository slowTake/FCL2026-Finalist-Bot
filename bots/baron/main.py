"""baron — multi-module bot template. Policy at top; dispatch only below.

Store slots (Player-owned until assigned):
  0  SLOT_RELAY_COMPLETE   TBD
  1  SLOT_PHALANX_THREAT    TBD
  2  SLOT_HARVESTER_COUNT   total harvesters built
  3  SLOT_SENTINEL_COUNT    bookkeeping TBD
  4–6  free
  7–15 HiveMind-owned — never write from Player
"""

from __future__ import annotations

from fcode import Controller, EntityType, Position

# --- Builder spawn policy ---
MAX_BUILDERS_EARLY = 5
MAX_BUILDERS = 7
MAX_BUILDERS_THREAT = 9
FIRST_INTERVAL_SPAWN = 10
SPAWN_INTERVAL = 25
LATE_SPAWN_INTERVAL = 50
FALLBACK_SPAWN_INTERVAL = 200
REPLENISH_START_TURN = 75
SPAWN_EXTRA_UNDER_THREAT = False

# --- Harvester caps (built count, store slot 2) — TBD values when economy lands ---
HARVESTER_CAP_BEFORE_50 = 2
HARVESTER_CAP_BEFORE_100 = 3
HARVESTER_CAP_BEFORE_200 = 4
HARVESTER_CAP_BEFORE_250 = 5
HARVESTER_CAP_BEFORE_400 = 6

# --- Ammo policy (guns.top_up_ammo reads these) ---
AMMO_BASE = 12
AMMO_PER_GUNNER = 2
AMMO_PER_SENTINEL = 10
AMMO_CONVERT_CHUNK = 10
AMMO_MIN_POOL = 20
DEFAULT_AMMO_START_TURN = 10
RAIDER_AMMO_START_TURN = 30
MAX_AMMO_POOL: int | None = None  # None = no hard cap beyond convert logic

# --- Store slots ---
SLOT_RELAY_COMPLETE = 0
SLOT_PHALANX_THREAT = 1
SLOT_HARVESTER_COUNT = 2
SLOT_SENTINEL_COUNT = 3
SLOT_PACKED_CORES = 15

DEBUG = False

from guns import run_gunner, run_sentinel, top_up_ammo
from hivemind import HiveMind
from respawn import Respawn, SpawnSchedule
from builder import run_builder_roles

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


class Player:
    def __init__(self) -> None:
        self.hivemind = HiveMind()
        self.respawn = Respawn()

    def run(self, ct: Controller) -> None:
        try:
            self.hivemind.tick(ct)
            etype = ct.get_entity_type()
            if etype == EntityType.CORE:
                self._run_core(ct)
            elif etype == EntityType.BUILDER_BOT:
                self._run_builder(ct)
            elif etype == EntityType.GUNNER:
                run_gunner(ct)
            elif etype == EntityType.SENTINEL:
                run_sentinel(ct)
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

        goal = Position(ct.get_map_width() // 2, ct.get_map_height() // 2)
        self.respawn.run_core_spawn(
            ct,
            schedule=SPAWN_SCHEDULE,
            threat=False,
            map_id=self.hivemind.map_id,
            goal=goal,
            hivemind=self.hivemind,
        )

    def _run_builder(self, ct: Controller) -> None:
        run_builder_roles(self, ct)
