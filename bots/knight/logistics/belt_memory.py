"""knight — Logistics Domain: Local Conveyor Relay Memory.

Tracks completed conveyor relay routes locally on each builder bot to enable
autonomous verification passes, gap inspections, and repair loops without shared memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional
from fcode import Position


class RelayStatus(Enum):
    ACTIVE = 1
    BROKEN = 2
    RETIRED = 3


@dataclass
class RelayRoute:
    relay_id: int
    harvester_pos: Position
    path: tuple[Position, ...]
    built_round: int
    last_verified_round: int
    status: RelayStatus
    broken_index: int | None
    repair_attempts: int


class BeltMemory:
    """Per-unit memory for tracking paved conveyor relays and scheduling maintenance patrols."""

    def __init__(self) -> None:
        self._relays: dict[int, RelayRoute] = {}
        self._next_relay_id: int = 1

    def add_relay(
        self,
        harvester_pos: Position,
        path: tuple[Position, ...],
        built_round: int,
    ) -> RelayRoute:
        """Record a newly paved conveyor relay route."""
        relay = RelayRoute(
            relay_id=self._next_relay_id,
            harvester_pos=harvester_pos,
            path=tuple(path),
            built_round=built_round,
            last_verified_round=built_round,
            status=RelayStatus.ACTIVE,
            broken_index=None,
            repair_attempts=0,
        )
        self._relays[self._next_relay_id] = relay
        self._next_relay_id += 1
        return relay

    def get_relay(self, relay_id: int) -> Optional[RelayRoute]:
        """Retrieve a specific relay by ID."""
        return self._relays.get(relay_id)

    def active_relays(self) -> list[RelayRoute]:
        """Return all non-retired relays."""
        return [
            r for r in self._relays.values()
            if r.status in (RelayStatus.ACTIVE, RelayStatus.BROKEN)
        ]

    def patrol_due(self, current_round: int, interval: int) -> list[RelayRoute]:
        """Return active/broken relays that are due for inspection, ordered by oldest verification."""
        due: list[RelayRoute] = []
        for r in self._relays.values():
            if r.status in (RelayStatus.ACTIVE, RelayStatus.BROKEN):
                if r.status == RelayStatus.BROKEN or (current_round - r.last_verified_round >= interval):
                    due.append(r)
        # Sort by oldest last_verified_round first
        due.sort(key=lambda r: (r.status != RelayStatus.BROKEN, r.last_verified_round))
        return due

    def retire_relay(self, relay_id: int) -> None:
        """Mark a depleted or defunct harvester relay as retired."""
        relay = self._relays.get(relay_id)
        if relay is not None:
            relay.status = RelayStatus.RETIRED
