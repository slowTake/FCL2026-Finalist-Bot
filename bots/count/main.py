"""count — dispatch only. Every decision lives in a role file.

Store slots are owned and documented by store.py — that is the only place the
layout lives, and the only writer for the slots that are ours.
"""

from __future__ import annotations

import os
import sys

# Put the subdirectories on the path so every module can keep importing flat.
# This is not cosmetic: hivemind.py is a frozen shared component that does
# `from features import ...`, so those files have to be importable by bare name.
# The submission zip is flattened by scripts/zip_bot.py, where these directories
# do not exist — hence the isdir guard, which makes this a no-op there.
# The runner execs main.py as source instead of importing it, so __file__ is not
# always defined. That only happens in the flattened zip, where there is nothing
# to add anyway, so a missing __file__ means skip the whole block.
_HERE = globals().get("__file__")
if _HERE:
    _HERE = os.path.dirname(os.path.abspath(_HERE))
    for _sub in ("hivemind", "roles"):
        _path = os.path.join(_HERE, _sub)
        if os.path.isdir(_path) and _path not in sys.path:
            sys.path.insert(0, _path)

from fcode import Controller, EntityType, Position  # noqa: E402

import debug  # noqa: E402
import guns  # noqa: E402
import launcher  # noqa: E402
import raid  # noqa: E402
import store  # noqa: E402
from ecoBot import runEcoBot  # noqa: E402
from guardianBot import runGuardian  # noqa: E402
from hivemind import CoreState, HiveMind, unpack_cores  # noqa: E402
from raidBot import runRaidBot  # noqa: E402
from respawn import isReplenishmentRaider, runCoreSpawn  # noqa: E402
from sentinelBot import runSentinelBot  # noqa: E402
from telemetry import ENABLED as CPU_TELEMETRY, CpuMeter  # noqa: E402


class Player:
    def __init__(self) -> None:
        self.hivemind = HiveMind()
        self.corePos: Position | None = None

        # Relay state (relays.py)
        self.inRelay = False
        self.relayPath: list[Position] | None = None
        self.relayAnchor: Position | None = None
        self.relayFrom: Position | None = None
        self.relayAvoid: set[tuple[int, int]] = set()
        # tile -> times this relay has re-laid it, so a destroy/rebuild war with
        # another builder is detectable (see relays.BELT_CONTEST_LIMIT)
        self.relayRebuilds: dict[tuple[int, int], int] = {}
        # Reconnection relays (ecoBot.reconnectOrphan) set this so planRelay merges
        # only onto a *proven* network member, not a belt chain it merely can't see to
        # be cut — the orphan's own dead-ended partial line reads intact from afar and
        # would otherwise be trusted, leaving the harvester unconnected. Off for normal
        # relays, which rely on that out-of-vision trust. Cleared by _teardownRelay.
        self._strictRelay = False

        # Walk state (movement.py)
        self.walkPath: list[Position] | None = None
        # What walkPath was planned toward. The cache is only reused while this
        # matches, so a retarget replans instead of finishing the old journey.
        self.walkGoal = None
        self.walkAvoid: set[tuple[int, int]] = set()

        # Core-reachable open-terrain set (ph.coreBeltReach). Cached across turns and
        # only re-flooded when the ledger grows (new terrain) or the refresh window
        # lapses — terrain is immutable, so this stays valid between. `_beltReachKnown`
        # is the ledger tile-count it was built at; `_beltReachRound` the round.
        # Drives pocket-ore rejection and the relay-seed approach in ecoBot.
        self._beltReachKnown = -1
        self._beltReachRound = -1
        self._beltReachSet: set[tuple[int, int]] = set()

        # Economy state (ecoBot.py)
        self.oreTarget: Position | None = None
        self.oreSkip: dict[tuple[int, int], int] = {}
        # Lowest HP this builder has driven an enemy barrier sitting on a claimed
        # ore node to, tile -> (low HP, consecutive stalled checks) — see
        # patience.stallCheck (ecoBot.buildHarvester via _barrierRepaired).
        # Barrier-grind is otherwise a stateless re-fire, so an enemy repairing the
        # barrier as fast as we cut would trap the builder bleeding Ti on a node it
        # can never free; when the low stops improving we give the node up via
        # oreSkip and move on. Per-unit and self-healing, same as oreSkip. Empty on
        # any unit that never grinds an ore barrier.
        self._oreGrindLow: dict[tuple[int, int], tuple[int, int]] = {}
        # Fling detection (ecoBot._handleFling). Position at the start of this
        # unit's last eco turn, to spot an external >1-tile displacement (an enemy
        # launcher throw) turn-over-turn. None until its first eco turn.
        self._ecoLastPos: Position | None = None
        # Net-progress backstop (ecoBot._approachStalled). The node currently being
        # walked to, the closest we have come to it, and how many turns that best
        # has stood without improving — so a builder reporting walk progress every
        # turn yet never closing on its node is given up rather than churning.
        self._ecoStallGoal: tuple[int, int] | None = None
        self._ecoBestDist: int = 0
        self._ecoStallTurns: int = 0
        # Orphan reconnection (ecoBot.reconnectOrphan). The harvester this builder
        # built but was flung off before its relay reached the core — it feeds nothing
        # and nobody else returns to it (rankOre skips own-harvester tiles). Held so
        # the builder re-relays it home once the fling threat clears; _orphanNextTry
        # rate-limits the re-plan so a still-blocked orphan isn't re-searched every turn.
        self._orphanHarvester: Position | None = None
        self._orphanNextTry: int = 0

        # Relay verification (ecoBot.verifyRelay). Every 2nd harvester this builder
        # delivers, it walks that line back to the core to catch a belt destroyed out
        # of vision — the shared ledger never expires a ghost belt, so nothing else
        # re-checks a completed line. _relaysConnected is the per-builder delivery
        # counter (own harvesters only); _verifyHarvester/_verifyLine/_verifyAnchor/
        # _verifyIdx hold the in-progress walk. A break found on the walk is handed to
        # reconnectOrphan, which owns the actual repair.
        self._relaysConnected: int = 0
        self._verifyHarvester: Position | None = None
        self._verifyLine: list[Position] | None = None
        self._verifyAnchor: Position | None = None
        self._verifyIdx: int = 0
        self._verifyStep: int = -1   # +1 walk harvester->core, -1 core->harvester
        self._verifyDeadline: int = 0  # round by which the walk must finish or abandon

        # Orphan walk-confirm (ecoBot._walkConfirmConnected). Before rebuilding an
        # orphan's line, the builder walks any belt leaving it to learn — from ground
        # truth, tile by tile — whether it already reaches home; a line another builder
        # laid may never have entered this unit's ledger, so isNetworkMember can't see
        # it. _followLine is the belt chain discovered so far; _followDeadline bounds
        # the walk; _followFor is the harvester it belongs to, so a follow paused by
        # higher ladder rungs is dropped rather than resumed on a different orphan if
        # the single _orphanHarvester slot was swapped underneath it. None when no
        # confirm is in progress.
        self._followLine: list[Position] | None = None
        self._followDeadline: int = 0
        self._followFor: Position | None = None

        # Guardian role (roles/guardianBot.py). Set True in relays.relayDone once
        # this builder has delivered a harvester end-to-end — the eligibility gate
        # for claiming guardian duty. The patrol cursor is this guardian's place
        # in its leash-waypoint ring. Both are only ever read on builders.
        self._harvesterDelivered = False
        self._guardianPatrolIdx = 0
        # Did the guardian ladder run to completion last turn. Gates the liveness
        # heartbeat, so a guardian that faults or TLEs in the same place every turn
        # stops stamping and is replaced instead of holding the role while doing
        # nothing (guardianBot.runGuardian). True initially so the first turn stamps.
        self._guardianTurnOk = True

        # Enemy core, once this unit (via the shared ledger) has actually seen it
        # (raid.enemyCorePos). Ground truth that overrides a wrong/absent
        # fingerprint; cached because the core never moves.
        self._seenEnemyCore: Position | None = None
        # Last round the (full-local_map) sighting scan ran. The hit above caches
        # only on success, which is the *rare* case — every unit that never sees
        # the enemy core would otherwise rescan a monotonically growing local_map
        # on every enemyCorePos call, several times a turn on a raider. This bounds
        # it to one scan per unit per round. -1 so round 0 still scans.
        self._seenEnemyCoreRound: int = -1

        # Core-line repair (ecoBot.repairCoreBelts). The enemy barrier this builder
        # committed to clearing and the connected tile to re-lay the belt toward —
        # held across the multi-turn grind so the belt goes back the moment the
        # barrier falls, and so the tile isn't re-scanned as a fresh target.
        self._repairTile: Position | None = None
        self._repairFace: Position | None = None

        # Launcher one-off (launcher.py). Set once the first raider plants the
        # core launcher, so it never plants a second after walking out of sight.
        self._placedLauncher = False

        # Raider replenishment clock (respawn.runCoreSpawn). Round the core last
        # spawned a replenishment raider (see respawn.REPLENISH_TI), so the next
        # waits out the interval. None until the first one; only ever set on the core.
        self._lastReplenishRound: int | None = None

        # Gunner idle timer (guns.gunnerLoop). Round this gunner last had a
        # target worth shooting in range — NOT the round it last fired: a gunner
        # that can see a target but can't shoot it (empty ammo pool, cooldown) is
        # on a live position with a broken economy, not a dead one, and must not
        # recycle. It self-destructs after guns.IDLE_SELF_DESTRUCT_ROUNDS with an
        # empty arc. None until its first loop, which seeds the clock at birth so
        # a never-targeted gunner still ages out. Stays None on non-gunner units.
        self._gunnerLastActive: int | None = None

        # Sentinel idle timer (guns.sentinelLoop). Round this sentinel last had a
        # target on its fixed arc — a sentinel can't rotate, so this is keyed on an
        # on-arc target (can_fire_from), not merely a visible one. It self-destructs
        # after guns.SENTINEL_IDLE_SELF_DESTRUCT_ROUNDS with a dead arc. None until
        # its first loop, which seeds the clock at birth so a never-targeted sentinel
        # still ages out. Stays None on non-sentinel units.
        self._sentinelLastActive: int | None = None

        # Patience (patience.py)
        self.holdReason: str | None = None
        self.holdTurns = 0

        # Pathing caches (pathfindingHelpers.costContext). Declared here rather
        # than conjured by the first costContext call, so this block stays the
        # whole truth about a unit's state. It matters which units *don't* get
        # them filled: costContext only runs while walking or relaying, so on
        # the core these stay empty all match — a fact that read as a bug when
        # raid.py trusted _ctxThreat to be populated.
        self._ctxCache: dict | None = None
        self._ctxRound: int = -1
        self._ctxNetwork: set[tuple[int, int]] = set()
        self._ctxThreat: set[tuple[int, int]] = set()

        # Tiles this raider fled from enemy gunner fire, tile -> round marked
        # (roles/raidBot._siege). Only avoiding a gunner's *live* facing means a
        # tile reads safe again the instant the gunner rotates off it, so without
        # this the raider ping-pongs between two tiles and eats the hit on
        # arrival — blacklisting the vacated tile for a few turns makes it orbit
        # instead. Empty on any unit that never sieges.
        self._siegeDodgeBlacklist: dict[tuple[int, int], int] = {}

        # Raider travel waypoint (roles/raidBot.advance). A raider crossing to the
        # enemy core commits to one ~8-tile staging tile and reuses it until reached
        # so walkTo caches the hop instead of replanning a full A* every step — the
        # churning-waypoint replan was the raider's dominant search cost on a large
        # map. Paired with the site it was picked for, so a retarget recomputes.
        # Both None on any unit that never travels as a raider.
        self._raidWaypoint: Position | None = None
        self._raidWaypointSite: tuple[int, int] | None = None

        # Raider press-core back-off (roles/raidBot.pressCore). Round until which a
        # raider whose route to the enemy core just failed stops re-running the
        # ~9ms A*+BFS. Without it, a raider flung outside the enemy core-ring wall
        # (its committed waypoint now walled off) re-grinds the same dead search
        # every turn and TLEs. 0 = not backed off. Only ever set on a raider.
        self._pressCoreBackoffUntil = 0

        # Tiles this raider's siege walk found no route to, tile -> round marked
        # (roles/raidBot._markIfNoRoute) — both the ring tiles it works and the tiles
        # it dodges to. Each has somewhere else to go, so a dead search bars that one
        # tile and the pick moves on, rather than re-grinding the same ~9ms A*+BFS at
        # it every turn (which is a per-turn TLE, i.e. a raider that visibly stops
        # doing anything). Barring is temporary: ground opens and closes as both
        # sides build.
        self._siegeUnreachable: dict[tuple[int, int], int] = {}

        # Repaired-belt give-up state for the raider's siege belt-cut
        # (roles/raidBot._siege). The belt-cut fire loop is otherwise stateless:
        # it re-fires an adjacent enemy belt every turn it can, so an enemy that
        # repairs the belt as fast as we grind it drains our Ti (2/shot) forever
        # for no net damage — a losing trade. _beltGrindLow tracks the lowest HP
        # this raider has driven each belt tile to, and how many consecutive
        # engaged looks it's held there (patience.stallCheck); once that reaches
        # _BELT_STALL_TURNS the belt is being out-repaired, so we bar it in
        # _beltGaveUp (tile -> round barred) and move on. Temporary and per-unit,
        # same as the blacklists above: _beltGaveUp self-heals on
        # _BELT_GIVEUP_MEMORY_TURNS so a belt the enemy stops tending is
        # re-probed; _beltGrindLow is cleared (patience.stallForget) the next
        # time this raider looks at a tile that's no longer a live enemy belt,
        # so a later rebuild is judged fresh rather than against a stale low —
        # best-effort, since a tile it never revisits keeps a stale entry until
        # the next thing that overwrites it. Both empty on any unit that never
        # sieges.
        self._beltGrindLow: dict[tuple[int, int], tuple[int, int]] = {}
        self._beltGaveUp: dict[tuple[int, int], int] = {}

        # Count of dead searches this unit's walkTo has run (bumped in
        # movement.walkTo). Callers snapshot it before a walk and compare after, to
        # ask "did *that* walk fail" — the sticky holdReason can't answer it, and a
        # round stamp can't either once two walks share a turn. Monotonic; only the
        # difference is ever read, so it never needs resetting.
        self.noRouteSeq = 0

        # Memo of the last walk search that came up empty: the goal it was for, the
        # tile we stood on, and the round. walkTo consults it to skip re-running a
        # full A*+BFS every turn for a route that just wasn't there and can't have
        # appeared while we haven't moved — the per-turn cost behind the stuck-unit
        # TLEs (ore pocket, core press). Any of goal/tile/window changing forces a
        # fresh search, so a blocker clearing or the unit shifting re-probes at once.
        self._noRouteGoal = None
        self._noRoutePos: tuple[int, int] | None = None
        self._noRouteRound = -(10 ** 9)

        # Consecutive turns guns.defendCore has stood in range of a core threat that
        # none of our pads bear on. Its own counter and not patience.holdTask,
        # because walkTo clears the shared hold on every arrival — which silently
        # made that patience infinite and kept a builder beside a threat until it
        # died. Reset on any turn defence actually acts or gives up.
        self._defendStuckTurns = 0

        # Instrumentation (telemetry.py) — None unless COUNT_CPU=1.
        self._cpu = CpuMeter() if CPU_TELEMETRY else None

    def run(self, ct: Controller) -> None:
        # An uncaught exception permanently destroys this unit for the rest of
        # the match, so nothing here is allowed to escape.
        try:
            self.hivemind.tick(ct)
            etype = ct.get_entity_type()
            if etype == EntityType.CORE:
                self._runCore(ct)
            elif etype == EntityType.BUILDER_BOT:
                self._runBuilder(ct)
            elif etype == EntityType.SENTINEL:
                self._runSentinel(ct)
            elif etype == EntityType.GUNNER:
                guns.gunnerLoop(self, ct)
            elif etype == EntityType.LAUNCHER:
                launcher.runLauncher(self, ct)
        except Exception:
            if self._cpu is not None:
                self._cpu.fault()
            # Say what died, when tracing is on. The swallow itself is right — an
            # escaping exception destroys this unit for the rest of the match — but
            # swallowing it *silently* cost us a whole class of bug: a unit that
            # throws on turn 1 and every turn after looks exactly like a unit that
            # decided to stand still, and a raider bricked this way sat idle for 89
            # rounds with a full bank before anyone thought to suspect a crash
            # (emplace.enemyTurrets on an enemy launcher). One debug-gated line
            # turns that silence into the answer.
            if debug.ENABLED:
                import traceback
                debug.log("fault", f"id={ct.get_id()} rnd={ct.get_current_round()} "
                                   f"{traceback.format_exc().strip().splitlines()[-1]}")

        # Sampled after the handler rather than in a `finally` — the engine's bot
        # validator rejects `finally` outright — so a faulted turn is measured
        # too; one that threw early would otherwise read as a fast turn.
        # sample() swallows its own errors, so it cannot become the thing that
        # kills the unit.
        if self._cpu is not None:
            self._cpu.sample(ct)

    def _runCore(self, ct: Controller) -> None:
        self.corePos = ct.get_position()
        runCoreSpawn(self, ct)
        # Emplacement selection lives here because the core has the budget for it
        # — it peaks near 1.6ms where builders sit at 9.4ms against a 10ms limit.
        raid.publishTarget(self, ct)
        guns.topUpAmmo(self, ct)

    def _runBuilder(self, ct: Controller) -> None:
        if self.corePos is None:
            ally, _enemy = unpack_cores(ct.read_store(store.PACKED_CORES))
            if ally[0] != CoreState.UNKNOWN:
                self.corePos = Position(ally[1], ally[2])
        if self.corePos is None:
            return

        # Roles are named by index rather than by anything a builder can see, so
        # they survive losing sight of the core and never change mid-match.
        index = self.hivemind.my_index
        raider1 = store.readRole(ct, store.ROLE_RAIDER)
        raider2 = store.readRole(ct, store.ROLE_RAIDER_2)

        # The first raider's one-off: plant the launcher beside the core before it
        # leaves to raid. placeCoreLauncher holds the turn while it's still placing
        # and returns False once the launcher exists (or it gave up).
        if index and index == raider1 and launcher.placeCoreLauncher(self, ct):
            return

        if index and (index in (raider1, raider2) or isReplenishmentRaider(index)):
            runRaidBot(self, ct)
            return

        # Guardian is claimed at runtime (not named at spawn like raiders): the
        # first eco-builder to deliver a harvester writes its index here via
        # guardianBot.claimIfOpen. The index is unique and never reused, so only
        # the actual guardian matches — a dead guardian's stale slot matches
        # nobody and is reclaimed by the next eligible builder.
        guardian, _hb = store.readGuardian(ct)
        if index and index == guardian:
            runGuardian(self, ct)
            return

        runEcoBot(self, ct)

    def _runSentinel(self, ct: Controller) -> None:
        runSentinelBot(self, ct)
