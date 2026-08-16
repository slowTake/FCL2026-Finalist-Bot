"""codex_bot — a self-contained, economy-first Florent Code League bot.

The bot is intentionally compact enough to be a useful starting point:

* the core spawns a small workforce and converts surplus titanium to ammo;
* builders discover and publish ore, construct harvesters, and progressively
  extend conveyors toward the core;
* builders defend themselves with their adjacent attack and add gunners near
  the core after the opening; and
* gunners fire whenever their line and the team ammo pool allow it.

Every engine action is preceded by its matching ``can_*`` check.  The module is
standalone so it can be uploaded as-is (with ``main.py`` at the archive root).
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Environment, GameConstants, Position


CARDINALS = (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)
ALL_DIRECTIONS = tuple(direction for direction in Direction if direction != Direction.CENTRE)

# Writes to the store are visible on the following round.  Positions are packed
# with a +1 offset, leaving 0 available as the "not known" value.
SLOT_CORE_POSITION = 0
SLOT_ORE_POSITION = 1

MAX_BUILDERS = 8
AMMO_RESERVE = 40


def pack_position(position: Position) -> int:
    return ((position.x + 1) << 16) | (position.y + 1)


def unpack_position(value: int) -> Position | None:
    if value == 0:
        return None
    return Position((value >> 16) - 1, (value & 0xFFFF) - 1)


def in_bounds(ct: Controller, position: Position) -> bool:
    return 0 <= position.x < ct.get_map_width() and 0 <= position.y < ct.get_map_height()


def cardinal_toward(source: Position, destination: Position) -> Direction:
    """Return the cardinal direction which makes the best one-step progress."""
    return min(CARDINALS, key=lambda direction: source.add(direction).distance_squared(destination))


class Player:
    def __init__(self) -> None:
        self.core_position: Position | None = None
        self.target: Position | None = None
        self.last_position: Position | None = None
        self.stuck_turns = 0

    def run(self, ct: Controller) -> None:
        # An exception escaping run permanently destroys this unit.  Actions are
        # guarded below; this final guard protects the bot if an engine release
        # changes an edge case while preserving the rest of the team.
        try:
            entity_type = ct.get_entity_type()
            if entity_type == EntityType.CORE:
                self._run_core(ct)
            elif entity_type == EntityType.BUILDER_BOT:
                self._run_builder(ct)
            elif entity_type == EntityType.GUNNER:
                self._run_gunner(ct)
            elif entity_type == EntityType.SENTINEL:
                self._run_sentinel(ct)
        except Exception:
            return

    # ---- Core -------------------------------------------------------------

    def _run_core(self, ct: Controller) -> None:
        position = ct.get_position()
        ct.write_store(SLOT_CORE_POSITION, pack_position(position))

        # Ammo conversion does not consume the core's spawn action.  Keep enough
        # titanium for the next builder so growth is not blocked by defence.
        if (
            ct.get_global_ammo() < AMMO_RESERVE
            and ct.get_global_resources() >= ct.get_builder_bot_cost() + 10
            and ct.can_convert_ammo(10)
        ):
            ct.convert_ammo(10)

        if ct.get_action_cooldown() != 0 or ct.get_unit_count() >= MAX_BUILDERS + 1:
            return
        if ct.get_global_resources() < ct.get_builder_bot_cost():
            return

        # Prefer cardinal exits, then diagonals.  This is deterministic and
        # avoids allocating/shuffling lists on the core's hot path.
        for direction in CARDINALS + ALL_DIRECTIONS:
            spawn_position = position.add(direction)
            if ct.can_spawn(spawn_position):
                ct.spawn_builder(spawn_position)
                return

    # ---- Builder bots -----------------------------------------------------

    def _run_builder(self, ct: Controller) -> None:
        position = ct.get_position()
        self._load_core_position(ct)
        self._update_stuck_state(position)
        self._publish_nearest_ore(ct)

        if ct.get_action_cooldown() == 0:
            # Immediate self-defence is higher priority than construction.
            if self._try_adjacent_attack(ct):
                return
            if self._try_build_harvester(ct):
                self.target = None
                return
            if self._try_heal(ct):
                return
            if self._try_extend_conveyor(ct):
                return
            if self._try_build_defence(ct):
                return

        self._move_to_target(ct)

    def _load_core_position(self, ct: Controller) -> None:
        stored = unpack_position(ct.read_store(SLOT_CORE_POSITION))
        if stored is not None:
            self.core_position = stored

    def _update_stuck_state(self, position: Position) -> None:
        if self.last_position == position:
            self.stuck_turns += 1
        else:
            self.stuck_turns = 0
        self.last_position = position

    def _try_adjacent_attack(self, ct: Controller) -> bool:
        position = ct.get_position()
        for direction in CARDINALS:
            target = position.add(direction)
            if ct.can_fire(target):
                ct.fire(target)
                return True
        return False

    def _try_build_harvester(self, ct: Controller) -> bool:
        if ct.get_global_resources() < ct.get_harvester_cost():
            return False
        position = ct.get_position()
        for direction in CARDINALS:
            site = position.add(direction)
            if ct.get_tile_env(site) == Environment.ORE_TITANIUM and ct.can_build_harvester(site):
                ct.build_harvester(site)
                return True
        return False

    def _try_heal(self, ct: Controller) -> bool:
        if ct.get_global_resources() < 1:
            return False
        position = ct.get_position()
        for direction in CARDINALS:
            site = position.add(direction)
            if ct.can_heal(site):
                ct.heal(site)
                return True
        return False

    def _try_extend_conveyor(self, ct: Controller) -> bool:
        """Add one core-facing conveyor after an adjacent resource building.

        A builder eventually walks alongside a harvester or existing conveyor.
        When it can reach the source's core-facing output tile, this incrementally
        grows a route home without assuming a complete global map.
        """
        if self.core_position is None or ct.get_global_resources() < ct.get_conveyor_cost():
            return False

        position = ct.get_position()
        for direction in CARDINALS:
            source_position = position.add(direction)
            source_id = ct.get_tile_building_id(source_position)
            if source_id is None:
                continue
            source_type = ct.get_entity_type(source_id)
            if source_type not in (EntityType.HARVESTER, EntityType.CONVEYOR, EntityType.SPLITTER):
                continue
            facing = cardinal_toward(source_position, self.core_position)
            site = source_position.add(facing)
            if ct.can_build_conveyor(site, facing):
                ct.build_conveyor(site, facing)
                return True
        return False

    def _try_build_defence(self, ct: Controller) -> bool:
        """Build occasional outward-facing gunners around the core.

        Builder IDs stagger construction attempts, which prevents every builder
        from spending the same round on a costly turret.
        """
        if self.core_position is None or ct.get_current_round() < 35:
            return False
        position = ct.get_position()
        if position.distance_squared(self.core_position) > 18:
            return False
        if ct.get_global_resources() < ct.get_gunner_cost():
            return False
        if (ct.get_current_round() + ct.get_id()) % 7 != 0:
            return False

        facing = self.core_position.direction_to(position)
        if facing == Direction.CENTRE:
            facing = Direction.NORTH
        for direction in CARDINALS:
            site = position.add(direction)
            if ct.can_build_gunner(site, facing):
                ct.build_gunner(site, facing)
                return True
        return False

    def _publish_nearest_ore(self, ct: Controller) -> None:
        position = ct.get_position()
        closest: Position | None = None
        closest_distance: int | None = None
        for tile in ct.get_nearby_tiles():
            if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
                continue
            if ct.get_tile_building_id(tile) is not None:
                continue
            distance = position.distance_squared(tile)
            if closest_distance is None or distance < closest_distance:
                closest, closest_distance = tile, distance
        if closest is not None:
            ct.write_store(SLOT_ORE_POSITION, pack_position(closest))

    def _pick_target(self, ct: Controller) -> Position:
        position = ct.get_position()
        visible_ore: Position | None = None
        visible_distance: int | None = None
        for tile in ct.get_nearby_tiles():
            if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
                continue
            if ct.get_tile_building_id(tile) is not None:
                continue
            distance = position.distance_squared(tile)
            if visible_distance is None or distance < visible_distance:
                visible_ore, visible_distance = tile, distance
        if visible_ore is not None:
            return visible_ore

        shared_ore = unpack_position(ct.read_store(SLOT_ORE_POSITION))
        if shared_ore is not None and in_bounds(ct, shared_ore):
            return shared_ore

        # Deterministic scouting points spread builders over the map, including
        # when the core is at coordinate 0 (which raw store coordinates cannot
        # represent safely).
        width, height = ct.get_map_width(), ct.get_map_height()
        bot_id = ct.get_id()
        waypoints = (
            Position(width - 1, 0),
            Position(0, height - 1),
            Position(width - 1, height - 1),
            Position(width // 2, height // 2),
        )
        return waypoints[(bot_id + ct.get_current_round() // 24) % len(waypoints)]

    def _move_to_target(self, ct: Controller) -> None:
        if ct.get_move_cooldown() != 0:
            return
        position = ct.get_position()
        if self.target is None or self.target == position or self.stuck_turns >= 3:
            self.target = self._pick_target(ct)
            self.stuck_turns = 0

        ranked = sorted(
            CARDINALS,
            key=lambda direction: position.add(direction).distance_squared(self.target),
        )
        for direction in ranked:
            if ct.can_move(direction):
                ct.move(direction)
                return

    # ---- Turrets ----------------------------------------------------------

    def _run_gunner(self, ct: Controller) -> None:
        target = ct.get_gunner_target()
        if target is not None and ct.can_fire(target):
            ct.fire(target)

    def _run_sentinel(self, ct: Controller) -> None:
        # Sentinels have no equivalent target helper; scan their attack pattern.
        for target in ct.get_attackable_tiles():
            if ct.can_fire(target):
                ct.fire(target)
                return
