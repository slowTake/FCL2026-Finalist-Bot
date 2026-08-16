#!/usr/bin/env python3
"""Standalone automated matchup test suite for fcode RTS engine replays.

Automates head-to-head testing between two specified bots across a configurable
number of maps, runs fcv analysis detectors on each generated replay, and outputs
clean, highly readable executive summaries and an aggregated campaign report.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import random
import sys
from pathlib import Path

# Add florent-code-visualiser to sys.path
REPO_DIR = Path(__file__).resolve().parent
FCV_DIR = REPO_DIR / "florent-code-visualiser"
if FCV_DIR.exists() and str(FCV_DIR) not in sys.path:
    sys.path.insert(0, str(FCV_DIR))

import fcv.analysis as analysis_mod
import fcv.metrics as metrics_mod
import fcv.replay as replay_mod
import fcv.runner as runner_mod


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automated Head-to-Head Bot Matchup Test Suite with FCV Analytics"
    )
    parser.add_argument(
        "--bot-a",
        required=True,
        help="Path or folder name of Bot A (e.g. bots/baron_v2 or baron_v2)",
    )
    parser.add_argument(
        "--bot-b",
        required=True,
        help="Path or folder name of Bot B (e.g. bots/baron or earl)",
    )
    parser.add_argument(
        "--num-maps",
        type=int,
        default=5,
        help="Number of maps to randomly select from map pool (default: 5)",
    )
    parser.add_argument(
        "--seeds",
        default="1,2",
        help="Comma-separated seed list per map (default: '1,2')",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=4,
        help="Number of parallel match execution workers (default: 4)",
    )
    parser.add_argument(
        "--output-dir",
        default="test_results",
        help="Directory where replays and reports will be saved (default: 'test_results')",
    )
    return parser.parse_args()


def resolve_bot_name(bot_input: str) -> str:
    """Normalize bot path or folder name to simple bot directory name."""
    p = Path(bot_input)
    if p.parent.name == "bots":
        return p.name
    return p.name


def format_cpu(cpu_us: int | float | None) -> str:
    if cpu_us is None or cpu_us <= 0:
        return "0.0ms"
    ms = cpu_us / 1000.0
    if ms >= 10.0:
        return f"{ms:.1f}ms (TIMEOUT WARNING)"
    return f"{ms:.1f}ms"


def format_win_condition(cond: str | None) -> str:
    if not cond:
        return "Match finished"
    return cond.replace("_", " ").title()


def format_connected_harvester(connected: int, total: int) -> str:
    if total == 0:
        return "0/0 connected"
    pct = int((connected / total) * 100)
    return f"{pct}% connected harvesters ({connected}/{total})"


def get_finding_team(f: analysis_mod.Finding, rep: replay_mod.Replay) -> str | None:
    """Extract team ownership for a given detector finding."""
    if isinstance(f.detail, dict) and "team" in f.detail:
        return f.detail["team"]
    if f.actor is not None and f.turn < len(rep.turns):
        world = rep.world_at(f.turn)
        ent = world.entities.get(f.actor)
        if ent is not None:
            return ent.team
    return None


def run_single_game_job(task_info: tuple[int, str, str, str, int, bool, Path, str]) -> dict:
    """Worker task executing a single match and performing FCV replay analysis."""
    match_idx, bot_a, bot_b, map_name, seed, is_swapped, output_dir, fcode_bin = task_info

    maps_dir = REPO_DIR / "maps"
    map_path = str(maps_dir / f"{map_name}.map26")

    # Call FCV runner
    # When swapped=False: Bot A is Blue (TEAM_A) and Bot B is Orange (TEAM_B)
    # When swapped=True:  Bot B is Blue (TEAM_A) and Bot A is Orange (TEAM_B)
    res = runner_mod.run_game(
        bot_a if not is_swapped else bot_b,
        bot_b if not is_swapped else bot_a,
        map_path,
        seed=seed,
        out_dir=output_dir,
        fcode=fcode_bin,
        swapped=is_swapped,
    )

    replay_file = Path(res.replay) if res.replay else None
    if not replay_file or not replay_file.exists():
        return {"match_idx": match_idx, "ok": False, "error": res.error or "Replay missing"}

    # Load and analyze replay using FCV
    rep = replay_mod.Replay.load(str(replay_file))
    findings = analysis_mod.analyse(rep)
    series, summaries = metrics_mod.compute(rep)

    # Determine which FCV team code belongs to Bot A vs Bot B
    bot_a_team = "TEAM_A" if not is_swapped else "TEAM_B"
    bot_b_team = "TEAM_B" if not is_swapped else "TEAM_A"

    bot_a_blue = not is_swapped
    bot_b_blue = is_swapped

    # Determine winner
    if rep.winner == bot_a_team:
        winner_code = "BOT_A"
        winner_name = f"Bot A ({bot_a})"
    elif rep.winner == bot_b_team:
        winner_code = "BOT_B"
        winner_name = f"Bot B ({bot_b})"
    else:
        winner_code = "DRAW"
        winner_name = "Draw"

    # Extract metrics per team
    a_summary = summaries.get(bot_a_team)
    b_summary = summaries.get(bot_b_team)

    # Extract total titanium collected/mined
    a_series_data = series.data.get(bot_a_team, {})
    b_series_data = series.data.get(bot_b_team, {})

    a_ti = a_series_data.get("collected", [0])[-1] if a_series_data else 0
    b_ti = b_series_data.get("collected", [0])[-1] if b_series_data else 0

    a_cpu_max = max(a_summary.cpu_samples) if a_summary and a_summary.cpu_samples else 0
    b_cpu_max = max(b_summary.cpu_samples) if b_summary and b_summary.cpu_samples else 0

    # Filter findings per team
    a_findings = [f for f in findings if get_finding_team(f, rep) == bot_a_team]
    b_findings = [f for f in findings if get_finding_team(f, rep) == bot_b_team]

    a_crashes = [f for f in a_findings if f.kind == "crash"]
    b_crashes = [f for f in b_findings if f.kind == "crash"]

    a_friendly_fire = len([f for f in a_findings if f.kind == "friendly_fire"])
    b_friendly_fire = len([f for f in b_findings if f.kind == "friendly_fire"])

    a_orphans = [f for f in a_findings if f.kind == "orphan_harvester"]
    b_orphans = [f for f in b_findings if f.kind == "orphan_harvester"]

    # Connected harvesters count from series data
    a_conn_cnt = a_series_data.get("connectedHarvesters", [0])[-1] if a_series_data else 0
    a_tot_cnt = a_series_data.get("harvesters", [0])[-1] if a_series_data else 0
    a_conn = (a_conn_cnt, a_tot_cnt)

    b_conn_cnt = b_series_data.get("connectedHarvesters", [0])[-1] if b_series_data else 0
    b_tot_cnt = b_series_data.get("harvesters", [0])[-1] if b_series_data else 0
    b_conn = (b_conn_cnt, b_tot_cnt)

    return {
        "match_idx": match_idx,
        "ok": True,
        "map": map_name,
        "seed": seed,
        "turns": rep.total_turns,
        "condition": rep.win_condition,
        "winner_code": winner_code,
        "winner_name": winner_name,
        "bot_a_blue": bot_a_blue,
        "bot_b_blue": bot_b_blue,
        "bot_a_ti": a_ti,
        "bot_b_ti": b_ti,
        "bot_a_cpu_max": a_cpu_max,
        "bot_b_cpu_max": b_cpu_max,
        "bot_a_crashes": a_crashes,
        "bot_b_crashes": b_crashes,
        "bot_a_ff": a_friendly_fire,
        "bot_b_ff": b_friendly_fire,
        "bot_a_orphans": len(a_orphans),
        "bot_b_orphans": len(b_orphans),
        "bot_a_conn": a_conn,
        "bot_b_conn": b_conn,
        "replay_path": str(replay_file),
    }


def main() -> None:
    args = parse_args()

    bot_a = resolve_bot_name(args.bot_a)
    bot_b = resolve_bot_name(args.bot_b)
    seeds = [int(s.strip()) for s in args.seeds.split(",") if s.strip()]

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    maps_dir = REPO_DIR / "maps"
    available_maps = runner_mod.discover_maps(str(maps_dir))
    if not available_maps:
        print(f"Error: No .map26 files found in {maps_dir}")
        sys.exit(1)

    num_maps = min(args.num_maps, len(available_maps))
    random.seed(42)
    selected_maps = sorted(random.sample(available_maps, num_maps))

    fcode_bin = runner_mod.find_fcode()

    # Build side-swapped match tasks
    tasks = []
    match_idx = 0
    for m in selected_maps:
        for seed in seeds:
            # Orientation 1: Bot A is Blue, Bot B is Orange (swapped=False)
            match_idx += 1
            tasks.append((match_idx, bot_a, bot_b, m, seed, False, output_dir, fcode_bin))

            # Orientation 2: Bot B is Blue, Bot A is Orange (swapped=True)
            match_idx += 1
            tasks.append((match_idx, bot_a, bot_b, m, seed, True, output_dir, fcode_bin))

    print("=" * 80)
    print("      AUTOMATED HEAD-TO-HEAD BOT MATCHUP TEST SUITE (FCV ENGINE)      ")
    print("=" * 80)
    print(f"  Bot A:           {bot_a}")
    print(f"  Bot B:           {bot_b}")
    print(f"  Maps Sampled:    {num_maps} maps ({', '.join(selected_maps)})")
    print(f"  Seeds per Map:   {seeds}")
    print(f"  Total Matches:   {len(tasks)} (side-swapped)")
    print(f"  Parallel Jobs:   {args.jobs}")
    print(f"  Output Directory:{output_dir}")
    print("=" * 80)
    print()

    # Execute matches in parallel
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = [executor.submit(run_single_game_job, task) for task in tasks]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            results.append(res)

    # Sort results by match_idx
    results.sort(key=lambda x: x["match_idx"])

    # Campaign statistics
    bot_a_wins = 0
    bot_b_wins = 0
    draws = 0
    total_a_ti = 0
    total_b_ti = 0
    total_a_crashes = 0
    total_b_crashes = 0
    total_a_orphans = 0
    total_b_orphans = 0

    for r in results:
        if not r.get("ok"):
            print(f"MATCH #{r['match_idx']}: FAILED ({r.get('error')})")
            continue

        if r["winner_code"] == "BOT_A":
            bot_a_wins += 1
        elif r["winner_code"] == "BOT_B":
            bot_b_wins += 1
        else:
            draws += 1

        total_a_ti += r["bot_a_ti"]
        total_b_ti += r["bot_b_ti"]

        total_a_crashes += len(r["bot_a_crashes"])
        total_b_crashes += len(r["bot_b_crashes"])

        total_a_orphans += r["bot_a_orphans"]
        total_b_orphans += r["bot_b_orphans"]

        blue_label = f"Bot A ({bot_a})" if r["bot_a_blue"] else f"Bot B ({bot_b})"
        orange_label = f"Bot B ({bot_b})" if r["bot_a_blue"] else f"Bot A ({bot_a})"

        win_cond_str = format_win_condition(r["condition"])

        print("=" * 80)
        print(f"MATCH #{r['match_idx']}: [{r['map']}] (Seed {r['seed']}) | {blue_label} [Blue] vs {orange_label} [Orange]")
        print(f"Winner: {r['winner_name']} (Turn {r['turns']} - {win_cond_str})")
        print("-" * 80)
        print("Key Insights & Detectors:")

        # Bot A line
        a_crash_msg = f"{len(r['bot_a_crashes'])} CRASHES" if r["bot_a_crashes"] else "0 crashes"
        a_conn_str = format_connected_harvester(r["bot_a_conn"][0], r["bot_a_conn"][1])
        print(f"  • Bot A ({bot_a}): {a_crash_msg} | {r['bot_a_ff']} friendly fire | {a_conn_str}")
        if r["bot_a_crashes"]:
            for c in r["bot_a_crashes"]:
                print(f"    └─ ⚠️  CRASH detected (Turn {c.turn})")
        if r["bot_a_orphans"]:
            print(f"    └─ {r['bot_a_orphans']} Orphan Harvester(s) detected")

        # Bot B line
        b_crash_msg = f"{len(r['bot_b_crashes'])} CRASHES" if r["bot_b_crashes"] else "0 crashes"
        b_conn_str = format_connected_harvester(r["bot_b_conn"][0], r["bot_b_conn"][1])
        print(f"  • Bot B ({bot_b}): {b_crash_msg} | {r['bot_b_ff']} friendly fire | {b_conn_str}")
        if r["bot_b_crashes"]:
            for c in r["bot_b_crashes"]:
                print(f"    └─ ⚠️  CRASH detected (Turn {c.turn})")
        if r["bot_b_orphans"]:
            print(f"    └─ {r['bot_b_orphans']} Orphan Harvester(s) detected")

        print("Performance:")
        print(f"  • Bot A Ti Delivered: {r['bot_a_ti']} Ti | CPU Max: {format_cpu(r['bot_a_cpu_max'])}")
        print(f"  • Bot B Ti Delivered: {r['bot_b_ti']} Ti | CPU Max: {format_cpu(r['bot_b_cpu_max'])}")
        print("=" * 80)
        print()

    # Final Aggregated Summary
    print("#" * 80)
    print("                        FINAL MATCHUP CAMPAIGN REPORT                        ")
    print("#" * 80)
    print(f"  Overall Match Score:  Bot A ({bot_a}) {bot_a_wins}  -  {bot_b_wins} Bot B ({bot_b})  (Draws: {draws})")
    print(f"  Total Ti Delivered:   Bot A ({bot_a}): {total_a_ti} Ti  |  Bot B ({bot_b}): {total_b_ti} Ti")
    print(f"  Total Unit Crashes:   Bot A ({bot_a}): {total_a_crashes}  |  Bot B ({bot_b}): {total_b_crashes}")
    print(f"  Orphan Harvesters:    Bot A ({bot_a}): {total_a_orphans}  |  Bot B ({bot_b}): {total_b_orphans}")
    print("-" * 80)
    if bot_a_wins > bot_b_wins:
        print(f"  CAMPAIGN WINNER: Bot A ({bot_a}) (+{bot_a_wins - bot_b_wins} wins)")
    elif bot_b_wins > bot_a_wins:
        print(f"  CAMPAIGN WINNER: Bot B ({bot_b}) (+{bot_b_wins - bot_a_wins} wins)")
    else:
        print("  CAMPAIGN RESULT: EVEN TIE")
    print("#" * 80)


if __name__ == "__main__":
    main()
