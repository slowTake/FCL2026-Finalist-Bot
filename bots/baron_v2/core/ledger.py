"""baron_v2 — Global Ore Claim Table & Shared Memory Ledger.

Provides decentralized ore claim tracking and strategic blackboard synchronization
over the 16-slot 32-bit fcode controller storage.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence
from fcode import Controller, EntityType, Position

from config import Config
from engine.safe_controller import SafeController
from utils.geometry import is_adjacent, is_in_vision
from utils.storecoding import (
    pack_builder_intent,
    pack_ore_discovery,
    pack_position,
    unpack_builder_intent,
    unpack_ore_discovery,
    unpack_position,
)


class OreStatus:
    UNCLAIMED: int = 0
    CLAIMED: int = 1
    OCCUPIED: int = 2
    DEPLETED: int = 3


def pack_ore_claim(pos: Position, status: int, builder_id: int, round_mod: int) -> int:
    """Bitpack ore claim state into a 32-bit unsigned integer.

    Layout:
      Bits 0..5   (6 bits):  X coordinate (0..63)
      Bits 6..11  (6 bits):  Y coordinate (0..63)
      Bits 12..13 (2 bits):  OreStatus (0..3)
      Bits 14..23 (10 bits): Claiming Builder Unit ID (0..1023)
      Bits 24..31 (8 bits):  Round stamp modulo 256 (0..255)
    """
    x_enc = (pos.x + 1) & 0x3F
    y_enc = (pos.y + 1) & 0x3F
    st_enc = status & 0x3
    bid_enc = builder_id & 0x3FF
    rnd_enc = round_mod & 0xFF
    return (rnd_enc << 24) | (bid_enc << 14) | (st_enc << 12) | (y_enc << 6) | x_enc


def unpack_ore_claim(val: int) -> Optional[tuple[Position, int, int, int]]:
    """Unpack 32-bit ore claim state. Returns (pos, status, builder_id, round_mod) or None."""
    if val == 0:
        return None
    x_enc = val & 0x3F
    y_enc = (val >> 6) & 0x3F
    status = (val >> 12) & 0x3
    builder_id = (val >> 14) & 0x3FF
    round_mod = (val >> 24) & 0xFF
    if x_enc == 0 or y_enc == 0:
        return None
    return Position(x_enc - 1, y_enc - 1), status, builder_id, round_mod


class OreRegistry:
    """Manages multi-builder ore reservations and occupied node retirement."""

    def __init__(self, safe: Optional[SafeController] = None) -> None:
        self.safe = safe
        # Local mirror: pos -> (status, claiming_builder_id, last_updated_round)
        self.claims: dict[Position, tuple[int, int, int]] = {}

    def sync_from_store(self, ct: Controller) -> None:
        """Read and reconcile ore claims from store slot SLOT_ORE_CLAIMS."""
        curr_rnd = ct.get_current_round()
        raw_val = ct.read_store(Config.SLOT_ORE_CLAIMS)
        claim_data = unpack_ore_claim(raw_val)
        if claim_data is not None:
            pos, status, bid, rmod = claim_data
            # Verify claim freshness (TTL: 60 turns)
            if (curr_rnd % 256 - rmod) % 256 <= 60:
                self.claims[pos] = (status, bid, curr_rnd)

    def sync_vision(self, ct: Controller) -> None:
        """Synchronize shared vision and blackboard state."""
        self.sync_from_store(ct)

    def register_claim(self, ct: Controller, pos: Position, builder_id: int) -> bool:
        """Attempt to claim an ore node for expansion."""
        curr_rnd = ct.get_current_round()
        if pos in self.claims:
            status, bid, last_rnd = self.claims[pos]
            if status in (OreStatus.OCCUPIED, OreStatus.DEPLETED):
                return False
            if status == OreStatus.CLAIMED and bid != builder_id and (curr_rnd - last_rnd) < 40:
                return False

        self.claims[pos] = (OreStatus.CLAIMED, builder_id, curr_rnd)
        val = pack_ore_claim(pos, OreStatus.CLAIMED, builder_id, curr_rnd % 256)
        ct.write_store(Config.SLOT_ORE_CLAIMS, val)
        return True

    def mark_occupied(self, ct: Controller, pos: Position, builder_id: int) -> None:
        """Mark an ore node as successfully harvested."""
        curr_rnd = ct.get_current_round()
        self.claims[pos] = (OreStatus.OCCUPIED, builder_id, curr_rnd)
        val = pack_ore_claim(pos, OreStatus.OCCUPIED, builder_id, curr_rnd % 256)
        ct.write_store(Config.SLOT_ORE_CLAIMS, val)

    def mark_depleted(self, ct: Controller, pos: Position) -> None:
        """Retire an impassable or occupied ore node across all units."""
        curr_rnd = ct.get_current_round()
        self.claims[pos] = (OreStatus.DEPLETED, 0, curr_rnd)
        val = pack_ore_claim(pos, OreStatus.DEPLETED, 0, curr_rnd % 256)
        ct.write_store(Config.SLOT_ORE_CLAIMS, val)

    def is_available(self, ct: Controller, pos: Position, my_id: int) -> bool:
        """Check if an ore coordinate is unmined and unclaimed by other friendly builders."""
        if pos in self.claims:
            status, bid, last_rnd = self.claims[pos]
            if status in (OreStatus.OCCUPIED, OreStatus.DEPLETED):
                return False
            if status == OreStatus.CLAIMED and bid != my_id:
                if (ct.get_current_round() - last_rnd) < 40:
                    return False
        return True


# Backward-compatible alias
Ledger = OreRegistry
