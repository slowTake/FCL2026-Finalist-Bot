# Magnum Grokus

Map-aware turtle fork with obstacle-aware L-path movement and profile-driven policy.

`main.py` is **self-contained and league-ready** — all helpers from `features.py`, `vision.py`, `florent_lib.py`, `conveyor_pathing.py`, and `hivemind.py` are inlined with section headers. Upload only `main.py` to the league.

## Features

- **Map detection** (slots 14–15): fingerprint lookup from inlined map data
- **Map profiles** (slot 4): CloseQuarter (sprint/duel), Choke, OpenLarge, Default
- **Stage** (slot 5): OPENING / EXPAND / DEFEND driven by harvester count
- **Movement**: BFS over HiveMind `local_map`, with `make_path_ct` L-path fallback
- **Relay belts**: BFS conveyor path from harvester to core (ore-aware)
- **Close-quarter**: early gunners, harvester-flank placement, gunner cap (slot 6)

## Store slots

| Slot | Purpose |
|------|---------|
| 0–1 | Core X/Y |
| 2 | Harvester count |
| 3 | Shared ore position |
| 4 | Map profile enum |
| 5 | Stage enum |
| 6 | Gunner count (close-quarter cap) |
| 7 | Builder spawn counter (HiveMind) |
| 8–12 | Vision/intel broadcast (HiveMind) |
| 14 | Map ID (1–15) |
| 15 | Packed ally/enemy core info |

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

## Dev branch

Work targets `dev/magnum_grokus`. Local experiments: `local_bots/magnum_grokus/` (gitignored).

`conveyor_pathing.py` is kept as a reference copy; **`main.py` is canonical** for submission and local runs.
