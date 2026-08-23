from fcode import Controller, EntityType, Position, Environment, Direction, Team
from enum import IntEnum
from features import KNOWN_MAPS, MAP_ID_TO_NAME, WALL_LOCATIONS, ORE_LOCATIONS
from vision_v2 import pack_ledger_slot, unpack_ledger_slot


class CoreState(IntEnum):
    UNKNOWN = 0
    ASSUMED = 1
    KNOWN = 2


def pack_cores(ally_state, ally_x, ally_y, enemy_state, enemy_x, enemy_y) -> int:
    ally_bits = (ally_state << 14) | (ally_x << 7) | ally_y
    enemy_bits = (enemy_state << 14) | (enemy_x << 7) | enemy_y
    return (enemy_bits << 16) | ally_bits


def unpack_cores(val: int):
    ally_bits = val & 0xFFFF
    enemy_bits = (val >> 16) & 0xFFFF
    ally = (CoreState(ally_bits >> 14), (ally_bits >> 7) & 0x7F, ally_bits & 0x7F)
    enemy = (CoreState(enemy_bits >> 14), (enemy_bits >> 7) & 0x7F, enemy_bits & 0x7F)
    return ally, enemy


class HiveMind:
    def __init__(self):
        self.local_map = {}
        self.global_turn = 0
        self.my_index = 0
        self.birth_round: int | None = None
        # Slot 4 as read on this unit's first tick. Named for what it actually
        # holds: the builder count INCLUDING this unit (the spawn write is already
        # visible by then), not the count before it. my_index = this + 1.
        self._birth_builder_count: int | None = None
        self.map_id = 0
        self.map_name = "UNKNOWN"
        self.enemy_core_pos = None  # set on fingerprint from KNOWN_MAPS
        self._map_detected = False
        self._walls_loaded = False
        self.last_communicated_ledger = {}  # (x,y) -> (etype, is_enemy, d)

    def _detect_and_store_map(self, ct: Controller) -> None:
        if self._map_detected: return

        w = ct.get_map_width()
        h = ct.get_map_height()
        team = ct.get_team()
        pos = ct.get_position()

        wall_count = 0
        for tile in ct.get_nearby_tiles():
            if ct.get_tile_env(tile) == Environment.WALL:
                wall_count += 1

        fingerprint = (w, h, team, pos.x, pos.y, wall_count)
        enemy_state = CoreState.UNKNOWN
        enemy_x = 0
        enemy_y = 0

        if fingerprint in KNOWN_MAPS:
            self.map_id, self.map_name, enemy_x, enemy_y = KNOWN_MAPS[fingerprint]
            enemy_state = CoreState.ASSUMED
            self.enemy_core_pos = Position(enemy_x, enemy_y)
        
        ct.write_store(14, self.map_id)

        packed = pack_cores(
            CoreState.KNOWN, pos.x, pos.y,
            enemy_state, enemy_x, enemy_y
        )
        ct.write_store(15, packed)
        self._map_detected = True

    def _update_local_vision(self, ct: Controller):
        pos = ct.get_position()
        w = ct.get_map_width()
        h = ct.get_map_height()

        entity_positions = {}
        self.visible_entities = []
        for eid in ct.get_nearby_entities():
            epos = ct.get_position(eid)
            etype = ct.get_entity_type(eid)
            eteam = ct.get_team(eid)
            self.visible_entities.append({"id": eid, "pos": epos, "type": etype, "team": eteam})
            # src="vision": personally observed, as opposed to a network-
            # ingested sighting (see _ingest_ledger) — _broadcast_ledger only
            # ever reports src="vision" entries, so a unit never re-broadcasts
            # a rumor it merely heard about from a teammate.
            ent_data = {"type": etype, "team": eteam, "src": "vision"}
            if etype in (EntityType.CONVEYOR, EntityType.SPLITTER, EntityType.GUNNER, EntityType.SENTINEL):
                try:
                    ent_data["direction"] = ct.get_direction(eid)
                except Exception:
                    pass
            if etype == EntityType.CORE:
                entity_positions[(epos.x, epos.y)] = ent_data
                entity_positions[(epos.x + 1, epos.y)] = ent_data
                entity_positions[(epos.x, epos.y + 1)] = ent_data
                entity_positions[(epos.x + 1, epos.y + 1)] = ent_data
            else:
                entity_positions[(epos.x, epos.y)] = ent_data

        for dy in range(-6, 7):
            for dx in range(-6, 7):
                px = pos.x + dx
                py = pos.y + dy
                if 0 <= px < w and 0 <= py < h:
                    pos_obj = Position(px, py)
                    if not ct.is_in_vision(pos_obj):
                        continue
                            
                    if (px, py) not in self.local_map:
                        self.local_map[(px, py)] = {}
                    self.local_map[(px, py)]["last_seen_turn"] = self.global_turn
                    if (px, py) in entity_positions:
                        ent_type = entity_positions[(px, py)]["type"]
                        if ent_type in (EntityType.CONVEYOR, EntityType.SPLITTER):
                            self.local_map[(px, py)]["passable"] = True
                        else:
                            self.local_map[(px, py)]["passable"] = False
                        self.local_map[(px, py)]["entity"] = entity_positions[(px, py)]
                    else:
                        env_tile = ct.get_tile_env(pos_obj)
                        is_wall = (env_tile == Environment.WALL)
                        self.local_map[(px, py)]["passable"] = not is_wall
                        if env_tile == Environment.ORE_TITANIUM:
                            self.local_map[(px, py)]["ore"] = True
                            
                        if "entity" in self.local_map[(px, py)]:
                            del self.local_map[(px, py)]["entity"]

    def _broadcast_ledger(self, ct: Controller):
        pos = ct.get_position()
        w = ct.get_map_width()
        h = ct.get_map_height()
        deltas = []
        
        # Always include our own position FIRST!
        # This guarantees liveness reporting.
        my_team = ct.get_team()
        deltas.append((pos.x, pos.y, EntityType.BUILDER_BOT, False, Direction.NORTH))
        self.last_communicated_ledger[(pos.x, pos.y)] = (EntityType.BUILDER_BOT, False, Direction.NORTH)

        # Iterate vision to find changed entities
        for priority_pass in range(3):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    if dx*dx + dy*dy > 16: continue
                    px = pos.x + dx
                    py = pos.y + dy
                    if 0 <= px < w and 0 <= py < h:
                        pos_tuple = (px, py)
                        ent = self.local_map.get(pos_tuple, {}).get("entity")
                        if ent is not None and ent.get("src") == "network":
                            # Never re-broadcast a sighting we only know about
                            # from a teammate's earlier broadcast — otherwise
                            # a rumor keeps getting relayed (and its
                            # last_seen_turn keeps getting refreshed) by
                            # whichever unit's turn it is next, long after the
                            # real thing might be gone. Only report what this
                            # unit has personally seen (src="vision" in
                            # _update_local_vision, or nothing yet).
                            ent = None

                        etype = ent.get("type") if ent else None
                        is_enemy = (ent.get("team") != my_team) if ent else False
                        d = ent.get("direction", Direction.NORTH) if ent else Direction.NORTH
                        
                        # WALL/ORE terrain sentinels are only useful before the
                        # team knows the map: once map_id is nonzero, tick()'s
                        # step 4 has already loaded the static WALL_LOCATIONS/
                        # ORE_LOCATIONS tables into local_map for every unit
                        # (including this one), so broadcasting terrain here is
                        # pure noise competing with real entity sightings for
                        # the 16-delta budget. This used to check
                        # self._map_detected instead, which _detect_and_store_map
                        # only ever sets True on the core (round 0) — every
                        # builder's own copy stayed permanently False, so every
                        # builder broadcast fake WALL/ORE entities on every empty
                        # tile it saw for the entire match. self.map_id is the
                        # fact that's actually propagated to every unit (tick()
                        # step 4 reads it off the store once the core has
                        # written it), so it's the right signal here.
                        if not ent and self.map_id == 0:
                            tile_data = self.local_map.get(pos_tuple, {})
                            if not tile_data.get("passable", True):
                                etype = "WALL"
                            elif tile_data.get("ore", False):
                                etype = "ORE"
                        
                        # Priority 1: Enemy Core, Gunners, Sentinels
                        # Priority 2: Enemy Harvesters, Builders
                        # Priority 3: Ally logistics / other
                        if priority_pass == 0:
                            if not is_enemy or etype not in (EntityType.CORE, EntityType.GUNNER, EntityType.SENTINEL):
                                continue
                        elif priority_pass == 1:
                            if not is_enemy or etype not in (EntityType.HARVESTER, EntityType.BUILDER_BOT):
                                continue
                        elif priority_pass == 2:
                            if is_enemy and etype in (EntityType.CORE, EntityType.GUNNER, EntityType.SENTINEL, EntityType.HARVESTER, EntityType.BUILDER_BOT):
                                continue
                                
                        current_state = (etype, is_enemy, d)
                        last_state = self.last_communicated_ledger.get(pos_tuple)
                        
                        # Only broadcast if changed
                        if current_state != last_state:
                            deltas.append((px, py, etype, is_enemy, d))
                            self.last_communicated_ledger[pos_tuple] = current_state
                            
                            if len(deltas) >= 16:
                                break
                if len(deltas) >= 16:
                    break
            if len(deltas) >= 16:
                break
                
        # Fill rest with None
        while len(deltas) < 16:
            deltas.append(None)
            
        # Pack into Slots 5 through 12
        for i in range(8):
            val = pack_ledger_slot(deltas[i*2], deltas[i*2 + 1])
            ct.write_store(5 + i, val)

    def _sync_ledger(self, ct: Controller):
        all_zero = True
        slots_data = []
        for i in range(8):
            val = ct.read_store(5 + i)
            if val != 0: all_zero = False
            slots_data.append(val)
            
        return all_zero, slots_data

    def _ingest_ledger(self, ct: Controller, slots_data) -> None:
        """Merge this round's broadcast deltas into local_map.

        Runs for every unit, not just the core — this is what makes the
        ledger actually team-shared. Each written entity is tagged
        src="network" so _broadcast_ledger never re-broadcasts it (a unit
        should only ever report what it personally saw, not echo a rumor it
        heard secondhand — an echoed entry's last_seen_turn would otherwise
        get refreshed indefinitely by whichever unit's turn it is to
        broadcast next, long after the real thing might be gone).
        _update_local_vision (step 6, always last) tags its own writes
        src="vision" and overwrites whatever's here the moment a unit sees
        the tile with its own eyes.

        last_seen_turn is stamped with this unit's current turn, not the
        original observation turn — the packed wire format (vision_v2.py) has
        no spare bits for one (16 bits per target, all spoken for). The
        freshness signal is therefore approximate, not exact: broadcast data
        is already up to `total_builders` rounds old by the time it's sent
        (the metronome only gives each builder a broadcast turn once per
        full cycle), and ingest doesn't try to recover the original age on
        top of that. Accepted rather than changing the wire format.
        """
        my_team = ct.get_team()
        other_team = Team.B if my_team == Team.A else Team.A
        for val in slots_data:
            t0, t1 = unpack_ledger_slot(val)
            for t in (t0, t1):
                if not t:
                    continue
                tx, ty, etype, is_enemy, d = t
                pos_tuple = (tx, ty)
                if pos_tuple not in self.local_map:
                    self.local_map[pos_tuple] = {}
                cell = self.local_map[pos_tuple]

                if etype is None:
                    # Empty/deleted entity.
                    if "entity" in cell:
                        del cell["entity"]
                    continue

                actual_team = other_team if is_enemy else my_team
                cell["entity"] = {
                    "type": etype,
                    "team": actual_team,
                    "direction": d,
                    "src": "network",
                }
                # CONVEYOR/SPLITTER and the "ORE" terrain sentinel are all
                # walkable; the "WALL" sentinel and every other real entity
                # type occupy the tile. Previously ORE fell into the
                # not-CONVEYOR/SPLITTER "impassable" branch alongside WALL —
                # harmless while only the core (which never paths) ingested,
                # but wrong the moment every unit's pathfinder reads this.
                if etype in (EntityType.CONVEYOR, EntityType.SPLITTER, "ORE"):
                    cell["passable"] = True
                else:
                    cell["passable"] = False
                if etype == "ORE":
                    cell["ore"] = True
                cell["last_seen_turn"] = self.global_turn

    def spawn_builder(self, ct: Controller, pos) -> bool:
        """Helper to spawn a builder and instantly register it with the HiveMind registry."""
        if ct.can_spawn(pos):
            ct.spawn_builder(pos)
            # Slot 4 is now the total builders
            total = ct.read_store(4)
            ct.write_store(4, total + 1)
            return True
        return False

    def tick(self, ct: Controller):
        """Must be called at the very top of every unit's run() method."""
        is_core = (ct.get_entity_type() == EntityType.CORE)

        # 1. Sync global turn natively from the engine
        self.global_turn = ct.get_current_round()

        # 2. Assign builder index from the slot-4 count read at birth.
        #
        # THE READ INCLUDES THIS UNIT. By the newborn's first tick the core's
        # spawn increment (write_store(4, total + 1), in spawn_builder) is
        # already visible, so slot 4 reads "builders including me", not
        # "builders before me". Verified empirically on every map with
        # COUNT_DEBUG: the first builder reads 1 and takes my_index 2, the
        # second reads 2 and takes 3. **The produced indices are therefore
        # 2..N+1 — index 1 is never assigned to anybody.**
        #
        # This comment used to assert the opposite ("the spawn that just
        # created this very unit has not landed yet"), reasoning from
        # write_store being buffered one round. That premise is wrong here,
        # because a newborn does not act on its spawn round — a full round
        # boundary passes before its first tick. The false premise is what
        # hid a real bug: the ledger metronome in step 5 cycled 1..N to
        # match, so it wasted every `current == 1` round on nobody and never
        # reached the newest builder at all. Anything keying off this value
        # must use the real 2..N+1 range — see respawn._MYINDEX_OFFSET (= 2)
        # and the metronome below, which must stay in agreement with it.
        #
        # Still captured on the FIRST tick (birth_round) rather than
        # re-read later, and that reasoning is unchanged: a later read gets
        # whatever is THEN current, which is no longer this unit's own count
        # if a second builder spawned in the intervening round — both
        # newborns' reads land on the same advanced value and both claim the
        # same index (observed directly, two live units with the same
        # my_index one round apart, count-vs-baron on valkyrie). Publication
        # still waits until birth_round + 1, so my_index == 0 remains the
        # "not yet assigned" signal for exactly one round — some callers
        # (e.g. baron's guardianRankAlive) treat that timing as an invariant.
        if ct.get_entity_type() == EntityType.BUILDER_BOT and self.my_index == 0:
            if self.birth_round is None:
                self.birth_round = self.global_turn
                self._birth_builder_count = ct.read_store(4)
            elif self.global_turn == self.birth_round + 1:
                if self._birth_builder_count is not None:
                    self.my_index = self._birth_builder_count + 1

        # 3. Process Map Detection (Core Only)
        if is_core and self.global_turn == 0:
            self._detect_and_store_map(ct)

        # 4. Read static map knowledge if we haven't
        if self.map_id == 0 and self.global_turn > 0:
            self.map_id = ct.read_store(14)
            
        if self.map_id != 0 and not self._walls_loaded:
            if self.map_id in WALL_LOCATIONS:
                for wx, wy in WALL_LOCATIONS[self.map_id]:
                    if (wx, wy) not in self.local_map:
                        self.local_map[(wx, wy)] = {}
                    self.local_map[(wx, wy)]["passable"] = False
                    
            if self.map_id in ORE_LOCATIONS:
                for ox, oy in ORE_LOCATIONS[self.map_id]:
                    if (ox, oy) not in self.local_map:
                        self.local_map[(ox, oy)] = {}
                    self.local_map[(ox, oy)]["passable"] = True
                    self.local_map[(ox, oy)]["ore"] = True
                    
            self._walls_loaded = True

        # 5. Broadcast/ingest ledger protocol. Every unit ingests whatever the
        # store currently holds into its own local_map — this is what makes
        # the ledger team-shared rather than core-only (previously this whole
        # step, ingest included, only ran `if is_core`, so a teammate's
        # sighting only ever reached the core's own copy of local_map and
        # every other unit stayed ignorant of it). Metronome advance + slot
        # clearing stays core-only: it's a shared-state write that needs
        # exactly one owner so two units can't race to pick the next
        # broadcaster. Builders still only ever *write* on their own assigned
        # turn, unchanged.
        if self.global_turn > 0:
            is_dead, slots_data = self._sync_ledger(ct)
            if not is_dead:
                self._ingest_ledger(ct, slots_data)

            if is_core:
                total_builders = ct.read_store(4)
                if total_builders > 0:
                    # Cycle over 2..total_builders+1, NOT 1..total_builders.
                    # Builders self-assign my_index = _birth_builder_count + 1
                    # (step 2), and that count already includes the unit itself, so
                    # the produced indices are 2..total_builders+1 — index 1 is
                    # never assigned and the newest builder is total_builders+1.
                    # A 1-based cycle therefore wasted every `current == 1` round
                    # on nobody AND never reached the newest builder (it stopped
                    # at total_builders), so ~1/N of broadcasts were dropped and
                    # the last builder was permanently mute. Match the real index
                    # range exactly. (Shared by count/baron/bandit via symlink.)
                    current = ct.read_store(13)
                    if current < 2:
                        current = 2
                    else:
                        current += 1
                        if current > total_builders + 1:
                            current = 2
                    ct.write_store(13, current)
                    # Clear slots
                    for i in range(8):
                        ct.write_store(5 + i, 0)

        if not is_core:
            # BUILDER: Check if it's our turn to broadcast
            current = ct.read_store(13)
            if current == self.my_index:
                self._broadcast_ledger(ct)

        # 6. Always update personal vision LAST (so it overrides stale network data)
        self._update_local_vision(ct)

        # 7. Garbage-collect stale ally-builder ghosts. Every unit runs this
        # now (used to be core-only) — un-gating ingest above without also
        # un-gating this would let every unit accumulate permanent phantom
        # ally-builder obstacles at any tile a teammate has ever stood on:
        # every broadcast injects the sender's own position as a BUILDER_BOT
        # entity (see _broadcast_ledger's "liveness reporting" comment),
        # ingest marks it impassable, and nothing but this step ever clears
        # it. These ghosts feed walkPassable/relayPassable, i.e. the A* every
        # role paths with, so leaving this core-only would have poisoned
        # pathing for every other unit the moment ingest went team-wide.
        total_builders = ct.read_store(4)
        stale_threshold = self.global_turn - (total_builders + 2)
        for p, cell in list(self.local_map.items()):
            if "entity" in cell:
                ent = cell["entity"]
                if ent["type"] == EntityType.BUILDER_BOT and ent["team"] == ct.get_team():
                    if cell.get("last_seen_turn", 0) < stale_threshold:
                        del cell["entity"]
                        cell["passable"] = True
