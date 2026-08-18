"""No-logic test opponent: every unit does nothing, every round.

Used to exercise count's raider "clear core" path — with no builders spawned
and nothing built, the enemy core ring stays bare, so a raider that reaches it
sees no belts and no enemy builders (_coreClear True) and should place gunners
only: no barriers, no apron launcher.
"""

from fcode import Controller


class Player:
    def run(self, ct: Controller) -> None:
        return
