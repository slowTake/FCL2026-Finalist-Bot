# roadmap.md

What's next and why, newest section last. Prioritized next steps distinct from
`mistakes.md` (errors/traps) and `observations.md` (facts about the game).

---

## 2026-08-14 — post-logistics priorities, from a baron retrospective

Baron got teaPot to top 20 before its architecture (~10,400 LOC, god files,
merge-proof chains) became unmaintainable. Count is greenfield, has ported
HiveMind, and has a mature logistics/relay/spawn/ecoBot pass. This entry
records what a full read of baron's history/docs turned up that isn't a
"mistake" or a "fact" but a gap to close, plus the priority order agreed on.

### Findings not yet tracked elsewhere

- **Raid bot has a design discrepancy.** Commit `875c646` says raid logic is
  "still missing ammo conversion and firing/rotating logic," but the shipped
  `raid.py`/`roles/raidBot.py` implement a stationary sentinel with no
  ammo/rotate concept (`rotate()` is gunner-only, unused here). Either the
  commit message is stale leftover language from an earlier gunner-based plan,
  or a second raid mechanism was intended and never started. Needs a
  deliberate decision, not silent drift.
- **No guardian/home-defence role exists in count.** Baron's `defence.py` had
  well-reasoned combat geometry (turret-leash sized against sentinel range,
  `blockGunLine` as a cheap ~3-Ti counter to a firing gunner run before any
  other response). Count currently has no answer to a harasser rush.
- **Builder-death respawn is a documented no-op.** `respawn.py`'s own
  docstring flags nothing respawns a dead builder yet; HiveMind slot 4
  (builder count) never decrements on death. Baron's cautionary tale: its old
  `MAX_BUILDERS_EARLY + round_n // interval` spawn formula caused an
  8-builder burst crossing 2000 Ti and a 400-round spawn freeze — the fix was
  checking stock against *living* builders, not a round-based formula. Build
  count's version against a living-count check from day one.
- **Harvester-steal / supply-denial not yet ported.** Baron's cleverest
  asymmetric win: any belt (any team's) can pull an enemy harvester's output —
  free titanium for the cost of belts, no 20-Ti build. Baron's implementation
  was tangled through its worst god-files; count already has scaffolding
  (`relays.py` hijack path, `raid.py`) suggesting a partial port was planned.
  Build it on count's tree-invariant relay model, not baron's merge-proof
  chain.
- **Mirror-symmetry fix only half-applied.** `observations.md`'s "Known
  asymmetry" section documents a ~10k-titanium ring-order bug on
  drakkarfjord, fixed at the spawn site by orienting against the
  core→enemy-core axis instead of compass — still owed to `_ORTHO`,
  `pathing.CARDINAL_DELTAS`, A*/BFS tie-breaks, and `relays.planRelay`'s
  anchor pick.
- **Process lessons from baron's commit history:**
  - Recurring TLE fixes (`d4c8d43`, `8d04c9d`, `18f8869`, `e1ea679`) came in
    waves chasing multiple root causes under one symptom — use
    `telemetry.py` (`COUNT_CPU=1`) *before* a TLE shows up, not after.
  - Baron's late, reactive extraction of `policy.py` produced a large flat
    pile of independently-toggleable knobs that got hard to reason about
    combinatorially. Question each new toggle before adding it.
  - Baron's `AGENT.md` documents `scripts/replay_debugger.py` as broken/
    orphaned (imports files never committed) — delete or fix tooling
    immediately when it rots, don't leave a doc-promised broken capability.
  - Baron's ADRs (`docs/adr/0001-0003`) are a reasonable lightweight
    practice — short, dated, one decision each. Worth the format, not the
    over-explanation.

### Priority order

1. **Finish the raid bot.**
   - Resolve the sentinel-vs-gunner discrepancy above explicitly: commit to
     sentinel-only (and correct the stale commit-message/doc trail) or scope
     gunner work as a deliberate addition.
   - Decide whether a retarget/second-wave response belongs in "finished" —
     current logic plants one sentinel and never reacts to it being
     destroyed, which is weak against any defence.
2. **Guardian/home-defence role.** Biggest capability gap right now — count
   has no answer to a rush. Re-derive baron's leash/`blockGunLine` geometry
   cleanly rather than porting `defence.py`.
3. **Death-respawn + builder-count decrement.** Close the `respawn.py`
   placeholder and the HiveMind slot-4 gap; use a living-builder-count check,
   not a round-based formula.
4. **Harvester-steal / supply-denial port.** Highest ROI single mechanic;
   build on count's tree-invariant relay model.
5. **Close the mirror-symmetry debt.** Propagate the axis-orientation fix to
   `_ORTHO`, `pathing.CARDINAL_DELTAS`, A*/BFS tie-breaks, and
   `relays.planRelay`'s anchor pick.

Order is by (gap severity × how cheaply count's existing abstractions make
it) — not locked. Revisit once the raid bot lands.

---

## 2026-08-15 — foundation remediation (done), and what it deferred

Three Opus architecture reviews were run over the logistics path, the shared
substrate, and the turret/raid layer before starting guardian work. All three
judged the foundation sound; the fixes below were what they found. Every one
was verified with a full `bot_gauntlet.py --bot count` run (15/15 throughout)
plus mirror runs for the behavioural ones.

**Landed:** hijack livelock (a builder beside an enemy harvester went inert for
the match — see mistakes.md); threat surcharge bypassed on our own belts;
`_isChokepoint`'s knowledge-poisoned match cache; `relayCtx` stale across
rounds; both A* fallbacks ignoring the avoid-set the failed search used;
deletion of the structurally-broken gunner pad search; ~50 lines of dead code
in `pathfindingHelpers`; the dead threat filter in `chooseEmplacement`;
`GameConstants` instead of four hardcoded copies; a gunner ammo guard before
rotating; `_ctx*` fields declared in `Player.__init__`; `store.py` as the sole
owner of the slot layout, with role indices packed into one slot so slot 3 is
free for defence; `walkTo` keyed by goal so a moving target replans.

**Resolved, previously listed as open:** the "raid bot discrepancy" from
2026-08-14 — firing and rotation shipped in `guns.py`, and the raid path is
sentinel-only by decision, not by omission. A gunner second wave placed from
`raidBot` is the intended next step there (user's call, 2026-08-15).

**Deferred, in rough priority order:**

1. **Raider death is unrecoverable.** `ROLE_RAIDER` is assigned once and only
   the raider clears `RAID_TARGET`, so if it dies both hold stale values
   forever and raiding stops silently with no signal. Needs ledger liveness —
   the same machinery death-respawn needs, so do them together.
2. **`emplace.py` — one mechanism, three policies.** `_reachOffsets` and the
   pad-inversion loop are duplicated across `raid._candidates`,
   `raid.fallbackEmplacement`, and (until its deletion) the gunner search. The
   *policies* differ legitimately (max standoff vs nearest; sentinel lines
   ignore obstacles, gunner lines do not); the mechanism should not. Blocked
   on a real import cycle (`raid → guns → respawn → raid`) that a neutral
   module sidesteps. Do it before a second turret-placing role, not before the
   guardian's first draft.
3. **`ore.py` split.** `ecoBot.py` answers two questions — "where is the ore?"
   and "what should this builder do?" — and `respawn.py` already imports
   `chooseOre` out of a role file. Split once the guardian's draft shows
   whether it needs ore knowledge too.
4. **Threat vetting for emplacements.** Deleted rather than fixed: the only
   threat set available was empty on the core and built from the wrong end of
   the map anyway. It belongs to the raider, which can see the ground it is
   about to build on.
5. **CPU items to measure, not assume** (`COUNT_CPU=1` first): `exploreStep`
   runs two capped searches and fails every turn once the map is explored;
   `knownOre` rebuilds a full-map list per `pickOre`; `buildNetwork` scans all
   of `local_map` per unit-turn and grows monotonically.
6. **`scripts/track_builder_leash.py`** is untracked and measures Chebyshev,
   which `observations.md` says has no place here. Fix or delete before the
   guardian work it was written for.
7. **Raid conversion rate is low.** On one instrumented drakkarfjord run, 20
   published emplacements produced 1 sentinel across 445 raider turns. Not
   investigated — it may be correct behaviour for a single raider crossing the
   map, or it may be a stall. Worth a look before leaning harder on raiding.

---

## 2026-08-17 — guardian role v1 landed

The 2026-08-14 priority #2 ("Guardian/home-defence role — biggest capability
gap") is now in: `roles/guardianBot.py`, an ADR (`docs/adr/0001-guardian-role.md`),
and a runtime-claimed role held by a store heartbeat. See the ADR for the design.

What it deliberately left for next increments, roughly ordered:

1. **Combat depth.** Re-derive baron's `blockGunLine` (a ~3-Ti barrier in a
   gunner's line to the core — cheaper than fielding a turret, so it runs ahead
   of the turret responses) and `defSentinel` (answer an out-of-leash sentinel
   from inside the leash). The v1 ladder is defendCore + patrol only.
2. ~~Point 2 — gunner spam on passing builders.~~ **Done (2026-08-17), then
   superseded (2026-08-21): builders dropped as `defendCore` targets entirely.**
   The 2026-08-17 cut kept a grinding builder as a target via
   `_builderThreateningStructure` (adjacency proxy). Measured Ti told the fuller
   story — a gunner spent on any builder, grinding or not, rarely earns back its
   cost against a mobile body — so `_builderThreateningStructure` was removed and
   `defendCore`/`_nearestCoreThreat` now answer only the turrets in
   `_CORE_THREAT_TYPES`. Accepted cost: a builder grinding a core belt to a *gap*
   (not a barrier) now draws no placed response — `repairCoreBelts` is barrier-only
   and does not cover it (see open item on established-line gap recovery). Separately,
   `GUNNER_BUILDER_ROTATE_COOLDOWN` stops an existing gunner re-aiming after a builder
   every turn it moves.
3. **Leash-bounded ore-seeking for the quiet guardian.** v1 keeps it home doing
   belt repair + heal only; letting it build harvesters on ore *inside* the leash
   would make it more useful without pulling it into a long relay — but needs the
   relay-abort-on-threat handling the guardian doesn't yet carry.
4. **Scale to N guardians.** One slot for now; the mechanism generalises by
   adding role fields (the heartbeat scheme is per-role-agnostic).

Note the heartbeat also demonstrates the ledger-liveness pattern deferred item #1
(raider death) wants — a dead guardian's claim ages out and is re-claimed. The
raider path could adopt the same `stamp/read + TTL` shape rather than waiting on
full death-respawn.

---

## 2026-08-17 — guardian review (Fable), fixed and deferred

A read-only review over the guardian role and the builder-threat gate. Four bugs
found and fixed the same day: patrol stalling forever on an unreachable waypoint
(no bounds/passability filter, and the ring index never advanced on a failed
walk — a permanent stall costing an A* *plus* its BFS fallback every quiet turn);
`claimIfOpen` able to fire mid-relay on a re-claim, orphaning a paid-for
harvester nothing re-relays; the heal rung pulling the guardian outside its leash
and oscillating against the leash step above it; and the heartbeat proving only
"run() was entered" rather than "the guardian works", so a repeating post-stamp
fault would have held the role forever while defence did nothing.

Verified sound and left alone: the claim/heartbeat machinery itself (same-round
races converge on the engine's next-turn write visibility; wraparound is
unreachable at 1000 rounds vs a 4096 field; an unencodable index is dropped, not
truncated, so a stale slot can never match a live builder).

**Deferred, in rough priority order:**

1. **The ring-walling siege is invisible to the builder gate.** Our own
   `raidBot._siege` two-phases the enemy core ring: sever the belts (a builder
   adjacent to a belt — the gate catches this), then *wall the open gaps with
   barriers*, standing wherever is nearest, often adjacent to nothing of ours
   (its own fresh barriers read as enemy). In a mirror the opponent's walling
   phase therefore draws no response, and pure gap-walling — which pens in our
   spawn ring — is answered by nothing at all (`repairCoreBelts` only fires on a
   barrier severing a *fed* line). Candidate fix: treat any enemy builder within
   2-3 of the core as unconditionally threatening (nothing innocently passes
   through the core ring) and keep the adjacency gate for the 3..8 band.
2. **Claim timing is economically aggressive.** Eco labour is 3 bodies and the
   claim fires at the *first* completed relay — the earliest possible moment —
   cutting farming ~33% for the rest of the match. Defensible on rush-prone
   maps, expensive on placid ones. A stronger gate is one read away
   (`store.HARVESTERS >= 2`), or defer until an enemy has been seen near home.
   Measure on mirror_gauntlet before changing.
3. **`get_hp(id)` as a stronger threat signal.** See the note in
   `docs/adr/0001-guardian-role.md`: "adjacent ally building is *damaged*"
   separates a builder actually grinding from one standing beside our belt, at
   one turn of reaction delay. Cheaper than the cross-turn stationarity tracking
   floated earlier.
4. **Guardian never heals its own turrets.** `heal.TURRET_HEAL_TYPES` exists and
   the ladder does not use it, while the home gunners it stands beside are
   exactly what takes fire. Add once the leash-bounded heal filter proves out.
5. **A broke/capped guardian drifts to patrol instead of holding station**
   beside a live threat it cannot yet afford to answer (`defendCore` returns
   False in that case — correct for the eco caller, which leaves to earn).
6. **If all three eco builders die the role goes permanently unfilled** — the
   same class as the deferred raider-death item, and fixed by the same
   living-count work.

---

## 2026-08-18 — hedged/load-bearing review (Fable): findings

Read-only pass over the low-confidence ("guess"-flavoured) functions and the
high-blast-radius core (`main` dispatch, pathing, movement, relays, spawn,
guns), plus a regression check on the 2026-08-17 guardian fixes. Nothing edited.
Findings 1 and 2 re-verified against the code by hand; 3–7 are the agent's,
carried with its confidence labels.

**Reviewed against `14ae833`, which was stale by 10 commits at write-up time**
(`origin/count/dev` had already moved to `abcd54e`, ~993 lines across
`guns.py`, `launcher.py`, `raidBot.py`, `ecoBot.py`, `main.py`, `movement.py`,
`emplace.py`). `relays.py`, `respawn.py`, `guardianBot.py`, `store.py`,
`pathing.py`, `pathfindingHelpers.py` are unchanged in that range, so the
Verified-sound section and findings 1, 2, 6, 7 still hold as written. Findings
3–5 sit in files that changed underneath the review (and two of the new commit
messages — "stop raiders idling: silent launcher crash + four siege stalls",
"defendCore: stop the infinite vigil beside a threat we can't shoot" — look
like they may already overlap 3/4) and need re-verification against current
HEAD before acting on them; treat them as retracted pending that pass.

### Verified sound and left alone

- **`pathing.astarFromSeeds`/`bfsFromSeeds`** — multi-seed A*/BFS correct:
  closed-set handling, tie-break counter, reconstruction. The inadmissible
  BASE-scaled heuristic is the documented, deliberate trade.
- **`movement.walkTo` goal-keying** — the 2026-08-15 "moving target replans" fix
  is still wired: cache reused only while `bot.walkGoal == goal`, set on every
  replan, cleared on blacklist; both avoid-set halves (movement BFS fallback and
  `planRelay`'s BFS fallback) still honour their avoid sets.
- **`_isChokepoint`** — knowledge-poisoning fix sound: True rests on immutable
  walls/edges (safe to match-cache), False cached only when all four neighbours
  are known. No sibling of that bug class in `buildNetwork`/`_threatTiles`, both
  rebuilt once per round via `costContext`.
- **`launcher.py`** — `_bestThrow`/`_repelHostile` are bounded by the engine
  throw disc (r²≤26), no unbounded-search risk; request packing refuses rather
  than truncates; consume-on-read bounds stale requests. (The `14ae833` TLE fix
  is nonetheless incomplete elsewhere — finding 3.)
- **Guardian regression check** — all four 2026-08-17 fixes present and correct:
  patrol waypoints filtered through `isImpassable` with the ring index advanced
  on an unroutable one; `claimIfOpen` guarded by `bot.inRelay`; heal
  leash-bounded via `accept=_inLeash`; heartbeat gated on a completed ladder. No
  regressions.
- **`respawn` replenishment** — no double-spawn (one spawn per core turn, clock
  advanced only on a real spawn); the cap is still the lifetime cap, so
  builder-death respawn is still the documented no-op, not silently something
  worse.

### Findings, ranked

1. **`relayDone` fires on relay *failure*, falsely marking `_harvesterDelivered`
   and permanently orphaning the harvester.** `relays.py:49-50` funnels every
   `planRelay` False into `relayDone`, but `planRelay`'s False is overloaded:
   one case is a genuine success (harvester already beside the network, nothing
   to lay — `relays.py:163-164`) and four are failures (no `relayFrom`; no
   belt-passable seed beside the harvester, `relays.py:170-171`, e.g. one built
   at the edge of an ore cluster where every neighbour is ore; both searches
   exhausting `_RELAY_MAX_ITERS`=450; no anchor). `relayDone` (`relays.py:403`)
   clears state **and sets `_harvesterDelivered = True`** for all of them. So a
   harvester we never connected (a) is orphaned — nothing re-relays it
   (`rankOre` skips own-harvester tiles, `repairCoreBelts` is barrier-only), a
   silent economy leak; and (b) makes its builder read as `eligible` for
   guardian duty (`guardianBot.py:54` gates on this flag), so the
   already-aggressive guardian claim can fire earlier and on a body that
   delivered nothing. The contested-concession path (`relays.py:280`,
   `_replanAvoiding(...) or relayDone(...)`) marks delivered on a mid-relay abort
   the same way. The `relayDone` "fires exactly once per completed line" comment
   is wrong on its face. Severity: medium-high. Confidence: high on the path;
   frequency is map-dependent. **Fix sketched below.**

2. **`_predictTarget` steers the birth tile with the wrong index — off by one
   from the newborn's real `my_index`.** `respawn.py:128` passes
   `read_store(BUILDERS) + 1` to `chooseOre`, while the same function twenty
   lines up (`respawn.py:121`) correctly uses `built + _MYINDEX_OFFSET` (= +2),
   and `observations.md:235-244` documents empirically (COUNT_DEBUG, every map)
   that produced indices are `built + 2`, never +1. The `ORE_SPREAD` fan-out the
   guess is meant to reproduce is therefore evaluated at the *previous*
   builder's index: every eco newborn is born on the ring tile facing its
   predecessor's node, then walks to its own — half-cancelling the measured
   steering win. Stale docs repeating the +1: `_predictTarget`'s own docstring
   (`respawn.py:101`), `chooseOre`'s (`ecoBot.py:485`), and `observations.md:102`
   — all contradicted by `observations.md:235`. Severity: low-medium (one
   suboptimal tile per spawn, by design never a commitment). Confidence: high.
   **Fix sketched below.**

3. **[RE-VERIFIED against `abcd54e`, see 2026-08-18 §2] The `14ae833` TLE fix
   gates `pressCore`'s search but not the published-site approach leg.** Still
   CONFIRMED. `5b83f44` ("stop raiders idling") added dead-search guards to the
   siege-ring walk, the dodge walk, and a creep to `pressCore`'s back-off, but
   `runRaidBot`'s surplus-gated `approach → walkOrRetarget → walkTo` leg at a
   published `RAID_TARGET` is untouched: a failed `_plan` still caches nothing,
   so an unreachable site still re-runs full A*+BFS every turn of the 4-turn
   patience window, then `_giveUpOnSite` hands back to a core that republishes
   ledger-blind — likely the same site — and the cycle repeats for as long as
   surplus holds. `5b83f44` also made this marginally worse: `advance`'s new
   `noRouteSeq`-keyed waypoint drop discards the waypoint on the *first* failed
   search, so each turn of the burst now replans from scratch too. Fix shape:
   snapshot `noRouteSeq` around the site-approach calls and give up on first
   dead search, the same way `pressCore` now does. Severity unchanged (medium,
   rare trigger). See 2026-08-18 §2 for the full re-check.

4. ~~Ring-walling siege invisible to the builder gate.~~ **Moot as a gate bug
   (2026-08-21), but reframed as a real open gap:** `_builderThreateningStructure` and
   all builder-answering in `defendCore` were removed — chasing any builder with a gun
   lost more Ti than it saved — so there is no longer a builder gate for corner
   ring-walling to slip past. The flip side is now an accepted hole: a builder that
   grinds an *established* core belt to a gap draws no response at all. A barrier over
   a belt is still recovered by `repairCoreBelts`; a plain grind-to-gap on a mature
   line is not (it reads like a relay under construction), and `verifyRelay`/
   `reconnectOrphan` only cover freshly-laid or orphaned lines. Established-line gap
   recovery is the real open item here — not a gun. (Attacker-side, `raidBot._coreClear`
   still anticipates an enemy builder "on a diagonal corner too" — unaffected.)

5. **[RE-VERIFIED against `abcd54e`, reframed] Surplus-gate flapping is real
   but was mis-described as "cross-map"; a bigger, now-fixed explanation for
   the low conversion rate surfaced instead.** The flap mechanism stands
   (`raidBot.py:77` re-reads a live bank every turn, each crossing swaps the
   `walkTo` goal key), but published emplacements sit ~8-9 tiles from the enemy
   core by construction, not across the map, and `buildLocalSentinel` usually
   absorbs the surplus turn before the site leg runs — so the flap is real but
   local. Bigger fish: `5b83f44` found `emplace.enemyTurrets` raised `GameError`
   on any enemy launcher in vision, silently eating the whole raider turn via
   `Player.run`'s swallow — surplus-gated, so it fired exactly when the bank
   was full. That's a near-perfect fit for "20 emplacements, 1 sentinel, 445
   turns" and is now fixed (`emplace.py:168-203`). **Re-measure raid conversion
   on current HEAD before designing any flap-damping** — the flapping may no
   longer be the bottleneck.

6. **`runCoreSpawn`'s pocket fallback hands unfiltered ring tiles to
   `can_spawn`** (`respawn.py:211-215`): the fallback `sites =
   coreRing(corePos)` skips the `inBounds` filter `spawnSites` applies, and
   `coreRing` does no bounds checking. If `can_spawn` raises on an
   out-of-bounds Position (the engine throws on some out-of-range queries — see
   `canStandNow`'s docstring) the core's whole turn aborts through the
   `Player.run` swallow, silently skipping `publishTarget`/`topUpAmmo` every
   turn the ring stays pocketed. Needs a corner-adjacent core plus a fully
   pocketed ring — low probability, engine behaviour unverified. A one-line
   `inBounds` filter on the fallback closes it regardless.

7. **`topUpAmmo` never opens if the team never reaches `MAX_BUILDERS` lifetime
   spawns** (`guns.py:721-722`): the gate is `read_store(BUILDERS) <
   MAX_BUILDERS` and slot 4 only counts spawns, so a match where the core stays
   too poor or too boxed-in to spawn all five leaves every turret mute all game
   while gunners are still placed by `defendCore`. Edge-case severity, but it is
   the combat layer's single upstream valve. Noted, not urgent.

### Stale docstrings (per the trust-the-code rule)

- `relays.relayDone` "fires exactly once per completed line" (`relays.py:410-412`)
  — wrong, see finding 1.
- `_predictTarget` (`respawn.py:101`), `chooseOre` (`ecoBot.py:485`), and
  `observations.md:102` all assert the +1 index — wrong, see finding 2, and
  already contradicted by `observations.md:235`.

---

## 2026-08-18 §2 — re-verification against `abcd54e`, plus a pass over the 10 new commits (Fable)

The §1 entry above was reviewed against a commit 10 behind origin at write-up
time. This is the redo against current HEAD (`abcd54e`): findings 3–5 above are
now marked `[RE-VERIFIED]` inline with their updated verdicts (all three
survive in some form — see there for the full text). This section covers what
changed underneath the review (`guns.py`, `launcher.py`, `raidBot.py`,
`ecoBot.py`, `main.py`, `movement.py`, `emplace.py`, plus new
`scripts/track_builder_leash.py` — ~993 lines across 10 commits) and a fresh
pass over that surface.

### Verified sound (the 10 commits' own headline fixes)

- **`visibleHostiles` ally-builder-on-hostile-tile exclusion** (`guns.py:99-140`)
  — complete: ally tiles collected in the same enumeration pass, exclusion
  applied post-footprint-expansion, both gunner and sentinel loops draw from it.
- **`_defenceAnswers` launcher branch** (`guns.py:636-663`) — sentinel-lead,
  gunner fallback, correctly cost-gated per answer.
- **`_markIfNoRoute`/`noRouteSeq`** (`5b83f44`) — counter-not-stamp reasoning
  holds; avoid-set cleanup on a bar is correct; documented coarseness has a
  fix sketched already.
- **`_escapable`/`requireEscapable`** (`45f3be3`) — bounded flood, applied only
  to the friendly ferry throw as intended.
- **`fe2b797` eco fling/stall backstops** — toward/away discrimination,
  fling-staleness bound, and the approach-stall budget all check out.
- **`buildSiegeGunner` geometry** (`guns.py:390-421`) — bearing verified via
  `can_fire_from`, exposed pads vetoed, barrier fallback on refusal.
- **The "silent launcher crash" fix** (`5b83f44`) is the `mistakes.md`
  "`except Exception: return` hides your bug" pattern exactly:
  `emplace.enemyTurrets` called `get_direction()` on `LAUNCHER` (no facing →
  `GameError`), silently eating the raider's turn through `Player.run`'s
  swallow. Fixed correctly (`_AIMED_TURRET_TYPES`, `emplace.py:170`) and given
  a debug-gated fault line (`main.py:229-232` — gated on `COUNT_DEBUG`, so
  production/gauntlet runs still fail silently, just no longer on this bug).
  Swept every other `get_direction` call site in `bots/count`: none share the
  pattern.

### New findings, ranked

1. **`defendCore`'s stuck-counter fix ends each vigil but not the pattern —
   became a ~4:1 duty-cycle re-entry, not a one-shot fix** (`guns.py:706-746`).
   The counter does terminate at `_DEFEND_STUCK_TURNS` (4) and give up for one
   turn, but the give-up isn't remembered: next turn's `defendCore` call finds
   the same unbearable threat, the counter resets to 0 on entry to the walkTo
   branch, and the vigil restarts. Net effect against a threat with no
   reachable bearing pad: hold 4, free 1, hold 4, forever — the original
   "infinite vigil until death" bug (measured r121→r130 on a drumlin mirror)
   became a mostly-infinite orbit at a 4:1 duty cycle, ended only by the
   enemy-gunner-line bail or the threat leaving vision. No per-threat memory
   analogous to `_siegeUnreachable`. Also: the counter isn't reset on a
   successful `place()`, so a stale partial count can shorten a later,
   unrelated episode. Severity: medium-low. Confirmed by direct code read.

2. **A raider beside its own apron (siege) launcher can post ferry requests
   that launcher will never serve** (`launcher.py:208-230`, `raidBot.py:755-814`).
   An apron launcher is repel-only — `runLauncher` returns after
   `_repelAwayFromCore` unconditionally and never reads `LAUNCH_REQUEST`
   (`launcher.py:436-441`) — but `requestLaunch`'s "any friendly launcher
   nearby" check doesn't distinguish it from a real ferry launcher. A raider
   whose published site is far enough from the ring posts into the single
   shared request slot and holds up to `LAUNCH_WAIT_TURNS` for a throw that
   can't come, repeatedly; worse, its unservable request can overwrite a
   genuinely-waiting eco builder's request in the last-writer-wins slot. Fix
   shape: gate `requestLaunch` on the same enemy-apron-radius test
   `runLauncher` already uses. Severity: low-medium, plausible but unverified
   (needs a raider near its own apron launcher with a distant published site).

3. **`buildSiegeGunner`'s spend skips the siege's own `_SIEGE_MIN_TI` reserve**
   (`raidBot.py:456-458` vs. `raidBot.py:772`). The adjacent apron-launcher
   branch explicitly won't build unless the bank clears
   `launcher_cost + _SIEGE_MIN_TI` (15), preserving a spend floor. The
   siege-gunner branch, added later (`11fe822`), gates only on
   `resources >= gunner_cost` — it can spend the bank to zero, exactly what the
   sibling branch's own comment says the siege must not do. One-line fix:
   mirror the `_SIEGE_MIN_TI` check. Severity: low, confirmed by direct read.

4. **`scripts/track_builder_leash.py` shipped still measuring Chebyshev** — the
   exact tool the 2026-08-15 roadmap entry (deferred #6) said to "fix or delete"
   before the guardian work it exists to debug. `363dd28` committed it
   unchanged in that respect (`chebyshev_to_footprint`), while the guardian
   leash it measures is Manhattan by explicit, documented decision
   (`guardianBot.py:35-39`). Chebyshev under-reads diagonals, so the tool
   systematically misses 45°-direction leash violations. Tooling-severity, but
   now shipped rather than merely absent.

5. **Stale docstring**: `emplace.enemyTurrets`'s comment still says the swallow
   hides errors "without so much as a debug line" (`emplace.py:181-183`) — the
   same commit that wrote this comment also added the debug-gated fault line.
   Present-tense claim, now wrong.

---

## 2026-08-18 §3 — four fixes landed (§1 #1/#2, §2 B2/B3), reviewed by Fable

Implemented against `abcd54e`, mirror_gauntlet CLEAN (no crashes, 15 maps), and
reviewed read-only by Fable (all four verdicts SOUND / SOUND-WITH-NITS, no
regressions). Not yet committed.

- **§1 finding 1 — relay failure forged a delivery.** `planRelay` now returns a
  tri-state `_Plan` (ROUTED / CONNECTED / FAILED) instead of an overloaded bool;
  `relayToHome` credits a delivery (`relayDone`, sets `_harvesterDelivered`) only
  on CONNECTED or the all-belts-placed exit, and routes every genuine failure to
  a new `relayAbort` that tears down without the flag. Shared `_teardownRelay`;
  `layBelt`'s contested concession and `ecoBot._concedeRelay` both go through
  `relayAbort` now (the latter de-duplicated onto it). `relayAbort` keeps
  `relayDone`'s `clearHold` and `return False`, so the failure path's behaviour is
  unchanged except for the (correct) absence of the delivery flag.
- **§1 finding 2 — off-by-one spawn index.** `_predictTarget` now passes
  `myIndex` (`built + _MYINDEX_OFFSET`, +2) to `chooseOre`, not `+ 1`. All three
  stale `+1` docs corrected (`respawn.py`, `ecoBot.chooseOre`, `observations.md`).
- **§2 B3 — siege-gunner spend skipped the economy floor.** The
  `buildSiegeGunner` gate now reserves `+ _SIEGE_MIN_TI`, matching the apron
  launcher; below it the ring gap is sealed with a barrier instead.
- **§2 B2 — unservable ferry requests to an apron launcher.** `requestLaunch`
  now skips an adjacent launcher within `ENEMY_APRON_RADIUS` of the enemy core
  (the geometry `runLauncher` uses to take its apron-repel branch).

Fable nits: N2 (`relayAbort` had dropped the failure-path `clearHold`) and N3
(docstrings naming callers, against the AGENTS.md rule) — both fixed in the same
pass. Deferred:

1. **N1 — `_replanAvoiding` conflates CONNECTED with FAILED.** If a re-plan on
   `layBelt`'s contested path finds the harvester has *become* network-connected
   mid-contention, `relayAbort` runs on a genuinely delivered line and withholds
   that builder's guardian eligibility (income still flows; it re-earns on its
   next harvester). Low severity, rare trigger. A proper fix means widening
   `_replanAvoiding` to a tri-state across its ~6 call sites — not worth the
   load-bearing churn for the size of the edge. Left as-is deliberately.

---

## 2026-08-18 §4 — flung-ecoBot orphaned-relay reconnection (user-reported)

**Bug (reproduced on antler mirror, rnd 20–29).** An orange raider plants an apron
launcher two tiles from blue's core; it flings a blue ecoBot mid-relay
(`_handleFling`), which concedes the line (`_concedeRelay`). The half-built
harvester then feeds nothing and *nobody ever returns to it* — `rankOre` skips
own-harvester tiles so no builder re-targets it, and `repairCoreBelts` only
reconnects a line an enemy *barrier* cut, never one that was never finished. The
user confirmed the launcher gets answered and destroyed, yet the harvester stays
orphaned for the match. (Not a false "relay complete" flag, as first hypothesised —
`_concedeRelay` correctly never set `_harvesterDelivered`; the gap was simply that
nothing re-relays it. `store.HARVESTERS` does over-count the orphan, but that slot
is currently write-only/unread, so it drove nothing.)

**Fix — `ecoBot.reconnectOrphan`.** The builder that built the harvester remembers it
(`_orphanHarvester`, set in `_handleFling`) and comes back once the fling threat has
had time to clear (`_orphanNextTry`, a `RECONNECT_RETRY_TURNS`=30 cooldown so a
still-blocked orphan isn't re-planned every turn). A new eco-ladder step (after
`repairCoreBelts`, before `seekOre`) re-relays it. Two things had to be solved, found
by instrumented antler runs:

1. **planRelay read the orphan as already-connected from a distance.** Its
   `_chainVisiblyIntact` merge check trusts a belt chain that runs out of vision — so
   the orphan's own dead-ended partial line looked intact and `planRelay` returned
   CONNECTED, laying nothing and (worse) marking it delivered. Fixed with a
   `bot._strictRelay` flag (set for reconnection, cleared by `_teardownRelay`): under
   it, `planRelay.mergeable` accepts only a *proven* network member, so a reconnection
   routes a real line home instead of trusting the dead end. Normal relays are
   unaffected — they still rely on that out-of-vision trust (see the comment block in
   `planRelay`, kept).
2. **The relay could route back into the launcher.** Threat *cost* alone still routes
   through a launcher's grab ring when it's the only way to the core, so reconnection
   hard-avoids the grab rings of any launcher in view (`_launcherGrabTiles` →
   `relayAvoid`); it routes around a live one or fails cleanly and retries after the
   cooldown.

Verified: instrumented antler run shows the orphan at (9,17), flung at rnd 29,
re-relayed at rnd 165 and connected by rnd 168. Full mirror_gauntlet CLEAN (15 maps,
no crashes). Anti-churn: reconnection is time/threat-gated and per-owner, and only
fires on a harvester with no proven network neighbour, so a live relay-in-progress
is never torn at.

**Fable review (read-only): SOUND-WITH-NITS.** Verified sound: the `_strictRelay`
lifecycle (always cleared by `_teardownRelay` on every relay-end path; never leaks
into a normal relay; stays strict across a multi-turn ROUTED line via the flag, not a
param), the non-strict path being byte-identical to before, CONNECTED being
unreachable from `reconnectOrphan` (the pre-check negates it, so `inRelay==False` post
call truly means FAILED), the anti-churn gates, `relayAvoid` teardown, and the CPU
bound (one A*+BFS per 30 rounds per builder, bails before `costContext` when idle).
Two items fixed the same pass:

- **F1 (medium) — memory lost on a non-fling abort.** The commit path cleared
  `_orphanHarvester` immediately, but `relayToHome` can still abort a committed line
  later without delivering (a contested tile, a collapsed re-plan) — and those aren't
  flings, so nothing re-recorded it: a reconnection that died to contention silently
  reverted to the original orphan bug. Fixed: keep `_orphanHarvester` set until the
  pre-check observes the line connected; the `inRelay` gate prevents interference
  while it runs, and a non-delivering abort now retries after the cooldown.
- **N1 — commit didn't clear `oreTarget`.** Every sibling relay-commit path
  (`buildHarvester`, `seizeHarvester`, `_handleFling`) clears it; `reconnectOrphan`
  now does too, so a stale target can't drive `buildHarvester` to overwrite
  `relayFrom` mid-strict-relay on a patience-held turn.

**Deferred / limitations:**

1. **Per-owner only.** If the builder that built the orphan dies (or converts to
   guardian) before reconnecting, the memory is lost and the harvester stays orphaned
   — the same per-builder-death class as the raider-death and guardian-refill items.
   A cross-builder version (any idle builder near a long-disconnected own harvester
   reconnects it) was prototyped as a stateless time-gated scan but shelved: nothing
   *draws* a builder to the orphan, so it rarely accumulated the observation window,
   and distinguishing an orphan from a slow live relay without per-owner context
   reopens the churn trap. Revisit if orphans from owner-death show up in practice.
2. **F2 (Fable, low-med, accepted) — strict mode can cross another builder's live
   *unproven* line.** Non-strict `mergeable` stops beside any visibly-intact ally
   belt; strict only stops at a proven network member, so a reconnection A* can route
   through (and `layBelt` re-face) another builder's in-progress line. Bounded by
   `BELT_CONTEST_LIMIT` + a one-tile detour, and it can never cross the *proven*
   network. Accepted rather than fixed: the whole point of strict is to distrust the
   orphan's own unproven line, and telling that apart from a neighbour's isn't cheap.
3. **F3 (Fable, low, accepted) — a harvester destroyed out of vision reads alive.**
   `readTile` falls back to `local_map`, where destroyed buildings never expire, so a
   reconnection can lay a full line to an empty tile and forge `_harvesterDelivered`.
   Self-mitigating (the ore tile is reused when someone rebuilds there) and a widening
   of the pre-existing "no re-verify of `relayFrom` mid-relay" gap, not a new one.
4. **`store.HARVESTERS` still counts orphans** (write-only today, so latent). If a
   future gate reads it (e.g. the guardian `HARVESTERS >= 2` idea), decrement it on
   `_concedeRelay` and re-increment on a successful reconnection.

---

## 2026-08-18 §5 — Fable pass over three new features, two bugs fixed

Read-only Fable review of three commits that landed after §1-§4 (`8f33c23` erebus
anti-rush gunners, `30c2ae3` siege belt-cut give-up, `8a3e7ca` ore-barrier
clearing near core), plus a fourth (`6d2bba6`, the `bots/nologic` no-logic test
opponent) out of scope as a pure test fixture. Baseline mirror_gauntlet on the
unmodified HEAD was already CLEAN (15 maps) before this pass.

**`8f33c23` (SOUND-WITH-NITS, no fix needed).** Against a clear core (`_coreClear`
— no belt to sever, no builder to lock out), the raider now goes gunner-or-nothing
on ring gaps instead of falling back to a barrier; verified this is strategically
correct (a barrier on a clear core seals nothing and permanently blocks the tile a
future gunner needs) and has a stable backstop when broke (`_nextSiegeTile` still
finds real work or the raider holds, no repeated search). Confirms the
`_SIEGE_MIN_TI` reserve fix from §3 still applies to the new gunner path. Minor
nits (a comment made stale by the next commit, a small CPU cost from reading
`_coreClear` eagerly) not worth fixing.

**`30c2ae3` and `8a3e7ca` (BUG-FOUND, fixed).** Both commits added a "give up if
being out-repaired" mechanism — siege belt-cut in `raidBot.py`, ore-barrier
clearing in `ecoBot.py` — independently, ~25 minutes apart, with the same shape
and the same class of bug:

1. **F1 — `raidBot._beltGrindLow` never cleared on a successful kill.** Only the
   give-up branch popped the entry. A belt ground to death and later rebuilt by the
   enemy would be judged against the stale near-death low on first sight and barred
   with zero shots fired. (`ecoBot.py`'s ore-barrier twin already had the correct
   cleanup on a "no barrier here now" read — this bug was raidBot-only.)
2. **F2 — give-up didn't propagate to retargeting.** `_siegeTileNeedsWork` and
   `_nextSiegeTile` didn't consult `bot._beltGaveUp`, so a raider with no other
   nearby work could be routed to park beside a belt it had already sworn off, idle
   for up to `_BELT_GIVEUP_MEMORY_TURNS` (25 rounds).
3. **F3 — both mechanisms compared wall-clock elapsed rounds, not turns of actual
   engagement.** An interruption (pushed off by fire, flung, retargeted, broke) of
   ≥11 rounds caused an instant false give-up the instant work resumed, with zero
   shots having actually failed during the gap.

**Fix:** a new shared helper, `patience.stallCheck`/`stallForget`
(`bots/count/patience.py`), replacing both hand-rolled trackers. It stores
`(low, consecutive-stale-checks)` instead of `(low, round-hit-low)` — the counter
only advances on a call, so an absence leaves it untouched rather than accruing
against elapsed time, closing F3 by construction for both call sites at once (and
dissolving the `ct.get_current_round()` vs `bot.hivemind.global_turn` inconsistency
between the two implementations, since neither touches a round/turn value anymore).
`raidBot.py`'s belt-cut loop now calls `stallForget` on any adjacent tile that no
longer reads as a live enemy belt (closes F1), and `_siegeTileNeedsWork` treats a
barred belt as done (closes F2). The two hand-rolled copies collapse to one
implementation, retiring the AGENTS.md "one counter, not six" nit the duplication
was drifting toward.

A second Fable pass over the fix itself confirmed F1/F2/F3 fully closed (traced the
kill-then-rebuild path, the prune/retarget interplay, and every belt-tile
classification through the restructured loop) and found no new bugs — verdict
SOUND-WITH-NITS. Two of its four nits were fixed in the same pass:

- **N2 — `ecoBot._barrierRepaired` was called before confirming a shot could
  actually be taken**, so a builder merely holding (broke, mid-cooldown-adjacent)
  advanced the stall counter without contesting anything — could give up a barrier
  it never got to fight. Fixed: gated behind `ct.can_fire(ore)` first, matching the
  tighter pattern already used at the raidBot call site.
- **N3 — a `main.py` comment overclaimed** that `_beltGrindLow` clears "the moment"
  a tile stops being a live enemy belt; reworded to state the actual (best-effort,
  next-look) guarantee, matching N1 below.

**Accepted, not fixed:**
- **N1 (low) — the forget-on-death is best-effort, not immediate.** It only fires
  the next time this raider looks at that tile from adjacency on an act-eligible
  turn (not exposed, not broke, cooldown clear). Narrower than the pre-fix F1 (which
  bit on every ordinary kill-then-rebuild); needs a near-give-up grind on that exact
  tile immediately before a rebuild-while-away to bite. `ecoBot.py`'s equivalent
  cleanup has the same gating class, pre-existing.
- **N4 (non-issue) — `stallCheck` doesn't reset its own counter on a True return**;
  both current callers immediately `stallForget`, so this never surfaces. Documented
  in the helper's own docstring ("caller owns cleanup") rather than guarded in code.
- **F4 (§ carried over, still not fixed) — give-up memory-window asymmetry**: belt
  give-up self-heals in `_BELT_GIVEUP_MEMORY_TURNS`=25 rounds; ore give-up reuses
  `_skipOre`'s `ORE_SKIP_TURNS`=150, tuned for "unreachable node" not "out-repaired
  barrier". A barriered node near our own core whose defender wanders off stays
  unharvested for 150 rounds. Fixing cleanly needs `_skipOre` to take a custom
  duration (touches 4 call sites) or a bespoke fourth barred-set — deferred as a
  nit, not a bug.

Verified: mirror_gauntlet CLEAN across 15 maps both before this pass (baseline on
the unmodified feature commits) and after (with the fix applied), byte-compiled
clean throughout.

---

## 2026-08-21 — siege repairer guard (user-requested), one follow-up deferred

`raidBot._siege` now skips grinding a core-ring belt on any turn a visible enemy
builder stands on it or orthogonally adjacent to it (`_beltRepairGuardTiles`): an
adjacent builder is almost certainly repairing, and repair keeps pace with our
2 Ti/shot cut, so grinding is a guaranteed losing trade. Skip-this-turn, never a
`_beltGaveUp` bar — the builder is mobile, so the cut resumes the instant it steps
away. `_beltGrindLow` is left untouched on the skip so the ~22-Ti out-repair
`stallCheck` still fires against a *flickering* repairer (forgetting it there would
reset the stale count every unguarded turn and bleed ~1 Ti/turn forever). The old
stallCheck path stays as the backstop for out-repair the vision test can't see.

**Deferred follow-up — CLOSED (2026-08-21, user-requested, Fable-planned).**
`_nextSiegeTile` now deprioritizes a repairer-guarded belt with a `(noSafe, guarded,
dist)` sort key (the guarded belt sorts after real work, never excluded — the
`noSafe`-style tie-break the finding proposed), and returns a `productive` flag that
is False when the only pick left is a guarded belt. On a no-productive-work turn the
raider no longer parks: `_siege` latches the nearest core-relevant enemy harvester
(`_divertStart`, within `_HARVESTER_DIVERT_RADIUS = 8` of the core, uncovered) and
walks to a pad beside it (`_divertRun`, driven from the top of `pressCore` so it
overrides the close-in and can't oscillate), then drops a gunner on it directly via
`guns.dropGunnerOn`. Dropping directly — not waiting on `placeGunner` — is deliberate:
`placeGunner`'s 150-Ti `surplusReady` gate is almost never met mid-siege (a probe
found 0/91 otherwise-eligible turns qualified), so the divert is gated instead on
`_divertAffordable` (gunner cost + `_SIEGE_MIN_TI`, the siege gunner's own floor), and
the direct drop also sidesteps `placeGunner`'s tier ranking (a harvester at core-dist
6–8 sits below turrets in its 3-target probe cap). The divert refuses to walk/stand in
an enemy gunner's live line (bars the target self-healingly and hands the turn to
`_siege`'s step-off). The latch clears — returning to the ring — when the harvester is
covered, dead, unaffordable, exposed, unreachable, or after `_DIVERT_GIVEUP_ROUNDS =
15`. Parking beside a guarded belt stays the fallback only when no harvester qualifies.

(Fable review of the first cut flagged three issues, all addressed here: walking into
gunner fire → the exposed-bar guard above; the radius-vs-tier mismatch → the direct
drop; and the 150-Ti surplus gate leaving it inert → `_divertAffordable`. One low,
self-healing item is left open: while a published sentinel emplacement preempts the
divert higher in `runRaidBot`, the wall-clock give-up clock keeps ticking, so a valid
harvester can occasionally be barred without 15 turns of real driving.)
