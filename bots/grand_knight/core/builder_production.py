"""Grand_knight — Core Domain: Builder Population & Gate Cycling Spawner.

Computes the Ti-tiered target builder population, spawns Builder Bots on an
open Core-adjacent tile when affordable, executes Gate Cycling (demolish the
designated gate barrier and retry next tick) when every spawn tile is blocked,
and broadcasts the dynamic Guardian/Reserve role assignment on each spawn.
"""

from __future__ import annotations

import sys
from fcode import Controller, Direction, EntityType, GameConstants, Position

from config import Config
from engine.safe_controller import SafeController
from utils.geometry import pack_role_assignment


class BuilderProductionManager:
    """Evaluates Ti affordability and spawns/role-assigns Builder Bots each Core turn."""

    def __init__(self, safe: SafeController) -> None:
        self.safe = safe
        self.spawn_count: int = 0
        self.guardian_builder_id: int = 0
        self.reserve_builder_id: int = 0

    def evaluate(self, ct: Controller) -> None:
        """Evaluate Titanium bank balance and spawn builders if affordable.

        Executes Gate Cycling if all adjacent spawn tiles are blocked by friendly barriers.
        Broadcasts dynamic role assignments to SLOT_ROLE_ASSIGNMENT.
        """
        if ct.get_action_cooldown() != 0:
            return

        living_units = ct.get_unit_count()
        if living_units >= GameConstants.MAX_TEAM_UNITS:
            return

        target_builders = self._get_target_builders(ct)
        builder_cost = ct.get_builder_bot_cost()
        harvester_cost = ct.get_harvester_cost()

        # Bootstrap-only reserve: hold back a harvester's worth of Ti so the
        # very first few builders can each actually afford to plant one, not
        # just exist. Unconditional past the opening -- reserving a full
        # harvester_cost out of every single spawn decision for the entire
        # match means affording one more builder needs builder_cost +
        # harvester_cost banked simultaneously, which a bank that's merely
        # thin (not empty) can spend a very long time never clearing, even
        # though the bank alone would cover the builder fine. Scaled by map
        # size like the population ramp above, not a harvester count --
        # same reasoning as _get_target_builders.
        map_scale = max(ct.get_map_width(), ct.get_map_height())
        bootstrap_round = Config.BUILDER_RAMP_ROUNDS_PER_TILE * map_scale
        bank_floor = harvester_cost if ct.get_current_round() < bootstrap_round else 0

        curr_ti = ct.get_global_resources()

        # Check affordability
        if living_units < target_builders and curr_ti >= builder_cost and (curr_ti - builder_cost) >= bank_floor:
            core_pos = ct.get_position()
            # 2x2 footprint adjacent spawn candidates
            spawn_candidates = [
                core_pos.add(Direction.NORTH),
                Position(core_pos.x + 1, core_pos.y).add(Direction.NORTH),
                core_pos.add(Direction.WEST),
                Position(core_pos.x, core_pos.y + 1).add(Direction.WEST),
                Position(core_pos.x + 1, core_pos.y).add(Direction.EAST),
                Position(core_pos.x + 1, core_pos.y + 1).add(Direction.EAST),
                Position(core_pos.x, core_pos.y + 1).add(Direction.SOUTH),
                Position(core_pos.x + 1, core_pos.y + 1).add(Direction.SOUTH),
            ]

            spawned_uid = None

            # Try direct spawn on open tile via SafeController
            for spawn_pos in spawn_candidates:
                if ct.can_spawn(spawn_pos):
                    outcome = self.safe.safe_spawn_builder(spawn_pos)
                    if outcome.success:
                        spawned_uid = outcome.value
                        self.spawn_count += 1
                        break

            # Gate Cycling: If all spawn tiles blocked, demolish barrier at gate tile and return to spawn next tick
            if spawned_uid is None:
                gate_pos = spawn_candidates[0]  # Designated gate tile
                bid = ct.get_tile_building_id(gate_pos)
                if bid is not None and ct.get_entity_type(bid) == EntityType.BARRIER:
                    destroy_outcome = self.safe.safe_destroy(gate_pos)
                    if destroy_outcome.success:
                        if Config.DEBUG_ECONOMY:
                            print(f"GATE_CYCLE_DESTROY pos={gate_pos} round={ct.get_current_round()}", file=sys.stderr)
                        return

            # Update dynamic role assignment on spawn
            if spawned_uid is not None and spawned_uid > 0:
                if self.guardian_builder_id == 0:
                    self.guardian_builder_id = spawned_uid
                elif self.reserve_builder_id == 0:
                    self.reserve_builder_id = spawned_uid

                ct.write_store(
                    Config.SLOT_ROLE_ASSIGNMENT,
                    pack_role_assignment(self.guardian_builder_id, self.reserve_builder_id),
                )

    def _get_target_builders(self, ct: Controller) -> int:
        """Target population ramps on a fixed round schedule, scaled by map
        size, instead of reacting to current Titanium.

        A bank-tiered target sounds more precise, but a temporary dip
        collapses the target below the population already built up --
        since spawning only fires while living < target, that freezes
        growth until the bank claws all the way back to whatever bracket
        it fell from, even if a single lost builder just needs replacing.
        Round progress relative to map size doesn't have that failure mode
        and doesn't need a global economy signal at all -- affordability is
        still checked separately by the caller, so this only ever governs
        how big a population is worth wanting, never whether we can afford
        it right now.
        """
        map_scale = max(ct.get_map_width(), ct.get_map_height())
        rounds_per_tier = map_scale * Config.BUILDER_RAMP_ROUNDS_PER_TILE
        curr_round = ct.get_current_round()

        if curr_round >= rounds_per_tier * 5:
            target = Config.MAX_BUILDERS_CEILING  # 28
        elif curr_round >= rounds_per_tier * 4:
            target = 20
        elif curr_round >= rounds_per_tier * 3:
            target = 14
        elif curr_round >= rounds_per_tier * 2:
            target = 10
        elif curr_round >= rounds_per_tier:
            target = 6
        else:
            target = Config.MAX_BUILDERS_EARLY  # 4

        return min(target, 28)
