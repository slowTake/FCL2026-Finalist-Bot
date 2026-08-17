#!/usr/bin/env python3
"""Multi-Opponent Gauntlet Tournament Evaluator for fcode RTS Bots.

Runs a specified target bot (e.g. bots/grand_knight) against benchmark bots
(bots/count, bots/baron, bots/starter) sampling 5 random competitive maps per bot.

Example:
    python3 multi_bot_gauntlet.py --target-bot bots/grand_knight
    python3 multi_bot_gauntlet.py --target-bot bots/grand_knight --opponents bots/count bots/baron bots/starter
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python3"

# Ensure repository virtualenv is active
if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
LOGGER = logging.getLogger("multi_bot_gauntlet")

DEFAULT_TARGET_BOT = "bots/grand_knight"
DEFAULT_OPPONENTS = ["bots/count", "bots/baron", "bots/starter"]
DEFAULT_SEEDS = [1, 2]
DEFAULT_TIMEOUT = 360
DEFAULT_SAMPLE_SIZE = 5

COMPETITIVE_MAPS = [
    "antler",
    "archipelago",
    "auroraveil",
    "drakkarfjord",
    "drumlin",
    "fjordgate",
    "frostgate",
    "glacierkeep",
    "icefloe",
    "midgard",
    "nordkap",
    "ragnarok",
    "royale",
    "valkyrie",
    "yulerune",
]


def find_fcv() -> str:
    """Locate fcv binary in venv or PATH."""
    venv_fcv = REPO_ROOT / ".venv" / "bin" / "fcv"
    if venv_fcv.exists():
        return str(venv_fcv)
    path_fcv = shutil.which("fcv")
    if path_fcv:
        return path_fcv
    raise RuntimeError("fcv executable not found in .venv/bin/fcv or system PATH.")


def validate_bot_directory(bot_path_str: str) -> Path:
    """Verify that a bot directory exists and contains a valid main.py entrypoint."""
    p = REPO_ROOT / bot_path_str
    if not p.exists():
        raise FileNotFoundError(f"Bot directory '{bot_path_str}' does not exist.")
    if not (p / "main.py").exists():
        raise FileNotFoundError(
            f"Bot directory '{bot_path_str}' does not contain 'main.py'. "
            f"Available valid bots with main.py: {get_valid_bots()}"
        )
    return p


def get_valid_bots() -> list[str]:
    """Scan bots/ directory and return list of valid bots containing main.py."""
    bots_dir = REPO_ROOT / "bots"
    valid = []
    if bots_dir.exists():
        for sub in sorted(bots_dir.iterdir()):
            if sub.is_dir() and (sub / "main.py").exists():
                valid.append(f"bots/{sub.name}")
    return valid


def parse_win_rate(report_dir: Path, stdout: str) -> dict[str, Any]:
    """Parse fcv JSON report and return win count, game count, and win rate."""
    report = report_dir / "batch.json"
    if report.exists():
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
            games = int(data.get("games", 0))
            wins = int(data.get("wins", 0))
            win_rate = (wins / games * 100.0) if games > 0 else 0.0
            return {"games": games, "wins": wins, "win_rate": win_rate}
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            LOGGER.warning("Could not parse %s: %s", report, error)

    match = re.search(r"score\s+(\d+)/(\d+)\s+=\s+([\d.]+)%", stdout)
    if match:
        wins, games, wr = int(match.group(1)), int(match.group(2)), float(match.group(3))
        return {"games": games, "wins": wins, "win_rate": wr}

    return {"games": 0, "wins": 0, "win_rate": 0.0}


def run_matchup(
    fcv_bin: str,
    target_bot: str,
    opponent: str,
    maps: list[str],
    seeds: list[int],
    jobs: int,
    timeout: int,
    out_dir: Path,
) -> dict[str, Any]:
    """Run fcv evaluation between target_bot and a single opponent."""
    opponent_slug = Path(opponent).name
    report_dir = out_dir / opponent_slug

    cmd = [
        fcv_bin,
        "run",
        target_bot,
        opponent,
        "--maps",
        *maps,
        "--seeds",
        ",".join(str(s) for s in seeds),
        "--jobs",
        str(jobs),
        "--out",
        str(report_dir),
    ]

    try:
        proc = subprocess.run(
            cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout
        )
        res = parse_win_rate(report_dir, proc.stdout)
        res["opponent"] = opponent_slug
        res["maps"] = maps
        res["stdout"] = proc.stdout[-1000:]
        res["stderr"] = proc.stderr[-1000:]
        return res
    except subprocess.TimeoutExpired:
        LOGGER.warning("Matchup against %s timed out after %ds", opponent_slug, timeout)
        return {"opponent": opponent_slug, "maps": maps, "games": 0, "wins": 0, "win_rate": 0.0, "error": "timeout"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-bot",
        default=DEFAULT_TARGET_BOT,
        help=f"Target bot directory to evaluate (default: {DEFAULT_TARGET_BOT}).",
    )
    parser.add_argument(
        "--opponents",
        nargs="+",
        default=DEFAULT_OPPONENTS,
        help=f"Opponent bot directories (default: {' '.join(DEFAULT_OPPONENTS)}).",
    )
    parser.add_argument(
        "--maps",
        nargs="+",
        default=None,
        help="Specific list of maps to evaluate, or omitted to sample 5 random competitive maps per bot.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help=f"Number of random maps to sample per opponent from competitive pool (default: {DEFAULT_SAMPLE_SIZE}).",
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        type=int,
        default=DEFAULT_SEEDS,
        help="Random seeds for fcv run (default: 1 2).",
    )
    parser.add_argument("--jobs", type=int, default=4, help="Parallel worker threads for fcv.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Per-matchup execution timeout in seconds (default: {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--out",
        default="test_results/multi_bot_gauntlet",
        help="Output directory for gauntlet match reports.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    fcv_bin = find_fcv()
    out_dir = REPO_ROOT / args.out

    # Pre-flight Bot Validation
    validate_bot_directory(args.target_bot)
    valid_opponents = []
    for opp in args.opponents:
        try:
            validate_bot_directory(opp)
            valid_opponents.append(opp)
        except FileNotFoundError as err:
            LOGGER.error("Skipping invalid opponent '%s': %s", opp, err)

    if not valid_opponents:
        LOGGER.error("No valid opponent bot directories provided! Valid options: %s", get_valid_bots())
        sys.exit(1)

    LOGGER.info("==================================================")
    LOGGER.info(" ⚔️  MULTI-BOT TOURNAMENT GAUNTLET")
    LOGGER.info("==================================================")
    LOGGER.info("  Target Bot:      %s", args.target_bot)
    LOGGER.info("  Opponents:       %s", ", ".join(valid_opponents))
    LOGGER.info("  Map Pool (15):   %s", ", ".join(COMPETITIVE_MAPS))
    LOGGER.info("  Maps per Bot:    %d (randomly sampled per opponent)", args.sample_size)
    LOGGER.info("  Seeds:           %s", ", ".join(str(s) for s in args.seeds))
    LOGGER.info("==================================================")

    total_wins = 0
    total_games = 0
    results: list[dict[str, Any]] = []

    for opponent in valid_opponents:
        if args.maps:
            sampled_maps = args.maps
        else:
            sampled_maps = random.sample(COMPETITIVE_MAPS, min(args.sample_size, len(COMPETITIVE_MAPS)))

        LOGGER.info("Running matchup: %s vs %s on [%s] ...", args.target_bot, opponent, ", ".join(sampled_maps))
        res = run_matchup(
            fcv_bin=fcv_bin,
            target_bot=args.target_bot,
            opponent=opponent,
            maps=sampled_maps,
            seeds=args.seeds,
            jobs=args.jobs,
            timeout=args.timeout,
            out_dir=out_dir,
        )
        results.append(res)
        total_wins += res["wins"]
        total_games += res["games"]
        LOGGER.info(
            " -> %s: %d/%d wins (%.2f%%) on maps: %s",
            res["opponent"],
            res["wins"],
            res["games"],
            res["win_rate"],
            ", ".join(res["maps"]),
        )

    overall_win_rate = (total_wins / total_games * 100.0) if total_games > 0 else 0.0

    LOGGER.info("==================================================")
    LOGGER.info(" 🏆 MULTI-BOT TOURNAMENT GAUNTLET SUMMARY")
    LOGGER.info("==================================================")
    LOGGER.info("  Target Bot:        %s", args.target_bot)
    LOGGER.info("  Overall Score:     %d / %d", total_wins, total_games)
    LOGGER.info("  Overall Win Rate:  %.2f%%", overall_win_rate)
    LOGGER.info("--------------------------------------------------")
    LOGGER.info("  Breakdown by Opponent:")
    for r in results:
        LOGGER.info(
            "   • %-18s: %2d / %2d wins  (%6.2f%%) | Maps: %s",
            r["opponent"],
            r["wins"],
            r["games"],
            r["win_rate"],
            ", ".join(r["maps"]),
        )
    LOGGER.info("==================================================")


if __name__ == "__main__":
    main()
