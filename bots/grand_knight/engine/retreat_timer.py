"""Grand_knight — Engine Domain: Sticky Retreat Commitment Timer.

Once a threat triggers a retreat, holds the commitment for a fixed duration
regardless of whether the trigger condition still holds this tick. This is a
different hysteresis shape from HoldingGate (engine/holding_gate.py): that one
retries the same blocked action until it works or patience runs out; this one
commits to a behavior for N turns so a threat that blinks in and out of
detection can't repeatedly re-trigger a fresh full-duration retreat every tick
it's seen.
"""

from __future__ import annotations

import sys
from typing import Optional
from fcode import Controller, Position


class RetreatTimer:
    """Sticky commitment latch for threat-triggered retreat."""

    def __init__(self) -> None:
        self.turns: int = 0
        self.target: Optional[Position] = None
        self.last_threat_round: int = -10000

    def trigger(self, ct: Controller, target: Position, reason: str, sticky_turns: int) -> None:
        """Latch onto target for sticky_turns, refreshing the timer even if already active."""
        if self.turns == 0:
            print(
                f"RETREAT_LATCH builder_id={ct.get_id()} timer={sticky_turns} "
                f"reason={reason} round={ct.get_current_round()}",
                file=sys.stderr,
            )
        self.turns = sticky_turns
        self.target = target
        self.last_threat_round = ct.get_current_round()

    def tick(self, ct: Controller, hp: int, dist_sq_to_core: int, clear_hp: int, memory_turns: int) -> bool:
        """Decrement the timer and apply early-clear conditions.

        Returns True if a retreat commitment was active this tick (whether or
        not it also clears during this same call) -- the caller still treats
        this tick as handled by retreat, resuming normal work only next turn.
        """
        if self.turns <= 0:
            return False
        self.turns -= 1
        curr_rnd = ct.get_current_round()

        if self.turns == 0 or dist_sq_to_core <= 4:
            if hp >= clear_hp or (curr_rnd - self.last_threat_round >= memory_turns) or dist_sq_to_core <= 4:
                if self.target is not None:
                    print(f"RETREAT_CLEAR builder_id={ct.get_id()} round={curr_rnd}", file=sys.stderr)
                self.target = None
                self.turns = 0

        return True

    def clear(self) -> None:
        """Release the commitment immediately, e.g. when already safe at Core."""
        self.turns = 0
        self.target = None
