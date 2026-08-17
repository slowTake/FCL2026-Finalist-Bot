"""Grand_knight — Core Domain: Authoritative Builder Intent Broadcast.

Authors the Core's single authoritative SLOT_BUILDER_INTENT target each turn --
core defense, belt repair, or ore expansion, in that priority order -- with
lease expiry so a stale or unsupported ore commitment gets released rather than
holding builders on a target that discovery no longer backs.
"""

from __future__ import annotations

import sys
from typing import Optional
from fcode import Controller, Position

from config import Config
from blackboard.belt_protocol import belt_alert_is_fresh, unpack_belt_alert
from blackboard.ore_protocol import is_plausible_ore_target
from utils.geometry import (
    get_core_footprint,
    pack_builder_intent,
    unpack_ore_discovery,
    unpack_ore_status,
)


class IntentBroadcaster:
    """Authors SLOT_BUILDER_INTENT with lease tracking and shared-invalidation checks."""

    def __init__(self) -> None:
        self.current_intent_target: Optional[Position] = None
        self.intent_created_round: int = 0
        self.intent_last_supported_round: int = 0

    def broadcast(self, ct: Controller, policy: int) -> None:
        """Author authoritative strategic intent into SLOT_BUILDER_INTENT with lease & invalidation checks."""
        curr_rnd = ct.get_current_round()
        if policy == Config.EXPANSION_LEASHED:
            self.current_intent_target = None
            core_pos = ct.get_position()
            val = pack_builder_intent(core_pos.x, core_pos.y, Config.INTENT_DEFEND_CORE, 15)
            ct.write_store(Config.SLOT_BUILDER_INTENT, val)
            return

        # Belt repair outranks ore-expansion intent below
        val_belt = ct.read_store(Config.SLOT_BELT_ALERT)
        if belt_alert_is_fresh(ct, val_belt):
            belt_pos, _ = unpack_belt_alert(val_belt)
            if belt_pos is not None:
                val = pack_builder_intent(belt_pos.x, belt_pos.y, Config.INTENT_REPAIR, 13)
                ct.write_store(Config.SLOT_BUILDER_INTENT, val)
                return

        val_ore = ct.read_store(Config.SLOT_ORE_DISCOVERY)
        ore_pos, round_mod = unpack_ore_discovery(val_ore)

        val_status = ct.read_store(Config.SLOT_ORE_STATUS)
        inv_pos, inv_status = unpack_ore_status(val_status)

        core_footprint = get_core_footprint(ct)

        # Check shared invalidation
        if inv_pos is not None and ore_pos is not None and inv_pos == ore_pos and inv_status != Config.ORE_STATUS_NONE:
            if Config.DEBUG_ECONOMY:
                print(f"INTENT_CLEAR round={curr_rnd} target={ore_pos} reason=STATUS_{inv_status}", file=sys.stderr)
            ct.write_store(Config.SLOT_ORE_DISCOVERY, 0)
            ore_pos = None

        if ore_pos is not None:
            age = (curr_rnd - round_mod) % 256
            fresh = age <= Config.ORE_DISCOVERY_TTL_ROUNDS
            plausible = is_plausible_ore_target(ct, ore_pos, core_footprint)

            if fresh and plausible:
                if self.current_intent_target == ore_pos:
                    self.intent_last_supported_round = curr_rnd
                else:
                    self.current_intent_target = ore_pos
                    self.intent_created_round = curr_rnd
                    self.intent_last_supported_round = curr_rnd

                val = pack_builder_intent(ore_pos.x, ore_pos.y, Config.INTENT_EXPAND_ORE, 10)
                ct.write_store(Config.SLOT_BUILDER_INTENT, val)
                return
            else:
                reason = "STALE" if not fresh else "IMPLAUSIBLE"
                if Config.DEBUG_ECONOMY:
                    print(f"INTENT_CLEAR round={curr_rnd} target={ore_pos} reason={reason}", file=sys.stderr)
                self.current_intent_target = None
                ct.write_store(Config.SLOT_ORE_DISCOVERY, 0)

        if self.current_intent_target is not None:
            if (curr_rnd - self.intent_last_supported_round) > Config.INTENT_LEASE_TURNS:
                if Config.DEBUG_ECONOMY:
                    print(f"INTENT_CLEAR round={curr_rnd} target={self.current_intent_target} reason=LEASE_EXPIRED", file=sys.stderr)
                self.current_intent_target = None

        ct.write_store(Config.SLOT_BUILDER_INTENT, 0)
