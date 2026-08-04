# Baron domain glossary

Names for concepts in this bot. For **where code lives**, see `AGENT.md` Key. For **behavior phases**, see task lists at the top of `builder.py`.

## Builder role

One of two duty loops for `BUILDER_BOT` units: **default** or **raider**. Dispatched from `builder.run_builder_roles`.

## Default

Economy, relay, and home duties (`logistics.run_default`; may call `defence.py` helpers). All builders use this path until assignment is implemented.

## Raider

Offensive execution (`offense.run_raider`): siege, belt raid, push core. Uses `guns` for placement.

## Task list

Ordered phases inside a role; the first matching phase wins each turn.

## Inbound relay

Committed conveyor chain from a harvester tile back to the ally core. Owned by `logistics.py` when implemented.

## Guns layer

Geometry and mechanics of turrets: valid placements, fallbacks, `place_*`, and operating living turrets (fire/rotate). No raid strategy.

## Offense module

Implementation home for the **raider** role (`offense.py`). Calls `guns` to place or fire.

## Defence module

Home-defence helpers for the **default** role (`defence.py`); not a separate builder role name.

## HiveMind slots 7–15

Team communication owned by HiveMind (`components/hivemind.py`). Player code must not write these slots.
