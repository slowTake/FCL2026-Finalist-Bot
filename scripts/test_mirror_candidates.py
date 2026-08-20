#!/usr/bin/env python3
"""Offline check for raid._mirrorCandidates against every map in KNOWN_MAPS.

The mirror fallback (raid.enemyCorePos tier 3) is what aims raiders on a map we
can't fingerprint — the finals case where every fingerprint misses. It cannot be
exercised by mirror_gauntlet: LIVE_MAP_POOL is entirely point-symmetric AND
fingerprinted, so the gauntlet never leaves tier 2, let alone reaches a wrong
mirror guess. This test is the regression guard for that blind spot.

Two invariants, checked against all 44 maps (both team orientations = 88 rows):

  1. The real enemy core is ALWAYS among the generated candidates. This is the
     property the whole design rests on — the old point-only formula failed it on
     8 maps (bridge, longship, meander, pinch, strait, sweden, twins, vase).
  2. For every LIVE_MAP_POOL map the FIRST candidate is already the real core, so
     the current ladder never leaves the best guess — the candidate ordering
     cannot regress point-symmetric play.

Run: python scripts/test_mirror_candidates.py   (exit 0 = pass, 1 = fail)
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "bots" / "count"))
sys.path.insert(0, str(_ROOT / "bots" / "count" / "hivemind"))

from features import KNOWN_MAPS, LIVE_MAP_POOL  # noqa: E402
from raid import _mirrorCandidates  # noqa: E402


def main() -> int:
    failures: list[str] = []
    for (w, h, _team, ax, ay, _wc), (_mid, name, ex, ey) in KNOWN_MAPS.items():
        cands = [(c.x, c.y) for c in _mirrorCandidates(w, h, ax, ay)]

        # Invariant 1: real core must be reachable as some candidate.
        if (ex, ey) not in cands:
            failures.append(
                f"{name}: real enemy core ({ex},{ey}) not in candidates {cands} "
                f"(our anchor ({ax},{ay}), map {w}x{h})"
            )

        # Invariant 2: on the live pool, the first guess must already be right.
        if name in LIVE_MAP_POOL and cands and cands[0] != (ex, ey):
            failures.append(
                f"{name} (LIVE_MAP_POOL): first candidate {cands[0]} != real "
                f"enemy core ({ex},{ey}) — ordering would regress the ladder"
            )

    total = len(KNOWN_MAPS)
    if failures:
        print(f"FAIL: {len(failures)} problem(s) across {total} map orientations:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"OK: real enemy core is among the mirror candidates on all {total} "
          f"map orientations; first candidate correct on every LIVE_MAP_POOL map.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
