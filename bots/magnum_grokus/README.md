# Magnum Grokus

Map-aware turtle fork with obstacle-aware L-path movement and profile-driven policy.

## Features

- **Map detection** (slots 14–15): fingerprint lookup from `map_detector_bot/features.py`
- **Map profiles** (slot 4): CloseQuarter (sprint/duel), Choke, OpenLarge, Default
- **Stage** (slot 5): OPENING / EXPAND / DEFEND driven by harvester count
- **Movement**: `florent_lib.make_path_ct` with greedy fallback
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
