# Baron domain glossary

For **where code lives**, see `AGENT.md` Key. For **behavior phases**, see task lists at the top of `builder.py`.

## Builder role

**default** or **harasser**. Assignment: `hivemind.my_index > 0 and my_index % 3 == 0` → harasser (builders 3, 6, 9, …); else default.

## Default

Economy and home duties orchestrated by `builder.runDefault`: `buildHarvester` → `maintainHarvester` → `defGun` → `defendHome` → `healAlly` → `recheckOreNodes`.

## Harasser

Offense role (`builder.runHarasser`, helpers in `offense.py`): `avoidFire` → `offGun` → `seekHarvester` → `seekCore`. No economy. Assignment: every 3rd builder (`my_index % 3 == 0`), or **all** builders once a wave gate fires (`WAVE_ATTACK_TURN` / `WAVE_ATTACK_TI` in `main.py`; `≤0` or `None` = unset).

## Wave attack

Tunable in `main.py`. Positive turn → all harass from that round; positive Ti → all harass when bank ≥ threshold. Either gate alone is enough.

## BuildHarvester

Places on empty ore (caps + not in relay). Skips a node if a **visible enemy gunner** is currently aiming at that tile (`oreThreatenedByGunner`).

## Spawn

Core rushes `MAX_BUILDERS_EARLY`, then +1 builder on an effective interval: base `SPAWN_INTERVAL`, shortened by `SPAWN_INTERVAL_BY_TI` tiers when the Ti bank is rich (capped at `MAX_BUILDERS`). Registry is HiveMind slot 4.

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
