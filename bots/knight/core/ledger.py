"""knight — Core Domain: Shared Blackboard & Store Slot IPC Ledger.

Synchronizes local unit vision with Store Slots 0-15. Provides packed
intent readers/writers for strategic coordination without 10ms CPU bloat.
"""

from __future__ import annotations

from typing import Any, Optional
from fcode import Controller, Position

from config import Config
from utils.geometry import (
    pack_builder_intent,
    pack_pos,
    unpack_builder_intent,
    unpack_pos,
    unpack_role_assignment,
)


class Ledger:
    """Blackboard interface for unit-to-unit communication via Store slots."""

    def __init__(self) -> None:
        self.core_pos: Optional[Position] = None
        self.global_flags: int = 0
        self.core_threat_active: bool = False
        self.builder_intent: Optional[tuple[Position, int, int]] = None  # (Position, action_type, priority)
        self.reserved_builders: tuple[int, ...] = Config.LEASH_RESERVED_BUILDER_IDS

    def sync_vision(self, ct: Controller) -> None:
        """Synchronize local unit vision state with global Store blackboard.
        
        Args:
            ct: Controller instance for the active unit.
        """
        # Read core position from Slot 0 (SLOT_CORE_POS)
        raw_core_pos = ct.read_store(Config.SLOT_CORE_POS)
        self.core_pos = unpack_pos(raw_core_pos)

        # Read global flags from Slot 11 (SLOT_GLOBAL_FLAGS)
        self.global_flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        self.core_threat_active = bool(self.global_flags & 0x02)

        # Read builder intent from Slot 14 (SLOT_BUILDER_INTENT)
        raw_intent = ct.read_store(Config.SLOT_BUILDER_INTENT)
        if raw_intent != 0:
            x, y, action, priority = unpack_builder_intent(raw_intent)
            self.builder_intent = (Position(x, y), action, priority)
        else:
            self.builder_intent = None

        # Read dynamic role assignment from Slot 15 (SLOT_ROLE_ASSIGNMENT)
        raw_role = ct.read_store(Config.SLOT_ROLE_ASSIGNMENT)
        if raw_role != 0:
            primary, secondary = unpack_role_assignment(raw_role)
            if primary != 0:
                self.reserved_builders = (primary, secondary)

    def broadcast_builder_intent(self, ct: Controller, target: Position, action: int, priority: int) -> None:
        """Broadcast 32-bit packed Builder Intent to Store Slot 14 (Core only)."""
        val = pack_builder_intent(target.x, target.y, action, priority)
        ct.write_store(Config.SLOT_BUILDER_INTENT, val)

    def report_threat_alert(self, ct: Controller, threat_pos: Position) -> None:
        """Write threat alert position to Store Slot 6 (SLOT_ALERTS)."""
        ct.write_store(Config.SLOT_ALERTS, pack_pos(threat_pos))

    def read_threat_alert(self, ct: Controller) -> Optional[Position]:
        """Read threat alert position from Store Slot 6 (SLOT_ALERTS)."""
        val = ct.read_store(Config.SLOT_ALERTS)
        return unpack_pos(val)
