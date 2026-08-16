"""Shared live-map-pool resolution for every gauntlet/eval script.

Extracted out of bot_gauntlet.py rather than left there, because
mirror_gauntlet.py, extreme_gauntlet.py, multiseed_eval.py, and
fcv_gauntlet/runner.py all had to answer the same question — "which maps is
this bot actually tuned for" — and were answering it four different ways:
one correct (bot_gauntlet's own live_map_pool), one hardcoded and already
wrong (mirror_gauntlet's old DEFAULT_LIVE_MAPS included atoll/heart/saga,
none of which are in any bot's MAP_ID_TO_NAME), two defaulting to every
*.map26 in maps_dir with no pool filtering at all (extreme_gauntlet,
multiseed_eval — silently running mostly off-pool matches), and one keeping
its own hardcoded copy of the 15-name list (fcv_gauntlet's COMPETITIVE_MAPS)
that can drift from features.py on the next map-rotation update without
anyone noticing. One canonical implementation, used everywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path


def live_map_pool(bot_dir: Path) -> list[str] | None:
    """MAP_ID_TO_NAME's values from the bot's own features.py, if it has one.

    This is the actual map pool the bot fingerprints/recognizes (see
    hivemind.py's _detect_and_store_map / KNOWN_MAPS) — narrower than every
    *.map26 file sitting in maps_dir, which accumulates maps the bot has no
    fingerprint table for and was never tuned against. Testing against those
    extra maps runs the bot in a materially different mode (no ORE_LOCATIONS/
    WALL_LOCATIONS, no fingerprinted enemy core) and produces regression
    signal that doesn't reflect real competitive performance — this script's
    own docstring used to say "every map in maps/, pass on 15/15," which only
    ever matched MAP_ID_TO_NAME's count; maps_dir just grew past it over time
    without this script being updated to filter back down.

    Searched recursively: bots may group shared components into a subdirectory
    (count/ keeps them in hivemind/). Missing it silently is expensive — the
    fallback quietly runs every map in maps/, which is three times the pool and
    exhausts the run's CPU budget partway through.

    Executed in an isolated namespace (not sys.modules) rather than imported,
    so gauntlet-ing multiple bots in one process can't cross-contaminate via
    Python's module cache — every bot's features.py shares the same module
    name. None if the bot has no features.py or no MAP_ID_TO_NAME, so callers
    can fall back to a full directory scan.
    """
    features_path = next(
        (p for p in sorted(bot_dir.rglob("features.py")) if "__pycache__" not in p.parts),
        None,
    )
    if features_path is None:
        print(
            f"Warning: no features.py under {bot_dir} — falling back to every map in "
            "maps/, which is a much larger and differently-shaped pool",
            file=sys.stderr,
        )
        return None
    ns: dict = {"__file__": str(features_path)}
    try:
        exec(compile(features_path.read_text(), str(features_path), "exec"), ns)
    except Exception as exc:
        print(f"Warning: could not read live map pool from {features_path}: {exc}", file=sys.stderr)
        return None
    mapping = ns.get("MAP_ID_TO_NAME")
    if not isinstance(mapping, dict) or not mapping:
        return None
    return sorted(mapping.values())


def resolve_maps(
    maps_dir: Path,
    override: list[str] | None,
    bot_dir: Path | None = None,
    all_maps: bool = False,
) -> list[Path]:
    """Explicit --maps override wins outright. Otherwise the bot's own live pool
    (features.py's MAP_ID_TO_NAME) — unless all_maps forces every *.map26 in
    maps_dir regardless of pool membership, or bot_dir is None / has no
    features.py, in which case that's the only option left."""
    if override:
        maps = []
        for name in override:
            path = maps_dir / name
            if not path.suffix:
                path = path.with_suffix(".map26")
            if not path.exists():
                raise FileNotFoundError(f"Map not found: {path}")
            maps.append(path)
        return maps

    if all_maps:
        return sorted(maps_dir.glob("*.map26"))

    pool = live_map_pool(bot_dir) if bot_dir is not None else None
    if pool:
        maps = []
        missing = []
        for name in pool:
            path = maps_dir / f"{name}.map26"
            if path.exists():
                maps.append(path)
            else:
                missing.append(name)
        if missing:
            print(
                f"Warning: live map pool names not found in {maps_dir}: {missing}",
                file=sys.stderr,
            )
        return maps
    return sorted(maps_dir.glob("*.map26"))
