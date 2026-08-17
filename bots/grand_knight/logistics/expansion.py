"""Grand_knight — Logistics Domain: Ore Expansion (Harvester Build & Belt Paving).

Owns the full "acquire a new harvester" pipeline: rescue an existing disconnected
harvester or commit to a scored ore target, build the Harvester (or hijack an
enemy one), then pave a Steiner-grafted (or wall-aware BFS) conveyor belt back to
the live network -- falling back to the scout walk once there's nothing left to do.
"""

from __future__ import annotations

import sys
from typing import Optional, Set
from fcode import Controller, Direction, EntityType, Position

from blackboard.ore_protocol import (
    OreLifecycle,
    claim_harvester_slot,
    get_invalid_ore_reason,
    is_harvester_claimed,
    release_harvester_slot,
)
from config import Config
from engine.holding_gate import HoldingGate
from engine.outcome import Reason
from engine.safe_controller import SafeController
from exploration.frontier import FrontierExplorer
from exploration.spread_zone import SpreadZone
from logistics.belt_memory import BeltMemory
from logistics.belt_network import BeltNetwork
from logistics.belt_path import (
    determine_conveyor_direction,
    determine_relay_terminal_facing,
    find_disconnected_harvester,
    find_wall_aware_belt_path,
)
from logistics.ore_targeting import OreTargeting
from spatial.movement import PathCache, execute_bug2_evasion, is_in_bounds, is_traversable
from utils.geometry import get_core_footprint, get_reserved_builder_ids, is_adjacent, is_in_vision


class ExpansionSkill:
    """Commit to an ore target, build/hijack a Harvester, and pave its belt back to Core."""

    def __init__(
        self,
        safe: SafeController,
        belt_network: BeltNetwork,
        belt_memory: BeltMemory,
        ore_targeting: OreTargeting,
    ) -> None:
        self.safe = safe
        self.belt_network = belt_network
        self.belt_memory = belt_memory
        self.ore_targeting = ore_targeting
        self.hold_gate = HoldingGate()

        self.target_ore: Optional[Position] = None
        self.committed_ore: Optional[Position] = None
        self.commit_round: int = -1
        self.commit_policy: int = Config.EXPANSION_FREE
        self.harvester_built: bool = False
        self.belt_path: list[Position] = []
        self.paving_index: int = 0
        self.belt_block_counter: int = 0
        self.relay_completed: bool = False
        self.path_cache = PathCache()

    def abort_commitment(self, ct: Controller) -> None:
        """Drop the current commitment (e.g. Role-Isolated Commitment Abort under EXPANSION_LEASHED)."""
        if self.committed_ore is not None:
            release_harvester_slot(ct, self.committed_ore)
        self.committed_ore = None
        self.target_ore = None

    def try_rescue_disconnected_harvester(
        self, ct: Controller, core_footprint: list[Position], seen_walls: Set[Position]
    ) -> None:
        """Soft check: if an existing harvester is disconnected, prioritize completing its belt line."""
        if self.target_ore is not None or self.harvester_built:
            return

        my_id = ct.get_id()
        curr_rnd = ct.get_current_round()
        disc_h = find_disconnected_harvester(ct, core_footprint, self.safe)
        if disc_h is None:
            return
        if OreLifecycle.is_claimed_by_other(disc_h, my_id, curr_rnd) or is_harvester_claimed(ct, disc_h):
            return
        self.target_ore = disc_h
        self.committed_ore = disc_h
        self.harvester_built = True
        OreLifecycle.claim_ore(disc_h, my_id, curr_rnd)
        claim_harvester_slot(ct, disc_h)
        self.belt_path = self.belt_network.plan_steiner_belt_path(ct, disc_h, seen_walls, Config.GRAFT_MAX_HOPS)
        self.paving_index = 0
        self.belt_block_counter = 0
        if Config.DEBUG_ECONOMY:
            print(f"DISCONNECTED_HARVESTER_RESCUE builder_id={my_id} harvester={disc_h} round={curr_rnd}", file=sys.stderr)

    def try_build_belt(
        self,
        ct: Controller,
        seen_walls: Set[Position],
        pos_history: list[Position],
        spread_zone: Optional[SpreadZone],
    ) -> tuple[bool, Optional[SpreadZone]]:
        """Priority 3: Single-path conveyor paving directly along self.belt_path."""
        if not self.harvester_built:
            return False, spread_zone

        pos = ct.get_position()

        # Refresh the shared claim while actively paving so a legitimately in-progress
        # relay can't fall through OreLifecycle's staleness window and invite a duplicate
        # rescue/commit from another idle builder re-discovering the same harvester.
        if self.target_ore is not None:
            OreLifecycle.claim_ore(self.target_ore, ct.get_id(), ct.get_current_round())

        # Generate or re-plan belt path if empty
        if not self.belt_path and self.target_ore is not None:
            if Config.ENABLE_STEINER_GRAFTING:
                self.belt_path = self.belt_network.plan_steiner_belt_path(
                    ct, self.target_ore, seen_walls, Config.GRAFT_MAX_HOPS
                )
            else:
                self.belt_path = find_wall_aware_belt_path(
                    ct, self.target_ore, Config.MAX_BELT_RELAY_HOPS, seen_walls
                )
            self.paving_index = 0
            self.belt_block_counter = 0

        # Finished paving all tiles in path
        if self.paving_index >= len(self.belt_path):
            self.relay_completed = True

            # Save relay in local BeltMemory and update live BeltNetwork
            if self.target_ore is not None and self.belt_path:
                harvester_pos = self.target_ore
                relay_tuple = tuple(self.belt_path)
                curr_rnd = ct.get_current_round()
                self.belt_memory.add_relay(harvester_pos, relay_tuple, curr_rnd)
                self.belt_network.update_network_from_memory(ct)
                anchor = self.belt_path[-1] if self.belt_path else harvester_pos
                print(
                    f"BELT_GRAFT_COMPLETED harvester={harvester_pos} anchor={anchor} "
                    f"length={len(relay_tuple)} round={curr_rnd}",
                    file=sys.stderr,
                )

            # Transition to new expansion ore target immediately
            if self.target_ore is not None:
                release_harvester_slot(ct, self.target_ore)
            self.harvester_built = False
            self.target_ore = None
            self.committed_ore = None
            self.belt_path = []
            self.paving_index = 0
            self.belt_block_counter = 0

            if spread_zone is not None:
                new_band = min(2, spread_zone.band + 1)
                spread_zone = SpreadZone(spread_zone.quadrant, new_band)
                if Config.DEBUG_ECONOMY:
                    print(
                        f"RELAY_COMPLETE builder_id={ct.get_id()} round={ct.get_current_round()} "
                        f"next_zone={spread_zone.quadrant}/{spread_zone.band}",
                        file=sys.stderr,
                    )
            return False, spread_zone

        target_tile = self.belt_path[self.paving_index]

        # Determine feed direction toward next tile in path or Core
        core_footprint = get_core_footprint(ct)
        if self.paving_index + 1 < len(self.belt_path):
            nxt_dest = self.belt_path[self.paving_index + 1]
            facing = determine_conveyor_direction(target_tile, nxt_dest)
        else:
            facing = determine_relay_terminal_facing(ct, target_tile, core_footprint)
            nxt_dest = target_tile.add(facing)

        # Check if already paved correctly
        if is_in_vision(ct, pos, target_tile, Config.BUILDER_BOT_VISION_RADIUS_SQ):
            bid = ct.get_tile_building_id(target_tile)
            if bid is not None and ct.get_entity_type(bid) == EntityType.CONVEYOR:
                self.paving_index += 1
                self.belt_block_counter = 0
                return True, spread_zone

        # Step-off if builder is standing on target_tile
        if pos == target_tile:
            if ct.get_move_cooldown() == 0:
                step_dir = pos.cardinal_direction_to(nxt_dest)
                if step_dir != Direction.CENTRE and ct.can_move(step_dir) and is_traversable(ct, pos.add(step_dir), seen_walls):
                    outcome = self.safe.safe_move(step_dir)
                    if outcome.success:
                        return True, spread_zone
                for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                    if ct.can_move(d) and is_traversable(ct, pos.add(d), seen_walls):
                        outcome = self.safe.safe_move(d)
                        if outcome.success:
                            return True, spread_zone
            return False, spread_zone

        # Build conveyor if adjacent and affordable
        if is_adjacent(pos, target_tile):
            if ct.can_act() and ct.get_global_resources() >= ct.get_conveyor_cost():
                outcome = self.safe.safe_build_conveyor(target_tile, facing)
                if outcome.success:
                    self.paving_index += 1
                    self.belt_block_counter = 0
                    self.hold_gate.clear()
                    return True, spread_zone
                elif outcome.reason == Reason.BLOCKED:
                    self.belt_block_counter += 1
                    if self.belt_block_counter >= Config.BELT_OBSTRUCTION_PATIENCE:
                        self.belt_path = []
                        self.belt_block_counter = 0
                    return True, spread_zone
            res = self.hold_gate.hold(
                ct, target_tile, "COOLDOWN" if not ct.can_act() else "NO_RESOURCES", debug_expired=Config.DEBUG_ECONOMY
            )
            return res.consumed, spread_zone

        # Navigate along belt path
        if ct.get_move_cooldown() == 0:
            if execute_bug2_evasion(ct, self.safe, self.path_cache, pos, target_tile, pos_history, seen_walls):
                return True, spread_zone
            else:
                self.belt_block_counter += 1
                if self.belt_block_counter >= Config.BELT_OBSTRUCTION_PATIENCE:
                    self.belt_path = []
                    self.belt_block_counter = 0
                return True, spread_zone

        return True, spread_zone

    def try_mine_ore(
        self,
        ct: Controller,
        seen_walls: Set[Position],
        pos_history: list[Position],
        frontier: FrontierExplorer,
        spread_zone: Optional[SpreadZone],
        stuck_turns: int,
    ) -> tuple[bool, bool]:
        """Priority 4: Navigate to unclaimed ore deposit and construct Harvester.

        Returns (handled, target_aborted) -- target_aborted signals the caller should
        reset its own stuck-turn counter even though this returned False.

        Declines (False, False) when target_ore is None -- callers must not
        assume this always takes an action. Exploration when there's nothing
        to mine is the caller's job (see EconomyBuilder.run_economy_builder's
        final scout_walk step), not this function's: scout_walk also drives
        the builder's own vision coverage, so it needs to keep running at
        low priority rather than being embedded here where it would starve
        patrol/flex-defense/raid of ever getting a turn.
        """
        if self.harvester_built:
            return False, False

        pos = ct.get_position()
        curr_rnd = ct.get_current_round()
        my_id = ct.get_id()
        core_footprint = get_core_footprint(ct)
        policy = ct.read_store(Config.SLOT_EXPANSION_POLICY)

        if self.committed_ore is not None:
            if not self.ore_targeting.is_valid(ct, self.committed_ore):
                reason = get_invalid_ore_reason(ct, self.committed_ore, core_footprint) or "INVALID"
                if Config.DEBUG_ECONOMY:
                    print(f"INVALID_ORE_TARGET builder_id={my_id} ore={self.committed_ore} round={curr_rnd} reason={reason}", file=sys.stderr)
                OreLifecycle.release_claim(self.committed_ore, my_id)
                release_harvester_slot(ct, self.committed_ore)
                self.ore_targeting.report_invalid(ct, self.committed_ore, reason)
                self.committed_ore = None
                self.target_ore = None
            elif self.commit_round > 0 and (curr_rnd - self.commit_round) > Config.UNREACHABLE_ORE_TIMEOUT:
                if Config.DEBUG_ECONOMY:
                    print(f"EXPANSION_TIMEOUT builder_id={my_id} ore={self.committed_ore} round={curr_rnd}", file=sys.stderr)
                OreLifecycle.release_claim(self.committed_ore, my_id)
                release_harvester_slot(ct, self.committed_ore)
                self.ore_targeting.mark_unreachable(self.committed_ore, curr_rnd)
                self.committed_ore = None
                self.target_ore = None
            else:
                self.target_ore = self.committed_ore

        reserved_ids = get_reserved_builder_ids(ct)
        reserved_builder = my_id in reserved_ids
        global_flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        emergency_exp = bool(global_flags & Config.FLAG_EMERGENCY_EXPLORATION)
        my_team = ct.get_team()
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )

        if self.target_ore is None or not self.ore_targeting.is_valid(ct, self.target_ore):
            self.target_ore = self.ore_targeting.find_target(ct, frontier, spread_zone)
            if self.target_ore is not None and is_harvester_claimed(ct, self.target_ore):
                self.target_ore = None
            allow_commit = (
                policy in (Config.EXPANSION_FREE, Config.EXPANSION_PARTIAL)
                or (not reserved_builder)
                or harvester_count == 0
                or emergency_exp
            )
            if self.target_ore is not None and allow_commit:
                self.committed_ore = self.target_ore
                self.commit_round = curr_rnd
                self.commit_policy = policy
                OreLifecycle.claim_ore(self.committed_ore, my_id, curr_rnd)
                claim_harvester_slot(ct, self.committed_ore)
                if Config.DEBUG_ECONOMY:
                    print(
                        f"EXPANSION_COMMIT builder_id={ct.get_id()} ore={self.committed_ore} "
                        f"round={curr_rnd} policy={policy}",
                        file=sys.stderr,
                    )

        if self.target_ore is None:
            return False, False

        # If builder is standing directly on target_ore, step off to an adjacent open tile
        if pos == self.target_ore:
            for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                nxt = pos.add(d)
                if is_in_bounds(ct, nxt) and is_traversable(ct, nxt, seen_walls):
                    try:
                        if ct.get_tile_builder_bot_id(nxt) is None:
                            outcome = self.safe.safe_move(d)
                            if outcome.success:
                                return True, False
                    except Exception:
                        pass

        # If orthogonally adjacent to target_ore, check for hijack or build Harvester
        if is_adjacent(pos, self.target_ore):
            # Check for Enemy Harvester Hijack / Tap:
            bid = ct.get_tile_building_id(self.target_ore)
            if bid is not None:
                btype = ct.get_entity_type(bid)
                bteam = ct.get_team(bid)
                if btype == EntityType.HARVESTER and bteam != my_team:
                    if Config.DEBUG_ECONOMY:
                        print(f"HARVESTER_HIJACK builder_id={my_id} ore={self.target_ore} round={curr_rnd}", file=sys.stderr)
                    OreLifecycle.record_harvester_built(self.target_ore)
                    self.harvester_built = True
                    self.committed_ore = None
                    self.belt_path = self.belt_network.plan_steiner_belt_path(ct, self.target_ore, seen_walls, Config.GRAFT_MAX_HOPS)
                    self.paving_index = 0
                    self.belt_block_counter = 0
                    self.hold_gate.clear()
                    return True, False

            if not self.ore_targeting.is_valid(ct, self.target_ore):
                reason = get_invalid_ore_reason(ct, self.target_ore, core_footprint) or "INVALID"
                if Config.DEBUG_ECONOMY:
                    print(f"INVALID_ORE_TARGET builder_id={my_id} ore={self.target_ore} round={curr_rnd} reason={reason}", file=sys.stderr)
                self.ore_targeting.report_invalid(ct, self.target_ore, reason)
                release_harvester_slot(ct, self.target_ore)
                self.target_ore = None
                self.committed_ore = None
                return False, False

            if ct.can_act() and ct.get_global_resources() >= ct.get_harvester_cost():
                outcome = self.safe.safe_build_harvester(self.target_ore)
                if outcome.success:
                    OreLifecycle.record_harvester_built(self.target_ore)
                    self.harvester_built = True
                    self.committed_ore = None
                    self.belt_path = self.belt_network.plan_steiner_belt_path(ct, self.target_ore, seen_walls, Config.GRAFT_MAX_HOPS)
                    self.paving_index = 0
                    self.belt_block_counter = 0
                    self.hold_gate.clear()
                    return True, False
            res = self.hold_gate.hold(
                ct, self.target_ore, "COOLDOWN" if not ct.can_act() else "NO_RESOURCES", debug_expired=Config.DEBUG_ECONOMY
            )
            return res.consumed, False

        # Navigate toward target_ore using BFS & sideways-first evasion
        if ct.get_move_cooldown() == 0:
            if execute_bug2_evasion(ct, self.safe, self.path_cache, pos, self.target_ore, pos_history, seen_walls):
                return True, False
            elif stuck_turns > 20:
                # Pathing blocked to target_ore — abort and pick alternative target
                if Config.DEBUG_ECONOMY:
                    print(f"PATH_BLOCKED_ABORT builder_id={my_id} ore={self.target_ore} stuck={stuck_turns} round={curr_rnd}", file=sys.stderr)
                self.ore_targeting.mark_unreachable(self.target_ore, curr_rnd)
                release_harvester_slot(ct, self.target_ore)
                self.target_ore = None
                self.committed_ore = None
                return False, True

        return True, False
