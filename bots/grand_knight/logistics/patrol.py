"""Grand_knight — Logistics Domain: Belt Patrol Scheduling.

Activates a locally-known conveyor relay for inspection (on a fresh belt
alert, a patrol-due schedule, or when the builder has no other economic
work), walks its path tile by tile, and repairs or re-verifies it as it goes.
"""

from __future__ import annotations

import sys
from typing import Optional, Set
from fcode import Controller, Position

from blackboard.belt_protocol import belt_alert_is_fresh, unpack_belt_alert
from config import Config
from engine.outcome import Reason
from engine.safe_controller import SafeController
from logistics.belt_memory import BeltMemory, RelayRoute, RelayStatus
from logistics.belt_repair import BeltRepairSkill, TileState
from spatial.movement import PathCache, execute_bug2_evasion
from utils.geometry import get_core_footprint


class PatrolSkill:
    """Selects and advances a builder along a due-for-inspection conveyor relay."""

    def __init__(self, safe: SafeController, belt_memory: BeltMemory, belt_repair: BeltRepairSkill) -> None:
        self.safe = safe
        self.belt_memory = belt_memory
        self.belt_repair = belt_repair
        self.patrol_relay_id: Optional[int] = None
        self.patrol_index: int = 0
        self.path_cache = PathCache()

    def try_patrol(
        self,
        ct: Controller,
        pos_history: list[Position],
        seen_walls: Set[Position],
        has_ore_target: bool,
        is_scouting: bool,
    ) -> bool:
        """Execute patrol inspection and repairs along locally known conveyor relays."""
        if ct.get_hp() < Config.RETREAT_HP_THRESHOLD:
            return False
        if ct.get_cpu_time_elapsed() > Config.CPU_SEARCH_VALVE_MICROSEC:
            return False

        curr_rnd = ct.get_current_round()
        my_id = ct.get_id()
        core_footprint = get_core_footprint(ct)
        flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        repair_needed_flag = bool(flags & Config.FLAG_BELT_REPAIR_NEEDED)

        # 1. Resolve or activate active patrol relay
        relay: Optional[RelayRoute] = None
        if self.patrol_relay_id is not None:
            relay = self.belt_memory.get_relay(self.patrol_relay_id)
            if relay is None or relay.status == RelayStatus.RETIRED:
                self.patrol_relay_id = None
                self.patrol_index = 0
                relay = None

        if relay is None:
            # Check conditions to activate patrol:
            # A. FLAG_BELT_REPAIR_NEEDED is set
            # B. Builder has no active ore target
            # C. Builder is idle/scouting and a relay is patrol-due
            # D. Builder sees a fresh SLOT_BELT_ALERT that matches one of its relays
            belt_alert_raw = ct.read_store(Config.SLOT_BELT_ALERT)
            alert_pos: Optional[Position] = None
            if belt_alert_is_fresh(ct, belt_alert_raw):
                alert_pos, _ = unpack_belt_alert(belt_alert_raw)

            matching_relay: Optional[RelayRoute] = None
            if alert_pos is not None:
                for r in self.belt_memory.active_relays():
                    if alert_pos in r.path:
                        matching_relay = r
                        break

            due_relays = self.belt_memory.patrol_due(curr_rnd, Config.BELT_PATROL_INTERVAL_TURNS)

            if matching_relay is not None:
                relay = matching_relay
            elif (repair_needed_flag or not has_ore_target or is_scouting) and due_relays:
                relay = due_relays[0]

            if relay is not None:
                self.patrol_relay_id = relay.relay_id
                self.patrol_index = 0
                print(
                    f"BELT_PATROL_START builder_id={my_id} relay_id={relay.relay_id} round={curr_rnd}",
                    file=sys.stderr,
                )

        if relay is None:
            return False

        # 2. Advance patrol along relay.path from self.patrol_index
        path_len = len(relay.path)
        if self.patrol_index >= path_len:
            # Reached the end of relay and verified connection to Core
            relay.status = RelayStatus.ACTIVE
            relay.last_verified_round = curr_rnd
            relay.broken_index = None
            print(
                f"BELT_RELAY_VERIFIED builder_id={my_id} relay_id={relay.relay_id} round={curr_rnd}",
                file=sys.stderr,
            )
            self.patrol_relay_id = None
            self.patrol_index = 0
            return True

        target_tile = relay.path[self.patrol_index]
        state = self.belt_repair.inspect_relay_tile(ct, relay, self.patrol_index, core_footprint)

        if state == TileState.OK:
            # Tile is fine, advance index and step toward next tile
            self.patrol_index += 1
            if self.patrol_index < path_len:
                nxt_tile = relay.path[self.patrol_index]
                if ct.get_move_cooldown() == 0:
                    execute_bug2_evasion(ct, self.safe, self.path_cache, ct.get_position(), nxt_tile, pos_history, seen_walls)
            return True

        # Tile is broken / blocked / missing / unknown
        if state != TileState.UNKNOWN:
            relay.status = RelayStatus.BROKEN
            relay.broken_index = self.patrol_index
            print(
                f"BELT_GAP_FOUND builder_id={my_id} relay_id={relay.relay_id} "
                f"tile={target_tile} state={state.name} round={curr_rnd}",
                file=sys.stderr,
            )

        # Attempt repair
        outcome = self.belt_repair.try_repair_tile(
            ct, relay, self.patrol_index, core_footprint, pos_history, seen_walls
        )
        if outcome.success:
            new_state = self.belt_repair.inspect_relay_tile(ct, relay, self.patrol_index, core_footprint)
            if new_state == TileState.OK:
                print(
                    f"BELT_REPAIR_SUCCESS builder_id={my_id} relay_id={relay.relay_id} "
                    f"tile={target_tile} action={outcome.reason.name} round={curr_rnd}",
                    file=sys.stderr,
                )
                self.patrol_index += 1
            return True
        else:
            if outcome.reason not in (Reason.NOT_ADJACENT, Reason.OUT_OF_VISION):
                print(
                    f"BELT_REPAIR_FAIL builder_id={my_id} relay_id={relay.relay_id} "
                    f"tile={target_tile} reason={outcome.reason.name} round={curr_rnd}",
                    file=sys.stderr,
                )
            return True
