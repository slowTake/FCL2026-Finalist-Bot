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
2. **Point 2 of the originating request — gunner spam on passing builders.**
   `defendCore._CORE_THREAT_TYPES` still includes `BUILDER_BOT`, so an eco-builder
   drops a gunner on an enemy builder merely walking past the core (1–2 shots,
   dead investment). With a guardian now standing, move that gate off the eco
   path: stop placing over a *passing* builder (baron did exactly this), and gate
   the guardian's builder-response on the builder being *occupied* —
   `get_action_cooldown(enemyId) != 0` (it built/fired this turn) AND adjacent to
   one of our structures. Validate the cooldown proxy empirically first; the
   engine exposes no enemy action/target getter.
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
