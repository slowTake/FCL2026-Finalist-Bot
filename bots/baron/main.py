"""baron — multi-module bot. Policy at top; dispatch only below.

Store slots (HiveMind v2):
  0  SLOT_RELAY_COMPLETE   1 after first inbound relay finishes
  1–3 SLOT_ORPHAN_1/2/3    orphaned-harvester registry (logistics.py) — the
       last Player-free slots; nothing else may claim them
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
# Previously the target was `MAX_BUILDERS_EARLY + round_n // interval`, i.e. a
# function of round number with the current Ti tier applied retroactively: crossing
# a bracket leapt the target and spawned a run of builders back to back, and falling
# back froze spawning for hundreds of rounds because it compared against a lifetime
# counter that never decreases. Both are gone with the round arithmetic.
MAX_BUILDERS_EARLY = 5  # unpaced rush; also the poor-state floor
MAX_BUILDERS = 10  # hard ceiling on living builders

# Richer bank → bigger crew. (min_ti, target); highest matching min_ti wins.
# Below all tiers the target is MAX_BUILDERS_EARLY.
BUILDER_TARGET_BY_TI: list[tuple[int, int]] = [
    (2000, 10),
    (1000, 7),
]

# --- Guardian cap (≤0 / None disables) ---
# Guardian slots are idx % 5 in (0, 1) — 2 per 5 spawns. Guardians past this rank
# dispatch as non-guardians instead. Inert while MAX_BUILDERS is 10: guardians at a
# crew of 10 are ranks 1-4, so the cap first binds at builder 11. Kept as a safety
# net for raising the crew ceiling later. Rank is derived from my_index alone
# (builder.guardianRank), so role stays a pure function of the index: deterministic,
# permanent per unit, no coordination, no oscillation.
MAX_GUARDIANS: int | None = 2

# --- Wave attack (≤0 / None = unset; positive = active gate) ---
WAVE_ATTACK_TURN: int | None = -1  # e.g. 300 → all builders harass from that turn
WAVE_ATTACK_TI: int | None = 2000  # e.g. 2000 → all harass once bank ≥ this

# --- Default early defend (age after birth_round; ≤0 disables) ---
# Only applies inside runDefault — harassers/guardians never see this.
BUILDER_HOME_TURNS = 0

# --- Defensive sentinel (False disables) ---
# An enemy sentinel reaches sqrt(32) ≈ 5.7 and shells the core from outside the
# Chebyshev-4 leash, where a gunner (reach sqrt(13) ≈ 3.6) cannot answer it
# without leaving home. Our own sentinel has the same 5.7, so a pad *inside* the
# leash covers the gap — the guardian never breaks the leash. Detection reads the
# HiveMind ledger, not just own vision (builder vision is only sqrt(20) ≈ 4.5).
DEF_SENTINEL = True
TURRET_THREAT_CHEBYSHEV = 6  # enemy turret this close to core counts as a threat
TURRET_MEMORY_TURNS = 20  # how stale a ledger sighting may be and still count

# --- Guardian idle economy (False disables) ---
# While no enemy unit is in vision, guardians build harvesters on ore inside the
# leash and finish/repair belt relays back to core instead of only patrolling.
GUARDIAN_ECONOMY = True
# Extra Chebyshev slack on the guardian leash while it is committed to a node or
# relay — an ore at the leash edge is worked from an adjacent tile one step out.
GUARDIAN_ECON_SLACK = 1

# --- Empty-ore pick (defaults) ---
# False = nearest convenient (core then self). True = index-stagger slots.
ORE_STAGGER = False

# --- Ore-block ledger memory (≤0 disables the age bound) ---
# How stale a remembered enemy blocker (most commonly a denyOre barrier) on an
# ore tile may be and still disqualify it in pickEmptyOre/_pickRecheckOre.
# Without this the rejection is permanent: nothing ever expires entity memory in
# a builder's own local_map short of regaining vision of that exact tile, and
# the rejection is exactly what stops a builder from ever walking back to look
# — self-reinforcing, forever. Mirrors TURRET_MEMORY_TURNS's role for
# threateningTurret: an old sighting is still useful signal, just not forever.
ORE_BLOCK_MEMORY_TURNS = 30

# --- Ore deadlock breaker (≤0 disables) ---
# Turns a default builder may sit motionless outside a relay before it drops its
# ore claim, blacklists the node and re-picks. Guards against builders piling up
# on one node and never yielding it.
ORE_DEADLOCK_TURNS = 8

# Turns a harasser may sit motionless before it blacklists its sticky belt/harvest
# target and re-picks. Mirrors ORE_DEADLOCK_TURNS for the offence side: denyBelt's
# Mode B (cut the belt) is gated off whenever the bank can afford a gun, so a funded
# harasser correctly decides it cannot profitably touch a belt — and then had no way
# to give up on it. ≤0 disables.
HARASS_DEADLOCK_TURNS = 8

# --- Blocked-route breaker (False disables) ---
# When A* proves the ore claim unreachable, replan with enemy barriers passable to
# find which one seals the lane, and answer it with a gunner: 3 shots / 6 Ti and ~3
# turns, against a builder's 15 turns and 30 Ti. The gunner then stays and kills
# re-walls. No barrier on the route (terrain or ally buildings) → the node is
# blacklisted instead. Known case: the walled-off centre ore on atoll.
OPEN_BLOCKED_ROUTE = True

# --- Harasser belt-cut throttle (≤0 disables) ---
# Rounds a harasser waits between *starting* belt cuts. A cut is 20 HP at 2 dmg per
# attack: 10 shots, 20 Ti, 10 turns — worth it when it completes (they pay 9-33 Ti to
# reopen and the downstream line delivers nothing meanwhile), pure waste when it does
# not. Only starts are throttled; a cut already underway always runs to the barrier.
DENY_CUT_COOLDOWN = 50

# --- Harasser ore denial (≤0 / None disables) ---
# Barrier empty ore in the enemy's expansion ring: 3 Ti makes the tile unbuildable and
# prevents a 20 Ti harvester plus its whole belt line; they pay 6-30 Ti to clear it.
# Two guards keep it off our own side. _oreCloserToAlly draws the midline, and the
# floor below covers what the midline cannot: on a small map an ore can be marginally
# nearer their core yet sit well inside our working range. Chosen from the map tables,
# not guessed — across all 21 known maps at radius 6, floors 0-6 barely bite (64->61
# candidate nodes) and leave atoll's contested centre deposit (9,8) exposed; 8 drops it
# to 52 and correctly silences the feature on sprint (10x10) and sweden, where the two
# cores are close enough that "near theirs" is "near ours". 10 over-corrects, killing 5.
DENY_ORE_CHEBYSHEV = 6
DENY_ORE_MIN_ALLY_CHEBYSHEV = 8

# --- Frontier scouting (≤0 / None disables) ---
# Last-resort exploration when the normal chains have no target. Needed because a
# missed map fingerprint leaves map_id 0, so ORE_LOCATIONS is empty and enemyCorePos is
# never set — and nothing in baron explores. On heart/sweden/vase the core sees no ore
# in its own vision at turn 0, so defaults had no ore claim and no way to find one, and
# the economy never started at all. Inert on maps we did fingerprint, because the static
# tables supply targets before this step is reached. Value is the re-pick interval: the
# ledger scan is ~900 cells against a 10ms budget, so raise it if turns get truncated.
FRONTIER_REFRESH_TURNS = 20

# --- Encirclement breaker (≤0 disables) ---
# Turns a builder may sit motionless with no legal move in any direction before
# it destroys/fires at an adjacent building to open a door. Catches a harasser
# that fenced itself in with its own denyBelt barriers.
ENCIRCLED_TURNS = 8

# --- Oscillation window (bot.stuck; ≤1 disables the extra check) ---
# `bot.stuck` used to count only exact standstill (identical tile turn over turn),
# which never trips while a unit bounces between two or more tiles — e.g. denyOre
# pulling toward an unreachable ore while avoidFire's dodge memory pushes back the
# next turn. That left every stuck-counter consumer (breakOreDeadlock,
# breakBeltDeadlock, breakEncirclement) dead code against a harasser oscillating in
# place: it never accumulates ENCIRCLED_TURNS/ORE_DEADLOCK_TURNS/HARASS_DEADLOCK_TURNS
# and, worse, never falls through to yield the turn to whatever comes after it in the
# role chain (see denyBelt never getting a turn behind a looping denyOre). A unit
# confined to OSCILLATION_DISTINCT_LIMIT or fewer distinct tiles across the last
# OSCILLATION_WINDOW turns now also counts as stuck.
OSCILLATION_WINDOW = 6
OSCILLATION_DISTINCT_LIMIT = 2

# --- Path-search budget (≤0 / None disables) ---
# Hard cap on full A* searches (`astarFromSeeds`, PATH_MAX_ITERS=800 each) one bot
# may run in a single turn. Without this, a harasser whose role chain falls through
# several move-toward steps in one turn (denyOre → seekHarvester's per-candidate
# loop → seekCore's orbit fallback) can trigger 5-15 full searches in that one call,
# which is the dominant cost behind an "Exec Time TLE" turn — one failed search
# already burns its full iteration budget (no early exit, unlike a hit), and nothing
# upstream bounded how many of those a single turn could stack. Once the budget is
# spent, `moveToward` falls back to a single direct cardinal step instead of
# searching, same as its existing no-path fallback.
MAX_PATH_SEARCHES_PER_TURN = 3

# --- Gun placement (≤0 disables) ---
GUN_PLACE_MIN_TI = 60 # need bank ≥ this to place gunner/sentinel

# --- React-freeze cap (≤0 / None disables) ---
# _reactEnemyTurret's skipEconomy branch consumes a default builder's whole turn,
# every turn, for as long as an enemy turret stays in vision and nothing about it
# is actionable (most commonly can't afford GUN_PLACE_MIN_TI yet) — with no cap,
# observed freezing a builder in place for 100+ turns next to a turret it was
# never going to answer anyway. Past this many *consecutive* frozen turns, stop
# freezing and let the chain fall through to economy instead: buildHarvester /
# checkOre / relayToCore already carry their own per-tile threat checks
# (tileThreatenedByGunner), so this only removes the blanket "turret merely
# visible" freeze, not tile-level danger avoidance.
REACT_FREEZE_CAP_TURNS = 15

# --- Core push gate (≤0 / None disables) ---
# Harvesters are the easier, cheaper kill — denying one starves a whole belt line for
# 3-20 Ti. Shooting the enemy core itself only pays off once the bank shows a real
# economic lead; below this the harasser should keep working harvesters/ore instead of
# tickling the core for free damage it can't press home.
CORE_PUSH_MIN_TI = 150  # need bank ≥ this before offGun targets the enemy core

# --- Ammo policy (guns.topUpAmmo reads these) ---
AMMO_BASE = 12
AMMO_PER_GUNNER = 3
AMMO_PER_SENTINEL = 10
AMMO_CONVERT_CHUNK = 10
AMMO_CONVERT_MIN_TI = 30  # need bank ≥ this to convert; ≤0 disables
AMMO_MIN_POOL = 20
DEFAULT_AMMO_START_TURN = 1
RAIDER_AMMO_START_TURN = 30
MAX_AMMO_POOL: int | None = None

# --- Store slots (Player 0–3 only; 4–15 HiveMind-owned) ---
SLOT_RELAY_COMPLETE = 0
SLOT_ORPHAN_1 = 1
SLOT_ORPHAN_2 = 2
SLOT_ORPHAN_3 = 3
SLOT_PACKED_CORES = 15

# --- Orphan relay registry (≤0 disables reporting/pickup) ---
# How stale an orphan-registry entry (bots/baron/logistics.py) may be before a
# reader treats it as unreadable and evicts it on next write. No claim/heartbeat
# mechanism exists (see docs/adr or CONTEXT.md's RelayToCore section for why),
# so this is the only thing that ever frees a slot whose harvester died or got
# reconnected some way the explicit clear-on-success hook didn't see. Mirrors
# ORE_RECHECK_CLEAR_INTERVAL (logistics.py) rather than inventing a new figure.
ORPHAN_REGISTRY_TTL_TURNS = 50

DEBUG = True

from guns import gunnerLoop, sentinelLoop, topUpAmmo
from hivemind import CoreState, HiveMind, unpack_cores
from respawn import Respawn, SpawnSchedule
from builder import runBuilderRoles

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
        self.blockadeDoor: tuple[int, int] | None = None
        self.relayPathBlocked = False
        self.relayPathBlockedFrom: tuple[int, int] | None = None
        self.lastPathFailed = False
        self.lastPathFailedTarget: tuple[int, int] | None = None
        self.cachedPath: list[Position] | None = None
        self.cachedPathTarget: Position | None = None
        self.lastPos: Position | None = None
        self.stuck = 0
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
        self.skippedTargets: set[tuple[int, int]] = set()
        self.lastOreRecheckRound = -999
        self.harassTarget: tuple[int, int] | None = None
        self.denyBeltTile: tuple[int, int] | None = None
        self.lastCutRound = -999
        self.scoutedOres: set[tuple[int, int]] = set()
        self.patrolIdx = 0
        self.harassOrbitIdx = 0

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
