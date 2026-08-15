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
