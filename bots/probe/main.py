"""knight — Ground-Truth Physics Probe Bot.

Empirically tests fcode engine mechanics:
1. is_tile_passable() for CONVEYOR, HARVESTER, BARRIER, SPLITTER, GUNNER.
2. can_build_conveyor(pos, dir) when pos is occupied by each.
3. Mutual exclusivity of move() vs build() in the same tick.
4. Build radius (orthogonal Manhattan distance == 1 vs diagonal distance).
"""

from __future__ import annotations

import sys
from fcode import Controller, Direction, EntityType, Position


class Player:
    def __init__(self) -> None:
        self.phase: int = 0

    def run(self, ct: Controller) -> None:
        etype = ct.get_entity_type()
        pos = ct.get_position()

        if etype == EntityType.CORE:
            if ct.get_current_round() == 0 and ct.can_spawn(pos.add(Direction.NORTH)):
                ct.spawn_builder(pos.add(Direction.NORTH))
            return

        if etype != EntityType.BUILDER_BOT:
            return

        round_num = ct.get_current_round()
        t_n = pos.add(Direction.NORTH)
        t_e = pos.add(Direction.EAST)
        t_s = pos.add(Direction.SOUTH)
        t_w = pos.add(Direction.WEST)
        t_ne = pos.add(Direction.NORTHEAST)

        if round_num == 1:
            print("=== PHYS PROBE START ===", file=sys.stderr)
            print(f"Builder Pos: {pos}", file=sys.stderr)
            print(f"Diagonal build check: can_build_conveyor(NORTHEAST) = {ct.can_build_conveyor(t_ne, Direction.NORTH)}", file=sys.stderr)

            # Build CONVEYOR at NORTH
            if ct.can_build_conveyor(t_n, Direction.NORTH):
                ct.build_conveyor(t_n, Direction.NORTH)
                print("Step 1: Built CONVEYOR at NORTH", file=sys.stderr)

        elif round_num == 2:
            # Test CONVEYOR passability & buildability
            print(f"CONVEYOR is_tile_passable: {ct.is_tile_passable(t_n)}", file=sys.stderr)
            print(f"can_build_conveyor ON CONVEYOR: {ct.can_build_conveyor(t_n, Direction.NORTH)}", file=sys.stderr)

            # Build BARRIER at EAST
            if ct.can_build_barrier(t_e):
                ct.build_barrier(t_e)
                print("Step 2: Built BARRIER at EAST", file=sys.stderr)

        elif round_num == 3:
            # Test BARRIER passability & buildability
            print(f"BARRIER is_tile_passable: {ct.is_tile_passable(t_e)}", file=sys.stderr)
            print(f"can_build_conveyor ON BARRIER: {ct.can_build_conveyor(t_e, Direction.NORTH)}", file=sys.stderr)

            # Build SPLITTER at SOUTH
            if ct.can_build_splitter(t_s, Direction.SOUTH):
                ct.build_splitter(t_s, Direction.SOUTH)
                print("Step 3: Built SPLITTER at SOUTH", file=sys.stderr)

        elif round_num == 4:
            # Test SPLITTER passability & buildability
            print(f"SPLITTER is_tile_passable: {ct.is_tile_passable(t_s)}", file=sys.stderr)
            print(f"can_build_conveyor ON SPLITTER: {ct.can_build_conveyor(t_s, Direction.NORTH)}", file=sys.stderr)

            # Build GUNNER at WEST (if ammo/cost permits, else check cost)
            if ct.get_global_resources() >= ct.get_gunner_cost():
                if ct.can_build_gunner(t_w, Direction.WEST):
                    ct.build_gunner(t_w, Direction.WEST)
                    print("Step 4: Built GUNNER at WEST", file=sys.stderr)
            else:
                print(f"Cannot afford GUNNER (cost={ct.get_gunner_cost()}, bank={ct.get_global_resources()})", file=sys.stderr)

        elif round_num == 5:
            if ct.is_in_vision(t_w):
                bid = ct.get_tile_building_id(t_w)
                if bid is not None:
                    print(f"GUNNER/Building at WEST is_tile_passable: {ct.is_tile_passable(t_w)}", file=sys.stderr)
                    print(f"can_build_conveyor ON GUNNER: {ct.can_build_conveyor(t_w, Direction.NORTH)}", file=sys.stderr)

            # Test Mutual Exclusivity: try move() then build() in same turn
            print("Step 5: Testing Move + Build Mutual Exclusivity in same round...", file=sys.stderr)
            can_m = ct.can_move(Direction.SOUTH)
            can_b = ct.can_build_barrier(t_w)
            print(f"Before move: can_move(SOUTH)={can_m}, can_build_barrier(WEST)={can_b}", file=sys.stderr)
            if can_m:
                ct.move(Direction.SOUTH)
                try:
                    ct.build_barrier(t_w)
                    print("RESULT: Move + Build allowed in same round!", file=sys.stderr)
                except Exception as e:
                    print(f"RESULT: Mutual Exclusivity Enforced! Exception after move: {e!r}", file=sys.stderr)
            print("=== PHYS PROBE END ===", file=sys.stderr)
