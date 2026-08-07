#!/usr/bin/env python3
"""Zip a bot in bots/ for submission, runtime files only.

    python scripts/zip_bot.py baron

Starts at the bot's main.py and follows local imports, so only modules the bot
actually loads go in. Docs, replays, __pycache__ and unreachable modules are
left out. Files land at the zip root (main.py must be the entry point). An
existing zip for the same bot is replaced.
"""

from __future__ import annotations

import argparse
import ast
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BOTS_DIR = REPO_ROOT / "bots"


def localImports(path: Path, botDir: Path) -> set[str]:
    """Module names imported by `path` that resolve to a .py file in botDir."""
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return {n for n in names if (botDir / f"{n}.py").is_file()}


def reachableModules(botDir: Path) -> list[Path]:
    """main.py plus every local module reachable from it, breadth-first."""
    entry = botDir / "main.py"
    if not entry.is_file():
        sys.exit(f"error: {entry} not found — a bot needs a main.py")

    seen = {"main"}
    queue = ["main"]
    while queue:
        for name in sorted(localImports(botDir / f"{queue.pop()}.py", botDir)):
            if name not in seen:
                seen.add(name)
                queue.append(name)
    return sorted(botDir / f"{n}.py" for n in seen)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bot", help=f"bot directory name under {BOTS_DIR.relative_to(REPO_ROOT)}/")
    parser.add_argument("-o", "--outdir", type=Path, default=REPO_ROOT, help="where to write the zip (default: repo root)")
    parser.add_argument("--all", action="store_true", help="include every .py in the bot dir, not just imported ones")
    parser.add_argument("--include", action="append", default=[], metavar="NAME", help="extra file to add, relative to the bot dir (repeatable)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="list what would be zipped and stop")
    args = parser.parse_args()

    botDir = BOTS_DIR / args.bot
    if not botDir.is_dir():
        available = ", ".join(sorted(p.name for p in BOTS_DIR.iterdir() if (p / "main.py").is_file()))
        sys.exit(f"error: no bot directory {botDir}\navailable: {available}")

    if args.all:
        files = sorted(p for p in botDir.glob("*.py") if p.is_file())
    else:
        files = reachableModules(botDir)

    for extra in args.include:
        path = botDir / extra
        if not path.is_file():
            sys.exit(f"error: --include {extra} not found in {botDir}")
        if path not in files:
            files.append(path)

    if not args.all:
        skipped = sorted(p.name for p in botDir.glob("*.py") if p not in files)
        if skipped:
            print(f"skipping unimported: {', '.join(skipped)}")

    zipPath = args.outdir / f"{args.bot}.zip"
    total = sum(p.stat().st_size for p in files)
    print(f"{len(files)} file(s), {total / 1024:.1f} KiB -> {zipPath}")
    for path in files:
        print(f"  {path.name}")

    if args.dry_run:
        return

    args.outdir.mkdir(parents=True, exist_ok=True)
    if zipPath.exists():
        print(f"replacing existing {zipPath.name}")
    tmpPath = zipPath.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmpPath, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, arcname=path.name)
    tmpPath.replace(zipPath)
    print(f"wrote {zipPath}")


if __name__ == "__main__":
    main()
