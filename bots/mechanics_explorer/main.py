from fcode import Controller, Direction, EntityType, Position

class BuilderTasks:
    def __init__(self):
        self.tasks = [
            ("GOTO", Position(4, 1)),
            ("GOTO", Position(6, 1)),
            ("BUILD", "SPLITTER", Position(5, 1), Direction.WEST),
            ("GOTO", Position(5, 1)),
            ("BUILD", "HARVESTER", Position(6, 1), None),
            ("BUILD", "CONVEYOR", Position(4, 1), Direction.WEST),
            ("GOTO", Position(4, 1)),
            ("BUILD", "CONVEYOR", Position(3, 1), Direction.WEST),
            
            # Build Sentinel at 5,2 facing SOUTH while standing on 4,1
            ("BUILD", "SENTINEL", Position(5, 2), Direction.SOUTH),
            
            # Build Gunner at 5,0 facing EAST while standing on 4,1
            ("BUILD", "GUNNER", Position(5, 0), Direction.EAST),
            
            # Test direct harvester feed: Move to 4,3
            ("GOTO", Position(4, 3)),
            ("BUILD", "SENTINEL", Position(5, 4), Direction.EAST),
            ("BUILD", "HARVESTER", Position(4, 4), None),
            
            # move to 8,2 and build a barrier at (7,2)
            ("GOTO", Position(8, 2)),
            ("BUILD", "BARRIER", Position(7, 2), None),
        ]
        self.idx = 0

    def current(self):
        if self.idx < len(self.tasks):
            return self.tasks[self.idx]
        return None

    def advance(self):
        self.idx += 1


class Player:
    def __init__(self):
        self.printed_info = False
        self.builder_tasks = BuilderTasks()
        self.core_spawned = False
        self.turn = 0

    def run(self, ct: Controller) -> None:
        self.turn += 1
        kind = ct.get_entity_type()
        
        if not self.printed_info:
            pos = ct.get_position()
            hp = ct.get_hp()
            max_hp = ct.get_max_hp()
            vision = ct.get_vision_radius_sq()
            print(f"[{kind.name} {ct.get_id()}] Created at ({pos.x}, {pos.y}). HP: {hp}/{max_hp}, Vision r2: {vision}")
            self.printed_info = True

        if kind == EntityType.CORE:
            self._run_core(ct)
        elif kind == EntityType.BUILDER_BOT:
            self._run_builder(ct)
        elif kind in (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER):
            self._run_turret(ct)
            
    def _run_core(self, ct: Controller) -> None:
        if ct.get_action_cooldown() == 0:
            if ct.get_unit_count() == 1 and ct.get_global_resources() >= ct.get_builder_bot_cost():
                spawn_pos = Position(3, 1)
                if ct.can_spawn(spawn_pos):
                    ct.spawn_builder(spawn_pos)
                    print(f"[CORE] Spawned explorer builder at {spawn_pos.x}, {spawn_pos.y}")

    def _pathfind(self, ct: Controller, start: Position, target: Position) -> Direction:
        import collections
        import random
        
        queue = collections.deque([(start, [])])
        visited = {start}
        
        w = ct.get_map_width()
        h = ct.get_map_height()
        
        while queue:
            curr, path = queue.popleft()
            if curr == target:
                return path[0] if path else Direction.CENTRE
                
            for d in [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]:
                nxt = curr.add(d)
                if 0 <= nxt.x < w and 0 <= nxt.y < h:
                    if nxt not in visited:
                        try:
                            passable = ct.is_tile_passable(nxt)
                        except Exception:
                            # Tile is out of vision, assume impassable for BFS
                            passable = False
                            
                        if passable:
                            visited.add(nxt)
                            queue.append((nxt, path + [d]))
                        
        # Fallback to random if no path found
        dirs = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]
        random.shuffle(dirs)
        for d in dirs:
            if ct.can_move(d):
                return d
        return Direction.CENTRE

    def _run_builder(self, ct: Controller) -> None:
        task = self.builder_tasks.current()
        if task is None:
            # Target practice loop
            pos = ct.get_position()
            target = Position(8, 2)
            if pos != target and ct.get_move_cooldown() == 0:
                d = self._pathfind(ct, pos, target)
                if d != Direction.CENTRE and ct.can_move(d):
                    ct.move(d)
                return
                
            if pos == target and ct.get_action_cooldown() == 0:
                # We are at 8,2. Check barrier at 7,2
                barrier_id = None
                for eid in ct.get_nearby_buildings(2):
                    if ct.get_entity_type(eid) == EntityType.BARRIER and ct.get_position(eid) == Position(7, 2):
                        barrier_id = eid
                        break
                        
                if barrier_id is None:
                    if ct.can_build_barrier(Position(7, 2)) and ct.get_global_resources() >= ct.get_barrier_cost():
                        ct.build_barrier(Position(7, 2))
                        return
                else:
                    # Heal barrier if damaged
                    if ct.get_hp(barrier_id) < ct.get_max_hp(barrier_id) and ct.can_heal(Position(7, 2)) and ct.get_global_resources() >= 1:
                        ct.heal(Position(7, 2))
                        return
                        
                # Heal ourselves
                if ct.get_hp() < ct.get_max_hp() and ct.can_heal(pos) and ct.get_global_resources() >= 1:
                    ct.heal(pos)
                    return
            return

        cmd = task[0]
        if cmd == "GOTO":
            target = task[1]
            pos = ct.get_position()
            if pos == target:
                print(f"[BUILDER] Reached {target.x},{target.y}")
                self.builder_tasks.advance()
                return
                
            if ct.get_move_cooldown() == 0:
                d = self._pathfind(ct, pos, target)
                if d != Direction.CENTRE and ct.can_move(d):
                    ct.move(d)
                            
        elif cmd == "BUILD":
            btype = task[1]
            pos = task[2]
            facing = task[3]
            
            if ct.get_action_cooldown() != 0:
                return
                
            ti = ct.get_global_resources()
            cost = 0
            if btype == "SPLITTER": cost = ct.get_splitter_cost()
            elif btype == "HARVESTER": cost = ct.get_harvester_cost()
            elif btype == "CONVEYOR": cost = ct.get_conveyor_cost()
            elif btype == "SENTINEL": cost = ct.get_sentinel_cost()
            elif btype == "GUNNER": cost = ct.get_gunner_cost()
            elif btype == "LAUNCHER": cost = ct.get_launcher_cost()
            elif btype == "BARRIER": cost = ct.get_barrier_cost()
            
            if ti < cost:
                return
                
            built = False
            if btype == "SPLITTER" and ct.can_build_splitter(pos, facing):
                ct.build_splitter(pos, facing)
                built = True
            elif btype == "HARVESTER" and ct.can_build_harvester(pos):
                ct.build_harvester(pos)
                built = True
            elif btype == "CONVEYOR" and ct.can_build_conveyor(pos, facing):
                ct.build_conveyor(pos, facing)
                built = True
            elif btype == "SENTINEL" and ct.can_build_sentinel(pos, facing):
                ct.build_sentinel(pos, facing)
                built = True
            elif btype == "GUNNER" and ct.can_build_gunner(pos, facing):
                ct.build_gunner(pos, facing)
                built = True
            elif btype == "LAUNCHER" and ct.can_build_launcher(pos):
                ct.build_launcher(pos)
                built = True
            elif btype == "BARRIER" and ct.can_build_barrier(pos):
                ct.build_barrier(pos)
                built = True
                
            if built:
                print(f"[BUILDER] Successfully built {btype} at {pos.x},{pos.y}")
                self.builder_tasks.advance()
            else:
                print(f"[BUILDER] Error: Failed to build {btype} at {pos.x},{pos.y}. Skipping...")
                self.builder_tasks.advance()

    def _run_turret(self, ct: Controller) -> None:
        kind = ct.get_entity_type()
        
        # 1. Handle rotation every 10 turns
        if self.turn % 10 == 0 and ct.get_action_cooldown() == 0:
            if kind == EntityType.GUNNER:
                d = ct.get_direction().rotate_right() # Clockwise
                if hasattr(ct, "can_rotate") and ct.can_rotate(d):
                    ct.rotate(d)
                    return
            elif kind == EntityType.SENTINEL:
                d = ct.get_direction().rotate_left() # Counter-clockwise
                if hasattr(ct, "can_rotate") and ct.can_rotate(d):
                    ct.rotate(d)
                    return
                    
        # 2. Target practice firing
        if ct.get_action_cooldown() == 0:
            target = Position(8, 2)
            if ct.can_fire(target):
                ct.fire(target)
                return

        if self.turn % 50 != 0:
            return
            
        ammo = ct.get_ammo_amount()
        
        target_list = []
        try:
            tiles = ct.get_attackable_tiles()
            target_list = [(t.x, t.y) for t in tiles]
        except Exception:
            target_list = ["(Any within r2=26)"]
            
        print(f"[{kind.name} {ct.get_id()}] Round {self.turn} - Ammo: {ammo}, Attackable: {target_list}")
