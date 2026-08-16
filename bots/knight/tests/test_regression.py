"""knight — Regression & Unit Test Suite.

Tests critical invariants across Safety, Macro, Blackboard, Geometry, and Logistics.
"""

from __future__ import annotations

import unittest
from fcode import Direction, EntityType, Environment, Position

from behavior.movement_skill import MovementSkill
from blackboard.ore_protocol import OreLifecycle
from config import Config
from core.macro import CoreMacro
from engine.outcome import Reason
from engine.safe_controller import SafeController
from engine.world import WorldView
from logistics.belt_repair import BeltRepairSkill
from logistics.economy import EconomyBuilder, EconomyRoleState
from strategy.defense_policy import DefensePolicy
from strategy.economy_policy import EconomyPolicy
from strategy.threat import ThreatAssessment, ThreatClass, ThreatClassifier
from tests.mock_controller import MockController
from utils.geometry import (
    get_core_footprint,
    is_confirmed_ore_target,
    is_plausible_ore_target,
)
from utils.storecoding import (
    pack_builder_intent,
    pack_ore_discovery,
    pack_ore_status,
    pack_position,
    unpack_builder_intent,
    unpack_ore_discovery,
    unpack_ore_status,
    unpack_position,
)


class TestKnightSafetyAndDiscipline(unittest.TestCase):
    """Test SafeController safety invariants, mutual exclusivity, and CPU limits."""

    def setUp(self) -> None:
        self.mock = MockController(unit_id=1, pos=Position(5, 5))
        self.safe = SafeController(self.mock)

    def test_safe_move_success_and_cooldown(self) -> None:
        self.safe.begin_turn(self.mock)
        outcome = self.safe.safe_move(Direction.NORTH)
        self.assertTrue(outcome.success)
        self.assertEqual(self.mock.pos, Position(5, 4))
        self.assertTrue(self.safe.major_action_used)

        # Second action in same tick must be refused
        second = self.safe.safe_move(Direction.SOUTH)
        self.assertFalse(second.success)
        self.assertEqual(second.reason, Reason.ALREADY_ACTED)

    def test_safe_controller_cpu_safety_valve(self) -> None:
        self.mock.cpu_time_elapsed = Config.CPU_SAFETY_LIMIT_MICROSEC + 100
        self.safe.begin_turn(self.mock)
        outcome = self.safe.safe_move(Direction.NORTH)
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.reason, Reason.CPU_BUDGET)

    def test_mutual_exclusivity_between_move_and_build(self) -> None:
        self.safe.begin_turn(self.mock)
        harv_pos = Position(5, 6)
        self.mock.env_grid[(5, 6)] = Environment.ORE_TITANIUM
        outcome_build = self.safe.safe_build_harvester(harv_pos)
        self.assertTrue(outcome_build.success)
        self.assertTrue(self.safe.major_action_used)

        # Move after build in same tick must fail
        outcome_move = self.safe.safe_move(Direction.NORTH)
        self.assertFalse(outcome_move.success)
        self.assertEqual(outcome_move.reason, Reason.ALREADY_ACTED)


class TestStoreCodingAndBlackboard(unittest.TestCase):
    """Test deterministic bitpacking for IPC slots."""

    def test_position_packing_with_zero_coords(self) -> None:
        pos_origin = Position(0, 0)
        packed = pack_position(pos_origin)
        self.assertNotEqual(packed, 0)
        unpacked = unpack_position(packed)
        self.assertEqual(unpacked, pos_origin)

    def test_ore_discovery_freshness(self) -> None:
        ore = Position(14, 22)
        packed = pack_ore_discovery(ore, 42)
        unpacked_pos, rmod = unpack_ore_discovery(packed)
        self.assertEqual(unpacked_pos, ore)
        self.assertEqual(rmod, 42)

    def test_ore_status_invalidation_packing(self) -> None:
        ore = Position(7, 19)
        packed = pack_ore_status(ore, Config.ORE_STATUS_OCCUPIED)
        unpacked_pos, status = unpack_ore_status(packed)
        self.assertEqual(unpacked_pos, ore)
        self.assertEqual(status, Config.ORE_STATUS_OCCUPIED)

    def test_builder_intent_packing(self) -> None:
        packed = pack_builder_intent(12, 18, Config.INTENT_EXPAND_ORE, 10)
        x, y, action, priority = unpack_builder_intent(packed)
        self.assertEqual((x, y), (12, 18))
        self.assertEqual(action, Config.INTENT_EXPAND_ORE)
        self.assertEqual(priority, 10)


class TestOreTargetValidation(unittest.TestCase):
    """Test plausible vs confirmed ore target semantics."""

    def setUp(self) -> None:
        self.mock = MockController(unit_id=2, pos=Position(10, 10))
        self.core_footprint = [Position(0, 0), Position(1, 0), Position(0, 1), Position(1, 1)]

    def test_unseen_tile_is_plausible_but_not_confirmed(self) -> None:
        far_ore = Position(25, 25)
        # Not in vision (dist_sq > 20)
        self.assertTrue(is_plausible_ore_target(self.mock, far_ore, self.core_footprint))
        self.assertFalse(is_confirmed_ore_target(self.mock, far_ore, self.core_footprint))

    def test_visible_ore_with_no_building_is_confirmed(self) -> None:
        near_ore = Position(10, 12)  # dist_sq = 4 <= 20
        self.mock.env_grid[(10, 12)] = Environment.ORE_TITANIUM
        self.assertTrue(is_plausible_ore_target(self.mock, near_ore, self.core_footprint))
        self.assertTrue(is_confirmed_ore_target(self.mock, near_ore, self.core_footprint))

    def test_occupied_ore_is_neither_plausible_nor_confirmed(self) -> None:
        near_ore = Position(10, 12)
        self.mock.env_grid[(10, 12)] = Environment.ORE_TITANIUM
        self.mock.building_grid[(10, 12)] = 999  # existing building
        self.assertFalse(is_plausible_ore_target(self.mock, near_ore, self.core_footprint))
        self.assertFalse(is_confirmed_ore_target(self.mock, near_ore, self.core_footprint))

    def test_ore_in_core_footprint_is_invalid(self) -> None:
        core_ore = Position(0, 0)
        self.assertFalse(is_plausible_ore_target(self.mock, core_ore, self.core_footprint))
        self.assertFalse(is_confirmed_ore_target(self.mock, core_ore, self.core_footprint))


class TestWorldViewMemoization(unittest.TestCase):
    """Test WorldView caching and blackboard reflection."""

    def test_worldview_reflects_store_slots(self) -> None:
        mock = MockController(unit_id=1, pos=Position(2, 2))
        mock.write_store(Config.SLOT_EXPANSION_POLICY, Config.EXPANSION_PARTIAL)
        mock.write_store(Config.SLOT_TACTICAL_MODE, 1)
        mock.write_store(Config.SLOT_GLOBAL_FLAGS, Config.FLAG_NEED_SCOUTS | Config.FLAG_CORE_THREAT)
        mock.write_store(Config.SLOT_TURRET_COUNT, 3)

        safe = SafeController(mock)
        world = WorldView(safe)
        world.refresh(mock)

        self.assertEqual(world.expansion_policy, Config.EXPANSION_PARTIAL)
        self.assertEqual(world.tactical_mode, 1)
        self.assertTrue(world.need_scouts)
        self.assertTrue(world.core_threat)
        self.assertEqual(world.turret_count, 3)


class TestThreatClassification(unittest.TestCase):
    """Test distance-calibrated threat classification."""

    def setUp(self) -> None:
        self.mock = MockController(unit_id=1, entity_type=EntityType.CORE, pos=Position(10, 10))
        self.safe = SafeController(self.mock)
        self.classifier = ThreatClassifier(self.safe)
        self.core_footprint = [Position(10, 10), Position(11, 10), Position(10, 11), Position(11, 11)]

    def test_distant_enemy_scout_is_safe(self) -> None:
        # Enemy builder 9 tiles away: dist_sq = 81 > IGNORE_SCOUT_DIST_SQ (64)
        self.mock.entities[100] = {
            "type": EntityType.BUILDER_BOT,
            "team": 1,
            "pos": Position(19, 10),
            "hp": 25,
            "max_hp": 25,
        }
        self.safe.begin_turn(self.mock)
        assessment = self.classifier.evaluate(self.mock, self.core_footprint)
        self.assertEqual(assessment.classification, ThreatClass.SAFE)
        self.assertFalse(assessment.breach)

    def test_close_enemy_scout_triggers_scout_sighted(self) -> None:
        # Enemy builder 5 tiles away: dist_sq = 25 <= ENEMY_BUILDER_ALERT_DIST_SQ (36)
        self.mock.entities[100] = {
            "type": EntityType.BUILDER_BOT,
            "team": 1,
            "pos": Position(15, 10),
            "hp": 25,
            "max_hp": 25,
        }
        self.safe.begin_turn(self.mock)
        assessment = self.classifier.evaluate(self.mock, self.core_footprint)
        self.assertEqual(assessment.classification, ThreatClass.SCOUT_SIGHTED)
        self.assertFalse(assessment.breach)

    def test_armed_unit_perimeter_pressure(self) -> None:
        # Enemy gunner 6 tiles away: dist_sq = 36 <= ARMED_ALERT_DIST_SQ (49)
        self.mock.entities[100] = {
            "type": EntityType.GUNNER,
            "team": 1,
            "pos": Position(16, 10),
            "hp": 30,
            "max_hp": 30,
        }
        self.safe.begin_turn(self.mock)
        assessment = self.classifier.evaluate(self.mock, self.core_footprint)
        self.assertEqual(assessment.classification, ThreatClass.PERIMETER_PRESSURE)
        self.assertFalse(assessment.breach)

    def test_armed_unit_close_range_triggers_breach(self) -> None:
        # Enemy gunner 3 tiles away: dist_sq = 9 <= ARMED_BREACH_DIST_SQ (16)
        self.mock.entities[100] = {
            "type": EntityType.GUNNER,
            "team": 1,
            "pos": Position(13, 10),
            "hp": 30,
            "max_hp": 30,
        }
        self.safe.begin_turn(self.mock)
        assessment = self.classifier.evaluate(self.mock, self.core_footprint)
        self.assertEqual(assessment.classification, ThreatClass.BREACH)
        self.assertTrue(assessment.breach)


class TestMultiTurnScenarios(unittest.TestCase):
    """Test multi-turn economy and policy progression."""

    def test_ore_commitment_and_relay_completion(self) -> None:
        mock = MockController(unit_id=2, pos=Position(5, 5))
        safe = SafeController(mock)
        builder = EconomyBuilder(safe)
        ore = Position(5, 7)
        mock.env_grid[(5, 7)] = Environment.ORE_TITANIUM

        # 1. Commit to ore
        builder.committed_ore = ore
        builder.target_ore = ore
        builder._transition_state(mock, EconomyRoleState.COMMITTING)
        self.assertEqual(builder.role_state, EconomyRoleState.COMMITTING)

        # 2. Build Harvester
        mock.pos = Position(5, 6)
        safe.begin_turn(mock)
        res = safe.safe_build_harvester(ore)
        self.assertTrue(res.success)
        builder.harvester_built = True
        builder._transition_state(mock, EconomyRoleState.PAVING_BELT)
        self.assertEqual(builder.role_state, EconomyRoleState.PAVING_BELT)

        # 3. Relay complete transition to EXPANDING_AGAIN
        builder.relay_completed = True
        builder._transition_state(mock, EconomyRoleState.RELAY_COMPLETE)
        builder._transition_state(mock, EconomyRoleState.EXPANDING_AGAIN)
        self.assertEqual(builder.role_state, EconomyRoleState.EXPANDING_AGAIN)

    def test_core_detects_armed_pressure_sets_partial(self) -> None:
        mock = MockController(unit_id=1, entity_type=EntityType.CORE, pos=Position(10, 10))
        mock.entities[99] = {"type": EntityType.GUNNER, "team": 1, "pos": Position(16, 10), "hp": 30, "max_hp": 30}
        safe = SafeController(mock)
        macro = CoreMacro(safe)

        tactical_mode = macro._evaluate_threats_and_tactical_mode(mock)
        self.assertEqual(tactical_mode, 1)

        # Mode 1 with 100% HP allows EXPANSION_FREE / EXPANSION_PARTIAL
        policy = macro._evaluate_expansion_policy(mock, tactical_mode)
        self.assertIn(policy, (Config.EXPANSION_FREE, Config.EXPANSION_PARTIAL))


class TestEdgeCasesAndSentinels(unittest.TestCase):
    """Test boundary coordinates, 0-value sentinels, and TTL boundaries."""

    def test_position_zero_zero_roundtrip(self) -> None:
        p0 = Position(0, 0)
        packed = pack_position(p0)
        self.assertGreater(packed, 0)
        self.assertEqual(unpack_position(packed), p0)

    def test_ore_at_map_boundaries(self) -> None:
        p_corner = Position(29, 29)
        packed = pack_position(p_corner)
        self.assertEqual(unpack_position(packed), p_corner)

        p_edge = Position(0, 29)
        packed_edge = pack_position(p_edge)
        self.assertEqual(unpack_position(packed_edge), p_edge)

    def test_store_zero_sentinel_vs_valid_value(self) -> None:
        # 0 must unpack to None
        self.assertIsNone(unpack_position(0))
        pos_none, rmod = unpack_ore_discovery(0)
        self.assertIsNone(pos_none)
        self.assertEqual(rmod, 0)

        pos_st, st = unpack_ore_status(0)
        self.assertIsNone(pos_st)
        self.assertEqual(st, 0)

    def test_discovery_freshness_at_exact_ttl_boundary(self) -> None:
        pos = Position(14, 18)
        disc_round = 100
        packed = pack_ore_discovery(pos, disc_round)
        unpacked_pos, rmod = unpack_ore_discovery(packed)
        self.assertEqual(unpacked_pos, pos)
        self.assertEqual(rmod, disc_round % 256)


class TestRegressionGuards(unittest.TestCase):
    """Ensure engine discipline invariants cannot be bypassed."""

    def test_cpu_search_valve_triggers_budget_refusal(self) -> None:
        mock = MockController(unit_id=2, pos=Position(5, 5))
        mock.cpu_time_elapsed = Config.CPU_SAFETY_LIMIT_MICROSEC + 50
        safe = SafeController(mock)
        safe.begin_turn(mock)
        res = safe.safe_move(Direction.NORTH)
        self.assertFalse(res.success)
        self.assertEqual(res.reason, Reason.CPU_BUDGET)

    def test_consecutive_action_prevention(self) -> None:
        mock = MockController(unit_id=2, pos=Position(5, 5))
        safe = SafeController(mock)
        safe.begin_turn(mock)
        mock.env_grid[(5, 6)] = Environment.ORE_TITANIUM
        res1 = safe.safe_build_harvester(Position(5, 6))
        self.assertTrue(res1.success)

        # Consecutive action in same tick must return Reason.ALREADY_ACTED
        res2 = safe.safe_build_conveyor(Position(5, 4), Direction.SOUTH)
        self.assertFalse(res2.success)
        self.assertEqual(res2.reason, Reason.ALREADY_ACTED)


class TestStrategyAndMovementSkills(unittest.TestCase):
    """Test policy logic and MovementSkill coordinators."""

    def test_defense_policy_defenders_needed_scaling(self) -> None:
        safe_threat = ThreatAssessment(ThreatClass.SAFE, 0, None, 1000, 0, 0, False)
        self.assertEqual(DefensePolicy.defenders_needed(safe_threat, builder_count=4), 1)

        breach_threat = ThreatAssessment(ThreatClass.BREACH, 5, Position(10, 10), 4, 2, 1, True)
        self.assertGreaterEqual(DefensePolicy.defenders_needed(breach_threat, builder_count=6), 2)

    def test_defense_policy_turret_priority_timing(self) -> None:
        safe_threat = ThreatAssessment(ThreatClass.SAFE, 0, None, 1000, 0, 0, False)
        # Prioritize turret if round >= TURRET_PRIORITY_ROUND (80)
        prio, reason = DefensePolicy.should_prioritize_turret(turret_count=0, barrier_count=1, threat=safe_threat, round_num=85)
        self.assertTrue(prio)
        self.assertEqual(reason, "PRIORITY_ROUND")

    def test_economy_policy_leash_preservation(self) -> None:
        safe_threat = ThreatAssessment(ThreatClass.SAFE, 0, None, 1000, 0, 0, False)
        # Non-reserved far builder is NOT leashed
        should_leash = EconomyPolicy.should_leash_builder(
            my_id=5, reserved_ids=(1, 2), threat=safe_threat, near_core=False, need_scouts=False
        )
        self.assertFalse(should_leash)

    def test_economy_policy_scout_triggers(self) -> None:
        self.assertTrue(EconomyPolicy.should_scout(no_ore_turns=25, need_scouts=False, emergency_exp=False, builder_id=5, map_dim=25))
        self.assertTrue(EconomyPolicy.should_scout(no_ore_turns=2, need_scouts=True, emergency_exp=False, builder_id=5, map_dim=25))

    def test_movement_skill_step_greedy(self) -> None:
        mock = MockController(unit_id=2, pos=Position(5, 5))
        safe = SafeController(mock)
        mover = MovementSkill(safe)
        safe.begin_turn(mock)
        res = mover.step_greedy(Position(5, 5), Position(5, 4))
        self.assertTrue(res.success)
        self.assertEqual(mock.pos, Position(5, 4))

    def test_movement_skill_retreat_inside(self) -> None:
        mock = MockController(unit_id=2, pos=Position(5, 5))
        safe = SafeController(mock)
        mover = MovementSkill(safe)
        core_footprint = [Position(0, 0), Position(1, 0), Position(0, 1), Position(1, 1)]
        safe.begin_turn(mock)
class TestBeltRepair(unittest.TestCase):
    """Test autonomous conveyor gap detection and repair."""

    def test_opportunistic_adjacent_belt_repair(self) -> None:
        mock = MockController(unit_id=2, pos=Position(5, 5))
        safe = SafeController(mock)
        repair = BeltRepairSkill(safe)
        core_footprint = [Position(0, 0), Position(1, 0), Position(0, 1), Position(1, 1)]

        # Broken belt gap at (5, 6) with upstream conveyor at (5, 7) facing NORTH (into 5, 6)
        mock.building_grid[(5, 7)] = 2001
        mock.entities[2001] = {
            "type": EntityType.CONVEYOR,
            "team": 0,
            "pos": Position(5, 7),
            "direction": Direction.NORTH,
            "hp": 10,
            "max_hp": 10,
        }

        safe.begin_turn(mock)
        repaired = repair.try_repair_adjacent_belt(mock, core_footprint)
        self.assertTrue(repaired)
        self.assertIn((5, 6), mock.building_grid)


if __name__ == "__main__":
    unittest.main()
