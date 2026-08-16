#!/usr/bin/env python3
"""Fetch the top 16 competitors from the Florent Code League ladder
and display their current active bot version numbers.

Usage:
    python3 scripts/get_top16_versions.py
"""

import urllib.request
import json
import sys
from pathlib import Path

LADDER_URL = "https://game.code.florent.vc/api/ladder"


def load_token() -> str | None:
    """Load the authentication token from fcode CLI configuration."""
    creds_file = Path.home() / ".fcode" / "credentials.json"
    if creds_file.exists():
        try:
            data = json.loads(creds_file.read_text())
            return data.get("token")
        except Exception:
            return None
    return None


def fetch_json(url: str, token: str = None) -> dict | list | None:
    """Helper to perform HTTP GET requests and return parsed JSON."""
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


def main():
    token = load_token()

    # 1. Fetch the public ladder
    ladder_data = fetch_json(LADDER_URL, token)
    if not ladder_data:
        print("Error: Could not retrieve ladder rankings.", file=sys.stderr)
        sys.exit(1)

    rankings = ladder_data if isinstance(ladder_data, list) else ladder_data.get("rankings", [])

    top_n = 16
    top_teams = rankings[:top_n]

    if not top_teams:
        print("No teams found on the ladder.", file=sys.stderr)
        sys.exit(1)

    # 2. Print header
    print(f"{'Rank':<6} {'Rating':>8}  {'Team Name':<30} {'Version':>8}  {'Matches':>8}  {'Members'}")
    print(f"{'─'*6} {'─'*8}  {'─'*30} {'─'*8}  {'─'*8}  {'─'*40}")

    # 3. For each team, try to get their latest submission version
    #    The ladder endpoint itself may include version info.
    #    We also attempt to look at recent matches to find their active version.
    for idx, entry in enumerate(top_teams, 1):
        team_id = entry.get("teamId", "")
        team_name = entry.get("teamName", "Unknown")
        rating = entry.get("rating", 0)
        matches_played = entry.get("matchesPlayed", 0)
        members = entry.get("members", [])
        member_names = ", ".join(m.get("name", "?") for m in members) if members else "N/A"

        # Try to get version from the team's most recent match
        version_label = "?"
        try:
            match_url = f"https://game.code.florent.vc/api/matches?teamIds={team_id}&limit=1"
            match_data = fetch_json(match_url, token)
            if match_data:
                match_list = match_data.get("matches", [])
                if match_list:
                    m = match_list[0]
                    if m.get("teamAId") == team_id:
                        v = m.get("teamAVersion")
                    else:
                        v = m.get("teamBVersion")
                    if v is not None:
                        version_label = f"v{v}"
        except Exception:
            pass

        print(f"#{idx:<5} {rating:>8.1f}  {team_name:<30} {version_label:>8}  {matches_played:>8}  {member_names}")

    print()

    # 4. Also dump as a simple JSON for programmatic use
    output = []
    for idx, entry in enumerate(top_teams, 1):
        team_id = entry.get("teamId", "")
        team_name = entry.get("teamName", "Unknown")
        rating = entry.get("rating", 0)
        matches_played = entry.get("matchesPlayed", 0)

        version = None
        try:
            match_url = f"https://game.code.florent.vc/api/matches?teamIds={team_id}&limit=1"
            match_data = fetch_json(match_url, token)
            if match_data:
                match_list = match_data.get("matches", [])
                if match_list:
                    m = match_list[0]
                    if m.get("teamAId") == team_id:
                        version = m.get("teamAVersion")
                    else:
                        version = m.get("teamBVersion")
        except Exception:
            pass

        output.append({
            "rank": idx,
            "team_id": team_id,
            "team_name": team_name,
            "rating": rating,
            "matches_played": matches_played,
            "active_version": int(version) if version is not None else None,
        })

    # Save JSON output
    out_path = Path(__file__).resolve().parents[1] / "top16_versions.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"JSON saved to {out_path.name}")


if __name__ == "__main__":
    main()
