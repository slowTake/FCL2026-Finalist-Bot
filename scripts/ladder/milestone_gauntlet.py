#!/usr/bin/env python3
"""Regression-tracking gauntlet: play the bot under development against a
hand-curated set of past ladder submissions ("milestones" — see
MILESTONE_VERSIONS below), using fcv_gauntlet's full causal-analysis engine
(the same one scripts/fcv_bot_gauntlet.py exposes) rather than plain
win/loss. Built for repeated use during development — "is the bot actually
getting better release over release" — not a one-off report. For a
fast/silly comparison across the *entire* submission history instead, see
scripts/ladder/version_bracket.py.

Focused on the bot under test, not the milestone opponents: each milestone
matchup runs the full fcv_gauntlet detector suite (crashes, orphan
harvesters, friendly fire, Ti leaks, causal chains...), but the summary
table this script prints only aggregates the *target's own* numbers across
every milestone — trending our own execution quality across the team's
history — rather than spending equal report space characterizing each
historical opponent's own long-dead bugs.

Each milestone matchup is its own `scripts/fcv_bot_gauntlet.py` subprocess
call (never called in-process): fcv_gauntlet's run_full_gauntlet() plays its
games sequentially in one process with no per-game isolation, so batching
every milestone's maps into one long-lived process risks the same
cumulative-CPU sandbox ceiling optuna/tune_bot.py works around — giving
each milestone its own process resets that budget.

Milestones are downloaded once via `fcode submission download` and cached
under submission_cache/ (gitignored, shared with version_bracket.py).

Usage:
    python3 scripts/ladder/milestone_gauntlet.py                    # bots/count vs every milestone, 5 sampled maps
    python3 scripts/ladder/milestone_gauntlet.py --bot count_v2
    python3 scripts/ladder/milestone_gauntlet.py --maps drumlin nordkap
    python3 scripts/ladder/milestone_gauntlet.py --markdown-out test_results/milestones.md
    python3 scripts/ladder/milestone_gauntlet.py --oracle --oracle-yes   # + AI root-cause diagnosis on every matchup
                                                                   # (costs real API money — see fcv_gauntlet/oracle/AGENT.md;
                                                                   # never pass --oracle-yes without explicit sign-off for this run)

    # One command, both runs: the baseline bot it's been tuning from, then
    # the tuned result, same milestones and same sampled maps for both, so
    # the two tables are directly comparable rather than needing two
    # separate invocations diffed by hand. Reads optuna/tune_bot.py's
    # study for --bot by default (same --db/--study-name conventions).
    python3 scripts/ladder/milestone_gauntlet.py --compare-tuned
    python3 scripts/ladder/milestone_gauntlet.py --compare-tuned --tuned-params my_trial.json
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))       # sibling: submission_cache
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # scripts/: live_map_pool
from live_map_pool import live_map_pool  # noqa: E402
from submission_cache import REPO_ROOT, list_submissions, ensure_local, slug  # noqa: E402
sys.path.insert(0, str(REPO_ROOT / "optuna"))
import materialize_trial  # noqa: E402

BOTS_DIR = REPO_ROOT / "bots"


def _venv_python() -> str:
    """Never trust sys.executable for the fcv_bot_gauntlet.py child process —
    if this script itself was invoked with the system python (venv not
    activated), sys.executable would propagate that into a process that
    hard-depends on fcode/fcv/fcv_gauntlet being importable, and fail with a
    traceback that (pre-fix) looked like a silent, misleading JSON parse
    error instead of the real cause."""
    venv_python = REPO_ROOT / ".venv" / "bin" / "python3"
    return str(venv_python) if venv_python.exists() else sys.executable
MILESTONES_DIR = BOTS_DIR / "_milestones"

# Hand-curated, not auto-derived from the submission list — add a line
# whenever a new version is worth checking the current bot against. Run
# `fcode submission list` to see the full history and pick more.
MILESTONE_VERSIONS: list[int] = [
    1,    # turtleHive — the very first submission
    9,    # earl — first stable earl-era bot
    45,   # baronDebut — first baron
    77,   # baronBandit — mid-baron era
    104,  # countDebut — first count
    118,  # countExplodingTurrets — current active (update as new versions land)
]

# How much a win/loss against each milestone should count when
# optuna/tune_bot.py's --milestone-weighted objective aggregates across
# all of them — the "recency/strength" weight requested to fix trials that
# trade wins against old milestones for wins against the current one and
# call it even. Not used by the plain (non-weighted) milestone_gauntlet.py
# report above, which already shows every milestone's raw W-L unweighted;
# this is specifically for steering the Optuna *search*, where an
# unweighted sum can't distinguish "beat the current frontier" from "beat
# a bot nobody plays anymore." Update alongside MILESTONE_VERSIONS.
MILESTONE_WEIGHTS: dict[int, float] = {
    1: 0.3,
    9: 0.4,
    45: 0.6,
    77: 0.8,
    104: 1.2,
    118: 2.0,
}
DEFAULT_MILESTONE_WEIGHT = 1.0  # for any --versions entry not listed above


def _sync_milestone_bots(versions: list[int]) -> dict[int, dict]:
    """Download (if needed) every requested milestone and place it under
    bots/_milestones/, so fcv_gauntlet's bots_dir-relative resolution finds
    it the same way it finds bots/count. Returns version -> submission dict.
    """
    subs = {s["version"]: s for s in list_submissions()}
    missing = [v for v in versions if v not in subs]
    if missing:
        print(f"Warning: versions not found on the ladder, skipping: {missing}", file=sys.stderr)
    picked = {v: subs[v] for v in versions if v in subs}
    for sub in picked.values():
        ensure_local(sub, dest_root=MILESTONES_DIR)
    return picked


def run_one_matchup(target: str, opponent_rel: str, maps: list[str], oracle: bool, oracle_yes: bool) -> dict | None:
    json_out = Path(tempfile.mkstemp(suffix=".json", prefix="milestone_")[1])
    cmd = [
        _venv_python(), str(REPO_ROOT / "scripts" / "fcv_bot_gauntlet.py"),
        target, opponent_rel,
        "--json-output", str(json_out),
        "--badges-only",
    ]
    for m in maps:
        cmd += ["-m", m]
    if oracle:
        cmd.append("--oracle")
    if oracle_yes:
        cmd.append("--oracle-yes")
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                           timeout=60 * len(maps) * 2 + 180)
    # mkstemp already created json_out empty, so existence alone doesn't mean
    # a report was actually written — and exit 1 is legitimately "candidate
    # lost the match", not necessarily an error, so size is the real signal.
    if not json_out.exists() or json_out.stat().st_size == 0:
        print(f"  fcv_bot_gauntlet exit {proc.returncode}, no report written: "
              f"{proc.stderr.strip()[-400:]}", file=sys.stderr)
        json_out.unlink(missing_ok=True)
        return None
    if proc.returncode not in (0, 1):
        print(f"  fcv_bot_gauntlet exit {proc.returncode}: {proc.stderr.strip()[-400:]}", file=sys.stderr)
    data = json.loads(json_out.read_text(encoding="utf-8"))
    json_out.unlink(missing_ok=True)
    return data


def summarize_for_target(report: dict, target: str) -> dict:
    """Pull only the target bot's own numbers out of a full fcv_gauntlet
    report — see this file's module docstring on why the opponent's side
    is intentionally not summarized here."""
    games = report.get("games", [])
    wins = losses = 0
    for g in games:
        if g.get("team_a_bot") == target:
            won = g.get("winner_side") == "A"
        elif g.get("team_b_bot") == target:
            won = g.get("winner_side") == "B"
        else:
            continue
        wins += 1 if won else 0
        losses += 0 if won else 1

    events = report.get("events", [])
    target_events = [e for e in events if e.get("metadata", {}).get("bot_name") == target]
    ti_loss = sum(e.get("estimated_ti_loss") or 0 for e in target_events)
    by_detector: dict[str, int] = {}
    for e in target_events:
        d = e.get("detector", "?")
        by_detector[d] = by_detector.get(d, 0) + 1

    return {
        "wins": wins, "losses": losses,
        "ti_loss": round(ti_loss, 1),
        "crashes": by_detector.get("crash", 0),
        "orphans": by_detector.get("orphan_harvester", 0),
        "friendly_fire": by_detector.get("friendly_fire", 0),
    }


def run_milestones(bot_name: str, picked: dict, maps: list[str], oracle: bool, oracle_yes: bool) -> list[tuple]:
    """Play `bot_name` (a name under bots/) against every picked milestone
    on the same maps, returning the same rows shape main() prints from.
    Shared between the base run and the --compare-tuned run so both go
    through identical logic."""
    rows: list[tuple[int, str, dict]] = []
    for v in sorted(picked):
        sub = picked[v]
        opp_rel = f"_milestones/{slug(sub)}"
        print(f"  vs v{v} ({sub.get('name')}) ...")
        report = run_one_matchup(bot_name, opp_rel, maps, oracle, oracle_yes)
        if report is None:
            print("    -> no report produced, skipping")
            continue
        stats = summarize_for_target(report, bot_name)
        rows.append((v, sub.get("name") or f"v{v}", stats))
        print(f"    -> {stats['wins']}-{stats['losses']}   "
              f"Ti lost: {stats['ti_loss']}   crashes: {stats['crashes']}   "
              f"orphans: {stats['orphans']}   friendly fire: {stats['friendly_fire']}")
    return rows


def render_table(bot_label: str, maps: list[str], rows: list[tuple]) -> str:
    lines = [f"# Milestone gauntlet: {bot_label} vs its own history", "",
             f"Maps: {', '.join(maps)}", "",
             "| Version | Name | W-L | Ti Lost | Crashes | Orphans | Friendly Fire |",
             "|---|---|---|---|---|---|---|"]
    for v, name, s in rows:
        lines.append(f"| v{v} | {name} | {s['wins']}-{s['losses']} | {s['ti_loss']} | "
                      f"{s['crashes']} | {s['orphans']} | {s['friendly_fire']} |")
    return "\n".join(lines)


def render_diff(base_rows: list[tuple], tuned_rows: list[tuple]) -> str:
    base_by_v = {v: s for v, _, s in base_rows}
    tuned_by_v = {v: s for v, _, s in tuned_rows}
    lines = ["", "## Difference (tuned - base)", "",
             "| Version | ΔWins | ΔLosses | ΔTi Lost |", "|---|---|---|---|"]
    for v in sorted(set(base_by_v) & set(tuned_by_v)):
        b, t = base_by_v[v], tuned_by_v[v]
        dw, dl = t["wins"] - b["wins"], t["losses"] - b["losses"]
        dti = round(t["ti_loss"] - b["ti_loss"], 1)
        lines.append(f"| v{v} | {dw:+d} | {dl:+d} | {dti:+.1f} |")
    base_w, base_g = sum(s["wins"] for _, _, s in base_rows), sum(s["wins"] + s["losses"] for _, _, s in base_rows)
    tuned_w, tuned_g = sum(s["wins"] for _, _, s in tuned_rows), sum(s["wins"] + s["losses"] for _, _, s in tuned_rows)
    lines.append("")
    lines.append(f"Overall: base {base_w}/{base_g} wins  →  tuned {tuned_w}/{tuned_g} wins")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bot", default="count", help="Target bot, as a name under bots/ (default: count)")
    ap.add_argument("--versions", nargs="+", type=int, default=MILESTONE_VERSIONS,
                     help="Milestone submission versions to test against (default: MILESTONE_VERSIONS in this file)")
    ap.add_argument("--maps", nargs="+", default=None,
                     help="Fixed maps for every milestone matchup (default: 5 random from the target's live pool)")
    ap.add_argument("--sample-size", type=int, default=5)
    ap.add_argument("--seed", type=int, default=None, help="Map-sampling RNG seed")
    ap.add_argument("--markdown-out", default=None, help="Write the trend summary table to this file too")
    ap.add_argument("--oracle", action="store_true", help="Pass through to fcv_bot_gauntlet.py per matchup")
    ap.add_argument("--oracle-yes", action="store_true", help="Pass through — costs real API money, see module docstring")
    ap.add_argument("--compare-tuned", action="store_true",
                     help="Also materialize a tuning result (optuna/materialize_trial.py) and run the exact "
                          "same milestones/maps against it, printing base vs tuned side by side.")
    ap.add_argument("--tuned-params", default=None,
                     help="With --compare-tuned: params JSON to materialize instead of the study's best trial.")
    ap.add_argument("--tuned-db", default=None, help="With --compare-tuned: overrides optuna/tune_bot.py's default DB.")
    ap.add_argument("--tuned-study-name", default=None, help="With --compare-tuned: overrides the default study name.")
    args = ap.parse_args()

    target_main = BOTS_DIR / args.bot / "main.py"
    if not target_main.exists():
        print(f"Error: {target_main} does not exist.", file=sys.stderr)
        sys.exit(1)

    print("Syncing milestone bots (downloading any not already cached)...")
    picked = _sync_milestone_bots(args.versions)
    if not picked:
        print("No valid milestones to run against.", file=sys.stderr)
        sys.exit(1)

    if args.maps:
        maps = args.maps
    else:
        pool = live_map_pool(BOTS_DIR / args.bot) or []
        if not pool:
            print("Error: could not resolve a live map pool — pass --maps explicitly.", file=sys.stderr)
            sys.exit(1)
        # Sampled once, before either run, so base and tuned (if
        # --compare-tuned) are judged on identical maps.
        maps = random.Random(args.seed).sample(pool, min(args.sample_size, len(pool)))

    print(f"Target: bots/{args.bot}   Maps ({len(maps)}): {', '.join(maps)}")

    print(f"\n=== base: bots/{args.bot} ===")
    base_rows = run_milestones(args.bot, picked, maps, args.oracle, args.oracle_yes)
    base_table = render_table(f"bots/{args.bot}", maps, base_rows)
    output = base_table

    if args.compare_tuned:
        if args.tuned_params:
            params = json.loads(Path(args.tuned_params).read_text(encoding="utf-8"))
            label = args.tuned_params
        else:
            params, label = materialize_trial.load_best_trial_params(args.bot, args.tuned_db, args.tuned_study_name)
        tuned_name = f"{args.bot}_tuned"
        tuned_dir = BOTS_DIR / tuned_name
        source_bot = BOTS_DIR / args.bot
        if tuned_dir.exists():
            shutil.rmtree(tuned_dir)
        shutil.copytree(source_bot, tuned_dir, ignore=shutil.ignore_patterns("__pycache__"))
        print(f"\nMaterializing {label} into bots/{tuned_name}:")
        for dotted, value in params.items():
            modname, _, attr = dotted.rpartition(".")
            file_path = materialize_trial.resolve_module_file(tuned_dir, modname)
            materialize_trial.patch_constant(file_path, attr, value)
            print(f"  {dotted} = {value!r}")

        print(f"\n=== tuned: bots/{tuned_name} ===")
        tuned_rows = run_milestones(tuned_name, picked, maps, args.oracle, args.oracle_yes)
        tuned_table = render_table(f"bots/{tuned_name} ({label})", maps, tuned_rows)
        output = base_table + "\n\n" + tuned_table + "\n" + render_diff(base_rows, tuned_rows)

    print("\n" + output)
    if args.markdown_out:
        Path(args.markdown_out).write_text(output + "\n", encoding="utf-8")
        print(f"\nWrote {args.markdown_out}")


if __name__ == "__main__":
    main()
