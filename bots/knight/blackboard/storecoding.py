"""knight — Blackboard Store Encoding & Bitpacking Central Registry.

Provides deterministic, offset-encoded 32-bit and 16-bit integer bitpacking
for blackboard IPC communication across units with safe sentinel (0 = uninitialized) handling.
"""

from __future__ import annotations

from typing import Optional, Tuple
from fcode import Position


def pack_position(pos: Position) -> int:
    """Pack Position into a 32-bit integer with +1 offset (supports x=0, y=0)."""
    return (((pos.x + 1) & 0xFFFF) << 16) | ((pos.y + 1) & 0xFFFF)


def unpack_position(value: int) -> Optional[Position]:
    """Unpack 32-bit integer into Position. Returns None if value == 0 (uninitialized)."""
    if value == 0:
        return None
    x = ((value >> 16) & 0xFFFF) - 1
    y = (value & 0xFFFF) - 1
    if x < 0 or y < 0:
        return None
    return Position(x, y)


def pack_ore_discovery(pos: Position, round_num: int) -> int:
    """Pack ore position (+1 offset) and round number (mod 256) into 32 bits:
    Bits 20-31: X + 1 (12 bits, max 4095)
    Bits 8-19:  Y + 1 (12 bits, max 4095)
    Bits 0-7:   Round Mod 256 (8 bits)
    """
    return (
        (((pos.x + 1) & 0xFFF) << 20)
        | (((pos.y + 1) & 0xFFF) << 8)
        | (round_num & 0xFF)
    )


def unpack_ore_discovery(value: int) -> tuple[Optional[Position], int]:
    """Unpack 32-bit integer into (Position | None, round_mod)."""
    if value == 0:
        return None, 0

    x = ((value >> 20) & 0xFFF) - 1
    y = ((value >> 8) & 0xFFF) - 1
    round_mod = value & 0xFF

    if x < 0 or y < 0:
        return None, 0

    return Position(x, y), round_mod


def pack_role_assignment(primary_id: int, secondary_id: int) -> int:
    """Pack primary (Guardian) and secondary (Reserve) builder IDs into 32-bit integer."""
    return ((primary_id & 0xFFFF) << 16) | (secondary_id & 0xFFFF)


def unpack_role_assignment(value: int) -> tuple[int, int]:
    """Unpack a 32-bit integer into (primary_id, secondary_id)."""
    primary = (value >> 16) & 0xFFFF
    secondary = value & 0xFFFF
    return primary, secondary


def pack_builder_intent(x: int, y: int, action: int, priority: int) -> int:
    """Pack Core-authored intent into a 32-bit integer:
    Bits 0-11:  Target X (0-4095)
    Bits 12-23: Target Y (0-4095)
    Bits 24-27: Action Type (0-15)
    Bits 28-31: Priority/Urgency (0-15)
    """
    return (x & 0xFFF) | ((y & 0xFFF) << 12) | ((action & 0xF) << 24) | ((priority & 0xF) << 28)


def unpack_builder_intent(value: int) -> tuple[int, int, int, int]:
    """Unpack a 32-bit integer into (x, y, action, priority)."""
    x = value & 0xFFF
    y = (value >> 12) & 0xFFF
    action = (value >> 24) & 0xF
    priority = (value >> 28) & 0xF
    return x, y, action, priority


def pack_ore_status(pos: Position, status: int) -> int:
    """Pack invalidation status and coordinate (+1 offset) into 32 bits:
    Bits 20-31: X + 1 (12 bits)
    Bits 8-19:  Y + 1 (12 bits)
    Bits 0-7:   Status (8 bits)
    """
    return (
        (((pos.x + 1) & 0xFFF) << 20)
        | (((pos.y + 1) & 0xFFF) << 8)
        | (status & 0xFF)
    )


def unpack_ore_status(value: int) -> tuple[Optional[Position], int]:
    """Unpack 32-bit integer into (Position | None, status)."""
    if value == 0:
        return None, 0

    x = ((value >> 20) & 0xFFF) - 1
    y = ((value >> 8) & 0xFFF) - 1
    status = value & 0xFF

    if x < 0 or y < 0:
        return None, 0

    return Position(x, y), status


# Backward-compatible aliases
pack_pos = pack_position
unpack_pos = unpack_position
pack_intent = pack_builder_intent
unpack_intent = unpack_builder_intent
