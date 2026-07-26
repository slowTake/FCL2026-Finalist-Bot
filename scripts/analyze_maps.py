#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.resolve()))
import florent_replay_pb2

def analyze_maps():
    maps_dir = Path(__file__).parent.parent / "maps"
    maps_stats = []
    
    for map_file in sorted(maps_dir.glob("*.map26")):
        with open(map_file, "rb") as f:
            data = f.read()
            
        try:
            m = florent_replay_pb2.Map()
            m.ParseFromString(data)
            
            wall_tiles = 0
            titanium_tiles = 0
            axionite_tiles = 0
            empty_tiles = 0
            
            for row in m.rows:
                for tile in row.tiles:
                    if tile == florent_replay_pb2.ENV_WALL:
                        wall_tiles += 1
                    elif tile == florent_replay_pb2.ENV_ORE_TITANIUM:
                        titanium_tiles += 1
                    elif tile == florent_replay_pb2.ENV_ORE_AXIONITE:
                        axionite_tiles += 1
                    else:
                        empty_tiles += 1
            
            maps_stats.append({
                "name": map_file.name,
                "width": m.width,
                "height": m.height,
                "walls": wall_tiles,
                "titanium": titanium_tiles,
                "axionite": axionite_tiles,
                "cores": len(m.cores)
            })
                        
        except Exception as e:
            print(f"Failed to parse {map_file.name}: {e}")

    # Print a nice table
    print(f"{'Map Name':<15} | {'Size':<7} | {'Area':<5} | {'Walls':<5} | {'Titanium':<8} | {'Axionite':<8} | {'Cores':<5}")
    print("-" * 68)
    for stat in maps_stats:
        size_str = f"{stat['width']}x{stat['height']}"
        area = stat['width'] * stat['height']
        print(f"{stat['name']:<15} | {size_str:<7} | {area:<5} | {stat['walls']:<5} | {stat['titanium']:<8} | {stat['axionite']:<8} | {stat['cores']:<5}")

if __name__ == "__main__":
    analyze_maps()
