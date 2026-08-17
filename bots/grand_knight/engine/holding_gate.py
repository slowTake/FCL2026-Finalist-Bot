"""Grand_knight — Engine Domain: Stationary Holding Gate.

Shared latch for a unit waiting adjacent to a valid target that's blocked on
action cooldown or insufficient resources: holds position for a bounded number
of turns before giving up, rather than looping the same failed action forever.
"""

from __future__ import annotations

import sys
from typing import Optional
from fcode import Controller, Position

from config import Config
from engine.step_result import StepResult


class HoldingGate:
    """Latches a unit in place while a target action is blocked on cooldown/resources."""

    def __init__(self) -> None:
        self.target: Optional[Position] = None
        self.reason: Optional[str] = None
        self.turns: int = 0

    def hold(self, ct: Controller, target: Position, reason: str, debug_expired: bool = True) -> StepResult:
        """Latch onto target, incrementing turns held; return FAILED once HOLD_MAX_TURNS is exceeded."""
        curr_rnd = ct.get_current_round()
        my_id = ct.get_id()
        if self.target == target:
            self.turns += 1
        else:
            self.target = target
            self.reason = reason
            self.turns = 1

        if self.turns > Config.HOLD_MAX_TURNS:
            if debug_expired:
                print(
                    f"TASK_HOLD_EXPIRED builder_id={my_id} target={target} "
                    f"reason={reason} turns={self.turns} round={curr_rnd}",
                    file=sys.stderr,
                )
            self.target = None
            self.reason = None
            self.turns = 0
            return StepResult.FAILED

        print(
            f"TASK_HOLD builder_id={my_id} target={target} "
            f"reason={reason} turns={self.turns} round={curr_rnd}",
            file=sys.stderr,
        )
        return StepResult.HOLDING

    def clear(self) -> None:
        """Release the latch, e.g. after the held action finally succeeds."""
        self.target = None
        self.reason = None
        self.turns = 0
