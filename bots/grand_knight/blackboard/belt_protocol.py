"""Grand_knight — Blackboard Belt Alert IPC Protocol.

Handles packing, unpacking, freshness validation, and store writes for broken conveyor alerts.
"""

from __future__ import annotations

from typing import Optional, Tuple
from fcode import Controller, Position

from config import Config


def pack_belt_alert(pos: Position, round_num: int) -> int:
    """Pack broken belt tile position (+1 offset) and round number (mod 256) into 32 bits:
    Bits 20-31: X + 1 (12 bits)
    Bits 8-19:  Y + 1 (12 bits)
    Bits 0-7:   Round Mod 256 (8 bits)
    """
    return (
        (((pos.x + 1) & 0xFFF) << 20)
        | (((pos.y + 1) & 0xFFF) << 8)
        | (round_num & 0xFF)
    )


def unpack_belt_alert(value: int) -> tuple[Optional[Position], int]:
    """Unpack 32-bit integer into (Position | None, round_mod)."""
    if value == 0:
        return None, 0

    x = ((value >> 20) & 0xFFF) - 1
    y = ((value >> 8) & 0xFFF) - 1
    round_mod = value & 0xFF

    if x < 0 or y < 0:
        return None, 0

    return Position(x, y), round_mod


def belt_alert_is_fresh(ct: Controller, value: int) -> bool:
    """True if value is a valid packed alert and was recorded within BELT_ALERT_TTL_ROUNDS."""
    if value == 0:
        return False
    pos, round_mod = unpack_belt_alert(value)
    if pos is None:
        return False
    curr_round = ct.get_current_round()
    age = (curr_round - round_mod) % 256
    return age <= Config.BELT_ALERT_TTL_ROUNDS


def write_belt_alert(ct: Controller, pos: Position, round_num: int) -> None:
    """Write broken belt alert to SLOT_BELT_ALERT."""
    packed = pack_belt_alert(pos, round_num)
    ct.write_store(Config.SLOT_BELT_ALERT, packed)


def clear_belt_alert_if_matches(ct: Controller, pos: Position) -> None:
    """Clear SLOT_BELT_ALERT if the current alert matches pos."""
    raw = ct.read_store(Config.SLOT_BELT_ALERT)
    if raw == 0:
        return
    alert_pos, _ = unpack_belt_alert(raw)
    if alert_pos == pos:
        ct.write_store(Config.SLOT_BELT_ALERT, 0)
