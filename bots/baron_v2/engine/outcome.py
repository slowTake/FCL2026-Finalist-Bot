"""knight — Safe Controller Outcomes & Reason Enums.

Defines deterministic outcome receipts for all unit actions and engine interactions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


class Reason(Enum):
    """Enumeration of possible execution outcomes for unit actions."""

    OK = auto()
    ALREADY_ACTED = auto()
    COOLDOWN = auto()
    WRONG_UNIT_TYPE = auto()
    CPU_BUDGET = auto()
    EXCEPTION = auto()
    OUT_OF_BOUNDS = auto()
    NOT_ADJACENT = auto()
    BLOCKED = auto()
    INVALID_DIRECTION = auto()
    NO_RESOURCES = auto()
    ENGINE_REJECTED = auto()


@dataclass(frozen=True)
class Outcome:
    """Immutable execution outcome receipt returned by SafeController methods."""

    success: bool
    reason: Reason
    message: str = ""
    value: Any = None
