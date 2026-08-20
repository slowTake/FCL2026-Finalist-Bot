#!/usr/bin/env python3
"""Analyze our team's most recent real ladder/scrim matches — not local
test games — using fcv_gauntlet's causal-analysis engine on the actual
downloaded replays. Every other tool in this repo answers "how does our
bot do against a controlled local opponent"; this pulls real match history
from the platform via the `fcode` CLI (same auth as `fcode login`),
downloads each game's real replay, and runs the same crash/Ti-leak/
orphan-harvester/friendly-fire/causal-chain detectors used locally
(scripts/fcv_bot_gauntlet.py) against the games that actually counted.

Side attribution is load-bearing and non-obvious, so it's worth spelling
out: fcv_gauntlet's detectors tag every event by the replay's own recorded
TEAM_A/TEAM_B, and which of "us" or the opponent is team A varies match to
match (verified against a real downloaded replay while building this — get
it backwards and the report silently swaps whose mistakes are whose, with
no error to catch it). Every call here derives team_a_bot/team_b_bot from
the match's own teamAId/teamBId, never assumes "us" is a fixed side.

Usage:
    python3 scripts/ladder/ladder_report.py                       # last 5 matches
    python3 scripts/ladder/ladder_report.py --limit 10 --type ladder
    python3 scripts/ladder/ladder_report.py --match <match-id>     # one specific match
    python3 scripts/ladder/ladder_report.py --losses-only
    python3 scripts/ladder/ladder_report.py --markdown-out test_results/ladder_report.md
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
from fcv_gauntlet.runner import run_standalone_replay_analysis  # noqa: E402

REPLAY_CACHE_DIR = REPO_ROOT / "ladder_replay_cache"


def find_fcode_bin() -> str:
    venv_fcode = REPO_ROOT / ".venv" / "bin" / "fcode"
    if venv_fcode.exists():
        return str(venv_fcode)
    path_fcode = shutil.which("fcode")
    if path_fcode:
        return path_fcode
    print("Could not locate `fcode`. Activate the repo venv first.", file=sys.stderr)
    sys.exit(1)


def _fcode_json(args: list[str]):
    fcode_bin = find_fcode_bin()
    proc = subprocess.run([fcode_bin, *args, "--json"], capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"fcode {' '.join(args)} failed: {proc.stderr.strip()}", file=sys.stderr)
        print("Are you logged in? Run `fcode login`.", file=sys.stderr)
        sys.exit(1)
    return json.loads(proc.stdout)


def my_team() -> tuple[str, str]:
    data = _fcode_json(["status"])
    return data["team"]["id"], data["team"]["name"]


def recent_matches(limit: int, match_type: str | None) -> list[dict]:
    args = ["match", "list", "--mine", "--limit", str(limit)]
    if match_type:
        args += ["--type", match_type]
    data = _fcode_json(args)
    return data.get("matches", [])


def match_info(match_id: str) -> dict:
    return _fcode_json(["match", "info", match_id])


def ensure_replays(match_id: str) -> Path:
    """Cache downloaded replays under ladder_replay_cache/<match_id>/ —
    same reasoning as submission_cache.py: avoid re-downloading on every
    report run, and it's regenerable from the platform, not source."""
    dest = REPLAY_CACHE_DIR / match_id
    if dest.exists() and any(dest.glob("*.replay26")):
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    fcode_bin = find_fcode_bin()
    proc = subprocess.run([fcode_bin, "match", "replay", match_id], cwd=dest,
                           capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"  Warning: could not download replays for {match_id}: {proc.stderr.strip()}", file=sys.stderr)
    return dest


def analyze_game(replay_path: Path, team_a_name: str, team_b_name: str) -> dict | None:
    """team_a_name/team_b_name must be the match's own teamAName/teamBName,
    in that order, always — never reordered to put "us" first. See module
    docstring."""
    json_out = Path(tempfile.mkstemp(suffix=".json", prefix="ladder_report_")[1])
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            run_standalone_replay_analysis(
                str(replay_path), bot1=team_a_name, bot2=team_b_name,
                json_output_path=str(json_out),
            )
    except Exception as exc:
        print(f"  Warning: analysis failed for {replay_path.name}: {exc}", file=sys.stderr)
        json_out.unlink(missing_ok=True)
        return None
    if not json_out.exists() or json_out.stat().st_size == 0:
        return None
    data = json.loads(json_out.read_text(encoding="utf-8"))
    json_out.unlink(missing_ok=True)
    return data


def summarize_side(report: dict, side_name: str) -> dict:
    """Only this side's own events — see milestone_gauntlet.py for the same
    "focus on us, not the opponent's separate bugs" philosophy."""
    events = [e for e in report.get("events", []) if e.get("metadata", {}).get("bot_name") == side_name]
    ti_loss = sum(e.get("estimated_ti_loss") or 0 for e in events)
    by_detector: dict[str, int] = {}
    for e in events:
        d = e.get("detector", "?")
        by_detector[d] = by_detector.get(d, 0) + 1
    top = max(events, key=lambda e: e.get("estimated_ti_loss") or 0, default=None)
    return {
        "ti_loss": round(ti_loss, 1),
        "crashes": by_detector.get("crash", 0),
        "orphans": by_detector.get("orphan_harvester", 0),
        "friendly_fire": by_detector.get("friendly_fire", 0),
        "top_issue": top["message"] if top else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=5, help="How many recent matches to analyze (default: 5)")
    ap.add_argument("--type", choices=["ladder", "unrated"], default=None, help="Filter by match type")
    ap.add_argument("--match", default=None, help="Analyze one specific match ID instead of recent history")
    ap.add_argument("--losses-only", action="store_true", help="Only show matches we lost")
    ap.add_argument("--markdown-out", default=None, help="Also write the report to this file")
    args = ap.parse_args()

    my_id, my_name = my_team()
    print(f"Team: {my_name} ({my_id})\n")

    if args.match:
        matches = [{"id": args.match}]
    else:
        matches = recent_matches(args.limit, args.type)
        if not matches:
            print("No matches found.")
            return

    lines: list[str] = [f"# Ladder report — {my_name}", ""]

    for m in matches:
        info = match_info(m["id"])
        match, games = info["match"], info["games"]

        we_are_a = match["teamAId"] == my_id
        our_name = match["teamAName"] if we_are_a else match["teamBName"]
        opp_name = match["teamBName"] if we_are_a else match["teamAName"]
        we_won_match = match.get("winnerId") == my_id
        result = "WIN" if we_won_match else ("LOSS" if match.get("winnerId") else "—")
        if args.losses_only and result != "LOSS":
            continue

        rated = "rated" if match.get("rated") else "unrated"
        elo = ""
        if match.get("rated"):
            delta = match["eloDeltaA"] if we_are_a else match["eloDeltaB"]
            if delta is not None:
                elo = f"  elo {delta:+.1f}"
        score = f"{match['scoreA']}-{match['scoreB']}" if we_are_a else f"{match['scoreB']}-{match['scoreA']}"

        header = f"## {result}  vs {opp_name}  ({score}, {rated}{elo})  [{match['id']}]"
        print(header)
        lines.append(header)
        lines.append("")

        replay_dir = ensure_replays(match["id"])
        for g in games:
            replay_path = replay_dir / f"{match['id']}_game_{g['gameNumber']}.replay26"
            if not replay_path.exists():
                continue
            report = analyze_game(replay_path, match["teamAName"], match["teamBName"])
            we_won_game = g.get("winnerSide") == ("a" if we_are_a else "b")
            game_result = "W" if we_won_game else ("L" if g.get("winnerSide") else "-")
            row = (f"  game {g['gameNumber']} [{game_result}] {g['mapName']:<14} "
                   f"{g['winCondition']:<16} t{g['turnsPlayed']}")
            if report:
                s = summarize_side(report, our_name)
                row += (f"   our Ti lost: {s['ti_loss']:<7} crashes: {s['crashes']} "
                        f"orphans: {s['orphans']} ff: {s['friendly_fire']}")
                if s["top_issue"]:
                    row += f"\n         top issue: {s['top_issue']}"
            print(row)
            lines.append(row)
        print()
        lines.append("")

    if args.markdown_out:
        Path(args.markdown_out).write_text("\n".join(lines), encoding="utf-8")
        print(f"Wrote {args.markdown_out}")


if __name__ == "__main__":
    main()
