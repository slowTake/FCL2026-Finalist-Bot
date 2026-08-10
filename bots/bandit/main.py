"""bandit — single-role economy bot, forked from baron. Policy at top; dispatch only below.

Where baron split five builders into 2 guardians / 1 harasser / 2 defaults and won
by holding ground, bandit runs **one role for every builder** and tries to win on
economy instead. Three ideas drive the fork:

  * **Spread, don't garrison.** Builders separate via `builder.spreadZone` — a
    static angle-wedge + distance-band assignment derived from `my_index` alone —
    applied as a soft score penalty on ore choice, never as a boundary. There is
    no leash and no roam cap anywhere in this bot.
  * **Siphon, don't destroy.** Any team's conveyor can pull any harvester's or
    belt's output, so an enemy harvester is worth more relayed than killed
    (`_harvesterOwnerAllowed` is hardcoded True) and an enemy belt is worth more
    hijacked than cut (`econwar.beltHijack`).
  * **Buy combat only out of surplus.** Turrets are gated behind
    `TI_SURPLUS_THRESHOLD` and exist to make the enemy spend Ti defending and
    rebuilding, not to break through.

Store slots (HiveMind v2):
  0  SLOT_RELAY_COMPLETE   bit 0 = a relay has completed (written by builders).
       Baron also packed CORE_THREAT_BIT into bit 1 to drive the guardian leash;
       bandit has no leash, so bit 1 is gone and only bit 0 is ever set.
  1  SLOT_ORPHAN_HINT      single-entry dangling-relay hint (logistics.py),
       last-write-wins, TTL-checked on read. Replaces baron's 3-slot orphan
       registry, which existed to cross the guardian-leash boundary — a boundary
       bandit does not have. UNDER REVIEW: if match observation shows this rarely
       populates (likely, since every builder already roams the whole map), drop
       it and free slot 1 too.
  2, 3  free — deliberately unallocated. The surplus gate and cordon band are
       pure live reads, and belt-hijack collision avoidance is the engine's own
       `can_build_conveyor`. Don't pre-allocate these; revisit only if testing
       shows a real coordination gap.
  4–15 HiveMind-owned — never write from Player
       4 builder registry · 5–12 delta ledger · 13 metronome
       14 map id · 15 packed cores
"""

from __future__ import annotations

from collections import deque

from fcode import Controller, EntityType, Position

# --- Builder spawn policy ---
# Crew size tracks the bank, counted in *living* builders so losses are replaced.
# Rush MAX_BUILDERS_EARLY unpaced, then hold whatever the bank justifies.
MAX_BUILDERS_EARLY = 5  # unpaced rush; also the poor-state floor
MAX_BUILDERS = 10  # hard ceiling on living builders

# Richer bank → bigger crew. (min_ti, target); highest matching min_ti wins.
# Below all tiers the target is MAX_BUILDERS_EARLY.
BUILDER_TARGET_BY_TI: list[tuple[int, int]] = [
    (2000, 10),
    (1000, 7),
]

# --- Spread zoning (builder.spreadZone) ---
# Bandit's replacement for baron's role split as the thing that stops every
# builder walking to the same best-scored ore. `my_index` maps to one of
# SPREAD_ANGLE_BUCKETS wedges around the ally core (bucket 0 centred on the
# core->enemy-core axis) and one of SPREAD_DISTANCE_BANDS radial bands
# (0 = our half, 1 = push toward the enemy core).
#
# The penalties below are *soft*: wrong wedge or wrong band costs an ore
# candidate points in the same weighted blend as ORE_PICK_SELF/CORE_WEIGHT, but
# never disqualifies it. A zone containing no ore must not starve its builder —
# it should simply drift into a neighbouring zone's leftovers, which is what a
# score penalty does and a hard boundary does not.
#
# Tuning lever: raise these to spread harder (builders commit to their wedge even
# when closer ore exists elsewhere), lower them to prioritise convenience.
SPREAD_ANGLE_BUCKETS = 4
SPREAD_DISTANCE_BANDS = 2
SPREAD_ANGLE_PENALTY = 6
SPREAD_BAND_PENALTY = 4

# --- Relay adoption (logistics.adoptDanglingBelt) ---
# Chebyshev radius, self-anchored, in which an idle builder will adopt someone
# else's orphaned belt end as its own relay origin and finish it via the normal
# relayToCore state machine. Small on purpose: this is "I am standing right
# here", not a search — a builder must never abandon its own ore to chase a belt
# tile it merely glimpsed. Complementary to DANGLING_BELT_SELF_CHEBYSHEV below,
# which handles the one-tile-gap case more cheaply.
ADOPT_BELT_CHEBYSHEV = 3

# Opportunistic single-tile belt patch radius (defence.finishNearbyBelt).
DANGLING_BELT_SELF_CHEBYSHEV = 3

# How stale a SLOT_ORPHAN_HINT entry may be before a reader evicts it.
ORPHAN_HINT_TTL_TURNS = 50

# --- Ti-surplus gated combat (econwar.py) ---
# Nothing offensive happens below this bank. Live read, no store slot — mirrors
# baron's GUN_PLACE_MIN_TI pattern. This is the single dial for "how rich must we
# be before we start burning Ti to make them burn Ti".
TI_SURPLUS_THRESHOLD = 300

# Cordon band, Chebyshev from the *enemy* core. Gunners go here and nowhere
# else: inside CORDON_MIN they are trivially answered by whatever defends the
# core, outside CORDON_MAX they are not applying pressure to anything.
CORDON_MIN_CHEBYSHEV = 6
CORDON_MAX_CHEBYSHEV = 8

# --- Relay merge (RELAY_MERGE_ENABLED False disables) ---
# A new relay may terminate into an existing ally belt line instead of walking
# all the way to the core, provided that line demonstrably reaches the core
# within RELAY_MERGE_MAX_HOPS.
RELAY_MERGE_ENABLED = True
RELAY_MERGE_MAX_HOPS = 10

# --- Ore pick weighting (logistics._oreConvenienceKey) ---
# Weighted blend, not a lexicographic sort: core proximity stays a real factor
# (breaks close ties toward the more defendable node) without letting a builder
# walk past ore at its feet to reach ore merely nearer the core.
ORE_PICK_SELF_WEIGHT = 2
ORE_PICK_CORE_WEIGHT = 1

# How long a remembered enemy blocker on an ore tile keeps disqualifying it
# before a builder is allowed to walk back and re-check.
ORE_BLOCK_MEMORY_TURNS = 30

# How stale a ledger belt sighting may be and still be trusted by beltFacingOk.
RELAY_LEDGER_TRUST_TURNS = 30

# Threat-aborts on one ore site before the builder blacklists it outright.
RELAY_THREAT_ABORT_LIMIT = 2

# Turns stuck on one ore claim before breakOreDeadlock drops it.
ORE_DEADLOCK_TURNS = 8

# Allow a builder to open/destroy an ally door blocking its own route.
OPEN_BLOCKED_ROUTE = True

# How often exploreFrontier re-picks a frontier target.
FRONTIER_REFRESH_TURNS = 20

# --- Encirclement breaker (≤0 disables) ---
# Turns a builder may sit motionless with no legal move in any direction before
# it destroys/fires at an adjacent building to open a door.
ENCIRCLED_TURNS = 8

# --- Oscillation window (bot.stuck; ≤1 disables the extra check) ---
# A unit confined to OSCILLATION_DISTINCT_LIMIT or fewer distinct tiles across
# the last OSCILLATION_WINDOW turns counts as stuck, not just one sitting
# perfectly still — otherwise a two- or three-tile ping-pong never trips any
# deadlock breaker.
OSCILLATION_WINDOW = 6
OSCILLATION_DISTINCT_LIMIT = 3

# --- Path-search budget (≤0 / None disables) ---
# Hard cap on full A* searches one bot may run in a single turn; past it,
# moveToward falls back to a direct cardinal step.
MAX_PATH_SEARCHES_PER_TURN = 3

# --- Gun placement (≤0 disables) ---
GUN_PLACE_MIN_TI = 60  # need bank ≥ this to place gunner/sentinel

# --- Ammo policy ---
AMMO_BASE = 12
AMMO_PER_GUNNER = 3
AMMO_PER_SENTINEL = 10
AMMO_CONVERT_CHUNK = 10
AMMO_CONVERT_MIN_TI = 30  # need bank ≥ this to convert; ≤0 disables
AMMO_MIN_POOL = 20
MAX_AMMO_POOL: int | None = None
AMMO_START_TURN = 1

# --- Store slots (Player-owned: 0-3 only) ---
SLOT_RELAY_COMPLETE = 0
SLOT_ORPHAN_HINT = 1
SLOT_PACKED_CORES = 15

# Bit 0 of SLOT_RELAY_COMPLETE. Baron's bit 1 (CORE_THREAT_BIT) is dropped along
# with the guardian leash it fed, so readers no longer need to mask — but the
# named bit is kept so the write stays explicit and slot 0 remains extensible.
RELAY_COMPLETE_BIT = 1

DEBUG = True

from builder import runBuilder
from guns import gunnerLoop, sentinelLoop, topUpAmmo
from hivemind import CoreState, HiveMind, unpack_cores
from respawn import Respawn, SpawnSchedule

SPAWN_SCHEDULE = SpawnSchedule(
    max_builders_early=MAX_BUILDERS_EARLY,
    max_builders=MAX_BUILDERS,
    builder_target_by_ti=tuple(BUILDER_TARGET_BY_TI),
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
        self.relayThreatBlocked: set[tuple[int, int]] = set()
        self.threatAbortSite: tuple[int, int] | None = None
        self.threatAbortCount = 0
        self.blockadeDoor: tuple[int, int] | None = None
        self.relayPathBlocked = False
        self.relayPathBlockedFrom: tuple[int, int] | None = None
        self.lastPathFailed = False
        self.lastPathFailedTarget: tuple[int, int] | None = None
        self.cachedPath: list[Position] | None = None
        self.cachedPathTarget: Position | None = None
        self.lastPos: Position | None = None
        self.stuck = 0
        # Exact-standstill only, never reset by anything but real movement —
        # unlike `stuck`, which also trips on oscillation and gets zeroed by
        # several consumers. breakEncirclement needs the pure "did this unit
        # truly not move" reading: an oscillating unit has a legal move (that is
        # what oscillation is), so it is not encircled by definition.
        self.motionless = 0
        self.posHistory: deque[tuple[int, int]] = deque(maxlen=OSCILLATION_WINDOW)
        self.pathSearchesThisTurn = 0
        self.recheckingOre = False
        self.skippedOres: set[tuple[int, int]] = set()
        self.lastOreRecheckRound = -999
        # Cached (angle_bucket, distance_band) from builder.spreadZone. Pure
        # function of my_index, so it is computed once and never invalidated.
        self._spreadZone: tuple[int, int] | None = None

    def run(self, ct: Controller) -> None:
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

    def _run_core(self, ct: Controller) -> None:
        topUpAmmo(
            ct, local_map=self.hivemind.local_map, min_turn=AMMO_START_TURN
        )

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
        self.pathSearchesThisTurn = 0
        pos = ct.get_position()
        self.posHistory.append((pos.x, pos.y))
        oscillating = (
            OSCILLATION_DISTINCT_LIMIT > 0
            and len(self.posHistory) == self.posHistory.maxlen
            and len(set(self.posHistory)) <= OSCILLATION_DISTINCT_LIMIT
        )
        if self.lastPos == pos or oscillating:
            self.stuck += 1
        else:
            self.stuck = 0
        self.motionless = self.motionless + 1 if self.lastPos == pos else 0
        self.lastPos = pos

        if self.corePos is None:
            ally, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
            if ally[0] != CoreState.UNKNOWN:
                self.corePos = Position(ally[1], ally[2])
            if enemy[0] != CoreState.UNKNOWN:
                self.enemyCorePos = Position(enemy[1], enemy[2])
        if self.enemyCorePos is None:
            self.enemyCorePos = self._resolveEnemyCore(ct)

        runBuilder(self, ct)
