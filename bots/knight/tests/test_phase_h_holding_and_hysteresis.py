"""knight — Unit Tests for Phase H: Intent Latching, Holding Gates, and Retreat Hysteresis."""

from __future__ import annotations

import unittest
from fcode import Direction, EntityType, Environment, Position

from config import Config
from combat.tactics import CombatTactics
from engine.safe_controller import SafeController
from engine.step_result import StepResult
from logistics.economy import EconomyBuilder
from tests.mock_controller import MockController


class TestPhaseHHoldingGates(unittest.TestCase):
    """Test stationary holding gates when builders are waiting on cooldown or resources."""

    def setUp(self) -> None:
        self.mock = MockController(unit_id=2, pos=Position(5, 5), map_width=30, map_height=30)
        self.safe = SafeController(self.mock)
        self.economy = EconomyBuilder(self.safe)
        # Mock Core at (2, 2)
        self.mock.store[Config.SLOT_CORE_POS] = (3 << 16) | 3  # (2, 2) packed

    def test_holding_on_insufficient_resources_for_barrier(self) -> None:
        # Set tactical mode to Balanced so barrier ring wants to build
        self.mock.store[Config.SLOT_TACTICAL_MODE] = 1
        # Target barrier tile at (1, 2), place bot adjacent at (1, 1)
        self.mock.pos = Position(1, 1)
        self.mock.global_resources = 0  # Not enough for barrier (cost 2)

        self.safe.begin_turn(self.mock)
        result = self.economy._try_build_barrier_ring(self.mock)
        self.assertTrue(result)
        self.assertEqual(self.economy.hold_turns, 1)
        self.assertEqual(self.economy.hold_reason, "NO_RESOURCES")
        # Ensure no movement occurred
        self.assertEqual(len(self.mock.moves_executed), 0)

    def test_holding_on_action_cooldown(self) -> None:
        self.mock.store[Config.SLOT_TACTICAL_MODE] = 1
        self.mock.pos = Position(1, 1)
        self.mock.global_resources = 100
        self.mock.action_cooldown = 1  # On cooldown

        self.safe.begin_turn(self.mock)
        result = self.economy._try_build_barrier_ring(self.mock)
        self.assertTrue(result)
        self.assertEqual(self.economy.hold_turns, 1)
        self.assertEqual(self.economy.hold_reason, "COOLDOWN")
        self.assertEqual(len(self.mock.moves_executed), 0)

    def test_holding_suppresses_exploration_fallthrough(self) -> None:
        self.mock.store[Config.SLOT_TACTICAL_MODE] = 1
        self.mock.pos = Position(1, 1)
        self.mock.global_resources = 0
        self.mock.set_tile_env(Position(1, 2), Environment.ORE_TITANIUM)
        self.economy.target_ore = Position(1, 2)

        self.safe.begin_turn(self.mock)
        self.economy.run_economy_builder(self.mock)

        # Unit should be holding and not executing an exploration walk
        self.assertEqual(len(self.mock.moves_executed), 0)
        self.assertEqual(self.economy.hold_turns, 1)

    def test_hold_expires_after_max_turns(self) -> None:
        self.mock.store[Config.SLOT_TACTICAL_MODE] = 1
        self.mock.pos = Position(1, 1)
        self.mock.global_resources = 0

        target_barrier = Position(1, 2)
        # Advance turns up to HOLD_MAX_TURNS
        for turn in range(1, Config.HOLD_MAX_TURNS + 1):
            self.safe.begin_turn(self.mock)
            step_res = self.economy._hold_position(self.mock, target_barrier, "NO_RESOURCES")
            self.assertEqual(step_res, StepResult.HOLDING)

        # Next turn exceeds HOLD_MAX_TURNS -> FAILED
        self.safe.begin_turn(self.mock)
        step_res_expired = self.economy._hold_position(self.mock, target_barrier, "NO_RESOURCES")
        self.assertEqual(step_res_expired, StepResult.FAILED)
        self.assertIsNone(self.economy.hold_target)


class TestPhaseHRetreatHysteresis(unittest.TestCase):
    """Test sticky retreat hysteresis upon damage or threat sighting."""

    def setUp(self) -> None:
        self.mock = MockController(unit_id=3, pos=Position(15, 15), map_width=30, map_height=30)
        self.safe = SafeController(self.mock)
        self.economy = EconomyBuilder(self.safe)
        self.mock.store[Config.SLOT_CORE_POS] = (3 << 16) | 3  # (2, 2) packed

    def test_low_hp_triggers_sticky_retreat(self) -> None:
        self.mock.hp = Config.RETREAT_HP_THRESHOLD - 1  # Low HP
        self.safe.begin_turn(self.mock)

        result = self.economy._try_evade_threat(self.mock)
        self.assertTrue(result)
        self.assertEqual(self.economy.retreat_timer, Config.RETREAT_STICKY_TURNS - 1)
        self.assertIsNotNone(self.economy.retreat_target)

    def test_retreat_persists_while_timer_positive(self) -> None:
        self.economy.retreat_timer = 3
        self.economy.retreat_target = Position(2, 2)
        self.mock.hp = 50  # HP recovered, but timer still running

        self.safe.begin_turn(self.mock)
        result = self.economy._try_evade_threat(self.mock)
        self.assertTrue(result)
        self.assertEqual(self.economy.retreat_timer, 2)
        self.assertIsNotNone(self.economy.retreat_target)

    def test_retreat_clears_after_timer_and_hp_secure(self) -> None:
        self.economy.retreat_timer = 1
        self.economy.retreat_target = Position(2, 2)
        self.economy.last_threat_round = 10
        self.mock.current_round = 30
        self.mock.hp = Config.RETREAT_CLEAR_HP + 5

        self.safe.begin_turn(self.mock)
        result = self.economy._try_evade_threat(self.mock)
        self.assertTrue(result)
        self.assertEqual(self.economy.retreat_timer, 0)
        self.assertIsNone(self.economy.retreat_target)


class TestPhaseHDefenseCommitment(unittest.TestCase):
    """Test Guardian defense commitment inside the Core apron."""

    def test_guardian_defense_commit_inside_core_apron(self) -> None:
        mock = MockController(unit_id=1, pos=Position(2, 2), map_width=30, map_height=30)
        safe = SafeController(mock)
        guardian = CombatTactics(safe)

        # Tactical Mode 2 (All-out defense)
        mock.store[Config.SLOT_TACTICAL_MODE] = 2
        mock.store[Config.SLOT_CORE_POS] = (3 << 16) | 3  # (2, 2) packed

        safe.begin_turn(mock)
        guardian.run_guardian(mock)

        # Ensure Guardian does not switch to economy exploration fallback
        self.assertIsNone(guardian.economy_fallback)


if __name__ == "__main__":
    unittest.main()
