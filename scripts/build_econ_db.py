#!/usr/bin/env python3
"""Build and populate a SQLite database with Titanium economic choices from match replays."""

import sys
import os
import json
import sqlite3
import math
import urllib.request
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

# Ensure sys.path includes root .venv site-packages for fcode imports
sys.path.insert(0, "/home/nraatika/projects/florent/.venv/lib/python3.12/site-packages")

# Local imports
teaPot_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(teaPot_dir / "scripts"))
import florent_replay_pb2 as pb
from fcode import Team, EntityType

# API Base URLs
LADDER_URL = "https://game.code.florent.vc/api/ladder"
MATCHES_URL = "https://game.code.florent.vc/api/matches"
REPLAY_BASE_URL = "https://game.code.florent.vc/api/replays"

# Engine Base Costs and Scaling Rates
BASE_COSTS = {
    "CONVEYOR": 3,
    "BARRIER": 3,
    "SPLITTER": 6,
    "HARVESTER": 20,
    "GUNNER": 10,
    "SENTINEL": 30,
    "LAUNCHER": 20,
    "BUILDER_BOT": 30,
}

SCALE_PER_BUILD = {
    "CONVEYOR": 1,
    "BARRIER": 1,
    "SPLITTER": 1,
    "HARVESTER": 5,
    "GUNNER": 10,
    "LAUNCHER": 10,
    "SENTINEL": 20,
    "BUILDER_BOT": 20,
}

FIXED_COSTS = {
    "BUILDER_HEAL": 1,
    "BUILDER_ATTACK": 2,
    "GUNNER_ROTATE": 10,
    "AMMO_STACK": 10,
}


def load_token() -> Optional[str]:
    """Load auth token from environment or fcode credentials file."""
    import os
    env_token = os.environ.get("FLORENT_TOKEN")
    if env_token:
        return env_token
        
    creds_file = Path.home() / ".fcode" / "credentials.json"
    if creds_file.exists():
        try:
            return json.loads(creds_file.read_text()).get("token")
        except:
            pass
    return None


def fetch_json(url: str, token: Optional[str] = None) -> Optional[dict]:
    """HTTP GET helper."""
    headers = {"User-Agent": "Mozilla/5.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"Error fetching {url}: {e}", file=sys.stderr)
        return None


def download_match_replays(match_id: str, games: list, replays_dir: Path, token: str) -> bool:
    """Download all .replay26 files for a match using direct urllib fetching (100x faster than shelling out)."""
    replays_dir.mkdir(parents=True, exist_ok=True)
    import urllib.parse
    
    success = True
    for g in games:
        gn = g.get("gameNumber", 0)
        out_path = replays_dir / f"{match_id}_game_{gn}.replay26"
        if out_path.exists():
            continue
            
        try:
            url_params = urllib.parse.urlencode({"matchId": match_id, "game": str(gn)})
            api_url = f"{MATCHES_URL}/replay?{url_params}"
            
            replay_data = fetch_json(api_url, token)
            if not replay_data or not replay_data.get("url"):
                continue
                
            urllib.request.urlretrieve(replay_data["url"], str(out_path))
        except Exception as e:
            print(f"Error downloading game {gn} for match {match_id}: {e}", file=sys.stderr)
            success = False
            
    return success


def get_scaled_cost(name: str, count: int) -> int:
    """Calculate scaled Titanium cost for an entity building/spawning."""
    base = BASE_COSTS.get(name, 10)
    scale = SCALE_PER_BUILD.get(name, 0)
    return math.floor((1.0 + count * scale / 100.0) * base)


def classify_spatial_region(x: int, y: int, team_enum: int, cores: Dict[int, Tuple[int, int]]) -> str:
    """Classify spatial location into own_core, opponent_core, own_contested, or opponent_contested."""
    own_c = cores.get(team_enum)
    opp_c = cores.get(1 - team_enum)
    if not own_c or not opp_c:
        return "own_contested"
    
    d_own = abs(x - own_c[0]) + abs(y - own_c[1])
    d_opp = abs(x - opp_c[0]) + abs(y - opp_c[1])

    if d_own <= 5 and d_own < d_opp:
        return "own_core"
    elif d_opp <= 5 and d_opp < d_own:
        return "opponent_core"
    elif d_own <= d_opp:
        return "own_contested"
    else:
        return "opponent_contested"


def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize SQLite database with schema tables."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS teams (
        team_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        rank INTEGER,
        rating REAL
    );

    CREATE TABLE IF NOT EXISTS matches (
        match_id TEXT PRIMARY KEY,
        team_a_id TEXT REFERENCES teams(team_id),
        team_b_id TEXT REFERENCES teams(team_id),
        score_a INTEGER,
        score_b INTEGER,
        winner_id TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS games (
        game_id TEXT PRIMARY KEY,
        match_id TEXT REFERENCES matches(match_id),
        game_number INTEGER,
        map_name TEXT NOT NULL,
        map_seed INTEGER,
        winner_team_id TEXT,
        win_condition TEXT,
        turns_played INTEGER,
        replay_key TEXT
    );

    CREATE TABLE IF NOT EXISTS titanium_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id TEXT REFERENCES games(game_id),
        turn INTEGER NOT NULL,
        team_id TEXT REFERENCES teams(team_id),
        opponent_team_id TEXT REFERENCES teams(team_id),
        map_name TEXT NOT NULL,
        spatial_region TEXT NOT NULL,
        category TEXT NOT NULL,
        action_type TEXT NOT NULL,
        unit_type TEXT,
        titanium_cost INTEGER NOT NULL,
        pos_x INTEGER,
        pos_y INTEGER,
        dist_to_own_core INTEGER,
        dist_to_opp_core INTEGER
    );

    CREATE TABLE IF NOT EXISTS turn_macro_snapshots (
        game_id TEXT REFERENCES games(game_id),
        turn INTEGER NOT NULL,
        team_id TEXT REFERENCES teams(team_id),
        titanium_stored INTEGER NOT NULL,
        living_builders INTEGER NOT NULL,
        living_harvesters INTEGER NOT NULL,
        living_turrets INTEGER NOT NULL,
        PRIMARY KEY (game_id, turn, team_id)
    );

    CREATE TABLE IF NOT EXISTS game_economic_summaries (
        game_id TEXT REFERENCES games(game_id),
        team_id TEXT REFERENCES teams(team_id),
        opponent_team_id TEXT REFERENCES teams(team_id),
        map_name TEXT NOT NULL,
        result TEXT NOT NULL,
        total_spent INTEGER NOT NULL,
        spent_builder INTEGER NOT NULL,
        spent_economy INTEGER NOT NULL,
        spent_defense INTEGER NOT NULL,
        spent_offense INTEGER NOT NULL,
        spent_infrastructure INTEGER NOT NULL,
        spent_ammo INTEGER NOT NULL,
        spent_own_core INTEGER NOT NULL,
        spent_own_contested INTEGER NOT NULL,
        spent_opp_contested INTEGER NOT NULL,
        spent_opp_core INTEGER NOT NULL,
        pct_builder REAL NOT NULL,
        pct_economy REAL NOT NULL,
        pct_defense REAL NOT NULL,
        pct_offense REAL NOT NULL,
        pct_infrastructure REAL NOT NULL,
        pct_ammo REAL NOT NULL,
        pct_own_core REAL NOT NULL,
        pct_own_contested REAL NOT NULL,
        pct_opp_contested REAL NOT NULL,
        pct_opp_core REAL NOT NULL,
        tles INTEGER DEFAULT 0,
        tle_turns TEXT,
        PRIMARY KEY (game_id, team_id)
    );
    """)
    try:
        conn.execute("ALTER TABLE game_economic_summaries ADD COLUMN tles INTEGER DEFAULT 0;")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE game_economic_summaries ADD COLUMN tle_turns TEXT;")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def process_replay(
    replay_path: Path,
    game_id: str,
    map_name: str,
    team_a_id: str,
    team_b_id: str,
    conn: sqlite3.Connection
):
    """Parse a .replay26 file and extract all Titanium expenditures and snapshots into SQLite."""
    with open(replay_path, "rb") as f:
        replay = pb.Replay()
        replay.ParseFromString(f.read())

    # Extract Core positions from map definition
    cores: Dict[int, Tuple[int, int]] = {}
    for c in replay.map.cores:
        t_enum = getattr(c, "team", 0)
        cores[t_enum] = (c.position.x, c.position.y)

    # Team ID mapping for Team A (enum 0) and Team B (enum 1)
    team_ids = {0: team_a_id, 1: team_b_id}
    opponents = {0: team_b_id, 1: team_a_id}

    # Tracking per-team build counts for scaling cost calculations
    build_counts = {0: {name: 0 for name in BASE_COSTS}, 1: {name: 0 for name in BASE_COSTS}}

    # Active entity tracking (id -> (team_enum, kind_name, pos_x, pos_y))
    entities: Dict[int, Tuple[int, str, int, int]] = {}
    for c in replay.map.cores:
        entities[c.id] = (getattr(c, "team", 0), "CORE", c.position.x, c.position.y)

    events_to_insert = []
    snapshots_to_insert = []

    # Category and Spatial Region counters per team per game
    category_spent = {
        0: {"BUILDER_UNIT": 0, "ECONOMY": 0, "DEFENSE": 0, "OFFENSE": 0, "INFRASTRUCTURE": 0, "AMMO": 0, "REPAIR": 0},
        1: {"BUILDER_UNIT": 0, "ECONOMY": 0, "DEFENSE": 0, "OFFENSE": 0, "INFRASTRUCTURE": 0, "AMMO": 0, "REPAIR": 0}
    }
    region_spent = {
        0: {"own_core": 0, "own_contested": 0, "opponent_contested": 0, "opponent_core": 0},
        1: {"own_core": 0, "own_contested": 0, "opponent_contested": 0, "opponent_core": 0}
    }
    tles = {0: 0, 1: 0}
    tle_turns_list = {0: [], 1: []}

    for turn_idx, turn in enumerate(replay.turns):
        current_titanium = {0: 0, 1: 0}

        for update in turn.updates:
            kind = update.WhichOneof("kind")
            
            if kind == "botOutput":
                if update.botOutput.tled:
                    ent_data = entities.get(update.botOutput.id)
                    if ent_data:
                        tles[ent_data[0]] += 1
                        tle_turns_list[ent_data[0]].append(str(turn_idx))

            if kind == "placeEntity":
                pe = update.placeEntity
                ent = pe.entity
                team_enum = ent.team
                team_id = team_ids.get(team_enum, "UNKNOWN")
                opp_id = opponents.get(team_enum, "UNKNOWN")
                px, py = ent.position.x, ent.position.y

                fields = [f[0].name for f in ent.ListFields()]
                unit_kind = "UNKNOWN"
                category = "INFRASTRUCTURE"
                action_type = "BUILD"
                cost_key = None

                if "builderBot" in fields:
                    unit_kind = "BUILDER_BOT"
                    category = "BUILDER_UNIT"
                    action_type = "SPAWN_BUILDER"
                    cost_key = "BUILDER_BOT"
                elif "harvester" in fields:
                    unit_kind = "HARVESTER"
                    category = "ECONOMY"
                    action_type = "BUILD_HARVESTER"
                    cost_key = "HARVESTER"
                elif "gunner" in fields:
                    unit_kind = "GUNNER"
                    category = "OFFENSE"
                    action_type = "BUILD_GUNNER"
                    cost_key = "GUNNER"
                elif "sentinel" in fields:
                    unit_kind = "SENTINEL"
                    category = "DEFENSE"
                    action_type = "BUILD_SENTINEL"
                    cost_key = "SENTINEL"
                elif "launcher" in fields:
                    unit_kind = "LAUNCHER"
                    category = "OFFENSE"
                    action_type = "BUILD_LAUNCHER"
                    cost_key = "LAUNCHER"
                elif "conveyor" in fields:
                    unit_kind = "CONVEYOR"
                    category = "INFRASTRUCTURE"
                    action_type = "BUILD_CONVEYOR"
                    cost_key = "CONVEYOR"
                elif "barrier" in fields or "wall" in fields:
                    unit_kind = "BARRIER"
                    category = "DEFENSE"
                    action_type = "BUILD_WALL"
                    cost_key = "BARRIER"
                elif "splitter" in fields:
                    unit_kind = "SPLITTER"
                    category = "INFRASTRUCTURE"
                    action_type = "BUILD_SPLITTER"
                    cost_key = "SPLITTER"

                # Calculate exact scaled cost for this placed entity
                count = build_counts[team_enum].get(cost_key, 0) if cost_key else 0
                cost = get_scaled_cost(cost_key, count) if cost_key else 0
                if cost_key:
                    build_counts[team_enum][cost_key] += 1

                # Classify 4-region spatial position
                spatial_region = classify_spatial_region(px, py, team_enum, cores)

                entities[ent.id] = (team_enum, unit_kind, px, py)
                category_spent[team_enum][category] += cost
                region_spent[team_enum][spatial_region] += cost

                own_c = cores.get(team_enum)
                opp_c = cores.get(1 - team_enum)
                d_own = abs(px - own_c[0]) + abs(py - own_c[1]) if own_c else -1
                d_opp = abs(px - opp_c[0]) + abs(py - opp_c[1]) if opp_c else -1

                events_to_insert.append((
                    game_id, turn_idx, team_id, opp_id, map_name,
                    spatial_region, category, action_type, unit_kind, cost, px, py, d_own, d_opp
                ))

            elif kind == "updatePlayers":
                upl = update.updatePlayers.players
                current_titanium[0] = upl.a.titanium
                current_titanium[1] = upl.b.titanium

            elif kind == "distributeResources":
                dr = update.distributeResources
                for rm in dr.moves:
                    from_pos = (getattr(rm, 'from').x, getattr(rm, 'from').y)
                    to_pos = (rm.to.x, rm.to.y)
                    # Detect Titanium ammo transfers to turrets
                    for eid, (t_enum, u_kind, ex, ey) in list(entities.items()):
                        if (ex, ey) == to_pos and u_kind in ("GUNNER", "SENTINEL", "LAUNCHER"):
                            cost = FIXED_COSTS["AMMO_STACK"]
                            team_id = team_ids[t_enum]
                            opp_id = opponents[t_enum]
                            spatial_region = classify_spatial_region(ex, ey, t_enum, cores)
                            category_spent[t_enum]["AMMO"] += cost
                            region_spent[t_enum][spatial_region] += cost
                            
                            own_c = cores.get(t_enum)
                            opp_c = cores.get(1 - t_enum)
                            d_own = abs(ex - own_c[0]) + abs(ey - own_c[1]) if own_c else -1
                            d_opp = abs(ex - opp_c[0]) + abs(ey - opp_c[1]) if opp_c else -1
                            
                            events_to_insert.append((
                                game_id, turn_idx, team_id, opp_id, map_name,
                                spatial_region, "AMMO", "AMMO_LOAD", u_kind, cost, ex, ey, d_own, d_opp
                            ))

            elif kind == "removeEntity":
                rm = update.removeEntity
                if rm.id in entities:
                    del entities[rm.id]

        # Record macro turn snapshot for living units and titanium reserves
        for t_enum, team_id in team_ids.items():
            living_b = sum(1 for e in entities.values() if e[0] == t_enum and e[1] == "BUILDER_BOT")
            living_h = sum(1 for e in entities.values() if e[0] == t_enum and e[1] == "HARVESTER")
            living_t = sum(1 for e in entities.values() if e[0] == t_enum and e[1] in ("GUNNER", "SENTINEL", "LAUNCHER"))
            snapshots_to_insert.append((
                game_id, turn_idx, team_id, current_titanium[t_enum],
                living_b, living_h, living_t
            ))

    # Insert events and snapshots into SQLite
    conn.executemany("""
        INSERT INTO titanium_events (
            game_id, turn, team_id, opponent_team_id, map_name,
            spatial_region, category, action_type, unit_type, titanium_cost, pos_x, pos_y,
            dist_to_own_core, dist_to_opp_core
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, events_to_insert)

    conn.executemany("""
        INSERT OR REPLACE INTO turn_macro_snapshots (
            game_id, turn, team_id, titanium_stored,
            living_builders, living_harvesters, living_turrets
        ) VALUES (?, ?, ?, ?, ?, ?, ?);
    """, snapshots_to_insert)

    # Insert Game Summaries per team
    game_summary_rows = []
    for t_enum, team_id in team_ids.items():
        opp_id = opponents[t_enum]
        sp = category_spent[t_enum]
        reg = region_spent[t_enum]
        total = sum(sp.values()) or 1  # prevent div by zero
        team_tles = tles.get(t_enum, 0)
        team_tle_turns = ",".join(tle_turns_list.get(t_enum, []))

        game_summary_rows.append((
            game_id, team_id, opp_id, map_name, "COMPLETED",
            total, sp["BUILDER_UNIT"], sp["ECONOMY"], sp["DEFENSE"], sp["OFFENSE"],
            sp["INFRASTRUCTURE"], sp["AMMO"],
            reg["own_core"], reg["own_contested"], reg["opponent_contested"], reg["opponent_core"],
            round((sp["BUILDER_UNIT"] / total) * 100, 1),
            round((sp["ECONOMY"] / total) * 100, 1),
            round((sp["DEFENSE"] / total) * 100, 1),
            round((sp["OFFENSE"] / total) * 100, 1),
            round((sp["INFRASTRUCTURE"] / total) * 100, 1),
            round((sp["AMMO"] / total) * 100, 1),
            round((reg["own_core"] / total) * 100, 1),
            round((reg["own_contested"] / total) * 100, 1),
            round((reg["opponent_contested"] / total) * 100, 1),
            round((reg["opponent_core"] / total) * 100, 1),
            team_tles, team_tle_turns
        ))

    conn.executemany("""
        INSERT OR REPLACE INTO game_economic_summaries (
            game_id, team_id, opponent_team_id, map_name, result,
            total_spent, spent_builder, spent_economy, spent_defense, spent_offense,
            spent_infrastructure, spent_ammo,
            spent_own_core, spent_own_contested, spent_opp_contested, spent_opp_core,
            pct_builder, pct_economy, pct_defense, pct_offense, pct_infrastructure, pct_ammo,
            pct_own_core, pct_own_contested, pct_opp_contested, pct_opp_core, tles, tle_turns
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, game_summary_rows)

    conn.commit()


from datetime import datetime, timezone, timedelta


def main():
    token = load_token()
    project_root = Path(__file__).resolve().parent.parent
    db_path = project_root / "data" / "economic_analysis.db"
    replays_dir = project_root / "data" / "replays"

    print("================================================================================")
    print("STEP 1: FETCHING TOP 10 TEAMS FROM LADDER")
    print("================================================================================")
    ladder = fetch_json(LADDER_URL, token)
    if not ladder:
        print("Error: Failed to fetch ladder.")
        sys.exit(1)

    top_10 = ladder[:10]
    conn = init_db(db_path)

    for rank_idx, team in enumerate(top_10, 1):
        tid = team["teamId"]
        tname = team["teamName"]
        trank = team.get("rank", rank_idx)
        trating = team.get("rating", 0.0)
        print(f"  #{trank:<2} Team: '{tname}' (ID: {tid}) | Rating: {trating:.2f}")

        conn.execute("""
            INSERT OR REPLACE INTO teams (team_id, name, rank, rating)
            VALUES (?, ?, ?, ?);
        """, (tid, tname, trank, trating))
    conn.commit()

    print("\n================================================================================")
    print("STEP 2: FETCHING LAST 12 HOURS MATCHES FOR TOP 10 TEAMS + HTTP 418")
    print("================================================================================")
    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=12)
    processed_matches = set()
    matches_to_process = []

    # Add HTTP 418 to the teams list for match processing
    teams_to_process = list(top_10)
    if not any(t["teamId"] == "12fdece5-48c9-4913-9533-6d95b73e22ab" for t in teams_to_process):
        teams_to_process.append({"teamId": "12fdece5-48c9-4913-9533-6d95b73e22ab", "teamName": "HTTP 418"})

    for team in teams_to_process:
        tid = team["teamId"]
        tname = team["teamName"]
        matches_data = fetch_json(f"{MATCHES_URL}?teamIds={tid}&limit=100", token)
        if not matches_data or "matches" not in matches_data:
            continue

        team_matches = 0
        for m in matches_data["matches"]:
            mid = m["id"]
            if mid in processed_matches or m.get("status") != "complete":
                continue

            created_str = m.get("createdAt", "")
            if created_str:
                try:
                    # ISO format parsing
                    dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    if dt < cutoff_dt:
                        continue
                except Exception:
                    pass

            processed_matches.add(mid)
            matches_to_process.append(m)
            team_matches += 1

        print(f"  Team '{tname}': {team_matches} matches found in last 24h")

    already_completed_matches = set(
        row[0] for row in conn.execute("SELECT DISTINCT match_id FROM matches").fetchall()
    )
    print(f"  Found {len(already_completed_matches)} matches already completed in SQLite database.")

    print("\n================================================================================")
    print("STEP 3: EXTRACTING REPLAYS & POPULATING SQLITE DATABASE")
    print("================================================================================")

    for idx, m in enumerate(matches_to_process, 1):
        match_id = m["id"]
        if match_id in already_completed_matches:
            continue

        team_a_id = m["teamAId"]
        team_b_id = m["teamBId"]
        team_a_name = m["teamAName"]
        team_b_name = m["teamBName"]
        score_a = m["scoreA"]
        score_b = m["scoreB"]
        winner_id = m.get("winnerId")
        created_at = m.get("createdAt", "")

        # Upsert teams
        conn.execute("INSERT OR REPLACE INTO teams (team_id, name, rank, rating) VALUES (?, ?, 0, 0.0);", (team_a_id, team_a_name))
        conn.execute("INSERT OR REPLACE INTO teams (team_id, name, rank, rating) VALUES (?, ?, 0, 0.0);", (team_b_id, team_b_name))

        # Insert Match
        conn.execute("""
            INSERT OR REPLACE INTO matches (match_id, team_a_id, team_b_id, score_a, score_b, winner_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?);
        """, (match_id, team_a_id, team_b_id, score_a, score_b, winner_id, created_at))
        conn.commit()

        print(f"\n[{idx}/{len(matches_to_process)}] Match {match_id}: {team_a_name} vs {team_b_name} ({score_a}-{score_b})")

        detail = fetch_json(f"{MATCHES_URL}/{match_id}", token)
        games = detail.get("games", []) if detail else []

        # Download match replays via fcode CLI
        download_match_replays(match_id, replays_dir)

        for g in games:
            game_id = g["id"]
            game_num = g["gameNumber"]
            map_name = g["mapName"]
            map_seed = g.get("mapSeed", 0)
            winner_team_id = g.get("winnerId")
            win_condition = g.get("winCondition", "unknown")
            turns_played = g.get("turnsPlayed", 0)
            replay_key = g["replayS3Key"]

            conn.execute("""
                INSERT OR REPLACE INTO games (
                    game_id, match_id, game_number, map_name, map_seed,
                    winner_team_id, win_condition, turns_played, replay_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (game_id, match_id, game_num, map_name, map_seed, winner_team_id, win_condition, turns_played, replay_key))
            conn.commit()

            replay_path = replays_dir / replay_key
            if not replay_path.exists():
                continue

            process_replay(replay_path, game_id, map_name, team_a_id, team_b_id, conn)

    print("\n================================================================================")
    print("STEP 4: DATABASE REVIEW & TOP 10 SUMMARY OUTPUT")
    print("================================================================================")

    cursor = conn.cursor()

    total_teams = cursor.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    total_matches = cursor.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    total_games = cursor.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    total_events = cursor.execute("SELECT COUNT(*) FROM titanium_events").fetchone()[0]
    total_snapshots = cursor.execute("SELECT COUNT(*) FROM turn_macro_snapshots").fetchone()[0]
    total_summaries = cursor.execute("SELECT COUNT(*) FROM game_economic_summaries").fetchone()[0]

    print(f"Database Path: {db_path.resolve()}")
    print(f"Extraction Totals:")
    print(f"  - Teams Stored:             {total_teams}")
    print(f"  - Matches Analyzed:         {total_matches}")
    print(f"  - Games Analyzed:           {total_games}")
    print(f"  - Titanium Spending Events: {total_events}")
    print(f"  - Turn Macro Snapshots:     {total_snapshots}")

    print("\n--- TOP 10 TEAMS SPENDING & LOCATION BASELINES (12H RANKED MATCHES) ---")
    hdr = f"{'Rank':<5} {'Team':<12} {'Games':<7} {'Tot Spent':<10} {'Builder%':<9} {'Econ%':<7} {'Def%':<7} {'Off%':<7} {'OwnCore%':<9} {'OppCore%':<9}"
    print(hdr)
    print("-" * len(hdr))

    iso_cutoff = cutoff_dt.isoformat().replace('+00:00', 'Z')
    rows = cursor.execute(f"""
        SELECT t.rank, t.name, COUNT(s.game_id) as games_cnt,
               ROUND(AVG(s.total_spent), 0) as avg_spent,
               ROUND(AVG(s.pct_builder), 1) as avg_builder,
               ROUND(AVG(s.pct_economy), 1) as avg_econ,
               ROUND(AVG(s.pct_defense), 1) as avg_def,
               ROUND(AVG(s.pct_offense), 1) as avg_off,
               ROUND(AVG(s.pct_own_core), 1) as avg_own_core,
               ROUND(AVG(s.pct_opp_core), 1) as avg_opp_core
        FROM teams t
        JOIN game_economic_summaries s ON t.team_id = s.team_id
        JOIN games g ON s.game_id = g.game_id
        JOIN matches m ON g.match_id = m.match_id
        WHERE (t.rank > 0 OR t.name = 'HTTP 418') AND m.created_at >= ?
        GROUP BY t.team_id
        ORDER BY t.rank ASC
    """, (iso_cutoff,)).fetchall()

    for r in rows:
        trank, tname, gcnt, avg_sp, avg_b, avg_ec, avg_d, avg_o, avg_oc, avg_opc = r
        print(f"#{trank:<4} {tname:<12} {gcnt:<7} {avg_sp:<10.0f} {avg_b:<9.1f} {avg_ec:<7.1f} {avg_d:<7.1f} {avg_o:<7.1f} {avg_oc:<9.1f} {avg_opc:<9.1f}")

    conn.close()


if __name__ == "__main__":
    main()
