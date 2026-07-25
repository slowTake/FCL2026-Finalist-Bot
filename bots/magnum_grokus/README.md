# Magnum Grokus

Florent Code League bot — v1 economy loop:

- Full Harvester → Core chains with **Splitter** last-mile
- Spiral exploration from core; ore shared via store
- v1.3: strict CHAIN_OK BFS, no vision-skip gaps, stall recovery, conveyor-first corners
- Heal when action free

Design: `docs/superpowers/specs/2026-07-22-magnum-grokus-design.md`

## Replay analysis

```bash
pip install protobuf
fcode run magnum_grokus starter maps/fjord.map26 --replay /tmp/fjord.replay26 --seed 1
cd scripts && PYTHONPATH=. python analyze_replay.py /tmp/fjord.replay26
```

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
