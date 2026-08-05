# Baron builder roles — human task lists (check behavior here first)
#
# ## Assignment (my_index > 0; idx % 5) — 2 guardians / 2 harassers / 1 default
#    1 → guardian · 2 → harasser · 3 → default · 4 → harasser · 0 → guardian
#
# ## all roles (runBuilderRoles, before dispatch)
#    breakEncirclement — motionless (bot.stuck) ≥ ENCIRCLED_TURNS with no legal
#        move in any direction → destroy the cheapest adjacent ally building
#        (barrier → belt → harvester → turret) and step through, else try firing
#        at an adjacent enemy building. Known case: a harasser fencing itself in
#        with its own denyBelt barriers.
#
# ## guardian (orchestrator: runGuardian below; helpers in defence.py)
#    idx % 5 in (1, 0) (builders 1,5,6,10,11,…)
#    Ignores wave. Home defence is entirely the guardians' job — no core-HP recall.
# 1. leash — outside Chebyshev 4 of core → moveToward core
# 2. reactEnemyTurret — defGun / defendHome / healAlly (skipEconomy=False: an
#        unactionable turret must not block patrol. homeOnly=True: no action that
#        needs leaving the leash — contestTurret and unleashed defGun both place
#        on a pad by the target, which the leash step then undoes — ping-pong)
# 3. defGun — near core; gunners only; homeOnly → targets inside the leash only
# 3b. defSentinel — enemy turret within TURRET_THREAT_CHEBYSHEV of core (ledger,
#        not just own vision) → our own sentinel on a pad inside the leash. Its
#        5.7 reach covers what a gunner's 3.6 cannot, without leaving home.
#        Above defendHome: healing loses to a sentinel (18/shot vs 4 HP/turn).
# 4. defendHome — attack enemy in vision, else heal core if HP < 500
# 5. healAlly — core → gunner/sentinel → harvester
# 6. clearBarriers — fire / move to nearest enemy barrier inside the leash only
# 7. economy — only while no enemy turret/builder is in vision (GUARDIAN_ECONOMY
#        in main.py). nearCoreEconomy=True, so ore is limited to the leash:
#        buildHarvester on empty leash ore, then maintainHarvester, which also
#        finishes/repairs a harvester's belt line back to core. A visible enemy
#        unit drops the relay claim so defence isn't blocked by the inRelay
#        guards; the leash widens by GUARDIAN_ECON_SLACK while a claim is held.
# 8. patrolHome — cycle mid-side waypoints around core
#
# ## default — priority order (orchestrator: runDefault below)
#    idx % 5 == 3 (builders 3,8,13,…)
#    Never recalled home — economy runs regardless of ally core HP.
#    0. reactEnemyTurret — enemy gunner/sentinel/launcher in vision → placeGun /
#        defGun / defendHome / healAlly; skip economy this turn. Skipped entirely
#        while inRelay: a committed belt is always finished.
#    0b. dodgeWhileRelaying — inRelay and standing in gunner LoS → avoidFire toward
#        ally core. Relays are dropped per-tile in relayToCore, never wholesale.
#    0c. breakOreDeadlock — motionless (bot.stuck) ≥ ORE_DEADLOCK_TURNS outside a
#        relay → blacklist the ore claim and re-pick. State-only, no turn cost.
#    Early phase (age < BUILDER_HOME_TURNS after birth_round): defend-first +
#    nearCoreEconomy (Chebyshev 4 ore). ≤0 disables. Harassers/guardians never enter.
#    Early (after react):
#      1. defGun — near core; gunners only
#      2. defendHome — near core; attack / heal core
#      3. healAlly — core → gunner/sentinel → harvester
#      4. buildHarvester — empty ore near core only
#      5. maintainHarvester — near-core ore / finish in-flight relay
#      6. recheckOreNodes — near-core empty → ally-blocked → harvester
#    After age gate (after react):
# 1. buildHarvester — empty ore in vision / HiveMind → build;
#        skip ore if visible enemy gunner is aiming at that tile
# 2. maintainHarvester
#    2a. checkOre — harvester → relayToCore; empty → build (same gunner-aim gate);
#        ally block → destroy (skip gunner/sentinel if that ore is threatened);
#        enemy gunner/sentinel/belt/launcher/barrier → placeGunnerOnOre (stub)
#    2b. relayToCore — abort only if the next belt tile is under a visible gunner's
#        LoS/arc; else path → facing / clearWrongBelt → placeRelayBelt → watermark
# 3. defGun — near core only; enemy builder/gunner/sentinel/launcher in vision → placeGun
#        (gunners only — never sentinels)
# 4. defendHome — near core only; attack enemy in vision, else heal core if HP < 500
# 5. healAlly — core → gunner/sentinel → harvester
# 6. recheckOreNodes — clear sticky oreSite; prefer empty → ally-blocked → harvester
# # idleHome — commented out (soft-idle trap)
#
# ## harasser (orchestrator: runHarasser below; helpers in offense.py)
#    idx % 5 in (2, 4) (builders 2,4,7,9,…)
#    OR wave attack: WAVE_ATTACK_TURN / WAVE_ATTACK_TI in main.py (>0 = set)
#    Guardians never enter this path.
# 1. snipeHarvester — visible uncovered enemy harvester → placeGun
# 2. offGun — placeGun/placeSentinel: core → gunner → sentinel → harvester → launcher;
#        ≤1 ally gunner covering each enemy harvester
# 3. avoidFire — under enemy gunner LoS → step to safe tile toward goal
# 4. denyBelt — barrier on the empty tile ahead of an enemy belt's facing; while
#        gun placement is unaffordable, also cut an enemy belt from an adjacent
#        tile and barrier it in place (sticky via bot.denyBeltTile; falls back to
#        mounting the belt if an adjacent shot is refused). Skips ore nearer our core.
# 5. seekHarvester — local_map enemy harvesters / scout ore nodes (vision updates map)
# 6. seekCore — moveToward enemy core; if stuck, orbit mid-side / ring waypoints

"""Builder role orchestrators — dispatch into logistics / offence helpers."""

from __future__ import annotations

from fcode import Controller, EntityType, Position

from hivemind import CoreState, unpack_cores
from logistics import (
    DEFEND_HOME_CHEBYSHEV,
    _chebyshevToCore,
    _nearCore,
    abortRelay,
    breakOreDeadlock,
    buildHarvester,
    contestTurret,
    defendHome,
    enemyTurretInVision,
    healAlly,
    maintainHarvester,
    moveToward,
    recheckOreNodes,
)
from defence import clearBarriers, defGun, defSentinel, patrolHome
from pathing import breakEncirclement
from offense import (
    avoidFire,
    denyBelt,
    offGun,
    seekCore,
    seekHarvester,
    snipeHarvester,
)


def _resolveCores(bot, ct: Controller) -> None:
    from main import SLOT_PACKED_CORES

    ally, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
    if bot.corePos is None and ally[0] != CoreState.UNKNOWN:
        bot.corePos = Position(ally[1], ally[2])
    if enemy[0] != CoreState.UNKNOWN:
        bot.enemyCorePos = Position(enemy[1], enemy[2])
    elif bot.enemyCorePos is None and bot.hivemind.enemy_core_pos is not None:
        bot.enemyCorePos = bot.hivemind.enemy_core_pos


def isGuardian(idx: int) -> bool:
    """True for builders 1,5,6,10,… (idx % 5 in {0, 1})."""
    return idx > 0 and idx % 5 in (0, 1)


def isHarasser(idx: int) -> bool:
    """True for builders 2,4,7,9,… (idx % 5 in {2, 4})."""
    return idx > 0 and idx % 5 in (2, 4)


def isDefaultRole(idx: int) -> bool:
    """True for builders 3,8,13,… (idx % 5 == 3)."""
    return idx > 0 and idx % 5 == 3


def homeGuardActive(bot, ct: Controller) -> bool:
    """True while default-role age is under BUILDER_HOME_TURNS after birth_round."""
    from main import BUILDER_HOME_TURNS

    if BUILDER_HOME_TURNS is None or BUILDER_HOME_TURNS <= 0:
        return False
    birth = bot.hivemind.birth_round
    if birth is None:
        return True  # brand-new — stay near core until indexed
    return ct.get_current_round() - birth < BUILDER_HOME_TURNS


def waveAttackActive(ct: Controller) -> bool:
    """True if a set wave gate (turn or Ti) has been reached."""
    from main import WAVE_ATTACK_TI, WAVE_ATTACK_TURN

    turn = WAVE_ATTACK_TURN
    if turn is not None and turn > 0 and ct.get_current_round() >= turn:
        return True
    ti = WAVE_ATTACK_TI
    if ti is not None and ti > 0 and ct.get_global_resources() >= ti:
        return True
    return False


def _reactEnemyTurret(
    bot, ct: Controller, *, skipEconomy: bool = True, homeOnly: bool = False
) -> bool:
    """Contest/defend when an enemy turret is in vision — never breaks off a relay.

    `skipEconomy`: when nothing could be done, still consume the turn so
    `maintainHarvester` cannot re-enter a relay while the turret stays visible.
    That guard only means anything for default builders. Guardians have no
    economy and pass False, so an unactionable turret leaves them free to fall
    through to `clearBarriers` / `patrolHome` instead of freezing in place.

    `homeOnly`: forbid anything that needs the caller to leave the leash.
    `contestTurret` places a gunner on a pad near *the target*, which for
    anything outside the leash is a tile the guardian's leash step marches it
    back from next turn, and `defGun` picks that target. Guardians pass True and
    answer turrets through `defGun` (inside the leash) and `defSentinel`
    (outside) instead; defaults have no leash and are unaffected.
    """
    if not enemyTurretInVision(ct):
        return False
    # Committed to a belt: finish it. relayToCore drops the relay itself if the
    # next tile is under a gunner; dodging is handled by _dodgeWhileRelaying.
    if bot.inRelay:
        return False
    if not homeOnly and contestTurret(bot, ct):
        return True
    if defGun(bot, ct, homeOnly=homeOnly):
        return True
    if defendHome(bot, ct):
        return True
    if healAlly(bot, ct):
        return True
    return skipEconomy


def _dodgeWhileRelaying(bot, ct: Controller) -> bool:
    """Mid-relay and standing in a gunner's LoS → step to a safe tile, homeward."""
    if not bot.inRelay:
        return False
    return avoidFire(bot, ct, goal=bot.corePos)


def _runDefaultHome(bot, ct: Controller) -> None:
    """Default early phase: defend first, then near-core economy only."""
    bot.nearCoreEconomy = True
    if _reactEnemyTurret(bot, ct):
        return
    if _dodgeWhileRelaying(bot, ct):
        return
    breakOreDeadlock(bot, ct)  # state-only; economy below re-picks this turn

    if defGun(bot, ct):
        return
    if defendHome(bot, ct):
        return
    if healAlly(bot, ct):
        return
    if buildHarvester(bot, ct):
        return
    if maintainHarvester(bot, ct):
        return
    recheckOreNodes(bot, ct)


def runDefault(bot, ct: Controller) -> None:
    """Default role: early near-core defend, then economy-first."""
    _resolveCores(bot, ct)
    if homeGuardActive(bot, ct):
        _runDefaultHome(bot, ct)
        return

    bot.nearCoreEconomy = False
    if _reactEnemyTurret(bot, ct):
        return
    if _dodgeWhileRelaying(bot, ct):
        return
    breakOreDeadlock(bot, ct)  # state-only; economy below re-picks this turn
    if buildHarvester(bot, ct):
        return
    if maintainHarvester(bot, ct):
        return
    if defGun(bot, ct):
        return
    if defendHome(bot, ct):
        return
    if healAlly(bot, ct):
        return
    recheckOreNodes(bot, ct)


_ENEMY_THREAT_TYPES = (
    EntityType.GUNNER,
    EntityType.SENTINEL,
    EntityType.LAUNCHER,
    EntityType.BUILDER_BOT,
)


def guardianEconomyOn() -> bool:
    from main import GUARDIAN_ECONOMY

    return bool(GUARDIAN_ECONOMY)


def _enemyUnitInVision(ct: Controller) -> bool:
    """Any enemy turret or builder in this unit's vision."""
    me = ct.get_team()
    for eid in ct.get_nearby_units():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) in _ENEMY_THREAT_TYPES:
            return True
    return False


def _guardianLeashOk(bot, ct: Controller) -> bool:
    """Leash test, widened by GUARDIAN_ECON_SLACK while working a node/relay.

    An ore sitting on the leash edge is built and relayed from a neighbouring
    tile, which is one step further out. Without the slack the leash step would
    march the guardian home every other turn and the relay would never finish.
    """
    if _nearCore(bot, ct):
        return True
    if not guardianEconomyOn():
        return False
    if not (bot.inRelay or bot.oreSite is not None):
        return False
    from main import GUARDIAN_ECON_SLACK

    slack = GUARDIAN_ECON_SLACK or 0
    if slack <= 0:
        return False
    reach = DEFEND_HOME_CHEBYSHEV + slack
    return _chebyshevToCore(ct.get_position(), bot.corePos) <= reach


def _guardianEconomy(bot, ct: Controller) -> bool:
    """Idle-time economy inside the leash: build harvesters, finish relays."""
    if not guardianEconomyOn():
        return False
    bot.nearCoreEconomy = True  # _allowOreSite → ore within the leash only
    breakOreDeadlock(bot, ct)  # state-only; picks below re-choose this turn
    if buildHarvester(bot, ct):
        return True
    return maintainHarvester(bot, ct)


def runGuardian(bot, ct: Controller) -> None:
    """Guardian: leash → react → defGun → defendHome → healAlly → clearBarriers
    → economy (only while nothing hostile is in vision) → patrol."""
    _resolveCores(bot, ct)
    bot.nearCoreEconomy = False
    if bot.corePos is None:
        return

    # Defence outranks economy: drop the relay claim so the inRelay guards in
    # _reactEnemyTurret / defGun don't lock the guardian out of fighting. Belts
    # already placed stay on the map and the watermark rebuilds on the re-pick.
    underAttack = _enemyUnitInVision(ct)
    if underAttack and (bot.inRelay or bot.oreSite is not None):
        abortRelay(bot)
        bot.oreSite = None

    if not _guardianLeashOk(bot, ct):
        moveToward(bot, ct, bot.corePos)
        return
    # skipEconomy=False: a turret we can't act on must not block patrol.
    # homeOnly=True: defGun / defSentinel answer turrets without leaving home.
    if _reactEnemyTurret(bot, ct, skipEconomy=False, homeOnly=True):
        return
    if defGun(bot, ct, homeOnly=True):
        return
    # Above defendHome: its no-enemy branch heals the core, which loses to a
    # sentinel parked outside the leash (18 a shot vs 4 HP/turn of healing).
    if defSentinel(bot, ct):
        return
    if defendHome(bot, ct):
        return
    if healAlly(bot, ct):
        return
    if clearBarriers(bot, ct):
        return
    if not underAttack and _guardianEconomy(bot, ct):
        return
    patrolHome(bot, ct)


def runHarasser(bot, ct: Controller) -> None:
    """Harasser: snipe → offGun → avoidFire → denyBelt → seekHarvester → seekCore."""
    _resolveCores(bot, ct)
    bot.nearCoreEconomy = False
    if snipeHarvester(bot, ct):
        return
    if offGun(bot, ct):
        return
    if avoidFire(bot, ct):
        return
    if denyBelt(bot, ct):
        return
    if seekHarvester(bot, ct):
        return
    seekCore(bot, ct)


def runBuilderRoles(bot, ct: Controller) -> None:
    """Dispatch: guardian → harasser/wave → default."""
    # Ahead of every role: boxed in on all four sides, nothing below can move.
    # Runs before breakOreDeadlock, which shares the stuck counter and resets it.
    if breakEncirclement(bot, ct, local_map=bot.hivemind.local_map):
        return
    idx = bot.hivemind.my_index
    if isGuardian(idx):
        runGuardian(bot, ct)
        return
    if waveAttackActive(ct) or isHarasser(idx):
        runHarasser(bot, ct)
        return
    runDefault(bot, ct)


# Back-compat
def run_builder_roles(bot, ct: Controller) -> None:
    runBuilderRoles(bot, ct)
