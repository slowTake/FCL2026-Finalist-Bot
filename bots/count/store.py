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

HARVESTERS = 0        # running count of harvesters built (ecoBot)
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
# so they compose. The one race — two masked writes landing the same round and
# the second dropping the first's field — cannot happen here: the core assigns
# the raider fields only during the opening spawns, well before any harvester is
# delivered, and guardian writes only begin once one has been. The two write
# windows never overlap.
#
# That non-overlap is an *invariant of the current roles*, not something the code
# enforces. Anything that gives the raider fields a live re-writer — the raider
# liveness stamp or role re-assignment on death that roadmap.md defers — puts a
# second writer on this word in the same rounds the guardian is stamping, and the
# argument above stops holding. Read this before adding one.
_GUARDIAN_HB_SHIFT = ROLE_GUARDIAN * _ROLE_BITS + _ROLE_BITS  # 15
_GUARDIAN_HB_BITS = 12
_GUARDIAN_HB_MASK = (1 << _GUARDIAN_HB_BITS) - 1


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
