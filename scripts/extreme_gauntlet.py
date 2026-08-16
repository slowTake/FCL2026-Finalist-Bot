#!/usr/bin/env python3
"""Run a bot against starter on its live map pool, 5 times each."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from fcode.config import find_config
from fcode.fcode_engine import run_game

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
from live_map_pool import resolve_maps  # noqa: E402

RUNS_PER_MAP = 5

CONDITION_LABELS = {
    "core_destroyed": "Core destroyed",
    "resigned": "Resigned",
    "resources": "Resources",
    "titanium_collected": "Titanium collected",
    "harvesters": "Harvesters",
    "titanium_stored": "Titanium stored",
    "coinflip": "Coinflip",
    "timeout": "Draw",
}


def write_summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def write_github_output(
    wins: int, total: int, winrate: float, passed: bool, bot_name: str
) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"wins={wins}\n")
            f.write(f"total={total}\n")
            f.write(f"score={wins}/{total}\n")
            f.write(f"winrate={winrate:.1f}\n")
            f.write(f"passed={'true' if passed else 'false'}\n")
            f.write(f"bot={bot_name}\n")

    level = "notice" if passed else "error"
    verdict = "PASS" if passed else "FAIL"
    print(
        f"::{level} title=Extreme Gauntlet ({bot_name})::"
        f"Won {wins}/{total} matches ({winrate:.1f}%) — {verdict}"
    )


def run_extreme_gauntlet(bot_name: str, map_names: list[str] | None) -> int:
    config, project_root = find_config()
    bots_dir = (project_root / config.bots_dir).resolve()
    maps_dir = (project_root / config.maps_dir).resolve()
    seed = config.seed
    engine_root = str(Path(__import__("fcode").__file__).resolve().parent)

    candidate = bots_dir / bot_name / "main.py"
    starter = bots_dir / "starter" / "main.py"

    if not candidate.is_file():
        print(f"Error: bot not found at {candidate}", file=sys.stderr)
        return 1
    if not starter.is_file():
        print(f"Error: starter bot not found at {starter}", file=sys.stderr)
        return 1

    maps = resolve_maps(maps_dir, map_names, bots_dir / bot_name)
    if not maps:
        print(f"Error: no maps found in {maps_dir}", file=sys.stderr)
        return 1

    total_matches = len(maps) * RUNS_PER_MAP
    print(
        f"Extreme gauntlet: {bot_name} vs starter "
        f"({len(maps)} maps x {RUNS_PER_MAP} runs = {total_matches} matches, "
        f"base seed={seed})"
    )
    print()
    print(
        f"{'Map':<12} {'Result':<8} {'Condition':<24} {'Ally Ti':>8} "
        f"{'Enemy Ti':>9} {'Turn':>6}"
    )
    print("-" * 71)

    wins = 0
    rows: list[tuple[str, str, str, int, int, int]] = []

    for map_path in maps:
        for run in range(1, RUNS_PER_MAP + 1):
            match_seed = seed + run - 1
            replay_path = (
                f"/tmp/extreme-{bot_name}-{map_path.stem}-r{run}.replay26"
            )
            result = run_game(
                str(candidate.resolve()),
                str(starter.resolve()),
                engine_root,
                str(map_path.resolve()),
                replay_path,
                match_seed,
                0,
            )

            won = result["winner"] == "A"
            if won:
                wins += 1

            ally_ti = result["a_titanium_collected"]
            enemy_ti = result["b_titanium_collected"]

            condition = CONDITION_LABELS.get(
                result["win_condition"], result["win_condition"]
            )
            label = "WIN" if won else "LOSS"
            map_label = map_path.stem if RUNS_PER_MAP == 1 else f"{map_path.stem}-{run}"
            rows.append((map_label, label, condition, ally_ti, enemy_ti, result["turns"]))
            print(
                f"{map_label:<12} {label:<8} {condition:<24} {ally_ti:>8} "
                f"{enemy_ti:>9} {result['turns']:>6}"
            )

    winrate = (wins / total_matches) * 100 if total_matches else 0.0
    passed = wins == total_matches
    print()
    print(f"Total: {wins}/{total_matches}")
    print(f"Win rate: {winrate:.1f}%")

    summary_lines = [
        f"## Extreme gauntlet: `{bot_name}` vs `starter`",
        "",
        f"**Total: {wins}/{total_matches} — {winrate:.1f}% win rate** "
        f"(base seed={seed}, {RUNS_PER_MAP} runs per map)",
        "",
        "| Map | Result | Condition | Ally Ti | Enemy Ti | Turn |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for map_label, label, condition, ally_ti, enemy_ti, turns in rows:
        summary_lines.append(
            f"| {map_label} | {label} | {condition} | {ally_ti} | {enemy_ti} | {turns} |"
        )
    write_summary(summary_lines)
    write_github_output(wins, total_matches, winrate, passed, bot_name)

    return 0 if passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run a bot against starter on its live map pool, 5 times each. "
            "Pass only if all matches are won."
        )
    )
    parser.add_argument(
        "--bot",
        required=True,
        help="Bot directory name under bots/ (e.g. turtle)",
    )
    parser.add_argument(
        "--maps",
        nargs="+",
        help=(
            "Optional map names to run (default: the bot's own features.py "
            "MAP_ID_TO_NAME pool, falling back to every *.map26 in maps/ if "
            "the bot has none)"
        ),
    )
    args = parser.parse_args()
    raise SystemExit(run_extreme_gauntlet(args.bot, args.maps))


if __name__ == "__main__":
    main()
