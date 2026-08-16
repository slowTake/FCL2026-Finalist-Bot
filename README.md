# teaPot — Florent Code League

Teamlink -> [Florent Code League](https://game.code.florent.vc/team/12fdece5-48c9-4913-9533-6d95b73e22ab).

Tutorials -> https://game.code.florent.vc/tutorials/

## Setup

```bash
git clone git@github.com:slowTake/teaPot.git
cd teaPot

python3 -m venv .venv
source .venv/bin/activate
pip install fcode

fcode login
fcode starter   # if you don't already have bots/scaffolded
```

On Windows (PowerShell): `.venv\Scripts\Activate.ps1`

## Daily use

```bash
source .venv/bin/activate
fcode run starter starter
fcode watch replay.replay26   # replay name may differ
```

## Scripts

Local test runners live in `scripts/`. Install CI deps first:

```bash
source .venv/bin/activate
pip install -r requirements-ci.txt
```

Both gauntlets pit your bot (team A) against `starter` on every map in `maps/`. Each match prints:

| Column | Meaning |
| --- | --- |
| Map | Map name (`extreme_gauntlet` appends `-1` … `-5` per run) |
| Result | `WIN` or `LOSS` for your bot |
| Condition | How the game ended (e.g. `Core destroyed`, `Titanium collected`) |
| Ally Ti | Titanium your bot delivered to its core |
| Enemy Ti | Titanium `starter` delivered to its core |
| Turn | Final turn count |

Optional: limit to specific maps with `--maps sprint strait`.

### `bot_gauntlet.py`

Runs each map once. Passes only on **15/15 wins** (same bar as CI).

```bash
python scripts/bot_gauntlet.py --bot <your-bot>
python scripts/bot_gauntlet.py --bot turtle --maps sprint strait
```

### `extreme_gauntlet.py`

Runs each map **5 times** with different seeds (`base`, `base+1`, …). Reports total wins and win rate at the bottom. Passes only if every match is won.

```bash
python scripts/extreme_gauntlet.py --bot <your-bot>
python scripts/extreme_gauntlet.py --bot turtle --maps sprint
```

Example footer:

```
Total: 12/15
Win rate: 80.0%
```

### `zip_bot.py`

Packages a bot for league submission. Starting from `main.py`, it follows local imports and zips only the modules your bot actually loads — docs, replays, `__pycache__`, and unreachable modules are left out. Files land at the zip root (`main.py` must be the entry point), and re-running replaces any existing zip for that bot.

```bash
python scripts/zip_bot.py baron
python scripts/zip_bot.py baron -n              # dry run: list files, don't write
python scripts/zip_bot.py baron -o dist/         # write elsewhere (default: repo root)
python scripts/zip_bot.py baron --all            # every .py in the bot dir, not just imported
python scripts/zip_bot.py baron --include data.json   # add an extra file (repeatable)
```

Prints the file list and total size before writing, and (unless `--all`) which `.py` files in the bot dir were skipped as unimported — useful for catching a module you meant to wire in but forgot to import.

### `fcv_bot_gauntlet.py`

Diagnostic engine for replay analysis — behavioral detectors (crashes, orphan harvesters, friendly fire, oscillation, ...), causal event chains, cost-weighted "top Ti leaks," and JSON/CSV/Markdown reports. Lives in [`fcv_gauntlet/`](fcv_gauntlet/).

**Needs the `fcv` package**, which isn't on PyPI — install it separately, once per venv:

```bash
git clone https://github.com/DataAthleteChamp/florent-code-visualiser.git
pip install -e florent-code-visualiser/
```

Without it, `fcv_bot_gauntlet.py` fails immediately with `ModuleNotFoundError: No module named 'fcv'`.

```bash
# Analyze a single existing replay
python scripts/fcv_bot_gauntlet.py --replay replay.replay26 --markdown-report /tmp/report.md

# Full gauntlet with JSON/CSV output
python scripts/fcv_bot_gauntlet.py <your-bot> starter --all-maps --json-output report.json --csv-metrics metrics.csv
```

Optional AI root-cause diagnosis (`--oracle`) uses the Claude API and **costs real money from a personal API budget** — it never spends without an explicit `--oracle-yes` on top of `--oracle`. See [`fcv_gauntlet/oracle/README.md`](fcv_gauntlet/oracle/README.md) for setup and budget caps, and [`fcv_gauntlet/oracle/AGENT.md`](fcv_gauntlet/oracle/AGENT.md) for the confirmation rule any agent must follow before using it.

## CI bot test

When your bot is ready, commit it under `bots/<your-bot>/main.py` and open a PR.

CI runs `bot_gauntlet.py` automatically on changed bots. The check passes only on **15/15 wins**.

Run the same test locally before pushing — see [`bot_gauntlet.py`](#bot_gauntletpy) above.

## Notes

- Do not commit `.venv/` — each person creates their own.
- Strategy notes: [`STRATEGY.md`](STRATEGY.md)
- Shared bot helpers: [`florent_lib.py`](florent_lib.py) (copy into `main.py` before league upload if needed)
