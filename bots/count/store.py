"""The team communication store: who owns which slot, and the only writer.

Sixteen u32 slots shared by every unit on the team. Slots 4-15 belong to the
frozen HiveMind component (builder registry, delta ledger, metronome, map id,
packed cores) — reading them is fine, writing one corrupts the ledger for every
unit at once, so `put` refuses. Slots 0-3 are ours.

Role indices share one slot rather than taking one each. A builder index is
small (respawn.MAX_BUILDERS is 5), so four roles fit inside 20 bits, and the
alternative spends our last free slot on the second role that wants one — which
is the position we were already in with a single raider.

This module is the one place the layout is written down. Before it, three
different files each documented a different and incorrect version of it.
"""

from __future__ import annotations

from fcode import Controller

# --- Ours -------------------------------------------------------------------

HARVESTERS = 0        # packed: running harvester count + the primary raider's
                      # liveness heartbeat (see the packing note below)
RAID_TARGET = 1       # packed sentinel emplacement, or 0 (raid.packTarget)
ROLES = 2             # packed builder indices, one field per role
LAUNCH_REQUEST = 3    # transient launch request: packed (requester, target) or 0,
                      # with a rescue flag in bit 29 (a pocketed builder's self-rescue,
                      # served only by the launcher adjacent to it — launcher.runLauncher).
# See launcher.packRequest for the encoding. (The 2026-08-15 roadmap once reserved
# this slot "free for defence" and warned a persistent defence signal could not
# share a transient one; the guardian landed inside ROLES instead, so the conflict
# never materialised and slot 3 is the launcher's alone.)

# --- HiveMind's. Read freely, never write. ----------------------------------

BUILDERS = 4          # lifetime builders spawned; also the my_index allocator
PACKED_CORES = 15

_FIRST_HIVEMIND_SLOT = 4

# --- Role fields inside ROLES -----------------------------------------------
# 5 bits each, so an index fits with room to spare. 0 means "nobody holds this
# role" — real indices start at 1, which is what makes the empty store mean
# what it should.

ROLE_RAIDER = 0
ROLE_RAIDER_2 = 1     # second raider — the opening pair raids (respawn.py)
ROLE_GUARDIAN = 2     # home-defence guardian — first eco-builder to deliver a
                      # harvester claims it (roles/guardianBot.py)

_ROLE_BITS = 5
_ROLE_MASK = (1 << _ROLE_BITS) - 1

# The guardian carries a liveness heartbeat so a dead guardian's claim goes stale
# and the next eligible eco-builder re-claims it — no builder-respawn machinery
# required (roadmap #3 is still open). Twelve bits of masked round number ride in
# the high end of the ROLES slot, above the three 5-bit role fields (bits 0-14),
# so the guardian needs no slot of its own. A match is ~1000 rounds and the field
# holds 4096, so it never wraps in practice; the age read below stays wrap-aware
# regardless.
#
# Write-ordering safety: stampGuardian and assignRole both do a masked
# read-modify-write over the whole ROLES word, each clearing only its own bits,
# so they compose *when they don't land the same round*. ROLE_RAIDER now has a
# live re-writer (raidBot.claimIfOpen, below) that can fire in the same rounds
# the guardian is stamping — the old non-overlap invariant this comment used to
# rely on no longer holds. The reason it's still safe: every writer here is
# either a repeating heartbeat (guardian) or re-checks "do I still need to
# write this" every turn until it sees its own claim reflected (raidBot's
# reclaim). If two units' writes land the same round, the engine's buffering
# means one is simply lost for that round, exactly like a guardian's heartbeat
# occasionally landing a round late — self-correcting on the next check, never
# corrupting, since each writer only ever touches its own bits. What this
# argument does *not* cover: a one-shot, non-repeating write racing a same-round
# collision would just lose that write silently. There isn't one of those left
# on this word — check before adding one.
_GUARDIAN_HB_SHIFT = ROLE_GUARDIAN * _ROLE_BITS + _ROLE_BITS  # 15
_GUARDIAN_HB_BITS = 12
_GUARDIAN_HB_MASK = (1 << _GUARDIAN_HB_BITS) - 1

# The primary raider (ROLE_RAIDER only — see raidBot.claimIfOpen) carries the
# same kind of liveness heartbeat, so a dead one can be reclaimed by an
# eligible eco-builder instead of leaving offense permanently unheld. There is
# no free store slot left (0-3 are all spoken for, 4-15 are HiveMind's) and no
# spare room left inside ROLES for a second full-width heartbeat (5+5+5 role
# bits + 12 guardian heartbeat bits already fills 27 of 32), so this one rides
# in HARVESTERS' high bits instead. HARVESTERS was, until this, a bare running
# count with no reader anywhere in the codebase (confirmed by grep) — repacking
# it is free precisely because there was nothing to break. 12 bits matches the
# guardian's width for the same reason: a match is ~1000 rounds and the field
# holds 4096, so the age arithmetic in raidBot.py never wraps in practice.
_RAIDER_HB_SHIFT = 20
_RAIDER_HB_BITS = 12
_RAIDER_HB_MASK = (1 << _RAIDER_HB_BITS) - 1
_HARVESTERS_COUNT_MASK = (1 << _RAIDER_HB_SHIFT) - 1  # low 20 bits


def put(ct: Controller, slot: int, value: int) -> None:
    """Write one of our slots.

    Raises on a HiveMind slot rather than writing it. That trade is deliberate:
    an exception kills the unit for the match (Player.run swallows it), which is
    loud and shows up in the gauntlet immediately, whereas a stray write into
    the delta ledger corrupts what every unit believes about the map and would
    be diagnosed days later. This can only fire on a coding mistake, never on
    game state.
    """
    if slot >= _FIRST_HIVEMIND_SLOT:
        raise ValueError(f"slot {slot} belongs to HiveMind — do not write it")
    ct.write_store(slot, value)


def readRole(ct: Controller, role: int) -> int:
    """The builder index holding `role`, or 0 if nobody does."""
    return (ct.read_store(ROLES) >> (role * _ROLE_BITS)) & _ROLE_MASK


def assignRole(ct: Controller, role: int, index: int) -> None:
    """Name the builder that owns `role`, leaving the other roles untouched.

    Silently ignores an index too large to encode — losing one role assignment
    costs a body doing the wrong job, while a truncated write would corrupt the
    neighbouring role's field too.
    """
    if not 0 <= index <= _ROLE_MASK:
        return
    shift = role * _ROLE_BITS
    keep = ct.read_store(ROLES) & ~(_ROLE_MASK << shift)
    put(ct, ROLES, keep | (index << shift))


def stampGuardian(ct: Controller, index: int, round_n: int) -> None:
    """Name the guardian and refresh its liveness heartbeat in one write.

    Clears only the guardian index field and the heartbeat field, leaving the
    raider fields untouched — see the write-ordering note above. An index too
    large to encode is dropped whole rather than truncated, matching assignRole.
    """
    if not 0 <= index <= _ROLE_MASK:
        return
    ts = round_n & _GUARDIAN_HB_MASK
    shift = ROLE_GUARDIAN * _ROLE_BITS
    val = ct.read_store(ROLES)
    val &= ~(_ROLE_MASK << shift)
    val &= ~(_GUARDIAN_HB_MASK << _GUARDIAN_HB_SHIFT)
    val |= index << shift
    val |= ts << _GUARDIAN_HB_SHIFT
    put(ct, ROLES, val)


def readGuardian(ct: Controller) -> tuple[int, int]:
    """The guardian's builder index and its last heartbeat round (both masked).

    Index 0 means nobody holds the role. The heartbeat is a round number masked
    to _GUARDIAN_HB_BITS — staleness is the caller's to compute, with a
    wrap-aware age against its own masked current round."""
    val = ct.read_store(ROLES)
    index = (val >> (ROLE_GUARDIAN * _ROLE_BITS)) & _ROLE_MASK
    ts = (val >> _GUARDIAN_HB_SHIFT) & _GUARDIAN_HB_MASK
    return index, ts


def guardianHeartbeatMask() -> int:
    """The modulus round numbers are reduced to before comparison (for callers
    doing the wrap-aware age arithmetic)."""
    return _GUARDIAN_HB_MASK


def harvesterCount(ct: Controller) -> int:
    """Team's running harvester-built count — the low bits of HARVESTERS,
    shared with the primary raider's heartbeat (see the packing note above)."""
    return ct.read_store(HARVESTERS) & _HARVESTERS_COUNT_MASK


def incrementHarvesters(ct: Controller) -> None:
    """+1 to the harvester count, preserving the raider heartbeat bits sharing
    this slot. The only writer of the count half of HARVESTERS."""
    val = ct.read_store(HARVESTERS)
    count = ((val & _HARVESTERS_COUNT_MASK) + 1) & _HARVESTERS_COUNT_MASK
    put(ct, HARVESTERS, (val & ~_HARVESTERS_COUNT_MASK) | count)


def stampRaider(ct: Controller, round_n: int) -> None:
    """Refresh the primary raider's liveness heartbeat, preserving the
    harvester count sharing this slot. See raidBot.runRaidBot for the
    stamp-only-after-a-good-turn reasoning (mirrors stampGuardian)."""
    ts = round_n & _RAIDER_HB_MASK
    val = ct.read_store(HARVESTERS)
    val &= ~(_RAIDER_HB_MASK << _RAIDER_HB_SHIFT)
    val |= ts << _RAIDER_HB_SHIFT
    put(ct, HARVESTERS, val)


def readRaiderHeartbeat(ct: Controller) -> int:
    """The primary raider's last heartbeat round, masked. Staleness is the
    caller's to compute (raidBot._raiderStale), same shape as readGuardian."""
    return (ct.read_store(HARVESTERS) >> _RAIDER_HB_SHIFT) & _RAIDER_HB_MASK


def raiderHeartbeatMask() -> int:
    return _RAIDER_HB_MASK
