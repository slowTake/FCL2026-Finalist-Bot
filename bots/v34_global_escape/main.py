import time

from fcode import Controller, Direction, EntityType, GameError, Position
from builder import DefenderState, HealerState, run_defender, run_healer
from economy import EconomyState, run_economy
from miner import MinerState, run_miner
from raider import RaiderState, run_raider
from repair_patrol import RepairPatrolState, run_repair_patrol
from constants import (COMPETITION_MODE, CORE_MAX_HP, CORE_VISION_RADIUS_SQ,
                       GUNNER_VISION_RADIUS_SQ, LAUNCHER_VISION_RADIUS_SQ,
                       MAP_MAX_SIZE, MAP_MIN_SIZE, MAP_UPDATE_EVERY_N_BUILDER,
                       MAP_UPDATE_EVERY_N_CORE, MAP_UPDATE_EVERY_N_TURRET,
                       MSG_DEFENSE_COMPLETE, RESET_LOGS_ON_START,
                       ROLE_DEFENDER, ROLE_ECONOMY, ROLE_HEALER, ROLE_MINER,
                       ROLE_RAIDER, ROLE_REPAIR_PATROL,
                       SENTINEL_VISION_RADIUS_SQ, SHOW_MAP_DEBUG,
                       STORE_CORE_X, STORE_CORE_Y,
                       STORE_DEFENSE_STATUS, STORE_SPAWN_ROLE, VALID_ROLES)
from local_map import UnitLocalMap, VisionLocalMap
from logger import (consume_tick_log_overhead_us, init_logger, log_event,
                    log_local_map, log_timing_event, reset_run_logs,
                    reset_tick_log_overhead)
from symmetry import detect_symmetry


def _max_raiders_for_round(rnd: int) -> int:
    """Hard cap on simultaneous raiders by round bracket."""
    if rnd < 200:
        return 2
    if rnd < 400:
        return 4
    if rnd < 800:
        return 6
    return 8


class Player:
    def __init__(self):
        init_logger()
        self.initialised = False
        self.core_pos = None
        self.symmetry = None
        self.local_map = None

        self.builder_state = None
        self.spawned_builder = False
        self.role = None
        self.healer_spawned = False

        self.defense_complete = False
        self.economy_spawned = False
        self.economy_spawn_round = None

        self.spawned_bot_roles = {}
        self.spawned_bot_history = []
        self.defender_bot_ids = set()
        self.healer_bot_ids = set()
        self.economy_bot_ids = set()

        self.miner_bot_ids = set()
        self.raider_bot_ids = set()
        self.repair_patrol_bot_ids = set()

        self.target_defender_count = 1
        self.target_economy_count = 1
        self.target_miner_count_early = 2
        self.miner_wave_interval = 60
        self.target_repair_patrol_count = 2
        self.target_raider_count_max = 8

        self.repair_patrol_spawned = False
        self._last_miner_wave_round = 0
        self.min_rounds_between_role_spawns = 5
        self.last_role_spawn_round = None
        self._timing_meta = {}
        self._map_created_round = None
        self.map_w = None
        self.map_h = None

    def _is_turret_entity(self, etype: EntityType) -> bool:
        return etype in (
            EntityType.GUNNER,
            EntityType.SENTINEL,
            EntityType.LAUNCHER,
        )

    def _vision_radius_sq_for_entity(self, etype: EntityType | None) -> int:
        if etype == EntityType.CORE:
            return CORE_VISION_RADIUS_SQ
        if etype == EntityType.GUNNER:
            return GUNNER_VISION_RADIUS_SQ
        if etype == EntityType.SENTINEL:
            return SENTINEL_VISION_RADIUS_SQ
        if etype == EntityType.LAUNCHER:
            return LAUNCHER_VISION_RADIUS_SQ
        return CORE_VISION_RADIUS_SQ

    def _is_adjacency_ready(self) -> bool:
        if self.local_map is None:
            return False
        if not getattr(self.local_map, "enable_adjacency", False):
            return True
        return bool(getattr(self.local_map, "_adjacency_bootstrapped", True))

    def _spawn_economy_builder(self, c: Controller):
        if self.core_pos is None:
            return None

        cx, cy = self.core_pos.x, self.core_pos.y
        for dy in (-1, 0, 1, 2):
            for dx in (-1, 0, 1, 2):
                if 0 <= dx <= 1 and 0 <= dy <= 1:
                    continue  # 2x2 Core footprint
                sx, sy = cx + dx, cy + dy
                sp = Position(sx, sy)
                try:
                    if c.can_spawn(sp):
                        spawned_id = c.spawn_builder(sp)
                        log_event(
                            c.get_current_round(),
                            c.get_id(),
                            "core",
                            f"({cx},{cy})",
                            "spawned_economy_builder",
                            sx=sx,
                            sy=sy,
                            sid=spawned_id,
                        )
                        return spawned_id
                except GameError:
                    continue
        return None

    def _spawn_builder_at_core_with_fallback(
        self,
        c: Controller,
        primary_event: str,
        fallback_event: str,
    ):
        if self.core_pos is None:
            return None

        cx, cy = self.core_pos.x, self.core_pos.y
        for dy in (-1, 0, 1, 2):
            for dx in (-1, 0, 1, 2):
                if 0 <= dx <= 1 and 0 <= dy <= 1:
                    continue  # 2x2 Core footprint
                sp = Position(cx + dx, cy + dy)
                try:
                    if c.can_spawn(sp):
                        spawned_id = c.spawn_builder(sp)
                        log_event(
                            c.get_current_round(),
                            c.get_id(),
                            "core",
                            f"({cx},{cy})",
                            primary_event,
                            sx=sp.x,
                            sy=sp.y,
                            sid=spawned_id,
                        )
                        return spawned_id
                except GameError:
                    continue

        return None

    def _register_spawned_builder_role(
        self,
        unit_id: int,
        role: int,
        rnd: int,
    ):
        if not isinstance(unit_id, int):
            return

        self.spawned_bot_roles[unit_id] = role
        self.spawned_bot_history.append((int(rnd), int(unit_id), int(role)))

        if role == ROLE_DEFENDER:
            self.defender_bot_ids.add(unit_id)
            self.spawned_builder = True
        elif role == ROLE_HEALER:
            self.healer_bot_ids.add(unit_id)
            self.healer_spawned = True
        elif role == ROLE_ECONOMY:
            self.economy_bot_ids.add(unit_id)
            self.economy_spawned = True
        elif role == ROLE_MINER:
            self.miner_bot_ids.add(unit_id)
            self._last_miner_wave_round = int(rnd)
        elif role == ROLE_RAIDER:
            self.raider_bot_ids.add(unit_id)
        elif role == ROLE_REPAIR_PATROL:
            self.repair_patrol_bot_ids.add(unit_id)
            self.repair_patrol_spawned = True

        self.last_role_spawn_round = int(rnd)

    def _spawn_round_cooldown_ready(self, rnd: int) -> bool:
        if not isinstance(self.last_role_spawn_round, int):
            return True
        return (int(rnd) - int(self.last_role_spawn_round)) >= int(
            self.min_rounds_between_role_spawns
        )

    def _visible_friendly_builder_ids(self, c: Controller):
        out = set()
        my_team = c.get_team()
        try:
            nearby_units = c.get_nearby_entities()
        except GameError:
            return out

        for uid in nearby_units:
            try:
                if c.get_team(uid) != my_team:
                    continue
                if c.get_entity_type(uid) != EntityType.BUILDER_BOT:
                    continue
                out.add(int(uid))
            except GameError:
                continue
        return out

    def _count_visible_friendly_harvesters(self, c: Controller) -> int:
        my_team = c.get_team()
        count = 0
        try:
            for bid in c.get_nearby_buildings():
                try:
                    if c.get_entity_type(bid) != EntityType.HARVESTER:
                        continue
                    if c.get_team(bid) != my_team:
                        continue
                    count += 1
                except GameError:
                    continue
        except GameError:
            return 0
        return count

    def _refresh_tracked_builder_roles(self, c: Controller):
        visible_builder_ids = self._visible_friendly_builder_ids(c)
        rnd = c.get_current_round()
        cid = c.get_id()

        _ROLE_NAME_MAP = {
            ROLE_DEFENDER: "defender",
            ROLE_HEALER: "healer",
            ROLE_MINER: "miner",
            ROLE_RAIDER: "raider",
            ROLE_REPAIR_PATROL: "repair_patrol",
        }
        for role, tracked_set in (
            (ROLE_DEFENDER, self.defender_bot_ids),
            (ROLE_HEALER, self.healer_bot_ids),
            (ROLE_MINER, self.miner_bot_ids),
            (ROLE_RAIDER, self.raider_bot_ids),
            (ROLE_REPAIR_PATROL, self.repair_patrol_bot_ids),
        ):
            for uid in tuple(tracked_set):
                if uid in visible_builder_ids:
                    continue
                tracked_set.discard(uid)
                self.spawned_bot_roles.pop(uid, None)
                role_name = _ROLE_NAME_MAP.get(role, "unknown")
                if self.core_pos is not None:
                    log_event(
                        rnd,
                        cid,
                        "core",
                        f"({self.core_pos.x},{self.core_pos.y})",
                        "core_tracked_bot_missing_v2",
                        lost_id=uid,
                        role=role_name,
                    )

        self.spawned_builder = len(self.defender_bot_ids) > 0
        self.healer_spawned = len(self.healer_bot_ids) > 0

    def _count_friendly_turrets(self, c: Controller) -> int:
        my_team = c.get_team()
        count = 0
        try:
            nearby_entities = c.get_nearby_entities()
        except GameError:
            return 0
        for entity_id in nearby_entities:
            try:
                if c.get_team(entity_id) != my_team:
                    continue
                if self._is_turret_entity(c.get_entity_type(entity_id)):
                    count += 1
            except GameError:
                continue
        return count

    def _estimate_economy_count(
        self,
        c: Controller,
        defender_count: int,
        healer_count: int,
    ) -> int:
        try:
            friendly_units_including_core = int(c.get_unit_count())
        except GameError:
            return 0

        turret_count = self._count_friendly_turrets(c)
        miner_count = len(self.miner_bot_ids)
        raider_count = len(self.raider_bot_ids)
        repair_count = len(self.repair_patrol_bot_ids)
        economy_count = friendly_units_including_core - (
            1
            + int(defender_count)
            + int(healer_count)
            + int(turret_count)
            + miner_count
            + raider_count
            + repair_count
        )
        return max(0, int(economy_count))

    def _spawn_builder_for_role(self, c: Controller, role: int) -> bool:
        if self.core_pos is None:
            return False

        # Communicate next spawn role to shared store slot 0
        try:
            c.write_store(STORE_SPAWN_ROLE, role)
        except GameError:
            pass

        if role == ROLE_ECONOMY:
            spawned_id = self._spawn_economy_builder(c)
        elif role == ROLE_DEFENDER:
            spawned_id = self._spawn_builder_at_core_with_fallback(
                c,
                "spawned_defender",
                "spawned_defender_fallback",
            )
        elif role == ROLE_HEALER:
            spawned_id = self._spawn_builder_at_core_with_fallback(
                c,
                "spawned_healer",
                "spawned_healer_fallback",
            )
        elif role == ROLE_MINER:
            spawned_id = self._spawn_builder_at_core_with_fallback(
                c,
                "spawned_miner",
                "spawned_miner_fallback",
            )
        elif role == ROLE_RAIDER:
            spawned_id = self._spawn_builder_at_core_with_fallback(
                c,
                "spawned_raider",
                "spawned_raider_fallback",
            )
        elif role == ROLE_REPAIR_PATROL:
            spawned_id = self._spawn_builder_at_core_with_fallback(
                c,
                "spawned_repair_patrol",
                "spawned_repair_patrol_fallback",
            )
        else:
            return False

        if not isinstance(spawned_id, int):
            return False

        try:
            unit_slot = (spawned_id % 12) + 4
            c.write_store(unit_slot, role)
        except GameError:
            pass

        rnd = c.get_current_round()
        self._register_spawned_builder_role(spawned_id, role, rnd)
        if role == ROLE_ECONOMY:
            self.economy_spawn_round = rnd
        return True

    def run(self, c: Controller):
        if not COMPETITION_MODE:
            reset_tick_log_overhead()
            start_ns = time.perf_counter_ns()
        else:
            start_ns = 0
        et = "unknown"
        self._timing_meta = {}
        try:
            etype = c.get_entity_type()
            et = str(etype.name).lower()

            if etype == EntityType.CORE:
                self._run_core(c)
            elif etype == EntityType.BUILDER_BOT:
                self._run_bot(c)
            elif etype in (EntityType.GUNNER, EntityType.SENTINEL):
                self._run_turret(c)
            elif etype == EntityType.LAUNCHER:
                self._run_launcher(c)
        except Exception as e:
            try:
                p = c.get_position()
                pos = f"({p.x},{p.y})"
                rnd = c.get_current_round()
                uid = c.get_id()
                et = str(c.get_entity_type().name).lower()
            except Exception:
                pos = "(?,?)"
                rnd = -1
                uid = -1
                et = "unknown"
            log_event(rnd, uid, et, pos, "fatal_exception",
                      force=True, err=repr(e))

        if COMPETITION_MODE:
            return

        try:
            p = c.get_position()
            pos = f"({p.x},{p.y})"
            rnd = c.get_current_round()
            uid = c.get_id()
        except Exception:
            pos = "(?,?)"
            rnd = -1
            uid = -1

        elapsed_us = (time.perf_counter_ns() - start_ns) // 1000
        log_overhead_us = consume_tick_log_overhead_us()
        startup_reset_logs_us = int(
            self._timing_meta.get("startup_reset_logs_us", 0)
        )
        net_elapsed_us = (
            elapsed_us
            - log_overhead_us
            - startup_reset_logs_us
        )
        if net_elapsed_us < 0:
            net_elapsed_us = 0

        self._timing_meta["tick_raw_us"] = elapsed_us
        self._timing_meta["log_overhead_us"] = log_overhead_us
        log_timing_event(rnd, uid, et, pos, net_elapsed_us,
                         **self._timing_meta)

    def _init_game(self, c: Controller):
        my_team = c.get_team()
        try:
            my_type = c.get_entity_type()
        except GameError:
            my_type = None
        log_type = str(my_type.name).lower(
        ) if my_type is not None else "unknown"

        # Core position
        try:
            if c.get_entity_type() == EntityType.CORE:
                self.core_pos = c.get_position()
        except GameError:
            self.core_pos = None

        if self.core_pos is None:
            for bid in c.get_nearby_buildings():
                try:
                    if c.get_entity_type(bid) == EntityType.CORE and c.get_team(bid) == my_team:
                        self.core_pos = c.get_position(bid)
                        break
                except GameError:
                    continue

        if self.core_pos is None:
            try:
                cx = c.read_store(STORE_CORE_X)
                cy = c.read_store(STORE_CORE_Y)
                if cx > 0 or cy > 0:
                    self.core_pos = Position(cx, cy)
            except GameError:
                pass

        if not self.core_pos:
            log_event(c.get_current_round(), c.get_id(),
                      log_type, "(?,?)", "init_no_core_visible")
            return

        map_w = c.get_map_width()
        map_h = c.get_map_height()
        self.map_w = map_w
        self.map_h = map_h

        size_ok = (map_w == map_h) and (MAP_MIN_SIZE <= map_w <= MAP_MAX_SIZE)
        if not size_ok:
            log_event(
                c.get_current_round(),
                c.get_id(),
                log_type,
                f"({self.core_pos.x},{self.core_pos.y})",
                "map_assumption_violation",
                w=map_w,
                h=map_h,
                expected=f"square,{MAP_MIN_SIZE}-{MAP_MAX_SIZE}",
                origin="top_left_0_0",
            )

        full_map_unit = my_type == EntityType.BUILDER_BOT

        if full_map_unit:
            self.symmetry = detect_symmetry(self.core_pos, map_w, map_h)
            self.local_map = UnitLocalMap(
                map_w,
                map_h,
                self.symmetry,
                my_team,
                infer_symmetry=True,
                enable_adjacency=True,
                vision_only=False,
            )
            map_mode = "full"
        else:
            self.symmetry = "NONE"
            try:
                center_pos = c.get_position()
            except GameError:
                center_pos = self.core_pos
            self.local_map = VisionLocalMap(
                map_w,
                map_h,
                my_team,
                center_pos,
                self._vision_radius_sq_for_entity(my_type),
            )
            map_mode = "vision_window"

        if self.local_map is not None:
            self.local_map.set_friendly_core(self.core_pos)
            self._map_created_round = c.get_current_round()
        else:
            self._map_created_round = None

        self.initialised = True
        log_event(
            c.get_current_round(),
            c.get_id(),
            log_type,
            f"({self.core_pos.x},{self.core_pos.y})",
            "init_done",
            symmetry=self.symmetry,
            map_mode=map_mode,
            w=map_w,
            h=map_h,
            origin="top_left_0_0",
            map_debug=SHOW_MAP_DEBUG,
        )

    def _update_local_map(
        self,
        c: Controller,
        entity_type: EntityType,
        stride: int,
    ):
        if self.local_map is None:
            return
        rnd = c.get_current_round()
        if self._map_created_round is not None and rnd <= self._map_created_round:
            if not COMPETITION_MODE:
                self._timing_meta["map_update_deferred_init"] = 1
            return
        if stride > 1 and (rnd % stride) != 0:
            if not COMPETITION_MODE:
                self._timing_meta["map_update_skipped"] = 1
            return

        if COMPETITION_MODE:
            self.local_map.update_from_controller(c)
        else:
            map_update_start_ns = time.perf_counter_ns()
            self.local_map.update_from_controller(c)
            map_update_us = (
                time.perf_counter_ns() - map_update_start_ns
            ) // 1000
            self._timing_meta["map_update_us"] = map_update_us
            self._timing_meta.update(self.local_map.consume_round_metrics())

    def _run_core(self, c: Controller):
        if not self.initialised:
            if RESET_LOGS_ON_START and not COMPETITION_MODE:
                reset_start_ns = time.perf_counter_ns()
                reset_run_logs()
                self._timing_meta["startup_reset_logs_us"] = (
                    time.perf_counter_ns() - reset_start_ns
                ) // 1000
            self._init_game(c)

        if not self.initialised:
            return

        self._update_local_map(
            c,
            EntityType.CORE,
            MAP_UPDATE_EVERY_N_CORE,
        )

        core_pos = c.get_position()
        cx, cy = self.core_pos.x, self.core_pos.y
        rnd = c.get_current_round()

        # Write Core position to store slots
        try:
            c.write_store(STORE_CORE_X, cx)
            c.write_store(STORE_CORE_Y, cy)
        except GameError:
            pass

        self._refresh_tracked_builder_roles(c)

        defender_count = len(self.defender_bot_ids)
        healer_count = len(self.healer_bot_ids)
        economy_count = self._estimate_economy_count(
            c,
            defender_count,
            healer_count,
        )
        self.economy_spawned = economy_count > 0

        desired_healer_count = 0
        core_max = CORE_MAX_HP
        try:
            if core_max > 0 and c.get_hp() < int(0.8 * core_max):
                desired_healer_count = 1
        except GameError:
            pass

        try:
            if core_max > 0 and c.get_hp() < int(0.7 * core_max):
                if self.target_defender_count < 1:
                    self.target_defender_count = 1
        except GameError:
            pass

        # Ammunition Auto-Conversion Loop (convert Ti to Ammunition if low)
        try:
            ti = c.get_global_resources()
            turret_count = self._count_friendly_turrets(c)
            if turret_count > 0 and ti >= 50:
                if c.can_convert_ammo(20):
                    c.convert_ammo(20)
        except GameError:
            pass

        try:
            ti_late = c.get_global_resources()
            if rnd >= 300 and ti_late > 800 and self.target_economy_count < 2:
                self.target_economy_count = 2
            if rnd >= 600 and ti_late > 1200 and self.target_economy_count < 3:
                self.target_economy_count = 3
            if rnd >= 900 and ti_late > 1800 and self.target_economy_count < 4:
                self.target_economy_count = 4
            if rnd >= 1200 and ti_late > 2400 and self.target_economy_count < 5:
                self.target_economy_count = 5
        except GameError:
            pass

        if c.get_action_cooldown() == 0 and self._spawn_round_cooldown_ready(rnd):
            if economy_count < self.target_economy_count:
                if self._spawn_builder_for_role(c, ROLE_ECONOMY):
                    return

            if defender_count < self.target_defender_count:
                if self._spawn_builder_for_role(c, ROLE_DEFENDER):
                    return

            if healer_count < desired_healer_count:
                if self._spawn_builder_for_role(c, ROLE_HEALER):
                    return

            try:
                ti = c.get_global_resources()
                builder_cost = c.get_builder_bot_cost() if hasattr(c, "get_builder_bot_cost") else 30
            except GameError:
                ti = 0
                builder_cost = 30

            miner_count = len(self.miner_bot_ids)
            raider_count = len(self.raider_bot_ids)

            harvester_count = self._count_visible_friendly_harvesters(c)
            combat_econ_ready = harvester_count >= 2 or rnd >= 300

            if (
                self.defense_complete
                and rnd >= 70
                and combat_econ_ready
                and (rnd - self._last_miner_wave_round) >= self.miner_wave_interval
                and ti >= builder_cost + 50
            ):
                if self._spawn_builder_for_role(c, ROLE_MINER):
                    return

            repair_count = len(self.repair_patrol_bot_ids)
            first_repair_ok = repair_count == 0
            second_repair_ok = repair_count >= 1 and rnd >= 600 and ti >= 1500
            if (
                self.defense_complete
                and rnd >= 80
                and self.economy_spawned
                and harvester_count >= 3
                and repair_count < self.target_repair_patrol_count
                and (first_repair_ok or second_repair_ok)
                and ti >= builder_cost + 30
            ):
                if self._spawn_builder_for_role(c, ROLE_REPAIR_PATROL):
                    return

            if (
                self.defense_complete
                and rnd >= 120
                and combat_econ_ready
            ):
                max_raiders = _max_raiders_for_round(rnd)
                force_raider_round = rnd in (
                    200, 280, 360, 440, 520, 600, 700, 800, 900,
                    1000, 1100, 1200, 1300, 1400, 1500, 1600,
                    1700, 1800, 1900,
                )
                ti_floor = builder_cost if force_raider_round else builder_cost + 40
                if raider_count < max_raiders and ti >= ti_floor:
                    if self._spawn_builder_for_role(c, ROLE_RAIDER):
                        return

        if not self.defense_complete and self.target_defender_count == 0:
            harvester_count_def = self._count_visible_friendly_harvesters(c)
            if harvester_count_def >= 3 and rnd >= 80:
                self.defense_complete = True
                log_event(
                    rnd, c.get_id(), "core", f"({cx},{cy})",
                    "defense_complete_bypass",
                    harvesters=harvester_count_def,
                )

        if self.spawned_builder and not self.defense_complete:
            # Check store slot for defense completion signal
            try:
                def_msg = c.read_store(STORE_DEFENSE_STATUS)
                if def_msg == MSG_DEFENSE_COMPLETE:
                    self.defense_complete = True
            except GameError:
                pass

    def _run_bot(self, c: Controller):
        if not self.initialised:
            self._init_game(c)

        if not self.initialised:
            return

        self._update_local_map(c, EntityType.BUILDER_BOT,
                               MAP_UPDATE_EVERY_N_BUILDER)

        # First tick: read dedicated per-unit store slot or fallback slot 0 to determine assigned role
        if self.builder_state is None and self.role is None:
            try:
                unit_slot = (c.get_id() % 12) + 4
                store_role = c.read_store(unit_slot)
                if store_role in VALID_ROLES:
                    assigned_role = store_role
                else:
                    assigned_role = ROLE_ECONOMY
            except GameError:
                assigned_role = ROLE_ECONOMY

            self.role = assigned_role

            if assigned_role == ROLE_ECONOMY:
                self.builder_state = EconomyState(self.core_pos)
            elif assigned_role == ROLE_DEFENDER:
                self.builder_state = DefenderState(self.core_pos, self.map_w, self.map_h)
            elif assigned_role == ROLE_HEALER:
                self.builder_state = HealerState(self.core_pos)
            elif assigned_role == ROLE_MINER:
                self.builder_state = MinerState(self.core_pos, c.get_id())
            elif assigned_role == ROLE_RAIDER:
                self.builder_state = RaiderState(self.core_pos, self.map_w, self.map_h)
            elif assigned_role == ROLE_REPAIR_PATROL:
                self.builder_state = RepairPatrolState(self.core_pos)
            else:
                self.builder_state = EconomyState(self.core_pos)

        if self.role == ROLE_ECONOMY:
            run_economy(c, self.builder_state, self.local_map)
        elif self.role == ROLE_DEFENDER:
            run_defender(c, self.builder_state)
        elif self.role == ROLE_HEALER:
            run_healer(c, self.builder_state)
        elif self.role == ROLE_MINER:
            run_miner(c, self.builder_state, self.local_map)
        elif self.role == ROLE_RAIDER:
            run_raider(c, self.builder_state, self.local_map)
        elif self.role == ROLE_REPAIR_PATROL:
            run_repair_patrol(c, self.builder_state, self.local_map)

    def _run_turret(self, c: Controller):
        if not self.initialised:
            self._init_game(c)

        if not self.initialised:
            return

        self._update_local_map(c, c.get_entity_type(), MAP_UPDATE_EVERY_N_TURRET)

    def _run_launcher(self, c: Controller):
        if not self.initialised:
            self._init_game(c)

        if not self.initialised:
            return

        self._update_local_map(c, EntityType.LAUNCHER, MAP_UPDATE_EVERY_N_TURRET)
