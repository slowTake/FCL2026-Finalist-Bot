# Magnum Grokus — Bot Design

Date: 2026-07-22 (v1 simplified 2026-07-25, v1.1 L-path 2026-07-25)  
Status: v1.2 — chain gating, one builder per route, wait-on-gap  
Location: `bots/magnum_grokus/`

## Goal (v1)

A Florent Code League bot with a **working economy loop**: explore for ore, build harvesters, lay complete conveyor chains home, finish with a splitter facing the core. All builders share one brain.

## Non-goals (v1)

- Store roles (Scout / Miner / Router / Defender / Raider)
- Bounded BFS pathfinding
- Gunners, ammo feed, raider sabotage
- Sentinels / Launchers / map-specific layouts
- `convert_ammo` (not present in fcode 2.2.0)

Deferred to v2 after logistics pass + replay feedback.

## Layout

```
bots/magnum_grokus/
  main.py       # single-file Player + helpers
  README.md     # run instructions
```

Run: `fcode run magnum_grokus starter`

## Store protocol (v1)

| Slot | Name | Purpose |
| --- | --- | --- |
| 0–1 | CORE_X / CORE_Y | Core writes every round |
| 2 | HARVESTER_COUNT | team harvester tally |
| 3 | ORE_LOCATION | packed shared ore |
| 4 | CHAIN_OK | 1 when active harvester chain verified to core |
| 5 | ACTIVE_HARVESTER | packed pos of harvester being routed |
| 6 | ROUTE_OWNER | entity id of builder owning the active route |
| 7–15 | reserved | future coordination (roles, etc.) |

Writes are buffered (visible next round). Pack positions with +1 offset so 0 means empty.

## Unit behavior

### Core

- Publish CORE_X/Y every round
- Spawn up to 5 builders when affordable

### Builder (all share one brain)

- `can_X` before `X`; bounds-check all tile queries
- Stuck ≥3 rounds → pick new target
- Heal damaged friendlies when action free

**Action phase:**
1. Build harvester on adjacent ore only when `CHAIN_OK` or no active route → set owner, `CHAIN_OK=0`, first hop, `routing=True`
2. If route owner and routing: repair first hop, place core splitter only when chain trace passes
3. Heal

**Team rules:**
- Only `ROUTE_OWNER` routes and lays belts; other builders explore only
- No second harvester while `CHAIN_OK=0` and a route is in progress
- Routing move: wait on empty next tile until belt/splitter is placed (no skipping gaps)

**Move phase:**
- Explore: walk toward ore / shared ore / random target — no belt laying
- Route home (`routing=True`): L-path cardinal steps (larger delta first; flip axis if stuck)
- Lay belts/splitters only while routing; facing = travel direction along path
- Corner tiles: conveyor or splitter facing the next leg (`leg_after` from L-path)
- Splitter facing core on core-adjacent tiles (last mile)

### Gunner

- Passive auto-fire only (no builder placement in v1)

## Success criteria (v1)

1. `fcode run magnum_grokus starter` completes 1000 rounds with no GameErrors from grokus
2. Nonzero mined titanium on every gauntlet map
3. Gauntlet score improves from 7/15 baseline

## Deferred design (v2)

See original mid-ladder spec in git history: roles, BFS, gunners + splitter ammo feed, heal priority, raider sabotage.
