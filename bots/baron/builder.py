# Baron builder roles — human task lists (check behavior here first)
#
# ## Assignment (my_index > 0; idx % 5) — 2 guardians / 1 harasser / 2 defaults
#    1 → guardian · 2 → harasser · 3 → default · 4 → default · 0 → guardian
#    Capped: guardian slots past MAX_GUARDIANS (main.py) dispatch as non-guardians
#    — guardianRank 1,2,3,4 = builders 1,5,6,10, so a cap of 2 first bites at
#    builder 6. Past the cap, slots alternate by excess rank
#    (guardianRank - MAX_GUARDIANS): odd excess → harasser, even → default —
#    so overflow guardians split between the two roles instead of all becoming
#    one. See guardianOverflowIsHarasser.
#    Wave attack (WAVE_ATTACK_TURN/WAVE_ATTACK_TI, main.py): default slots
#    alternate by defaultRank parity too — odd ranks harass, even ranks keep
#    building — instead of the whole default cohort converting at once. See
#    waveDefaultStaysBuilder.
#
# ## all roles (runBuilderRoles, before dispatch)
#    breakEncirclement — motionless (bot.motionless) ≥ ENCIRCLED_TURNS with no legal
#        move in any direction → destroy the cheapest adjacent ally building
#        (barrier → belt → harvester → turret) and step through, else try firing
#        at an adjacent enemy building. Known case: a harasser fencing itself in
#        with its own denyBelt barriers. Own counter, never reset by anything but
#        real movement — unlike bot.stuck (also used by breakOreDeadlock etc.),
#        which _reactEnemyTurret's freeze branch zeroes every turn it consumes, so
#        an encircled builder sitting next to a visible-but-unactionable turret
#        would otherwise never reach ENCIRCLED_TURNS at all.
#
# ## guardian (orchestrator: runGuardian below; helpers in defence.py)
#    idx % 5 in (1, 0) (builders 1,5,6,10,11,…)
#    Ignores wave. Home defence is entirely the guardians' job — no core-HP recall.
# 1. leash — outside Chebyshev 4 of core → moveToward core
# 1b. blockGunLine — enemy gunner currently landing a shot on the core → barrier
#        (~3 Ti) directly in its line of fire, forcing it to chew through before it
#        can hit the core again. Cheaper than every turret response below, so it
#        runs first. Gunners only — no chew-through LoS model exists for
#        sentinels/launchers, so those fall through to reactEnemyTurret as before.
# 1c. maintainGunLineBlock — same tier, right after: rebuilds THIS guardian's own
#        gun-line barrier (bot.guardian.gunLineBlockTile) the instant it's destroyed, rather
#        than waiting on blockGunLine's own reactive re-place (which only fires once
#        the gunner has already landed another hit through the gap). Opportunistic —
#        only acts when the tile is already in vision, never a dedicated sortie.
# 2. reactEnemyTurret — defGun / defendHome / healAlly (skipEconomy=False: an
#        unactionable turret must not block patrol. homeOnly=True: no action that
#        needs leaving the leash — contestTurret and unleashed defGun both place
#        on a pad by the target, which the leash step then undoes — ping-pong)
# 3. defGun — near core; gunners only; homeOnly → targets inside the leash only
# 3b. defSentinel — enemy turret within TURRET_THREAT_CHEBYSHEV of core (ledger,
#        not just own vision) → our own sentinel on a pad inside the leash. Its
#        5.7 reach covers what a gunner's 3.6 cannot, without leaving home.
#        Above defendHome: healing loses to a sentinel (18/shot vs 4 HP/turn).
# 3c. avoidFire — standing in an enemy gunner's LoS → step to a safe tile,
#        goal=corePos so the dodge stays homeward and inside the leash. Below the
#        turret answers (placing a counter-gunner is worth eating a shot for) and
#        above every chore (healing 4 HP/turn while taking 10 is not).
#        Caveat: step 2's _reactEnemyTurret runs defendHome/healAlly internally, so
#        a guardian that can heal something will still do so from inside the line
#        before ever reaching this step.
# 3d. patchAdjacentCoreBelt — belt gap that both touches the core footprint
#        and is already adjacent to this guardian → place it, no walk. Ahead
#        of defendHome on purpose: defendHome returns True every turn it
#        heals, which otherwise starves reconnectBelts (parked well below
#        it) for as long as the core stays damaged, so a guardian stuck
#        healing never gets a turn to plug a gap sitting right next to it.
#        Zero-movement by construction — can never pull the guardian off the
#        core to fix a belt further away; that stays reconnectBelts' job.
# 4. defendHome — heal core if HP < 500. No attack branch: builder fire is
#        1.0 Ti/dmg against a gunner's 0.20, and the approach walked a 40 HP
#        builder into something dealing 10 dmg/turn. Turrets answer turrets.
# 5. healAlly — core → gunner/sentinel → harvester
#    Steps 6-8 are chores and share one gate, `not underAttack`: enemy turret OR
#    builder in vision, relaxed to turret-only until the team's first relay
#    completes (relayEverComplete, slot 0) — before there is any economy an enemy
#    builder in vision must not keep a guardian off ore.
# 6. clearBarriers — fire / move to nearest enemy barrier inside the leash only
# 6a. buildHarvesterOnClearedBarrier — runs ahead of clearBarriers itself: if the
#        barrier clearBarriers just killed sat on ore (bot.guardian.guardianOreBarrier, set
#        by clearBarriers while firing), build a harvester on it before wandering
#        off to the next barrier. Ti-starved holds position for up to
#        GUARDIAN_ORE_WAIT_TURNS turns (main.py) rather than giving up outright,
#        then forgets the tile so clearBarriers can move on.
# 7. reconnectBelts — ally conveyor inside the leash aiming at a layable empty
#        tile → build the patch belt. Facing must be *proven*: a neighbour one step
#        nearer the core already holding a belt/core, or (2-tile gap) that neighbour
#        layable with the tile beyond it reconnecting. Otherwise the break is skipped
#        entirely — no crow-flies guess, which used to march a line of belts at the
#        core one per turn and get torn out again by clearBeltObstacle. Ore-anchored
#        repair cannot reach these: a guardian's _allowOreSite confines it to leash
#        ore, so a break near the core owned by a harvester further out is
#        unclaimable. Above economy because a cut line yields zero while a new
#        harvester is incremental. Skips tiles within Chebyshev 2 of another builder
#        (a relay in progress owns its dangling head).
# 8. economy — (GUARDIAN_ECONOMY in main.py). nearCoreEconomy=True, so ore is
#        limited to the leash: buildHarvester on empty leash ore, then
#        maintainHarvester, which also finishes/repairs a harvester's belt line
#        back to core. A visible enemy unit drops the relay claim so defence isn't
#        blocked by the inRelay guards; the leash widens by GUARDIAN_ECON_SLACK
#        while a claim is held.
# 9. patrolHome — cycle mid-side waypoints around core at GUARDIAN_PATROL_OFFSET
#        (main.py, one tile inside the leash — zero slack while merely patrolling,
#        so a waypoint on the boundary itself would risk moveTowardLeash yanking
#        the guardian home mid-cycle); each side falls back to its two nearest
#        diagonal corners if the mid-side tile is walled (some maps wall N/S but
#        not the corners), so patrol coverage doesn't silently drop below 4 sides
#
# ## default — priority order (orchestrator: runDefault below)
#    idx % 5 in (3, 4) (builders 3,4,8,9,…)
#    Never recalled home — economy runs regardless of ally core HP.
#    0. reactEnemyTurret — enemy gunner/sentinel/launcher in vision → placeGun /
#        defGun / defendHome / healAlly; skip economy this turn — but only once the
#        team's first relay is done (skipEconomy=relayEverComplete, slot 0), else a
#        turret in vision starves the opening. Skipped entirely while inRelay: a
#        committed belt is always finished.
#    0b. dodgeWhileRelaying — inRelay and standing in gunner LoS → avoidFire toward
#        ally core. Threatened belt tiles are routed around in relayToCore.
#    0b2. openBlockedRoute — walk to oreSite proven unreachable, or relay from
#        relayOrigin proven unreachable → replan with doors passable to find the one
#        sealing the lane. Ally door (barrier/launcher/sentinel/gunner — never core
#        or harvester) → destroy: free, no cooldown. Enemy barrier → placeGun
#        (3 shots / 6 Ti vs a builder's 15 turns / 30 Ti; the gunner stays and kills
#        re-walls). Sticky via bot.blockadeDoor so the walk to the pad cannot lose
#        it. Above breakOreDeadlock, which clears the claim it needs. Terrain, core
#        or harvester in the way → falls through and the node is blacklisted.
#    0c. breakOreDeadlock — motionless (bot.stuck) ≥ ORE_DEADLOCK_TURNS outside a
#        relay → blacklist the ore claim and re-pick. State-only, no turn cost.
#        Fires immediately, without the wait, when A* has proven the claim
#        unreachable (pathKnownBlocked) — a walled-off node is not a timing
#        problem. Note skippedOres is still wiped every ORE_RECHECK_CLEAR_INTERVAL
#        (50) rounds, so an unreachable node does come back around.
#    Early phase (age < BUILDER_HOME_TURNS after birth_round): defend-first +
#    nearCoreEconomy (Chebyshev 4 ore). ≤0 disables. Harassers/guardians never enter.
#    Early (after react):
#      1. defGun — near core; gunners only
#      2. defendHome — near core; heal core if damaged (no attack branch)
#      3. healAlly — core → gunner/sentinel → harvester
#      4. buildHarvester — empty ore near core only
#      5. maintainHarvester — near-core ore / finish in-flight relay
#      6. recheckOreNodes — near-core empty → ally-blocked → harvester
#    After age gate (after react):
# 1. buildHarvester — empty ore in vision / HiveMind → build;
#        skip ore if visible enemy gunner is aiming at that tile
# 2. maintainHarvester
#    2a. checkOre — harvester → relayToCore (team-blind by default: an
#        enemy-owned harvester is relayed the same as our own, minus the build
#        step — STEAL_ENEMY_HARVESTERS, see logistics_helpers._harvesterOwnerAllowed
#        and CONTEXT.md's StealEnemyHarvesters); empty → build (same
#        gunner-aim gate); ally block → destroy (skip gunner/sentinel if that
#        ore is threatened); enemy gunner/sentinel/belt/launcher/barrier →
#        nothing (yields the turn)
#    2b. relayToCore — belts are placed/repaired straight through gunner fire now
#        (killing one costs the enemy more than it costs us to rebuild/heal it —
#        see _repairBeltIfDamaged). Path → facing / clearBeltObstacle →
#        placeRelayBelt → watermark
# 2c. denyHarvesterSupply (offense.py) — a harvester our own steal-relay just
#        claimed (an ally belt already lands next to it) may still have an enemy
#        belt cardinally touching it too. Cut that so 100% of the output routes
#        to us instead of leaking back to their network. Same call the harasser
#        role already runs — reused here so the default builder actually doing
#        the steal handles it itself. Unthrottled.
# 3. defGun — near core only; enemy gunner/sentinel/launcher in vision → placeGun
#        (gunners only — never sentinels; a builder is never a placement target,
#        though an already-placed gunner still opportunistically fires on one)
# 4. defendHome — near core only; heal core if HP < 500 (no attack branch)
# 5. healAlly — core → gunner/sentinel → harvester
# 5b. verifyHomeHarvester — every HARVESTER_VERIFY_INTERVAL_TURNS, re-walk the
#        belt line of our nearest-core harvester with bot.relayVerify set, which
#        forces beltFacingOk to prove each tile by vision instead of accepting a
#        ≤RELAY_LEDGER_TRUST_TURNS-old ledger entry. Without it a belt destroyed
#        out of sight lingers in local_map as a ghost, relayToCore declares the
#        line complete without looking, and the builder drops to exploreFrontier
#        and circles mid-map forever. Sits below every real-work step, so it only
#        ever spends a turn that was going to be a wander anyway.
# 5c. reconnectBelts (guard) — a quick core-anchored check-and-patch below every
#        real-work step: any near-core ally belt dangling? Fix that (walks home
#        if needed) rather than falling straight through to exploreFrontier's
#        aimless wander. Same call guardians already use for near-core patrol
#        repair (defence.py).
# 6. recheckOreNodes — clear sticky oreSite; prefer empty → ally-blocked → harvester
# 7. exploreFrontier(outward=False) — last resort, no ore anywhere to claim. Walks to
#        the nearest tile absent from local_map that borders known space, preferring
#        candidates with known ore nearby. Only reachable when the ore pool is truly
#        empty, which on an unfingerprinted map (map_id 0 → ORE_LOCATIONS empty) is the
#        normal state; on heart/sweden/vase the core sees no ore at turn 0 either, so
#        without this the economy never started. Sticky target, re-picked every
#        FRONTIER_REFRESH_TURNS — the ledger scan is too costly to run every turn.
# # idleHome — commented out (soft-idle trap)
#
# ## harasser (orchestrator: runHarasser below; helpers in offense.py)
#    idx % 5 == 2 (builders 2,7,12,…)
#    OR wave attack: WAVE_ATTACK_TURN / WAVE_ATTACK_TI in main.py (>0 = set) —
#        but only odd-ranked default slots (defaultRank; idx % 5 in {3,4});
#        even-ranked ones keep building (waveDefaultStaysBuilder), so the wave
#        gate doesn't dump the whole default cohort into harasser at once.
#    OR a capped guardian slot with odd excess rank (guardianOverflowIsHarasser)
#    Uncapped guardians never enter this path. Capped ones with odd excess rank
#    always do, independent of the wave gate; even-excess capped slots fall to
#    default instead (see runDefault).
# 1. snipeHarvester — visible uncovered enemy harvester → placeGun; skips one
#        already claimed by our own steal-relay (geometry.harvesterClaimedByAlly
#        — an ally belt already lands next to it), worth more alive than dead
# 1b. denyHarvesterSupply — a claimed harvester with an enemy belt still
#        cardinally adjacent → cut that belt (reuses denyBelt Mode B's
#        fire-then-barrier mechanic under its own bot.offense.supplyCutTile, fully
#        independent of denyBelt's own state/cooldown) so 100% of the output
#        routes to us instead of leaking back to their network. Unthrottled —
#        defends an asset already being captured for free.
# 2. offGun — killCore: core (only once bank ≥ CORE_PUSH_MIN_TI; harvesters are the
#        cheaper kill until then) tries placeSentinel then placeGun; a landed sentinel
#        arms a KILL_CORE_COMMIT_TURNS belt-cut commitment (offense.killCore) that
#        keeps cutting belts touching the core even once the bank recovers. Falls back
#        to gunner → sentinel → harvester → launcher on the nearest visible threat;
#        ≤1 ally gunner covering each enemy harvester; harvester branch also skips
#        one already claimed by our own steal-relay, same as snipeHarvester
# 2b. wallCore — enemy core known → wallEnemyCore: barrier one empty tile of its
#        perimeter, one action per call. Ungated by CORE_PUSH_MIN_TI (3 Ti, unthrottled,
#        opportunistic — worth taking any time a harasser is in position).
# 3. avoidFire — under enemy gunner LoS → step to safe tile toward goal
#    0. breakBeltDeadlock — motionless (bot.stuck) ≥ HARASS_DEADLOCK_TURNS →
#        blacklist sticky denyBeltTile / harassTarget / supplyCutTile into
#        bot.offense.skippedTargets and clear them. State-only, no turn cost. Needed because denyBelt's Mode B is
#        gated off while a gun is affordable, so a funded harasser beside a belt
#        correctly refuses to cut it (10 shots / 20 Ti for a 3 Ti conveyor) and then
#        had no way to give the target up. bot.stuck now also trips on tight
#        oscillation (main.OSCILLATION_WINDOW/DISTINCT_LIMIT), not just exact
#        standstill — a unit bouncing between two tiles used to never accumulate it.
# 3b. denyOre — barrier empty ore inside DENY_ORE_CHEBYSHEV of the enemy core:
#        3 Ti denies a 20 Ti harvester and its whole line, and they pay 6-30 Ti to
#        clear it. Two guards keep it off our side — _oreCloserToAlly (midline) and
#        DENY_ORE_MIN_ALLY_CHEBYSHEV (floor from our own core, for small maps where an
#        ore is marginally nearer them yet inside our working range). Skips occupied
#        nodes and tiles under gunner fire. Not a lock-out: pickEmptyOre admits our own
#        barriers and checkOre clears them to build. Candidates are filtered through
#        _dropSkipped (previously weren't — breakBeltDeadlock's blacklist had no
#        effect on this list) and a target moveToward just proved unreachable is
#        blacklisted immediately via pathKnownBlocked, not after HARASS_DEADLOCK_TURNS
#        more full A* searches. Both existed to fix a real bug: a harasser stuck on a
#        walled-off or harvester-blocked ore never reached denyBelt/seekHarvester
#        below it and re-ran a full uncached 800-iter search every turn — see also
#        MAX_PATH_SEARCHES_PER_TURN, a hard per-turn cap on full searches in
#        movement.moveToward that bounds the worst case regardless of which step in
#        the chain is looping.
# 4. denyBelt — Mode A: barrier on the empty tile ahead of an enemy belt's facing
#        (3 Ti, unthrottled). Mode B: cut an enemy belt from an adjacent tile and
#        barrier it in place (sticky via bot.offense.denyBeltTile; falls back to mounting the
#        belt if an adjacent shot is refused). Skips ore nearer our core.
#        Mode B starts (once this harasser's DENY_CUT_COOLDOWN has elapsed) when
#        either gun placement is unaffordable OR the target has no enemy builder
#        within UNDEFENDED_BELT_CHEBYSHEV — undefended means nothing can heal or
#        rebuild it, so the denial is effectively permanent regardless of the bank
#        (_worthwhileCutTarget). Once started it runs to the barrier regardless of
#        any of that (_cutInProgress), because a cut is 20 Ti / 10 turns and
#        abandoning it half-way spends the Ti for nothing. A belt healed back to full
#        HP stops counting as in-progress — that is the 4:1 heal-race bail-out.
# 5. seekHarvester — local_map enemy harvesters / scout ore nodes (vision updates map)
# 6. seekCore — moveToward enemy core; if stuck, orbit mid-side / ring waypoints
# 7. exploreFrontier(outward=True) — last resort, enemy core unknown so everything
#        above is inert. Same frontier scan, but biased *away* from our own core (which
#        is known even on an unfingerprinted map). Finding the enemy core is what makes
#        it pay off: _resolveCores now reads one out of the ledger via
#        _enemyCoreFromLedger, where before only the turn-0 fingerprint ever set it.

"""Builder role orchestrators — dispatch into logistics / offence helpers."""

from __future__ import annotations

from fcode import Controller, EntityType, GameConstants, Position

from geometry import (
    DEFEND_HOME_CHEBYSHEV,
    TURRET_TYPES,
    chebyshevToCore,
    nearCore,
)
from hivemind import CoreState, unpack_cores
from logistics import abortRelay, coreThreatened, relayEverComplete
from logistics_helpers import (
    breakOreDeadlock,
    buildHarvester,
    contestTurret,
    defendHome,
    enemyTurretInVision,
    exploreFrontier,
    finishNearbyHarvester,
    guardianLeash,
    healAlly,
    maintainHarvester,
    recheckOreNodes,
    verifyHomeHarvester,
)
from movement import moveToward, moveTowardLeash, openBlockedRoute
from defence import (
    _buildHarvesterOnClearedBarrier,
    blockGunLine,
    clearBarriers,
    defGun,
    defSentinel,
    finishNearbyBelt,
    maintainGunLineBlock,
    patchAdjacentCoreBelt,
    patrolHome,
    reconnectBelts,
)
from pathing import breakEncirclement
from offense import (
    _cutInProgress,
    avoidFire,
    breakBeltDeadlock,
    denyBelt,
    denyHarvesterSupply,
    denyOre,
    offGun,
    seekCore,
    seekHarvester,
    snipeHarvester,
    wallCore,
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
    elif bot.enemyCorePos is None:
        bot.enemyCorePos = _enemyCoreFromLedger(bot, ct)
        if bot.enemyCorePos is None:
            w, h = ct.get_map_width(), ct.get_map_height()
            cp = bot.corePos or ct.get_position()
            ex = w - 1 - cp.x if cp.x > w // 2 else w - 2 - cp.x
            ey = h - 1 - cp.y if cp.y > h // 2 else h - 2 - cp.y
            bot.enemyCorePos = Position(max(0, ex), max(0, ey))


def _enemyCoreFromLedger(bot, ct: Controller) -> Position | None:
    """Enemy core position from anything the team has actually seen.

    Both sources above are the turn-0 map fingerprint: slot 15 is packed once inside
    `_detect_and_store_map`, and `hivemind.enemy_core_pos` is set in the same call. So
    when the fingerprint misses, *seeing* the enemy core changed nothing and the whole
    offensive chain — `seekCore`, `denyOre`, `offGun`'s core branch — stayed inert for
    the rest of the game. It also fails open in two guards: `_oreCloserToAlly` and
    `_scoutOreTargets` both stop protecting our own ore when the enemy core is unknown.

    Reads only *this builder's* sightings: the delta ledger runs builder → core only,
    so another unit seeing the enemy core does not help this one. Superseded in practice
    by map identification, which publishes the enemy core to slot 15 for everyone — this
    remains the fallback for a map the tables cannot identify at all.
    Returns the footprint anchor (min x, min y) so it matches the packed convention.
    """
    me = ct.get_team()
    tiles = [
        (x, y)
        for (x, y), cell in bot.hivemind.local_map.items()
        if (ent := cell.get("entity")) is not None
        and ent.get("type") == EntityType.CORE
        and ent.get("team") != me
    ]
    if not tiles:
        return None
    return Position(min(t[0] for t in tiles), min(t[1] for t in tiles))


def isGuardian(idx: int) -> bool:
    """True for builders 1,5,6,10,… (idx % 5 in {0, 1})."""
    return idx > 0 and idx % 5 in (0, 1)


def guardianRank(idx: int) -> int:
    """1-based position among guardian slots: 1, 5, 6, 10, 11, … → 1, 2, 3, 4, 5, …

    Pure index arithmetic, so every unit agrees without reading anything shared.
    """
    return (idx // 5) * 2 + (1 if idx % 5 else 0)


def guardianCapped(idx: int) -> bool:
    """True for guardian slots past MAX_GUARDIANS — they dispatch as non-guardians."""
    from main import MAX_GUARDIANS

    if MAX_GUARDIANS is None or MAX_GUARDIANS <= 0:
        return False
    return guardianRank(idx) > MAX_GUARDIANS


def guardianOverflowIsHarasser(idx: int) -> bool:
    """Capped guardian slots alternate harasser / default past the cap.

    excess = guardianRank(idx) - MAX_GUARDIANS (1-based: 1st slot over the cap,
    2nd slot over the cap, …). Odd excess -> harasser, even excess -> default,
    so consecutive overflow slots split evenly instead of every excess
    guardian dumping into one role.
    """
    from main import MAX_GUARDIANS

    if MAX_GUARDIANS is None or MAX_GUARDIANS <= 0:
        return False  # guardianCapped is already False in this case too
    excess = guardianRank(idx) - MAX_GUARDIANS
    if excess <= 0:
        return False  # not actually capped; guardianCapped(idx) is False
    return excess % 2 == 1


def isHarasser(idx: int) -> bool:
    """True for builders 2,7,12,… (idx % 5 == 2)."""
    return idx > 0 and idx % 5 == 2


def isDefaultRole(idx: int) -> bool:
    """True for builders 3,4,8,9,… (idx % 5 in {3, 4})."""
    return idx > 0 and idx % 5 in (3, 4)


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


def defaultRank(idx: int) -> int:
    """1-based position among default-role slots: 3, 4, 8, 9, … → 1, 2, 3, 4, …

    Mirrors guardianRank's pure index arithmetic — every unit agrees without
    reading anything shared.
    """
    return (idx // 5) * 2 + (2 if idx % 5 == 4 else 1)


def waveDefaultStaysBuilder(idx: int) -> bool:
    """True for default-role slots that keep building even once wave attack fires.

    Once WAVE_ATTACK_TURN/WAVE_ATTACK_TI trips, runBuilderRoles used to send
    every non-guardian builder into runHarasser permanently — the whole default
    cohort (idx % 5 in {3, 4}) converts in one shot and never mines, expands, or
    replaces losses again for the rest of the game. That starves the crew: bank
    growth stalls right as WAVE_ATTACK_TI (which reads that same bank) fires, so
    the trigger never un-fires either. Alternating by defaultRank parity — odd
    ranks harass, even ranks keep building — splits the cohort instead of
    dumping all of it into one role, the same fix guardianOverflowIsHarasser
    applies to capped guardian slots.
    """
    return defaultRank(idx) % 2 == 0


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
        bot.reactFrozen = 0
        return False
    # Committed to a belt: finish it. relayToCore drops the relay itself if the
    # next tile is under a gunner; dodging is handled by _dodgeWhileRelaying.
    if bot.inRelay:
        return False
    if not homeOnly and contestTurret(bot, ct):
        bot.reactFrozen = 0
        return True
    if defGun(bot, ct, homeOnly=homeOnly):
        bot.reactFrozen = 0
        return True
    if defendHome(bot, ct):
        bot.reactFrozen = 0
        return True
    if healAlly(bot, ct):
        bot.reactFrozen = 0
        return True
    if skipEconomy:
        # Nothing actionable, but the turret stays in vision — this consumes the
        # turn every turn it's visible. bot.stuck keeps climbing the whole time
        # regardless of why the position didn't change (Player._run_builder tracks
        # position only, not cause), so by the time the turret finally leaves and
        # breakOreDeadlock gets a turn again, it blames and blacklists whatever
        # ore the builder happens to be standing on — a false deadlock caused by
        # this freeze, not by the ore being unreachable. Reset here so the count
        # only reflects turns actually spent failing at economy once react lets go.
        bot.stuck = 0

        from main import REACT_FREEZE_CAP_TURNS

        if REACT_FREEZE_CAP_TURNS and REACT_FREEZE_CAP_TURNS > 0:
            bot.reactFrozen = getattr(bot, "reactFrozen", 0) + 1
            if bot.reactFrozen > REACT_FREEZE_CAP_TURNS:
                # Capped: a turret parked nearby but unaffordable to answer
                # (GUN_PLACE_MIN_TI) used to freeze a default builder here for
                # 100+ turns straight. Past the cap, stop freezing and let the
                # chain fall through to economy — buildHarvester/checkOre/
                # relayToCore all still carry their own per-tile threat check
                # (tileThreatenedByGunner), so this only lifts the blanket
                # "turret merely visible" freeze, not tile-level danger
                # avoidance. Don't reset reactFrozen here: if economy also
                # can't make progress and the turret is still visible next
                # turn, this must not freeze for another full cap before
                # yielding again.
                return False
        return True
    return False


def _dodgeWhileRelaying(bot, ct: Controller) -> bool:
    """Mid-relay and standing in a gunner's LoS → step to a safe tile, homeward."""
    if not bot.inRelay:
        return False
    return avoidFire(bot, ct, goal=bot.corePos)


def _runDefaultHome(bot, ct: Controller) -> None:
    """Default early phase: defend first, then near-core economy only."""
    bot.nearCoreEconomy = True
    if _reactEnemyTurret(bot, ct, skipEconomy=relayEverComplete(ct)):
        return
    if _dodgeWhileRelaying(bot, ct):
        return
    if openBlockedRoute(bot, ct):
        return
    breakOreDeadlock(bot, ct)  # state-only; economy below re-picks this turn

    if defGun(bot, ct):
        return
    #if defendHome(bot, ct):
    #    return
    if healAlly(bot, ct):
        return
    if finishNearbyBelt(bot, ct):
        return
    if buildHarvester(bot, ct):
        return
    if maintainHarvester(bot, ct):
        return
    # Opportunistic: no ore/relay of our own to work this turn, but a dangling
    # ally belt end (most commonly a relay abandoned mid-build by a builder that
    # died — no on-death hook reports that to OrphanRelayRegistry) might be
    # sitting right next to us. Cheap, self-anchored — see finishNearbyBelt.

    # Same idea, for a harvester nobody has started relaying yet — see
    # finishNearbyHarvester.
    if finishNearbyHarvester(bot, ct):
        return
    if recheckOreNodes(bot, ct):
        return
    # Last resort: nothing to build or maintain. Only reachable when the ore pool is
    # genuinely empty, which on an unfingerprinted map is the normal state.
    exploreFrontier(bot, ct, outward=False)


def runDefault(bot, ct: Controller) -> None:
    """Default role: early near-core defend, then economy-first."""
    _resolveCores(bot, ct)
    if homeGuardActive(bot, ct):
        _runDefaultHome(bot, ct)
        return

    bot.nearCoreEconomy = False
    # skipEconomy consumes the turn when a visible turret leaves nothing to do —
    # it guards maintainHarvester against re-entering a relay, which only matters
    # once a harvester exists. Before the first relay it just starves the opening:
    # on a small map a turret is in vision almost every turn, and this is the only
    # economy builder in the first five spawns (idx 3 of 1..5).
    if _reactEnemyTurret(bot, ct, skipEconomy=relayEverComplete(ct)):
        return
    if _dodgeWhileRelaying(bot, ct):
        return
    # Above breakOreDeadlock, which clears oreSite — this needs the claim to know
    # what route was blocked. Removing the blockage beats giving up on the node.
    if openBlockedRoute(bot, ct):
        return
    breakOreDeadlock(bot, ct)  # state-only; economy below re-picks this turn
    if buildHarvester(bot, ct):
        return
    if maintainHarvester(bot, ct):
        return
    # A harvester our own steal-relay just claimed (an ally belt already lands
    # next to it — geometry.harvesterClaimedByAlly) may still have an enemy
    # belt cardinally touching it too, leaking part of its output back into
    # their network instead of routing 100% to us. Same cut-and-barrier this
    # harasser role already runs (offense.denyHarvesterSupply, step 1b there)
    # — reused here so the default builder actually doing the steal handles
    # it itself the moment it's in position, instead of waiting on a harasser
    # that may be busy elsewhere or never pass by. Unthrottled, like the
    # harasser call — defends an asset already being captured for free.
    if denyHarvesterSupply(bot, ct):
        return
    if defGun(bot, ct):
        return
    if defendHome(bot, ct):
        return
    if healAlly(bot, ct):
        return
    # Opportunistic: no ore/relay of our own to work this turn, but a dangling
    # ally belt end (most commonly a relay abandoned mid-build by a builder that
    # died — no on-death hook reports that to OrphanRelayRegistry) might be
    # sitting right next to us. Cheap, self-anchored — see finishNearbyBelt.
    if finishNearbyBelt(bot, ct):
        return
    # Same idea, for a harvester nobody has started relaying yet — see
    # finishNearbyHarvester.
    if finishNearbyHarvester(bot, ct):
        return
    # A quick core-anchored check-and-patch before falling through to the
    # last-resort steps below: any near-core ally belt dangling? Fix that
    # first. Same call guardians already use for near-core patrol repair
    # (defence.py) — reused here so a default doesn't wander off to
    # exploreFrontier while the core's own supply line sits cut nearby.
    if reconnectBelts(bot, ct):
        return
    # Below every real-work step, above the last-resort ones: this only fires on
    # a builder that would otherwise fall through to exploreFrontier's aimless
    # wander, which is exactly the builder that should be going home to check on
    # an old harvester whose line may have been cut behind our back.
    if verifyHomeHarvester(bot, ct):
        return
    if recheckOreNodes(bot, ct):
        return
    # Last resort: nothing to build or maintain. Only reachable when the ore pool is
    # genuinely empty, which on an unfingerprinted map is the normal state.
    exploreFrontier(bot, ct, outward=False)


# Turrets plus builders: the guardian's chore gate treats an enemy builder as
# hostile too, unlike the turret-only checks elsewhere.
_ENEMY_THREAT_TYPES = TURRET_TYPES + (EntityType.BUILDER_BOT,)


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

    The leash itself is dynamic (`guardianLeash`): wide while home is quiet,
    tight once the core sees a threat. The slack rides on top of whichever is
    current — it is a delta, not an absolute.
    """
    leash = guardianLeash(ct)
    if nearCore(bot, ct, leash):
        return True
    if not guardianEconomyOn():
        return False
    if not (bot.inRelay or bot.oreSite is not None):
        return False
    from main import GUARDIAN_ECON_SLACK

    slack = GUARDIAN_ECON_SLACK or 0
    if slack <= 0:
        return False
    reach = leash + slack
    return chebyshevToCore(ct.get_position(), bot.corePos) <= reach


def _harassLeashOk(bot, ct: Controller) -> bool:
    """True while inside HARASS_LEASH_CHEBYSHEV of the enemy core, or the enemy
    core isn't known yet (nothing to leash to — same fallback shape offGun
    uses for `enemy_core is not None`)."""
    from main import HARASS_LEASH_CHEBYSHEV

    if bot.enemyCorePos is None:
        return True
    if HARASS_LEASH_CHEBYSHEV is None or HARASS_LEASH_CHEBYSHEV <= 0:
        return True
    return chebyshevToCore(ct.get_position(), bot.enemyCorePos) <= HARASS_LEASH_CHEBYSHEV


def _guardianEconomy(bot, ct: Controller) -> bool:
    """Idle-time economy inside the leash: build harvesters, finish relays.

    denyHarvesterSupply after maintainHarvester for the same reason runDefault
    calls it (see runDefault's 2c) — a guardian can team-blind-steal an enemy
    harvester inside its leash same as any other role, and should cut the
    enemy's own supply belt off it once claimed rather than leave that to a
    harasser that may never come by.
    """
    if not guardianEconomyOn():
        return False
    bot.nearCoreEconomy = True  # _allowOreSite → ore within the leash only
    breakOreDeadlock(bot, ct)  # state-only; picks below re-choose this turn
    if buildHarvester(bot, ct):
        return True
    if maintainHarvester(bot, ct):
        return True
    return denyHarvesterSupply(bot, ct)


_RELAY_ABORT_EXEMPT_TILES = 1


def _relayNearlyComplete(bot) -> bool:
    """True if the in-progress relay has at most one belt tile left to place.

    Guards the turret-in-vision abort below from discarding a relay that's a
    single tile from done: `enemyTurretInVision`/`_enemyUnitInVision` only check
    whether a turret is anywhere in this unit's vision, with no LoS/range/facing
    relevance check at all (unlike `relayToCore`'s own `tileThreatenedByGunner`
    handling, which reroutes around a specifically-threatened tile instead of
    aborting outright — and even that has no sentinel equivalent, so a sentinel
    merely passing through vision, nowhere near actually threatening anything,
    was enough to drop nearly-finished work). Narrow by design: this only
    exempts the last stretch of an already-committed relay, it does not change
    the abort behaviour for an ore claim with no belts placed yet, or a relay
    that still has real work left.
    """
    path = bot.relayPath
    if not bot.inRelay or path is None:
        return False
    remaining = len(path) - (bot.relayBuiltTo + 1)
    return 0 <= remaining <= _RELAY_ABORT_EXEMPT_TILES


def runGuardian(bot, ct: Controller) -> None:
    """Guardian: leash → react → defGun → defSentinel → avoidFire →
    patchAdjacentCoreBelt → defendHome → healAlly → buildHarvesterOnClearedBarrier
    → clearBarriers → reconnectBelts → economy (clearBarriers and economy only
    while nothing hostile is in vision; patchAdjacentCoreBelt/
    buildHarvesterOnClearedBarrier/reconnectBelts/finishNearbyBelt run
    regardless but drop their walk-to-a-distant-break fallback (n/a for
    patchAdjacentCoreBelt and the harvester step, which never walk) while
    hostile, keeping only the zero-movement adjacent action) → patrol.

    defendHome is called twice: once in its normal slot at the default
    stop_hp (_CORE_HEAL_STOP_HP, 400/500 — urgent, stops short of full to
    avoid thrashing on chip damage), and again last in the chain, gated
    `not underAttack`, with stop_hp=GameConstants.CORE_MAX_HP — a pure
    opportunistic top-off once nothing else is productive to do."""
    _resolveCores(bot, ct)
    bot.nearCoreEconomy = False
    if bot.corePos is None:
        return

    # Defence outranks economy: drop the relay claim so the inRelay guards in
    # _reactEnemyTurret / defGun don't lock the guardian out of fighting. Belts
    # already placed stay on the map and the watermark rebuilds on the re-pick.
    # Turret-only until the first relay lands: `_enemyUnitInVision` also counts
    # enemy *builders*, and on a small map one is in vision almost constantly, so
    # this gate keeps `_guardianEconomy` off for the whole opening. With no
    # economy yet there is nothing for the stricter reading to protect.
    #
    # `coreThreatened` is OR'd into both branches — it's the core's own r²=36
    # vision (~6 tiles, vs. a builder's ~4.5), written team-wide via
    # CORE_THREAT_BIT specifically because a guardian working a relay away
    # from the core often can't see what's shelling it. Without this, a
    # guardian mid-relay that never personally spots the attacker never aborts,
    # so `bot.inRelay` stays true and `defendHome`'s inRelay gate skips healing
    # even as the guardian walks right past a dying core.
    if relayEverComplete(ct):
        underAttack = _enemyUnitInVision(ct) or coreThreatened(ct)
    else:
        underAttack = enemyTurretInVision(ct) or coreThreatened(ct)
    if underAttack and (bot.inRelay or bot.oreSite is not None):
        if not _relayNearlyComplete(bot):
            abortRelay(bot)
            bot.oreSite = None

    if not _guardianLeashOk(bot, ct):
        # Recall target must be the *same* dynamic leash the test above used —
        # recalling to a narrower radius than the test tolerates is exactly the
        # over-pull GUARDIAN_ECON_SLACK exists to prevent.
        moveTowardLeash(bot, ct, bot.corePos, radius=guardianLeash(ct))
        return
    # Ahead of blockGunLine: a dangling belt end next to the core is exactly
    # the kind of tile blockGunLine will barrier over if left open, which
    # would wall off a relay mid-line instead of just blocking the gunner.
    # Patch it first so blockGunLine never has a belt gap to barricade.
    if patchAdjacentCoreBelt(bot, ct):
        return
    # Cheapest answer first: a barrier in the gunner's line of fire (~3 Ti) costs
    # far less than fielding a gunner to kill it. Gunners only — see blockGunLine.
    if blockGunLine(bot, ct):
        return
    # Same tier: whichever guardian placed a block owns rebuilding it the
    # instant it's gone, rather than waiting on the reactive re-place that
    # only fires once the gunner has already landed another hit through it.
    if maintainGunLineBlock(bot, ct):
        return
    # skipEconomy=False: a turret we can't act on must not block patrol.
    # homeOnly=True: defGun / defSentinel answer turrets without leaving home.
    if _reactEnemyTurret(bot, ct, skipEconomy=False, homeOnly=True):
        return
    if defGun(bot, ct, homeOnly=True):
        return
    # Above defendHome, which heals the core: healing alone loses to a sentinel
    # parked outside the leash (18 a shot vs 4 HP/turn), so killing it is the
    # defence and the heal is only the holding action underneath.
    if defSentinel(bot, ct):
        return
    # Below the turret answers, above every chore. Placing a counter-gunner is
    # worth eating a shot for — it is the actual response. Healing at 4 HP/turn
    # while taking 10 is not, and neither is clearing a barrier or laying a belt.
    # goal=corePos so the dodge retreats homeward and stays inside the leash.
    if avoidFire(bot, ct, goal=bot.corePos):
        return
    if defendHome(bot, ct):
        return
    if healAlly(bot, ct):
        return
    # Ahead of clearBarriers itself: if a barrier this guardian already killed
    # sat on ore, claim it before wandering off to the next barrier. Zero
    # movement (only acts while still adjacent) and a single build/wait action,
    # so — like reconnectBelts' adjacent-patch case below — it's safe to run
    # even with something hostile in vision; it can't pull the guardian
    # anywhere it isn't already standing.
    if _buildHarvesterOnClearedBarrier(bot, ct):
        return
    # Barrier clearing is a chore, not a response: 30 HP at 2 dmg is 15 turns and
    # 30 Ti to remove a 3 Ti barrier, so it must never run while anything hostile
    # is in vision. Same gate as the economy below it.
    if not underAttack and clearBarriers(bot, ct):
        return
    # Above the economy: a cut belt zeroes a whole line's throughput, while a new
    # harvester is only incremental. Repair beats build. Called unconditionally
    # (not gated on `not underAttack`): the adjacent-patch case inside these is a
    # single build action and no movement, so it's safe even with something
    # hostile in vision — it doesn't pull the guardian out of position, and
    # delaying it lets an enemy actively shooting the belt keep taxing Ti on the
    # same cut turn after turn. Only the "walk to a distant break" fallback
    # inside `_tryPatchDanglingBelts` still backs off while under attack.
    if reconnectBelts(bot, ct, allow_walk=not underAttack):
        return
    # Self-anchored fallback: reconnectBelts only looks near the core, but a
    # guardian working an ore/relay under GUARDIAN_ECON_SLACK (or simply
    # patrolling somewhere a dangling tail happens to sit) can be standing right
    # next to a break reconnectBelts will never see. Cheap, opportunistic.
    if finishNearbyBelt(bot, ct, allow_walk=not underAttack):
        return
    if not underAttack and finishNearbyHarvester(bot, ct):
        return
    if not underAttack and _guardianEconomy(bot, ct):
        return
    # Opportunistic top-off: everything above — including the urgent defendHome
    # call, which stops at _CORE_HEAL_STOP_HP (400/500) to avoid thrashing on
    # chip damage mid-fight — already had first crack at the turn. Last in the
    # chain and gated not underAttack, so it never preempts a real response;
    # it only spends a turn here when there's truly nothing else productive to
    # do, healing the remaining 100 HP instead of leaving the core parked short
    # of full indefinitely.
    if not underAttack and defendHome(bot, ct, stop_hp=GameConstants.CORE_MAX_HP):
        return
    patrolHome(bot, ct)


def runHarasser(bot, ct: Controller) -> None:
    """Harasser: leash → snipe → denyHarvesterSupply → offGun →
    wallCore → denyBelt (only if already mid-cut) → avoidFire → denyOre →
    denyBelt → seek → seekCore.

    Used to start a steal-relay via `finishNearbyHarvester` (belting a nearby
    un-relayed harvester, ally or enemy, all the way back to our own core) and
    commit to finishing it once `bot.inRelay` — mirroring `runDefault`'s own
    bot.inRelay bypass. Removed: a harasser is, by construction, deep in or
    near enemy territory, so that belt line is long, runs straight through
    enemy fire the whole way, and is expensive relative to what it protects —
    worst right at the opening, when a harasser committing to it immediately
    on spawn tied the unit down instead of harassing. Steal-relays from
    `runDefault`/`runGuardian` (near-core economy, short lines) are unaffected.
    """
    _resolveCores(bot, ct)
    bot.nearCoreEconomy = False
    # State-only; the selectors below re-pick this turn. Costs no turn.
    breakBeltDeadlock(bot, ct)
    if not _harassLeashOk(bot, ct):
        # Recall to the region within the leash rather than the literal core
        # tile — moveTowardLeash A*-paths around obstacles; seekCore's own
        # moveToward fallback has no such avoidance.
        from main import HARASS_LEASH_CHEBYSHEV

        moveTowardLeash(bot, ct, bot.enemyCorePos, radius=HARASS_LEASH_CHEBYSHEV)
        return
    if snipeHarvester(bot, ct):
        return
    # snipeHarvester/offGun both now skip a harvester our own steal-relay
    # already claimed (geometry.harvesterClaimedByAlly) — no overlap with this
    # step. Protecting an asset we're already capturing for free outranks a
    # fresh opportunistic gun placement, and it's cheap (a belt cut, not a
    # build), so it runs at the same tier as the other placeGun/attack-adjacent
    # steps already ahead of avoidFire in this chain.
    if denyHarvesterSupply(bot, ct):
        return
    # offGun now covers both gun/sentinel placement on the core and (once
    # unaffordable, or for KILL_CORE_COMMIT_TURNS after a sentinel lands) the
    # core belt-cut, via killCore — see offense.killCore.
    if offGun(bot, ct):
        return
    #if wallCore(bot, ct):
    #    return
    # A denyBelt Mode B cut already committed to gets the same chain priority
    # denyHarvesterSupply/offGun already have, ahead of avoidFire below —
    # otherwise the generic avoidFire step preempts it on every turn spent
    # under any gunner's LoS near the target and the cut never finishes
    # ("pecking" instead of the ~10 shots a belt needs). _cutAndBarrier's own
    # dodge (offense._CUT_DODGE_HP_FRACTION) still lets it bail once actually
    # hurt, so this isn't an unconditional stand-and-tank.
    if _cutInProgress(bot, ct) and denyBelt(bot, ct):
        return
    if avoidFire(bot, ct):
        return
    # Above denyBelt: 3 Ti and no attrition to deny a whole future line beats 20 Ti of
    # attacks to cut one belt. Below the gun steps, which kill production that exists.
    if denyOre(bot, ct):
        return
    if denyBelt(bot, ct):
        return
    if seekHarvester(bot, ct):
        return
    if seekCore(bot, ct):
        return
    # Last resort: no enemy core known, so nothing above has a target to aim at.
    exploreFrontier(bot, ct, outward=True)


def runBuilderRoles(bot, ct: Controller) -> None:
    """Dispatch: guardian → harasser/wave → default."""
    # Ahead of every role: boxed in on all four sides, nothing below can move.
    # Reads bot.motionless, its own counter — not bot.stuck, which breakOreDeadlock
    # etc. read and which gets reset by several downstream steps.
    if breakEncirclement(bot, ct, local_map=bot.hivemind.local_map):
        return
    idx = bot.hivemind.my_index
    # A capped guardian is simply not a guardian: it falls through to the normal
    # non-guardian path below. Which one depends on excess rank — odd slots over
    # the cap dispatch as harasser, even slots as default (guardianOverflowIsHarasser)
    # — so overflow guardians split between the two instead of all becoming one.
    if isGuardian(idx) and not guardianCapped(idx):
        runGuardian(bot, ct)
        return
    capped_guardian_as_harasser = isGuardian(idx) and guardianOverflowIsHarasser(idx)
    # Wave attack pulls the default cohort into harassing too, but not all of
    # it — even-ranked default slots keep building (waveDefaultStaysBuilder) so
    # the crew doesn't stall out with zero builders the moment the gate fires.
    wave_as_harasser = waveAttackActive(ct) and not (
        isDefaultRole(idx) and waveDefaultStaysBuilder(idx)
    )
    if wave_as_harasser or isHarasser(idx) or capped_guardian_as_harasser:
        runHarasser(bot, ct)
        return
    runDefault(bot, ct)