# codex_bot

`codex_bot` is a self-contained improved starter bot. It grows an economy from
nearby ore, extends conveyors back toward the core, keeps an ammunition reserve,
spawns up to eight builders, and adds staggered gunner defence after the opening.

Run it locally after installing `fcode`:

```bash
fcode run codex_bot starter
python3 scripts/bot_gauntlet.py --bot codex_bot
```

The bot uses only `fcode` plus the Python standard library, so `main.py` can be
uploaded directly (or archived together with this file, though it is not needed
at runtime).
