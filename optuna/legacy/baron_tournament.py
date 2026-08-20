#!/usr/bin/env python3
"""Reproducible Multi-Opponent Optuna tuning for the Baron RTS bot.

Each trial is copied into its own bot directory, evaluated across a fixed map,
seed, and opponent suite, then discarded. The source bot is never modified.

Target module: `bots/baron/policy.py`

Example:
    python3 optuna_tournament.py --n-trials 40 --opponents bots/earl bots/starter
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


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python3"

# Prefer the repository environment, but do not loop if it is already active.
if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    try:
        import optuna  # noqa: F401
    except ImportError:
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

import optuna


logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
LOGGER = logging.getLogger("baron_optuna")

SOURCE_BOT_DIR = REPO_ROOT / "bots" / "baron"
DEFAULT_OPPONENTS = ["bots/earl"]
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
DEFAULT_SEEDS = [1, 2]
DEFAULT_TIMEOUT = 360
DEFAULT_DB = f"sqlite:///{Path(__file__).resolve().parent / 'baron_optuna.db'}"
DEFAULT_STUDY_NAME = "baron_policy_tuning_v3"


class TrialExecutionError(RuntimeError):
    """Raised when fcv fails to produce parseable output for a trial."""


def find_fcv() -> str:
    """Locate the executable path for the fcv CLI runner."""
    venv_fcv = REPO_ROOT / ".venv" / "bin" / "fcv"
    if venv_fcv.exists() and os.access(venv_fcv, os.X_OK):
        return str(venv_fcv)
    path_fcv = shutil.which("fcv")
    if path_fcv:
        return path_fcv
    raise RuntimeError("fcv executable not found in .venv or system PATH.")


def update_policy_py_constants(policy_py: Path, params: dict[str, Any]) -> None:
    """Apply one internally consistent policy profile to policy.py in a copied Baron bot."""
    content = policy_py.read_text(encoding="utf-8")

    replacements = [
        (r'BUILDER_GROWTH_DELAY_TURNS\s*=\s*.*', f'BUILDER_GROWTH_DELAY_TURNS = {params["BUILDER_GROWTH_DELAY_TURNS"]}'),
        (r'MAX_GUARDIANS\s*(?::\s*[^=]+)?=\s*.*', f'MAX_GUARDIANS: int | None = {params["MAX_GUARDIANS"]}'),
        (r'WAVE_ATTACK_TURN\s*(?::\s*[^=]+)?=\s*.*', f'WAVE_ATTACK_TURN: int | None = {params["WAVE_ATTACK_TURN"]}'),
        (r'WAVE_ATTACK_TI\s*(?::\s*[^=]+)?=\s*.*', f'WAVE_ATTACK_TI: int | None = {params["WAVE_ATTACK_TI"]}'),
        (r'GUARDIAN_LEASH_WIDE\s*=\s*.*', f'GUARDIAN_LEASH_WIDE = {params["GUARDIAN_LEASH_WIDE"]}'),
        (r'GUARDIAN_LEASH_TIGHT\s*=\s*.*', f'GUARDIAN_LEASH_TIGHT = {params["GUARDIAN_LEASH_TIGHT"]}'),
        (r'DENY_ORE_CHEBYSHEV\s*(?::\s*[^=]+)?=\s*.*', f'DENY_ORE_CHEBYSHEV = {params["DENY_ORE_CHEBYSHEV"]}'),
        (r'DENY_ORE_MIN_ALLY_CHEBYSHEV\s*(?::\s*[^=]+)?=\s*.*', f'DENY_ORE_MIN_ALLY_CHEBYSHEV = {params["DENY_ORE_MIN_ALLY_CHEBYSHEV"]}'),
        (r'HARVESTER_VERIFY_INTERVAL_TURNS\s*=\s*.*', f'HARVESTER_VERIFY_INTERVAL_TURNS = {params["HARVESTER_VERIFY_INTERVAL_TURNS"]}'),
        (r'DENY_CUT_COOLDOWN\s*=\s*.*', f'DENY_CUT_COOLDOWN = {params["DENY_CUT_COOLDOWN"]}'),
        (r'CORE_PUSH_MIN_TI\s*(?::\s*[^=]+)?=\s*.*', f'CORE_PUSH_MIN_TI = {params["CORE_PUSH_MIN_TI"]}'),
        (r'DEF_SENTINEL\s*=\s*.*', f'DEF_SENTINEL = {"True" if params["DEF_SENTINEL"] else "False"}'),
        (r'TURRET_THREAT_CHEBYSHEV\s*=\s*.*', f'TURRET_THREAT_CHEBYSHEV = {params["TURRET_THREAT_CHEBYSHEV"]}'),
        (r'TURRET_MEMORY_TURNS\s*=\s*.*', f'TURRET_MEMORY_TURNS = {params["TURRET_MEMORY_TURNS"]}'),
        (r'BUILDER_HOME_TURNS\s*=\s*.*', f'BUILDER_HOME_TURNS = {params["BUILDER_HOME_TURNS"]}'),
        (r'ORE_DEADLOCK_TURNS\s*=\s*.*', f'ORE_DEADLOCK_TURNS = {params["ORE_DEADLOCK_TURNS"]}'),
        (r'HARASS_DEADLOCK_TURNS\s*=\s*.*', f'HARASS_DEADLOCK_TURNS = {params["HARASS_DEADLOCK_TURNS"]}'),
        (r'^\s*\(2000,\s*\d+\),\s*$', f'    (2000, {params["BUILDERS_AT_2000"]}),'),
        (r'^\s*\(1000,\s*\d+\),\s*$', f'    (1000, {params["BUILDERS_AT_1000"]}),'),
    ]

    for pattern, replacement in replacements:
        content, count = re.subn(pattern, replacement, content, flags=re.MULTILINE)
        if count == 0:
            LOGGER.warning(f"Pattern '{pattern}' was not found in {policy_py.name}")

    policy_py.write_text(content, encoding="utf-8")


def suggest_policy(trial: optuna.Trial) -> dict[str, Any]:
    """Sample a compact, economically plausible Baron policy profile."""
    builders_at_1000 = trial.suggest_int("BUILDERS_AT_1000", 6, 8)
    max_builders = trial.suggest_int("MAX_BUILDERS", 8, 12)
    builders_at_2000 = trial.suggest_int("BUILDERS_AT_2000", 6, 12)
    def_sentinel = trial.suggest_categorical("DEF_SENTINEL", [True, False])

    return {
        "MAX_BUILDERS_EARLY": trial.suggest_int("MAX_BUILDERS_EARLY", 4, 6),
        "MAX_BUILDERS": max_builders,
        "BUILDER_GROWTH_DELAY_TURNS": trial.suggest_int("BUILDER_GROWTH_DELAY_TURNS", 20, 160, step=20),
        "WAVE_ATTACK_TURN": trial.suggest_int("WAVE_ATTACK_TURN", 0, 900, step=300),
        "WAVE_ATTACK_TI": trial.suggest_int("WAVE_ATTACK_TI", 1000, 2500, step=100),
        "MAX_GUARDIANS": trial.suggest_int("MAX_GUARDIANS", 1, 4),
        "GUARDIAN_LEASH_WIDE": trial.suggest_int("GUARDIAN_LEASH_WIDE", 4, 8),
        "GUARDIAN_LEASH_TIGHT": trial.suggest_int("GUARDIAN_LEASH_TIGHT", 3, 5),
        "BUILDERS_AT_1000": builders_at_1000,
        "BUILDERS_AT_2000": max(builders_at_2000, builders_at_1000),
        "DENY_CUT_COOLDOWN": trial.suggest_int("DENY_CUT_COOLDOWN", 10, 50, step=5),
        "DENY_ORE_CHEBYSHEV": trial.suggest_int("DENY_ORE_CHEBYSHEV", 4, 10),
        "DENY_ORE_MIN_ALLY_CHEBYSHEV": trial.suggest_int("DENY_ORE_MIN_ALLY_CHEBYSHEV", 4, 10),
        "HARVESTER_VERIFY_INTERVAL_TURNS": trial.suggest_int("HARVESTER_VERIFY_INTERVAL_TURNS", 20, 80, step=10),
        "DEF_SENTINEL": def_sentinel,
        "TURRET_THREAT_CHEBYSHEV": trial.suggest_int("TURRET_THREAT_CHEBYSHEV", 5, 7),
        "TURRET_MEMORY_TURNS": trial.suggest_int("TURRET_MEMORY_TURNS", 10, 30, step=5),
        "BUILDER_HOME_TURNS": trial.suggest_int("BUILDER_HOME_TURNS", 0, 30, step=5),
        "ORE_DEADLOCK_TURNS": trial.suggest_int("ORE_DEADLOCK_TURNS", 15, 45, step=5),
        "HARASS_DEADLOCK_TURNS": trial.suggest_int("HARASS_DEADLOCK_TURNS", 15, 45, step=5),
        "CORE_PUSH_MIN_TI": trial.suggest_int("CORE_PUSH_MIN_TI", 200, 800, step=50),
    }


def parse_win_rate(report_dir: Path, stdout: str) -> float | None:
    """Read fcv's JSON result, falling back to its human-readable score line."""
    report = report_dir / "batch.json"
    if report.exists():
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
            if data.get("winRate") is not None:
                value = float(data["winRate"])
                return value * 100 if value <= 1 else value
            games, wins = int(data.get("games", 0)), int(data.get("wins", 0))
            if games:
                return wins * 100 / games
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            LOGGER.warning("Could not parse %s: %s", report, error)
    match = re.search(r"score\s+\d+/\d+\s+=\s+([\d.]+)%", stdout)
    return float(match.group(1)) if match else None


def log_trial_card(
    study: optuna.Study,
    trial: optuna.Trial,
    score: float,
    opponents: list[str],
    scores: list[float],
    trial_maps: list[str],
    params: dict[str, Any],
):
    """Print a clean, beautifully formatted terminal card for each trial."""
    is_new_best = study.best_trial.number == trial.number

    LOGGER.info("-" * 60)
    best_marker = " 🌟 NEW BEST TRIAL!" if is_new_best else ""
    LOGGER.info(f"🏆 Trial #{trial.number} Complete | Overall Win Rate: {score:.2f}%{best_marker}")
    LOGGER.info("-" * 60)
    LOGGER.info("  ⚔️  Opponent Scores:")
    for opp, sc in zip(opponents, scores):
        opp_slug = Path(opp).name
        LOGGER.info(f"     - {opp_slug:<22}: {sc:>6.2f}%")

    LOGGER.info(f"  🗺️  Maps Sampled (5): {', '.join(trial_maps)}")

    if is_new_best:
        LOGGER.info(f"  🌟 Current Best Study Value: Trial #{study.best_trial.number} ({study.best_value:.2f}%)")

    LOGGER.info("  ⚙️  Key Policy Parameters:")
    LOGGER.info(f"     - Economy:   GROWTH_DELAY={params['BUILDER_GROWTH_DELAY_TURNS']}t | BUILDERS_1000={params['BUILDERS_AT_1000']} | BUILDERS_2000={params['BUILDERS_AT_2000']}")
    LOGGER.info(f"     - Guardians: MAX_GUARDIANS={params['MAX_GUARDIANS']} | LEASH_WIDE={params['GUARDIAN_LEASH_WIDE']} | LEASH_TIGHT={params['GUARDIAN_LEASH_TIGHT']}")
    LOGGER.info(f"     - Offense:   WAVE_TI={params['WAVE_ATTACK_TI']} Ti | WAVE_TURN={params['WAVE_ATTACK_TURN']}t | CORE_PUSH={params['CORE_PUSH_MIN_TI']} Ti")
    LOGGER.info(f"     - Denial:    ORE_CHEB={params['DENY_ORE_CHEBYSHEV']} | CUT_CD={params['DENY_CUT_COOLDOWN']}t | MIN_ALLY_CHEB={params['DENY_ORE_MIN_ALLY_CHEBYSHEV']}")
    LOGGER.info(f"     - Defense:   DEF_SENTINEL={params['DEF_SENTINEL']} | THREAT_CHEB={params['TURRET_THREAT_CHEBYSHEV']} | HOME_TURNS={params['BUILDER_HOME_TURNS']}t")
    LOGGER.info("-" * 60)


def create_objective(
    fcv_bin: str,
    opponents: list[str],
    map_pool: list[str],
    sample_size: int,
    seeds: list[int],
    jobs: int,
    timeout: int,
    keep_artifacts: bool,
    study: optuna.Study,
    target_bot: Path = SOURCE_BOT_DIR,
):
    def objective(trial: optuna.Trial) -> float:
        params = suggest_policy(trial)
        trial_bot = REPO_ROOT / "bots" / f"tmp_baron_trial_{trial.number}"
        trial_out = REPO_ROOT / "test_results" / f"optuna_trial_{trial.number}"
        scores: list[float] = []
        failures: list[str] = []
        trial.set_user_attr("policy", params)

        # Randomly sample sample_size (5) maps per trial from the map pool
        if len(map_pool) > sample_size:
            trial_maps = random.sample(map_pool, sample_size)
        else:
            trial_maps = list(map_pool)

        try:
            shutil.rmtree(trial_bot, ignore_errors=True)
            shutil.rmtree(trial_out, ignore_errors=True)
            shutil.copytree(target_bot, trial_bot)
            update_policy_py_constants(trial_bot / "policy.py", params)

            for opponent in opponents:
                opponent_slug = Path(opponent).name
                report_dir = trial_out / opponent_slug
                cmd = [
                    fcv_bin, "run", str(trial_bot), opponent, "--maps", *trial_maps,
                    "--seeds", ",".join(str(seed) for seed in seeds), "--jobs", str(jobs),
                    "--out", str(report_dir),
                ]
                try:
                    proc = subprocess.run(
                        cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout
                    )
                    score = parse_win_rate(report_dir, proc.stdout)
                    if score is None:
                        failures.append(f"{opponent}: exit={proc.returncode}; {proc.stderr[-500:]}")
                        score = 0.0
                    trial.set_user_attr(f"stdout_{opponent_slug}", proc.stdout[-2000:])
                    trial.set_user_attr(f"stderr_{opponent_slug}", proc.stderr[-2000:])
                except subprocess.TimeoutExpired:
                    LOGGER.warning(f"Trial {trial.number} match against {opponent_slug} timed out after {timeout}s")
                    score = 0.0
                    failures.append(f"{opponent}: timed out after {timeout}s")

                scores.append(score)

            trial.set_user_attr("scores_by_opponent", dict(zip(opponents, scores)))
            trial.set_user_attr("failures", failures)
            score = sum(scores) / len(scores) if scores else 0.0

            log_trial_card(study, trial, score, opponents, scores, trial_maps, params)
            return score
        finally:
            shutil.rmtree(trial_bot, ignore_errors=True)
            if not keep_artifacts:
                shutil.rmtree(trial_out, ignore_errors=True)

    return objective


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-bot", default="bots/baron", help="Target bot directory relative to workspace root (default: bots/baron).")
    parser.add_argument("--n-trials", type=int, default=40, help="Number of trials to execute.")
    parser.add_argument(
        "--opponents", nargs="+", default=DEFAULT_OPPONENTS,
        help="Opponent bot directories relative to workspace root.",
    )
    parser.add_argument(
        "--maps", nargs="+", default=None,
        help="List of maps to evaluate, or omitted to sample 5 random competitive maps per trial",
    )
    parser.add_argument(
        "--sample-size", type=int, default=5,
        help="Number of random maps to sample per trial from competitive pool (default: 5)",
    )
    parser.add_argument(
        "--seeds", nargs="+", type=int, default=DEFAULT_SEEDS,
        help="Random seeds passed to fcv run.",
    )
    parser.add_argument("--jobs", type=int, default=4, help="Parallel worker threads for fcv.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"Per-command execution timeout in seconds (default: {DEFAULT_TIMEOUT}).")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite database URL for Optuna.")
    parser.add_argument("--study-name", default=DEFAULT_STUDY_NAME, help="Optuna study name.")
    parser.add_argument(
        "--keep-artifacts", action="store_true", help="Preserve temporary fcv trial reports.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    fcv_bin = find_fcv()
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    map_pool = args.maps if args.maps else COMPETITIVE_MAPS

    LOGGER.info("==================================================")
    LOGGER.info(" CODEX MULTI-OPPONENT OPTUNA TUNER FOR BARON")
    LOGGER.info("==================================================")
    LOGGER.info("  Target Bot:     %s (policy.py)", args.target_bot)
    LOGGER.info("  Opponents:      %s", ", ".join(args.opponents))
    LOGGER.info("  Map Pool (15):  %s", ", ".join(map_pool))
    LOGGER.info("  Maps per Trial: %d (randomly sampled per trial)", args.sample_size)
    LOGGER.info("  Seeds:          %s", ", ".join(str(s) for s in args.seeds))
    LOGGER.info("  Database:       %s", args.db)
    LOGGER.info("  Study Name:     %s", args.study_name)
    LOGGER.info("==================================================")

    study = optuna.create_study(
        study_name=args.study_name,
        storage=args.db,
        direction="maximize",
        load_if_exists=True,
    )
    objective = create_objective(
        fcv_bin=fcv_bin,
        opponents=args.opponents,
        map_pool=map_pool,
        sample_size=args.sample_size,
        seeds=args.seeds,
        jobs=args.jobs,
        timeout=args.timeout,
        keep_artifacts=args.keep_artifacts,
        study=study,
        target_bot=REPO_ROOT / args.target_bot,
    )
    study.optimize(objective, n_trials=args.n_trials)

    best = study.best_trial
    LOGGER.info("==================================================")
    LOGGER.info(" 🏆 TOURNAMENT OPTIMIZATION COMPLETE")
    LOGGER.info("==================================================")
    LOGGER.info("  🥇 Best Trial:           #%d", best.number)
    LOGGER.info("  📈 Best Avg Win Rate:    %.2f%%", best.value)
    LOGGER.info("  📋 Best Policy Parameters:")
    for key, value in sorted(best.params.items()):
        LOGGER.info("     - %-32s: %s", key, value)
    LOGGER.info("==================================================")


if __name__ == "__main__":
    main()
