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
- **Deferred, next increment:** baron's `blockGunLine` (a ~3-Ti barrier in a
  gunner's line to the core, cheaper than a turret) and `defSentinel`
  (out-of-leash sentinel answer); leash-bounded ore-seeking for the quiet
  guardian; scaling to N guardians (add role fields). The v1 ladder ships
  without them.
- **Related follow-up (point 2 of the originating request):** now that a
  guardian exists, `defendCore` can stop placing gunners on enemy *builders*
  merely passing the core (1–2 wasted shots), moving that gate — with a smarter
  "is the builder actually attacking/repairing" trigger off
  `get_action_cooldown(enemyId)` — onto the guardian. Not in this change.
