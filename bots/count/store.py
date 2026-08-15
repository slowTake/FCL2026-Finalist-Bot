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
FREE = 3              # unclaimed — the next role's target belongs here

# --- HiveMind's. Read freely, never write. ----------------------------------

BUILDERS = 4          # lifetime builders spawned; also the my_index allocator
PACKED_CORES = 15

_FIRST_HIVEMIND_SLOT = 4

# --- Role fields inside ROLES -----------------------------------------------
# 5 bits each, so an index fits with room to spare. 0 means "nobody holds this
# role" — real indices start at 1, which is what makes the empty store mean
# what it should.

ROLE_RAIDER = 0

_ROLE_BITS = 5
_ROLE_MASK = (1 << _ROLE_BITS) - 1


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
