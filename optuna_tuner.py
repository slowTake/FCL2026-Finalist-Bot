#!/usr/bin/env python3
"""Optuna Hyperparameter Tuning Suite for `baron` RTS Bot.

Automates the optimization of economic, tactical, and combat policy thresholds
for the `baron` bot in the `fcode` RTS engine using Optuna and `fcv`.

Target module: `bots/baron/policy.py`

Usage:
    python3 optuna_tuner.py --n-trials 20 --baseline bots/earl
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

# Auto-reexecute inside repo's .venv if optuna is not found in system python
repo_root = Path(__file__).resolve().parent
venv_python = repo_root / ".venv" / "bin" / "python3"
if venv_python.exists() and sys.executable != str(venv_python):
    try:
        import optuna
    except ImportError:
        os.execv(str(venv_python), [str(venv_python)] + sys.argv)

import optuna

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("optuna_tuner")

REPO_ROOT = Path(__file__).resolve().parent
SOURCE_BOT_DIR = REPO_ROOT / "bots" / "baron"
DEFAULT_BASELINE_BOT = "bots/baron_hybrid"
DEFAULT_TIMEOUT = 720
DEFAULT_DB = f"sqlite:///{REPO_ROOT / 'baron_optuna.db'}"
DEFAULT_STUDY_NAME = "baron_policy_tuning_v3"

# Active 15-map competition ladder pool
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


def find_fcv_bin() -> str:
    """Locate the `fcv` executable, checking venv and PATH."""
    venv_fcv = REPO_ROOT / ".venv" / "bin" / "fcv"
    if venv_fcv.exists() and os.access(venv_fcv, os.X_OK):
        return str(venv_fcv)

    path_fcv = shutil.which("fcv")
    if path_fcv:
        return path_fcv

    logger.error("Could not locate `fcv` binary. Ensure fcv is installed in .venv or PATH.")
    sys.exit(1)


def update_policy_py_constants(policy_py_path: Path, params: dict[str, any]):
    """Dynamically overwrite key policy threshold constants in policy.py."""
    content = policy_py_path.read_text(encoding="utf-8")

    replacements = [
        (r'BUILDER_GROWTH_DELAY_TURNS\s*=\s*.*', f'BUILDER_GROWTH_DELAY_TURNS = {params["BUILDER_GROWTH_DELAY_TURNS"]}'),
        (r'MAX_GUARDIANS\s*(?::\s*[^=]+)?=\s*.*', f'MAX_GUARDIANS: int | None = {params["MAX_GUARDIANS"]}'),
        (r'WAVE_ATTACK_TI\s*(?::\s*[^=]+)?=\s*.*', f'WAVE_ATTACK_TI: int | None = {params["WAVE_ATTACK_TI"]}'),
        (r'GUARDIAN_LEASH_WIDE\s*=\s*.*', f'GUARDIAN_LEASH_WIDE = {params["GUARDIAN_LEASH_WIDE"]}'),
        (r'GUARDIAN_LEASH_TIGHT\s*=\s*.*', f'GUARDIAN_LEASH_TIGHT = {params["GUARDIAN_LEASH_TIGHT"]}'),
        (r'DENY_ORE_CHEBYSHEV\s*(?::\s*[^=]+)?=\s*.*', f'DENY_ORE_CHEBYSHEV = {params["DENY_ORE_CHEBYSHEV"]}'),
        (r'DENY_ORE_MIN_ALLY_CHEBYSHEV\s*(?::\s*[^=]+)?=\s*.*', f'DENY_ORE_MIN_ALLY_CHEBYSHEV = {params["DENY_ORE_MIN_ALLY_CHEBYSHEV"]}'),
        (r'HARVESTER_VERIFY_INTERVAL_TURNS\s*=\s*.*', f'HARVESTER_VERIFY_INTERVAL_TURNS = {params["HARVESTER_VERIFY_INTERVAL_TURNS"]}'),
        (r'DENY_CUT_COOLDOWN\s*=\s*.*', f'DENY_CUT_COOLDOWN = {params["DENY_CUT_COOLDOWN"]}'),
        (r'CORE_PUSH_MIN_TI\s*(?::\s*[^=]+)?=\s*.*', f'CORE_PUSH_MIN_TI = {params["CORE_PUSH_MIN_TI"]}'),
        (r'^\s*\(2000,\s*\d+\),\s*$', f'    (2000, {params["BUILDERS_AT_2000"]}),'),
        (r'^\s*\(1000,\s*\d+\),\s*$', f'    (1000, {params["BUILDERS_AT_1000"]}),'),
    ]

    for pattern, replacement in replacements:
        content, count = re.subn(pattern, replacement, content, flags=re.MULTILINE)
        if count == 0:
            logger.warning(f"Pattern '{pattern}' was not found in {policy_py_path.name}")

    policy_py_path.write_text(content, encoding="utf-8")


def parse_win_rate_from_batch_json(out_dir: Path) -> float | None:
    """Extract win rate percentage from fcv's batch.json report."""
    batch_json_path = out_dir / "batch.json"
    if not batch_json_path.exists():
        return None

    try:
        data = json.loads(batch_json_path.read_text(encoding="utf-8"))
        games = data.get("games", 0)
        wins = data.get("wins", 0)
        win_rate = data.get("winRate")

        if win_rate is not None:
            return float(win_rate) * 100.0
        elif games > 0:
            return (wins / games) * 100.0
    except Exception as e:
        logger.warning(f"Failed to parse {batch_json_path}: {e}")

    return None


def parse_win_rate_from_stdout(stdout: str) -> float | None:
    """Fallback parser to extract win rate percentage from fcv stdout."""
    match = re.search(r'score\s+\d+/\d+\s+=\s+([\d.]+)\%', stdout)
    if match:
        return float(match.group(1))
    return None


def create_objective(
    baseline_bot: str,
    fcv_bin: str,
    map_pool: list[str],
    sample_size: int,
    jobs: int,
    keep_artifacts: bool,
):
    """Factory function for the Optuna objective function."""

    def objective(trial: optuna.Trial) -> float:
        # Select a random subset of maps (e.g. 5) from the competitive map pool for each trial
        if len(map_pool) > sample_size:
            trial_maps = random.sample(map_pool, sample_size)
        else:
            trial_maps = list(map_pool)

        # High-impact policy hyperparameters to suggest
        params = {
            "BUILDER_GROWTH_DELAY_TURNS": trial.suggest_int("BUILDER_GROWTH_DELAY_TURNS", 20, 160, step=20),
            "MAX_GUARDIANS": trial.suggest_int("MAX_GUARDIANS", 1, 4),
            "WAVE_ATTACK_TI": trial.suggest_int("WAVE_ATTACK_TI", 800, 2500, step=100),
            "GUARDIAN_LEASH_WIDE": trial.suggest_int("GUARDIAN_LEASH_WIDE", 4, 8),
            "GUARDIAN_LEASH_TIGHT": trial.suggest_int("GUARDIAN_LEASH_TIGHT", 3, 5),
            "DENY_ORE_CHEBYSHEV": trial.suggest_int("DENY_ORE_CHEBYSHEV", 4, 10),
            "DENY_ORE_MIN_ALLY_CHEBYSHEV": trial.suggest_int("DENY_ORE_MIN_ALLY_CHEBYSHEV", 4, 10),
            "HARVESTER_VERIFY_INTERVAL_TURNS": trial.suggest_int("HARVESTER_VERIFY_INTERVAL_TURNS", 20, 80, step=10),
            "DENY_CUT_COOLDOWN": trial.suggest_int("DENY_CUT_COOLDOWN", 10, 50, step=5),
            "CORE_PUSH_MIN_TI": trial.suggest_int("CORE_PUSH_MIN_TI", 200, 800, step=50),
            "BUILDERS_AT_1000": trial.suggest_int("BUILDERS_AT_1000", 6, 9),
            "BUILDERS_AT_2000": trial.suggest_int("BUILDERS_AT_2000", 9, 12),
        }

        trial_bot_dir = REPO_ROOT / "bots" / f"tmp_baron_trial_{trial.number}"
        out_dir = REPO_ROOT / "test_results" / f"optuna_trial_{trial.number}"

        try:
            # 1. Trial Sandbox Generation
            if trial_bot_dir.exists():
                shutil.rmtree(trial_bot_dir)
            shutil.copytree(SOURCE_BOT_DIR, trial_bot_dir)

            policy_py_path = trial_bot_dir / "policy.py"
            update_policy_py_constants(policy_py_path, params)

            # 2. Execution via fcv run
            cmd = [
                fcv_bin,
                "run",
                str(trial_bot_dir),
                baseline_bot,
                "--maps",
                *trial_maps,
                "--seeds",
                "1",
                "--jobs",
                str(jobs),
                "--out",
                str(out_dir),
            ]

            logger.info(
                f"[Trial {trial.number}] Gauntlet vs {baseline_bot} | "
                f"Maps (5/15 random): {', '.join(trial_maps)} | "
                f"GROWTH_DELAY={params['BUILDER_GROWTH_DELAY_TURNS']}, MAX_G={params['MAX_GUARDIANS']}, "
                f"WAVE_TI={params['WAVE_ATTACK_TI']}, LEASH_WIDE={params['GUARDIAN_LEASH_WIDE']}"
            )

            proc = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=180,
            )

            if proc.returncode != 0:
                logger.warning(
                    f"[Trial {trial.number}] fcv run returned exit code {proc.returncode}. "
                    f"Stderr: {proc.stderr.strip()[:200]}"
                )

            # 3. Parse win rate fitness score
            win_rate = parse_win_rate_from_batch_json(out_dir)
            if win_rate is None:
                win_rate = parse_win_rate_from_stdout(proc.stdout)

            if win_rate is None:
                logger.error(
                    f"[Trial {trial.number}] Could not parse win rate from output. Defaulting score to 0.0%"
                )
                win_rate = 0.0

            logger.info(f"[Trial {trial.number}] Result Win Rate: {win_rate:.1f}%")
            return win_rate

        except subprocess.TimeoutExpired:
            logger.error(f"[Trial {trial.number}] Timed out after 180s. Score: 0.0%")
            return 0.0
        except Exception as e:
            logger.error(f"[Trial {trial.number}] Unexpected error: {e}. Score: 0.0%")
            return 0.0
        finally:
            # 4. Cleanup
            if trial_bot_dir.exists():
                try:
                    shutil.rmtree(trial_bot_dir)
                except Exception as e:
                    logger.warning(f"Could not remove {trial_bot_dir}: {e}")

            if not keep_artifacts and out_dir.exists():
                try:
                    shutil.rmtree(out_dir)
                except Exception as e:
                    logger.warning(f"Could not remove {out_dir}: {e}")

    return objective


def print_best_callback(study: optuna.Study, trial: optuna.FrozenTrial):
    """Callback to print the best parameters found so far after each trial."""
    logger.info(f"--- Trial #{trial.number} Summary ---")
    logger.info(f"  Trial Score (Win Rate): {trial.value:.1f}%")
    if study.best_trial.number == trial.number:
        logger.info(f"  🌟 NEW BEST WIN RATE: {study.best_value:.1f}%!")
    logger.info(f"  Current Best: Trial #{study.best_trial.number} ({study.best_value:.1f}%)")
    logger.info(f"  Current Best Params: {json.dumps(study.best_params, indent=2)}")
    logger.info("-" * 40)


def main():
    parser = argparse.ArgumentParser(
        description="Optuna Hyperparameter Tuning Suite for `baron` RTS bot"
    )
    parser.add_argument(
        "--n-trials",
        type=int,
        default=20,
        help="Number of Optuna optimization trials to execute (default: 20)",
    )
    parser.add_argument(
        "--baseline",
        type=str,
        default=DEFAULT_BASELINE_BOT,
        help="Path to baseline competitor bot (default: bots/earl)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=DEFAULT_DB,
        help="SQLite database URL for Optuna persistence (default: sqlite:///baron_optuna.db)",
    )
    parser.add_argument(
        "--study-name",
        type=str,
        default=DEFAULT_STUDY_NAME,
        help="Optuna study name (default: baron_policy_tuning_v3)",
    )
    parser.add_argument(
        "--maps",
        nargs="+",
        default=None,
        help="List of maps to evaluate, or omitted to sample 5 random competitive maps per trial",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=5,
        help="Number of random maps to sample per trial from competitive pool (default: 5)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=4,
        help="Parallel jobs for fcv run (default: 4)",
    )
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Keep temporary trial replay/report folders instead of deleting them",
    )

    args = parser.parse_args()
    fcv_bin = find_fcv_bin()

    map_pool = args.maps if args.maps else COMPETITIVE_MAPS

    logger.info("==================================================")
    logger.info(" OPTUNA HYPERPARAMETER TUNER FOR BARON BOT")
    logger.info("==================================================")
    logger.info(f"  Target Bot:       bots/baron (policy.py)")
    logger.info(f"  Baseline Bot:     {args.baseline}")
    logger.info(f"  Trials:           {args.n_trials}")
    logger.info(f"  Map Pool (15):    {', '.join(map_pool)}")
    logger.info(f"  Maps per Trial:   {args.sample_size} (randomly sampled per trial)")
    logger.info(f"  fcv Binary:       {fcv_bin}")
    logger.info(f"  Database Storage: {args.db}")
    logger.info(f"  Study Name:       {args.study_name}")
    logger.info("==================================================")

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    study = optuna.create_study(
        study_name=args.study_name,
        storage=args.db,
        direction="maximize",
        load_if_exists=True,
    )

    obj_fn = create_objective(
        baseline_bot=args.baseline,
        fcv_bin=fcv_bin,
        map_pool=map_pool,
        sample_size=args.sample_size,
        jobs=args.jobs,
        keep_artifacts=args.keep_artifacts,
    )

    study.optimize(obj_fn, n_trials=args.n_trials, callbacks=[print_best_callback])

    logger.info("==================================================")
    logger.info(" OPTIMIZATION COMPLETE")
    logger.info("==================================================")
    logger.info(f"  Best Trial:     #{study.best_trial.number}")
    logger.info(f"  Best Win Rate:  {study.best_value:.1f}%")
    logger.info("  Best Parameters:")
    for k, v in study.best_params.items():
        logger.info(f"    - {k}: {v}")
    logger.info("==================================================")


if __name__ == "__main__":
    main()
