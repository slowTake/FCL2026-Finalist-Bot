"""Per-turn CPU sampling.

The engine gives each unit 10ms and the gauntlet enforces it (--tle 10), but a
pass only says "no unit blew the limit" — it never says how close we were. That
gap matters when tuning anything the search budget depends on: a weight change
that costs 2ms on one builder is invisible in a green sweep right up until it
isn't. This reports the headroom so those calls can be made on measurement.

Off unless COUNT_CPU=1 is in the environment, so a submission zip pays one
boolean check per turn and nothing else.
"""

from __future__ import annotations

import os
import sys

ENABLED = os.environ.get("COUNT_CPU") == "1"

BUDGET_US = 10_000    # the engine's per-unit limit; exceeding it skips the turn
WARN_US = 7_000       # 70% of budget — a turn this slow has no room for growth
REPORT_EVERY = 100    # rounds between summary lines, per unit


class CpuMeter:
    """One meter per unit.

    Player is instantiated once per unit, so plain instance state is already
    per-unit. This must never become a module global: units share a process,
    and a shared meter would blend a core's turns with five builders'.
    """

    __slots__ = ("label", "turns", "peak", "peakRound", "warn", "over",
                 "faults", "nextReport")

    def __init__(self) -> None:
        self.label = "?"
        self.turns = 0
        self.peak = 0
        self.peakRound = 0
        self.warn = 0
        self.over = 0
        self.faults = 0
        self.nextReport = REPORT_EVERY

    def fault(self) -> None:
        """Record a turn that died in the role code.

        Counted alongside the timings because a turn that threw after 1ms
        otherwise reads as a fast turn — without this the CPU numbers flatter
        themselves exactly when something is broken.
        """
        self.faults += 1

    def sample(self, ct) -> None:
        """Read the turn's elapsed time. Call from a finally, so a faulted turn
        is still measured."""
        # Nothing in here may escape: an uncaught exception costs the unit the
        # rest of the match, and telemetry is never worth that.
        try:
            us = ct.get_cpu_time_elapsed()
            rnd = ct.get_current_round()

            self.turns += 1
            if us > self.peak:
                self.peak = us
                self.peakRound = rnd
            if us >= BUDGET_US:
                self.over += 1
            elif us >= WARN_US:
                self.warn += 1

            if self.label == "?":
                self.label = f"{ct.get_entity_type().value}#{ct.get_id()}"

            if rnd >= self.nextReport:
                self.nextReport = rnd + REPORT_EVERY
                self._emit(rnd)
        except Exception:
            return

    def _emit(self, rnd: int) -> None:
        sys.stderr.write(
            f"[cpu] {self.label} r{rnd} turns={self.turns} "
            f"peak={self.peak}us@r{self.peakRound} "
            f"warn>={WARN_US}:{self.warn} over>={BUDGET_US}:{self.over} "
            f"faults={self.faults}\n"
        )
