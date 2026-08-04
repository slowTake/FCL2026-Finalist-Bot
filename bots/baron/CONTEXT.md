# Baron domain glossary

For **where code lives**, see `AGENT.md` Key. For **behavior phases**, see task lists at the top of `builder.py`.

## Builder role

**default** or **harasser**. Assignment: `hivemind.my_index > 0 and my_index % 3 == 0` → harasser (builders 3, 6, 9, …); else default.

## Default

Economy and home duties orchestrated by `builder.runDefault`: `buildHarvester` → `maintainHarvester` → `defGun` → `defendHome` → `healAlly` → `recheckOreNodes`.

## Harasser

Offense role (`offense.runHarasser`): `avoidFire` → `offGun` → `seekHarvester` → `seekCore`. No economy. `seekHarvester` reads HiveMind `local_map` for enemy harvesters and ore nodes to scout/clear; visiting tiles refreshes the map via vision.

## Spawn

Core rushes `MAX_BUILDERS_EARLY`, then produces **+1 builder every `SPAWN_INTERVAL` (100) turns**, capped at `MAX_BUILDERS`. Registry is HiveMind slot 4.

## DefGun

Near-core defensive **gunner** placement only (`defence.defGun` → `guns.placeGun`). Never places sentinels. Skipped while relaying or rechecking ore.

## RecheckOreNodes

Last-resort helper: clears sticky `oreSite` / path cache and picks a fresh empty ore or harvester so builders keep maintaining/stealing after relays finish.

## DefendHome

Near-core only (Chebyshev 8 of footprint). Skipped while `inRelay` or `recheckingOre`. Attacks enemy gunner/sentinel/builder in vision; otherwise heals core if HP &lt; 500.

## MaintainHarvester / checkOre

Sticky ore site inspection: empty → build; harvester → `relayToCore`; ally infra on ore → destroy; enemy infra → gun stub.

## RelayToCore

Inbound conveyor chain from ore/harvester to ally-core-adjacent tiles. Orchestrator plus facing/clear/place helpers. Belts face next hop; corners face the new outbound direction. Wrong-facing ally belts destroyed; enemy belts cleared by builder fire.

## Task list

Ordered phases inside a role; first match wins each turn.

## Guns layer

`placeGun` / `gunnerLoop` for gunners (destroyable-transparent LoS). `placeSentinel` / `sentinelLoop` used by harasser `offGun` toward enemy core; not by `defGun`.

## HiveMind v2

Delta ledger + metronome (`components/hivemind_v2.py`). Builder count in slot **4**; deltas 5–12; metronome 13; map id 14; packed cores 15. Player owns slots **0–3** only.
