#!/usr/bin/env python3
"""Automated CI/CD Bot Performance & Regression Testing Suite.

Runs a map gauntlet across match replays, extracts economic & tactical
performance benchmarks, and generates a structured Markdown pass/fail summary.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Tuple

from pathlib import Path

# Ensure fcv is importable from repo root or florent-code-visualiser subfolder
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
fcv_dir = os.path.join(repo_root, "florent-code-visualiser")
if fcv_dir not in sys.path:
    sys.path.insert(0, fcv_dir)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from fcv import metrics
    from fcv.replay import Replay
    from fcv.runner import find_fcode, run_game
except Exception as e:
    print(f"Error: Could not import fcv runner/metrics module: {e}")
    sys.exit(1)


@dataclass
class MatchBenchmark:
    map_name: str
    team_a_bot: str
    team_b_bot: str
    winner: str
    end_turn: int
    ti_delivered_a: int
    ti_delivered_b: int
    crashes_a: int
    crashes_b: int
    orphans_a: int
    orphans_b: int
    first_connected_turn_a: int | None
    first_connected_turn_b: int | None


class RegressionSuite:
    def __init__(self, bot_a: str, bot_b: str, maps_dir: str = "maps", max_maps: int = 10):
        self.bot_a = bot_a
        self.bot_b = bot_b
        self.maps_dir = maps_dir
        self.max_maps = max_maps
        self.benchmarks: List[MatchBenchmark] = []

    def run_gauntlet(self) -> List[MatchBenchmark]:
        map_files = sorted(glob.glob(os.path.join(self.maps_dir, "*.map26")))[: self.max_maps]
        if not map_files:
            print(f"No .map26 files found in {self.maps_dir}")
            return []

        out_dir = os.path.abspath("test_results/ci_gauntlet")
        os.makedirs(out_dir, exist_ok=True)
        fcode_bin = find_fcode()

        for i, map_file in enumerate(map_files):
            map_name = os.path.basename(map_file).replace(".map26", "")

            # Run match
            res = run_game(self.bot_a, self.bot_b, map_file, seed=1, out_dir=Path(out_dir), fcode=fcode_bin)
            replay_path = res.replay

            # Analyze replay
            if replay_path and os.path.exists(replay_path):
                bench = self.analyze_replay(map_name, str(replay_path))
                self.benchmarks.append(bench)
            else:
                print(f"Warning: Replay file not generated for {map_name}: {res.error}")

        return self.benchmarks

    def analyze_replay(self, map_name: str, replay_path: str) -> MatchBenchmark:
        replay = Replay.load(replay_path)
        series, summaries = metrics.compute(replay)

        sum_a = summaries.get("TEAM_A")
        sum_b = summaries.get("TEAM_B")

        # Determine winner
        winner = "DRAW"
        if replay.total_turns < 1000:
            last_world = replay.world_at(replay.total_turns)
            core_a = [e for e in last_world.entities.values() if e.team == "TEAM_A" and e.kind == "core"]
            core_b = [e for e in last_world.entities.values() if e.team == "TEAM_B" and e.kind == "core"]
            if not core_a and core_b:
                winner = self.bot_b
            elif not core_b and core_a:
                winner = self.bot_a

        return MatchBenchmark(
            map_name=map_name,
            team_a_bot=self.bot_a,
            team_b_bot=self.bot_b,
            winner=winner,
            end_turn=replay.total_turns,
            ti_delivered_a=int(sum_a.final_scale) if sum_a else 0,
            ti_delivered_b=int(sum_b.final_scale) if sum_b else 0,
            crashes_a=sum_a.tled if sum_a else 0,
            crashes_b=sum_b.tled if sum_b else 0,
            orphans_a=sum_a.orphan_harvester_turns if sum_a else 0,
            orphans_b=sum_b.orphan_harvester_turns if sum_b else 0,
            first_connected_turn_a=sum_a.first_connected_harvester if sum_a else None,
            first_connected_turn_b=sum_b.first_connected_harvester if sum_b else None,
        )

    def generate_markdown_report(self) -> str:
        lines = [
            "# 🤖 CI/CD Bot Regression & Benchmark Report",
            "",
            f"**Bot Under Test**: `{self.bot_a}` vs `{self.bot_b}`",
            f"**Total Maps Tested**: {len(self.benchmarks)}",
            "",
            "| Map | Winner | End Turn | Bot A First Belt | Bot B First Belt | Bot A Crashes | Bot B Crashes | Status |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        ]

        total_wins_a = 0
        total_crashes_a = 0

        for b in self.benchmarks:
            status = "✅ PASS"
            if b.crashes_a > 0:
                status = "❌ CRASH FAIL"
            elif b.winner != self.bot_a and b.end_turn >= 1000:
                status = "⚠️ TIMEOUT/LOSS"

            if b.winner == self.bot_a:
                total_wins_a += 1
            total_crashes_a += b.crashes_a

            belt_a = str(b.first_connected_turn_a) if b.first_connected_turn_a is not None else "None"
            belt_b = str(b.first_connected_turn_b) if b.first_connected_turn_b is not None else "None"

            lines.append(
                f"| `{b.map_name}` | `{b.winner}` | {b.end_turn} | Turn {belt_a} | Turn {belt_b} | {b.crashes_a} | {b.crashes_b} | {status} |"
            )

        win_rate = (total_wins_a / len(self.benchmarks)) * 100 if self.benchmarks else 0.0
        lines.append("")
        lines.append("## Summary Verdict")
        lines.append(f"- **Overall Win Rate**: `{win_rate:.1f}%` ({total_wins_a}/{len(self.benchmarks)})")
        lines.append(f"- **Total Unit Crashes**: `{total_crashes_a}`")

        final_verdict = "PASS" if total_crashes_a == 0 and win_rate >= 50.0 else "FAIL"
        lines.append(f"- **CI/CD Build Verdict**: **`{final_verdict}`**")

        return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CI/CD Automated Regression Suite")
    parser.add_argument("--bot-a", default="bots/sprint", help="Path to bot A")
    parser.add_argument("--bot-b", default="bots/baron", help="Path to bot B")
    parser.add_argument("--num-maps", type=int, default=5, help="Number of maps to test")
    args = parser.parse_args()

    suite = RegressionSuite(args.bot_a, args.bot_b, max_maps=args.num_maps)
    suite.run_gauntlet()
    report = suite.generate_markdown_report()
    print("\n" + report)

    report_file = "test_results/ci_gauntlet/CI_REPORT.md"
    with open(report_file, "w") as f:
        f.write(report)
    print(f"\nReport written to {report_file}")
