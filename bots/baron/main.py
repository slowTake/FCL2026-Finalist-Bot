"""baron — multi-module bot. Policy at top; dispatch only below.

Store slots (HiveMind v2):
  0  SLOT_RELAY_COMPLETE   bit-packed: bit 0 = a relay has completed (written
       by builders), bit 1 = CORE_THREAT_BIT, the core sees an enemy turret
       (gunner/sentinel/launcher — not a bare builder; written only by the
       core). Readers must mask — no slot is free, so the guardian-leash
       signal rides in slot 0's 31 unused bits.
  1–3 SLOT_ORPHAN_1/2/3    orphaned-harvester registry (logistics.py) — the
       last Player-free slots; nothing else may claim them
  4–15 HiveMind-owned — never write from Player
       4 builder registry · 5–12 delta ledger · 13 metronome
       14 map id · 15 packed cores
"""

from __future__ import annotations

from collections import deque

from fcode import Controller, EntityType, Position

# All tuning constants and store-slot layout live in policy.py (a leaf module,
# so it can be imported at module scope everywhere without risking a cycle
# with this file). Re-exported here via `import *` so every existing
# `from main import X` call site elsewhere in the codebase keeps working
# unchanged — this only moved where the values live, not how they're read.
from policy import *  # noqa: F401,F403

DEBUG = True

from guns import gunnerLoop, sentinelLoop, topUpAmmo
from hivemind import CoreState, HiveMind, unpack_cores
from respawn import Respawn, SpawnSchedule
from builder import runBuilderRoles
from defence import GuardianMemory
from logistics import setCoreThreat
from logistics_helpers import enemyTurretInVision
from offense import OffenseMemory

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
        self.offense = OffenseMemory()
        self.guardian = GuardianMemory()

        self.corePos: Position | None = None
        self.enemyCorePos: Position | None = None
        # Core-only: last round the core saw an enemy turret/builder in its own
        # vision. Drives CORE_THREAT_BIT and hence the guardian leash.
        self.coreThreatRound = -(10**9)
        self.oreSite: Position | None = None
        self.inRelay = False
        self.relayOrigin: Position | None = None
        self.relayPath: list[Position] | None = None
        self.relayBuiltTo = -1
        self.relayStuck = 0
        # Set only for a verifyHomeHarvester pass: makes beltFacingOk refuse the
        # ledger fallback so the line is proven by eye, not by memory. Cleared by
        # clearRelay so it can never leak into an ordinary relay.
        self.relayVerify = False
        self.lastHarvesterVerifyRound = -999
        # (x, y) -> round this builder last verified that harvester's line.
        self.verifiedHarvesters: dict[tuple[int, int], int] = {}
        self.blockadeDoor: tuple[int, int] | None = None
        self.relayPathBlocked = False
        self.relayPathBlockedFrom: tuple[int, int] | None = None
        self.lastPathFailed = False
        self.lastPathFailedTarget: tuple[int, int] | None = None
        self.cachedPath: list[Position] | None = None
        self.cachedPathTarget: Position | None = None
        # Own cache for moveTowardLeash — region-goal-shaped, so it can't share
        # cachedPath/cachedPathTarget (point-goal-shaped) without each clobbering
        # the other whenever a guardian alternates between the two.
        self.cachedLeashPath: list[Position] | None = None
        self.cachedLeashCore: Position | None = None
        self.cachedLeashRadius: int | None = None
        self.lastPos: Position | None = None
        self.stuck = 0
        # Gunner-only: consecutive turns since this gunner last fired. Drives
        # GUNNER_IDLE_TURNS self-destruct in gunnerLoop.
        self.turnsSinceFire = 0
        # Exact-standstill only, never reset by anything but real movement — unlike
        # `stuck` (also trips on oscillation, and gets reset by several consumers,
        # e.g. _reactEnemyTurret's freeze branch, breakOreDeadlock). breakEncirclement
        # needs the pure "did this unit truly not move" reading: an oscillating unit
        # has a legal move (that's what oscillation is), so it is not encircled by
        # definition, and a counter other steps can reset out from under it would
        # let a genuinely walled-in unit sit past ENCIRCLED_TURNS indefinitely
        # whenever something else keeps zeroing the shared counter first.
        self.motionless = 0
        self.posHistory: deque[tuple[int, int]] = deque(maxlen=OSCILLATION_WINDOW)
        self.pathSearchesThisTurn = 0
        # Consecutive turns _reactEnemyTurret has frozen this builder via its
        # skipEconomy branch — see REACT_FREEZE_CAP_TURNS.
        self.reactFrozen = 0
        self.recheckingOre = False
        self.skippedOres: set[tuple[int, int]] = set()
        self.lastOreRecheckRound = -999

    def run(self, ct: Controller) -> None:
        self.hivemind.tick(ct)
        etype = ct.get_entity_type()
        if etype == EntityType.CORE:
            self._run_core(ct)
        elif etype == EntityType.BUILDER_BOT:
            self._run_builder(ct)
        elif etype == EntityType.GUNNER:
            gunnerLoop(self, ct)
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

        # Guardian leash signal. The core is the right detector: its vision is
        # r²=36 against a builder's r²=20, it is a singleton (so no store write
        # contention with itself), and HiveMind.tick already enumerated
        # get_nearby_entities() this turn, so the scan is effectively free.
        # The hold window lives here, in core instance state, rather than
        # costing store bits — Player persists across turns.
        #
        # Turret-only (enemyTurretInVision: gunner/sentinel/launcher), not
        # _enemyUnitInVision's turret-or-builder — an enemy builder scouting
        # past the core isn't a threat the guardian leash needs to tighten
        # for; only something that can actually shell the core should do that.
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
