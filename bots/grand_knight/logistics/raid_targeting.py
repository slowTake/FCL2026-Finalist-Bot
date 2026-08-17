"""Grand_knight — Logistics Domain: Raider Hijack Target Validity.

Free functions backing OreTargeting(role=TARGETING_RAIDER)'s validity gate.
Kept separate from OreTargeting itself: raiding is a structurally different
concern (threat-gated, friendly-half-bypassing) from economy scoring, and
these don't need blacklist state, only ct/pos/core_footprint.
"""

from __future__ import annotations

from fcode import Controller, EntityType, Position

from blackboard.ore_protocol import is_confirmed_ore_target, is_plausible_ore_target
from config import Config
from engine.safe_controller import SafeController
from utils.geometry import is_in_vision


def is_valid_raid_target(
    safe: SafeController,
    ct: Controller,
    pos: Position,
    core_footprint: list[Position],
) -> bool:
    """pos must be a hijackable enemy Harvester if currently in vision; if
    not (e.g. a broadcast target the raider hasn't reached yet), fall back
    to the same plausibility trust economy targeting already extends to
    out-of-vision ore -- SLOT_ORE_DISCOVERY doesn't carry a bit
    distinguishing "empty ore" from "enemy harvester", so a distant
    broadcast target can turn out to be plain ore once reached.
    RaidSkill.try_hijack's own adjacency check re-verifies for real and
    declines gracefully rather than acting on a bad target.
    """
    my_pos = ct.get_position()
    if is_in_vision(ct, my_pos, pos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
        if not is_confirmed_ore_target(ct, pos, core_footprint):
            return False
        bid = ct.get_tile_building_id(pos)
        if bid is None or ct.get_entity_type(bid) != EntityType.HARVESTER or ct.get_team(bid) == ct.get_team():
            return False
    elif not is_plausible_ore_target(ct, pos, core_footprint):
        return False
    return not _is_defended(safe, ct, pos)


def _is_defended(safe: SafeController, ct: Controller, pos: Position) -> bool:
    """True if an enemy Gunner/Sentinel is within threat radius of pos.

    A flat radius, not firing-arc geometry -- cheap and correct-enough for
    a first cut; tighten later if turret-LOS work reuses this.
    """
    for bid in safe.get_nearby_buildings(Config.RAID_HIJACK_THREAT_RADIUS_SQ):
        if ct.get_team(bid) == ct.get_team():
            continue
        if ct.get_entity_type(bid) not in (EntityType.GUNNER, EntityType.SENTINEL):
            continue
        if pos.distance_squared(ct.get_position(bid)) <= Config.RAID_HIJACK_THREAT_RADIUS_SQ:
            return True
    return False
