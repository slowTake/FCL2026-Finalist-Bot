# Magnum Grokus

Map-aware turtle fork with obstacle-aware L-path movement and profile-driven policy.

`main.py` is **self-contained and league-ready** — all helpers from `components/features.py`, `components/vision.py`, `components/hivemind.py`, `florent_lib.py`, and `conveyor_pathing.py` are inlined with section headers. Upload only `main.py` to the league.

The inlined HiveMind and vision sections are kept in sync with the canonical modules in [`components/`](../../components/). When those change upstream, re-sync the matching section here.

## Features

- **Map detection** (slots 14–15): fingerprint lookup from inlined map data
- **Map profiles** (slot 4): CloseQuarter (sprint/duel), Choke, OpenLarge, Default
- **Stage** (slot 5): OPENING / EXPAND / DEFEND driven by harvester count
- **Movement**: BFS over HiveMind `local_map`, with `make_path_ct` L-path fallback
- **Relay belts**: BFS conveyor path from harvester to core (ore-aware), planned
  once and committed — replanning every turn caused opposite-route pacing
- **Gunners**: skip friendly targets (`get_gunner_target` can return our own belts)
- **Close-quarter**: early gunners, harvester-flank placement, gunner cap (slot 6)

## Store slots

Slots 0–6 belong to the bot's own policy; 7–15 are reserved by HiveMind. Nothing may
be written to a HiveMind slot from `Player`.

| Slot | Owner | Purpose |
|------|-------|---------|
| 0 | Player | Spawn credits (earned by completing a relay) |
| 1 | Player | Orphaned relay origin, packed position |
| 2 | Player | Harvester count |
| 3 | Player | Shared ore position |
| 4 | Player | Map profile enum |
| 5 | Player | Stage enum |
| 6 | Player | Gunner count (close-quarter cap) |
| 7 | HiveMind | Builder registry / spawn counter |
| 8–9 | HiveMind | Enemy intel targets |
| 10–12 | HiveMind | Packed 9x9 vision grid |
| 13 | HiveMind | Broadcast metronome |
| 14 | HiveMind | Map ID (1–15) |
| 15 | HiveMind | Packed ally/enemy core positions |

Ally core position is read from slot 15 via `unpack_cores`, not stored separately.

## Run

```bash
source .venv/bin/activate
fcode run magnum_grokus starter
fcode run magnum_grokus turtle maps/sprint.map26
fcode watch replay.replay26
```

Gauntlet:

```bash
python scripts/bot_gauntlet.py --bot magnum_grokus
```

**A single gauntlet score is not a reliable measurement.** The bot calls
`random.shuffle`/`random.choice` and never calls `random.seed`, so the engine's
`seed` does not control the bot's own randomness — the same code can score 15/15
on one run and 12/15 on the next. Most matches also reach turn 1000 with
near-identical titanium and are settled by tiebreak, so a single flipped coin
moves the score.

To compare versions more reliably, average over many games with
`multiseed_eval.py` (every map × 5 seeds, same CLI as gauntlet):

```bash
python scripts/multiseed_eval.py --bot magnum_grokus
python scripts/multiseed_eval.py --bot magnum_grokus --maps atoll vault
```

## Dev branch

Work targets `dev/magnum_grokus`. Local experiments: `local_bots/magnum_grokus/` (gitignored).

`conveyor_pathing.py` is kept as a reference copy; **`main.py` is canonical** for submission and local runs.
