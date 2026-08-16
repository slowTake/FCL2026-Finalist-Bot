"""knight — Unit Tests for Phase G: Belt Integrity, Relay Memory, and Repair Protocol."""

from __future__ import annotations

import unittest
from fcode import Direction, EntityType, Environment, Position

from blackboard.belt_protocol import (
    belt_alert_is_fresh,
    clear_belt_alert_if_matches,
    pack_belt_alert,
    unpack_belt_alert,
    write_belt_alert,
)
from config import Config
from core.macro import CoreMacro
from engine.safe_controller import SafeController
from logistics.belt_memory import BeltMemory, RelayRoute, RelayStatus
from logistics.belt_repair import BeltRepairSkill, TileState
from logistics.economy import EconomyBuilder
from tests.mock_controller import MockController


class TestPhaseGBeltProtocol(unittest.TestCase):
    """Test Phase G.3 belt alert packing and freshness."""

    def test_pack_unpack_belt_alert_roundtrip(self) -> None:
        pos = Position(17, 23)
        round_num = 142
        packed = pack_belt_alert(pos, round_num)
        unpacked_pos, unpacked_round = unpack_belt_alert(packed)
        self.assertEqual(unpacked_pos, pos)
        self.assertEqual(unpacked_round, round_num % 256)

    def test_belt_alert_freshness(self) -> None:
        mock = MockController(unit_id=1, pos=Position(5, 5))
        mock.current_round = 100

        # Fresh alert (created round 90)
        fresh_packed = pack_belt_alert(Position(10, 10), 90)
        self.assertTrue(belt_alert_is_fresh(mock, fresh_packed))

        # Stale alert (created round 10, TTL=80)
        stale_packed = pack_belt_alert(Position(10, 10), 10)
        self.assertFalse(belt_alert_is_fresh(mock, stale_packed))

        # Uninitialized (0)
        self.assertFalse(belt_alert_is_fresh(mock, 0))

    def test_write_and_clear_belt_alert(self) -> None:
        mock = MockController(unit_id=1, pos=Position(5, 5))
        mock.current_round = 50
        tile = Position(12, 14)

        write_belt_alert(mock, tile, 50)
        self.assertNotEqual(mock.read_store(Config.SLOT_BELT_ALERT), 0)

        # Clear if matching
        clear_belt_alert_if_matches(mock, tile)
        self.assertEqual(mock.read_store(Config.SLOT_BELT_ALERT), 0)


class TestPhaseGBeltMemory(unittest.TestCase):
    """Test Phase G.2 and G.5 local belt memory and patrol scheduling."""

    def test_add_and_retrieve_relay(self) -> None:
        memory = BeltMemory()
        hpos = Position(3, 3)
        path = (Position(3, 4), Position(3, 5), Position(3, 6))
        relay = memory.add_relay(hpos, path, built_round=20)

        self.assertEqual(relay.relay_id, 1)
        self.assertEqual(relay.harvester_pos, hpos)
        self.assertEqual(relay.path, path)
        self.assertIsInstance(relay.path, tuple)
        self.assertEqual(relay.status, RelayStatus.ACTIVE)
        self.assertEqual(relay.built_round, 20)

        retrieved = memory.get_relay(1)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.relay_id, 1)

    def test_patrol_due_scheduling(self) -> None:
        memory = BeltMemory()
        # Relay 1 built at round 10
        r1 = memory.add_relay(Position(2, 2), (Position(2, 3),), built_round=10)
        # Relay 2 built at round 50
        r2 = memory.add_relay(Position(5, 5), (Position(5, 6),), built_round=50)

        # At round 65 (interval = 60):
        # Relay 1 age = 55 (not due)
        due_65 = memory.patrol_due(current_round=65, interval=60)
        self.assertEqual(len(due_65), 0)

        # At round 75:
        # Relay 1 age = 65 (due!), Relay 2 age = 25 (not due)
        due_75 = memory.patrol_due(current_round=75, interval=60)
        self.assertEqual(len(due_75), 1)
        self.assertEqual(due_75[0].relay_id, 1)

        # If Relay 2 is broken, it is immediately due regardless of interval
        r2.status = RelayStatus.BROKEN
        due_broken = memory.patrol_due(current_round=75, interval=60)
        self.assertEqual(len(due_broken), 2)
        # Broken relay sorted first
        self.assertEqual(due_broken[0].relay_id, 2)


class TestPhaseGBeltInspectionAndRepair(unittest.TestCase):
    """Test Phase G.4 inspection states and repair execution."""

    def setUp(self) -> None:
        self.mock = MockController(unit_id=1, pos=Position(5, 5), map_width=20, map_height=20)
        self.safe = SafeController(self.mock)
        self.repair = BeltRepairSkill(self.safe)
        self.core_footprint = [Position(5, 8), Position(6, 8)]

    def test_tile_inspection_states(self) -> None:
        relay = RelayRoute(
            relay_id=1,
            harvester_pos=Position(5, 4),
            path=(Position(5, 5), Position(5, 6), Position(5, 7)),
            built_round=10,
            last_verified_round=10,
            status=RelayStatus.ACTIVE,
            broken_index=None,
            repair_attempts=0,
        )

        # 1. Tile outside vision -> UNKNOWN
        state_unknown = self.repair.inspect_relay_tile(self.mock, relay, index=2, core_footprint=self.core_footprint)
        # Distance (5,5) to (5,7) is 4 dist_sq <= 16 (in vision)
        # Let's test out of vision index with distant position
        self.mock.pos = Position(0, 0)
        state_far = self.repair.inspect_relay_tile(self.mock, relay, index=2, core_footprint=self.core_footprint)
        self.assertEqual(state_far, TileState.UNKNOWN)

        # Move back in vision
        self.mock.pos = Position(5, 5)

        # 2. Empty tile -> MISSING_CONVEYOR
        state_missing = self.repair.inspect_relay_tile(self.mock, relay, index=1, core_footprint=self.core_footprint)
        self.assertEqual(state_missing, TileState.MISSING_CONVEYOR)

        # 3. Enemy building -> ENEMY_BLOCK
        enemy_bid = 99
        self.mock.building_grid[(5, 6)] = enemy_bid
        self.mock.entities[enemy_bid] = {
            "type": EntityType.GUNNER,
            "team": 1,
            "pos": Position(5, 6),
            "hp": 30,
            "max_hp": 30,
        }
        state_enemy = self.repair.inspect_relay_tile(self.mock, relay, index=1, core_footprint=self.core_footprint)
        self.assertEqual(state_enemy, TileState.ENEMY_BLOCK)

        # 4. Friendly barrier -> FRIENDLY_BLOCK
        friendly_barrier_id = 101
        self.mock.building_grid[(5, 6)] = friendly_barrier_id
        self.mock.entities[friendly_barrier_id] = {
            "type": EntityType.BARRIER,
            "team": 0,
            "pos": Position(5, 6),
            "hp": 40,
            "max_hp": 40,
        }
        state_barrier = self.repair.inspect_relay_tile(self.mock, relay, index=1, core_footprint=self.core_footprint)
        self.assertEqual(state_barrier, TileState.FRIENDLY_BLOCK)

        # 5. Friendly conveyor with correct facing -> OK
        friendly_conveyor_id = 102
        self.mock.building_grid[(5, 6)] = friendly_conveyor_id
        self.mock.entities[friendly_conveyor_id] = {
            "type": EntityType.CONVEYOR,
            "team": 0,
            "pos": Position(5, 6),
            "hp": 20,
            "max_hp": 20,
            "direction": Direction.SOUTH,  # Expected facing from (5,6) to (5,7) is SOUTH
        }
        state_ok = self.repair.inspect_relay_tile(self.mock, relay, index=1, core_footprint=self.core_footprint)
        self.assertEqual(state_ok, TileState.OK)

        # 6. Friendly conveyor with wrong facing -> WRONG_DIRECTION
        self.mock.entities[friendly_conveyor_id]["direction"] = Direction.NORTH
        state_wrong = self.repair.inspect_relay_tile(self.mock, relay, index=1, core_footprint=self.core_footprint)
        self.assertEqual(state_wrong, TileState.WRONG_DIRECTION)

    def test_repair_missing_conveyor_builds(self) -> None:
        self.mock.pos = Position(5, 5)
        relay = RelayRoute(
            relay_id=1,
            harvester_pos=Position(5, 4),
            path=(Position(5, 6),),
            built_round=10,
            last_verified_round=10,
            status=RelayStatus.BROKEN,
            broken_index=0,
            repair_attempts=0,
        )

        outcome = self.repair.try_repair_tile(self.mock, relay, index=0, core_footprint=self.core_footprint)
        self.assertTrue(outcome.success)
        self.assertEqual(len(self.mock.builds_executed), 1)
        self.assertEqual(self.mock.builds_executed[0][0], "conveyor")
        self.assertEqual(self.mock.builds_executed[0][1], Position(5, 6))

    def test_repair_enemy_block_fire_attempts(self) -> None:
        self.mock.pos = Position(5, 5)
        enemy_bid = 99
        self.mock.building_grid[(5, 6)] = enemy_bid
        self.mock.entities[enemy_bid] = {
            "type": EntityType.SENTINEL,
            "team": 1,
            "pos": Position(5, 6),
            "hp": 30,
            "max_hp": 30,
        }

        relay = RelayRoute(
            relay_id=1,
            harvester_pos=Position(5, 4),
            path=(Position(5, 6),),
            built_round=10,
            last_verified_round=10,
            status=RelayStatus.BROKEN,
            broken_index=0,
            repair_attempts=0,
        )

        # Attempt 1
        self.safe.begin_turn(self.mock)
        outcome1 = self.repair.try_repair_tile(self.mock, relay, index=0, core_footprint=self.core_footprint)
        self.assertTrue(outcome1.success)
        self.assertEqual(relay.repair_attempts, 1)

        # Attempt 2
        self.mock.action_cooldown = 0
        self.safe.begin_turn(self.mock)
        outcome2 = self.repair.try_repair_tile(self.mock, relay, index=0, core_footprint=self.core_footprint)
        self.assertTrue(outcome2.success)
        self.assertEqual(relay.repair_attempts, 2)

        # Attempt 3 (hits max attempts -> marks CONTESTED)
        self.mock.action_cooldown = 0
        self.safe.begin_turn(self.mock)
        outcome3 = self.repair.try_repair_tile(self.mock, relay, index=0, core_footprint=self.core_footprint)
        self.assertTrue(outcome3.success)
        self.assertEqual(relay.repair_attempts, 0)
        self.assertIn(Position(5, 6), self.repair.contested_tiles)


class TestPhaseGCoreIncomeStallAndIntent(unittest.TestCase):
    """Test Phase G.7 income stall monitoring and G.8 repair intent."""

    def test_core_income_stall_sets_flag(self) -> None:
        mock = MockController(unit_id=0, entity_type=EntityType.CORE, pos=Position(5, 5))
        core_macro = CoreMacro(SafeController(mock))

        # Add a friendly harvester
        harv_id = 10
        mock.building_grid[(5, 3)] = harv_id
        mock.entities[harv_id] = {
            "type": EntityType.HARVESTER,
            "team": 0,
            "pos": Position(5, 3),
            "hp": 20,
            "max_hp": 20,
        }

        # Round 1: bank = 100
        mock.current_round = 1
        mock.global_resources = 100
        core_macro.run_core_turn(mock)

        flags = mock.read_store(Config.SLOT_GLOBAL_FLAGS)
        self.assertFalse(bool(flags & Config.FLAG_BELT_REPAIR_NEEDED))

        # Round 60: bank still 100 (stalled for 59 turns > BELT_INCOME_STALL_TURNS=50)
        mock.current_round = 60
        core_macro.run_core_turn(mock)

        flags = mock.read_store(Config.SLOT_GLOBAL_FLAGS)
        self.assertTrue(bool(flags & Config.FLAG_BELT_REPAIR_NEEDED))

    def test_core_repair_intent_broadcast(self) -> None:
        mock = MockController(unit_id=0, entity_type=EntityType.CORE, pos=Position(5, 5))
        core_macro = CoreMacro(SafeController(mock))

        # Write fresh belt alert at Position(7, 7)
        mock.current_round = 40
        write_belt_alert(mock, Position(7, 7), 40)

        core_macro.run_core_turn(mock)

        intent_val = mock.read_store(Config.SLOT_BUILDER_INTENT)
        self.assertNotEqual(intent_val, 0)
        from utils.geometry import unpack_builder_intent
        tx, ty, action, prio = unpack_builder_intent(intent_val)
        self.assertEqual(Position(tx, ty), Position(7, 7))
        self.assertEqual(action, Config.INTENT_REPAIR)
        self.assertEqual(prio, 13)


if __name__ == "__main__":
    unittest.main()
