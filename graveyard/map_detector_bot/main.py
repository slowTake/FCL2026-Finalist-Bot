from fcode import Controller, EntityType, Direction, Position
from hivemind import HiveMind

class Player:
    def __init__(self):
        self.hivemind = HiveMind()
        self.spawned_builders = 0

    def print_core_map(self, ct: Controller):
        w = ct.get_map_width()
        h = ct.get_map_height()
        print(f"--- CORE LOCAL MAP (Turn {self.hivemind.global_turn}) ---")
        for y in range(h):
            row = ""
            for x in range(w):
                if (x, y) not in self.hivemind.local_map:
                    row += "?"
                elif not self.hivemind.local_map[(x, y)]["passable"]:
                    row += "#"
                else:
                    row += "."
            print(row)
        print("---------------------------")

    def get_next_step_to_unknown(self, ct: Controller) -> Position | None:
        pos = ct.get_position()
        w = ct.get_map_width()
        h = ct.get_map_height()

        queue = [(pos, [])]
        visited = { (pos.x, pos.y) }
        iterations = 0

        while queue and iterations < 400:
            iterations += 1
            curr, path = queue.pop(0)

            for d in [Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST]:
                nxt = curr.add(d)
                nx, ny = nxt.x, nxt.y
                if 0 <= nx < w and 0 <= ny < h:
                    if (nx, ny) not in visited:
                        visited.add((nx, ny))
                        if (nx, ny) not in self.hivemind.local_map:
                            return path[0] if path else nxt
                        if self.hivemind.local_map[(nx, ny)].get("passable", False):
                            queue.append((nxt, path + [nxt]))
        return None

    def run(self, ct: Controller) -> None:
        # Plug & Play Hivemind execution
        self.hivemind.tick(ct)

        if ct.get_entity_type() == EntityType.CORE:
            # Macro Strategy: Spawn exactly 2 builders for exploration test
            if self.spawned_builders < 2:
                for d in [Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST]:
                    if self.hivemind.spawn_builder(ct, ct.get_position().add(d)):
                        self.spawned_builders += 1
                        break

            # Print synced map on sync turns (Odd)
            if self.hivemind.global_turn % 2 != 0:
                self.print_core_map(ct)

        elif ct.get_entity_type() == EntityType.BUILDER_BOT:
            # Micro Strategy: Explore the unknown
            next_pos = self.get_next_step_to_unknown(ct)
            if next_pos:
                curr = ct.get_position()
                if next_pos.x > curr.x and ct.can_move(Direction.EAST): ct.move(Direction.EAST)
                elif next_pos.x < curr.x and ct.can_move(Direction.WEST): ct.move(Direction.WEST)
                elif next_pos.y > curr.y and ct.can_move(Direction.SOUTH): ct.move(Direction.SOUTH)
                elif next_pos.y < curr.y and ct.can_move(Direction.NORTH): ct.move(Direction.NORTH)
