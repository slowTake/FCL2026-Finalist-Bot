import os
import sys
from pathlib import Path

sys.path.append("/home/nraatika/projects/florent/teaPot/scripts")
import florent_replay_pb2

def main():
    maps_dir = Path("/home/nraatika/projects/florent/teaPot/maps")
    features_out = Path("/home/nraatika/projects/florent/teaPot/components/features.py")
    
    # The user asked for "active maps". Should I parse all maps or only active maps?
    # I'll just parse all maps to be safe, because if the rotation changes, the bot still works.
    
    map_id_counter = 1
    map_id_to_name = {}
    known_maps = {}
    wall_locations = {}
    ore_locations = {}
    
    for map_file in sorted(maps_dir.glob("*.map26")):
        name = map_file.stem
        
        # Skip unknown big map or anything weird
        if "unknown" in name:
            continue
            
        map_id = map_id_counter
        map_id_counter += 1
        map_id_to_name[map_id] = name
        
        with open(map_file, "rb") as f:
            data = f.read()
            m = florent_replay_pb2.Map()
            m.ParseFromString(data)
            
            w, h = m.width, m.height
            grid = [[florent_replay_pb2.ENV_EMPTY for _ in range(m.width)] for _ in range(m.height)]
            
            walls = []
            ores = []
            
            for y, row in enumerate(m.rows):
                for x, tile in enumerate(row.tiles):
                    grid[y][x] = tile
                    if tile == florent_replay_pb2.ENV_WALL:
                        walls.append((x, y))
                    elif tile in (florent_replay_pb2.ENV_ORE_TITANIUM, florent_replay_pb2.ENV_ORE_AXIONITE):
                        ores.append((x, y))
                        
            wall_locations[map_id] = walls
            ore_locations[map_id] = ores
            
            team_a_start = None
            team_b_start = None
            for c in m.cores:
                if c.team == florent_replay_pb2.TEAM_A:
                    team_a_start = (c.position.x, c.position.y)
                elif c.team == florent_replay_pb2.TEAM_B:
                    team_b_start = (c.position.x, c.position.y)
                    
            def get_wall_count(cx, cy):
                count = 0
                for dy in range(-6, 7):
                    for dx in range(-6, 7):
                        if dx*dx + dy*dy <= 36:
                            px = cx + dx
                            py = cy + dy
                            if 0 <= px < w and 0 <= py < h:
                                if grid[py][px] == florent_replay_pb2.ENV_WALL:
                                    count += 1
                return count

            if team_a_start and team_b_start:
                wa = get_wall_count(team_a_start[0], team_a_start[1])
                wb = get_wall_count(team_b_start[0], team_b_start[1])
                
                # Team A
                key_a = f"({w}, {h}, Team.A, {team_a_start[0]}, {team_a_start[1]}, {wa})"
                val_a = f"({map_id}, '{name}', {team_b_start[0]}, {team_b_start[1]})"
                known_maps[key_a] = val_a
                
                # Team B
                key_b = f"({w}, {h}, Team.B, {team_b_start[0]}, {team_b_start[1]}, {wb})"
                val_b = f"({map_id}, '{name}', {team_a_start[0]}, {team_a_start[1]})"
                known_maps[key_b] = val_b

    with open(features_out, "w") as f:
        f.write("from fcode import Team\n\n")
        
        f.write("KNOWN_MAPS = {\n")
        for k, v in known_maps.items():
            f.write(f"    {k}: {v},\n")
        f.write("}\n\n")
        
        f.write("MAP_ID_TO_NAME = {\n")
        for mid, mname in map_id_to_name.items():
            f.write(f"    {mid}: '{mname}',\n")
        f.write("}\n\n")
        
        f.write("WALL_LOCATIONS = {\n")
        for mid, w_locs in wall_locations.items():
            f.write(f"    {mid}: {w_locs},\n")
        f.write("}\n\n")
        
        f.write("ORE_LOCATIONS = {\n")
        for mid, o_locs in ore_locations.items():
            f.write(f"    {mid}: {o_locs},\n")
        f.write("}\n")
        
    print(f"Successfully generated features.py at {features_out} with {len(map_id_to_name)} maps.")

if __name__ == "__main__":
    main()
