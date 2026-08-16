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
_HERE = os.path.dirname(os.path.abspath(__file__))
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
from hivemind import CoreState, HiveMind, unpack_cores  # noqa: E402
from raidBot import runRaidBot  # noqa: E402
from respawn import runCoreSpawn  # noqa: E402
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

        # Enemy core, once this unit (via the shared ledger) has actually seen it
        # (raid.enemyCorePos). Ground truth that overrides a wrong/absent
        # fingerprint; cached because the core never moves.
        self._seenEnemyCore: Position | None = None

        # Core-line repair (ecoBot.repairCoreBelts). The enemy barrier this builder
        # committed to clearing and the connected tile to re-lay the belt toward —
        # held across the multi-turn grind so the belt goes back the moment the
        # barrier falls, and so the tile isn't re-scanned as a fresh target.
        self._repairTile: Position | None = None
        self._repairFace: Position | None = None

        # Launcher one-off (launcher.py). Set once the first raider plants the
        # core launcher, so it never plants a second after walking out of sight.
        self._placedLauncher = False

        # Gunner idle timer (guns.gunnerLoop). Round this gunner last fired; it
        # recycles itself after guns.IDLE_SELF_DESTRUCT_ROUNDS idle rounds. None
        # until its first loop, which seeds the clock at birth so a never-fired
        # gunner still ages out. Stays None on every non-gunner unit.
        self._gunnerLastFired: int | None = None

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

        if index and index in (raider1, raider2):
            runRaidBot(self, ct)
            return

        runEcoBot(self, ct)

    def _runSentinel(self, ct: Controller) -> None:
        runSentinelBot(self, ct)
