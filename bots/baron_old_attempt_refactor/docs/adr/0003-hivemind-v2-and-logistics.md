# ADR 0003: HiveMind v2 and default logistics

## Status

Accepted.

## Context

Baron needs HiveMind v2’s delta ledger and a lean default economy (harvester + inbound relay) without Earl’s role menu or gun placement yet.

## Decision

- Symlink `hivemind.py` → `hivemind_v2.py` and add `vision_v2.py` (import name required by v2).
- Player store **0–3** only; HiveMind owns **4–15** (builder count on **4**).
- Default task order: `buildHarvester` → `maintainHarvester` (`checkOre` / `relayToCore`) → `idleHome` → `defendHome` → `healAlly`.
- `relayToCore` is an orchestrator; belt facing helpers validate or clear (ally destroy / enemy fire) then place.
- Public API: camelCase; main → helpers pattern (see `AGENT.md`).
- `placeGunnerOnOre` stubbed; no guns logic this change.

## Consequences

- Slot 4 is no longer free for Player.
- Architecture doc’s aspirational “core memory 0–4” is not implemented; fingerprint/cores remain on 14/15.
