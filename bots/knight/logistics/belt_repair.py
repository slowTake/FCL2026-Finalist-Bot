"""knight — Logistics Domain: Conveyor Belt Gap Detection, Inspection & Autonomous Repair.

Scans for severed or dangling conveyor belt lines, determines correct downstream facing
toward the Core footprint, and autonomously repairs breaks in the ore delivery pipeline.
"""

from __future__ import annotations

import sys
from enum import Enum
from typing import Optional, Set, Tuple
from fcode import Controller, Direction, EntityType, Environment, Position

from behavior.movement_skill import MovementSkill
from blackboard.belt_protocol import clear_belt_alert_if_matches, write_belt_alert
from config import Config
from engine.outcome import Outcome, Reason
from engine.safe_controller import SafeController
from logistics.belt_memory import RelayRoute, RelayStatus
from spatial.movement import (
    execute_bug2_evasion,
    find_builder_navigation_path,
    get_greedy_step,
    is_in_bounds,
    is_traversable,
)
from utils.geometry import (
    get_core_footprint,
    get_nearest_core_tile,
    is_adjacent,
    is_in_vision,
    is_inside_core_area,
)


class TileState(Enum):
    OK = 1
    MISSING_CONVEYOR = 2
    WRONG_DIRECTION = 3
    FRIENDLY_BLOCK = 4
    ENEMY_BLOCK = 5
    UNKNOWN = 6
    CONTESTED = 7


def get_conveyor_target(ct: Controller, conveyor_id: int, conveyor_pos: Position) -> Optional[Position]:
    """Determine the tile a conveyor is feeding into based on its facing direction."""
    try:
        facing = ct.get_direction(conveyor_id)
        if facing and facing != Direction.CENTRE:
            return conveyor_pos.add(facing)
    except Exception:
        pass
    return None


def determine_reconnect_facing(
    ct: Controller,
    gap_tile: Position,
    core_footprint: list[Position],
    seen_walls: Optional[Set[Position]] = None,
) -> Optional[Direction]:
    """Determine the required conveyor facing at gap_tile to feed downstream toward Core."""
    my_team = ct.get_team()

    # 1. If adjacent to Core footprint, point directly into Core
    for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
        nbr = gap_tile.add(d)
        if nbr in core_footprint:
            return d

    # 2. Look for an adjacent downstream friendly conveyor closer to Core
    best_dir: Optional[Direction] = None
    best_dist = 10**9
    nearest_core = core_footprint[0] if core_footprint else Position(0, 0)
    gap_dist_to_core = gap_tile.distance_squared(nearest_core)

    for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
        nbr = gap_tile.add(d)
        if not is_in_bounds(ct, nbr):
            continue
        try:
            bid = ct.get_tile_building_id(nbr)
            if bid is not None and ct.get_team(bid) == my_team:
                btype = ct.get_entity_type(bid)
                if btype in (EntityType.CONVEYOR, EntityType.CORE):
                    dist_to_core = nbr.distance_squared(nearest_core)
                    if dist_to_core < gap_dist_to_core and dist_to_core < best_dist:
                        best_dist = dist_to_core
                        best_dir = d
        except Exception:
            pass

    if best_dir is not None:
        return best_dir

    # 3. Fallback: greedy direction toward nearest core tile
    greedy_d = get_greedy_step(gap_tile, nearest_core)
    return greedy_d if greedy_d != Direction.CENTRE else Direction.NORTH


class BeltRepairSkill:
    """Autonomous conveyor belt repair, inspection, and reconnection coordinator."""

    def __init__(self, safe: SafeController) -> None:
        self.safe = safe
        self.mover = MovementSkill(safe)
        self.contested_tiles: dict[Position, int] = {}

    def expected_facing(
        self,
        relay: RelayRoute,
        index: int,
        core_footprint: list[Position],
    ) -> Direction:
        """Determine downstream facing direction for tile at relay.path[index]."""
        path = relay.path
        if index < 0 or index >= len(path):
            return Direction.NORTH

        tile = path[index]
        if index + 1 < len(path):
            nxt = path[index + 1]
            return tile.cardinal_direction_to(nxt)
        else:
            # Last tile in path: points into Core footprint
            for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                if tile.add(d) in core_footprint:
                    return d
            nearest = min(core_footprint, key=lambda c: tile.distance_squared(c)) if core_footprint else Position(0, 0)
            card = tile.cardinal_direction_to(nearest)
            return card if (card.is_cardinal() and card != Direction.CENTRE) else Direction.NORTH

    def inspect_relay_tile(
        self,
        ct: Controller,
        relay: RelayRoute,
        index: int,
        core_footprint: list[Position],
    ) -> TileState:
        """Inspect a specific tile in a relay route and classify its state."""
        if index < 0 or index >= len(relay.path):
            return TileState.UNKNOWN

        tile = relay.path[index]
        curr_round = ct.get_current_round()

        # Check contested cooldown
        if tile in self.contested_tiles:
            if curr_round < self.contested_tiles[tile]:
                return TileState.CONTESTED
            else:
                del self.contested_tiles[tile]

        my_pos = ct.get_position()
        if not is_in_vision(ct, my_pos, tile, Config.BUILDER_BOT_VISION_RADIUS_SQ):
            return TileState.UNKNOWN

        if not is_in_bounds(ct, tile):
            return TileState.UNKNOWN

        my_team = ct.get_team()
        exp_facing = self.expected_facing(relay, index, core_footprint)

        try:
            bid = ct.get_tile_building_id(tile)
            if bid is None:
                env = ct.get_tile_env(tile)
                if env == Environment.WALL:
                    return TileState.FRIENDLY_BLOCK
                return TileState.MISSING_CONVEYOR

            bteam = ct.get_team(bid)
            btype = ct.get_entity_type(bid)

            if bteam != my_team:
                return TileState.ENEMY_BLOCK

            if btype == EntityType.CONVEYOR:
                facing = ct.get_direction(bid)
                if facing == exp_facing:
                    return TileState.OK
                else:
                    return TileState.WRONG_DIRECTION
            else:
                return TileState.FRIENDLY_BLOCK

        except Exception:
            return TileState.UNKNOWN

    def try_repair_tile(
        self,
        ct: Controller,
        relay: RelayRoute,
        index: int,
        core_footprint: list[Position],
        pos_history: Optional[list[Position]] = None,
        seen_walls: Optional[set[Position]] = None,
    ) -> Outcome:
        """Execute repair actions (build, dismantle, destroy, move) for a specific relay tile."""
        if index < 0 or index >= len(relay.path):
            return Outcome(False, Reason.INVALID_ARGUMENT, "Index out of range")

        if ct.get_hp() < Config.RETREAT_HP_THRESHOLD:
            return Outcome(False, Reason.COOLDOWN, "Retreating due to low HP")

        if ct.get_cpu_time_elapsed() > Config.CPU_SEARCH_VALVE_MICROSEC:
            return Outcome(False, Reason.CPU_BUDGET, "CPU valve exceeded")

        tile = relay.path[index]
        state = self.inspect_relay_tile(ct, relay, index, core_footprint)
        my_pos = ct.get_position()
        exp_facing = self.expected_facing(relay, index, core_footprint)
        curr_round = ct.get_current_round()

        if state == TileState.OK:
            clear_belt_alert_if_matches(ct, tile)
            return Outcome(True, Reason.OK, "Tile OK")

        if state == TileState.CONTESTED:
            return Outcome(False, Reason.CONTESTED, "Tile is currently contested")

        # Broadcast broken belt alert on discovery
        write_belt_alert(ct, tile, curr_round)

        # 1. Tile outside vision -> move closer
        if state == TileState.UNKNOWN:
            if ct.get_move_cooldown() == 0:
                if execute_bug2_evasion(ct, my_pos, tile, pos_history, seen_walls):
                    return Outcome(True, Reason.OK, "Approaching tile")
            return Outcome(False, Reason.OUT_OF_VISION, "Tile out of vision")

        # 2. ENEMY_BLOCK -> safe_fire if adjacent, or move adjacent
        if state == TileState.ENEMY_BLOCK:
            if is_adjacent(my_pos, tile):
                if ct.can_act():
                    outcome = self.safe.safe_fire(tile)
                    if outcome.success:
                        relay.repair_attempts += 1
                        if relay.repair_attempts >= Config.BELT_REPAIR_MAX_FIRE_ATTEMPTS:
                            self.contested_tiles[tile] = curr_round + Config.BELT_REPAIR_CONTESTED_COOLDOWN_TURNS
                            relay.repair_attempts = 0
                        return outcome
                    return outcome
                return Outcome(False, Reason.COOLDOWN, "Action on cooldown")
            else:
                if ct.get_move_cooldown() == 0:
                    if execute_bug2_evasion(ct, my_pos, tile, pos_history, seen_walls):
                        return Outcome(True, Reason.OK, "Approaching enemy block")
                return Outcome(False, Reason.NOT_ADJACENT, "Moving to attack enemy block")

        # 3. FRIENDLY_BLOCK -> safe_destroy if adjacent, or move adjacent
        if state == TileState.FRIENDLY_BLOCK:
            if is_adjacent(my_pos, tile):
                if ct.can_act():
                    return self.safe.safe_destroy(tile)
                return Outcome(False, Reason.COOLDOWN, "Action on cooldown")
            else:
                if ct.get_move_cooldown() == 0:
                    if execute_bug2_evasion(ct, my_pos, tile, pos_history, seen_walls):
                        return Outcome(True, Reason.OK, "Approaching friendly block")
                return Outcome(False, Reason.NOT_ADJACENT, "Moving to destroy friendly block")

        # 4. WRONG_DIRECTION -> safe_destroy if adjacent, or move adjacent
        if state == TileState.WRONG_DIRECTION:
            if is_adjacent(my_pos, tile):
                if ct.can_act():
                    return self.safe.safe_destroy(tile)
                return Outcome(False, Reason.COOLDOWN, "Action on cooldown")
            else:
                if ct.get_move_cooldown() == 0:
                    if execute_bug2_evasion(ct, my_pos, tile, pos_history, seen_walls):
                        return Outcome(True, Reason.OK, "Approaching misaligned conveyor")
                return Outcome(False, Reason.NOT_ADJACENT, "Moving to realign conveyor")

        # 5. MISSING_CONVEYOR -> safe_build_conveyor if adjacent, or move adjacent
        if state == TileState.MISSING_CONVEYOR:
            if my_pos == tile:
                if ct.get_move_cooldown() == 0:
                    for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                        nxt = my_pos.add(d)
                        if ct.can_move(d) and is_traversable(ct, nxt, seen_walls):
                            outcome = self.safe.safe_move(d)
                            if outcome.success:
                                return outcome
                return Outcome(False, Reason.BLOCKED, "Standing on target tile")

            if is_adjacent(my_pos, tile):
                if ct.can_act() and ct.get_global_resources() >= ct.get_conveyor_cost():
                    outcome = self.safe.safe_build_conveyor(tile, exp_facing)
                    if outcome.success:
                        clear_belt_alert_if_matches(ct, tile)
                    return outcome
                return Outcome(False, Reason.NO_RESOURCES, "Waiting for resources/cooldown")
            else:
                if ct.get_move_cooldown() == 0:
                    if execute_bug2_evasion(ct, my_pos, tile, pos_history, seen_walls):
                        return Outcome(True, Reason.OK, "Approaching missing conveyor")
                return Outcome(False, Reason.NOT_ADJACENT, "Moving to missing conveyor")

        return Outcome(False, Reason.EXCEPTION, "Unhandled state")

    def try_repair_adjacent_belt(self, ct: Controller, core_footprint: list[Position]) -> bool:
        """Zero-movement check-and-patch: repair if adjacent to a severed conveyor."""
        if not ct.can_act() or ct.get_global_resources() < ct.get_conveyor_cost() or self.safe.major_action_used:
            return False

        my_pos = ct.get_position()
        my_team = ct.get_team()

        for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
            adj = my_pos.add(d)
            if not is_in_bounds(ct, adj) or adj in core_footprint:
                continue
            if not is_in_vision(ct, my_pos, adj, Config.BUILDER_BOT_VISION_RADIUS_SQ):
                continue

            try:
                if ct.get_tile_building_id(adj) is None and ct.get_tile_env(adj) != Environment.WALL:
                    feeding_upstream = False
                    for nd in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                        nbr = adj.add(nd)
                        if nbr == my_pos or not is_in_bounds(ct, nbr):
                            continue
                        nbr_bid = ct.get_tile_building_id(nbr)
                        if nbr_bid is not None and ct.get_team(nbr_bid) == my_team:
                            nb_type = ct.get_entity_type(nbr_bid)
                            if nb_type == EntityType.CONVEYOR:
                                facing = ct.get_direction(nbr_bid)
                                if facing and nbr.add(facing) == adj:
                                    feeding_upstream = True
                                    break

                    if feeding_upstream:
                        facing = determine_reconnect_facing(ct, adj, core_footprint)
                        if facing is not None:
                            outcome = self.safe.safe_build_conveyor(adj, facing)
                            if outcome.success:
                                return True
            except Exception:
                continue

        return False

    def try_reconnect_broken_belts(
        self,
        ct: Controller,
        core_footprint: list[Position],
        allow_walk: bool = True,
        seen_walls: Optional[Set[Position]] = None,
    ) -> bool:
        """Scan for broken gaps, repair if adjacent, or step toward gap if allow_walk."""
        return self.try_repair_adjacent_belt(ct, core_footprint)
