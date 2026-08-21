# ADR 0001: Guardian home-defence role

## Status

Accepted (2026-08-17). First ADR in count — the format is baron's
(`bots/baron/docs/adr/`), which the roadmap flagged as a practice worth keeping:
short, dated, one decision each.

## Context

count had no home-defence role — the roadmap's #1 capability gap. Its only
answer to a rush was `guns.defendCore`, called from inside the ecoBot task
ladder, which the code itself labels a "First-cut guardian... Reactive and
vision-limited": only a farming builder that happened to be near the core *and*
could see the threat responded, and it never left a relay to come home. There
was no body whose job was defence, so the response was slow and inconsistent.

## Decision

A dedicated **guardian** role (`roles/guardianBot.py`), one per team for now.

- **Assignment is runtime, not spawn-time.** Raiders are named by index at
  birth (`respawn._OPENING_RAIDERS`); the guardian is *claimed* by the first
  eco-builder to **deliver** a harvester (relay complete — income already
  flowing before a body leaves the economy). `relays.relayDone` sets
  `bot._harvesterDelivered`; `guardianBot.claimIfOpen`, called at the top of
  `runEcoBot`, writes the claim.
- **The claim carries a liveness heartbeat** packed into the high bits of the
  `ROLES` store slot (`store.stampGuardian` / `readGuardian`). The guardian
  re-stamps every turn it runs; a claim older than `GUARDIAN_ALIVE_TTL` rounds
  is treated as dead and the next eligible eco-builder re-claims it. This is
  what makes the role *permanent* — it recovers from the guardian's death with
  no dependence on the still-unbuilt builder-respawn system (roadmap #3). No new
  store slot: the guardian index + heartbeat sit above the three 5-bit role
  fields, leaving the contested slot 3 (`LAUNCH_REQUEST`) untouched.
- **`runGuardian` is a priority ladder:** heartbeat → `guns.defendCore` (shared
  with the eco path, bounded to the core-threat radius so it never chases a
  threat far from home) → return-to-leash → quiet economy (`repairCoreBelts` +
  heal, both near-core by construction) → patrol. When quiet the guardian stays
  useful rather than idle, but it deliberately does **not** seek fresh ore —
  committing to a new harvester's relay would pull it away from the core, the
  one thing a guardian must not do.

## Consequences

- A rush at the core now draws fire from a standing body immediately, not only
  when a farming builder happens by.
- `guns.defendCore` is unchanged and still runs in the eco ladder as the
  fallback for threats the guardian can't reach yet.
- **Deferred, next increment:** leash-bounded ore-seeking for the quiet
  guardian; scaling to N guardians (add role fields). The v1 ladder ships
  without them.
- **`blockGunLine`/`maintainGunLineBlock` and `defSentinel`: landed
  2026-08-21 (2)**, re-derived from baron's `defence.py` — see
  `docs/roadmap.md`'s entry for the design and the real bug testing caught
  in `defSentinel`'s first cut.
- **Point 2 of the originating request (shipped in the same change):** a gunner is
  no longer placed on an enemy builder merely passing the core (1–2 wasted shots).
  `defendCore` now answers a builder **only when it is grinding one of our
  buildings** — `_builderThreateningStructure`: one of ours orthogonally adjacent.
  The gate is universal, not guardian-only: both the guardian and the eco-path
  fallback apply it, so an *attacking* builder is still answered before the
  guardian is even claimed (early belt-cuts are devastating), while a *passing* one
  never is. `BUILDER_BOT` was removed from `guns._CORE_THREAT_TYPES` (the
  "always a threat" set) and handled through the adjacency gate instead.

  The trigger sketched here originally was `get_action_cooldown(enemyId) != 0` —
  that does not exist: the engine's `get_action_cooldown`/`get_move_cooldown` take
  **no** entity id (verified in `fcode/_types.py`), and there is no target/order
  getter, so an enemy's *action* state is unreadable. Adjacency is the stand-in —
  build and fire are adjacent-only, so a builder attacking our structure must be
  next to it, while one crossing open ground is not. (A lesson in the repo's own
  rule: trust the code over a docs claim.)

  Adjacency is not, however, the *only* available signal, as an earlier draft of
  this ADR claimed: `get_hp(id)`/`get_max_hp(id)` do take an id (`heal.py` already
  uses them), so "the adjacent ally building is **damaged**" is a strictly
  stronger read — it distinguishes a builder that has actually started grinding
  from one merely standing beside our belt. It costs one turn of reaction delay
  (the first hit lands before HP drops) against a ~15-turn grind, and would cut
  the residual false positives near belt corridors. Worth measuring as a
  refinement; the current gate ships without it.

  **Superseded (2026-08-21):** the adjacency gate and `_builderThreateningStructure`
  were removed. `defendCore` no longer answers enemy builders *at all* — even a
  grinding one — for either the guardian or the eco path: in play the gunners spent
  on mobile builders cost more Ti than the damage they prevented. `_CORE_THREAT_TYPES`
  is now the whole story (turrets + launcher), and a separate
  `GUNNER_BUILDER_ROTATE_COOLDOWN` keeps an *existing* gunner from churning Ti to
  re-aim after a builder every turn it jinks.

  Accepted cost of the removal: a builder that grinds a core belt to a *gap* (not a
  barrier) now draws no placed response. `repairCoreBelts` does **not** cover it — it
  is barrier-only by design (a gap reads identically to a relay still being laid) —
  so this grind case is genuinely undefended, judged worth it against the Ti a gun
  bled chasing the mobile griefer. Closing it later means gap-recovery on an
  *established* line (distinct from `verifyRelay`/`reconnectOrphan`), not a gun.
