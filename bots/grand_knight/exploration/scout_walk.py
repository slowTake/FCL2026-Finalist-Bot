"""Grand_knight — Exploration Domain: Scout Walk & Enclosure Escape.

Fallback movement for a Builder Bot with no active ore target or belt work:
break out of a boxed-in spawn first, then frontier-search toward the assigned
SpreadZone, falling back to wall-following and finally an open-direction step
when no frontier target or path is available.
"""

from __future__ import annotations

import sys
from typing import Optional, Set

from fcode import Controller, Direction, Position

from combat.dismantle import try_dismantle_adjacent_hostile
from config import Config
from engine.safe_controller import SafeController
from exploration.frontier import FrontierExplorer
from exploration.spread_zone import SpreadZone
from exploration.symmetry import SymmetryHints
from logistics.belt_repair import BeltRepairSkill
from spatial.movement import PathCache, execute_bug2_evasion, is_traversable
from utils.geometry import get_core_footprint
from utils.support import SupportSkill


class ScoutWalk:
    """Enclosure escape plus frontier-search exploration fallback."""

    def __init__(
        self,
        safe: SafeController,
        support: SupportSkill,
        belt_repair: BeltRepairSkill,
        frontier: FrontierExplorer,
        symmetry: SymmetryHints,
    ) -> None:
        self.safe = safe
        self.support = support
        self.belt_repair = belt_repair
        self.frontier = frontier
        self.symmetry = symmetry
        self.last_scout_target: Optional[Position] = None
        self.path_cache = PathCache()

    def try_escape_enclosure(self, ct: Controller, seen_walls: Set[Position]) -> bool:
        """Wall-contour following for enclosed spawns (surrounded on 3+ sides or in tight corridor)."""
        if ct.get_move_cooldown() != 0:
            return False
        pos = ct.get_position()
        cardinals = (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)

        impassable_count = sum(
            1 for d in cardinals
            if not is_traversable(ct, pos.add(d), seen_walls)
        )

        # If surrounded on 3 sides or in a 1-tile dead end
        if impassable_count >= 3:
            open_steps = []
            for d in cardinals:
                nxt = pos.add(d)
                if is_traversable(ct, nxt, seen_walls) and ct.can_move(d):
                    exits = sum(1 for ed in cardinals if is_traversable(ct, nxt.add(ed), seen_walls))
                    open_steps.append((exits, d))
            if open_steps:
                open_steps.sort(key=lambda item: item[0], reverse=True)
                outcome = self.safe.safe_move(open_steps[0][1])
                if outcome.success:
                    return True
        return False

    def walk(
        self,
        ct: Controller,
        spread_zone: SpreadZone,
        pos_history: list[Position],
        seen_walls: Set[Position],
    ) -> bool:
        """Frontier search & maze-breaking exploration walk."""
        if ct.get_move_cooldown() != 0:
            return False

        pos = ct.get_position()
        my_id = ct.get_id()
        curr_rnd = ct.get_current_round()
        core_footprint = get_core_footprint(ct)
        core_pos = core_footprint[0] if core_footprint else Position(0, 0)

        # Active self-defense & support checks
        if try_dismantle_adjacent_hostile(ct, self.safe):
            return True
        if self.belt_repair.try_repair_adjacent_belt(ct, core_footprint):
            return True
        if self.support.try_heal_damaged_structures(ct):
            return True

        # Priority 0: break out of a boxed-in spawn before attempting frontier search
        if self.try_escape_enclosure(ct, seen_walls):
            return True

        # Step 1: Choose scout/frontier target
        scout_target = self.frontier.choose_scout_target(ct, spread_zone, self.symmetry, core_pos)
        if scout_target is not None:
            if Config.DEBUG_ECONOMY and scout_target != self.last_scout_target:
                print(f"FRONTIER_TARGET builder_id={my_id} target={scout_target} round={curr_rnd}", file=sys.stderr)
                self.last_scout_target = scout_target

            # Try BFS through known walkable tiles to frontier
            path = self.frontier.find_path_to_frontier(ct, pos, [scout_target])
            if path:
                outcome = self.safe.safe_move(path[0])
                if outcome.success:
                    return True

            # Fallback to Bug2 navigation toward target
            if execute_bug2_evasion(ct, self.safe, self.path_cache, pos, scout_target, pos_history, seen_walls):
                return True

        # Step 2: Maze wall follow fallback
        step = self.frontier.wall_follow_fallback(ct)
        if step is not None:
            if Config.DEBUG_ECONOMY and curr_rnd % 10 == 0:
                print(f"MAZE_WALL_FOLLOW builder_id={my_id} round={curr_rnd} hand={self.frontier.wall_hand}", file=sys.stderr)
            outcome = self.safe.safe_move(step)
            if outcome.success:
                return True

        # Step 3: Open direction step fallback
        cardinals = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]
        primary_dir = cardinals[spread_zone.quadrant]
        valid_steps = []
        recent_visited = set(pos_history[-4:]) if pos_history else set()
        for d in (primary_dir, primary_dir.rotate_right(), primary_dir.rotate_left(), primary_dir.opposite()):
            if d.is_cardinal():
                nxt = pos.add(d)
                if is_traversable(ct, nxt, seen_walls) and ct.can_move(d) and nxt not in recent_visited:
                    exits = sum(1 for ed in cardinals if is_traversable(ct, nxt.add(ed), seen_walls))
                    valid_steps.append((exits, d == primary_dir, d))
        if valid_steps:
            valid_steps.sort(key=lambda item: (item[1], item[0]), reverse=True)
            outcome = self.safe.safe_move(valid_steps[0][2])
            if outcome.success:
                return True

        return False
