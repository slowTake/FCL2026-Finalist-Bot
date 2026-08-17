"""Grand_knight — Unit Tests for Phases GK0-GK4.

Validates MovementSkill safety, holding gates, sticky retreat, core macro outputs,
ore blacklist, Steiner belt grafting, and belt repair mechanics.
"""

from __future__ import annotations

import unittest
from fcode import Direction, EntityType, Environment, Position

from behavior.movement_skill import MovementSkill
from combat.tactics import CombatTactics
from config import Config
from core.macro import CoreMacro
from engine.safe_controller import SafeController
from engine.step_result import StepResult
from logistics.belt_memory import BeltMemory, RelayStatus
from logistics.belt_network import BeltNetwork
from logistics.belt_repair import BeltRepairSkill, TileState
from logistics.economy import EconomyBuilder
from tests.mock_controller import MockController


class TestGrandKnightMovementSkill(unittest.TestCase):
    """1 & 2. MovementSkill uses SafeController only, no raw ct.move."""

    def setUp(self) -> None:
        self.mock = MockController(unit_id=2, pos=Position(5, 5), map_width=30, map_height=30)
        self.safe = SafeController(self.mock)
        self.mover = MovementSkill(self.safe)

    def test_movement_skill_uses_safe_controller(self) -> None:
        self.safe.begin_turn(self.mock)
        res = self.mover.move_towards(self.mock, Position(5, 5), Position(5, 7))
        self.assertEqual(res, StepResult.ACTED)
        self.assertEqual(len(self.mock.moves_executed), 1)
        self.assertTrue(self.safe.movement_used)

    def test_movement_skill_respects_cooldown(self) -> None:
        self.mock.move_cooldown = 2
        self.safe.begin_turn(self.mock)
        res = self.mover.move_towards(self.mock, Position(5, 5), Position(5, 7))
        self.assertEqual(res, StepResult.HOLDING)
        self.assertEqual(len(self.mock.moves_executed), 0)


class TestGrandKnightHoldingAndRetreat(unittest.TestCase):
    """3 & 4. Holding gate prevents fallthrough; Retreat latch suppresses exploration."""

    def setUp(self) -> None:
        self.mock = MockController(unit_id=2, pos=Position(1, 1), map_width=30, map_height=30)
        self.safe = SafeController(self.mock)
        self.economy = EconomyBuilder(self.safe)
        self.mock.store[Config.SLOT_CORE_POS] = (3 << 16) | 3  # (2, 2) packed

    def test_holding_gate_on_insufficient_titanium(self) -> None:
        self.mock.store[Config.SLOT_TACTICAL_MODE] = 1
        self.mock.pos = Position(1, 1)
        self.mock.global_resources = 0
        self.mock.set_tile_env(Position(1, 2), Environment.ORE_TITANIUM)
        self.economy.expansion.target_ore = Position(1, 2)

        self.safe.begin_turn(self.mock)
        res, _ = self.economy.expansion.try_mine_ore(
            self.mock, self.economy.seen_walls, self.economy.pos_history,
            self.economy.frontier, self.economy.spread_zone, self.economy.stuck_turns,
        )
        self.assertTrue(res)
        self.assertEqual(self.economy.expansion.hold_gate.turns, 1)
        self.assertEqual(self.economy.expansion.hold_gate.reason, "NO_RESOURCES")
        self.assertEqual(len(self.mock.moves_executed), 0)

    def test_retreat_latch_suppresses_exploration(self) -> None:
        self.mock.pos = Position(15, 15)
        self.mock.hp = Config.RETREAT_HP_THRESHOLD - 2  # Low HP
        self.safe.begin_turn(self.mock)
        res = self.economy._try_evade_threat(self.mock)
        self.assertTrue(res)
        self.assertEqual(self.economy.retreat.turns, Config.RETREAT_STICKY_TURNS - 1)


class TestGrandKnightCoreMacro(unittest.TestCase):
    """5 & 6. Core writes SLOT_TURRET_COUNT & SLOT_BUILDER_COUNT; ignores stale ore."""

    def setUp(self) -> None:
        self.mock = MockController(unit_id=0, pos=Position(2, 2), map_width=30, map_height=30)
        self.safe = SafeController(self.mock)
        self.macro = CoreMacro(self.safe)

    def test_core_writes_turret_and_builder_counts(self) -> None:
        self.safe.begin_turn(self.mock)
        self.macro.run_core(self.mock)
        self.assertEqual(self.mock.read_store(Config.SLOT_TURRET_COUNT), 0)
        self.assertEqual(self.mock.read_store(Config.SLOT_BUILDER_COUNT), 0)

    def test_core_ignores_stale_ore_discovery(self) -> None:
        self.mock.current_round = 100
        # Broadcast discovery with round_mod = 0 (100 rounds ago -> exceeds TTL 80)
        from blackboard.storecoding import pack_ore_discovery
        self.mock.store[Config.SLOT_ORE_DISCOVERY] = pack_ore_discovery(Position(10, 10), 0)

        self.safe.begin_turn(self.mock)
        self.macro.run_core(self.mock)
        # Should not author stale ore into intent slot
        val_intent = self.mock.read_store(Config.SLOT_BUILDER_INTENT)
        self.assertEqual(val_intent, 0)


class TestGrandKnightOreLifecycle(unittest.TestCase):
    """7 & 8. Blacklisted ore is not committed; Second harvester priority."""

    def setUp(self) -> None:
        self.mock = MockController(unit_id=2, pos=Position(5, 5), map_width=30, map_height=30)
        self.safe = SafeController(self.mock)
        self.economy = EconomyBuilder(self.safe)
        self.mock.store[Config.SLOT_CORE_POS] = (3 << 16) | 3

    def test_blacklisted_ore_not_committed(self) -> None:
        ore_tile = Position(5, 7)
        self.economy.ore_targeting.ore_blacklist[ore_tile] = 100
        self.mock.current_round = 10

        self.assertFalse(self.economy.ore_targeting.is_valid(self.mock, ore_tile))

    def test_second_harvester_priority_when_harvester_count_below_2(self) -> None:
        self.mock.set_tile_env(Position(5, 6), Environment.ORE_TITANIUM)
        target = self.economy.ore_targeting.find_target(self.mock, self.economy.frontier, self.economy.spread_zone)
        self.assertIsNotNone(target)


class TestGrandKnightSteinerNetworkAndRepair(unittest.TestCase):
    """9, 10, 11, 12. Steiner graft path, missing conveyor repair, friendly/enemy obstructions."""

    def setUp(self) -> None:
        self.mock = MockController(unit_id=2, pos=Position(5, 5), map_width=30, map_height=30)
        self.safe = SafeController(self.mock)
        self.memory = BeltMemory()
        self.network = BeltNetwork(self.memory)
        self.repair = BeltRepairSkill(self.safe)

    def test_steiner_graft_chooses_known_network_anchor(self) -> None:
        harvester_1 = Position(10, 10)
        relay_path = (Position(10, 9), Position(10, 8), Position(10, 7))
        self.memory.add_relay(harvester_1, relay_path, 1)

        # New harvester at (12, 8) should graft onto (10, 8) rather than Core at (2, 2)
        new_harvester = Position(12, 8)
        anchor, is_graft = self.network.find_nearest_network_anchor(self.mock, new_harvester)
        self.assertTrue(is_graft)
        self.assertIn(anchor, relay_path)

    def test_belt_repair_detects_missing_conveyor(self) -> None:
        harvester = Position(5, 7)
        path = (Position(5, 6), Position(5, 5))
        relay = self.memory.add_relay(harvester, path, 1)
        core_fp = [Position(2, 2), Position(2, 3), Position(3, 2), Position(3, 3)]

        state = self.repair.inspect_relay_tile(self.mock, relay, 0, core_fp)
        self.assertEqual(state, TileState.MISSING_CONVEYOR)

    def test_friendly_belt_obstruction_destroyed(self) -> None:
        harvester = Position(5, 7)
        path = (Position(5, 6), Position(5, 5))
        relay = self.memory.add_relay(harvester, path, 1)
        core_fp = [Position(2, 2), Position(2, 3), Position(3, 2), Position(3, 3)]

        tile = Position(5, 6)
        # Place friendly barrier as obstruction
        self.mock.spawn_building(tile, EntityType.BARRIER, team=self.mock.get_team())
        self.safe.begin_turn(self.mock)
        self.mock.pos = Position(5, 5)

        outcome = self.repair.try_repair_tile(self.mock, relay, 0, core_fp)
        self.assertTrue(outcome.success)
        self.assertEqual(len(self.mock.destroys_executed), 1)


class TestHarvesterHijack(unittest.TestCase):
    """Tests for enemy harvester hijacking and preservation."""

    def setUp(self) -> None:
        self.mock = MockController(unit_id=5, pos=Position(10, 11), map_width=30, map_height=30)
        self.mock.spawn_building(Position(2, 2), EntityType.CORE, team=0)
        self.safe = SafeController(self.mock)

    def test_enemy_harvester_not_dismantled(self) -> None:
        from combat.dismantle import try_dismantle_adjacent_hostile
        enemy_harvester_pos = Position(10, 10)
        self.mock.spawn_building(enemy_harvester_pos, EntityType.HARVESTER, team=1)
        self.safe.begin_turn(self.mock)
        self.mock.action_cooldown = 0

        # Should NOT dismantle enemy harvester
        dismantled = try_dismantle_adjacent_hostile(self.mock, self.safe)
        self.assertFalse(dismantled)
        self.assertEqual(len(self.mock.fires_executed), 0)

    def test_enemy_harvester_hijacked_in_economy(self) -> None:
        from logistics.economy import EconomyBuilder
        builder = EconomyBuilder(self.safe)
        enemy_harvester_pos = Position(10, 10)
        self.mock.spawn_building(enemy_harvester_pos, EntityType.HARVESTER, team=1)
        self.mock.env_grid[(enemy_harvester_pos.x, enemy_harvester_pos.y)] = Environment.ORE_TITANIUM
        self.safe.begin_turn(self.mock)

        builder.expansion.target_ore = enemy_harvester_pos
        builder.expansion.committed_ore = enemy_harvester_pos
        result, _ = builder.expansion.try_mine_ore(
            self.mock, builder.seen_walls, builder.pos_history,
            builder.frontier, builder.spread_zone, builder.stuck_turns,
        )

        self.assertTrue(result)
        self.assertTrue(builder.expansion.harvester_built)
        self.assertIsNotNone(builder.expansion.belt_path)


if __name__ == "__main__":
    unittest.main()
