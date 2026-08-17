"""Grand_knight — Blackboard Domain: Ore Lifecycle Protocol & Invalidation Registry.

Manages multi-tier ore validation, local builder blacklisting, shared slot invalidation,
and Core intent leasing to eliminate stale re-commitment loops.
"""

from __future__ import annotations

import sys
from typing import Dict, Optional
from fcode import Controller, EntityType, Environment, Position

from blackboard.storecoding import pack_ore_status, unpack_ore_discovery
from config import Config
from utils.geometry import is_in_bounds, is_in_friendly_half, is_in_vision, is_inside_core_area


def pos_key(pos: Position) -> int:
    """Pack coordinate into 32-bit integer key for hash indexing."""
    return (pos.x << 16) | (pos.y & 0xFFFF)


# -------------------------------------------------------------
# In-Transit Harvester Claim Ledger (SLOT_ORE_CLAIM_LEDGER)
# -------------------------------------------------------------
# OreLifecycle's active_claims/known_friendly_ores/mined_friendly_ores above are
# plain Python class attributes -- each Builder Bot runs in its own subinterpreter
# with no shared memory (see docs/fcode_api_reference.md), so they only give a
# unit self-consistency against its own past decisions, never real cross-unit
# dogpile prevention. This ledger is the genuine cross-unit channel: three (x, y)
# claims packed into the one otherwise-dead store slot (SLOT_ENEMY_CORE was
# reserved but never written), 10 bits each ((x+1) << 5 | (y+1), 0 = empty slot,
# +1 offset so a genuine claim at (0, 0) can't collide with the empty sentinel).
# Best-effort, not a hard lock: callers proceed with their commitment even when
# the ledger is full or a race lets two units claim the same tile in the same
# round (the T+1 store write delay means a claim isn't visible to other units
# until the following round) -- this only needs to cut the common case, not
# guarantee exclusivity.

_CLAIM_BITS = 10
_CLAIM_MASK = 0x3FF
_CLAIM_SLOTS = 3


def _pack_claim(pos: Optional[Position]) -> int:
    if pos is None:
        return 0
    return ((pos.x + 1) << 5) | (pos.y + 1)


def _unpack_claim(val: int) -> Optional[Position]:
    if val == 0:
        return None
    return Position((val >> 5) - 1, (val & 0x1F) - 1)


def unpack_claim_ledger(raw: int) -> list[Optional[Position]]:
    """Decode the packed ledger slot into its 3 claim sub-slots (None = empty)."""
    return [_unpack_claim((raw >> (_CLAIM_BITS * i)) & _CLAIM_MASK) for i in range(_CLAIM_SLOTS)]


def pack_claim_ledger(claims: list[Optional[Position]]) -> int:
    """Encode 3 claim sub-slots back into a single store value."""
    raw = 0
    for i, pos in enumerate(claims):
        raw |= _pack_claim(pos) << (_CLAIM_BITS * i)
    return raw


def is_harvester_claimed(ct: Controller, pos: Position) -> bool:
    """True if pos is present anywhere in the shared claim ledger."""
    return pos in unpack_claim_ledger(ct.read_store(Config.SLOT_ORE_CLAIM_LEDGER))


def claim_harvester_slot(ct: Controller, pos: Position) -> None:
    """Best-effort: record pos in the shared ledger if there's a free sub-slot.

    Silently no-ops if pos is already present or the ledger is full -- this is
    advisory dogpile-reduction, not a hard lock, so callers should proceed with
    their commitment either way.
    """
    claims = unpack_claim_ledger(ct.read_store(Config.SLOT_ORE_CLAIM_LEDGER))
    if pos in claims:
        return
    for i, c in enumerate(claims):
        if c is None:
            claims[i] = pos
            ct.write_store(Config.SLOT_ORE_CLAIM_LEDGER, pack_claim_ledger(claims))
            return


def release_harvester_slot(ct: Controller, pos: Position) -> None:
    """Clear pos from the shared ledger, e.g. on relay completion, abort, or timeout."""
    claims = unpack_claim_ledger(ct.read_store(Config.SLOT_ORE_CLAIM_LEDGER))
    if pos not in claims:
        return
    claims = [None if c == pos else c for c in claims]
    ct.write_store(Config.SLOT_ORE_CLAIM_LEDGER, pack_claim_ledger(claims))


def is_plausible_ore_target(ct: Controller, pos: Position, core_footprint: list[Position]) -> bool:
    """True if pos is in bounds, not Core footprint, and not known invalid (enemy harvesters valid for hijack)."""
    if not is_in_bounds(ct, pos):
        return False
    if is_inside_core_area(pos, core_footprint, margin=0):
        return False
    my_pos = ct.get_position()
    if is_in_vision(ct, my_pos, pos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
        try:
            bid = ct.get_tile_building_id(pos)
            if bid is not None:
                if ct.get_entity_type(bid) == EntityType.HARVESTER and ct.get_team(bid) != ct.get_team():
                    return True
                return False
            if ct.get_tile_env(pos) != Environment.ORE_TITANIUM:
                return False
        except Exception:
            return False
    return True


def is_confirmed_ore_target(ct: Controller, pos: Position, core_footprint: list[Position]) -> bool:
    """True if pos is in bounds, not Core footprint, in vision, has no ally building, and is Environment.ORE_TITANIUM or enemy harvester."""
    if not is_in_bounds(ct, pos):
        return False
    if is_inside_core_area(pos, core_footprint, margin=0):
        return False
    my_pos = ct.get_position()
    if not is_in_vision(ct, my_pos, pos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
        return False
    try:
        bid = ct.get_tile_building_id(pos)
        if bid is not None:
            if ct.get_entity_type(bid) == EntityType.HARVESTER and ct.get_team(bid) != ct.get_team():
                return True
            return False
        if ct.get_tile_env(pos) != Environment.ORE_TITANIUM:
            return False
    except Exception:
        return False
    return True


def is_valid_ore_target(ct: Controller, pos: Position, core_footprint: list[Position]) -> bool:
    """Validate that pos is a plausible ore target (in-bounds, not inside Core footprint, not known invalid)."""
    return is_plausible_ore_target(ct, pos, core_footprint)


def get_invalid_ore_reason(ct: Controller, pos: Position, core_footprint: list[Position]) -> Optional[str]:
    """Return explicit reason string for invalid ore target for telemetry.

    Mirrors OreTargeting.is_valid()'s gates as closely as a stateless function
    can -- it omits the per-builder local blacklist (that state isn't
    available here), so a blacklisted-but-otherwise-plausible tile still
    falls through to the generic "INVALID" caller default. The builder that
    blacklisted it already logged why at the time, so that residual case is
    lower-value to resolve here.
    """
    if not is_in_bounds(ct, pos):
        return "OUT_OF_BOUNDS"
    if is_inside_core_area(pos, core_footprint, margin=0):
        return "CORE_FOOTPRINT"
    if not is_in_friendly_half(ct, pos, core_footprint):
        return "ENEMY_HALF"
    my_pos = ct.get_position()
    if is_in_vision(ct, my_pos, pos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
        try:
            bid = ct.get_tile_building_id(pos)
            if bid is not None:
                if ct.get_entity_type(bid) == EntityType.HARVESTER and ct.get_team(bid) != ct.get_team():
                    return None  # Enemy harvester is valid for hijack!
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
            self.record_harvester_built(pos)
        elif reason == "NOT_ORE":
            status = Config.ORE_STATUS_NOT_ORE
            self.known_friendly_ores.discard(pos)
        elif reason in ("CORE_FOOTPRINT", "OUT_OF_BOUNDS"):
            status = Config.ORE_STATUS_CORE_FOOTPRINT
            self.known_friendly_ores.discard(pos)
        elif reason == "UNREACHABLE":
            status = Config.ORE_STATUS_UNREACHABLE
        else:
            status = Config.ORE_STATUS_NOT_ORE
            self.known_friendly_ores.discard(pos)

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
        if self.is_blacklisted(ct, pos):
            return False

        my_pos = ct.get_position()
        if is_in_vision(ct, my_pos, pos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
            return is_confirmed_ore_target(ct, pos, core_footprint)

        return is_plausible_ore_target(ct, pos, core_footprint)
