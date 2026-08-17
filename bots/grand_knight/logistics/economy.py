from __future__ import annotations

from enum import Enum
import sys
from typing import Optional
from fcode import Controller, EntityType, Position

from behavior.movement_skill import MovementSkill
from combat.barriers import ApronDefenseSkill
from combat.dismantle import try_dismantle_adjacent_hostile
from combat.smart_barriers import try_block_gunner_lines
from config import Config
from engine.retreat_timer import RetreatTimer
from engine.safe_controller import SafeController
from exploration.frontier import FrontierExplorer
from exploration.scout_walk import ScoutWalk
from exploration.spread_zone import SpreadZone, assign_spread_zone
from exploration.symmetry import SymmetryHints
from logistics.belt_memory import BeltMemory
from logistics.belt_network import BeltNetwork
from logistics.belt_repair import BeltRepairSkill
from logistics.expansion import ExpansionSkill
from logistics.ore_targeting import OreTargeting
from logistics.patrol import PatrolSkill
from logistics.raid_skill import RaidSkill
from spatial.movement import PathCache, execute_bug2_evasion
from utils.geometry import get_core_footprint, get_reserved_builder_ids, is_adjacent
from utils.support import SupportSkill


class EconomyRoleState(str, Enum):
    """Explicit lifecycle states for Economy Builder Bots."""
    IDLE = "IDLE"
    SCOUTING = "SCOUTING"
    COMMITTING = "COMMITTING"
    BUILDING_HARVESTER = "BUILDING_HARVESTER"
    PAVING_BELT = "PAVING_BELT"
    RELAY_COMPLETE = "RELAY_COMPLETE"
    EXPANDING_AGAIN = "EXPANDING_AGAIN"
    DEFENDING = "DEFENDING"
    RETREATING = "RETREATING"


class EconomyBuilder:
    """Logistics and economic expansion state machine for Builder Bots."""

    def __init__(self, safe: Optional[SafeController] = None) -> None:
        self.safe = safe or SafeController(None)  # type: ignore
        self.support = SupportSkill(self.safe)
        self.mover = MovementSkill(self.safe)
        self.ore_targeting = OreTargeting(self.safe)
        self.belt_repair = BeltRepairSkill(self.safe)
        self.belt_memory = BeltMemory()
        self.belt_network = BeltNetwork(self.belt_memory)
        self.patrol = PatrolSkill(self.safe, self.belt_memory, self.belt_repair)
        self.role_state: EconomyRoleState = EconomyRoleState.IDLE

        self.retreat = RetreatTimer()

        self.last_pos: Optional[Position] = None
        self.stuck_turns: int = 0
        self.seen_walls: set[Position] = set()
        self.pos_history: list[Position] = []
        self.path_cache = PathCache()
        self.current_step: str = "init"
        self.flexed_to_defense: bool = False
        self.unshackled_logged: bool = False

        self.frontier = FrontierExplorer()
        self.symmetry = SymmetryHints()
        self.spread_zone: Optional[SpreadZone] = None
        self.is_scouting: bool = False
        self.no_ore_turns: int = 0
        self.scout_walk = ScoutWalk(self.safe, self.support, self.belt_repair, self.frontier, self.symmetry)

        self.expansion = ExpansionSkill(self.safe, self.belt_network, self.belt_memory, self.ore_targeting)
        self.apron_defense = ApronDefenseSkill(self.safe)
        self.raid = RaidSkill(self.safe, self.belt_network)

    def set_safe_controller(self, safe: SafeController) -> None:
        """Inject active SafeController instance."""
        self.safe = safe
        self.support.safe = safe
        self.mover.safe = safe
        self.ore_targeting.safe = safe
        self.belt_repair.safe = safe
        self.patrol.safe = safe
        self.scout_walk.safe = safe
        self.expansion.safe = safe
        self.apron_defense.safe = safe
        self.raid.safe = safe

    def _transition_state(self, ct: Controller, new_state: EconomyRoleState) -> None:
        """Record state transition and emit structured telemetry."""
        if self.role_state != new_state:
            if Config.DEBUG_ECONOMY:
                print(
                    f"ROLE_STATE_CHANGE builder_id={ct.get_id()} old={self.role_state.value} "
                    f"new={new_state.value} round={ct.get_current_round()}",
                    file=sys.stderr,
                )
            self.role_state = new_state

    def run_economy_builder(self, ct: Controller) -> None:
        """Main tick execution for Economy Builder Bot."""
        self.safe.begin_turn(ct)
        pos = ct.get_position()
        curr_rnd = ct.get_current_round()
        my_id = ct.get_id()

        if self.last_pos == pos:
            if ct.get_move_cooldown() == 0:
                self.stuck_turns += 1
        else:
            self.stuck_turns = 0
        self.last_pos = pos

        if not self.pos_history or self.pos_history[-1] != pos:
            self.pos_history.append(pos)
            if len(self.pos_history) > 10:
                self.pos_history.pop(0)

        self.frontier.observe(ct)

        if self.spread_zone is None:
            self.spread_zone = assign_spread_zone(my_id)
            if Config.DEBUG_ECONOMY:
                print(
                    f"SPREAD_ZONE_ASSIGNED builder_id={my_id} quadrant={self.spread_zone.quadrant} "
                    f"band={self.spread_zone.band} round={curr_rnd}",
                    file=sys.stderr,
                )

        core_footprint = get_core_footprint(ct)
        core_tile = core_footprint[0]
        self.symmetry.initialize(ct, core_tile)

        self.ore_targeting.scan_and_broadcast(ct, self.seen_walls)

        # Dismantle any adjacent enemy weapon/turret/unit immediately
        if try_dismantle_adjacent_hostile(ct, self.safe):
            self.stuck_turns = 0
            return

        policy = ct.read_store(Config.SLOT_EXPANSION_POLICY)
        dist_sq_to_core = pos.distance_squared(core_tile)
        reserved_ids = get_reserved_builder_ids(ct)
        reserved_builder = my_id in reserved_ids
        near_core = dist_sq_to_core <= (Config.CORE_LEASH_WIDE ** 2)

        # Step away if damaged or facing lethal turret fire
        if self._try_evade_threat(ct):
            self.current_step = "evading"
            self.stuck_turns = 0
            return

        # Strategic Smart Barriers: Block enemy gunner lines if near base
        if near_core and try_block_gunner_lines(ct, self.safe, core_footprint):
            self.stuck_turns = 0
            return

        global_flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        need_scouts = bool(global_flags & Config.FLAG_NEED_SCOUTS)
        emergency_exp = bool(global_flags & Config.FLAG_EMERGENCY_EXPLORATION)
        emergency_recall = bool(global_flags & Config.FLAG_CORE_THREAT) and (dist_sq_to_core <= (Config.CORE_LEASH_TIGHT ** 2))

        # Allow reserved builders to scout after ALLOW_RESERVED_SCOUT_AFTER_ROUND in emergency
        if emergency_exp and curr_rnd >= Config.ALLOW_RESERVED_SCOUT_AFTER_ROUND:
            reserved_builder = False

        # A reserved builder under EXPANSION_LEASHED aborts its own
        # commitment rather than waiting on another role to release it
        my_team = ct.get_team()
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )

        if policy == Config.EXPANSION_LEASHED and self.expansion.committed_ore is not None and harvester_count > 0:
            if reserved_builder or emergency_recall:
                if not (is_adjacent(pos, self.expansion.committed_ore) and ct.can_act() and ct.get_global_resources() >= ct.get_harvester_cost()):
                    if Config.DEBUG_ECONOMY:
                        print(
                            f"EXPANSION_ABORT builder_id={my_id} ore={self.expansion.committed_ore} "
                            f"round={curr_rnd} policy={policy}",
                            file=sys.stderr,
                        )
                    self.expansion.abort_commitment(ct)
            else:
                if Config.DEBUG_ECONOMY and (curr_rnd % 50 == 0):
                    print(
                        f"ECONOMY_CONTINUATION builder_id={my_id} round={curr_rnd} "
                        f"policy={policy} ore={self.expansion.committed_ore}",
                        file=sys.stderr,
                    )

        # Opportunistic belt repair if adjacent to any broken conveyor gap
        if self.belt_repair.try_repair_adjacent_belt(ct, core_footprint):
            self.stuck_turns = 0
            return
        else:
            self.flexed_to_defense = False
            self.unshackled_logged = False

        # Soft check: if an existing harvester is disconnected, prioritize completing its belt line
        self.expansion.try_rescue_disconnected_harvester(ct, core_footprint, self.seen_walls)

        if not self.expansion.harvester_built:
            if self.expansion.target_ore is None:
                self.no_ore_turns += 1
                should_scout = (
                    self.no_ore_turns > 20
                    or need_scouts
                    or emergency_exp
                    or (my_id in (3, 4) and max(ct.get_map_width(), ct.get_map_height()) > 20)
                )
                if should_scout and not self.is_scouting:
                    self.is_scouting = True
                    if Config.DEBUG_ECONOMY:
                        reason = "emergency" if emergency_exp else ("need_scouts" if need_scouts else "no_ore")
                        print(f"SCOUT_ASSIGNED builder_id={my_id} round={curr_rnd} reason={reason}", file=sys.stderr)
            else:
                self.no_ore_turns = 0
                self.is_scouting = False

        # Opportunistic raid: nothing economy-critical committed yet this tick,
        # and Core has signalled the economy can spare a builder -- check for a
        # nearby undefended enemy Harvester before falling into aimless
        # wandering. Must run before try_mine_ore: when target_ore is None,
        # that function's own fallback is scout_walk, which reliably takes
        # some action and would otherwise consume the tick first.
        if (
            self.expansion.target_ore is None
            and not self.expansion.harvester_built
            and bool(global_flags & Config.FLAG_OFFENSE_READY)
        ):
            if self.raid.try_hijack(ct, pos, self.seen_walls, self.pos_history, self.expansion):
                self.current_step = "raiding"
                self.stuck_turns = 0
                return

        belt_handled, self.spread_zone = self.expansion.try_build_belt(ct, self.seen_walls, self.pos_history, self.spread_zone)
        if belt_handled:
            self.current_step = "conveyor"
            self.stuck_turns = 0
            return

        mine_handled, target_aborted = self.expansion.try_mine_ore(
            ct, self.seen_walls, self.pos_history, self.frontier, self.spread_zone, self.stuck_turns
        )
        if mine_handled:
            self.current_step = "mining"
            self.stuck_turns = 0
            return
        if target_aborted:
            self.stuck_turns = 0

        if self.patrol.try_patrol(ct, self.pos_history, self.seen_walls, self.expansion.target_ore is not None, self.is_scouting):
            self.current_step = "patrol_repair"
            self.stuck_turns = 0
            return

        # Flex duty: nothing economy-critical to do this tick, help finish the apron barrier ring
        if self.apron_defense.try_build_barrier_ring(ct, self.seen_walls):
            self.current_step = "flex_defense"
            self.flexed_to_defense = True
            self.stuck_turns = 0
            return

        # True last resort: explore. Kept last (not embedded in try_mine_ore)
        # so patrol/flex-defense/raid always get first refusal, but still runs
        # every idle tick rather than being disabled after some cutoff -- it's
        # what drives the builder's own vision coverage, not just a filler action.
        if self.scout_walk.walk(ct, self.spread_zone, self.pos_history, self.seen_walls):
            self.current_step = "exploring"
            self.stuck_turns = 0
            return

        self.current_step = "idle"

    def _try_evade_threat(self, ct: Controller) -> bool:
        """Priority 1: Sticky retreat hysteresis when damaged or facing hostile turret fire."""
        hp = ct.get_hp()
        pos = ct.get_position()
        core_footprint = get_core_footprint(ct)
        nearest_core = min(core_footprint, key=lambda c: pos.distance_squared(c)) if core_footprint else Position(0, 0)
        dist_sq_to_core = pos.distance_squared(nearest_core)

        # If already at Core and healthy, do not lock in retreat — allow active defense/demolition
        if dist_sq_to_core <= 4 and hp >= Config.RETREAT_HP_THRESHOLD:
            self.retreat.clear()
            return False

        threat_detected = hp < Config.RETREAT_HP_THRESHOLD
        if not threat_detected:
            for bid in self.safe.get_nearby_buildings(Config.BUILDER_BOT_VISION_RADIUS_SQ):
                if ct.get_team(bid) != ct.get_team() and ct.get_entity_type(bid) in (EntityType.GUNNER, EntityType.SENTINEL):
                    threat_detected = True
                    break

        if threat_detected:
            if self.expansion.target_ore is not None:
                self.ore_targeting.report_invalid(ct, self.expansion.target_ore, "UNREACHABLE")
                self.expansion.abort_commitment(ct)
            reason = "HP_LOW" if hp < Config.RETREAT_HP_THRESHOLD else "TURRET_THREAT"
            self.retreat.trigger(ct, nearest_core, reason, Config.RETREAT_STICKY_TURNS)

        if self.retreat.tick(ct, hp, dist_sq_to_core, Config.RETREAT_CLEAR_HP, Config.RETREAT_MEMORY_TURNS):
            if ct.get_move_cooldown() == 0 and self.retreat.target is not None:
                execute_bug2_evasion(ct, self.safe, self.path_cache, pos, self.retreat.target, self.pos_history, self.seen_walls)
            return True

        return False

