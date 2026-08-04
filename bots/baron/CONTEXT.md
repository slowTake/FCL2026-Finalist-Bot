# Baron domain glossary

For **where code lives**, see `AGENT.md` Key. For **behavior phases**, see task lists at the top of `builder.py`.

## Builder role

**default** or **raider**. Assignment TBD; all builders run `runDefault` today.

## Default

Economy and home duties in `logistics.py`: `buildHarvester` → `maintainHarvester` → `defendHome` → `healAlly` → `recheckOreNodes`.

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

Deferred for placement. Living turret stubs only in `guns.py`.

## HiveMind v2

Delta ledger + metronome (`components/hivemind_v2.py`). Builder count in slot **4**; deltas 5–12; metronome 13; map id 14; packed cores 15. Player owns slots **0–3** only.
