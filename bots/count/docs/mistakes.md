# mistakes.md

Agent errors and traps, newest last. Keep entries short. If a mistake is really
a fact about the game, it belongs in `observations.md` instead.

---

## Baron is the anti-pattern, not the reference

~7,500 lines over 12 modules; `logistics.py` + `logistics_helpers.py` alone are
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
14 (map id), 15 (packed cores) — so 0–3 are free. Trust the code.

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
