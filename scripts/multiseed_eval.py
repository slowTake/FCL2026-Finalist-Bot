#!/usr/bin/env python3
"""Run a bot vs starter on every map, 5 seeds each.

Same CLI shape as bot_gauntlet / extreme_gauntlet:

    python scripts/multiseed_eval.py --bot turtleHive
    python scripts/multiseed_eval.py --bot turtleHive --maps atoll vault
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from fcode.config import find_config
from fcode.fcode_engine import run_game

SEEDS_PER_MAP = 5

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


def run_multiseed(bot_name: str, map_names: list[str] | None) -> int:
    config, project_root = find_config()
    bots_dir = (project_root / config.bots_dir).resolve()
    maps_dir = (project_root / config.maps_dir).resolve()
    base_seed = config.seed
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

    seeds = [base_seed + i for i in range(SEEDS_PER_MAP)]
    total = len(maps) * SEEDS_PER_MAP
    print(
        f"Multiseed eval: {bot_name} vs starter "
        f"({len(maps)} maps x {SEEDS_PER_MAP} seeds = {total} matches, "
        f"seeds={seeds})",
        flush=True,
    )
    print(flush=True)
    print(
        f"{'Map':<12} {'Result':<8} {'Condition':<24} {'Ally Ti':>8} "
        f"{'Enemy Ti':>9} {'Turn':>6}",
        flush=True,
    )
    print("-" * 71, flush=True)

    wins = 0
    done = 0
    per_map: dict[str, list[str]] = defaultdict(list)

    for map_path in maps:
        for i, match_seed in enumerate(seeds, start=1):
            map_label = f"{map_path.stem}-{i}"
            print(
                f"  … {map_label} (seed {match_seed})  [{done + 1}/{total}]",
                flush=True,
            )
            replay_path = (
                f"/tmp/multiseed-{bot_name}-{map_path.stem}-s{match_seed}.replay26"
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
            done += 1

            ally_ti = result["a_titanium_collected"]
            enemy_ti = result["b_titanium_collected"]
            condition = CONDITION_LABELS.get(
                result["win_condition"], result["win_condition"]
            )
            label = "WIN" if won else "LOSS"
            per_map[map_path.stem].append("W" if won else "L")
            print(
                f"{map_label:<12} {label:<8} {condition:<24} {ally_ti:>8} "
                f"{enemy_ti:>9} {result['turns']:>6}  "
                f"({wins}/{done})",
                flush=True,
            )

    winrate = (wins / total) * 100 if total else 0.0
    print(flush=True)
    print(f"Total: {wins}/{total}", flush=True)
    print(f"Win rate: {winrate:.1f}%", flush=True)
    print(flush=True)
    print(f"=== Per map (one letter per seed {seeds}) ===", flush=True)
    for map_path in maps:
        letters = "".join(per_map[map_path.stem])
        print(f"  {map_path.stem:<12} {letters}", flush=True)

    return 0 if wins == total else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run a bot against starter on every map, 5 seeds each. "
            "Same CLI as bot_gauntlet."
        )
    )
    parser.add_argument(
        "--bot",
        required=True,
        help="Bot directory name under bots/ (e.g. turtleHive)",
    )
    parser.add_argument(
        "--maps",
        nargs="+",
        help="Optional map names to run (default: all *.map26 in maps/)",
    )
    args = parser.parse_args()
    raise SystemExit(run_multiseed(args.bot, args.maps))


if __name__ == "__main__":
    main()
