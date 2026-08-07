#!/usr/bin/env python3
"""Match Triage Tool — Fetch recent matches, download replays for losses/errors,
scan for TLE'd units, and produce a debug manifest.

Run from the teaPot/ directory using the project venv:
    .venv/bin/python scripts/triage_matches.py [--hours 6] [--losses-only] [--output triage_report.md]

Workflow:
    1. Fetches recent matches from the Florent API (configurable time window)
    2. Identifies losses, errors, TLE-prone games, and close results
    3. Downloads replay files to ranked/triage/
    4. Scans each replay for units that TLE'd (botOutput.tled == True)
    5. Outputs a triage_report.md with actionable debug commands

Requires: fcode login (auth token in ~/.fcode/credentials.json)
"""

import sys
import json
import subprocess
import urllib.request
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# Protobuf import
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import florent_replay_pb2 as pb

TEAM_ID = "12fdece5-48c9-4913-9533-6d95b73e22ab"
API_BASE = "https://game.code.florent.vc/api"
FCODE_BIN = str(Path(__file__).resolve().parent.parent / ".venv" / "bin" / "fcode")

CONDITION_LABELS = {
    "core_destroyed": "Core Destroyed",
    "resigned": "Resigned",
    "resources": "Resource Victory",
    "titanium_collected": "Titanium Collected",
    "harvesters": "Harvesters",
    "titanium_stored": "Titanium Stored",
    "coinflip": "Coinflip",
    "timeout": "Timeout",
    "error": "Error",
}

# TLE threshold in microseconds (10ms = 10000µs game budget)
TLE_WARNING_US = 8000  # flag units using >80% of budget even if not TLE'd


# ─── API helpers (reused from create_db.py / get_stats.py) ──────────────────

def load_token() -> str | None:
    creds_file = Path.home() / ".fcode" / "credentials.json"
    if creds_file.exists():
        try:
            return json.loads(creds_file.read_text()).get("token")
        except Exception:
            return None
    return None


def fetch_json(url: str, token: str = None) -> dict | None:
    headers = {"User-Agent": "Mozilla/5.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  ⚠ Error fetching {url}: {e}", file=sys.stderr)
        return None


# ─── Match fetching ─────────────────────────────────────────────────────────

def fetch_recent_matches(token: str, hours: int) -> list[dict]:
    """Fetch completed matches from the last `hours` hours."""
    url = f"{API_BASE}/matches?limit=100&teamIds={TEAM_ID}"
    data = fetch_json(url, token)
    if not data or "matches" not in data:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent = []
    for m in data["matches"]:
        created = m.get("createdAt")
        if not created:
            continue
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if dt > cutoff:
                recent.append(m)
        except ValueError:
            continue
    return recent


def get_match_detail(token: str, match_id: str) -> dict | None:
    return fetch_json(f"{API_BASE}/matches/{match_id}", token)


def determine_our_side(match_detail: dict, match_summary: dict) -> tuple[str, str, int]:
    """Returns (our_side 'a'|'b', opponent_name, pb_team_enum)."""
    m_info = match_detail.get("match", match_detail)
    team_a_id = m_info.get("teamAId") or match_summary.get("teamAId")
    is_team_a = team_a_id == TEAM_ID
    if is_team_a:
        opponent = match_summary.get("teamBName") or m_info.get("teamBName", "Unknown")
        return "a", opponent, pb.TEAM_A
    else:
        opponent = match_summary.get("teamAName") or m_info.get("teamAName", "Unknown")
        return "b", opponent, pb.TEAM_B


# ─── Replay downloading ────────────────────────────────────────────────────

def download_replays(match_id: str, dest_dir: Path) -> bool:
    """Download replay files for a match using .venv/bin/fcode."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [FCODE_BIN, "match", "replay", match_id],
        cwd=str(dest_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    return result.returncode == 0


# ─── Replay scanning ───────────────────────────────────────────────────────

def scan_replay(replay_path: str, our_team_enum: int) -> dict:
    """Scan a replay file for TLEs, errors, and interesting events.

    Returns a dict with:
        - tles: list of (round, unit_id, unit_type, exec_time_us)
        - slow_units: list of (round, unit_id, unit_type, exec_time_us)  # >80% budget
        - our_units_destroyed: list of (round, unit_id, unit_type)
        - total_rounds: int
        - winner: 'us' | 'them' | None
    """
    with open(replay_path, "rb") as f:
        replay = pb.Replay()
        replay.ParseFromString(f.read())

    # Track active entities (id -> protobuf kind string)
    entities: dict[int, tuple[str, int]] = {}  # id -> (kind, team_enum)
    tles = []
    slow_units = []
    our_units_destroyed = []

    for turn_idx, turn in enumerate(replay.turns):
        for update in turn.updates:
            kind = update.WhichOneof("kind")

            if kind == "placeEntity":
                ent = update.placeEntity.entity
                ent_kind = ent.WhichOneof("kind")
                entities[ent.id] = (ent_kind, ent.team)

            elif kind == "removeEntity":
                ent_id = update.removeEntity.id
                if ent_id in entities:
                    ent_kind, ent_team = entities[ent_id]
                    if ent_team == our_team_enum:
                        our_units_destroyed.append((turn_idx, ent_id, ent_kind))
                    del entities[ent_id]

            elif kind == "botOutput":
                bo = update.botOutput
                ent_info = entities.get(bo.id)
                ent_kind = ent_info[0] if ent_info else "unknown"
                ent_team = ent_info[1] if ent_info else -1

                if ent_team == our_team_enum:
                    if bo.tled:
                        tles.append((turn_idx, bo.id, ent_kind, bo.execTimeUs))
                    elif bo.execTimeUs >= TLE_WARNING_US:
                        slow_units.append((turn_idx, bo.id, ent_kind, bo.execTimeUs))

    # Determine winner
    winner = None
    if replay.HasField("winner"):
        if replay.winner == our_team_enum:
            winner = "us"
        else:
            winner = "them"

    return {
        "tles": tles,
        "slow_units": slow_units,
        "our_units_destroyed": our_units_destroyed,
        "total_rounds": len(replay.turns),
        "winner": winner,
    }


# ─── Report generation ─────────────────────────────────────────────────────

def generate_report(
    triage_results: list[dict],
    hours: int,
    output_path: Path,
):
    """Generate a markdown triage report."""
    lines = []
    lines.append(f"# Match Triage Report")
    lines.append(f"")
    lines.append(f"Time window: last **{hours} hours** | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"")

    # Summary counts
    total_matches = len(set(r["match_id"] for r in triage_results))
    total_games = len(triage_results)
    games_with_tles = sum(1 for r in triage_results if r["tles"])
    games_lost = sum(1 for r in triage_results if r["winner"] == "them")
    games_won = sum(1 for r in triage_results if r["winner"] == "us")
    total_tles = sum(len(r["tles"]) for r in triage_results)

    lines.append(f"## Summary")
    lines.append(f"")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Matches analyzed | {total_matches} |")
    lines.append(f"| Games analyzed | {total_games} |")
    lines.append(f"| Games won | {games_won} |")
    lines.append(f"| Games lost | {games_lost} |")
    lines.append(f"| Games with TLEs | {games_with_tles} |")
    lines.append(f"| Total TLE events | {total_tles} |")
    lines.append(f"")

    # ── TLE section ──
    tle_games = [r for r in triage_results if r["tles"]]
    if tle_games:
        lines.append(f"## ⚠️ Games with TLE'd Units")
        lines.append(f"")
        for r in tle_games:
            lines.append(f"### {r['map']} — vs {r['opponent']} ({r['result']}) — {r['timestamp']}")
            lines.append(f"")
            lines.append(f"Replay: `{r['replay_path']}`")
            lines.append(f"")
            lines.append(f"| Round | Unit ID | Unit Type | Exec Time |")
            lines.append(f"|-------|---------|-----------|-----------|")
            shown = r["tles"][:10]
            for round_num, uid, utype, exec_us in shown:
                lines.append(f"| {round_num} | {uid} | {utype} | {exec_us}µs |")
            if len(r["tles"]) > 10:
                lines.append(f"| ... | ... | *{len(r['tles']) - 10} more TLEs* | ... |")
            lines.append(f"")

            # Debug commands
            first_tle = r["tles"][0]
            lines.append(f"Debug commands:")
            lines.append(f"```bash")
            lines.append(f"# Show bot output at TLE round")
            lines.append(f".venv/bin/python scripts/replay_debugger.py {r['replay_path']} -r {first_tle[0]} -u {first_tle[1]} -t {r['our_side'].upper()} --show-output")
            lines.append(f"# Re-run unit logic (no CPU limit)")
            lines.append(f".venv/bin/python scripts/replay_debugger.py {r['replay_path']} -r {first_tle[0]} -u {first_tle[1]} -t {r['our_side'].upper()}")
            lines.append(f"```")
            lines.append(f"")

    # ── Slow units section ──
    slow_games = [r for r in triage_results if r["slow_units"] and not r["tles"]]
    if slow_games:
        lines.append(f"## ⏱️ Games with Near-TLE Units (>{TLE_WARNING_US}µs)")
        lines.append(f"")
        for r in slow_games:
            lines.append(f"### {r['map']} — vs {r['opponent']} ({r['result']}) — {r['timestamp']}")
            lines.append(f"")
            lines.append(f"Replay: `{r['replay_path']}`")
            lines.append(f"")
            # Show top 5 slowest
            sorted_slow = sorted(r["slow_units"], key=lambda x: x[3], reverse=True)[:5]
            lines.append(f"| Round | Unit ID | Unit Type | Exec Time |")
            lines.append(f"|-------|---------|-----------|-----------|")
            for round_num, uid, utype, exec_us in sorted_slow:
                lines.append(f"| {round_num} | {uid} | {utype} | {exec_us}µs |")
            lines.append(f"")

    # ── Losses section ──
    loss_games = [r for r in triage_results if r["winner"] == "them" and not r["tles"]]
    if loss_games:
        lines.append(f"## ❌ Losses (no TLEs)")
        lines.append(f"")
        lines.append(f"| Map | Opponent | Condition | Rounds | Time | Replay |")
        lines.append(f"|-----|----------|-----------|--------|------|--------|")
        for r in loss_games:
            replay_short = Path(r["replay_path"]).name
            lines.append(f"| {r['map']} | {r['opponent']} | {r['win_condition']} | {r['total_rounds']} | {r['timestamp']} | `{replay_short}` |")
        lines.append(f"")

    # ── TLE frequency by unit type ──
    if total_tles > 0:
        lines.append(f"## 📊 TLE Distribution by Unit Type")
        lines.append(f"")
        type_counts = defaultdict(int)
        for r in triage_results:
            for _, _, utype, _ in r["tles"]:
                type_counts[utype] += 1
        lines.append(f"| Unit Type | TLE Count |")
        lines.append(f"|-----------|-----------|")
        for utype, count in sorted(type_counts.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"| {utype} | {count} |")
        lines.append(f"")

        # TLE frequency by map
        lines.append(f"## 📊 TLE Distribution by Map")
        lines.append(f"")
        map_counts = defaultdict(int)
        for r in triage_results:
            if r["tles"]:
                map_counts[r["map"]] += len(r["tles"])
        lines.append(f"| Map | TLE Count |")
        lines.append(f"|-----|-----------|")
        for map_name, count in sorted(map_counts.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"| {map_name} | {count} |")
        lines.append(f"")

    # Write file
    output_path.write_text("\n".join(lines))
    print(f"  Report written to {output_path}")


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Match Triage Tool — scan recent matches for errors, TLEs, and losses",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--hours", type=int, default=6,
        help="Time window in hours to scan (default: 6)",
    )
    parser.add_argument(
        "--losses-only", action="store_true",
        help="Only download and scan replays for lost matches",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output report path (default: observations/triage_report_<hours>h.md)",
    )
    parser.add_argument(
        "--skip-download", action="store_true",
        help="Skip replay download, only scan existing files in ranked/triage/",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    obs_dir = project_root / "observations"
    obs_dir.mkdir(parents=True, exist_ok=True)

    triage_dir = project_root / "ranked" / "triage"
    triage_dir.mkdir(parents=True, exist_ok=True)

    default_filename = f"triage_report_{args.hours}h.md"
    output_path = Path(args.output) if args.output else obs_dir / default_filename

    # ── Step 1: Fetch matches ──
    token = load_token()
    if not token:
        print("  Error: No auth token. Run '.venv/bin/fcode login' first.", file=sys.stderr)
        sys.exit(1)

    print(f"  Fetching matches from the last {args.hours} hours...")
    matches = fetch_recent_matches(token, args.hours)
    if not matches:
        print(f"  No matches found in the last {args.hours} hours.")
        sys.exit(0)

    completed = [m for m in matches if m.get("status") == "complete"]
    errored = [m for m in matches if m.get("status") == "error"]

    print(f"  Found {len(completed)} completed + {len(errored)} errored matches.")

    # ── Step 2: Filter and download ──
    triage_results = []
    processed_matches = set()

    for m in completed + errored:
        match_id = m["id"]
        if match_id in processed_matches:
            continue
        processed_matches.add(match_id)

        detail = get_match_detail(token, match_id)
        if not detail:
            continue

        our_side, opponent, our_team_enum = determine_our_side(detail, m)
        winner_id = m.get("winnerId")
        we_won = winner_id == TEAM_ID
        is_error = m.get("status") == "error"

        if args.losses_only and we_won and not is_error:
            continue

        # Download replays
        if not args.skip_download:
            print(f"  Downloading {match_id[:8]}... (vs {opponent}, {'WIN' if we_won else 'LOSS'})")
            if not download_replays(match_id, triage_dir):
                print(f"    ⚠ Download failed for {match_id[:8]}")
                continue

        raw_time = m.get("completedAt") or m.get("createdAt") or ""
        timestamp_str = raw_time[:16].replace("T", " ") if raw_time else "N/A"

        # ── Step 3: Scan each game's replay ──
        for game in detail.get("games", []):
            game_num = game.get("gameNumber")
            map_name = game.get("mapName", "unknown")
            win_condition = game.get("winCondition", "unknown")
            game_winner_id = game.get("winnerId")
            game_result = "WIN" if game_winner_id == TEAM_ID else "LOSS"

            replay_filename = f"{match_id}_game_{game_num}.replay26"
            replay_path = triage_dir / replay_filename

            if not replay_path.exists():
                continue

            scan = scan_replay(str(replay_path), our_team_enum)

            triage_results.append({
                "match_id": match_id,
                "game_num": game_num,
                "map": map_name,
                "opponent": opponent,
                "our_side": our_side,
                "result": game_result,
                "timestamp": timestamp_str,
                "win_condition": CONDITION_LABELS.get(win_condition, win_condition),
                "winner": scan["winner"],
                "total_rounds": scan["total_rounds"],
                "tles": scan["tles"],
                "slow_units": scan["slow_units"],
                "replay_path": str(replay_path),
            })

    if not triage_results:
        print("  No games to report.")
        sys.exit(0)

    # ── Step 4: Generate report ──
    print(f"\n  Scanned {len(triage_results)} games across {len(processed_matches)} matches.")
    games_with_tles = sum(1 for r in triage_results if r["tles"])
    games_lost = sum(1 for r in triage_results if r["winner"] == "them")
    total_tles = sum(len(r["tles"]) for r in triage_results)
    print(f"  TLE events: {total_tles} across {games_with_tles} games")
    print(f"  Losses: {games_lost}")

    generate_report(triage_results, args.hours, output_path)


if __name__ == "__main__":
    main()
