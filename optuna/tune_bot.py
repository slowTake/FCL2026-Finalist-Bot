#!/usr/bin/env python3
"""Optuna hyperparameter tuning for any bot under bots/ — not just count.
See optuna/README.md first — it has the one-time venv/fcv setup this script
assumes, plus how this fits together with optuna/discover_params.py.

Fully isolated from the real bot: the target bot directory is never edited,
and never has tuning code of its own. Every run, this script copies it into
two throwaway directories under optuna/ — <bot>_trial (gets a tuning.py + a
TUNE_PARAMS hook appended to main.py, rebuilt fresh every invocation so it
always matches current source) and <bot>_baseline (a plain, frozen copy, no
injection at all, so it's structurally deaf to TUNE_PARAMS even though env
vars are process-wide) — plays games between the two copies, and leaves the
results in the Optuna study. bots/<bot> itself never changes and never
contains anything tuning-related; `git status` after a run should show
nothing under bots/.

Setup (once per bot):
    python3 optuna/tune_bot.py --bot count --freeze-baseline

    Copies bots/count -> optuna/count_baseline, untouched. This is the
    fixed opponent every trial in a study plays against, so it's *not*
    rebuilt automatically like <bot>_trial is — re-run with --force
    deliberately, after promoting a winning trial's params into bots/count
    for real, otherwise a study silently keeps comparing against a stale
    target.

The parameter search space is a separate JSON file, not baked into this
script — see optuna/param_space_count.json for the format, and
optuna/discover_params.py for finding candidates in any bot.

Usage (from anywhere — paths below are all repo-root-relative regardless of cwd):
    python3 optuna/tune_bot.py --n-trials 30 --jobs 4                      # bot defaults to count
    python3 optuna/tune_bot.py --bot baron --param-space optuna/param_space_baron.json --freeze-baseline
    python3 optuna/tune_bot.py --n-trials 30 --maps sprint duel strait     # faster loop
    python3 optuna/tune_bot.py --show-best                                 # print the current best without running trials
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TUNING_DIR = REPO_ROOT / "optuna"
sys.path.insert(0, str(REPO_ROOT / "scripts"))          # live_map_pool
sys.path.insert(0, str(REPO_ROOT / "scripts" / "ladder"))  # submission_cache, milestone_gauntlet
from live_map_pool import live_map_pool  # noqa: E402
from submission_cache import list_submissions, ensure_local, slug  # noqa: E402
from milestone_gauntlet import (  # noqa: E402
    MILESTONE_VERSIONS, MILESTONE_WEIGHTS, DEFAULT_MILESTONE_WEIGHT, MILESTONES_DIR,
)

DEFAULT_PARAM_SPACE_PATH = TUNING_DIR / "param_space_count.json"
# Appended to <bot>_baseline to make the tournament's default opponent
# roster — a frozen copy of itself (so a genuine change is being measured,
# not noise from the bot moving under you mid-comparison), plus baron and
# bandit as real, differently-built bots. Override with
# --tournament-opponents when tuning one of these two, since facing
# yourself twice isn't useful.
DEFAULT_TOURNAMENT_EXTRA_OPPONENTS = ["bots/baron", "bots/bandit"]

# Auto-reexecute inside the repo venv if optuna isn't on the system python —
# same convenience legacy/baron_tuner.py already has.
venv_python = REPO_ROOT / ".venv" / "bin" / "python3"
if venv_python.exists() and sys.executable != str(venv_python):
    try:
        import optuna  # noqa: F401
    except ImportError:
        os.execv(str(venv_python), [str(venv_python)] + sys.argv)

import optuna  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S"
)
logger = logging.getLogger("tune_bot")


def load_param_space(path: Path) -> dict[str, tuple[str, float, float, float]]:
    """{"module.CONST": [kind, low, high, step]} JSON -> the dict shape the
    rest of this script uses. See optuna/discover_params.py to find
    candidates for a bot you haven't built a param-space file for yet —
    that script's output isn't in this format (it's the full, unfiltered
    candidate list); picking entries out of it into bounds here is the
    curation step, by design."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: tuple(v) for k, v in data.items()}


def _suggest(trial: optuna.Trial, name: str, spec: tuple) -> object:
    kind, low, high, step = spec
    if kind == "int":
        return trial.suggest_int(name, int(low), int(high), step=int(step))
    if kind == "float":
        return trial.suggest_float(name, float(low), float(high), step=step)
    raise ValueError(f"param_space[{name!r}]: unknown kind {kind!r}")


def current_param_values(target_bot: Path, param_space: dict) -> dict[str, object]:
    """Read each param_space constant's live value straight from the target
    bot's own source today (not <bot>_trial/<bot>_baseline — those may not
    even exist yet). Read-only: imports the real modules to getattr the
    constant, never writes to them.

    Adds every immediate subdirectory of the bot to sys.path — not just
    hivemind/roles (count's own names) — since a bot's internal layout is
    otherwise unknown to this script; harmless for a flat bot like
    baron/bandit, and matches count's own main.py sys.path shim for one
    that splits into subdirectories.
    """
    candidates = [target_bot] + [p for p in target_bot.iterdir() if p.is_dir() and p.name != "__pycache__"]
    for p in candidates:
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    values: dict[str, object] = {}
    for dotted in param_space:
        modname, _, attr = dotted.rpartition(".")
        mod = importlib.import_module(modname)
        values[dotted] = getattr(mod, attr)
    return values


# --- Baseline setup -----------------------------------------------------


def freeze_baseline(target_bot: Path, baseline_bot: Path, force: bool) -> None:
    if baseline_bot.exists():
        if not force:
            logger.info(
                f"{baseline_bot} already exists — leaving it alone "
                f"(pass --force to refreeze from the current {target_bot})."
            )
            return
        shutil.rmtree(baseline_bot)

    shutil.copytree(
        target_bot, baseline_bot, ignore=shutil.ignore_patterns("__pycache__")
    )
    logger.info(f"Froze {baseline_bot} from {target_bot} (plain copy, no injection).")


# --- Trial bot setup ------------------------------------------------------
# The only place TUNE_PARAMS-awareness exists at all. The target bot itself
# never imports or contains this — it's written into a disposable copy,
# fresh every run, so a study always tunes what the bot actually is right
# now.

_TUNING_MODULE = '''"""Injected by optuna/tune_bot.py — not part of the real bot.

Reads TUNE_PARAMS (a path to a JSON file of {"module.CONST": value}) and
overwrites the named module constants in this process. apply() is called
once, appended to the end of this copy's main.py, so it runs before the
game engine ever calls into a unit. Uses importlib rather than a
sys.modules lookup so it works even for a module only reachable via one of
the bot's own function-level lazy imports that hasn't loaded yet at this
point in a fresh process.
"""

from __future__ import annotations

import importlib
import json
import os

PARAMS_PATH = os.environ.get("TUNE_PARAMS")


def apply() -> dict[str, tuple[object, object]]:
    if not PARAMS_PATH:
        return {}
    with open(PARAMS_PATH, encoding="utf-8") as f:
        overrides = json.load(f)
    applied: dict[str, tuple[object, object]] = {}
    for dotted, value in overrides.items():
        modname, _, attr = dotted.rpartition(".")
        if not modname:
            raise ValueError(
                f"TUNE_PARAMS: {dotted!r} needs a module.CONST form"
            )
        mod = importlib.import_module(modname)
        if not hasattr(mod, attr):
            raise AttributeError(f"TUNE_PARAMS: {modname} has no {attr!r}")
        old = getattr(mod, attr)
        setattr(mod, attr, value)
        applied[dotted] = (old, value)
    return applied
'''

_TRIAL_MAIN_APPENDIX = """

# --- Appended by optuna/tune_bot.py — not part of the real bot's main.py ---
import tuning as _optuna_tuning
_optuna_tuning.apply()
"""


def prepare_trial_bot(target_bot: Path, trial_bot: Path) -> None:
    """(Re)build <bot>_trial from the current target bot source.

    Not frozen like the baseline — rebuilt unconditionally on every run, so
    staleness here can't silently defeat the point of tuning. The target
    bot is only ever read, never written.
    """
    if trial_bot.exists():
        shutil.rmtree(trial_bot)
    shutil.copytree(target_bot, trial_bot, ignore=shutil.ignore_patterns("__pycache__"))
    (trial_bot / "tuning.py").write_text(_TUNING_MODULE, encoding="utf-8")
    main_py = trial_bot / "main.py"
    main_py.write_text(main_py.read_text(encoding="utf-8") + _TRIAL_MAIN_APPENDIX, encoding="utf-8")
    logger.info(f"Rebuilt {trial_bot} from {target_bot} (tuning hook injected into the copy only).")


# --- Running one trial ---------------------------------------------------


def find_fcv_bin() -> str:
    venv_fcv = REPO_ROOT / ".venv" / "bin" / "fcv"
    if venv_fcv.exists() and os.access(venv_fcv, os.X_OK):
        return str(venv_fcv)
    path_fcv = shutil.which("fcv")
    if path_fcv:
        return path_fcv
    logger.error("Could not locate `fcv`. Install florent-code-visualiser first.")
    sys.exit(1)


def score_batch(batch_json: Path, trial_bot: Path) -> tuple[float, float, int]:
    """Mean per-game score, raw win rate, and game count, from fcv's batch.json.

    Score is a smooth proxy, not raw win/loss: 1.0 for a win, 0.0 for a loss
    (0.5 split on an unresolved draw), plus a bounded tanh nudge from the Ti
    delivered margin — the game's own primary tiebreak metric
    (a_titanium_collected/b_titanium_collected). The nudge is capped at ±0.4,
    so it can refine ordering between two same-outcome trials but can never
    flip a real win above a real loss. Optuna's TPE sampler needs a gradient
    to climb; raw win/loss over a handful of maps is a step function that
    gives it almost none — see bots/count/docs/mistakes.md's note on
    gauntlet noise for why comparing single runs isn't reliable at all,
    which this doesn't fix, only softens (still combine with a fixed map
    set, not resampled per trial — see --maps below).
    """
    if not batch_json.exists():
        return 0.0, 0.0, 0
    data = json.loads(batch_json.read_text(encoding="utf-8"))
    scores: list[float] = []
    wins = 0
    n = 0
    for g in data.get("games_detail", []):
        if not g.get("ok"):
            continue
        n += 1
        stats = g.get("stats") or {}
        # bot_a in this record is whichever path fcv put on side A for *this*
        # game (it plays every map/seed both un-swapped and swapped), so read
        # "ours" off which literal path matches trial_bot, not off "a"/"b".
        trial_is_a = Path(g["bot_a"]).resolve() == trial_bot.resolve()
        our_ti = stats.get("a_titanium_collected" if trial_is_a else "b_titanium_collected", 0)
        their_ti = stats.get("b_titanium_collected" if trial_is_a else "a_titanium_collected", 0)
        our_side = "A" if trial_is_a else "B"
        won = g.get("winner") == our_side
        if won:
            wins += 1
        margin = our_ti - their_ti
        nudge = 0.4 * math.tanh(margin / 2000.0)
        scores.append((1.0 if won else 0.0) + nudge)
    if n == 0:
        return 0.0, 0.0, 0
    return sum(scores) / n, wins / n, n


def create_objective(
    fcv_bin: str, trial_bot: Path, baseline_bot: Path, param_space: dict,
    maps: list[str], seeds: list[int], jobs: int, keep: bool,
):
    def objective(trial: optuna.Trial) -> float:
        params = {name: _suggest(trial, name, spec) for name, spec in param_space.items()}

        params_path = Path(tempfile.mkstemp(suffix=".json", prefix=f"trial{trial.number}_")[1])
        params_path.write_text(json.dumps(params), encoding="utf-8")
        out_dir = REPO_ROOT / "test_results" / f"optuna_{trial_bot.name}_{trial.number}"

        env = {**os.environ, "TUNE_PARAMS": str(params_path)}
        cmd = [
            fcv_bin, "run", str(trial_bot), str(baseline_bot),
            "--maps", *maps, "--seeds", *[str(s) for s in seeds],
            "--jobs", str(jobs), "--out", str(out_dir),
        ]
        logger.info(f"[trial {trial.number}] {params}")

        try:
            proc = subprocess.run(
                cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                timeout=60 * len(maps) * len(seeds) + 120, env=env,
            )
            if proc.returncode != 0:
                logger.warning(f"[trial {trial.number}] fcv exit {proc.returncode}: "
                                f"{proc.stderr.strip()[-300:]}")
            score, win_rate, n = score_batch(out_dir / "batch.json", trial_bot)
            trial.set_user_attr("win_rate", win_rate)
            trial.set_user_attr("games", n)
            logger.info(f"[trial {trial.number}] score={score:.3f} win_rate={win_rate:.1%} "
                        f"over {n} games")
            return score
        except subprocess.TimeoutExpired:
            logger.error(f"[trial {trial.number}] timed out")
            return 0.0
        finally:
            params_path.unlink(missing_ok=True)
            if not keep and out_dir.exists():
                shutil.rmtree(out_dir, ignore_errors=True)

    return objective


# --- Weighted milestone objective: anti-overfitting alternative ----------
# The baseline-only objective above answers "did this trial beat a frozen
# copy of itself" — a real, gradient-friendly signal, but a trial can climb
# it by trading wins (lose ground against an old milestone, gain the same
# amount against the current one) and still score well, since a frozen
# self-copy has no opinion about *which* opponent you're stronger against.
# This objective plays every milestone instead of one baseline, and weighs
# wins/losses by scripts/ladder/milestone_gauntlet.py's MILESTONE_WEIGHTS — a win
# against the current frontier (bots/count's own v118) counts for more than
# a win against an ancient one (v1), and a LOSS against an old, previously-
# solved milestone is penalized more than a loss against the current one,
# specifically to punish exactly the "traded wins, called it even" pattern
# --compare-tuned surfaced. Costs #opponents times as many games per trial
# as the baseline objective — budget --n-trials/--maps accordingly.


def _win_loss_counts(batch_json: Path, trial_bot: Path) -> tuple[int, int, int]:
    _, win_rate, n = score_batch(batch_json, trial_bot)
    wins = round(win_rate * n)
    return wins, n - wins, n


def create_weighted_milestone_objective(
    fcv_bin: str, trial_bot: Path, milestone_bots: dict[int, Path], weights: dict[int, float],
    param_space: dict, maps: list[str], seeds: list[int], jobs: int, keep: bool, loss_penalty_base: float,
):
    def objective(trial: optuna.Trial) -> float:
        params = {name: _suggest(trial, name, spec) for name, spec in param_space.items()}

        params_path = Path(tempfile.mkstemp(suffix=".json", prefix=f"trial{trial.number}_")[1])
        params_path.write_text(json.dumps(params), encoding="utf-8")
        env = {**os.environ, "TUNE_PARAMS": str(params_path)}
        logger.info(f"[trial {trial.number}] {params}")

        total = 0.0
        total_wins = total_games = 0
        per_milestone: dict[str, str] = {}
        try:
            for v, opp_dir in milestone_bots.items():
                weight = weights.get(v, DEFAULT_MILESTONE_WEIGHT)
                out_dir = REPO_ROOT / "test_results" / f"optuna_{trial_bot.name}_{trial.number}_v{v}"
                cmd = [
                    fcv_bin, "run", str(trial_bot), str(opp_dir),
                    "--maps", *maps, "--seeds", *[str(s) for s in seeds],
                    "--jobs", str(jobs), "--out", str(out_dir),
                ]
                try:
                    proc = subprocess.run(
                        cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                        timeout=60 * len(maps) * len(seeds) + 120, env=env,
                    )
                    if proc.returncode != 0:
                        logger.warning(f"[trial {trial.number}] vs v{v} fcv exit {proc.returncode}: "
                                        f"{proc.stderr.strip()[-300:]}")
                    wins, losses, n = _win_loss_counts(out_dir / "batch.json", trial_bot)
                finally:
                    if not keep and out_dir.exists():
                        shutil.rmtree(out_dir, ignore_errors=True)

                contribution = wins * weight - losses * (loss_penalty_base / weight)
                total += contribution
                total_wins += wins
                total_games += n
                per_milestone[f"v{v}"] = f"{wins}-{losses}"
                logger.info(f"[trial {trial.number}]   vs v{v} (w={weight}): {wins}-{losses} "
                            f"-> {contribution:+.2f}")

            win_rate = (total_wins / total_games) if total_games else 0.0
            trial.set_user_attr("win_rate", win_rate)
            trial.set_user_attr("games", total_games)
            trial.set_user_attr("per_milestone", per_milestone)
            logger.info(f"[trial {trial.number}] weighted_score={total:.3f} "
                        f"win_rate={win_rate:.1%} over {total_games} games")
            return total
        finally:
            params_path.unlink(missing_ok=True)

    return objective


# --- Tournament: competitive-rules sanity check vs real opponents --------
# The optimization loop above only ever measures trial vs a frozen copy of
# itself — good for climbing a gradient, but beating your own clone doesn't
# prove you're stronger against a genuinely different playstyle. This plays
# <bot>_trial against real opponents (frozen self, baron, bandit by
# default) on a handful of maps sampled independently per opponent from the
# target bot's own live competitive pool — the same shape as the repo's
# multi_bot_gauntlet.py, just scoped to this specific opponent suite.


def _resolve_bot_dir(spec: str) -> Path:
    p = Path(spec)
    if not p.is_absolute():
        p = REPO_ROOT / spec
    if not (p / "main.py").exists():
        raise FileNotFoundError(f"{p} has no main.py")
    return p


def run_tournament(
    fcv_bin: str,
    trial_bot: Path,
    live_pool_bot: Path,
    opponents: list[str],
    sample_size: int,
    maps_override: list[str] | None,
    seeds: list[int],
    jobs: int,
    out_dir: Path,
    env_extra: dict[str, str],
) -> None:
    pool = live_map_pool(live_pool_bot)
    if not pool:
        logger.error(f"Could not resolve {live_pool_bot}'s live map pool — pass --maps explicitly.")
        sys.exit(1)

    resolved: list[Path] = []
    for spec in opponents:
        try:
            resolved.append(_resolve_bot_dir(spec))
        except FileNotFoundError as exc:
            logger.error(f"Skipping opponent {spec!r}: {exc}")
    if not resolved:
        logger.error("No valid opponents to play — check --tournament-opponents.")
        sys.exit(1)

    env = {**os.environ, **env_extra}
    logger.info(f"Tournament: {trial_bot.name} vs {[p.name for p in resolved]}")

    rows: list[tuple[str, int, int, float, list[str]]] = []
    for opp in resolved:
        maps = maps_override or random.sample(pool, min(sample_size, len(pool)))
        opp_out = out_dir / opp.name
        cmd = [
            fcv_bin, "run", str(trial_bot), str(opp),
            "--maps", *maps, "--seeds", *[str(s) for s in seeds],
            "--jobs", str(jobs), "--out", str(opp_out),
        ]
        logger.info(f"  vs {opp.name} on [{', '.join(maps)}] ...")
        try:
            proc = subprocess.run(
                cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                timeout=60 * len(maps) * len(seeds) + 120, env=env,
            )
            if proc.returncode != 0:
                logger.warning(f"  fcv exit {proc.returncode}: {proc.stderr.strip()[-300:]}")
        except subprocess.TimeoutExpired:
            logger.error(f"  vs {opp.name} timed out")
        _, win_rate, n = score_batch(opp_out / "batch.json", trial_bot)
        wins = round(win_rate * n)
        rows.append((opp.name, wins, n, win_rate, maps))
        logger.info(f"  -> {wins}/{n} ({win_rate:.1%}) vs {opp.name}")

    total_wins = sum(r[1] for r in rows)
    total_games = sum(r[2] for r in rows)
    overall = (total_wins / total_games) if total_games else 0.0

    print()
    print("=" * 64)
    print(" TOURNAMENT — competitive-rules sanity check vs real opponents")
    print("=" * 64)
    for name, wins, n, wr, maps in rows:
        print(f"  {name:<14} {wins:>2}/{n:<2} ({wr:6.1%})   maps: {', '.join(maps)}")
    print("-" * 64)
    print(f"  {'OVERALL':<14} {total_wins:>2}/{total_games:<2} ({overall:6.1%})")
    print("=" * 64)


def print_best_callback(study: optuna.Study, trial: optuna.FrozenTrial) -> None:
    if study.best_trial.number == trial.number:
        logger.info(f"NEW BEST: trial {trial.number}, score={study.best_value:.3f}, "
                     f"win_rate={trial.user_attrs.get('win_rate', 0):.1%}")


def show_best(study: optuna.Study, target_bot: Path, param_space: dict) -> None:
    if not study.trials:
        print("No trials yet.")
        return
    current = current_param_values(target_bot, param_space)
    print(f"Starting values ({target_bot} today):")
    for k in param_space:
        print(f"  {k}: {current[k]}")
    print()
    t = study.best_trial
    print(f"Best trial: #{t.number}  score={t.value:.3f}  "
          f"win_rate={t.user_attrs.get('win_rate', 0):.1%}  games={t.user_attrs.get('games', 0)}")
    print("Params:")
    for k, v in t.params.items():
        note = "  (unchanged)" if v == current.get(k) else f"  (was {current.get(k)})"
        print(f"  {k}: {v}{note}")
    print()
    print(f"To apply these permanently, edit {target_bot}'s source with these values,")
    print(f"then re-run: python3 optuna/tune_bot.py --bot {target_bot.name} --freeze-baseline --force")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bot", default="count", help="Bot to tune, as a name under bots/ (default: count)")
    ap.add_argument("--param-space", default=None,
                     help="JSON file of {'module.CONST': [kind, low, high, step]}. See "
                          "optuna/discover_params.py to find candidates for a bot. "
                          "Default: optuna/param_space_<bot>.json")
    ap.add_argument("--n-trials", type=int, default=20)
    ap.add_argument("--maps", nargs="+", default=None,
                     help="Fixed map set for the whole study (default: the bot's live pool — "
                          "never resampled per trial, see score_batch's docstring on why)")
    ap.add_argument("--seeds", nargs="+", type=int, default=[1])
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--db", default=None, help="Default: optuna/<bot>_optuna.db")
    ap.add_argument("--study-name", default=None, help="Default: <bot>_tuning_v1")
    ap.add_argument("--keep-artifacts", action="store_true")
    ap.add_argument("--freeze-baseline", action="store_true",
                     help="(Re)create optuna/<bot>_baseline from the current bots/<bot>, then exit.")
    ap.add_argument("--force", action="store_true", help="With --freeze-baseline, overwrite an existing one.")
    ap.add_argument("--show-best", action="store_true", help="Print the study's best trial so far and exit.")
    ap.add_argument("--tournament", action="store_true",
                     help="Instead of running the optimization loop, play the target bot (or a "
                          "candidate's params, see --tournament-params/--tournament-best) against real "
                          "opponents (default: <bot>_baseline, bots/baron, bots/bandit) on maps sampled "
                          "from its live competitive pool, then exit.")
    ap.add_argument("--tournament-opponents", nargs="+", default=None,
                     help="Opponent bot directories for --tournament. Default: <bot>_baseline, bots/baron, bots/bandit.")
    ap.add_argument("--tournament-params", default=None,
                     help="With --tournament: path to a params JSON (e.g. saved from a trial) to apply "
                          "before playing. Omit to tournament-test the target bot exactly as it is today.")
    ap.add_argument("--tournament-best", action="store_true",
                     help="With --tournament: apply the study's current best trial's params before playing.")
    ap.add_argument("--sample-size", type=int, default=5,
                     help="With --tournament: random maps sampled per opponent from the live pool "
                          "(default: 5). Ignored if --maps is also given.")
    ap.add_argument("--out", default=None, help="With --tournament: output directory for match reports "
                                                  "(default: test_results/optuna_tournament_<bot>)")
    ap.add_argument("--milestone-weighted", action="store_true",
                     help="Score each trial against every milestone (scripts/ladder/milestone_gauntlet.py's "
                          "MILESTONE_WEIGHTS), not a frozen copy of itself. Anti-overfitting alternative to "
                          "the default baseline-only objective — see this file's "
                          "create_weighted_milestone_objective() docstring for why. Costs #milestones times "
                          "as many games per trial; --freeze-baseline is not needed in this mode.")
    ap.add_argument("--milestone-versions", nargs="+", type=int, default=None,
                     help="With --milestone-weighted: which milestone versions to score against "
                          "(default: MILESTONE_VERSIONS in scripts/ladder/milestone_gauntlet.py)")
    ap.add_argument("--loss-penalty-base", type=float, default=1.0,
                     help="With --milestone-weighted: a loss against milestone v costs "
                          "loss_penalty_base / weight[v] — deliberately not a fixed 'massive' number, since "
                          "an unbounded penalty can make the sampler too risk-averse to explore at all "
                          "(default: 1.0; raise it if trades against old milestones still slip through)")
    args = ap.parse_args()

    target_bot = REPO_ROOT / "bots" / args.bot
    if not (target_bot / "main.py").exists():
        logger.error(f"{target_bot} has no main.py — is --bot {args.bot!r} right?")
        sys.exit(1)
    trial_bot = TUNING_DIR / f"{args.bot}_trial"
    baseline_bot = TUNING_DIR / f"{args.bot}_baseline"
    db = args.db or f"sqlite:///{TUNING_DIR / f'{args.bot}_optuna.db'}"
    # Deliberately a different default study name in --milestone-weighted
    # mode: its objective values are an unbounded weighted sum, not the
    # baseline objective's roughly-[-0.4, 1.4] smooth score — mixing the
    # two into one study would make "best trial" meaningless, silently.
    default_study = f"{args.bot}_tuning_weighted_v1" if args.milestone_weighted else f"{args.bot}_tuning_v1"
    study_name = args.study_name or default_study
    tournament_opponents = args.tournament_opponents or [str(baseline_bot), *DEFAULT_TOURNAMENT_EXTRA_OPPONENTS]
    out_dir = REPO_ROOT / (args.out or f"test_results/optuna_tournament_{args.bot}")

    param_space_path = Path(args.param_space) if args.param_space else \
        (DEFAULT_PARAM_SPACE_PATH if args.bot == "count" else TUNING_DIR / f"param_space_{args.bot}.json")
    if not param_space_path.is_absolute():
        param_space_path = REPO_ROOT / param_space_path

    if args.freeze_baseline:
        freeze_baseline(target_bot, baseline_bot, force=args.force)
        return

    if not param_space_path.exists():
        logger.error(f"{param_space_path} does not exist. Build one with optuna/discover_params.py "
                      f"--bot {args.bot} plus hand-picked bounds, or pass --param-space explicitly.")
        sys.exit(1)
    param_space = load_param_space(param_space_path)

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        study_name=study_name, storage=db, direction="maximize", load_if_exists=True,
    )

    if args.show_best:
        show_best(study, target_bot, param_space)
        return

    if args.tournament:
        fcv_bin = find_fcv_bin()
        prepare_trial_bot(target_bot, trial_bot)
        params_path = None
        if args.tournament_best:
            if not study.trials:
                logger.error("No trials in the study yet — nothing to use for --tournament-best.")
                sys.exit(1)
            params_path = Path(tempfile.mkstemp(suffix=".json", prefix="tournament_best_")[1])
            params_path.write_text(json.dumps(study.best_trial.params), encoding="utf-8")
            logger.info(f"Using study best trial #{study.best_trial.number}'s params for the tournament.")
        elif args.tournament_params:
            params_path = Path(args.tournament_params)
        try:
            run_tournament(
                fcv_bin=fcv_bin,
                trial_bot=trial_bot,
                live_pool_bot=target_bot,
                opponents=tournament_opponents,
                sample_size=args.sample_size,
                maps_override=args.maps,
                seeds=args.seeds,
                jobs=args.jobs,
                out_dir=out_dir,
                env_extra={"TUNE_PARAMS": str(params_path)} if params_path else {},
            )
        finally:
            if args.tournament_best and params_path:
                params_path.unlink(missing_ok=True)
        return

    if not param_space:
        logger.error(f"{param_space_path} has no parameters — nothing to tune.")
        sys.exit(1)

    maps = args.maps if args.maps else live_map_pool(target_bot)
    if not maps:
        logger.error("Could not resolve a map pool — pass --maps explicitly.")
        sys.exit(1)
    fcv_bin = find_fcv_bin()
    prepare_trial_bot(target_bot, trial_bot)

    logger.info(f"bot={args.bot}  target={target_bot} (untouched) trial={trial_bot}")
    logger.info(f"maps ({len(maps)}, fixed for this study): {', '.join(maps)}")
    logger.info(f"seeds: {args.seeds}  jobs: {args.jobs}  trials: {args.n_trials}")
    current = current_param_values(target_bot, param_space)
    for k in param_space:
        logger.info(f"  starting value: {k} = {current[k]}")

    if args.milestone_weighted:
        versions = args.milestone_versions or MILESTONE_VERSIONS
        subs = {s["version"]: s for s in list_submissions()}
        missing = [v for v in versions if v not in subs]
        if missing:
            logger.warning(f"Versions not found on the ladder, skipping: {missing}")
        picked = {v: subs[v] for v in versions if v in subs}
        if not picked:
            logger.error("No valid milestone versions to score against.")
            sys.exit(1)
        logger.info("Syncing milestone bots (downloading any not already cached)...")
        milestone_bots = {v: ensure_local(sub, dest_root=MILESTONES_DIR) for v, sub in picked.items()}
        weights = {v: MILESTONE_WEIGHTS.get(v, DEFAULT_MILESTONE_WEIGHT) for v in milestone_bots}
        logger.info(f"milestone-weighted objective: {len(milestone_bots)} opponents "
                    f"({sum(1 for _ in milestone_bots)}x the games per trial vs baseline mode) — "
                    f"weights: {weights}")
        obj = create_weighted_milestone_objective(
            fcv_bin, trial_bot, milestone_bots, weights, param_space, maps, args.seeds, args.jobs,
            args.keep_artifacts, args.loss_penalty_base,
        )
    else:
        if not baseline_bot.exists():
            logger.error(f"{baseline_bot} does not exist. Run with --freeze-baseline first.")
            sys.exit(1)
        logger.info(f"baseline={baseline_bot}")
        obj = create_objective(fcv_bin, trial_bot, baseline_bot, param_space, maps, args.seeds, args.jobs,
                                args.keep_artifacts)

    study.optimize(obj, n_trials=args.n_trials, callbacks=[print_best_callback])

    show_best(study, target_bot, param_space)


if __name__ == "__main__":
    main()
