"""Grand_knight — Logistics Domain: Opportunistic Harvester Raiding.

Lowest-priority economy fallback: once Core signals FLAG_OFFENSE_READY and a
builder has nothing more useful to do, hijack a nearby undefended enemy
Harvester. Deliberately bypasses ExpansionSkill.try_mine_ore rather than
routing through it -- that pipeline revalidates its target every tick through
an ECONOMY-mode OreTargeting (friendly-half gated), which would immediately
reject and overwrite a raid target outside friendly territory. Hands off to
the normal belt-paving pipeline only after the tap succeeds, via the same
harvester_built/belt_path fields economy's own incidental hijack already uses
(see ExpansionSkill.try_mine_ore's hijack branch).
"""

from __future__ import annotations

import sys
from typing import Any, Optional, Set
from fcode import Controller, EntityType, Position

from blackboard.ore_protocol import OreLifecycle
from config import Config
from engine.safe_controller import SafeController
from logistics.belt_network import BeltNetwork
from logistics.ore_targeting import OreTargeting
from spatial.movement import PathCache, execute_bug2_evasion
from utils.geometry import is_adjacent


class RaidSkill:
    """Opportunistic hijack targeting and execution for idle economy builders."""

    def __init__(self, safe: SafeController, belt_network: BeltNetwork) -> None:
        self.safe = safe
        self.belt_network = belt_network
        self.targeting = OreTargeting(safe, role=Config.TARGETING_RAIDER)
        self.path_cache = PathCache()
        self.target: Optional[Position] = None

    def try_hijack(
        self,
        ct: Controller,
        pos: Position,
        seen_walls: Set[Position],
        pos_history: list[Position],
        expansion: Any,
    ) -> bool:
        """Navigate to and tap a hijack target, handing the result to expansion
        (an ExpansionSkill) on success. Returns True if this tick was spent raiding."""
        target = self.target
        if target is None or not self.targeting.is_valid(ct, target):
            target = self.targeting.find_hijack_target(ct)
        if target is None:
            self.target = None
            return False
        self.target = target

        if not is_adjacent(pos, target):
            if ct.get_move_cooldown() == 0:
                execute_bug2_evasion(ct, self.safe, self.path_cache, pos, target, pos_history, seen_walls)
            return True

        bid = ct.get_tile_building_id(target)
        if bid is None or ct.get_entity_type(bid) != EntityType.HARVESTER or ct.get_team(bid) == ct.get_team():
            self.target = None
            return False

        if Config.DEBUG_ECONOMY:
            print(f"RAID_HIJACK builder_id={ct.get_id()} ore={target} round={ct.get_current_round()}", file=sys.stderr)
        OreLifecycle.record_harvester_built(target)
        expansion.harvester_built = True
        expansion.target_ore = target
        expansion.committed_ore = None
        expansion.belt_path = self.belt_network.plan_steiner_belt_path(ct, target, seen_walls, Config.GRAFT_MAX_HOPS)
        expansion.paving_index = 0
        expansion.belt_block_counter = 0
        self.target = None
        return True
