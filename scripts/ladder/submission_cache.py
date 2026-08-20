"""Shared helper: list and locally cache Florent Code League submissions.

Used by scripts/ladder/version_bracket.py and scripts/ladder/milestone_gauntlet.py. Shells
out to the already-authenticated `fcode` CLI (`submission list` / `submission
download`) rather than re-implementing the ladder API's auth — same
dependency scripts/get_stats.py already has on `fcode login` having been run,
just through the CLI instead of hand-rolling the bearer-token HTTP calls.

Downloaded bots are cached under submission_cache/ (gitignored, regenerated
on demand) keyed by version, so re-running either script only re-downloads
whatever's genuinely missing.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = REPO_ROOT / "submission_cache"


def find_fcode_bin() -> str:
    venv_fcode = REPO_ROOT / ".venv" / "bin" / "fcode"
    if venv_fcode.exists():
        return str(venv_fcode)
    path_fcode = shutil.which("fcode")
    if path_fcode:
        return path_fcode
    print("Could not locate `fcode`. Activate the repo venv first.", file=sys.stderr)
    sys.exit(1)


def list_submissions() -> list[dict]:
    """Every submission this team has ever uploaded, oldest first."""
    fcode_bin = find_fcode_bin()
    proc = subprocess.run(
        [fcode_bin, "submission", "list", "--json"], capture_output=True, text=True
    )
    if proc.returncode != 0:
        print(f"`fcode submission list` failed: {proc.stderr.strip()}", file=sys.stderr)
        print("Are you logged in? Run `fcode login`.", file=sys.stderr)
        sys.exit(1)
    subs = json.loads(proc.stdout)
    return sorted(subs, key=lambda s: s["version"])


def slug(sub: dict) -> str:
    """v<N>_<sanitized name>, e.g. v104_countDebut — used as both the cache
    directory name and the label shown in bracket/gauntlet output."""
    name = sub.get("name") or ""
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "", name)
    return f"v{sub['version']}_{clean}" if clean else f"v{sub['version']}"


def ensure_local(sub: dict, dest_root: Path = CACHE_DIR) -> Path:
    """Download+unzip `sub` into dest_root/ if not already cached there."""
    dest = dest_root / slug(sub)
    if (dest / "main.py").exists():
        return dest
    dest_root.mkdir(parents=True, exist_ok=True)
    fcode_bin = find_fcode_bin()
    zip_path = dest_root / f"_dl_v{sub['version']}.zip"
    proc = subprocess.run(
        [fcode_bin, "submission", "download", str(sub["version"]), "-o", str(zip_path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"download v{sub['version']} failed: {proc.stderr.strip()}")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir()
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    zip_path.unlink()
    if not (dest / "main.py").exists():
        # Some older submissions zipped one wrapping directory instead of
        # its contents (e.g. earl/main.py instead of main.py) — flatten it.
        entries = list(dest.iterdir())
        if len(entries) == 1 and entries[0].is_dir() and (entries[0] / "main.py").exists():
            wrapper = entries[0]
            for child in wrapper.iterdir():
                shutil.move(str(child), str(dest / child.name))
            wrapper.rmdir()
    if not (dest / "main.py").exists():
        raise RuntimeError(f"v{sub['version']}'s zip had no main.py, even after flattening a wrapper dir")
    return dest
