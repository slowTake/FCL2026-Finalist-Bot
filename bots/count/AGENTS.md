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
| `main.py` | `Player` dispatch and the per-unit state manifest — routes CORE to spawn, raid-target publishing and ammo top-up; BUILDER_BOT to `raidBot` or `ecoBot` by role (first raider also plants the launcher); SENTINEL to `sentinelBot`; GUNNER to `guns.gunnerLoop`; LAUNCHER to `launcher`. |
| `movement.py` | Generic `walkTo`/`stepToward`/`exploreStep` — plan-once, cache, follow. |
| `pathfindingHelpers.py` | Shared toolbox: tile reads, impassability, core geometry, network connectivity (`buildNetwork`), cost function + heuristic for A*. |
| `pathing.py` | Generic weighted 4-connected A*/BFS (`astarFromSeeds`, `bfsFromSeeds`) — no game knowledge by design. |
| `patience.py` | Single shared "give up yet?" counter (`holdTask`/`clearHold`/`waitTi`). |
| `raid.py` | Core-side sentinel emplacement selection (geometry-driven), plus the raider's local fallback when the published site goes stale. |
| `guns.py` | Turret target priorities, the shared hostile gatherer, gunner/sentinel fire+rotate loops, core-side ammo conversion, the `TI_SURPLUS_THRESHOLD` combat gate (`surplusReady`), and `placeGunner` — the raider's opportunistic gunner drop on an adjacent pad bearing on an enemy harvester/belt. |
| `store.py` | The communication-store layout: which slot holds what, and the only writer for the ones we own. |
| `launcher.py` | All launcher logic: the first raider plants one beside the core; builders post their own goal to `store.LAUNCH_REQUEST` and hold; the LAUNCHER loop defends first (flings a grabbable enemy builder back toward the enemy core) then throws a requesting ally toward its goal. A builder sealed in a pocket for `POCKET_STUCK_TURNS` (with >80 Ti) buys a throwaway launcher to fling itself out (`rescueFromPocket`, served by a rescue-flagged request in bit 29); that launcher self-detonates after the throw. |
| `relays.py` | Belt-laying/relay logic: plan, lay, contested-tile concession, obstruction clearing. |
| `respawn.py` | Core spawn logic: builder cap, spawn-tile selection, raider designation. |
| `telemetry.py` | Optional (`COUNT_CPU=1`) per-unit CPU sampling to stderr. |
| `hivemind/` | Symlinks into `components/` — shared with baron/bandit, see below. |
| `roles/ecoBot.py` | Builder economy role: harvester build/seize, ore seeking/picking, and `repairCoreBelts` — an idle builder grinds an enemy barrier that has severed a live supply line near our core (barrier adjacent to our network with a real feeder) and re-lays the belt. Barrier-only by design: a plain gap is indistinguishable from a normal relay still being built, so keying off gaps churned live relays. |
| `roles/guardianBot.py` | Guardian role: the standing home-defence body. Claimed at runtime by the first eco-builder to *deliver* a harvester (not named at spawn like raiders), and held via a liveness heartbeat in the `ROLES` slot so it recovers from death. Ladder: heartbeat → `blockGunLine`/`maintainGunLineBlock` (~3-Ti barrier in a connecting gunner's line of fire) → `guns.defendCore` → `defSentinel` (out-of-leash sentinel/launcher answer, built from inside the leash) → return-to-leash → quiet economy (belt repair / heal, now including turrets) → patrol. See `docs/adr/0001-guardian-role.md`. |
| `roles/raidBot.py` | Raider role — a core-sieger, never an eco bot. Drops a gunner beside enemy harvesters (`guns.placeGunner`, harvesters only), builds surplus-gated sentinels (published emplacement + `buildLocalSentinel` to densify). With nothing to build it `pressCore`s: at the enemy core ring it severs the incoming belts and walls the ring with barriers (`_siege`), else it closes in. It never harvests, relays, or hijacks (that's `ecoBot`'s job). The primary raider (`ROLE_RAIDER`) carries a liveness heartbeat like the guardian's, so a dead one can be reclaimed by an eligible eco-builder (`claimIfOpen`) instead of ending offense for the match — `ROLE_RAIDER_2` and replenishment raiders don't (no spare store bits for a second heartbeat), so they stay unrecovered. |
| `roles/sentinelBot.py` | Sentinel role: fire only — facing is permanent, so there is no rotate step. |
| `docs/adr/` | Architecture decision records — short, dated, one decision each (baron's format). |
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
- **Store slots go through `store.py`.** It owns the layout and refuses writes
  to the slots HiveMind owns. No bare `read_store(4)` integer literals.
- **A docstring may state its own contract; it may never state who calls it.**
  Claims about call sites rot silently — three functions here once asserted
  callers that did not exist, and one of them was load-bearing in a review.
- **Keep files single-purpose and small.** If a file is answering more than
  one kind of question, it's a sign to split it.
- **Trust the code over docstrings/docs when they disagree** — instrument and
  check rather than assume the doc is right (see `docs/mistakes.md`).
- **New tuning constant?** Ask whether it needs to be a toggle at all before
  adding it — baron's `policy.py` accumulated dozens of independently
  toggleable knobs that became hard to reason about combinatorially.
