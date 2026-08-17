"""Grand_knight — Logistics Domain: Steiner Belt Network & Grafting Coordinator.

Implements Steiner-tree conveyor networking: grafts newly discovered harvesters to the
closest live network tile or known relay anchor, minimizing conveyor costs and builder turns.
"""

from __future__ import annotations

import sys
from collections import deque
from typing import Optional, Set
from fcode import Controller, Direction, EntityType, Environment, Position

from config import Config
from logistics.belt_memory import BeltMemory
from logistics.belt_path import find_wall_aware_belt_path
from spatial.movement import is_in_bounds, is_in_vision
from utils.geometry import get_core_footprint


class BeltNetwork:
    """Steiner conveyor network graph tracking live connected tiles and routing grafts."""

    def __init__(self, belt_memory: Optional[BeltMemory] = None) -> None:
        self.belt_memory = belt_memory or BeltMemory()
        self.known_network_tiles: set[Position] = set()

    def update_network_from_memory(self, ct: Controller) -> None:
        """Refresh live network tiles from local BeltMemory and verified buildings."""
        core_footprint = get_core_footprint(ct)
        for c in core_footprint:
            self.known_network_tiles.add(c)

        for relay in self.belt_memory.routes.values():
            for p in relay.path:
                self.known_network_tiles.add(p)

    def find_nearest_network_anchor(
        self,
        ct: Controller,
        harvester_pos: Position,
        seen_walls: Optional[Set[Position]] = None,
    ) -> tuple[Position, bool]:
        """Find closest live network tile or fallback to Core footprint.
        
        Returns:
            (anchor_pos, is_steiner_graft)
        """
        core_footprint = get_core_footprint(ct)
        self.update_network_from_memory(ct)

        if not Config.ENABLE_STEINER_GRAFTING or not self.known_network_tiles:
            nearest_core = min(core_footprint, key=lambda c: harvester_pos.distance_squared(c))
            return nearest_core, False

        # Candidates: verified live conveyors in vision or known network tiles
        candidates: list[Position] = []
        my_team = ct.get_team()
        my_pos = ct.get_position()

        # Scan nearby visible friendly conveyors
        for tile in ct.get_nearby_tiles():
            if not is_in_bounds(ct, tile):
                continue
            bid = ct.get_tile_building_id(tile)
            if bid is not None:
                try:
                    if ct.get_team(bid) == my_team and ct.get_entity_type(bid) in (EntityType.CONVEYOR, EntityType.SPLITTER):
                        candidates.append(tile)
                except Exception:
                    pass

        # Include known network tiles from local BeltMemory
        for t in self.known_network_tiles:
            if t not in core_footprint:
                candidates.append(t)

        if not candidates:
            nearest_core = min(core_footprint, key=lambda c: harvester_pos.distance_squared(c))
            return nearest_core, False

        # Filter out unreachable/wall candidates and pick closest
        valid_candidates = [
            c for c in candidates
            if seen_walls is None or c not in seen_walls
        ]

        if not valid_candidates:
            nearest_core = min(core_footprint, key=lambda c: harvester_pos.distance_squared(c))
            return nearest_core, False

        best_anchor = min(valid_candidates, key=lambda c: harvester_pos.distance_squared(c))
        is_graft = best_anchor not in core_footprint
        return best_anchor, is_graft

    def plan_steiner_belt_path(
        self,
        ct: Controller,
        harvester_pos: Position,
        seen_walls: Optional[Set[Position]] = None,
        max_hops: int = Config.GRAFT_MAX_HOPS,
    ) -> list[Position]:
        """Path a new conveyor line from harvester to closest live network anchor or Core."""
        anchor, is_graft = self.find_nearest_network_anchor(ct, harvester_pos, seen_walls)
        core_footprint = get_core_footprint(ct)
        seen = seen_walls if seen_walls is not None else set()

        # Target goals: anchor itself or its placeable adjacent tiles
        target_goals: set[Position] = set()
        if is_graft:
            for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                adj = anchor.add(d)
                if is_in_bounds(ct, adj) and adj != harvester_pos:
                    target_goals.add(adj)
        else:
            for c in core_footprint:
                target_goals.add(c)

        queue: deque[tuple[Position, list[Position]]] = deque([(harvester_pos, [])])
        visited: set[Position] = {harvester_pos}

        while queue:
            if ct.get_cpu_time_elapsed() > Config.CPU_SEARCH_VALVE_MICROSEC:
                break

            curr, path = queue.popleft()

            if len(path) > max_hops:
                continue

            for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                nxt = curr.add(d)

                if not is_in_bounds(ct, nxt):
                    continue

                if nxt in target_goals:
                    completed_path = path + [nxt]
                    print(
                        f"BELT_GRAFT_PLANNED harvester={harvester_pos} anchor={anchor} "
                        f"length={len(completed_path)} round={ct.get_current_round()}",
                        file=sys.stderr,
                    )
                    return completed_path

                if nxt in visited or nxt in seen:
                    continue

                visited.add(nxt)

                # Check impassable obstacle in vision
                my_pos = ct.get_position()
                if is_in_vision(ct, my_pos, nxt, Config.BUILDER_BOT_VISION_RADIUS_SQ):
                    try:
                        if ct.get_tile_env(nxt) == Environment.WALL:
                            seen.add(nxt)
                            continue
                        bid = ct.get_tile_building_id(nxt)
                        if bid is not None:
                            btype = ct.get_entity_type(bid)
                            if btype not in (EntityType.CONVEYOR, EntityType.SPLITTER):
                                continue
                    except Exception:
                        pass

                queue.append((nxt, path + [nxt]))

        # Fallback to standard Core pathfinder if graft path failed
        return find_wall_aware_belt_path(ct, harvester_pos, Config.MAX_BELT_RELAY_HOPS, seen_walls)
