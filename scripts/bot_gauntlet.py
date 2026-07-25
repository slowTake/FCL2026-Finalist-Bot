#!/usr/bin/env python3
"""Run a bot against starter on every map in maps/. Pass only on 15/15 wins."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from fcode.config import find_config
from fcode.fcode_engine import run_game

CONDITION_LABELS = {
    "core_destroyed": "Core destroyed",
    "resigned": "Resigned",
    "resources": "Resources (tiebreak)",
    "titanium_collected": "Titanium collected (tiebreak)",
    "harvesters": "Harvesters (tiebreak)",
    "titanium_stored": "Titanium stored (tiebreak)",
    "coinflip": "Coinflip (tiebreak)",
    "timeout": "Draw",
}


def resolve_maps(maps_dir: Path, override: list[str] | None) -> list[Path]:
    if override:
        maps = []
        for name in override:
            path = maps_dir / name
            if not path.suffix:
                path = path.with_suffix(".map26")
            if not path.exists():
                raise FileNotFoundError(f"Map not found: {path}")
            maps.append(path)
        return maps
    return sorted(maps_dir.glob("*.map26"))


def write_summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run_gauntlet(bot_name: str, map_names: list[str] | None) -> int:
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

    maps = resolve_maps(maps_dir, map_names)
    if not maps:
        print(f"Error: no maps found in {maps_dir}", file=sys.stderr)
        return 1

    print(f"Bot gauntlet: {bot_name} vs starter ({len(maps)} maps, seed={seed})")
    print()
    print(f"{'Map':<12} {'Result':<8} {'Condition':<32} {'Turn':>6}")
    print("-" * 62)

    wins = 0
    rows: list[tuple[str, str, str, int]] = []

    for map_path in maps:
        replay_path = f"/tmp/gauntlet-{bot_name}-{map_path.stem}.replay26"
        result = run_game(
            str(candidate.resolve()),
            str(starter.resolve()),
            engine_root,
            str(map_path.resolve()),
            replay_path,
            seed,
            0,
        )

        won = result["winner"] == "A"
        if won:
            wins += 1

        condition = CONDITION_LABELS.get(
            result["win_condition"], result["win_condition"]
        )
        label = "WIN" if won else "LOSS"
        rows.append((map_path.stem, label, condition, result["turns"]))
        print(f"{map_path.stem:<12} {label:<8} {condition:<32} {result['turns']:>6}")

    total = len(maps)
    passed = wins == total
    verdict = "PASS" if passed else "FAIL"
    print()
    print(f"Score: {wins}/{total} — {verdict}")

    summary_lines = [
        f"## Bot gauntlet: `{bot_name}` vs `starter`",
        "",
        f"**Score: {wins}/{total} — {verdict}** (seed={seed})",
        "",
        "| Map | Result | Condition | Turn |",
        "| --- | --- | --- | ---: |",
    ]
    for map_name, label, condition, turns in rows:
        summary_lines.append(f"| {map_name} | {label} | {condition} | {turns} |")
    write_summary(summary_lines)

    return 0 if passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a bot against starter on every map. Pass only if all matches are won."
    )
    parser.add_argument(
        "--bot",
        required=True,
        help="Bot directory name under bots/ (e.g. turtle)",
    )
    parser.add_argument(
        "--maps",
        nargs="+",
        help="Optional map names to run (default: all *.map26 in maps/)",
    )
    args = parser.parse_args()
    raise SystemExit(run_gauntlet(args.bot, args.maps))


if __name__ == "__main__":
    main()
