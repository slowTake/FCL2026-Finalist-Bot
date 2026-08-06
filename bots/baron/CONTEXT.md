# Baron domain glossary

For **where code lives**, see `AGENT.md` Key. For **behavior phases**, see task lists at the top of `builder.py`.

## Builder role

**default**, **guardian**, or **harasser**. Assignment from `hivemind.my_index` (`> 0`, `idx % 5`): `1` or `0` → guardian (1, 5, 6, 10, …); `2` or `4` → harasser (2, 4, 7, 9, …); `3` → default (3, 8, 13, …). Per five spawns: 2 guardians / 2 harassers / 1 default. Wave gates switch non-guardians to harasser; guardians stay home.

## Guardian

Home defence role (`builder.runGuardian`, helpers in `defence.py`): leash (Chebyshev 4) → `_reactEnemyTurret` (`skipEconomy=False`, `homeOnly=True`) → `defGun` → `defSentinel` → `defendHome` → `healAlly` → *[not `underAttack`]* `clearBarriers` → *[not `underAttack`]* **idle economy** → `patrolHome`. `_reactEnemyTurret`'s terminal "consume the turn anyway" exists only to stop `maintainHarvester` re-entering a relay, so guardians opt out (`skipEconomy=False`) — otherwise a turret they cannot act on (bank under `GUN_PLACE_MIN_TI`, target outside `_nearCore`, nothing adjacent, nothing damaged) freezes them out of `clearBarriers` / `patrolHome` for as long as it stays visible. Assignment: `idx % 5 in (0, 1)`. Ignores wave. Home defence is entirely the guardians' job — there is no core-HP recall pulling other roles home.

`underAttack` gates the two chores at the bottom. It reads `_enemyUnitInVision` (turret **or** builder) normally, but falls back to `enemyTurretInVision` (turret only) until the team's first relay completes (`logistics.relayEverComplete`, HiveMind slot 0). Before there is any economy, an enemy builder merely in vision must not keep a guardian off ore; after that, the stricter reading protects what exists. The same variable also decides whether a visible enemy drops the guardian's relay claim.

**No builder-attack.** Guardians never poke a turret with builder fire: 1.0 Ti/dmg against a gunner's 0.20, and the `moveToward` approach walked a 40 HP builder into something dealing 10 dmg/turn at sqrt(13). Turrets answer turrets — `defGun` inside the leash, `defSentinel` outside. The only builder fire left in the chain is `clearBarriers`.

## Default

Economy and home duties orchestrated by `builder.runDefault`; runs regardless of ally core HP. First: if any enemy gunner/sentinel is in vision **and the builder is not mid-relay**, contest (`contestTurret` / `defGun` / `defendHome` / `healAlly`) — skip economy that turn. A builder that is `inRelay` never breaks off to fight: it dodges instead (`_dodgeWhileRelaying` → `avoidFire` toward the ally core when standing in a gunner's LoS), and a threatened belt tile is routed around inside `relayToCore` rather than dropping the relay. For `BUILDER_HOME_TURNS` after `birth_round` (default path only): defend-first + near-core economy. After that: `buildHarvester` → `maintainHarvester` → `defGun` → `defendHome` → `healAlly` → `recheckOreNodes`. Knob `≤0` disables the early phase. Harassers and guardians never run this phase.

## Harasser

Offense role (`builder.runHarasser`, helpers in `offense.py`): `snipeHarvester` → `offGun` → `avoidFire` → `denyBelt` → `seekHarvester` → `seekCore`. No economy. Assignment: `idx % 5 in (2, 4)`, or **non-guardian** builders once a wave gate fires (`WAVE_ATTACK_TURN` / `WAVE_ATTACK_TI` in `main.py`; `≤0` or `None` = unset). Unaffected by default’s early defend phase. `snipeHarvester` places a gun on the nearest visible uncovered enemy harvester; `avoidFire` steps to safe tiles that still advance toward `harassTarget` / enemy core. `denyBelt` attacks enemy logistics two ways: **Mode A** plants a barrier on the empty tile one step ahead of an enemy belt's facing (blocks throughput, no fight); **Mode B**, only while gun placement is unaffordable (`_canAffordGunPlace`), cuts an enemy belt from an adjacent tile and barriers the cleared tile in place — staying adjacent means the barrier goes down the moment the belt dies, with no move-on / step-off. Sticky target via `bot.denyBeltTile`; both modes skip ore tiles nearer our core than the enemy's (`_oreCloserToAlly`) so we never wall off our own expansion. Target selection is staggered by `harasserRank` so harassers spread across belts. `seekCore` approaches the enemy core then orbits mid-side / ring waypoints (corner-safe) so harassers keep moving.

## Wave attack

Tunable in `main.py`. Positive turn → non-guardians harass from that round; positive Ti → non-guardians harass when bank ≥ threshold. Either gate alone is enough. Guardians ignore wave.

## BuildHarvester

Places on empty ore (not in relay). With `ORE_STAGGER = False` (default), picks the most convenient empty pad `(dist to core, dist to self)`. With `ORE_STAGGER = True`, index-staggers economy defaults (`idx % 5 == 3`) over ores sorted by core distance. Pads with a visible ally builder **on the ore tile** are skipped (gap-fill if all claimed). Sticky `oreSite` is kept until invalid. Also skips a node if a **visible enemy gunner** is aiming at that tile (`tileThreatenedByGunner`). No store-based harvester cap.

## Spawn

Core rushes `MAX_BUILDERS_EARLY`, then +1 builder on an effective interval: base `SPAWN_INTERVAL`, shortened by `SPAWN_INTERVAL_BY_TI` tiers when the Ti bank is rich (capped at `MAX_BUILDERS`). Registry is HiveMind slot 4.

## DefGun

Near-core defensive **gunner** placement only (`defence.defGun` → `guns.placeGun`). Never places sentinels. Skipped while relaying or rechecking ore. Gunner/sentinel pads never on ore. With `homeOnly` (guardians only — defaults pass it unset and are unchanged) targets are restricted to **inside the leash**: a gunner pad must sit within sqrt(13) ≈ 3.6 of its target, so aiming at something further out sends the guardian to a pad the leash step then marches it back from — it ping-pongs and never places. Out-of-leash turrets belong to `defSentinel`.

## DefSentinel

Answer to turrets parked outside the leash (`defence.defSentinel` → `guns.placeDefSentinel`, knobs `DEF_SENTINEL`, `TURRET_THREAT_CHEBYSHEV`, `TURRET_MEMORY_TURNS` in `main.py`). The geometry is the whole problem: an enemy sentinel's attack radius is sqrt(32) ≈ 5.7, so it shells the core from Chebyshev 5–6, outside the guardian leash of 4 and beyond a gunner's 3.6 reach. **Our own sentinel has the same 5.7**, so a pad *inside* the leash covers the gap and the guardian never leaves home — no leash arithmetic, no sticky sortie state, no travel phase.

Detection reads the HiveMind `local_map` rather than `get_nearby_units`, because builder vision is sqrt(20) ≈ 4.5 and a guardian on its patrol ring usually cannot see the thing at all; the core (sqrt(36)) or a passing builder spots it and the ledger carries it (own vision is folded in by `tick`). Sightings older than `TURRET_MEMORY_TURNS` are ignored. Candidates are enemy gunner/sentinel/launcher within `TURRET_THREAT_CHEBYSHEV` of the core footprint, ranked sentinel → launcher → gunner, then by distance to core.

Order matters and is deliberate: **below `defGun`** (anything a gunner can reach is cheaper answered that way, so a gunner or builder pressing the core still comes first) and **above `defendHome`**, which heals the core — healing alone loses that trade at 4 HP/turn against 18 a shot, so killing the sentinel is the defence and the heal is only the holding action underneath it. `placeDefSentinel` drops pads outside the leash rather than merely deprioritising them, so it cannot walk a guardian out; pads are then sorted nearest-home first. Skipped when an ally gunner or sentinel already covers the target, which also keeps the second guardian from stacking turrets on one threat.

*Note:* the threat gate is Chebyshev while sentinel range is Euclidean, so a turret on the diagonal at Chebyshev 6 (Euclidean 8.5) passes the gate without actually being able to hit the core. It may still be shelling outlying belts or harvesters. Tighten to an r²≤32 test against the footprint if that matters.

## BreakOreDeadlock

Anti-livelock for economy defaults (`logistics.breakOreDeadlock`, knob `ORE_DEADLOCK_TURNS` in `main.py`; `≤0` disables). `bot.stuck` counts turns with no position change (maintained in `main.py`) and was previously read only by harasser orbit and guardian patrol. When a default sits motionless for `ORE_DEADLOCK_TURNS` **outside a relay**, it blacklists its `oreSite` into `skippedOres`, clears sticky site / path cache / relay state, and re-arms the counter. State-only — it costs no turn, and the economy steps below it re-pick immediately. Relays are exempt (`relayStuck` already covers those). Needed because `_clearStickyIfAllyClaimed` exempts any builder already on the ore or cardinally adjacent to it (`_selfNearOre`, manhattan ≤ 1), so a cluster that forms on one node never yields it. `pickEmptyOre` also honours `skippedOres` (falling back to the full pool if that would empty it) so a blacklisted node isn't immediately re-picked.

## GuardianEconomy

Guardian idle work, below every defence step and `clearBarriers`, above `patrolHome` (`builder._guardianEconomy`, knob `GUARDIAN_ECONOMY` in `main.py`). Runs only on turns with **no enemy turret or builder in vision** (`_enemyUnitInVision`). Sets `nearCoreEconomy = True`, so `_allowOreSite` confines every claim to the leash (Chebyshev 4 of the core footprint), then `buildHarvester` → `maintainHarvester`. Relay repair falls out of the second: `maintainHarvester` on a leash harvester re-enters `relayToCore`, whose watermark walks the existing belts and resumes at the first gap or wrong-facing tile, so a harvester whose line never reached the core gets finished. A complete line marks done and consumes no turn.

Two interactions make it safe. A visible enemy drops the relay claim (`abortRelay` + clear `oreSite`) before the defence chain, because `_reactEnemyTurret` and `defGun` both bail out early on `inRelay` — a guardian holding a relay would otherwise be locked out of fighting; placed belts stay on the map and the watermark rebuilds on re-pick. And `_guardianLeashOk` widens the leash by `GUARDIAN_ECON_SLACK` (default 1) **only** while `inRelay` or `oreSite` is set, since ore on the leash edge is worked from a neighbouring tile one step out; without it the leash step marches the guardian home every other turn and the relay never finishes.

## BreakEncirclement

Anti-livelock for every builder role (`pathing.breakEncirclement`, knob `ENCIRCLED_TURNS` in `main.py`; `≤0` disables), called at the top of `runBuilderRoles` before dispatch. Fires when `bot.stuck` ≥ `ENCIRCLED_TURNS` **and** no `can_move` direction is legal: the builder is boxed in and every role's movement below would fall through silently forever. Known cause is a harasser fencing itself in with its own `denyBelt` barriers. Doors are the four adjacent tiles holding a building (never the core, never terrain walls), ranked by whether the tile *behind* the door is walkable — punching into another sealed cell buys nothing — then by cheapness of the sacrifice (barrier → conveyor/splitter → harvester → launcher/sentinel/gunner), then x/y for stability. Ally buildings come down with `destroy`, which costs no action cooldown, so the same turn steps through the gap; enemy buildings only get the guarded adjacent-`can_fire` attempt used in `clearWrongBelt` (the engine restricts builder fire to its own tile, so this usually declines). Re-arms `bot.stuck` and clears the path cache. Runs before `breakOreDeadlock`, which shares the counter at the same default threshold and resets it.

## RecheckOreNodes

Last-resort helper: clears sticky `oreSite` / path cache and picks a fresh empty ore or harvester so builders keep maintaining/stealing after relays finish.

## DefendHome

Keep a damaged ally core alive. Near-core only (Chebyshev 4 of footprint), skipped while `inRelay` or `recheckingOre`: heal an adjacent core footprint tile if core HP &lt; 500, else `moveToward` one. Called from `runGuardian`, `runDefault`, `_runDefaultHome` and `_reactEnemyTurret`.

It used to lead with an attack branch — `_findDefendTarget` picked the nearest enemy turret (else builder), walked to it, and poked for 2 dmg at 2 Ti. Both that branch and the helper are gone. The Ti rate was bad on its own (1.0 Ti/dmg against a gunner's 0.20), but the approach was worse: a 40 HP builder closing on something dealing 10 dmg/turn at sqrt(13) dies in four turns and must survive the walk before landing a single hit — trading a 30 Ti builder for ~6 damage to a 10 Ti gunner. Defaults had it worst, since they have no leash and could chase across the map.

Healing is no longer gated on empty vision either. The core is healed *because* something is shelling it; under the old gate any enemy builder wandering into view flipped the guardian into the attack branch and it abandoned the core to give chase. Against a sentinel parked outside the leash this is the holding action while `defSentinel` builds the answer — 4 HP/turn against 6 dmg/turn does not hold outright, but it stretches a 500 HP core from ~83 turns to ~250, and wins the Ti trade 3.3:1.

## ClearBarriers

Guardian helper: nearest enemy barrier in vision **inside the leash** (Chebyshev 4 of the core footprint) → adjacent fire, else `moveToward`. Barriers beyond the leash are deliberately ignored: walking out to one puts the guardian outside the leash, so `runGuardian`'s leash step marches it home next turn and it ping-pongs forever without patrolling. Distant barriers are the harassers' job.

**Gated on `not underAttack`** — this is a chore, not a response. A barrier is 30 HP against 2 dmg per attack: 15 turns and 30 Ti to remove something the enemy replaced for 3. It must never run while anything hostile is in vision, so it shares the gate with the idle economy below it. Kept despite the Ti ratio because the enemy cuts relay lines and plugs them with barriers, and nothing else answers that.

*Note:* it does not answer that tactic where it usually happens. The leash restriction means a cut out along the belt run — well beyond Chebyshev 4 — is never seen. Enemy barriers on a relay path are handled by `relayToCore` routing around them (`_RELAY_BLOCK_TYPES` in `pathing.py` makes a barriered tile unlayable); nothing actively clears one, and when no detour exists the relay hits its "no viable path" abort.

## PatrolHome

Guardian idle: cycle four mid-side waypoints around the ally core footprint inside the leash so rear approaches enter vision.

## MaintainHarvester / checkOre

Sticky ore site inspection: empty → build; harvester → `relayToCore`; ally infra on ore → destroy (gunner/sentinel skipped only if that ore is threatened by an enemy gunner); enemy infra → gun stub. Recheck prefers empty → clearable ally-blocked → harvester.

## RelayToCore

Inbound conveyor chain from ore/harvester to ally-core-adjacent tiles. Orchestrator plus facing/clear/place helpers. Belts face next hop; corners face the new outbound direction. Wrong-facing ally belts destroyed; enemy belts cleared by builder fire. An enemy turret merely being in vision never drops the belt. Aborts are failure exits: no ally core, no viable path, path adjacent to the enemy core, or `relayStuck >= 3`.

**Threatened tiles are routed around, not aborted on.** When the next belt tile is under a visible enemy gunner's LoS/arc (`tileThreatenedByGunner`, the same predicate `buildHarvester` uses to skip ore) the tile goes into `bot.relayThreatBlocked` and `pickRelayPath` re-runs from `relayOrigin`. Titanium is still never spent into a firing line, but the relay survives. `abortRelay` is kept as the fallback for when the replan finds nothing — so the boxed-in case degrades to the old behavior.

Replanning from the origin rather than splicing at the watermark is deliberate: belts already laid sit behind the threat and stay valid, and a path that retraces them costs nothing because `extendRelayWatermark` resumes at the first gap or wrong-facing tile. The replan consumes the turn; the first belt on the new route goes down next turn.

`relayThreatBlocked` is plumbed through `relayPassable` / `findPathAroundObstruction` as `threat_blocked`, kept separate from `ore_blocked` because the lifecycles differ — ore blocking is derived fresh from the current claim, threat blocking accumulates over one relay attempt. It is **reset in `clearRelay`**, so it clears on completion *and* on abort: a tile is blocked because a gunner covered it *then*, and that gunner may be gone by the next relay. An empty set is passed as `None`, so the no-threat case costs exactly what it did before.

Two known limits. Threat is discovered one tile at a time (the check only runs on the next tile to build), so belts are laid up to the edge of a firing line before we notice, and a wide arc costs several replan turns. And because the blacklist resets on abort, a relay genuinely walled off by a firing line will abort, re-pick the same ore, and rediscover the same tiles — the pre-existing cycle, now doing more work per lap. If replays show pacing, the fix is escalating to `skippedOres` (blacklist the *node*), not lengthening the blacklist's life; note neither existing counter catches it, since `clearRelay` resets `relayStuck` and `bot.stuck` only counts turns without movement.

## Task list

Ordered phases inside a role; first match wins each turn.

## Guns layer

`placeGun` / `gunnerLoop` for gunners (destroyable-transparent LoS except **barriers**, which are opaque). Rotate uses engine `can_fire_from` only so ally barriers force a re-aim; enemy barriers stay on the fire priority list. `placeSentinel` / `sentinelLoop` used by harasser `offGun` toward enemy core; not by `defGun`. Sentinel pads require a facing that hits the **2x2 core footprint** (nearest footprint tile; no NORTH fallback) and prefer pads nearer the core, then nearer the builder. Pads skip ore tiles, tiles on an ally gunner’s facing ray (8-dir belt-style), and Chebyshev-1 neighbors of ally gunners. `offGun` skips placing another gunner on an enemy harvester already covered by an ally gunner (`allyGunCovering`).

## HiveMind v2

Delta ledger + metronome (`components/hivemind_v2.py`). Builder count in slot **4**; deltas 5–12; metronome 13; map id 14; packed cores 15. Player owns slots **0–3** (0 = first relay done; 1–3 free).
