# AGENTS.md

Conventions and a module map for count. Kept short on purpose: baron's
254-line `CONTEXT.md` explained *why* at such length that nobody read it. If
you're about to add rationale here, it probably belongs in
[docs/observations.md](docs/observations.md) (facts about the game/engine) or
a short decision record instead — this file stays *where/how*, not *why*.

See also: [docs/mistakes.md](docs/mistakes.md) (agent traps, newest last) and
[docs/observations.md](docs/observations.md) (engine facts, design rationale,
known gaps) and [docs/roadmap.md](docs/roadmap.md) (what's next and why).

## Module map

| File | Purpose |
|---|---|
| `main.py` | `Player` dispatch — routes CORE to spawn+raid-target publishing, BUILDER_BOT to `raidBot` or `ecoBot` by index. |
| `movement.py` | Generic `walkTo`/`stepToward`/`exploreStep` — plan-once, cache, follow. |
| `pathfindingHelpers.py` | Shared toolbox: tile reads, impassability, core geometry, network connectivity (`buildNetwork`), cost function + heuristic for A*. |
| `pathing.py` | Generic weighted 4-connected A*/BFS (`astarFromSeeds`, `bfsFromSeeds`) — no game knowledge by design. |
| `patience.py` | Single shared "give up yet?" counter (`holdTask`/`clearHold`/`waitTi`). |
| `raid.py` | Core-side sentinel emplacement selection (geometry-driven, packs target into store slot 1). |
| `relays.py` | Belt-laying/relay logic: plan, lay, contested-tile concession, obstruction clearing. |
| `respawn.py` | Core spawn logic: builder cap, spawn-tile selection, raider designation. |
| `telemetry.py` | Optional (`COUNT_CPU=1`) per-unit CPU sampling to stderr. |
| `hivemind/` | Symlinks into `components/` — shared with baron/bandit, see below. |
| `roles/ecoBot.py` | Builder economy role: harvester build/seize, ore seeking/picking. |
| `roles/raidBot.py` | Raider role: walk to the published emplacement, build the sentinel. |
| `docs/mistakes.md` | Agent errors and traps. |
| `docs/observations.md` | Game facts, design rationale, known gaps. |
| `docs/roadmap.md` | Prioritized next steps and why. |

## Conventions

- **One orchestrator per role, calling single-purpose helpers.** Don't grow a
  role function into a god-function — factor a new helper instead.
- **All "should I give up yet?" logic goes through `patience.holdTask`.** Do
  not invent a new ad-hoc counter at a call site — baron grew six near-duplicate
  ones (`relayStuck`, `motionless`, `ORE_DEADLOCK_TURNS`, ...) this way.
- **`hivemind/*.py` are symlinks into `components/`, shared with baron and
  bandit.** Editing one silently changes the others. Raise it first; treat
  them as frozen.
- **Keep files single-purpose and small.** If a file is answering more than
  one kind of question, it's a sign to split it.
- **Trust the code over docstrings/docs when they disagree** — instrument and
  check rather than assume the doc is right (see `docs/mistakes.md`).
- **New tuning constant?** Ask whether it needs to be a toggle at all before
  adding it — baron's `policy.py` accumulated dozens of independently
  toggleable knobs that became hard to reason about combinatorially.
