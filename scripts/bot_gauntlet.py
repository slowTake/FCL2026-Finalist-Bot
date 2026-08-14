#!/usr/bin/env python3
"""Run a bot against starter on its live map pool. Pass only on a clean sweep.

Enforces the server's real per-turn time limit by default (--tle 10) — a
change that's cheap for one or two units can still be a real TLE regression
once it's added to a role that runs on many; a pass with TLE off (--tle 0)
doesn't catch that class of bug.
"""

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
    "resources": "Resources",
    "titanium_collected": "Titanium collected",
    "harvesters": "Harvesters",
    "titanium_stored": "Titanium stored",
    "coinflip": "Coinflip",
    "timeout": "Draw",
}


def live_map_pool(bot_dir: Path) -> list[str] | None:
    """MAP_ID_TO_NAME's values from the bot's own features.py, if it has one.

    This is the actual map pool the bot fingerprints/recognizes (see
    hivemind.py's _detect_and_store_map / KNOWN_MAPS) — narrower than every
    *.map26 file sitting in maps_dir, which accumulates maps the bot has no
    fingerprint table for and was never tuned against. Testing against those
    extra maps runs the bot in a materially different mode (no ORE_LOCATIONS/
    WALL_LOCATIONS, no fingerprinted enemy core) and produces regression
    signal that doesn't reflect real competitive performance — this script's
    own docstring used to say "every map in maps/, pass on 15/15," which only
    ever matched MAP_ID_TO_NAME's count; maps_dir just grew past it over time
    without this script being updated to filter back down.

    Searched recursively: bots may group shared components into a subdirectory
    (count/ keeps them in hivemind/). Missing it silently is expensive — the
    fallback quietly runs every map in maps/, which is three times the pool and
    exhausts the run's CPU budget partway through.

    Executed in an isolated namespace (not sys.modules) rather than imported,
    so gauntlet-ing multiple bots in one process can't cross-contaminate via
    Python's module cache — every bot's features.py shares the same module
    name. None if the bot has no features.py or no MAP_ID_TO_NAME, so callers
    can fall back to a full directory scan.
    """
    features_path = next(
        (p for p in sorted(bot_dir.rglob("features.py")) if "__pycache__" not in p.parts),
        None,
    )
    if features_path is None:
        print(
            f"Warning: no features.py under {bot_dir} — falling back to every map in "
            "maps/, which is a much larger and differently-shaped pool",
            file=sys.stderr,
        )
        return None
    ns: dict = {"__file__": str(features_path)}
    try:
        exec(compile(features_path.read_text(), str(features_path), "exec"), ns)
    except Exception as exc:
        print(f"Warning: could not read live map pool from {features_path}: {exc}", file=sys.stderr)
        return None
    mapping = ns.get("MAP_ID_TO_NAME")
    if not isinstance(mapping, dict) or not mapping:
        return None
    return sorted(mapping.values())


def resolve_maps(
    maps_dir: Path, override: list[str] | None, bot_dir: Path | None = None
) -> list[Path]:
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

    pool = live_map_pool(bot_dir) if bot_dir is not None else None
    if pool:
        maps = []
        missing = []
        for name in pool:
            path = maps_dir / f"{name}.map26"
            if path.exists():
                maps.append(path)
            else:
                missing.append(name)
        if missing:
            print(
                f"Warning: live map pool names not found in {maps_dir}: {missing}",
                file=sys.stderr,
            )
        return maps
    return sorted(maps_dir.glob("*.map26"))


def write_summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def write_github_output(wins: int, total: int, passed: bool, bot_name: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"wins={wins}\n")
            f.write(f"total={total}\n")
            f.write(f"score={wins}/{total}\n")
            f.write(f"passed={'true' if passed else 'false'}\n")
            f.write(f"bot={bot_name}\n")

    level = "notice" if passed else "error"
    verdict = "PASS" if passed else "FAIL"
    print(
        f"::{level} title=Bot Gauntlet ({bot_name})::"
        f"Won {wins}/{total} maps — {verdict}"
    )


def run_gauntlet(bot_name: str, map_names: list[str] | None, tle: int = 10) -> int:
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

    tle_label = "off" if tle == 0 else f"{tle}ms"
    print(f"Bot gauntlet: {bot_name} vs starter ({len(maps)} maps, seed={seed}, TLE={tle_label})")
    print()
    print(
        f"{'Map':<12} {'Result':<8} {'Condition':<24} {'Ally Ti':>8} "
        f"{'Enemy Ti':>9} {'Turn':>6}"
    )
    print("-" * 71)

    wins = 0
    rows: list[tuple[str, str, str, int, int, int]] = []

    for map_path in maps:
        replay_path = f"/tmp/gauntlet-{bot_name}-{map_path.stem}.replay26"
        result = run_game(
            str(candidate.resolve()),
            str(starter.resolve()),
            engine_root,
            str(map_path.resolve()),
            replay_path,
            seed,
            tle,
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
        rows.append(
            (map_path.stem, label, condition, ally_ti, enemy_ti, result["turns"])
        )
        print(
            f"{map_path.stem:<12} {label:<8} {condition:<24} {ally_ti:>8} "
            f"{enemy_ti:>9} {result['turns']:>6}"
        )

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
        "| Map | Result | Condition | Ally Ti | Enemy Ti | Turn |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for map_name, label, condition, ally_ti, enemy_ti, turns in rows:
        summary_lines.append(
            f"| {map_name} | {label} | {condition} | {ally_ti} | {enemy_ti} | {turns} |"
        )
    write_summary(summary_lines)
    write_github_output(wins, total, passed, bot_name)

    return 0 if passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a bot against starter on its live map pool. Pass only if all matches are won."
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
            "Optional map names to run (default: the bot's own "
            "features.py MAP_ID_TO_NAME pool, falling back to every "
            "*.map26 in maps/ if the bot has none)"
        ),
    )
    parser.add_argument(
        "--tle",
        type=int,
        default=10,
        help=(
            "Turn time limit in ms, matching the server's real enforcement "
            "(0 disables it). Defaults to 10 — a win on this gauntlet with "
            "TLE disabled says nothing about whether a change made some "
            "unit's per-turn cost too expensive; a change that's cheap for "
            "one or two units can still be a real regression once it's added "
            "to a role that runs on many."
        ),
    )
    args = parser.parse_args()
    raise SystemExit(run_gauntlet(args.bot, args.maps, args.tle))


if __name__ == "__main__":
    main()
