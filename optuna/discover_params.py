#!/usr/bin/env python3
"""Discover every module-level ALL_CAPS constant in a bot directory — the
full candidate surface tuning could ever touch, not a curated selection.
Sibling to tune_bot.py's param_space_<bot>.json files: those are the
curated subset someone's actually decided to search over, with bounds;
this script's output is everything that COULD go in one, so picking a new
entry is "skim a list" rather than "grep the codebase by hand." Works on
any bot directory, not just bots/count — that's the point of keeping it
separate from tune_bot.py.

Static (ast-only, never imports or executes the bot — safe to run against
any bot, even a currently-broken one), so it can only see LITERAL values
(numbers, bools, strings, small tuples/lists of literals). An assignment
computed from an expression (references another name, calls a function)
still gets recorded — flagged non-literal, since we can't know its
resolved value without actually running the bot — because being thorough
here matters more than being clean. Nothing is filtered out.

Nothing here is language-aware analysis, just ast pattern-matching against
a naming convention every bot in this repo happens to follow (ALL_CAPS
module-level constants). A bot that keeps its tunables in a class, a dict,
or computes them at runtime won't be found this way — that's a real limit,
not a bug to work around here.

Ranking, not filtering: every discovered constant is kept, but sorted with
the most-likely-genuine-tuning-knobs first and the most-likely-structural
noise (store slot indices, role IDs, entity-type tables, ...) pushed
toward the bottom, via a small transparent point-scored heuristic (see
_score()). The JSON output keeps every entry's score and the reasons that
produced it, so you can see *why* something sank before deciding it's
still worth tuning.

Usage:
    python3 optuna/discover_params.py bots/count
    python3 optuna/discover_params.py bots/baron --out optuna/discovered_baron.json
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Name substrings that push a constant toward "probably don't touch this" —
# store wire-format slots, role/entity IDs, bit-packing details. Positive
# weight = higher avoid_score = sinks toward the bottom.
_NOISE_NAME_HINTS: dict[str, int] = {
    "SLOT": 8, "ROLE": 6, "ENTITY": 6, "DIRECTION": 5, "MASK": 5,
    "INDEX": 4, "BIT": 4, "SHIFT": 4, "VERSION": 4, "STATE": 3,
    "CONDITION": 3, "TYPE": 3, "OFFSET": 2,
}
# Name substrings that push a constant toward "this looks like a real
# tuning knob" — negative weight = lower avoid_score = floats to the top.
_SIGNAL_NAME_HINTS: dict[str, int] = {
    "THRESHOLD": -6, "LEASH": -6, "DELAY": -5, "RADIUS": -5, "LIMIT": -5,
    "RESERVE": -5, "MAX": -4, "MIN": -4, "RATE": -4, "WINDOW": -4,
    "TURNS": -4, "ROUNDS": -4, "COOLDOWN": -4, "COUNT": -4, "RANGE": -3,
    "CAP": -3, "TARGET": -3, "BUDGET": -3, "DISTANCE": -3, "SIZE": -2,
}

_CONST_NAME_RE = re.compile(r"^_?[A-Z][A-Z0-9_]*$")


def _score(name: str, value: object, is_literal: bool) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    upper = name.upper()
    for hint, weight in _NOISE_NAME_HINTS.items():
        if hint in upper:
            score += weight
            reasons.append(f"name contains {hint!r} ({weight:+d}, looks structural)")
    for hint, weight in _SIGNAL_NAME_HINTS.items():
        if hint in upper:
            score += weight
            reasons.append(f"name contains {hint!r} ({weight:+d}, looks like a tuning knob)")
    if not is_literal:
        score += 10
        reasons.append("value isn't a literal — can't see it without running the bot (+10)")
    elif isinstance(value, (list, tuple, dict)):
        score += 6
        reasons.append("composite value (tuple/list/dict), not a single scalar knob (+6)")
    elif isinstance(value, str):
        score += 5
        reasons.append("string constant, unlikely to be a numeric tuning knob (+5)")
    elif isinstance(value, bool):
        pass  # feature flags are legitimately tunable — no adjustment either way
    elif isinstance(value, (int, float)) and value in (0, 1):
        score += 2
        reasons.append("value is 0 or 1 — often an index/flag rather than a magnitude (+2)")
    return score, reasons


def discover(bot_dir: Path) -> list[dict]:
    entries: list[dict] = []
    seen_names: dict[str, str] = {}
    for path in sorted(bot_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        modname = path.stem
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as e:
            print(f"Warning: could not parse {path}: {e}", file=sys.stderr)
            continue

        for node in tree.body:  # top level only — excludes class/function-local names
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                value_node = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
                value_node = node.value
            else:
                continue
            if value_node is None:
                continue

            for name in names:
                if not _CONST_NAME_RE.match(name):
                    continue
                try:
                    value = ast.literal_eval(value_node)
                    is_literal = True
                except Exception:
                    value = None
                    is_literal = False

                dotted = f"{modname}.{name}"
                rel_path = str(path.relative_to(bot_dir))
                if dotted in seen_names and seen_names[dotted] != rel_path:
                    print(f"Warning: {dotted} appears in both {seen_names[dotted]} and "
                          f"{rel_path} — dotted name is ambiguous for tuning.py, "
                          "disambiguate by file before adding either to a param space.",
                          file=sys.stderr)
                seen_names[dotted] = rel_path

                score, reasons = _score(name, value, is_literal)
                entries.append({
                    "name": dotted,
                    "value": value,
                    "literal": is_literal,
                    "type": type(value).__name__ if is_literal else None,
                    "file": rel_path,
                    "line": node.lineno,
                    "avoid_score": score,
                    "reasons": reasons,
                })

    entries.sort(key=lambda e: (e["avoid_score"], e["name"]))
    return entries


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bot_dir", help="Path to a bot directory, e.g. bots/count")
    ap.add_argument("--out", default=None,
                     help="Output JSON path (default: optuna/discovered_<botname>.json)")
    ap.add_argument("--top", type=int, default=15, help="How many top/bottom entries to preview on stdout")
    args = ap.parse_args()

    bot_dir = Path(args.bot_dir)
    if not bot_dir.is_absolute():
        bot_dir = REPO_ROOT / bot_dir
    if not (bot_dir / "main.py").exists():
        print(f"Error: {bot_dir} has no main.py — is this a bot directory?", file=sys.stderr)
        sys.exit(1)

    entries = discover(bot_dir)

    out_path = Path(args.out) if args.out else REPO_ROOT / "optuna" / f"discovered_{bot_dir.name}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"bot": bot_dir.name, "count": len(entries), "params": entries}, indent=2),
                         encoding="utf-8")

    print(f"Found {len(entries)} module-level constants in {bot_dir}.")
    print(f"Wrote {out_path}\n")
    print(f"Most likely genuine tuning knobs (top {args.top}):")
    for e in entries[:args.top]:
        print(f"  {e['avoid_score']:>3}  {e['name']:<40} = {e['value']!r}")
    print(f"\nMost likely structural noise (bottom {args.top}):")
    for e in entries[-args.top:]:
        print(f"  {e['avoid_score']:>3}  {e['name']:<40} = {e['value']!r}")


if __name__ == "__main__":
    main()
