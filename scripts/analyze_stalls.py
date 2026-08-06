import csv
from collections import defaultdict
from pathlib import Path

# --- TUNABLE PARAMETERS ---
# How many turns the builder count must remain completely flat to be considered a "stall"
STALL_TURN_THRESHOLD = 200  
# Max number of builders below which a flatline is considered a stall
# (If we are at the 15+ cap, it's not a stall, it's just being maxed out)
MAX_STALL_BUILDER_COUNT = 14  
# --------------------------

csv_path = str(Path(__file__).parent / "match_observations.csv")

# Since snapshots are every 50 turns, we calculate how many consecutive snapshots are needed
CONSECUTIVE_SNAPSHOTS_REQUIRED = max(1, STALL_TURN_THRESHOLD // 50)

# Group rows into distinct games.
# A new game starts whenever turn == 0
games = []
current_game = []

with open(csv_path, "r") as f:
    reader = csv.DictReader(f)
    for row in reader:
        turn = int(row["turn"])
        if turn == 0 and current_game:
            games.append(current_game)
            current_game = []
        current_game.append(row)
    if current_game:
        games.append(current_game)

# Analysis dictionaries
map_stats = defaultdict(lambda: {
    "total_games": 0,
    "builder_stalls": 0
})

for game in games:
    if not game: continue
    
    map_name = game[0]["map"]
    map_stats[map_name]["total_games"] += 1
            
    # Builder Stalls Analysis
    stall_found = False
    consecutive = 1
    last_count = -1
    
    for r in game:
        b = int(r["builders"])
        if 0 < b <= MAX_STALL_BUILDER_COUNT:
            if b == last_count:
                consecutive += 1
                if consecutive >= CONSECUTIVE_SNAPSHOTS_REQUIRED:
                    stall_found = True
                    break
            else:
                last_count = b
                consecutive = 1
        else:
            last_count = -1
            consecutive = 1
            
    if stall_found:
        map_stats[map_name]["builder_stalls"] += 1

print(f"--- Builder Stall Analysis (Threshold: {STALL_TURN_THRESHOLD} turns) ---")
print(f"{'Map':<15} | {'Games':<6} | {'Builder Stalls':<15} | {'Stall Rate':<10}")
print("-" * 55)
for m, stats in sorted(map_stats.items(), key=lambda x: x[1]["total_games"], reverse=True):
    total = stats['total_games']
    stalls = stats['builder_stalls']
    rate = f"{(stalls/total)*100:.1f}%" if total > 0 else "0.0%"
    print(f"{m:<15} | {total:<6} | {stalls:<15} | {rate:<10}")
