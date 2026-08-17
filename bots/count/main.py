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

        # Walk state (movement.py)
        self.walkPath: list[Position] | None = None
        # What walkPath was planned toward. The cache is only reused while this
        # matches, so a retarget replans instead of finishing the old journey.
        self.walkGoal = None
        self.walkAvoid: set[tuple[int, int]] = set()

        # Economy state (ecoBot.py)
        self.oreTarget: Position | None = None
        self.oreSkip: dict[tuple[int, int], int] = {}

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
