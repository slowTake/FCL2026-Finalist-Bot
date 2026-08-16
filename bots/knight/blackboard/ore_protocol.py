"""knight — Blackboard Domain: Ore Lifecycle Protocol & Invalidation Registry.

Manages multi-tier ore validation, local builder blacklisting, shared slot invalidation,
and Core intent leasing to eliminate stale re-commitment loops.
"""

from __future__ import annotations

import sys
from typing import Dict, Optional, Tuple
from fcode import Controller, Environment, Position

from blackboard.storecoding import (
    pack_ore_status,
    unpack_ore_discovery,
    unpack_ore_status,
)
from config import Config
from utils.geometry import is_in_bounds, is_in_vision, is_inside_core_area


def pos_key(pos: Position) -> int:
    """Pack coordinate into 32-bit integer key for hash indexing."""
    return (pos.x << 16) | (pos.y & 0xFFFF)


def is_plausible_ore_target(ct: Controller, pos: Position, core_footprint: list[Position]) -> bool:
    """True if pos is in bounds, not Core footprint, and not known invalid (allowed as soft target if not visible)."""
    if not is_in_bounds(ct, pos):
        return False
    if is_inside_core_area(pos, core_footprint, margin=0):
        return False
    my_pos = ct.get_position()
    if is_in_vision(ct, my_pos, pos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
        try:
            if ct.get_tile_building_id(pos) is not None:
                return False
            if ct.get_tile_env(pos) != Environment.ORE_TITANIUM:
                return False
        except Exception:
            return False
    return True


def is_confirmed_ore_target(ct: Controller, pos: Position, core_footprint: list[Position]) -> bool:
    """True if pos is in bounds, not Core footprint, in vision, has no building, and is Environment.ORE_TITANIUM."""
    if not is_in_bounds(ct, pos):
        return False
    if is_inside_core_area(pos, core_footprint, margin=0):
        return False
    my_pos = ct.get_position()
    if not is_in_vision(ct, my_pos, pos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
        return False
    try:
        if ct.get_tile_building_id(pos) is not None:
            return False
        if ct.get_tile_env(pos) != Environment.ORE_TITANIUM:
            return False
    except Exception:
        return False
    return True


def get_invalid_ore_reason(ct: Controller, pos: Position, core_footprint: list[Position]) -> Optional[str]:
    """Return explicit reason string for invalid ore target for telemetry."""
    if not is_in_bounds(ct, pos):
        return "OUT_OF_BOUNDS"
    if is_inside_core_area(pos, core_footprint, margin=0):
        return "CORE_FOOTPRINT"
    my_pos = ct.get_position()
    if is_in_vision(ct, my_pos, pos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
        try:
            if ct.get_tile_building_id(pos) is not None:
                return "BUILDING_OCCUPIED"
            if ct.get_tile_env(pos) != Environment.ORE_TITANIUM:
                return "NOT_ORE"
        except Exception:
            pass
    return None


class OreLifecycle:
    """Manages local ore blacklisting, shared registry, and shared invalidation reporting."""

    known_friendly_ores: set[Position] = set()
    mined_friendly_ores: set[Position] = set()
    active_claims: Dict[Position, tuple[int, int]] = {}

    @classmethod
    def record_discovered_ore(cls, pos: Position) -> None:
        """Register a discovered unmined ore deposit in friendly territory."""
        cls.known_friendly_ores.add(pos)

    @classmethod
    def record_harvester_built(cls, pos: Position) -> None:
        """Mark an ore deposit as having an active harvester."""
        cls.mined_friendly_ores.add(pos)
        if pos in cls.active_claims:
            del cls.active_claims[pos]

    @classmethod
    def claim_ore(cls, pos: Position, bot_id: int, curr_rnd: int) -> None:
        """Assign exclusive commitment lease for an ore coordinate."""
        cls.active_claims[pos] = (bot_id, curr_rnd)

    @classmethod
    def release_claim(cls, pos: Position, bot_id: int) -> None:
        """Release claim when builder aborts or is reassigned."""
        if pos in cls.active_claims and cls.active_claims[pos][0] == bot_id:
            del cls.active_claims[pos]

    @classmethod
    def is_claimed_by_other(cls, pos: Position, my_id: int, curr_rnd: int) -> bool:
        """True if pos is currently claimed by another active builder."""
        if pos in cls.active_claims:
            owner, claim_rnd = cls.active_claims[pos]
            if owner != my_id:
                if curr_rnd - claim_rnd <= 50:
                    return True
                del cls.active_claims[pos]
        return False

    @classmethod
    def get_unmined_candidates(cls, my_id: int = 0, curr_rnd: int = 0) -> list[Position]:
        """Return all discovered ore deposits that currently have no harvester and no conflicting claim."""
        unmined = cls.known_friendly_ores - cls.mined_friendly_ores
        return [p for p in unmined if not cls.is_claimed_by_other(p, my_id, curr_rnd)]

    def __init__(self) -> None:
        self.blacklist: Dict[int, int] = {}  # pos_key -> expiry_round

    def blacklist_ore(self, ct: Controller, pos: Position, reason: str) -> None:
        """Add an invalid/occupied ore coordinate to local blacklist."""
        curr_rnd = ct.get_current_round()
        if reason == "BUILDING_OCCUPIED":
            ttl = Config.ORE_BLACKLIST_OCCUPIED_TTL
        elif reason == "NOT_ORE":
            ttl = Config.ORE_BLACKLIST_NOT_ORE_TTL
        elif reason in ("CORE_FOOTPRINT", "OUT_OF_BOUNDS"):
            ttl = Config.ORE_BLACKLIST_CORE_TTL
        elif reason == "UNREACHABLE":
            ttl = Config.ORE_BLACKLIST_UNREACHABLE_TTL
        else:
            ttl = 100

        expiry = curr_rnd + ttl
        k = pos_key(pos)
        self.blacklist[k] = expiry
        if Config.DEBUG_ECONOMY:
            print(
                f"ORE_BLACKLIST builder_id={ct.get_id()} ore={pos} reason={reason} expiry={expiry}",
                file=sys.stderr,
            )

    def is_blacklisted(self, ct: Controller, pos: Position) -> bool:
        """True if pos is currently blacklisted and not yet expired."""
        k = pos_key(pos)
        if k in self.blacklist:
            if ct.get_current_round() < self.blacklist[k]:
                return True
            del self.blacklist[k]
        return False

    def report_invalid_ore(self, ct: Controller, pos: Position, reason: str) -> None:
        """Publish shared invalidation to SLOT_ORE_STATUS and clear matching discovery in slot 9."""
        if reason == "BUILDING_OCCUPIED":
            status = Config.ORE_STATUS_OCCUPIED
        elif reason == "NOT_ORE":
            status = Config.ORE_STATUS_NOT_ORE
        elif reason in ("CORE_FOOTPRINT", "OUT_OF_BOUNDS"):
            status = Config.ORE_STATUS_CORE_FOOTPRINT
        elif reason == "UNREACHABLE":
            status = Config.ORE_STATUS_UNREACHABLE
        else:
            status = Config.ORE_STATUS_NOT_ORE

        # Write invalidation status into slot 3
        ct.write_store(Config.SLOT_ORE_STATUS, pack_ore_status(pos, status))

        # Clear matching ore discovery if present in slot 9
        raw_disc = ct.read_store(Config.SLOT_ORE_DISCOVERY)
        disc_pos, _ = unpack_ore_discovery(raw_disc)
        if disc_pos is not None and disc_pos == pos:
            ct.write_store(Config.SLOT_ORE_DISCOVERY, 0)

        self.blacklist_ore(ct, pos, reason)

    def is_valid_candidate(self, ct: Controller, pos: Position, core_footprint: list[Position]) -> bool:
        """Validate an ore candidate, combining blacklisting, vision confirmation, and plausible checks."""
        my_pos = ct.get_position()
        if is_in_vision(ct, my_pos, pos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
            if is_confirmed_ore_target(ct, pos, core_footprint):
                k = pos_key(pos)
                if k in self.blacklist:
                    del self.blacklist[k]
                return True
            return False

        if self.is_blacklisted(ct, pos):
            return False

        return is_plausible_ore_target(ct, pos, core_footprint)
