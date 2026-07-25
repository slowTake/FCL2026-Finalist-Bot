# Fix Logistics Regression (v1.2 → v1.3)

> **Cloud agent:** use **Composer 2.5**. Escalate to Sonnet thinking only if verification fails twice.

**Goal:** Fix fjord-style 0-mined broken chains without regressing quarry or gauntlet score.

**Regression gate:** quarry `mined > 0` AND gauntlet `>= 11/15` (min of 3 runs).

**Branch:** `7-adding-turtle`

## Current state (v1.2)

[`bots/magnum_grokus/main.py`](../../bots/magnum_grokus/main.py) has route owner, wait-on-gap, and BFS chain check — but:

1. `_chain_reaches_core()` false-positives on any conveyor near core
2. `_route_tile_ready()` skips empty out-of-vision tiles (creates gaps)
3. `_can_build_harvester_team()` can stall team at 1 harvester forever
4. Splitter-heavy placement wastes Ti

Fjord replay (seed 1): 1 harvester, 3 conveyors, 6 splitters, 0 Ti collected.

## Tasks

- [ ] **strict-chain-check** — Fix `_chain_reaches_core()` and `_try_finish_routing()`; no blanket conveyor-near-core shortcut; never mark CHAIN_OK without active harvester
- [ ] **no-vision-gaps** — `_route_tile_ready()`: do not move onto empty out-of-vision tiles during routing
- [ ] **stall-recovery** — `SLOT_CHAIN_STALL` (slot 7): timeout + route reclaim after 30 rounds stuck
- [ ] **splitter-discipline** — Conveyors on straights/corners; splitters only at core + true aligned corners
- [ ] **replay-analyzer** — Add `scripts/analyze_replay.py` (mined, harvester count, conveyor/splitter ratio)
- [ ] **verify-gauntlet** — fjord + quarry replays, then gauntlet 3×

## Do NOT change

- Routing-only belt laying (no explore-time toward-core belts — breaks quarry)
- `l_path_step()` larger-delta-first + y_first toggle on stuck
- Harvester placement scoring (builder on first hop tile)

## Verify

```bash
source .venv/bin/activate
pip install protobuf  # for replay scripts

# Quick replay checks
fcode run magnum_grokus starter maps/fjord.map26 --replay /tmp/fjord.replay26 --seed 1
fcode run magnum_grokus starter maps/quarry.map26 --replay /tmp/quarry.replay26 --seed 1
cd scripts && PYTHONPATH=. python decode_replay.py /tmp/fjord.replay26
# After analyze_replay.py exists:
PYTHONPATH=. python analyze_replay.py /tmp/fjord.replay26 /tmp/quarry.replay26

# Full gate
python scripts/bot_gauntlet.py --bot magnum_grokus  # run 3×, min score >= 11/15
```

## Files to change

| File | Change |
|------|--------|
| `bots/magnum_grokus/main.py` | Strict BFS, no vision gaps, stall recovery, fewer splitters |
| `docs/superpowers/specs/2026-07-22-magnum-grokus-design.md` | Document v1.3 chain rules |
| `scripts/analyze_replay.py` | New replay assertions |
| `bots/magnum_grokus/README.md` | Document analyze usage |
