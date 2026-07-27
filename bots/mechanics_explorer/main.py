from fcode import Controller, Direction, EntityType, Position, Environment
from hivemind import HiveMind

def log(msg):
    print(f"[TEST] {msg}")

class Player:
    def __init__(self):
        self.hivemind = HiveMind()
        self.turn = 0

    def run(self, ct: Controller) -> None:
        self.turn = ct.get_current_round()
        try:
            self.hivemind.tick(ct)
        except Exception as e:
            log(f"HiveMind tick error: {e}")
            
        kind = ct.get_entity_type()
        
        if kind == EntityType.CORE:
            if self.turn == 1:
                # Spawn a builder
                for d in [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]:
                    p = ct.get_position().add(d)
                    if ct.can_spawn(p):
                        ct.spawn_builder(p)
                        log(f"Core {ct.get_team().name} spawned explorer builder at {p.x},{p.y}")
                        break
            if self.turn % 50 == 0:
                known_tiles = len(self.hivemind.local_map)
                known_walls = sum(1 for v in self.hivemind.local_map.values() if not v.get("passable", True))
                log(f"Core {ct.get_team().name} HiveMind sync: {known_tiles} tiles mapped, {known_walls} walls found.")

        elif kind == EntityType.BUILDER_BOT:
            if ct.get_move_cooldown() == 0:
                pos = ct.get_position()
                target = Position(24, 0) if ct.get_team().name == "A" else Position(1, 25)
                
                if pos == target:
                    if self.turn % 50 == 0:
                        log(f"Builder {ct.get_id()} holding position at {target.x},{target.y}!")
                else:
                    d = self._pathfind(ct, pos, target)
                    if d != Direction.CENTRE and ct.can_move(d):
                        ct.move(d)

    def _pathfind(self, ct: Controller, start: Position, target: Position) -> Direction:
        import collections
        import random
        
        queue = collections.deque([(start, [])])
        visited = { (start.x, start.y) }
        
        w = ct.get_map_width()
        h = ct.get_map_height()
        
        while queue:
            curr, path = queue.popleft()
            if curr == target:
                return path[0] if path else Direction.CENTRE
                
            for d in [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]:
                nxt = curr.add(d)
                if 0 <= nxt.x < w and 0 <= nxt.y < h:
                    pos_tuple = (nxt.x, nxt.y)
                    if pos_tuple not in visited:
                        # Use hivemind local_map!
                        # If it's not in local_map, assume passable to encourage exploration!
                        passable = True
                        if pos_tuple in self.hivemind.local_map:
                            passable = self.hivemind.local_map[pos_tuple].get("passable", True)
                            
                        if passable:
                            visited.add(pos_tuple)
                            queue.append((nxt, path + [d]))
                        
        # Fallback to random if no path found
        dirs = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]
        random.shuffle(dirs)
        for d in dirs:
            if ct.can_move(d):
                return d
        return Direction.CENTRE

