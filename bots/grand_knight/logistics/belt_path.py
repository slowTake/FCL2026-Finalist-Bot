"""Grand_knight — Logistics Domain: Conveyor Belt Path Planning Helpers.

Pure, stateless helpers for planning a conveyor line from a Harvester back to the
Core footprint: a wall-aware BFS pathfinder, conveyor facing determination, and a
soft check for an existing friendly Harvester that isn't connected to Core.
"""

from __future__ import annotations

from collections import deque
from typing import Optional, Set
from fcode import Controller, Direction, EntityType, Environment, Position

from config import Config
from engine.safe_controller import SafeController
from spatial.movement import is_in_bounds
from utils.geometry import get_core_footprint, get_nearest_core_tile, is_in_vision


def find_wall_aware_belt_path(
    ct: Controller,
    harvester_pos: Position,
    max_hops: int = 40,
    seen_walls: Optional[Set[Position]] = None,
) -> list[Position]:
    """Single-path Wall-Aware BFS pathfinder to lay conveyor belt toward Core with CPU valve."""
    core_footprint = get_core_footprint(ct)
    seen = seen_walls if seen_walls is not None else set()

    queue: deque[tuple[Position, list[Position]]] = deque([(harvester_pos, [])])
    visited: set[Position] = {harvester_pos}

    for c in core_footprint:
        visited.add(c)

    while queue:
        if ct.get_cpu_time_elapsed() > Config.CPU_SEARCH_VALVE_MICROSEC:
            return []

        curr, path = queue.popleft()

        for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
            nxt = curr.add(d)

            if not is_in_bounds(ct, nxt):
                continue

            if nxt in core_footprint:
                return path

            if nxt in visited:
                continue

            visited.add(nxt)

            if nxt in seen:
                continue

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

            if len(path) + 1 > max_hops:
                continue

            queue.append((nxt, path + [nxt]))

    return []


def determine_conveyor_direction(curr: Position, nxt: Position) -> Direction:
    """Determine Direction required for conveyor at curr to feed nxt."""
    dx = nxt.x - curr.x
    dy = nxt.y - curr.y

    if dx == 1 and dy == 0:
        return Direction.EAST
    elif dx == -1 and dy == 0:
        return Direction.WEST
    elif dx == 0 and dy == 1:
        return Direction.SOUTH
    elif dx == 0 and dy == -1:
        return Direction.NORTH

    # If diagonal or separated, use primary cardinal direction
    card = curr.cardinal_direction_to(nxt)
    if card.is_cardinal() and card != Direction.CENTRE:
        return card
    return Direction.EAST


def get_cardinal_core_step(pos: Position, core_footprint: list[Position]) -> Direction:
    """Find cardinal direction from pos into an orthogonally adjacent core tile."""
    for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
        if pos.add(d) in core_footprint:
            return d
    nearest = get_nearest_core_tile(pos, core_footprint)
    card = pos.cardinal_direction_to(nearest)
    return card if (card.is_cardinal() and card != Direction.CENTRE) else Direction.EAST


def determine_relay_terminal_facing(
    ct: Controller,
    tile: Position,
    core_footprint: list[Position],
) -> Direction:
    """Facing for the final tile of a relay path.

    A relay doesn't always end at Core: Steiner grafting (see belt_network.py)
    deliberately routes to the nearest point on the *existing* network, which
    is frequently off the direct line to Core. RelayRoute only stores the
    path, not what it was planned to connect to, so this looks at what's
    actually built on each neighbor instead of assuming Core -- into Core if
    adjacent, else into an already-built friendly conveyor/splitter (the
    graft join), falling back to a greedy step toward Core only if neither
    exists yet.
    """
    for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
        if tile.add(d) in core_footprint:
            return d

    my_team = ct.get_team()
    for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
        nbr = tile.add(d)
        if not is_in_bounds(ct, nbr):
            continue
        try:
            bid = ct.get_tile_building_id(nbr)
            if bid is not None and ct.get_team(bid) == my_team and ct.get_entity_type(bid) in (EntityType.CONVEYOR, EntityType.SPLITTER):
                return d
        except Exception:
            continue

    return get_cardinal_core_step(tile, core_footprint)


def find_disconnected_harvester(
    ct: Controller,
    core_footprint: list[Position],
    safe: SafeController,
) -> Optional[Position]:
    """Soft check: returns position of an existing friendly harvester whose belt run is
    provably NOT connected to Core.

    Tiles outside the calling unit's current vision are inconclusive, not proof of a
    break -- ct.get_tile_building_id()/get_direction() are unreliable there, the same
    reason belt_repair.inspect_relay_tile checks is_in_vision before trusting them. A
    harvester whose belt walks off the edge of vision is left alone rather than flagged,
    since misreading fog-of-war as a severed line just wastes a full belt replan on an
    intact route.
    """
    my_team = ct.get_team()
    my_pos = ct.get_position()
    for bid in safe.get_nearby_buildings(400):
        try:
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER:
                hpos = ct.get_position(bid)
                connected = False
                inconclusive = False
                for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                    adj = hpos.add(d)
                    if not is_in_bounds(ct, adj):
                        continue
                    if adj in core_footprint:
                        connected = True
                        break
                    if not is_in_vision(ct, my_pos, adj, Config.BUILDER_BOT_VISION_RADIUS_SQ):
                        inconclusive = True
                        continue
                    abid = ct.get_tile_building_id(adj)
                    if abid is not None and ct.get_team(abid) == my_team and ct.get_entity_type(abid) == EntityType.CONVEYOR:
                        curr = adj
                        seen_tiles = {hpos, adj}
                        for _ in range(Config.MAX_BELT_RELAY_HOPS):
                            facing = ct.get_direction(abid)
                            if not facing or facing == Direction.CENTRE:
                                break
                            nxt = curr.add(facing)
                            if not is_in_bounds(ct, nxt) or nxt in seen_tiles:
                                break
                            if nxt in core_footprint:
                                connected = True
                                break
                            if not is_in_vision(ct, my_pos, nxt, Config.BUILDER_BOT_VISION_RADIUS_SQ):
                                inconclusive = True
                                break
                            seen_tiles.add(nxt)
                            abid = ct.get_tile_building_id(nxt)
                            if abid is None or ct.get_team(abid) != my_team or ct.get_entity_type(abid) != EntityType.CONVEYOR:
                                break
                            curr = nxt
                        if connected:
                            break
                if connected or inconclusive:
                    continue
                return hpos
        except Exception:
            continue
    return None
