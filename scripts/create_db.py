import urllib.request
import json
import sys
import subprocess
import glob
from pathlib import Path
from datetime import datetime, timezone, timedelta
import csv

sys.path.append("/home/nraatika/projects/florent/teaPot/scripts")
import florent_replay_pb2

TEAM_ID = "12fdece5-48c9-4913-9533-6d95b73e22ab"

def load_token():
    creds_file = Path.home() / ".fcode" / "credentials.json"
    if creds_file.exists():
        return json.loads(creds_file.read_text()).get("token")
    return None

def fetch_json(url, token):
    headers = {"User-Agent": "Mozilla/5.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def parse_replay(replay_path, match_id, map_name, opponent, our_team_enum):
    with open(replay_path, "rb") as f:
        data = f.read()
    replay = florent_replay_pb2.Replay()
    replay.ParseFromString(data)
    
    entities = {} # id -> kind
    
    titanium = 0
    titanium_collected = 0
    
    rows = []
    
    for i, turn in enumerate(replay.turns):
        for update in turn.updates:
            if update.HasField('placeEntity'):
                ent = update.placeEntity.entity
                if ent.team == our_team_enum:
                    entities[ent.id] = ent.WhichOneof("kind")
            elif update.HasField('removeEntity'):
                ent_id = update.removeEntity.id
                if ent_id in entities:
                    del entities[ent_id]
            elif update.HasField('updatePlayers'):
                players = update.updatePlayers.players
                our_player = players.a if our_team_enum == florent_replay_pb2.TEAM_A else players.b
                titanium = our_player.titanium
                titanium_collected = our_player.titaniumCollected

        if i % 50 == 0:
            counts = {
                "builderBot": 0,
                "harvester": 0,
                "conveyor": 0,
                "splitter": 0,
                "gunner": 0,
                "sentinel": 0,
                "barrier": 0,
                "launcher": 0
            }
            for kind in entities.values():
                if kind in counts:
                    counts[kind] += 1
                    
            rows.append({
                "matchID": match_id,
                "map": map_name,
                "enemy": opponent,
                "turn": i,
                "builders": counts["builderBot"],
                "harvesters": counts["harvester"],
                "conveyors": counts["conveyor"],
                "splitters": counts["splitter"],
                "gunners": counts["gunner"],
                "sentinels": counts["sentinel"],
                "barriers": counts["barrier"],
                "launchers": counts["launcher"],
                "titanium_harvested": titanium_collected,
                "titanium_store": titanium,
                "ammo_store": "N/A" # Engine tracks this internally
            })
            
    return rows

token = load_token()
print("Fetching our recent matches...")
matches_data = fetch_json(f"https://game.code.florent.vc/api/matches?limit=100&teamIds={TEAM_ID}", token)

if not matches_data or 'matches' not in matches_data:
    print("Failed to fetch matches")
    sys.exit(1)

now = datetime.now(timezone.utc)
six_hours_ago = now - timedelta(hours=6)

target_matches = []
for m in matches_data['matches']:
    created_at = m.get('createdAt')
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            if dt > six_hours_ago:
                target_matches.append(m)
        except ValueError:
            pass

print(f"Found {len(target_matches)} matches in the last 6 hours.")

all_rows = []

for m in target_matches:
    match_id = m['id']
    detail = fetch_json(f"https://game.code.florent.vc/api/matches/{match_id}", token)
    if not detail: continue
    
    team_a = detail.get('teamA', {})
    team_b = detail.get('teamB', {})
    
    if team_a.get('id') == TEAM_ID:
        our_team_enum = florent_replay_pb2.TEAM_A
        opponent = team_b.get('name', 'Unknown')
    else:
        our_team_enum = florent_replay_pb2.TEAM_B
        opponent = team_a.get('name', 'Unknown')
        
    print(f"Downloading match {match_id} (vs {opponent})...")
    subprocess.run(["fcode", "match", "replay", match_id], cwd="/tmp", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    for game in detail.get('games', []):
        map_name = game.get('mapName', 'unknown')
        game_num = game.get('gameNumber')
        replay_path = f"/tmp/{match_id}_game_{game_num}.replay26"
        if Path(replay_path).exists():
            rows = parse_replay(replay_path, match_id, map_name, opponent, our_team_enum)
            all_rows.extend(rows)

csv_path = str(Path(__file__).parent / "match_observations.csv")
with open(csv_path, "w", newline="") as f:
    fieldnames = ["matchID", "map", "enemy", "turn", "builders", "harvesters", "conveyors", 
                  "splitters", "gunners", "sentinels", "barriers", "launchers", 
                  "titanium_harvested", "titanium_store", "ammo_store"]
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(all_rows)

print(f"Wrote {len(all_rows)} observations to {csv_path}")
