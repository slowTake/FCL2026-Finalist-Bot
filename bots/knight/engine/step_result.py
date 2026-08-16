"""knight — Priority Pipeline Step Result Enums.

Defines deterministic execution control outcomes for priority handlers in unit pipelines,
distinguishing between active execution (ACTED), intentional stationary waiting (HOLDING),
and fallthrough condition failure (FAILED).
"""

from __future__ import annotations

from enum import Enum, auto


class StepResult(Enum):
    """Pipeline step execution outcome controlling handler flow and turn consumption."""

    ACTED = auto()    # Major action or movement performed; consumes the unit's tick
    HOLDING = auto()  # Intentional stationary hold/wait; consumes tick without movement
    FAILED = auto()   # Handler precondition failed; allows subsequent priority handlers to run

    @property
    def consumed(self) -> bool:
        """True if this result consumes the unit's turn (stopping pipeline fallthrough)."""
        return self in (StepResult.ACTED, StepResult.HOLDING)

    @classmethod
    def from_bool(cls, success: bool) -> StepResult:
        """Wrap a legacy boolean return value for backwards compatibility."""
        return cls.ACTED if success else cls.FAILED
