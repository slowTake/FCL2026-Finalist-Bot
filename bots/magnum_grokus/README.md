# Magnum Grokus

Mid-ladder Florent Code League bot following `STRATEGY.md`:

- Full Harvester → Core chains with **Splitter** last-mile
- Conveyor/splitter-fed Gunner ammo (fcode 2.2)
- Store roles (Scout / Miner / Router / Defender / Raider) + ore claims
- Bounded BFS pathfinding
- Heal + logistics sabotage

Portable under `local_bots/` (gitignored). Design: `docs/superpowers/specs/2026-07-22-magnum-grokus-design.md`.

## Run

```bash
source .venv/bin/activate
fcode run local_bots/magnum_grokus starter
fcode watch replay.replay26
```

Vs turtle:

```bash
fcode run local_bots/magnum_grokus local_bots/turtle
```

## Lift out

```bash
cp -r local_bots/magnum_grokus ~/somewhere/magnum_grokus
```
