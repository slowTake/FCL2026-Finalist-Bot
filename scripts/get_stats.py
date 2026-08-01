#!/usr/bin/env python3
"""Fetch team stats, leaderboard rank, and detailed map performance, saving the results to a bot-specific performance file.

================================================================================
FLORENT CODE LEAGUE API REFERENCE
================================================================================
Below is a complete index of all dictionary keys available from the platform APIs,
which can be extracted to calculate further metrics (like opponent Elo ratings,
map seed patterns, or version success rates).

1. Match Object (from `/api/matches` and `/api/matches/{match_id}`)
   - id (str): Unique UUID identifying the match.
   - status (str): Status of the match ('complete', 'queued', 'running', 'error').
   - stage (str): Specific execution stage (e.g., 'starting', 'running_game_3').
   - errorMessage (str/None): Stack trace or execution error message if status is 'error'.
   - winnerId (str/None): Team ID of the overall match winner (null if draw or incomplete).
   - scoreA (int): Games won by Team A in the Best-of-5 (0 to 3).
   - scoreB (int): Games won by Team B in the Best-of-5 (0 to 3).
   - rated (bool): True if this match affects ELO rating.
   - triggeredBy (str): Source origin of the match (e.g., 'ladder' matchmaking, 'unrated' scrim).
   - eloDeltaA (float/None): Elo change value for Team A.
   - eloDeltaB (float/None): Elo change value for Team B.
   - ratingABefore (float/None): ELO of Team A before the match.
   - ratingBBefore (float/None): ELO of Team B before the match.
   - teamARating (float/None): ELO of Team A after the match.
   - teamBRating (float/None): ELO of Team B after the match.
   - teamAMatchesPlayed (int): Total matches played by Team A after this match.
   - teamBMatchesPlayed (int): Total matches played by Team B after this match.
   - teamAId (str): Unique UUID of Team A.
   - teamBId (str): Unique UUID of Team B.
   - teamAName (str): Display name of Team A.
   - teamBName (str): Display name of Team B.
   - teamAVersion (str): Upload version hash/ID for Team A's bot.
   - teamBVersion (str): Upload version hash/ID for Team B's bot.
   - teamANameStyle (dict/None): Styling overrides for Team A's name display.
   - teamBNameStyle (dict/None): Styling overrides for Team B's name display.
   - sourceMatchAId (str/None): Historical match ID origin for Team A's submission.
   - sourceMatchBId (str/None): Historical match ID origin for Team B's submission.
   - testBotAName (str/None): Test bot filename for A (unrated CLI testing).
   - testBotBName (str/None): Test bot filename for B (unrated CLI testing).
   - submissionAS3Key (str/None): S3 key storage location for A's zip archive.
   - submissionBS3Key (str/None): S3 key storage location for B's zip archive.
   - mapConfig (dict/None): Custom layout configurations.
   - startedAt (str/None): ISO timestamp when the match began running.
   - completedAt (str/None): ISO timestamp when the match finished.
   - createdAt (str): ISO timestamp when the match was created.

2. Game Object (from `/api/matches/{match_id}` inside the list key `games`)
   - id (str): Unique UUID identifying this single game.
   - matchId (str): Parent match UUID.
   - gameNumber (int): The sequence index in the BO5 series (1 to 5).
   - mapName (str): Name of the map played (e.g., 'atoll', 'showdown').
   - mapSeed (int): Random generator seed used by the game engine for this round.
   - winnerId (str/None): Team ID of the game winner (null if draw/incomplete).
   - winnerSide (str/None): Side letter of the winner ('a' or 'b').
   - winCondition (str): Termination condition ('core_destroyed', 'titanium_collected', 'timeout', 'resigned', 'error').
   - resignMessage (str/None): Message provided by the bot if it chose to resign.
   - turnsPlayed (int): Number of rounds completed before the game ended (1 to 1000).
   - replayS3Key (str): S3 filename key for downloading the visualizer replay (e.g., 'matchId_game_X.replay26').
   - createdAt (str): ISO timestamp when the game object was generated.
================================================================================
"""

import urllib.request
import json
import sys
from pathlib import Path
from collections import defaultdict

TEAM_ID = "12fdece5-48c9-4913-9533-6d95b73e22ab"
LADDER_URL = "https://game.code.florent.vc/api/ladder"
MATCHES_URL = f"https://game.code.florent.vc/api/matches?teamIds={TEAM_ID}&limit=100"
SUBMISSIONS_URL = "https://game.code.florent.vc/api/submissions"

_CONDITION_LABELS = {
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

def load_token() -> str or None:
    """Load the authentication token from fcode CLI configuration."""
    creds_file = Path.home() / ".fcode" / "credentials.json"
    if creds_file.exists():
        try:
            data = json.loads(creds_file.read_text())
            return data.get("token")
        except Exception:
            return None
    return None

def fetch_json(url: str, token: str = None) -> dict or None:
    """Helper to perform HTTP GET requests and return parsed JSON."""
    headers = {"User-Agent": "Mozilla/5.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return None

def write_markdown_report(team_name, rank, rating, map_stats, overall_stats, version_stats, match_history_logs, filename):
    """Write the detailed performance metrics to a dynamically named markdown file."""
    output_path = Path(__file__).resolve().parents[1] / filename
    
    lines = []
    lines.append(f"# Florent Code League - Team Performance Report")
    lines.append(f"")
    lines.append(f"## Team Summary")
    lines.append(f"* **Team Name**: `{team_name}`")
    lines.append(f"* **Team ID**: `{TEAM_ID}`")
    lines.append(f"* **Leaderboard Rank**: `#{rank}`")
    lines.append(f"* **Rating**: `{rating:.2f}`")
    lines.append(f"* **Matches Played**: `{overall_stats['total_matches']}`")
    lines.append(f"")
    
    lines.append(f"## Overall Game Metrics")
    lines.append(f"* **Total Games Played**: `{overall_stats['total_games']}`")
    lines.append(f"* **Total Games Won**: `{overall_stats['total_wins']}`")
    lines.append(f"* **Overall Win Rate**: `{overall_stats['overall_win_rate']:.1f}%`")
    lines.append(f"* **Average Game Length**: `{overall_stats['avg_turns']:.1f} turns`")
    lines.append(f"* **Most Played Map**: `{overall_stats['most_played_map']} ({overall_stats['most_played_count']} games)`")
    lines.append(f"* **Most Winning Map**: `{overall_stats['best_map']} ({overall_stats['best_rate']:.1f}% win rate)`")
    lines.append(f"* **Least Winning Map**: `{overall_stats['worst_map']} ({overall_stats['worst_rate']:.1f}% win rate)`")
    lines.append(f"")

    lines.append(f"## Bot Version Performance Breakdown")
    lines.append(f"| Bot Version | Upload Name | Games Played | Wins | Losses | Win Rate | Avg. Turns |")
    lines.append(f"| :--- | :--- | :---: | :---: | :---: | :---: | :---: |")
    
    for v_num in sorted(version_stats.keys()):
        v_data = version_stats[v_num]
        played = v_data["played"]
        wins = v_data["wins"]
        losses = played - wins
        rate = (wins / played) * 100 if played > 0 else 0
        avg_t = sum(v_data["turns"]) / played if played > 0 else 0
        name = v_data["name"] or "[Unnamed]"
        lines.append(f"| **v{v_num}** | {name} | {played} | {wins} | {losses} | `{rate:.1f}%` | {avg_t:.1f} |")
    lines.append(f"")
    
    lines.append(f"## Map-by-Map Statistics")
    lines.append(f"| Map Name | Games Played | Wins | Losses | Win Rate | Avg. Turns | Win Conditions |")
    lines.append(f"| :--- | :---: | :---: | :---: | :---: | :---: | :--- |")
    
    # Sort maps alphabetically for consistent table layout
    for map_name in sorted(map_stats.keys()):
        stats = map_stats[map_name]
        played = stats["played"]
        wins = stats["wins"]
        losses = played - wins
        win_rate = (wins / played) * 100 if played > 0 else 0
        avg_turns = sum(stats["turns"]) / played if played > 0 else 0
        
        # Format win conditions string
        cond_strings = []
        for cond, count in stats["win_conditions"].items():
            label = _CONDITION_LABELS.get(cond, cond)
            cond_strings.append(f"{label} (x{count})")
        conds_formatted = ", ".join(cond_strings) if cond_strings else "None"
        
        lines.append(f"| **{map_name}** | {played} | {wins} | {losses} | `{win_rate:.1f}%` | {avg_turns:.1f} | {conds_formatted} |")
        
    lines.append(f"")
    lines.append(f"## Win Condition Distribution")
    for cond, count in sorted(overall_stats["conditions"].items(), key=lambda x: x[1], reverse=True):
        label = _CONDITION_LABELS.get(cond, cond)
        pct = (count / overall_stats["total_wins"]) * 100 if overall_stats["total_wins"] > 0 else 0
        lines.append(f"* **{label}**: {count} wins (`{pct:.1f}%` of total wins)")
        
    lines.append(f"")
    lines.append(f"## Recent Match History")
    lines.append(f"| Match ID | Opponent Team | Result | Score | Bot Used | ELO Change | Date |")
    lines.append(f"| :--- | :--- | :---: | :---: | :---: | :---: | :--- |")
    for m in match_history_logs:
        lines.append(f"| `{m['id'][:8]}` | {m['opponent']} | {m['result']} | {m['score']} | **v{m['bot_version']}** ({m['bot_name']}) | {m['elo']} | {m['date']} |")
    
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"*Report generated automatically by `scripts/get_stats.py`.*")
    
    output_path.write_text("\n".join(lines))
    print(f"  Report:          Successfully saved to {filename}")

def main():
    # 1. Fetch Public Ladder Stats
    ladder_data = fetch_json(LADDER_URL)
    if not ladder_data:
        print("Error: Could not retrieve ladder rankings.", file=sys.stderr)
        sys.exit(1)

    rankings = ladder_data if isinstance(ladder_data, list) else ladder_data.get("rankings", [])
    team_entry = None
    rank = None

    for idx, entry in enumerate(rankings):
        if entry.get("teamId") == TEAM_ID:
            team_entry = entry
            rank = idx + 1
            break

    if not team_entry:
        print(f"Team ID {TEAM_ID} is not currently ranked on the ladder.")
        sys.exit(0)

    team_name = team_entry.get('teamName', 'HTTP 418')
    rating = team_entry.get('rating', 0)
    matches_played = team_entry.get('matchesPlayed', 0)

    print(f"==================================================")
    print(f" TEAM STATS FOR: {team_name}")
    print(f"==================================================")
    print(f"  Rank:            #{rank}")
    print(f"  Rating:          {rating:.2f}")
    print(f"  Matches Played:  {matches_played}")
    print(f"  Region:          {team_entry.get('region', 'N/A').title()}")
    print(f"  Members:")
    for member in team_entry.get("members", []):
        print(f"    - {member.get('name')} ({member.get('affiliation', 'N/A')})")

    # 2. Fetch Authenticated Map Stats (if logged in)
    token = load_token()
    if not token:
        print(f"==================================================")
        print(f"  Map stats:       Locked [Unauthenticated]")
        print(f"  Tip: Run 'fcode login' in your terminal to unlock")
        print(f"       most/least winning map calculations.")
        print(f"==================================================")
        sys.exit(0)

    print(f"  Map stats:       Calculating from match history...")
    
    # Fetch Submissions to get name lookup and active version
    subs_data = fetch_json(SUBMISSIONS_URL, token)
    submission_names = {}
    active_submission_name = "team"
    
    if subs_data:
        subs = subs_data.get("submissions", [])
        for s in subs:
            v = s.get("version")
            if v is not None:
                submission_names[int(v)] = s.get("name")
            if s.get("isActive"):
                active_submission_name = s.get("name") or f"v{v}"
    
    # Sanitize the active bot name to make it a safe filename
    clean_bot_name = "".join(c for c in active_submission_name if c.isalnum() or c in ("_", "-")).lower()
    filename = f"{clean_bot_name}_performance.md"

    matches_data = fetch_json(MATCHES_URL, token)
    if not matches_data:
        print(f"  Error: Failed to fetch match list.")
        print(f"==================================================")
        sys.exit(1)

    match_list = matches_data.get("matches", [])
    completed_matches = [m for m in match_list if m.get("status") == "complete"]

    if not completed_matches:
        print(f"  No completed matches found in history.")
        print(f"==================================================")
        sys.exit(0)

    # Detailed statistics mappings
    map_stats = defaultdict(lambda: {
        "wins": 0,
        "played": 0,
        "turns": [],
        "win_conditions": defaultdict(int),
        "loss_conditions": defaultdict(int)
    })
    
    # Bot version statistics mapping: version_num -> data
    version_stats = defaultdict(lambda: {
        "name": None,
        "wins": 0,
        "played": 0,
        "turns": []
    })
    
    # Store submission names in our struct
    for v_num, name in submission_names.items():
        version_stats[v_num]["name"] = name
    
    overall_wins = 0
    overall_turns = []
    overall_conditions = defaultdict(int)
    match_history_logs = []

    for m in completed_matches:
        match_id = m.get("id")
        
        # Determine opponent team details
        is_team_a = (m.get("teamAId") == TEAM_ID)
        my_version_raw = m.get("teamAVersion") if is_team_a else m.get("teamBVersion")
        bot_version = int(my_version_raw) if my_version_raw is not None else 0
        bot_name = submission_names.get(bot_version) or "Unknown"
        version_stats[bot_version]["name"] = bot_name

        opponent = m.get("teamBName") if is_team_a else m.get("teamAName")
        score = f"{m.get('scoreA')}-{m.get('scoreB')}" if is_team_a else f"{m.get('scoreB')}-{m.get('scoreA')}"
        
        winner_id = m.get("winnerId")
        won = (winner_id == TEAM_ID)
        result_label = "[green]WIN[/green]" if won else "[red]LOSS[/red]"
        
        elo_change = m.get("eloDeltaA") if is_team_a else m.get("eloDeltaB")
        elo_label = "0.0"
        if elo_change is not None:
            sign = "+" if elo_change >= 0 else ""
            elo_label = f"{sign}{elo_change:.1f}"

        raw_date = m.get("completedAt") or m.get("createdAt")
        date_label = raw_date[:10] if raw_date else "N/A"

        match_history_logs.append({
            "id": match_id,
            "opponent": opponent,
            "result": result_label,
            "score": score,
            "bot_version": bot_version,
            "bot_name": bot_name,
            "elo": elo_label,
            "date": date_label
        })

        detail = fetch_json(f"https://game.code.florent.vc/api/matches/{match_id}", token)
        if not detail:
            continue
        
        for g in detail.get("games", []):
            map_name = g.get("mapName")
            if not map_name:
                continue
            
            g_winner_id = g.get("winnerId")
            turns = g.get("turnsPlayed", 0)
            cond = g.get("winCondition", "unknown")
            
            map_stats[map_name]["played"] += 1
            map_stats[map_name]["turns"].append(turns)
            overall_turns.append(turns)
            
            version_stats[bot_version]["played"] += 1
            version_stats[bot_version]["turns"].append(turns)
            
            if g_winner_id == TEAM_ID:
                map_stats[map_name]["wins"] += 1
                map_stats[map_name]["win_conditions"][cond] += 1
                version_stats[bot_version]["wins"] += 1
                overall_wins += 1
                overall_conditions[cond] += 1
            else:
                map_stats[map_name]["loss_conditions"][cond] += 1

    total_games = len(overall_turns)
    if total_games == 0:
        print(f"  Could not extract individual game data.")
        print(f"==================================================")
        sys.exit(0)

    # Compute performance summaries
    performance = []
    most_played_map = None
    most_played_count = -1
    
    for map_name, stats in map_stats.items():
        wins = stats["wins"]
        played = stats["played"]
        win_rate = (wins / played) * 100
        performance.append((map_name, wins, played, win_rate))
        
        if played > most_played_count:
            most_played_count = played
            most_played_map = map_name

    performance.sort(key=lambda x: (x[3], x[2]), reverse=True)
    best_map = performance[0]
    worst_map = performance[-1]

    overall_stats = {
        "total_matches": len(completed_matches),
        "total_games": total_games,
        "total_wins": overall_wins,
        "overall_win_rate": (overall_wins / total_games) * 100,
        "avg_turns": sum(overall_turns) / total_games,
        "most_played_map": most_played_map,
        "most_played_count": most_played_count,
        "best_map": best_map[0],
        "best_rate": best_map[3],
        "worst_map": worst_map[0],
        "worst_rate": worst_map[3],
        "conditions": overall_conditions
    }

    # Print Console Summary
    print(f"==================================================")
    print(f" OVERALL SUMMARY:")
    print(f"==================================================")
    print(f"  Win Rate:        {overall_stats['overall_win_rate']:.1f}% ({overall_wins}/{total_games} games)")
    print(f"  Avg. Game Len:   {overall_stats['avg_turns']:.1f} turns")
    print(f"  Most Played:     {most_played_map} ({most_played_count} games)")
    print(f"  Most Winning:    {best_map[0]} ({best_map[3]:.1f}% win rate)")
    print(f"  Least Winning:   {worst_map[0]} ({worst_map[3]:.1f}% win rate)")
    
    # 3. Write Detailed Markdown File
    write_markdown_report(team_name, rank, rating, map_stats, overall_stats, version_stats, match_history_logs, filename)
    print(f"==================================================")

if __name__ == "__main__":
    main()
