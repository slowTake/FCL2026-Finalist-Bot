"""count — dispatch only. Every decision lives in a role file.

Store slots: 0 harvester count (ecoBot). 1-3 free. 4-15 owned by HiveMind
(4 builder registry, 5-12 delta ledger, 13 metronome, 14 map id, 15 packed cores)
— never write those from here.
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

import raid  # noqa: E402
from ecoBot import runEcoBot  # noqa: E402
from gunnerBot import runGunnerBot  # noqa: E402
from hivemind import CoreState, HiveMind, unpack_cores  # noqa: E402
from raidBot import runRaidBot  # noqa: E402
from respawn import runCoreSpawn  # noqa: E402
from sentinelBot import runSentinelBot  # noqa: E402
from telemetry import ENABLED as CPU_TELEMETRY, CpuMeter  # noqa: E402

SLOT_PACKED_CORES = 15


class Player:
    def __init__(self) -> None:
        self.hivemind = HiveMind()
        self.corePos: Position | None = None

        # Relay state (relays.py)
        self.inRelay = False
        self.relayPath: list[Position] | None = None
        self.relayAnchor: Position | None = None
        self.relayCtx = None
        self.relayFrom: Position | None = None
        self.relayAvoid: set[tuple[int, int]] = set()
        # tile -> times this relay has re-laid it, so a destroy/rebuild war with
        # another builder is detectable (see relays.BELT_CONTEST_LIMIT)
        self.relayRebuilds: dict[tuple[int, int], int] = {}

        # Walk state (movement.py)
        self.walkPath: list[Position] | None = None
        self.walkAvoid: set[tuple[int, int]] = set()

        # Economy state (ecoBot.py)
        self.oreTarget: Position | None = None
        self.oreSkip: dict[tuple[int, int], int] = {}

        # Patience (patience.py)
        self.holdReason: str | None = None
        self.holdTurns = 0

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
            elif etype == EntityType.GUNNER:
                self._runGunner(ct)
            elif etype == EntityType.SENTINEL:
                self._runSentinel(ct)
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

    def _runBuilder(self, ct: Controller) -> None:
        if self.corePos is None:
            ally, _enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
            if ally[0] != CoreState.UNKNOWN:
                self.corePos = Position(ally[1], ally[2])
        if self.corePos is None:
            return

        # The raider is named by index rather than by anything it can see, so the
        # role survives losing sight of the core and never changes mid-match.
        index = self.hivemind.my_index
        if index and index == ct.read_store(raid.SLOT_RAIDER_INDEX):
            runRaidBot(self, ct)
            return

        runEcoBot(self, ct)

    def _runGunner(self, ct: Controller) -> None:
        runGunnerBot(self, ct)

    def _runSentinel(self, ct: Controller) -> None:
        runSentinelBot(self, ct)
