#!/usr/bin/env python3
"""Mirror-match regression check: a bot against itself on the live map pool.

Unlike bot_gauntlet.py (vs starter, pass = win every map), a mirror match has no
natural win/loss signal to grade — both sides are the same bot, so a "loss" on one
side is a "win" on the other. What this catches instead:

  1. Crashes — a Python exception inside the bot, surfaced via a Traceback in the
     replay's captured botOutput.stdout even when the match itself completes (the
     engine catches per-unit exceptions and keeps going, so a crash does NOT show
     up as a nonzero run_game exit or a bad win_condition — only in stdout).
  2. Side imbalance — both players are the same code, so anything but a roughly
     even outcome on a symmetric map (survivors, buildings, titanium_collected on
     both sides) points at a real bug, not matchup luck. Reported, not graded pass
     /fail on its own; the default maps aren't all fully symmetric, so a threshold
     would need per-map tuning this doesn't attempt yet.

Defaults to 4 of the 15 live-rotation maps from bots/baron/features.py
(MAP_ID_TO_NAME) — a size/shape spread, not the first 4 alphabetically.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fcode.config import find_config
from fcode.fcode_engine import run_game

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import florent_replay_pb2 as pb  # noqa: E402

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

# antler (14x18, small) / atoll (18x18, walled centre) / heart (28x20, large) /
# saga (24x24, open) — a spread of sizes and layouts, not four similar maps.
DEFAULT_LIVE_MAPS = ["antler", "atoll", "heart", "saga"]

# Any of these appearing in a unit's captured stdout means the bot's own code
# raised — the engine swallows per-unit exceptions and keeps the match running,
# so this is the only signal a crash leaves behind.
_CRASH_MARKERS = ("Traceback (most recent call last)",)


def _scan_replay_for_crashes(replay_path: str) -> list[str]:
    """Return captured stdout snippets containing a crash marker, deduped."""
    with open(replay_path, "rb") as f:
        replay = pb.Replay()
        replay.ParseFromString(f.read())

    found: list[str] = []
    seen: set[str] = set()
    for turn in replay.turns:
        for update in turn.updates:
            if update.WhichOneof("kind") != "botOutput":
                continue
            out = update.botOutput.stdout
            if not out or not any(marker in out for marker in _CRASH_MARKERS):
                continue
            snippet = out.strip()[:400]
            if snippet in seen:
                continue
            seen.add(snippet)
            found.append(snippet)
    return found


def run_mirror_gauntlet(bot_name: str, map_names: list[str]) -> int:
    config, project_root = find_config()
    bots_dir = (project_root / config.bots_dir).resolve()
    maps_dir = (project_root / config.maps_dir).resolve()
    seed = config.seed
    engine_root = str(Path(__import__("fcode").__file__).resolve().parent)

    candidate = bots_dir / bot_name / "main.py"
    if not candidate.is_file():
        print(f"Error: bot not found at {candidate}", file=sys.stderr)
        return 1

    maps: list[Path] = []
    for name in map_names:
        path = maps_dir / name
        if not path.suffix:
            path = path.with_suffix(".map26")
        if not path.exists():
            print(f"Error: map not found: {path}", file=sys.stderr)
            return 1
        maps.append(path)

    print(f"Mirror gauntlet: {bot_name} vs itself ({len(maps)} maps, seed={seed})")
    print()
    print(
        f"{'Map':<12} {'Condition':<20} {'A Ti':>8} {'B Ti':>8} "
        f"{'A units':>8} {'B units':>8} {'Turn':>6}  Crash"
    )
    print("-" * 84)

    any_crash = False
    rows: list[tuple] = []

    for map_path in maps:
        replay_path = f"/tmp/mirror-{bot_name}-{map_path.stem}.replay26"
        result = run_game(
            str(candidate.resolve()),
            str(candidate.resolve()),
            engine_root,
            str(map_path.resolve()),
            replay_path,
            seed,
            0,
        )

        crashes = _scan_replay_for_crashes(replay_path)
        if crashes:
            any_crash = True

        condition = CONDITION_LABELS.get(
            result["win_condition"], result["win_condition"]
        )
        crash_flag = f"YES ({len(crashes)})" if crashes else "-"
        rows.append(
            (
                map_path.stem,
                condition,
                result["a_titanium_collected"],
                result["b_titanium_collected"],
                result["a_units"],
                result["b_units"],
                result["turns"],
                crashes,
            )
        )
        print(
            f"{map_path.stem:<12} {condition:<20} "
            f"{result['a_titanium_collected']:>8} {result['b_titanium_collected']:>8} "
            f"{result['a_units']:>8} {result['b_units']:>8} {result['turns']:>6}  "
            f"{crash_flag}"
        )

    print()
    if any_crash:
        print("Crash detail:")
        for map_name, *_rest, crashes in rows:
            for snippet in crashes:
                print(f"  [{map_name}] {snippet}")
        print()
        print(f"Score: CRASH — {bot_name} raised on at least one map")
    else:
        print(f"Score: CLEAN — no crashes across {len(maps)} maps")

    return 1 if any_crash else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a bot against itself (mirror match) on the live map pool. "
        "Reports crashes and per-side stats; does not grade win/loss."
    )
    parser.add_argument(
        "--bot",
        required=True,
        help="Bot directory name under bots/ (e.g. baron)",
    )
    parser.add_argument(
        "--maps",
        nargs="+",
        default=DEFAULT_LIVE_MAPS,
        help=f"Map names to run (default: {' '.join(DEFAULT_LIVE_MAPS)})",
    )
    args = parser.parse_args()
    raise SystemExit(run_mirror_gauntlet(args.bot, args.maps))


if __name__ == "__main__":
    main()
