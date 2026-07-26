#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.resolve()))
import florent_replay_pb2

def generate_map_details():
    maps_dir = Path(__file__).parent.parent / "maps"
    output_path = Path(__file__).parent.parent / "maps_details.md"
    
    with open(output_path, "w") as out:
        out.write("# Map Details\n\n")
        out.write("This file contains parseable metadata and ASCII representations for every map.\n\n")
        
        for map_file in sorted(maps_dir.glob("*.map26")):
            with open(map_file, "rb") as f:
                data = f.read()
                
            try:
                m = florent_replay_pb2.Map()
                m.ParseFromString(data)
                
                team_a_start = None
                team_b_start = None
                
                for c in m.cores:
                    if c.team == florent_replay_pb2.TEAM_A:
                        team_a_start = (c.position.x, c.position.y)
                    elif c.team == florent_replay_pb2.TEAM_B:
                        team_b_start = (c.position.x, c.position.y)
                        
                ore_locations = []
                wall_locations = []
                
                grid = []
                for y, row in enumerate(m.rows):
                    row_chars = []
                    for x, tile in enumerate(row.tiles):
                        if tile == florent_replay_pb2.ENV_WALL:
                            wall_locations.append((x, y))
                            row_chars.append("x")
                        elif tile == florent_replay_pb2.ENV_ORE_TITANIUM or tile == florent_replay_pb2.ENV_ORE_AXIONITE:
                            ore_locations.append((x, y))
                            row_chars.append("o")
                        else:
                            row_chars.append(".")
                    grid.append(row_chars)
                    
                # Overwrite core locations on the grid (Cores are 2x2 footprint)
                if team_a_start:
                    x, y = team_a_start
                    grid[y][x] = "1"
                    grid[y][x+1] = "1"
                    grid[y+1][x] = "1"
                    grid[y+1][x+1] = "1"
                if team_b_start:
                    x, y = team_b_start
                    grid[y][x] = "2"
                    grid[y][x+1] = "2"
                    grid[y+1][x] = "2"
                    grid[y+1][x+1] = "2"
                    
                out.write(f"## {map_file.name}\n")
                out.write(f"- **Width**: {m.width}\n")
                out.write(f"- **Height**: {m.height}\n")
                out.write(f"- **Team A Start**: {team_a_start}\n")
                out.write(f"- **Team B Start**: {team_b_start}\n")
                
                out.write(f"- **Ore Locations**: {ore_locations}\n")
                out.write(f"- **Wall Locations**: {wall_locations}\n")
                
                out.write("### ASCII Map\n")
                out.write("```text\n")
                for row_chars in grid:
                    out.write("".join(row_chars) + "\n")
                out.write("```\n\n")
                
            except Exception as e:
                print(f"Failed to parse {map_file.name}: {e}")

if __name__ == "__main__":
    generate_map_details()
