"""knight — Exploration Domain: Frontier Search & Maze-Breaking Navigation.

Maintains spatial occupancy memory (walkable/blocked/visited) and computes
frontier boundary targets to aggressively explore unmapped terrain and mazes.
"""

from __future__ import annotations

from collections import deque
import sys
from typing import Optional, Set
from fcode import Controller, Direction, EntityType, Environment, Position

from config import Config
from exploration.spread_zone import SpreadZone, in_spread_zone, zone_score
from exploration.symmetry import SymmetryHints
from utils.geometry import (
    get_core_footprint,
    is_adjacent,
    is_in_bounds,
    is_in_vision,
    is_valid_ore_target,
    unpack_ore_discovery,
    unpack_pos,
)


def pos_key(pos: Position) -> int:
    """Pack Position into uint32 for fast set/dict operations."""
    return ((pos.x & 0xFFFF) << 16) | (pos.y & 0xFFFF)


def key_to_pos(key: int) -> Position:
    """Unpack uint32 into Position."""
    return Position((key >> 16) & 0xFFFF, key & 0xFFFF)


class FrontierExplorer:
    """Frontier-based exploration state machine for Economy and Scout Builders."""

    def __init__(self) -> None:
        self.seen_walkable: set[int] = set()
        self.seen_blocked: set[int] = set()
        self.visited: set[int] = set()
        self.failed_targets: dict[int, int] = {}
        self.frontier_cache: list[Position] = []
        self.frontier_round: int = -100
        self.wall_follow_mode: bool = False
        self.wall_hand: Direction = Direction.EAST
        self.stuck_counter: int = 0
        self.last_pos: Optional[Position] = None
        self.zone_relax_turns: int = 0

    def observe(self, ct: Controller) -> None:
        """Scan local vision tiles and update spatial occupancy memory."""
        my_team = ct.get_team()
        pos = ct.get_position()
        self.visited.add(pos_key(pos))

        for tile in ct.get_nearby_tiles():
            k = pos_key(tile)
            env = ct.get_tile_env(tile)

            if env == Environment.WALL:
                self.seen_blocked.add(k)
                self.seen_walkable.discard(k)
            elif env == Environment.ORE_TITANIUM:
                self.seen_walkable.add(k)
            else:
                bid = ct.get_tile_building_id(tile)
                if bid is not None:
                    etype = ct.get_entity_type(bid)
                    if etype in (EntityType.CONVEYOR, EntityType.SPLITTER):
                        self.seen_walkable.add(k)
                    else:
                        self.seen_blocked.add(k)
                        self.seen_walkable.discard(k)
                else:
                    self.seen_walkable.add(k)

    def local_ore_target(self, ct: Controller) -> Optional[Position]:
        """Return nearest visible unmined ORE_TITANIUM tile."""
        pos = ct.get_position()
        core_footprint = get_core_footprint(ct)
        best_ore: Optional[Position] = None
        best_dist = 10**9

        for tile in ct.get_nearby_tiles():
            k = pos_key(tile)
            if k in self.failed_targets:
                continue
            if is_valid_ore_target(ct, tile, core_footprint):
                d = pos.distance_squared(tile)
                if d < best_dist:
                    best_dist = d
                    best_ore = tile

        return best_ore

    def store_ore_target(self, ct: Controller) -> Optional[Position]:
        """Read fresh unmined ore broadcast from SLOT_ORE_DISCOVERY."""
        curr_rnd = ct.get_current_round()
        core_footprint = get_core_footprint(ct)
        raw_val = ct.read_store(Config.SLOT_ORE_DISCOVERY)
        if raw_val == 0:
            return None

        # Check freshness via round_mod (mod 256)
        pos, round_mod = unpack_ore_discovery(raw_val)
        if pos is not None:
            age = (curr_rnd - round_mod) % 256
            if age <= Config.ORE_DISCOVERY_TTL_ROUNDS:
                if pos_key(pos) not in self.failed_targets and is_valid_ore_target(ct, pos, core_footprint):
                    return pos

        return None

    def frontier_targets(self, ct: Controller) -> list[Position]:
        """Return known walkable tiles that border unobserved terrain with CPU valve guard."""
        curr_rnd = ct.get_current_round()
        if curr_rnd - self.frontier_round < Config.FRONTIER_CACHE_TTL_TURNS and self.frontier_cache:
            return self.frontier_cache

        if ct.get_cpu_time_elapsed() > Config.CPU_SEARCH_VALVE_MICROSEC and self.frontier_cache:
            return self.frontier_cache

        frontiers: list[Position] = []
        map_w = ct.get_map_width()
        map_h = ct.get_map_height()

        for k in self.seen_walkable:
            p = key_to_pos(k)
            is_frontier = False
            for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                adj = p.add(d)
                if 0 <= adj.x < map_w and 0 <= adj.y < map_h:
                    adj_k = pos_key(adj)
                    if adj_k not in self.seen_walkable and adj_k not in self.seen_blocked:
                        is_frontier = True
                        break

            if is_frontier:
                frontiers.append(p)

        self.frontier_cache = frontiers
        self.frontier_round = curr_rnd
        return frontiers

    def choose_scout_target(
        self,
        ct: Controller,
        zone: SpreadZone,
        symmetry: SymmetryHints,
        core_pos: Position,
    ) -> Optional[Position]:
        """Select best exploration target using tiered priority pipeline."""
        pos = ct.get_position()
        curr_rnd = ct.get_current_round()
        map_w = ct.get_map_width()
        map_h = ct.get_map_height()
        is_maze = max(map_w, map_h) <= Config.MAZE_MAP_MAX_DIMENSION

        # Increment zone relax turns once per turn
        self.zone_relax_turns += 1

        # Clean expired failed targets
        self.failed_targets = {
            k: r for k, r in self.failed_targets.items() if curr_rnd - r <= Config.ORE_TARGET_COOLDOWN_TURNS
        }

        # Priority 1: Visible local ore
        loc_ore = self.local_ore_target(ct)
        if loc_ore is not None:
            self.zone_relax_turns = 0
            return loc_ore

        # Priority 2: Fresh store ore discovery
        store_ore = self.store_ore_target(ct)
        if store_ore is not None:
            return store_ore

        # Priority 3 & 4: Frontier targets (Spread zone aligned first)
        frontiers = self.frontier_targets(ct)
        if frontiers:
            scored_frontiers: list[tuple[int, Position]] = []
            for fp in frontiers:
                if pos_key(fp) in self.failed_targets:
                    continue
                d = pos.distance_squared(fp)
                z_score = zone_score(fp, core_pos, zone) if self.zone_relax_turns < Config.SPREAD_ZONE_RELAX_AFTER_TURNS else 50
                score = z_score * 10 - d
                scored_frontiers.append((score, fp))

            if scored_frontiers:
                scored_frontiers.sort(key=lambda item: item[0], reverse=True)
                return scored_frontiers[0][1]

        # Priority 5: Symmetry hint (when local/mid-range frontier is fully mapped)
        if not is_maze and symmetry.enabled:
            sym_hint = symmetry.best_ore_hint(ct, pos, zone, core_pos)
            if sym_hint is not None and pos_key(sym_hint) not in self.failed_targets:
                return sym_hint

        # Priority 6: Directional vector toward enemy core
        if symmetry.enemy_core_hint is not None:
            return symmetry.enemy_core_hint

        return None

    def find_path_to_frontier(
        self,
        ct: Controller,
        start: Position,
        targets: list[Position],
    ) -> list[Direction]:
        """BFS pathfinder through known walkable tiles toward target set."""
        if not targets:
            return []

        target_set = set(targets)
        if start in target_set:
            return []

        queue: deque[tuple[Position, list[Direction]]] = deque([(start, [])])
        visited_nodes: set[int] = {pos_key(start)}
        nodes_checked = 0

        while queue:
            if ct.get_cpu_time_elapsed() > Config.CPU_SEARCH_VALVE_MICROSEC:
                return []

            nodes_checked += 1
            if nodes_checked > Config.FRONTIER_BFS_NODE_LIMIT:
                break

            curr, path = queue.popleft()

            if curr in target_set:
                return path

            for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                nxt = curr.add(d)
                k = pos_key(nxt)
                if k in visited_nodes or k in self.seen_blocked:
                    continue
                if k not in self.seen_walkable:
                    continue

                visited_nodes.add(k)
                queue.append((nxt, path + [d]))

        return []

    def wall_follow_fallback(self, ct: Controller) -> Optional[Direction]:
        """Lightweight maze right-hand/left-hand wall follower."""
        if ct.get_move_cooldown() != 0:
            return None

        pos = ct.get_position()
        cardinals = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]

        # Check for stuck condition
        if self.last_pos == pos:
            self.stuck_counter += 1
            if self.stuck_counter >= Config.WALL_FOLLOW_STUCK_THRESHOLD:
                # Switch hand rule
                self.wall_hand = self.wall_hand.opposite()
                self.stuck_counter = 0
        else:
            self.stuck_counter = 0
        self.last_pos = pos

        # Order attempts based on current wall_hand
        rotations = [
            self.wall_hand.rotate_right(),
            self.wall_hand,
            self.wall_hand.rotate_left(),
            self.wall_hand.opposite(),
        ]

        for d in rotations:
            if d.is_cardinal():
                nxt = pos.add(d)
                k = pos_key(nxt)
                if k not in self.seen_blocked and is_in_bounds(ct, nxt) and ct.can_move(d):
                    self.wall_hand = d
                    return d

        return None
