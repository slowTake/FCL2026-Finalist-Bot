# mistakes.md

Agent errors and traps, newest last. Keep entries short. If a mistake is really
a fact about the game, it belongs in `observations.md` instead.

---

## Baron is the anti-pattern, not the reference

10,417 lines over 16 modules; `logistics.py` + `logistics_helpers.py` alone are
2,889. Bandaid fixes layered on bandaid fixes until it became unmaintainable —
five chained functions (`beltFacingOk` → `_relayReachesCore` → `_walkMergeChain`
→ `_mergeCandidateNeighbor` → `_tryExtendThroughMerge`) to answer one question:
"does this belt reach the core?". Its 65 KB `CONTEXT.md` was never read by
anyone. Comment density was high enough to hide the logic.

Read baron to learn what went wrong. Port nothing from it except HiveMind.

## Do not edit the shared components

`hivemind.py`, `vision_v2.py`, `features.py` are symlinks into `components/`,
shared with baron and bandit. Editing one silently changes two other bots.
Raise it first; treat them as frozen.

## The hivemind architecture doc is stale

`components/hivemind_v2_architecture.md` claims slots 0–4 are core and 5–13 are
deltas. The code actually uses 4 (builder count), 5–12 (ledger), 13 (metronome),
14 (map id), 15 (packed cores). Trust the code — and for our own slots 0–3,
trust `store.py`, which is now the only place that layout is written down. It
used to be stated in three files, in three different and equally wrong ways.

## `except Exception: return` hides your bug

`Player.run` swallows everything, because an escaping exception permanently
destroys the unit. That also means a silent logic failure looks identical to a
clean run — the mirror gauntlet's crash scan reported zero crashes while the bot
mined zero titanium. When diagnosing, temporarily print the traceback to stderr
inside that handler, and put it back afterwards.

## Don't trust a docstring over the engine

`is_tile_passable` says the allied core is standable. It isn't. Both the earliest
relay bug and the midgard deadlock traced back to believing it. When behaviour
disagrees with the API docs, instrument and check.

## One counter, not six

Baron grew `relayStuck`, `relayTurretWaitTurns`, `reactFrozen`, `motionless`,
`stuck` and `ORE_DEADLOCK_TURNS` as near-duplicate retry counters because each
call site invented its own. All "should I give up yet?" questions go through
`patience.holdTask`.

## Arrived-and-idle is a state nothing was watching

`seekOre` guarded the *travelling* builder — a target it cannot reach gets
skipped after `walkTo` runs out of patience. Nothing guarded the builder that
had already arrived. `buildHarvester` ran before `seizeHarvester`, so beside an
enemy harvester `can_build_harvester` failed, `oreTarget` was cleared,
`seizeHarvester` then read `None` and bailed, and `seekOre` re-picked the same
tile and returned "already in position" without acting. Cold action cooldown,
identical next turn, forever. Order the checks so the specific case is asked
before the general one — and when adding a give-up path, ask which *state* it
protects, not just which function.

## Engine constants live in `GameConstants`

Import them. A comment here once asserted the 50-unit cap was "documented only
in can_spawn's docstring, not exposed as a constant" — it is
`GameConstants.MAX_TEAM_UNITS`, alongside `GUNNER_ROTATE_COST`,
`GUNNER_AMMO_COST` and `SENTINEL_AMMO_COST`, all star-exported from `fcode`.
Hardcoding one is bad; writing a comment claiming it does not exist is worse,
because the next reader stops looking.

## Memoising for the match needs the input to be *known*, not just immutable

`_isChokepoint` cached wall-derived answers for the whole game on the grounds
that walls never change. True of the world, false of our view of it: an
unexplored neighbour reads back `wall=False`, so a tile first evaluated while
its surroundings were dark was written off as open permanently — and A* expands
frontier tiles precisely when their neighbours are unknown. Cache a
knowledge-derived answer only once the knowledge is complete.

## The gauntlet is not deterministic

Two runs of identical code give different titanium, turn counts, and even
different win conditions — and so does `starter` against itself, so it is the
engine, not us, and not the TLE. Per-map numbers are noise. What a run actually
tells you is pass/fail, crashes, and TLE. Do not attribute an economy change to
a diff on the strength of one run.

## A give-up path must name the state it tears down

bb6ad66 replaced `clearRelayObstruction`'s grind-first branch with re-plan-first
and dropped the `or _replanAvoiding(...)` escape tail, leaving a bare
`return holdTask("clear", ...)`. When that patience expired it returned a plain
`False` — but `inRelay` and the through-route `relayPath` stayed live, so the
ladder fell through and re-entered the identical grind next turn. Compounded by
there being no per-tile stall detector on this path (unlike the ore grind), a
barrier the enemy repaired in lockstep was ground forever. Two lessons: a give-up
that returns `False` must also *clear* the state that would otherwise re-enter it
(here `relayAbort`), and every grind needs a `stallCheck` bound, not just a
`holdTask` one (the shared counter is reset by other ladder rungs anyway).

## A "re-plan first" refactor must account for the replan failing outright

bb6ad66's hostile-obstruction branch nulled `relayPath` before re-pricing the
barrier. CONNECTED and ROUTED were handled, but FAILED fell through to the grind
with the null still live — converting "no detour exists" into fire-one-shot-then-
abort, stranding a harvester whose sole exit the barrier blocked (strict reconnects
fail the same empty-seed check while it stands). When a refactor moves state-clearing
ahead of a fallible call, trace *every* outcome of that call to the end of the turn
AND into the next turn's re-entry path — not just the success ones. Fix: snapshot the
route before nulling, restore on FAILED.
