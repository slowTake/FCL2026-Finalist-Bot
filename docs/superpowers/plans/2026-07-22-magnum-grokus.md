# Magnum Grokus Implementation Plan

> **For agentic workers:** implement task-by-task. Steps use checkbox syntax.

**Goal:** Reliable logistics bot v1 at `bots/magnum_grokus/` per simplified spec.

**Architecture:** Single-file `Player` — starter-plus with routing flag, spiral explore, conveyor chains while routing home, splitter last-mile at core.

**Tech Stack:** Python 3 + `fcode` 2.2 Controller API.

## Global Constraints

- Live under `bots/magnum_grokus/`
- `can_X` before `X`; bounds-check tile queries
- Lay conveyors only while `routing == True`
- Max 5 builders
- Verify: nonzero mined on all maps; no GameErrors from grokus

## Tasks

- [x] Scaffold `main.py` + README (constants, Store, pack/unpack, in_bounds)
- [x] Core: publish pos, spawn builders
- [x] Builder: harvester → route home laying chain → splitter at core
- [x] Spiral exploration from core; share ore via store
- [x] Heal when action free
- [ ] Replay feedback on still-broken maps (user-driven v1.1)
- [ ] Deferred v2: roles, BFS, gunners, raider sabotage

## Verify

```bash
fcode run magnum_grokus starter
fcode run magnum_grokus starter maps/quarry.map26
python scripts/bot_gauntlet.py --bot magnum_grokus
```
