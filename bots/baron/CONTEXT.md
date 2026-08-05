# Baron domain glossary

For **where code lives**, see `AGENT.md` Key. For **behavior phases**, see task lists at the top of `builder.py`.

## Builder role

**default** or **harasser**. Assignment: `hivemind.my_index > 0 and my_index % 2 == 0` → harasser (builders 2, 4, 6, …); else default. Wave gates switch all to harasser.

## Threat recall

Core latches `SLOT_THREAT` with HP hysteresis (`THREAT_CORE_HP_ON` / `OFF` in `main.py`: set when HP &lt; ON, clear when HP ≥ OFF, sticky in between). While set, **default** builders run `runThreatHome`: abort relay (even mid first relay) → move to Chebyshev 4 of core → `_reactEnemyTurret` / `defGun` / `defendHome` / `healAlly`. No economy. Harassers ignore the flag.

## Default

Economy and home duties orchestrated by `builder.runDefault` (only when threat is clear). First: if any enemy gunner/sentinel is in vision (and the first relay is already complete, or the builder is not mid-first-relay), abort relay and contest (`contestTurret` / `defGun` / `defendHome` / `healAlly`) — skip economy that turn. For `BUILDER_HOME_TURNS` after `birth_round` (default path only): defend-first + near-core economy. After that: `buildHarvester` → `maintainHarvester` → `defGun` → `defendHome` → `healAlly` → `recheckOreNodes`. Knob `≤0` disables the early phase. Harassers never run this phase.

## Harasser

Offense role (`builder.runHarasser`, helpers in `offense.py`): `offGun` → `seekHarvester` → `seekCore`. No economy. Assignment: every even builder (`my_index % 2 == 0`), or **all** builders once a wave gate fires (`WAVE_ATTACK_TURN` / `WAVE_ATTACK_TI` in `main.py`; `≤0` or `None` = unset). Unaffected by default’s early defend phase and by `SLOT_THREAT` recall.

## Wave attack

Tunable in `main.py`. Positive turn → all harass from that round; positive Ti → all harass when bank ≥ threshold. Either gate alone is enough.

## BuildHarvester

Places on empty ore (not in relay). Skips a node if a **visible enemy gunner** is currently aiming at that tile (`oreThreatenedByGunner`). No store-based harvester cap.

## Spawn

Core rushes `MAX_BUILDERS_EARLY`, then +1 builder on an effective interval: base `SPAWN_INTERVAL`, shortened by `SPAWN_INTERVAL_BY_TI` tiers when the Ti bank is rich (capped at `MAX_BUILDERS`). Registry is HiveMind slot 4.

## DefGun

Near-core defensive **gunner** placement only (`defence.defGun` → `guns.placeGun`). Never places sentinels. Skipped while relaying or rechecking ore. Gunner/sentinel pads never on ore.

## RecheckOreNodes

Last-resort helper: clears sticky `oreSite` / path cache and picks a fresh empty ore or harvester so builders keep maintaining/stealing after relays finish.

## DefendHome

Near-core only (Chebyshev 4 of footprint). Skipped while `inRelay` or `recheckingOre`. Attacks enemy gunner/sentinel/builder in vision; otherwise heals core if HP &lt; 500.

## MaintainHarvester / checkOre

Sticky ore site inspection: empty → build; harvester → `relayToCore`; ally infra on ore → destroy (gunner/sentinel skipped only if that ore is threatened by an enemy gunner); enemy infra → gun stub. Recheck prefers empty → clearable ally-blocked → harvester.

## RelayToCore

Inbound conveyor chain from ore/harvester to ally-core-adjacent tiles. Aborts on enemy gunner/sentinel in vision **only after** the first relay has completed (`SLOT_RELAY_COMPLETE`); until then the first belt is protected. Default also contests turrets before economy once that flag is set. Orchestrator plus facing/clear/place helpers. Belts face next hop; corners face the new outbound direction. Wrong-facing ally belts destroyed; enemy belts cleared by builder fire.

## Task list

Ordered phases inside a role; first match wins each turn.

## Guns layer

`placeGun` / `gunnerLoop` for gunners (destroyable-transparent LoS). `placeSentinel` / `sentinelLoop` used by harasser `offGun` toward enemy core; not by `defGun`. Sentinel pads require a facing that hits the **2x2 core footprint** (nearest footprint tile; no NORTH fallback). Pads skip ore tiles, tiles on an ally gunner’s facing ray (8-dir belt-style), and Chebyshev-1 neighbors of ally gunners. `offGun` skips placing another gunner on an enemy harvester already covered by an ally gunner (`allyGunCovering`).

## HiveMind v2

Delta ledger + metronome (`components/hivemind_v2.py`). Builder count in slot **4**; deltas 5–12; metronome 13; map id 14; packed cores 15. Player owns slots **0–3** (0 = first relay done; 1 = `SLOT_THREAT` core HP latch; 2–3 free).
