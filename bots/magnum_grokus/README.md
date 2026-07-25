# Magnum Grokus

Florent Code League bot — v1 economy loop:

- Full Harvester → Core chains with **Splitter** last-mile
- Spiral exploration from core; ore shared via store
- v1.2: one chain at a time, route owner, wait-on-gap, continuity check before `CHAIN_OK`
- Heal when action free

Design: `docs/superpowers/specs/2026-07-22-magnum-grokus-design.md`

## Run

```bash
source .venv/bin/activate
fcode run magnum_grokus starter
fcode watch replay.replay26
```

Specific map:

```bash
fcode run magnum_grokus starter maps/quarry.map26
```

Gauntlet:

```bash
python scripts/bot_gauntlet.py --bot magnum_grokus
```
