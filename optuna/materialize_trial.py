#!/usr/bin/env python3
"""Turn a tuning result — the study's current best trial, or any saved
params.json — into a real, standalone bot directory, so any existing
gauntlet tool (milestone_gauntlet.py, multi_bot_gauntlet.py,
head_to_head.py, version_bracket.py, tune_bot.py --tournament-opponents,
...) can play it like any other bot, with zero changes to any of those
tools. "Run the base bot, then the improved one, in the same test" becomes
"run the same tool twice with a different --bot" — nothing has to learn
about tuning to make that comparison possible.

This is "promoting a result" made concrete and comparable, not just
described — optuna/README.md's "Promoting a result" section already tells
you to hand-edit the bot's real source with a winning trial's values; this
does the same edit, just into a copy, so you can compare before/after
directly instead of overwriting your working source.

Unlike optuna/tune_bot.py's <bot>_trial (env-var injection at runtime,
gitignored, disposable, rebuilt every tuning run), this writes the values
directly into the copied source files — a real, inspectable, git-diffable
bot directory. The values are patched via ast (find the exact
module-level assignment, replace only that line) rather than baron's old
regex-substitution approach, which broke if a line's formatting drifted
from what the regex expected.

Usage:
    # Materialize count's current study's best trial into bots/count_tuned
    python3 optuna/materialize_trial.py --bot count

    # From a specific saved params.json instead of the study's best trial
    python3 optuna/materialize_trial.py --bot count --params my_trial.json --out bots/count_v135

    # Then run any gauntlet tool against it like any other bot:
    python3 scripts/ladder/milestone_gauntlet.py --bot count        # base
    python3 scripts/ladder/milestone_gauntlet.py --bot count_tuned  # improved — same maps, same milestones
"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TUNING_DIR = REPO_ROOT / "optuna"


def resolve_module_file(bot_dir: Path, modname: str) -> Path:
    candidates = [bot_dir] + [p for p in bot_dir.iterdir() if p.is_dir() and p.name != "__pycache__"]
    for d in candidates:
        f = d / f"{modname}.py"
        if f.exists():
            return f
    raise FileNotFoundError(f"Could not find {modname}.py under {bot_dir} or its subdirectories")


def _find_assignment_span(path: Path, name: str) -> tuple[int, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.lineno, node.end_lineno
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node.lineno, node.end_lineno
    raise ValueError(f"{name!r} is not a module-level assignment in {path}")


def patch_constant(path: Path, name: str, value: object) -> None:
    """Replace exactly the module-level `name = ...` (or annotated
    `name: T = ...`) assignment in path with `name = <value>`, preserving
    any type annotation. Any trailing inline comment on that line is
    dropped — a known, accepted limitation, not silently wrong."""
    lineno, end_lineno = _find_assignment_span(path, name)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    original = "".join(lines[lineno - 1:end_lineno]).strip()
    node = ast.parse(original).body[0]
    if isinstance(node, ast.AnnAssign):
        prefix = f"{name}: {ast.unparse(node.annotation)} = "
    else:
        prefix = f"{name} = "
    lines[lineno - 1:end_lineno] = [f"{prefix}{value!r}\n"]
    path.write_text("".join(lines), encoding="utf-8")


def load_best_trial_params(bot: str, db: str | None, study_name: str | None) -> tuple[dict, str]:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    db = db or f"sqlite:///{TUNING_DIR / f'{bot}_optuna.db'}"
    study_name = study_name or f"{bot}_tuning_v1"
    study = optuna.load_study(study_name=study_name, storage=db)
    if not study.trials:
        raise RuntimeError(f"No trials in study {study_name!r} ({db})")
    t = study.best_trial
    return t.params, f"trial #{t.number} (score={t.value:.3f}, win_rate={t.user_attrs.get('win_rate', 0):.1%})"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bot", default="count", help="Source bot, as a name under bots/ (default: count)")
    ap.add_argument("--params", default=None,
                     help="Path to a saved params JSON. Default: read the study's current best trial "
                          "(same --db/--study-name defaults as optuna/tune_bot.py).")
    ap.add_argument("--db", default=None)
    ap.add_argument("--study-name", default=None)
    ap.add_argument("--out", default=None, help="Output bot directory. Default: bots/<bot>_tuned")
    ap.add_argument("--force", action="store_true", help="Overwrite --out if it already exists.")
    args = ap.parse_args()

    source_bot = REPO_ROOT / "bots" / args.bot
    if not (source_bot / "main.py").exists():
        print(f"Error: {source_bot} has no main.py", file=sys.stderr)
        sys.exit(1)

    if args.params:
        params = json.loads(Path(args.params).read_text(encoding="utf-8"))
        label = args.params
    else:
        params, label = load_best_trial_params(args.bot, args.db, args.study_name)

    if not params:
        print("No parameters to apply.", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out) if args.out else REPO_ROOT / "bots" / f"{args.bot}_tuned"
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    if out_dir.exists():
        if not args.force:
            print(f"{out_dir} already exists — pass --force to overwrite.", file=sys.stderr)
            sys.exit(1)
        shutil.rmtree(out_dir)

    shutil.copytree(source_bot, out_dir, ignore=shutil.ignore_patterns("__pycache__"))

    print(f"Materializing {label} from {source_bot} into {out_dir}:")
    for dotted, value in params.items():
        modname, _, attr = dotted.rpartition(".")
        file_path = resolve_module_file(out_dir, modname)
        patch_constant(file_path, attr, value)
        print(f"  {dotted} = {value!r}  ({file_path.relative_to(out_dir)})")

    print(f"\nDone. {out_dir} is a real, standalone bot — run any gauntlet tool against it by name, e.g.:")
    print(f"  python3 scripts/ladder/milestone_gauntlet.py --bot {args.bot}          # base")
    print(f"  python3 scripts/ladder/milestone_gauntlet.py --bot {out_dir.name}   # improved — same milestones")
    print(f"  python3 scripts/multi_bot_gauntlet.py --target-bot {out_dir} "
          f"--opponents bots/{args.bot} bots/baron bots/bandit")


if __name__ == "__main__":
    main()
