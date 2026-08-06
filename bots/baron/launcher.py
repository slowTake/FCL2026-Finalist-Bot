# Baron launcher — placement (guardian chore) and operation.
#
# ## What a launcher is worth
#    20 Ti, 30 HP, no facing, reach r²=26 (~5.1 tiles), cooldown 1. It does no
#    damage: it picks a builder bot off a tile in reach and throws it to another
#    tile in reach. Both teams' builders are fair game, and that is the whole
#    trick — one rule ("throw toward the enemy core") is defence against their
#    builders and transport for ours.
#      enemy builder → flung back the way it came, off our ore and out of our
#        apron. Cheaper than shooting it: a gunner needs 4 shots / 8 Ti to kill a
#        40 HP builder and it walks back anyway; the throw costs nothing per use.
#      ally builder → flung up to ~5 tiles toward the enemy core, i.e. a free
#        head start for harassers leaving home.
#
# ## launcherLoop (main.py dispatch)
# 1. enemyThrow — nearest enemy builder in reach → landing tile that minimises
#        distance to *their* core. Always outranks the ally throw: the cooldown
#        is the scarce resource and defence is why the thing was built.
# 2. allyThrow — (LAUNCH_ALLIES) most advanced ally builder in reach → same
#        landing rule. Two gates keep miners on the ground: round ≥
#        ALLY_LAUNCH_START, and the ally must already be on the outbound side of
#        the launcher (no further from the enemy core than the launcher itself).
#        A launcher cannot read a builder's my_index, so role is inferred from
#        position — see the caveat on _outboundAllies.
#
# ## placeLauncher (guardian chore, builder.py)
#    One launcher (MAX_LAUNCHERS) on the enemy-facing side of the core, at
#    Chebyshev 2..LAUNCHER_CHEBYSHEV so its radius covers the approach lanes
#    while the pad stays inside the guardian leash (4). Chebyshev ≤1 is excluded
#    so the core's spawn apron is never bricked over. Gated on LAUNCHER_MIN_TI
#    because 20 Ti in the opening is a harvester's worth of tempo.

from __future__ import annotations

from typing import Any

from fcode import Controller, Environment, GameConstants, Position

from pathing import manhattan

# Reach is the launcher's vision radius: r²=26, ~5.1 tiles.
LAUNCH_RANGE_SQ = getattr(GameConstants, "LAUNCHER_VISION_RADIUS_SQ", 26)


def _distSq(a: Position, b: Position) -> int:
    dx = a.x - b.x
    dy = a.y - b.y
    return dx * dx + dy * dy


def allyLauncherCount(ct: Controller, local_map: dict[Any, Any] | None) -> int:
    """Launchers we own, from the ledger.

    Same conventions and the same caveat as `guns.allyTurretCounts`: immobile, so
    no trail of stale cells, but the GC only prunes BUILDER_BOT, so a destroyed
    launcher's cell lingers until a unit sees the tile empty. Over-counting is the
    safe direction for both callers — placement stops one short, and
    `respawn._aliveBuilders` under-reports builders.
    """
    from fcode import EntityType

    if not local_map:
        return 0
    my_team = ct.get_team()
    count = 0
    for cell in local_map.values():
        ent = cell.get("entity")
        if not ent or ent.get("team") != my_team:
            continue
        if ent.get("type") == EntityType.LAUNCHER:
            count += 1
    return count


# ----------------------------------------------------------------------
# Operation
# ----------------------------------------------------------------------


def _launchGoal(ct: Controller) -> Position:
    """Aim point for every throw: enemy core, else its mirror, else map centre.

    A launcher has no HiveMind of its own — it reads the packed cores slot, which
    the core fills at turn 0 on any fingerprinted map. The mirror fallback assumes
    the usual point-symmetric layout and is only ever used as a *direction*, since
    targets are scored by distance to it.
    """
    from hivemind import CoreState, unpack_cores
    from main import SLOT_PACKED_CORES

    width, height = ct.get_map_width(), ct.get_map_height()
    ally, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
    if enemy[0] != CoreState.UNKNOWN:
        return Position(enemy[1], enemy[2])
    if ally[0] != CoreState.UNKNOWN:
        return Position(
            min(max(width - 1 - ally[1], 0), width - 1),
            min(max(height - 1 - ally[2], 0), height - 1),
        )
    return Position(width // 2, height // 2)


def _bestLandingTile(
    ct: Controller, bot_pos: Position, goal: Position
) -> Position | None:
    """Legal landing tile that gets bot_pos closest to goal, or None.

    Seeded with the bot's *current* distance, so a throw that does not make
    progress is never made — no shuffling a builder sideways, and no throwing an
    enemy builder that is already as far away as we can put it.
    """
    best: Position | None = None
    best_d = _distSq(bot_pos, goal)
    for target in ct.get_nearby_tiles(dist_sq=LAUNCH_RANGE_SQ):
        if target == bot_pos:
            continue
        d = _distSq(target, goal)
        if d >= best_d:
            continue
        if not ct.is_tile_passable(target):
            continue
        if not ct.can_launch(bot_pos, target):
            continue
        best, best_d = target, d
    return best


def _buildersInReach(ct: Controller) -> tuple[list[Position], list[Position]]:
    """(enemy, ally) builder tiles within launch reach."""
    pos = ct.get_position()
    me = ct.get_team()
    enemies: list[Position] = []
    allies: list[Position] = []
    for tile in ct.get_nearby_tiles(dist_sq=LAUNCH_RANGE_SQ):
        if tile == pos:
            continue
        bot_id = ct.get_tile_builder_bot_id(tile)
        if bot_id is None:
            continue
        if ct.get_team(bot_id) == me:
            allies.append(tile)
        else:
            enemies.append(tile)
    return enemies, allies


def _outboundAllies(
    allies: list[Position], pos: Position, goal: Position
) -> list[Position]:
    """Allies already on the enemy side of the launcher, most advanced first.

    Role is unreadable from a turret — `my_index` lives in each builder's own
    HiveMind and nothing publishes an id → index map — so "is this a harasser?"
    is answered by geometry instead. A builder that has passed the launcher on the
    way out is one that is leaving; miners orbiting the core sit behind it and are
    left alone. It is a heuristic, not a role check: a default builder relaying to
    an ore node out on the enemy side can be thrown too. If that shows up in
    replays, the fix is a store bit harassers set on themselves, not a wider gate.
    """
    launcher_d = _distSq(pos, goal)
    out = [p for p in allies if _distSq(p, goal) <= launcher_d]
    out.sort(key=lambda p: _distSq(p, goal))
    return out


def launcherLoop(ct: Controller) -> None:
    """Throw toward the enemy core: enemy builders home, then our own forward."""
    if ct.get_action_cooldown() != 0:
        return

    from main import ALLY_LAUNCH_START, LAUNCH_ALLIES

    pos = ct.get_position()
    goal = _launchGoal(ct)
    enemies, allies = _buildersInReach(ct)

    # Defence first — nearest threat, so the one already in our apron goes first.
    enemies.sort(key=lambda p: manhattan(p, pos))
    for bot_pos in enemies:
        target = _bestLandingTile(ct, bot_pos, goal)
        if target is not None:
            ct.launch(bot_pos, target)
            return

    if not LAUNCH_ALLIES:
        return
    if ALLY_LAUNCH_START is not None and ct.get_current_round() < ALLY_LAUNCH_START:
        return
    for bot_pos in _outboundAllies(allies, pos, goal):
        target = _bestLandingTile(ct, bot_pos, goal)
        if target is not None:
            ct.launch(bot_pos, target)
            return


def run_launcher(ct: Controller) -> None:
    launcherLoop(ct)


# ----------------------------------------------------------------------
# Placement
# ----------------------------------------------------------------------


def _launcherPads(ct: Controller, core_pos: Position, goal: Position) -> list[Position]:
    """Empty pads on the enemy-facing side of the core, best first.

    Chebyshev 2..LAUNCHER_CHEBYSHEV of the core footprint: 1 and closer is the
    spawn apron (`respawn._apronSites`) and must stay clear, and the outer bound
    keeps the pad inside the guardian leash so placing it never walks a guardian
    off station. Ore is skipped — a launcher on a node denies our own harvester.
    """
    from logistics import _chebyshevToCore
    from main import LAUNCHER_CHEBYSHEV

    pads: list[Position] = []
    w, h = ct.get_map_width(), ct.get_map_height()
    reach = max(2, LAUNCHER_CHEBYSHEV)
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            site = Position(core_pos.x + dx, core_pos.y + dy)
            if not (0 <= site.x < w and 0 <= site.y < h):
                continue
            cheb = _chebyshevToCore(site, core_pos)
            if cheb < 2 or cheb > reach:
                continue
            if not ct.is_in_vision(site):
                continue
            env = ct.get_tile_env(site)
            if env == Environment.WALL or env == Environment.ORE_TITANIUM:
                continue
            if not ct.is_tile_empty(site):
                continue
            pads.append(site)
    # Facing the enemy first, then compact against the core.
    pads.sort(key=lambda p: (_distSq(p, goal), _chebyshevToCore(p, core_pos)))
    return pads


def placeLauncher(bot, ct: Controller) -> bool:
    """Guardian chore: stand up the home launcher when the bank can spare it.

    The count comes from the ledger, so two guardians acting on the same turn can
    both pass the cap and build — bounded overshoot of one launcher (20 Ti), and
    the same race every ledger-counted placement in baron already runs.
    """
    from main import LAUNCHER_CHEBYSHEV, LAUNCHER_MIN_TI, MAX_LAUNCHERS

    if MAX_LAUNCHERS is None or MAX_LAUNCHERS <= 0:
        return False
    if LAUNCHER_CHEBYSHEV is None or LAUNCHER_CHEBYSHEV < 2:
        return False
    if bot.corePos is None:
        return False
    if ct.get_global_resources() < max(LAUNCHER_MIN_TI, ct.get_launcher_cost()):
        return False
    if allyLauncherCount(ct, bot.hivemind.local_map) >= MAX_LAUNCHERS:
        return False

    goal = bot.enemyCorePos or _launchGoal(ct)
    pads = _launcherPads(ct, bot.corePos, goal)
    if not pads:
        return False

    pos = ct.get_position()
    if ct.get_action_cooldown() == 0:
        for site in pads:
            if manhattan(pos, site) != 1:
                continue
            if ct.can_build_launcher(site):
                ct.build_launcher(site)
                return True

    best = pads[0]
    if manhattan(pos, best) <= 1:
        return False
    from logistics import moveToward

    return moveToward(bot, ct, best)
