# Baron domain glossary

For **where code lives**, see `AGENT.md` Key. For **behavior phases**, see task lists at the top of `builder.py`.

## Builder role

**default**, **guardian**, or **harasser**. Assignment from `hivemind.my_index` (`> 0`, `idx % 5`): `1` or `0` → guardian (1, 5, 6, 10, …); `2` → harasser (2, 7, 12, …); `3` or `4` → default (3, 4, 8, 9, …). Per five spawns: **2 guardians / 1 harasser / 2 defaults**, order `G H D D G`. Guardian slots past `MAX_GUARDIANS` (`main.py`, `≤0`/`None` disables) dispatch as **non**-guardians instead — `builder.guardianRank` maps slots 1, 5, 6, 10, 11, … to ranks 1, 2, 3, 4, 5, …, so a cap of 4 first applies to builder 11 — which `MAX_BUILDERS = 10` prevents, leaving the cap **inert** in practice and kept only as a safety net for raising the crew ceiling. Deriving the cap from `my_index` alone keeps role a pure function of the index: deterministic, permanent per unit, no coordination and no oscillation. A capped guardian is a default from birth and is wave-eligible like any other non-guardian. Wave gates switch non-guardians to harasser; guardians stay home.

## Guardian

Home defence role (`builder.runGuardian`, helpers in `defence.py`): leash (Chebyshev 4) → `_reactEnemyTurret` (`skipEconomy=False`, `homeOnly=True`) → `defGun` → `defSentinel` → `avoidFire` → `defendHome` → `healAlly` → *[not `underAttack`]* `clearBarriers` → *[not `underAttack`]* `reconnectBelts` → *[not `underAttack`]* **idle economy** → `patrolHome`. `_reactEnemyTurret`'s terminal "consume the turn anyway" exists only to stop `maintainHarvester` re-entering a relay, so guardians opt out (`skipEconomy=False`) — otherwise a turret they cannot act on (bank under `GUN_PLACE_MIN_TI`, target outside `_nearCore`, nothing adjacent, nothing damaged) freezes them out of `clearBarriers` / `patrolHome` for as long as it stays visible. Assignment: `idx % 5 in (0, 1)`. Ignores wave. Home defence is entirely the guardians' job — there is no core-HP recall pulling other roles home.

`underAttack` gates the two chores at the bottom. It reads `_enemyUnitInVision` (turret **or** builder) normally, but falls back to `enemyTurretInVision` (turret only) until the team's first relay completes (`logistics.relayEverComplete`, HiveMind slot 0). Before there is any economy, an enemy builder merely in vision must not keep a guardian off ore; after that, the stricter reading protects what exists. The same variable also decides whether a visible enemy drops the guardian's relay claim.

**Dodging.** `avoidFire` (goal = ally core) sits below the turret answers and above every chore. Placing a counter-gunner is the actual response to being shot and is worth eating a shot for; healing at 4 HP/turn while taking 10, clearing a barrier, or laying a belt are not. *Caveat:* `_reactEnemyTurret` at step 2 runs `defendHome` / `healAlly` internally, so a guardian with something to heal still heals from inside the line before reaching the dodge.

**No builder-attack.** Guardians never poke a turret with builder fire: 1.0 Ti/dmg against a gunner's 0.20, and the `moveToward` approach walked a 40 HP builder into something dealing 10 dmg/turn at sqrt(13). Turrets answer turrets — `defGun` inside the leash, `defSentinel` outside. The only builder fire left in the chain is `clearBarriers`.

## Default

Economy and home duties orchestrated by `builder.runDefault`; runs regardless of ally core HP. First: if any enemy gunner/sentinel is in vision **and the builder is not mid-relay**, contest (`contestTurret` / `defGun` / `defendHome` / `healAlly`) — skip economy that turn. A builder that is `inRelay` never breaks off to fight: it dodges instead (`_dodgeWhileRelaying` → `avoidFire` toward the ally core when standing in a gunner's LoS), and a threatened belt tile is routed around inside `relayToCore` rather than dropping the relay. For `BUILDER_HOME_TURNS` after `birth_round` (default path only): defend-first + near-core economy. After that: `buildHarvester` → `maintainHarvester` → `defGun` → `defendHome` → `healAlly` → `recheckOreNodes`. Knob `≤0` disables the early phase. Harassers and guardians never run this phase.

## Harasser

Offense role (`builder.runHarasser`, helpers in `offense.py`): `snipeHarvester` → `offGun` → `avoidFire` → `denyOre` → `denyBelt` → `seekHarvester` → `seekCore`. No economy. Assignment: `idx % 5 == 2`, or **non-guardian** builders once a wave gate fires (`WAVE_ATTACK_TURN` / `WAVE_ATTACK_TI` in `main.py`; `≤0` or `None` = unset). Unaffected by default’s early defend phase. `snipeHarvester` places a gun on the nearest visible uncovered enemy harvester; `avoidFire` steps to safe tiles that still advance toward `harassTarget` / enemy core. `denyBelt` attacks enemy logistics two ways: **Mode A** plants a barrier on the empty tile one step ahead of an enemy belt's facing (blocks throughput, no fight); **Mode B** cuts an enemy belt from an adjacent tile and barriers the cleared tile in place — staying adjacent means the barrier goes down the moment the belt dies, with no move-on / step-off.

Mode B is rationed on the way in and committed to once begun. A cut is 20 HP at 2 dmg an attack: **10 shots, 20 Ti, 10 turns**. Completed it is strongly positive — they pay 9–33 Ti to reopen and the whole downstream line delivers nothing meanwhile (a harvester is 2.5 Ti/turn). Abandoned it is pure waste.

*Starting* requires gun placement to be unaffordable (`_canAffordGunPlace` — a gun is better value than a cut when we can afford one) **and** this harasser's `DENY_CUT_COOLDOWN` (`main.py`, per-harasser via `bot.lastCutRound`, stamped once on fresh target selection; `≤0` disables) to have elapsed. *Continuing* requires neither: `_cutInProgress` overrides both while the sticky target is fogged (selected, still walking in), a damaged enemy belt (mid-cut), or empty (belt dead, barrier is the next action). The bank gate was previously applied to continuation too, and since each attack pushes the bank toward the threshold while one harvester delivery pushes it back over, it flipped mid-cut and abandoned belts with the titanium already spent.

A **full-HP** enemy belt is deliberately not "in progress", which gives the heal-race bail-out for free: their builder restores 4 HP/turn for 1 Ti against our 2 dmg for 2 Ti, so once they have healed it back to full we are losing 4:1 and walk away rather than grind. Mode A stays unthrottled throughout — 3 Ti, no attrition, no engagement. Sticky target via `bot.denyBeltTile`; both modes skip ore tiles nearer our core than the enemy's (`_oreCloserToAlly`) so we never wall off our own expansion. Target selection is staggered by `harasserRank` so harassers spread across belts. `seekCore` approaches the enemy core then orbits mid-side / ring waypoints (corner-safe) so harassers keep moving.

## DenyOre

Harasser ore denial (`offense.denyOre`, knobs `DENY_ORE_CHEBYSHEV` / `DENY_ORE_MIN_ALLY_CHEBYSHEV` in `main.py`, either `≤0` disabling). Sits between `avoidFire` and `denyBelt`: below the gun steps, which kill production that already exists, and above belt cutting, because 3 Ti with no attrition to deny a whole future line beats 20 Ti of attacks to cut one belt.

A barrier on empty ore makes the tile unbuildable, so 3 Ti prevents a 20 Ti harvester **and** the belt line that would have fed from it. Clearing it costs them 15 builder-turns / 30 Ti, or 3 gunner shots / 6 Ti — every route costs more than it cost us. Ore knowledge comes from `knownOreTiles`, which reads the ledger and falls back to `features.ORE_LOCATIONS`, so a harasser can head for enemy-side ore before anyone has scouted it. Ranked nearest the enemy core first, then nearest us; occupied nodes are skipped (a live enemy harvester is `snipeHarvester`'s job) as are tiles under gunner fire, where the barrier would be shot down and rebuilt forever.

**Two independent guards keep it off our own side.** `_oreCloserToAlly` draws the midline, and `DENY_ORE_MIN_ALLY_CHEBYSHEV` is a floor from our own core footprint — the midline alone is not enough on a small map, where an ore can be marginally nearer their core yet sit well inside our working range. The floor is chosen from the map tables rather than guessed: across all 21 known maps at radius 6, floors 0–6 barely bite (64→61 candidate nodes) and leave atoll's `(9, 8)` exposed, the contested centre deposit `observations/combat_findings.md` records our own raider building on; **8** drops it to 52 and correctly silences the feature on `sprint` (10x10) and `sweden`, where the two cores are close enough that "near theirs" *is* "near ours". 10 over-corrects, killing five maps.

**Never a lock-out.** `pickEmptyOre` admits ore carrying one of our own barriers (`_allyBarrierOnOre`) and `checkOre` destroys it and builds the harvester on arrival, so if we later want a denied node we simply take it. No cooldown: unlike belt cutting the candidate set is inherently bounded — a handful of nodes in one radius, each denied once — so it cannot drip titanium.

## Wave attack

Tunable in `main.py`. Positive turn → non-guardians harass from that round; positive Ti → non-guardians harass when bank ≥ threshold. Either gate alone is enough. Guardians ignore wave.

## BuildHarvester

Places on empty ore (not in relay). With `ORE_STAGGER = False` (default), picks the most convenient empty pad `(dist to core, dist to self)`. With `ORE_STAGGER = True`, index-staggers economy defaults (`idx % 5 in (3, 4)`) over ores sorted by core distance. Pads with a visible ally builder **on the ore tile** are skipped (gap-fill if all claimed). "Empty" admits ore carrying one of our own `denyOre` barriers (`_allyBarrierOnOre`, barrier-only — not the wider `_shouldClearAllyOreBlock` set); `checkOre` clears it and builds on arrival. Sticky `oreSite` is kept until invalid. Also skips a node if a **visible enemy gunner** is aiming at that tile (`tileThreatenedByGunner`). No store-based harvester cap.

## Spawn

Crew size tracks the bank and is counted in **living** builders (`respawn.run_core_spawn`). Core rushes `MAX_BUILDERS_EARLY` (5) unpaced, then holds whatever `BUILDER_TARGET_BY_TI` justifies — 5 poor, 7 at 1000 Ti, 10 at 2000 — clamped by `MAX_BUILDERS` (10). Spawning is otherwise ungated: the engine allows one spawn per turn and the target is bounded, so growth is gradual by construction.

`_aliveBuilders` counts ally `BUILDER_BOT` cells in the core's `local_map`, clamped to slot 4. Slot 4 is builders *ever spawned* and must stay monotonic — it is the `my_index` allocator — so it cannot answer "how many are left"; the ledger can, because every builder broadcasts itself and the core already garbage-collects entries unseen for `total_builders + 2` turns. Counting alive is what makes **losses get replaced**. Under-counting (a live builder that missed a broadcast rotation) costs an extra spawn, which is the safer direction to be wrong in.

This replaced `MAX_BUILDERS_EARLY + round_n // interval`, which was a function of round number with the current Ti tier applied *retroactively*. Crossing 2000 Ti at round 400 leapt the target 13 → 21 and spawned eight builders back to back; falling back froze spawning for 400 rounds, because the lifetime counter it compared against never decreases. Both failures are gone with the round arithmetic, and `SPAWN_INTERVAL` / `SPAWN_INTERVAL_BY_TI` were removed with it.

Resulting crew: 5 poor → 2G/1H/2D, 7 → 3G/1H/3D, 10 rich → 4G/2H/4D.

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

## PathKnownBlocked

Reachability signal (`logistics._notePathVerdict` / `pathKnownBlocked`). `moveToward` returned a bare `False` for three unrelated situations — move cooldown, a legal step `can_move` refused, and *no route exists at all* — so nothing upstream could distinguish "wait a turn" from "that ore is walled off". Each planning turn now records the verdict on `bot.lastPathFailed` / `lastPathFailedTarget`.

The verdict is paired with a target because one turn may call `moveToward` for several different goals as the role chain falls through; a bare flag would describe whichever ran last. It is only written on turns that actually planned — under move cooldown the unit has learned nothing, so the previous verdict stands. A cache hit records success. Only `path is None` counts as blocked: a short path is a planner quirk, not proof.

**Conservative by construction.** `walkPassable` treats tiles that are neither in vision nor in `local_map` as passable, so A* routes optimistically through fog and only fails once the known map *proves* the region sealed. It errs toward "reachable" — better to walk out and discover a wall than wrongly abandon ore. Note also `PATH_MAX_ITERS = 800` is under a 30x30 map's 900 tiles, so a very long but real route could exhaust the budget and read as blocked.

Consumed by `BreakOreDeadlock`, which no longer waits out `ORE_DEADLOCK_TURNS` when the claim is provably unreachable. Known case: the walled-off centre ore on `atoll`, where a builder blacklisted a node, picked another behind the same wall, and cycled for the rest of the game.

Consumed also by `OpenBlockedRoute`, which turns the verdict into an action.

## OpenBlockedRoute

Removing a blockage instead of giving up on the node (`logistics.openBlockedRoute` + `findBlockingDoor`, knob `OPEN_BLOCKED_ROUTE` in `main.py`). Sits in `runDefault` / `_runDefaultHome` **above** `breakOreDeadlock`, which clears `oreSite` — the door search needs the claim to know which route was blocked.

`pathKnownBlocked` says no route exists; it cannot say what to remove. `findBlockingDoor` answers that by replanning with **enemy barriers treated as passable**: if a route appears, the first barrier along it is the tile holding the lane shut. `None` means the blockage is not a barrier — terrain, or ally buildings, which `walkPassable` also rejects — and the caller falls through to blacklisting the node.

The answer is a **gunner aimed at the barrier**, not builder fire. 3 shots / 6 Ti / ~3 turns against a builder's 15 turns and 30 Ti, and the builder is free after one turn rather than pinned for fifteen. No new machinery is needed: `_GUNNER_PRIORITY` already ranks `BARRIER` above belts, so `gunnerLoop` shoots it. The gunner then stays on the lane and kills re-walls automatically — they pay 3 Ti to rebuild, we pay 6 Ti of ammo to reopen, a losing Ti trade that buys a permanently open economy lane.

**The door is sticky** (`bot.blockadeDoor`). `placeGun` walks to its pad via `moveToward`, which rewrites the path verdict to describe the *pad* — so re-deriving the door from `pathKnownBlocked` every turn would lose it mid-approach and let the economy walk the builder back at the ore. The claim is held until the barrier dies, an ally gun covers it (`allyGunCovering`), or the builder stalls for `ORE_DEADLOCK_TURNS`.

**Two kinds of door, and the cheaper one is ours.** An **ally** building (`_allyDoorAt`: barrier, launcher, sentinel, gunner) comes down with `destroy` — allied-only, free, no action cooldown. Never the **core**, and never a **harvester**: 20 Ti and actively producing, so if one seals the only corridor the node is blacklisted instead. Conveyors and splitters are walkable and so never block.

**Two triggers.** A blocked *walk* to `oreSite` (`pathKnownBlocked`) or a blocked *relay* from `relayOrigin` (`bot.relayPathBlocked`, set by `pickRelayPath` when both `astarFromSeeds` and `findPathAroundObstruction` return `None`). The relay verdict is separate because `pathKnownBlocked` only describes `moveToward`; a relay sealed by our own gunner aborts silently and would never otherwise reach the door search. `findBlockingDoor(relay=True)` then plans with `relayPassable` rather than `walkPassable`, since a relay needs a *layable* corridor — stricter than a walkable one.

**This never second-guesses the pathfinder.** Both planners already route *around* obstructions — `relayPassable` rejects `_RELAY_BLOCK_TYPES`, and `findPathAroundObstruction` is the explicit fallback — so by the time we get here A* has proven there is no way around, which only happens at a choke. That gate is the only thing keeping a builder from demolishing a deliberately-placed defensive turret; if turrets near the core start disappearing, it is leaking.

After any successful door action `_clearBlockageState` drops the stale verdicts, path cache and `bot.stuck`. Without it `breakOreDeadlock` fires next turn on a verdict recorded *before* the lane opened and blacklists the node we just unblocked.

*Gap:* **guardians do not run this.** Their domain is the leash, where `clearBarriers` already applies.

## BreakBeltDeadlock

Anti-livelock for harassers (`offense.breakBeltDeadlock`, knob `HARASS_DEADLOCK_TURNS` in `main.py`; `≤0` disables) — the offence mirror of `BreakOreDeadlock`, called state-only at the top of `runHarasser` so the selectors below re-pick the same turn.

The failure it fixes: `denyBelt` runs Mode B (`_cutAndBarrier`) **only while gun placement is unaffordable** (`_canAffordGunPlace`). That gate is right — cutting a 20 HP conveyor costs 10 shots and 20 Ti to destroy 3 Ti of theirs — so a funded harasser standing beside a belt correctly concludes it cannot profitably touch it. It then had no way to give the target up: it sat there indefinitely, firing Mode A only when a passing enemy builder happened to open a fresh forward pad beside it. Neither existing guard caught it, because `breakEncirclement` requires all four moves to be illegal and there was no blacklist for harasser targets at all.

When `bot.stuck` reaches the threshold, both sticky claims (`denyBeltTile`, `harassTarget`) are blacklisted into `bot.skippedTargets`, cleared, the path cache is dropped and the counter re-armed. `_forwardBarrierPads`, the `_cutAndBarrier` fallback pick and `_localEnemyHarvesters` all filter through `_dropSkipped`, which honours the blacklist but **falls back to the unfiltered pool if filtering would empty it** — the same concession `pickEmptyOre` makes, since a blacklist that leaves nothing to do is worse than a bad target.

One set holds both belts and harvest targets, hence `skippedTargets` rather than `skippedBelts`. Entries never expire — acceptable while a harasser's lifetime is short and the fallback prevents total starvation, but it is the first thing to revisit if harassers start refusing everything late game.

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

## ReconnectBelts

Guardian idle repair of a cut belt line near the core (`defence.reconnectBelts`), gated on `not underAttack`, **above** the idle economy — a cut line yields zero while a new harvester is only incremental.

It exists because normal relay repair is **ore-anchored**. `maintainHarvester` re-enters `relayToCore` and `extendRelayWatermark` rebuilds from the first gap, but a guardian runs with `nearCoreEconomy = True`, so `_allowOreSite` rejects any ore beyond the leash. A break *near the core* belonging to a harvester *outside* the leash is therefore unreachable: the guardian can never claim the ore that would carry it there, and the one-in-five economy defaults are off working their own nodes. Anchoring on the belt instead of the ore is the point.

Detection reads the HiveMind ledger, not own vision — a guardian on its patrol ring cannot see the whole near-core belt run at sqrt(20). A candidate is an ally **conveyor** inside the leash whose output tile (`pos + direction`) is not another ally belt and not the core footprint. Splitters are excluded: they rotate output among three directions, so their facing does not identify a break. The output tile must be in vision, empty, and `Environment.EMPTY` — a belt aimed at a wall or a building is *wrong-facing*, which is `clearWrongBelt`'s problem, not a cut. Candidates are skipped when a visible ally builder is within Chebyshev `_RELAY_OWNER_CHEBYSHEV` (2) of the tile — a default mid-relay has a legitimately dangling head, and cardinal adjacency was too tight because `relayToCore` steps the builder onto the belt it just placed and falls back to `moveToward` for a distant next tile, leaving the owner two or more tiles from its own head or when `tileThreatenedByGunner` covers the tile. Ranked nearest-core first: the most downstream break reconnects the most line.

Facing is **proven or refused** (`_reconnectFacing` → `Direction | None`). Two lookaheads, both of which see the far side of the gap: a cardinal neighbour strictly closer to the core that already holds an ally belt or the core footprint (1-tile gap, closes exactly); or that neighbour being empty-but-layable with the tile beyond it in the same direction reconnecting (2-tile gap — fill the first, the second follows on a later turn from the same evidence). Anything else returns `None` and the break is skipped, including for the walk, so an unfixable break cannot draw a guardian across the leash.

*There is deliberately no fallback.* `facingIntoCore` previously filled that role: a crow-flies heading at the core anchor with no route behind it. The belt it produced pointed at another empty tile, so it became a dangling end itself the next turn and the guardian marched a line of belts at the core one per turn — and because those belts agreed with no route, whatever `relayToCore` later planned disagreed and `clearWrongBelt` tore them out. That was the observed "guardians placing and replacing belts out of combat". Where the hop cannot be proven, `maintainHarvester` → `relayToCore` owns the repair and has an actual path to work from.

Note the Chebyshev test means a gap needing a *lateral* detour around an obstacle is never patched here — sideways hops do not reduce distance to the footprint, so they are not provable without a path.

Deliberately greedy — no A*, so a blocked coreward tile cannot be routed around. Candidates are tried in order and a failed placement falls through to the next rather than retrying, so it cannot thrash; if none is adjacent it walks to the most downstream one. Gaps wider than two tiles are not patched at all — the hop stops being provable — so a turret raking several tiles out of a line is left to the ore-anchored relay repair. Reuses `placeRelayBelt` / `travelFacing` / `facingIntoCore`, so there is no second belt-laying implementation.

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
