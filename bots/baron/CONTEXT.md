# Baron domain glossary

For **where code lives**, see `AGENT.md` Key. For **behavior phases**, see task lists at the top of `builder.py`.

## Builder role

**default**, **guardian**, or **harasser**. Assignment from `hivemind.my_index` (`> 0`, `idx % 5`): `1` or `0` → guardian (1, 5, 6, 10, …); `2` or `4` → harasser (2, 4, 7, 9, …); `3` → default (3, 8, 13, …). Per five spawns: 2 guardians / 2 harassers / 1 default. Wave gates switch non-guardians to harasser; guardians stay home.

## Guardian

Home defence role (`builder.runGuardian`, helpers in `defence.py`): leash (Chebyshev 4) → `_reactEnemyTurret` → `defGun` → `defendHome` → `healAlly` → `clearBarriers` → `patrolHome`. No economy. Assignment: `idx % 5 in (0, 1)`. Ignores wave. Home defence is entirely the guardians' job — there is no core-HP recall pulling other roles home.

## Default

Economy and home duties orchestrated by `builder.runDefault`; runs regardless of ally core HP. First: if any enemy gunner/sentinel is in vision **and the builder is not mid-relay**, contest (`contestTurret` / `defGun` / `defendHome` / `healAlly`) — skip economy that turn. A builder that is `inRelay` never breaks off to fight: it dodges instead (`_dodgeWhileRelaying` → `avoidFire` toward the ally core when standing in a gunner's LoS), and the relay itself is dropped only per-tile inside `relayToCore`. For `BUILDER_HOME_TURNS` after `birth_round` (default path only): defend-first + near-core economy. After that: `buildHarvester` → `maintainHarvester` → `defGun` → `defendHome` → `healAlly` → `recheckOreNodes`. Knob `≤0` disables the early phase. Harassers and guardians never run this phase.

## Harasser

Offense role (`builder.runHarasser`, helpers in `offense.py`): `snipeHarvester` → `offGun` → `avoidFire` → `denyBelt` → `seekHarvester` → `seekCore`. No economy. Assignment: `idx % 5 in (2, 4)`, or **non-guardian** builders once a wave gate fires (`WAVE_ATTACK_TURN` / `WAVE_ATTACK_TI` in `main.py`; `≤0` or `None` = unset). Unaffected by default’s early defend phase. `snipeHarvester` places a gun on the nearest visible uncovered enemy harvester; `avoidFire` steps to safe tiles that still advance toward `harassTarget` / enemy core. `denyBelt` attacks enemy logistics two ways: **Mode A** plants a barrier on the empty tile one step ahead of an enemy belt's facing (blocks throughput, no fight); **Mode B**, only while gun placement is unaffordable (`_canAffordGunPlace`), cuts an enemy belt from an adjacent tile and barriers the cleared tile in place — staying adjacent means the barrier goes down the moment the belt dies, with no move-on / step-off. Sticky target via `bot.denyBeltTile`; both modes skip ore tiles nearer our core than the enemy's (`_oreCloserToAlly`) so we never wall off our own expansion. Target selection is staggered by `harasserRank` so harassers spread across belts. `seekCore` approaches the enemy core then orbits mid-side / ring waypoints (corner-safe) so harassers keep moving.

## Wave attack

Tunable in `main.py`. Positive turn → non-guardians harass from that round; positive Ti → non-guardians harass when bank ≥ threshold. Either gate alone is enough. Guardians ignore wave.

## BuildHarvester

Places on empty ore (not in relay). With `ORE_STAGGER = False` (default), picks the most convenient empty pad `(dist to core, dist to self)`. With `ORE_STAGGER = True`, index-staggers economy defaults (`idx % 5 == 3`) over ores sorted by core distance. Pads with a visible ally builder **on the ore tile** are skipped (gap-fill if all claimed). Sticky `oreSite` is kept until invalid. Also skips a node if a **visible enemy gunner** is aiming at that tile (`oreThreatenedByGunner`). No store-based harvester cap.

## Spawn

Core rushes `MAX_BUILDERS_EARLY`, then +1 builder on an effective interval: base `SPAWN_INTERVAL`, shortened by `SPAWN_INTERVAL_BY_TI` tiers when the Ti bank is rich (capped at `MAX_BUILDERS`). Registry is HiveMind slot 4.

## DefGun

Near-core defensive **gunner** placement only (`defence.defGun` → `guns.placeGun`). Never places sentinels. Skipped while relaying or rechecking ore. Gunner/sentinel pads never on ore.

## BreakOreDeadlock

Anti-livelock for economy defaults (`logistics.breakOreDeadlock`, knob `ORE_DEADLOCK_TURNS` in `main.py`; `≤0` disables). `bot.stuck` counts turns with no position change (maintained in `main.py`) and was previously read only by harasser orbit and guardian patrol. When a default sits motionless for `ORE_DEADLOCK_TURNS` **outside a relay**, it blacklists its `oreSite` into `skippedOres`, clears sticky site / path cache / relay state, and re-arms the counter. State-only — it costs no turn, and the economy steps below it re-pick immediately. Relays are exempt (`relayStuck` already covers those). Needed because `_clearStickyIfAllyClaimed` exempts any builder already on the ore or cardinally adjacent to it (`_selfNearOre`, manhattan ≤ 1), so a cluster that forms on one node never yields it. `pickEmptyOre` also honours `skippedOres` (falling back to the full pool if that would empty it) so a blacklisted node isn't immediately re-picked.

## RecheckOreNodes

Last-resort helper: clears sticky `oreSite` / path cache and picks a fresh empty ore or harvester so builders keep maintaining/stealing after relays finish.

## DefendHome

Near-core only (Chebyshev 4 of footprint). Skipped while `inRelay` or `recheckingOre`. Attacks enemy gunner/sentinel/builder in vision; otherwise heals core if HP &lt; 500.

## ClearBarriers

Guardian helper: nearest enemy barrier in vision (prefer near-core) → adjacent fire, else `moveToward`.

## PatrolHome

Guardian idle: cycle four mid-side waypoints around the ally core footprint inside the leash so rear approaches enter vision.

## MaintainHarvester / checkOre

Sticky ore site inspection: empty → build; harvester → `relayToCore`; ally infra on ore → destroy (gunner/sentinel skipped only if that ore is threatened by an enemy gunner); enemy infra → gun stub. Recheck prefers empty → clearable ally-blocked → harvester.

## RelayToCore

Inbound conveyor chain from ore/harvester to ally-core-adjacent tiles. Aborts **only** when the next belt tile is under a visible enemy gunner's LoS/arc (`oreThreatenedByGunner`, the same predicate `buildHarvester` uses to skip ore) — so titanium is never spent into a firing line, but an enemy turret merely being in vision no longer drops the belt. Other aborts are failure exits: no ally core, no viable path, path adjacent to the enemy core, or `relayStuck >= 3`. Orchestrator plus facing/clear/place helpers. Belts face next hop; corners face the new outbound direction. Wrong-facing ally belts destroyed; enemy belts cleared by builder fire.

## Task list

Ordered phases inside a role; first match wins each turn.

## Guns layer

`placeGun` / `gunnerLoop` for gunners (destroyable-transparent LoS except **barriers**, which are opaque). Rotate uses engine `can_fire_from` only so ally barriers force a re-aim; enemy barriers stay on the fire priority list. `placeSentinel` / `sentinelLoop` used by harasser `offGun` toward enemy core; not by `defGun`. Sentinel pads require a facing that hits the **2x2 core footprint** (nearest footprint tile; no NORTH fallback) and prefer pads nearer the core, then nearer the builder. Pads skip ore tiles, tiles on an ally gunner’s facing ray (8-dir belt-style), and Chebyshev-1 neighbors of ally gunners. `offGun` skips placing another gunner on an enemy harvester already covered by an ally gunner (`allyGunCovering`).

## HiveMind v2

Delta ledger + metronome (`components/hivemind_v2.py`). Builder count in slot **4**; deltas 5–12; metronome 13; map id 14; packed cores 15. Player owns slots **0–3** (0 = first relay done; 1–3 free).
