import sqlite3
import sys
import json
import urllib.request
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Add project modules to path
project_root = Path('/home/nraatika/projects/florent').resolve()
teaPot_root = project_root / 'teaPot'
sys.path.insert(0, str(project_root / ".venv" / "lib" / "python3.12" / "site-packages"))
sys.path.insert(0, str(teaPot_root))
sys.path.insert(0, str(teaPot_root / "fcv_gauntlet"))
sys.path.insert(0, str(teaPot_root / "scripts"))

from scripts.build_econ_db import (
    load_token, fetch_json, init_db, process_replay, download_match_replays,
    LADDER_URL, MATCHES_URL
)

from fcv.replay import Replay
from fcv_gauntlet.detectors.improved import (
    ImprovedCrashDetector, ImprovedOrphanHarvesterDetector, ImprovedShotDetector,
    ImprovedRebuildChurnDetector, ImprovedOscillationDetector, ImprovedIdleDetector, ImprovedHoardingDetector
)

class DummyConfig:
    cpu_tle_threshold_us = 9000
    orphan_grace_turns = 10
    churn_min_rebuilds = 3
    oscillation_window = 6
    oscillation_min_hits = 3
    idle_min_turns = 8
    hoarding_min_ti = 250
    hoarding_min_turns = 25

OUR_TEAM_ID = "12fdece5-48c9-4913-9533-6d95b73e22ab"
OUR_TEAM_NAME = "HTTP 418"

def main():
    import argparse
    from dateutil import parser as dt_parser
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-enrichment", action="store_true", help="Skip the diagnostic enrichment phase")
    parser.add_argument("--since", type=str, help="Start parsing from a specific timestamp (e.g. 11:51)")
    args = parser.parse_args()

    token = load_token()
    db_path = teaPot_root / "data" / "economic_analysis.db"
    replays_dir = teaPot_root / "data" / "replays"

    if not db_path.exists():
        print("Database not found, creating a new one.")
    else:
        print("Existing database found, resuming from where we left off.")

    conn = init_db(db_path)

    # Add new columns needed for enrichment (ignore if they already exist)
    try:
        conn.execute("ALTER TABLE game_economic_summaries ADD COLUMN total_wasted_ti REAL DEFAULT 0.0;")
        conn.execute("ALTER TABLE game_economic_summaries ADD COLUMN pct_wasted REAL DEFAULT 0.0;")
    except sqlite3.OperationalError:
        pass # Columns probably already exist
    conn.execute("""
    CREATE TABLE IF NOT EXISTS diagnostic_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id TEXT REFERENCES games(game_id),
        turn_start INTEGER,
        team_id TEXT REFERENCES teams(team_id),
        detector TEXT,
        likely_cause TEXT,
        estimated_ti_loss REAL
    );
    """)
    try:
        conn.execute("ALTER TABLE games ADD COLUMN is_enriched BOOLEAN DEFAULT 0;")
    except sqlite3.OperationalError:
        pass # Column already exists

    conn.commit()

    if args.since:
        parsed_dt = dt_parser.parse(args.since)
        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.astimezone() # Local tz
        cutoff_dt = parsed_dt.astimezone(timezone.utc)
        print(f"Using manual cutoff time: {cutoff_dt.isoformat()}")
    else:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=8)
    
    print("Fetching Top 32 teams from ladder...")
    ladder_data = fetch_json(LADDER_URL, token)
    if not ladder_data:
        print("Failed to fetch ladder.")
        sys.exit(1)
        
    top_teams = []
    for t in ladder_data:
        if not t.get("ladderBanned"):
            top_teams.append(t)
        if len(top_teams) >= 32:
            break
            
    target_team_ids = [t["teamId"] for t in top_teams]
    print(f"Targeting matches for {len(target_team_ids)} teams since {cutoff_dt.isoformat()}.")
    
    all_matches_to_process = []
    processed_match_ids = set()
    
    for team_id in target_team_ids:
        team_name = next(t["teamName"] for t in top_teams if t["teamId"] == team_id)
        print(f"Fetching matches for {team_name}...")
        opp_matches_data = fetch_json(f"{MATCHES_URL}?teamIds={team_id}&limit=100", token)
        if not opp_matches_data or "matches" not in opp_matches_data:
            continue
            
        for m in opp_matches_data["matches"]:
            if m.get("status") != "complete": continue
            if m["id"] in processed_match_ids: continue

            created_str = m.get("createdAt", "")
            if created_str:
                dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                if dt < cutoff_dt: continue

            all_matches_to_process.append(m)
            processed_match_ids.add(m["id"])
            
    print(f"Total matches to process (Top 32 teams): {len(all_matches_to_process)}")

    already_completed_matches = set(
        row[0] for row in conn.execute("SELECT DISTINCT match_id FROM matches").fetchall()
    )
    import concurrent.futures
    import threading

    print(f"Found {len(already_completed_matches)} matches already in DB.")

    def process_single_match(idx, total, m, db_path, replays_dir, token):
        from datetime import datetime
        now_str = datetime.now().strftime("%H:%M:%S")
        match_id = m["id"]
        if match_id in already_completed_matches:
            print(f"[{now_str}] [{idx}/{total}] Skipping Match {match_id} (already in DB)")
            return

        print(f"[{now_str}] [{idx}/{total}] Downloading replays for Match {match_id}")

        # Connect to DB locally for thread safety
        conn = sqlite3.connect(db_path, timeout=60.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")

        team_a_id = m["teamAId"]
        team_b_id = m["teamBId"]

        # Insert teams
        conn.execute("INSERT OR REPLACE INTO teams (team_id, name, rank, rating) VALUES (?, ?, 0, 0.0);", (team_a_id, m["teamAName"]))
        conn.execute("INSERT OR REPLACE INTO teams (team_id, name, rank, rating) VALUES (?, ?, 0, 0.0);", (team_b_id, m["teamBName"]))

        # Insert Match
        conn.execute("INSERT OR REPLACE INTO matches (match_id, team_a_id, team_b_id, score_a, score_b, winner_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?);",
            (match_id, team_a_id, team_b_id, m["scoreA"], m["scoreB"], m.get("winnerId"), m.get("createdAt", "")))
        conn.commit()

        detail = fetch_json(f"{MATCHES_URL}/{match_id}", token)
        games = detail.get("games", []) if detail else []

        download_match_replays(match_id, games, replays_dir, token)

        for g in games:
            game_id = g["id"]
            replay_key = g["replayS3Key"]

            conn.execute("INSERT OR REPLACE INTO games (game_id, match_id, game_number, map_name, map_seed, winner_team_id, win_condition, turns_played, replay_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
                (game_id, match_id, g["gameNumber"], g["mapName"], g.get("mapSeed", 0), g.get("winnerId"), g.get("winCondition", "unknown"), g.get("turnsPlayed", 0), replay_key))
            conn.commit()

            replay_path = replays_dir / replay_key
            if not replay_path.exists(): continue

            try:
                # process_replay needs the sqlite connection, which is safe per-thread since we instantiated it here
                process_replay(replay_path, game_id, g["mapName"], team_a_id, team_b_id, conn)
            except Exception as e:
                print(f"Error processing {replay_path}: {e}", file=sys.stderr)

        conn.close()

    # 3. Process Replays and Insert into DB in parallel
    total = len(all_matches_to_process)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = []
        for idx, m in enumerate(all_matches_to_process, 1):
            futures.append(executor.submit(process_single_match, idx, total, m, db_path, replays_dir, token))

        for future in concurrent.futures.as_completed(futures):
            future.result() # Will raise any unhandled exceptions

    if args.skip_enrichment:
        print("Skipping Waste Enrichment phase as requested.")
    else:
        # 4. Enrich Data
        enrich_replays(conn, replays_dir, db_path)
        
    print("Database rebuilding complete.")

def enrich_replays(conn, replays_dir, db_path):
    import concurrent.futures
    print("Starting enrichment...")
    games = conn.execute("SELECT g.game_id, g.replay_key, m.team_a_id, m.team_b_id FROM games g JOIN matches m ON g.match_id = m.match_id").fetchall()
    cfg = DummyConfig()

    def enrich_single_game(idx, total, game_id, replay_key, team_a_id, team_b_id):
        # isolation_level=None enables autocommit mode, preventing implicit transactions that cause deadlocks
        conn_local = sqlite3.connect(db_path, timeout=60.0, isolation_level=None)

        # Manually start an immediate transaction to acquire the write lock safely without deadlocking
        conn_local.execute("BEGIN IMMEDIATE;")

        try:
            # Check if already enriched
            is_enriched = conn_local.execute("SELECT is_enriched FROM games WHERE game_id = ?", (game_id,)).fetchone()
            if is_enriched and is_enriched[0]:
                if idx % 50 == 0:
                    from datetime import datetime
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Skipped {idx}/{total} (already enriched)")
                conn_local.execute("ROLLBACK;")
                conn_local.close()
                return

            # Delete just in case it was partially enriched
            conn_local.execute("DELETE FROM diagnostic_events WHERE game_id = ?", (game_id,))

            replay_path = replays_dir / replay_key
            if not replay_path.exists():
                conn_local.execute("ROLLBACK;")
                conn_local.close()
                return

            try:
                r = Replay.load(str(replay_path))
            except Exception:
                conn_local.execute("ROLLBACK;")
                conn_local.close()
                return

            detectors = [
                ImprovedCrashDetector(cfg), ImprovedOrphanHarvesterDetector(cfg),
                ImprovedShotDetector(cfg), ImprovedRebuildChurnDetector(cfg),
                ImprovedOscillationDetector(cfg), ImprovedIdleDetector(cfg), ImprovedHoardingDetector(cfg)
            ]

            diag_events = []
            wasted_ti = {team_a_id: 0.0, team_b_id: 0.0}

            for det in detectors:
                for ev in det.analyze_replay(r):
                    tid = team_a_id if ev.team == "TEAM_A" else (team_b_id if ev.team == "TEAM_B" else None)
                    if not tid: continue
                    loss = ev.estimated_ti_loss or 0.0
                    wasted_ti[tid] += loss
                    diag_events.append((game_id, ev.turn_start, tid, ev.detector, ev.likely_cause, loss))

            if diag_events:
                conn_local.executemany("INSERT INTO diagnostic_events (game_id, turn_start, team_id, detector, likely_cause, estimated_ti_loss) VALUES (?, ?, ?, ?, ?, ?)", diag_events)

            for tid in (team_a_id, team_b_id):
                waste = wasted_ti[tid]
                spent_row = conn_local.execute("SELECT total_spent FROM game_economic_summaries WHERE game_id=? AND team_id=?", (game_id, tid)).fetchone()
                total_spent = spent_row[0] if spent_row and spent_row[0] > 0 else 1.0
                pct_wasted = round((waste / total_spent) * 100, 1)
                conn_local.execute("UPDATE game_economic_summaries SET total_wasted_ti=?, pct_wasted=? WHERE game_id=? AND team_id=?", (waste, pct_wasted, game_id, tid))

            conn_local.execute("UPDATE games SET is_enriched = 1 WHERE game_id = ?", (game_id,))

            conn_local.execute("COMMIT;")

            if idx % 10 == 0:
                from datetime import datetime
                now_str = datetime.now().strftime("%H:%M:%S")
                print(f"[{now_str}] Enriched {idx}/{total} games...")

        except Exception as e:
            conn_local.execute("ROLLBACK;")
            print(f"Error enriching {game_id}: {e}")
        finally:
            conn_local.close()

    total_games = len(games)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = []
        for idx, (game_id, replay_key, team_a_id, team_b_id) in enumerate(games, 1):
            futures.append(executor.submit(enrich_single_game, idx, total_games, game_id, replay_key, team_a_id, team_b_id))

        for future in concurrent.futures.as_completed(futures):
            future.result()

if __name__ == "__main__":
    main()

    # Finally, update team ratings
    print("Updating team ratings from the ladder...")
    import subprocess, json
    res = subprocess.run(["/home/nraatika/projects/florent/.venv/bin/fcode", "ladder", "--limit", "200", "--json"], capture_output=True, text=True)
    if res.returncode == 0:
        try:
            ladder = json.loads(res.stdout)
            conn_local = sqlite3.connect(Path(__file__).parent.parent / "data" / "economic_analysis.db")
            for team in ladder:
                conn_local.execute("UPDATE teams SET rank = ?, rating = ? WHERE name = ?",
                             (team.get("_rank"), team.get("rating"), team.get("teamName")))
            conn_local.commit()
            conn_local.close()
            print("Successfully updated team ratings.")
        except Exception as e:
            print("Failed to update ratings:", e)
