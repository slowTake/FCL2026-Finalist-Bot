"""knight — Entry Point & Unit Execution Router.

Principal Python Systems Architecture for knight RTS bot. Routes per-tick
unit execution into domain-specific Priority Pipelines with SafeController protection,
lazy role instantiation, CPU time safety valves, and global survival exception wrapping.
"""

from __future__ import annotations

import sys
from typing import Any, Optional
from fcode import Controller, Direction, EntityType

from config import Config
from core.ledger import Ledger
from core.macro import CoreMacro
from engine.safe_controller import SafeController
from engine.world import WorldView


class Player:
    """Isolated unit instance state class loaded by fcode engine per unit."""

    def __init__(self) -> None:
        self.safe = SafeController(None)  # type: ignore
        self.world = WorldView(self.safe)
        self.ledger = Ledger()
        self.role_handler: Optional[Any] = None
        self.role_type: Optional[str] = None
        self.tick_counter: int = 0

    def run(self, ct: Controller) -> None:
        """Main execution entry point invoked once per tick per active unit."""
        # CPU Safety Valve: if elapsed CPU time exceeds limit, yield immediately
        if ct.get_cpu_time_elapsed() > Config.CPU_SAFETY_LIMIT_MICROSEC:
            return

        self.safe.begin_turn(ct)

        try:
            self._safe_run(ct)
        except Exception as e:
            import traceback

            print(f"[KNIGHT SURVIVAL] Unit {ct.get_id()} caught exception: {e!r}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            # Survival fallback: step away to prevent fatal inaction
            try:
                if ct.get_entity_type() == EntityType.BUILDER_BOT:
                    if ct.get_move_cooldown() == 0 and not self.safe.major_action_used:
                        for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                            if ct.can_move(d):
                                self.safe.safe_move(d)
                                break
            except Exception:
                pass

    def _safe_run(self, ct: Controller) -> None:
        self.tick_counter += 1

        # Step 1: Refresh cached WorldView and synchronize blackboard Store ledger
        self.world.refresh(ct)
        self.ledger.sync_vision(ct)

        # Step 2: Lazy role resolution on unit activation
        etype = ct.get_entity_type()

        if self.role_handler is None:
            if etype == EntityType.CORE:
                self.role_type = "core"
                self.role_handler = CoreMacro()
            elif etype == EntityType.BUILDER_BOT:
                my_idx = ct.get_id()
                from utils.geometry import get_reserved_builder_ids
                reserved_ids = get_reserved_builder_ids(ct)
                guardian_id = reserved_ids[0] if reserved_ids else 0

                my_team = ct.get_team()
                harvester_count = sum(
                    1 for bid in self.safe.get_nearby_buildings()
                    if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
                )
                threat_alert = ct.read_store(Config.SLOT_ALERTS)

                # Dedicated Guardian role only when base has active harvesters or under attack
                if (harvester_count > 0 or threat_alert > 0) and (my_idx == guardian_id or (guardian_id == 0 and my_idx == 1)):
                    self.role_type = "guardian"
                    from combat.tactics import CombatTactics
                    self.role_handler = CombatTactics(self.safe)
                else:
                    self.role_type = "economy"
                    from logistics.economy import EconomyBuilder
                    self.role_handler = EconomyBuilder(self.safe)
            elif etype in (EntityType.GUNNER, EntityType.SENTINEL):
                self.role_type = "turret"
                from combat.tactics import CombatTactics
                self.role_handler = CombatTactics(self.safe)

        # Step 3: Execute active role pipeline
        if self.role_type == "core":
            self.role_handler.run_core_turn(ct)
        elif self.role_type == "guardian":
            self.role_handler.set_safe_controller(self.safe)
            self.role_handler.run_guardian(ct)
        elif self.role_type == "economy":
            self.role_handler.set_safe_controller(self.safe)
            self.role_handler.run_economy_builder(ct)
        elif self.role_type == "turret":
            self.role_handler.set_safe_controller(self.safe)
            self.role_handler.run_turret(ct)
