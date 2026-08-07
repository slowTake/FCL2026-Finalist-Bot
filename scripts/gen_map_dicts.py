import os
import sys
import subprocess
from pathlib import Path
import re

sys.path.append("/home/nraatika/projects/florent/teaPot/scripts")
import florent_replay_pb2

def main():
    maps_dir = Path("/home/nraatika/projects/florent/teaPot/maps")
    features_out = Path("/home/nraatika/projects/florent/teaPot/components/features.py")
    
    map_id_counter = 1
    map_id_to_name = {}
    known_maps = {}
    wall_locations = {}
    ore_locations = {}
    
    for map_file in sorted(maps_dir.glob("*.map26")):
        name = map_file.stem
        if "unknown" in name:
            continue
            
        map_id = map_id_counter
        map_id_counter += 1
        map_id_to_name[map_id] = name
        
        # Clear temp file
        if os.path.exists("/tmp/fp.txt"):
            os.remove("/tmp/fp.txt")
            
        res = subprocess.run(["fcode", "run", "/tmp/fp_bot", "/tmp/fp_bot", str(map_file), "--tle", "0"], 
                             capture_output=True, text=True, cwd="/home/nraatika/projects/florent/teaPot")
        
        if not os.path.exists("/tmp/fp.txt"):
            print(f"Warning: could not extract fingerprints for {name} from engine output")
            continue
            
        with open("/tmp/fp.txt", "r") as f:
            content = f.read()
            
        fps = re.findall(r"FP_OUT:(\d+),(\d+),(\w+),(\d+),(\d+),(\d+)", content)
        
        if len(fps) < 2:
            print(f"Warning: could not extract fingerprints for {name} from engine output")
            continue
            
        with open(map_file, "rb") as f:
            data = f.read()
            m = florent_replay_pb2.Map()
            m.ParseFromString(data)
            walls = []
            ores = []
            for y, row in enumerate(m.rows):
                for x, tile in enumerate(row.tiles):
                    if tile == florent_replay_pb2.ENV_WALL:
                        walls.append((x, y))
                    elif tile in (florent_replay_pb2.ENV_ORE_TITANIUM, florent_replay_pb2.ENV_ORE_AXIONITE):
                        ores.append((x, y))
            wall_locations[map_id] = walls
            ore_locations[map_id] = ores
            
        fp_a = None
        fp_b = None
        for fp in fps:
            w, h, team_name, x, y, wall_count = fp
            key = f"({w}, {h}, Team.{team_name}, {x}, {y}, {wall_count})"
            if team_name == "A":
                fp_a = (key, int(x), int(y))
            elif team_name == "B":
                fp_b = (key, int(x), int(y))
                
        if fp_a and fp_b:
            val_a = f"({map_id}, '{name}', {fp_b[1]}, {fp_b[2]})"
            known_maps[fp_a[0]] = val_a
            
            val_b = f"({map_id}, '{name}', {fp_a[1]}, {fp_a[2]})"
            known_maps[fp_b[0]] = val_b

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
        
    print(f"Successfully regenerated features.py at {features_out} using engine fingerprints!")

if __name__ == "__main__":
    main()
