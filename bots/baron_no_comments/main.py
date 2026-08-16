
from __future__ import annotations

from collections import deque

from fcode import Controller, EntityType, Position

MAX_BUILDERS_EARLY = 5  # unpaced rush; also the poor-state floor
MAX_BUILDERS = 10  # hard ceiling on living builders

BUILDER_TARGET_BY_TI: list[tuple[int, int]] = [
    (2000, 10),
    (1000, 7),
]

BUILDER_GROWTH_DELAY_TURNS = 80

MAX_GUARDIANS: int | None = 2

WAVE_ATTACK_TURN: int | None = -1  # e.g. 300 → all builders harass from that turn
WAVE_ATTACK_TI: int | None = 2000  # e.g. 2000 → all harass once bank ≥ this

BUILDER_HOME_TURNS = 0

DEF_SENTINEL = True
TURRET_THREAT_CHEBYSHEV = 6  # enemy turret this close to core counts as a threat
TURRET_MEMORY_TURNS = 20  # how stale a ledger sighting may be and still count

GUARDIAN_ECONOMY = True
GUARDIAN_ECON_SLACK = 1

GUARDIAN_LEASH_WIDE = 8
GUARDIAN_LEASH_TIGHT = 4
GUARDIAN_LEASH_HOLD_TURNS = 20

DANGLING_BELT_SELF_CHEBYSHEV = 3

HARVESTER_VERIFY_INTERVAL_TURNS = 40

GUARDIAN_ORE_WAIT_TURNS = 3

NEARBY_HARVESTER_SELF_CHEBYSHEV = 3
GUARDIAN_PATROL_OFFSET = 3

RELAY_MERGE_ENABLED = True
RELAY_MERGE_MAX_HOPS = 10

STEAL_ENEMY_HARVESTERS = True

ORE_STAGGER = False

ORE_PICK_SELF_WEIGHT = 2
ORE_PICK_CORE_WEIGHT = 1

ORE_BLOCK_MEMORY_TURNS = 30

RELAY_LEDGER_TRUST_TURNS = 30

ORE_DEADLOCK_TURNS = 8

HARASS_DEADLOCK_TURNS = 8

HARASS_LEASH_CHEBYSHEV = 7

OPEN_BLOCKED_ROUTE = True

DENY_CUT_COOLDOWN = 50

DENY_BELT_COOLDOWN = 10

UNDEFENDED_BELT_CHEBYSHEV = 5

DENY_ORE_CHEBYSHEV = 6
DENY_ORE_MIN_ALLY_CHEBYSHEV = 8

FRONTIER_REFRESH_TURNS = 20

ENCIRCLED_TURNS = 8

OSCILLATION_WINDOW = 6
OSCILLATION_DISTINCT_LIMIT = 3

MAX_PATH_SEARCHES_PER_TURN = 3

GUN_PLACE_MIN_TI = 60 # need bank ≥ this to place gunner/sentinel

REACT_FREEZE_CAP_TURNS = 15

CORE_PUSH_MIN_TI = 150  # need bank ≥ this before offGun targets the enemy core

AMMO_BASE = 12
AMMO_PER_GUNNER = 3
AMMO_PER_SENTINEL = 10
AMMO_CONVERT_CHUNK = 10
AMMO_CONVERT_MIN_TI = 30  # need bank ≥ this to convert; ≤0 disables
AMMO_MIN_POOL = 20
DEFAULT_AMMO_START_TURN = 1
RAIDER_AMMO_START_TURN = 30
MAX_AMMO_POOL: int | None = None

SLOT_RELAY_COMPLETE = 0
SLOT_ORPHAN_1 = 1
SLOT_ORPHAN_2 = 2
SLOT_ORPHAN_3 = 3
SLOT_PACKED_CORES = 15

RELAY_COMPLETE_BIT = 1
CORE_THREAT_BIT = 2

ORPHAN_REGISTRY_TTL_TURNS = 50

DEBUG = True

from guns import gunnerLoop, sentinelLoop, topUpAmmo
from hivemind import CoreState, HiveMind, unpack_cores
from respawn import Respawn, SpawnSchedule
from builder import runBuilderRoles
from logistics import enemyTurretInVision, setCoreThreat

SPAWN_SCHEDULE = SpawnSchedule(
    max_builders_early=MAX_BUILDERS_EARLY,
    max_builders=MAX_BUILDERS,
    builder_target_by_ti=tuple(BUILDER_TARGET_BY_TI),
    growth_delay_turns=BUILDER_GROWTH_DELAY_TURNS,
)


class Player:
    def __init__(self) -> None:
        self.hivemind = HiveMind()
        self.respawn = Respawn()

        self.corePos: Position | None = None
        self.enemyCorePos: Position | None = None
        self.coreThreatRound = -(10**9)
        self.oreSite: Position | None = None
        self.inRelay = False
        self.relayOrigin: Position | None = None
        self.relayPath: list[Position] | None = None
        self.relayBuiltTo = -1
        self.relayStuck = 0
        self.relayVerify = False
        self.lastHarvesterVerifyRound = -999
        self.verifiedHarvesters: dict[tuple[int, int], int] = {}
        self.blockadeDoor: tuple[int, int] | None = None
        self.relayPathBlocked = False
        self.relayPathBlockedFrom: tuple[int, int] | None = None
        self.lastPathFailed = False
        self.lastPathFailedTarget: tuple[int, int] | None = None
        self.cachedPath: list[Position] | None = None
        self.cachedPathTarget: Position | None = None
        self.lastPos: Position | None = None
        self.stuck = 0
        self.motionless = 0
        self.posHistory: deque[tuple[int, int]] = deque(maxlen=OSCILLATION_WINDOW)
        self.pathSearchesThisTurn = 0
        self.reactFrozen = 0
        self.recheckingOre = False
        self.skippedOres: set[tuple[int, int]] = set()
        self.skippedTargets: set[tuple[int, int]] = set()
        self.lastOreRecheckRound = -999
        self.harassTarget: tuple[int, int] | None = None
        self.denyBeltTile: tuple[int, int] | None = None
        self.lastCutRound = -999
        self.lastDenyBeltRound = -999
        self.supplyCutTile: tuple[int, int] | None = None
        self.lastSupplyCutRound = -999
        self.scoutedOres: set[tuple[int, int]] = set()
        self.patrolIdx = 0
        self.harassOrbitIdx = 0
        self.gunLineBlockTile: tuple[int, int] | None = None
        self.guardianOreBarrier: Position | None = None
        self.guardianOreWaitTurns = 0

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
        round_n = ct.get_current_round()
        min_turn = (
            DEFAULT_AMMO_START_TURN
            if round_n < RAIDER_AMMO_START_TURN
            else RAIDER_AMMO_START_TURN
        )
        topUpAmmo(ct, local_map=self.hivemind.local_map, min_turn=min_turn)

        if enemyTurretInVision(ct):
            self.coreThreatRound = round_n
        setCoreThreat(
            ct, round_n - self.coreThreatRound <= GUARDIAN_LEASH_HOLD_TURNS
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

        runBuilderRoles(self, ct)