#!/usr/bin/env python3
"""Summarize TLE Bottlenecks from a Replay File.

Scans all TLE events in a .replay26 file, profiles Player.run(ct) for each TLE turn offline,
and outputs a summary of which specific functions are causing the bot to get stuck or TLE.
"""

from __future__ import annotations

import argparse
import cProfile
import pstats
import sys

from collections import defaultdict
from pathlib import Path

# Add script and bot paths
dbg_dir = Path(__file__).resolve().parent
scripts_dir = dbg_dir.parent
teaPot_dir = scripts_dir.parent
bot_dir = teaPot_dir / "bots" / "baron"

sys.path.insert(0, "/home/nraatika/projects/florent/.venv/lib/python3.12/site-packages")
sys.path.insert(0, str(dbg_dir))
sys.path.insert(0, str(scripts_dir))
sys.path.insert(0, str(teaPot_dir))
sys.path.insert(0, str(bot_dir))

import florent_replay_pb2 as pb
from fcode import Team
from mock_controller import MockController
from replay_debugger import _rebuild_store
from replay_state import ReplayStateBuilder

import main
main.DEBUG = True
from main import Player

EXCLUDED_FUNCTIONS = {
    "run",
    "_run_builder",
    "runBuilderRoles",
    "_run_core",
    "gunnerLoop",
    "sentinelLoop",
}


def analyze_replay_tles(replay_path: str, team_str: str = "A") -> int:
    path = Path(replay_path).resolve()
    if not path.exists():
        print(f"Error: replay file not found: {path}", file=sys.stderr)
        return 1

    builder = ReplayStateBuilder(str(path))
    target_team = Team.A if team_str.upper() == "A" else Team.B

    # Scan replay turns for TLE events on target team
    tle_events: list[tuple[int, int, str, int]] = []  # (round, unit_id, unit_type, exec_us)

    with open(str(path), "rb") as f:
        replay_pb = pb.Replay()
        replay_pb.ParseFromString(f.read())

    for r_idx, turn in enumerate(replay_pb.turns):
        for update in turn.updates:
            if update.HasField("botOutput"):
                bo = update.botOutput
                # Check if this output belongs to target team and TLE'd
                if bo.tled:
                    # Find unit entity type from state
                    state = builder.build_state(r_idx)
                    ent = state.entities.get(bo.id)
                    if ent and ent.team == target_team:
                        tle_events.append((r_idx, bo.id, ent.entity_type.name, bo.execTimeUs))

    print("=" * 80)
    print(f"REPLAY TLE BOTTLENECK ANALYSIS: {path.name}")
    print(f"Team: {team_str.upper()} | Total Rounds: {builder.get_total_rounds()} | Winner: {builder.get_winner()}")
    print(f"Total TLE Rounds Detected: {len(tle_events)}")
    print("=" * 80)
    print()

    if not tle_events:
        print(f"No TLE events found for Team {team_str.upper()} in this replay!")
        return 0

    # Profile TLE turns and accumulate bottleneck functions
    func_stats: dict[str, dict[str, float | int | str]] = defaultdict(
        lambda: {"cumtime_ms": 0.0, "calls": 0, "file": "", "line": 0}
    )

    # Rebuild store once up to maximum TLE round
    max_round = max(e[0] for e in tle_events)
    store = {i: 0 for i in range(16)}
    pending_writes: dict[int, int] = {}
    players: dict[int, Player] = {}

    print(f"Profiling {len(tle_events)} TLE round instances offline...\n")

    current_r = 0
    tle_set = set((e[0], e[1]) for e in tle_events)

    for r in range(max_round + 1):
        store.update(pending_writes)
        pending_writes.clear()

        # Check if any TLE unit runs at round r
        for e in tle_events:
            if e[0] == r:
                r_num, uid, utype, exec_us = e
                state = builder.build_state(r_num)
                ct = MockController(state, uid, target_team)
                ct.store = dict(store)

                if uid not in players:
                    players[uid] = Player()
                p = players[uid]

                # Profile this TLE turn
                profiler = cProfile.Profile()
                profiler.enable()
                try:
                    p.run(ct)
                except Exception:
                    pass
                profiler.disable()

                # Record actions to pending_writes
                for action in ct.get_action_log():
                    if action.startswith("write_store"):
                        parts = action.split()
                        slot, val = int(parts[1]), int(parts[2])
                        pending_writes[slot] = val

                # Accumulate pstats
                ps = pstats.Stats(profiler)
                for (fn, line, name), (cc, nc, tt, cumtime, callers) in ps.stats.items():
                    if name in EXCLUDED_FUNCTIONS:
                        continue
                    if any(k in fn for k in ["bots/baron", "components"]):
                        key = f"{name} ({Path(fn).name}:{line})"
                        func_stats[key]["cumtime_ms"] += cumtime * 1000.0
                        func_stats[key]["calls"] += nc
                        func_stats[key]["file"] = Path(fn).name
                        func_stats[key]["line"] = line

    # Sort bottlenecks by cumulative time
    sorted_funcs = sorted(func_stats.items(), key=lambda x: x[1]["cumtime_ms"], reverse=True)

    print("=" * 80)
    print("TOP BOTTLENECK FUNCTIONS GETTING US STUCK (Excluding orchestrators)")
    print("=" * 80)
    print(f"{'Rank':<5} {'Function (File:Line)':<42} {'Total Time (ms)':>16} {'Calls':>8} {'Avg/Call (ms)':>14}")
    print("-" * 80)

    for idx, (func_key, data) in enumerate(sorted_funcs[:15], 1):
        cum_ms = data["cumtime_ms"]
        calls = data["calls"]
        avg_ms = (cum_ms / calls) if calls else 0.0
        print(f"[{idx:>2}]  {func_key:<42} {cum_ms:>16.2f} ms {calls:>8} {avg_ms:>14.3f} ms")

    print("=" * 80)
    print()
    print("Sample TLE Events Scanned:")
    print(f"{'Round':<8} {'Unit ID':<10} {'Unit Type':<15} {'Server Exec Time (µs)':>22}")
    print("-" * 60)
    for r_num, uid, utype, exec_us in tle_events[:10]:
        print(f"{r_num:<8} {uid:<10} {utype:<15} {exec_us:>22,} µs")
    if len(tle_events) > 10:
        print(f"... and {len(tle_events) - 10} more TLE instances.")

    return 0


def main_cli():
    parser = argparse.ArgumentParser(
        description="Summarize TLE bottlenecks from a .replay26 file."
    )
    parser.add_argument("replay", help="Path to .replay26 file")
    parser.add_argument("-t", "--team", default="A", help="Team to analyze (A or B, default A)")
    args = parser.parse_args()

    sys.exit(analyze_replay_tles(args.replay, args.team))


if __name__ == "__main__":
    main_cli()
