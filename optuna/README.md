# optuna/ — hyperparameter tuning

`tune_bot.py` runs [Optuna](https://optuna.org/) trials against any bot
under `bots/` — not just `count`, though that's still the default. It's
fully isolated from the real bot: the target bot's directory is never
edited and never contains any tuning-related code. Every run, the script
copies it into two throwaway directories under `optuna/` —

- `<bot>_trial/` — gets a `tuning.py` written into it and a small
  `TUNE_PARAMS` hook appended to its `main.py`. Rebuilt from scratch on
  *every* run, so a study always tunes whatever the bot currently is, never
  a stale copy.
- `<bot>_baseline/` — a plain, untouched copy: the fixed opponent every
  trial plays against. Frozen deliberately (see below), not rebuilt
  automatically.

Both are gitignored — they're regenerated on demand, never committed. The
real bot's `main.py` itself has no `import tuning` line and no idea any of
this exists; `git status` after a tuning run should show nothing under
`bots/`. `legacy/` is the older baron-era tooling (regex-edits a copied
`policy.py` per trial); kept for reference, not run day to day.

## One-time setup

You need two things beyond the repo's base venv: `optuna` itself, and `fcv`
(the match-runner these scripts drive — it's what actually plays the games and
reports the results).

```bash
source .venv/bin/activate            # this repo's venv, from the repo root
pip install -r optuna/requirements.txt
```

`fcv` isn't on PyPI. If `florent-code-visualiser/` doesn't already exist at the
repo root:

```bash
git clone https://github.com/DataAthleteChamp/florent-code-visualiser.git
```

Either way, install it into *this* venv (it's a separate package from the repo
itself, and a fresh venv won't have it even if the clone is already sitting
there from someone else's setup):

```bash
pip install -e florent-code-visualiser/
```

Check both landed:

```bash
python -c "import optuna, fcv; print('optuna', optuna.__version__)"
```

If that fails on `fcv`, you're most likely running the system `python3`
instead of the venv one — `tune_bot.py` re-execs itself into
`.venv/bin/python3` automatically if it finds `optuna` missing, but it can't
do that for `fcv` since `fcv` is what it's trying to invoke as a subprocess,
not import directly. Just make sure your shell has the venv activated first.

## Quickstart

```bash
# Once per bot: freeze today's source as the fixed opponent every trial
# plays against. Re-run this (with --force) only when you deliberately want
# to lock in a new baseline — see "promoting a result" below.
python3 optuna/tune_bot.py --freeze-baseline                # bot defaults to count
python3 optuna/tune_bot.py --bot baron --freeze-baseline

# A real run (~15 maps x 2 sides x n-trials games — budget time accordingly)
python3 optuna/tune_bot.py --n-trials 30 --jobs 4

# A fast loop while you're iterating on the parameter list itself
python3 optuna/tune_bot.py --n-trials 5 --maps sprint duel strait --jobs 4

# A different bot — param-space defaults to optuna/param_space_<bot>.json
python3 optuna/tune_bot.py --bot baron --n-trials 30 --jobs 4

# Check progress any time, including while a run is still going in another
# terminal — reads the shared SQLite study, doesn't touch a live run
python3 optuna/tune_bot.py --show-best
```

All paths are resolved from the script's own location, not the shell's
working directory — run it from the repo root or from inside `optuna/`,
either works. Each bot gets its own study (`<bot>_tuning_v1` in
`optuna/<bot>_optuna.db` by default), so tuning `count` and `baron` never
collide.

## Discovering parameters

Don't hand-write a param-space file from scratch — `discover_params.py`
statically scans a bot directory for every module-level `ALL_CAPS` constant
(the convention every bot here already follows), no importing or running
the bot required, so it's safe even against a currently-broken one:

```bash
python3 optuna/discover_params.py bots/count
python3 optuna/discover_params.py bots/baron
```

Nothing is filtered out — a store slot index and `GUARDIAN_LEASH` both show
up — but entries are ranked, most-likely-genuine-tuning-knob first and
most-likely-structural-noise last, via a small transparent point score (see
`_score()`'s weights at the top of the file). The JSON output
(`optuna/discovered_<bot>.json`, gitignored — regenerate on demand) keeps
every entry's score and the specific reasons that produced it, so picking a
new one becomes "skim the top of a list" rather than "grep the codebase by
hand." This is the full *candidate* list, though, not something `tune_bot.py`
reads directly — the next section is the curation step that turns entries
from it into an actual param-space file.

## Adding a parameter to tune

The search space is a plain JSON file, `optuna/param_space_<bot>.json` (that's
where `tune_bot.py` looks by default — see `optuna/param_space_count.json`
and `optuna/param_space_baron.json` for real examples):

```json
{
  "guns.TI_SURPLUS_THRESHOLD": ["int", 60, 400, 20],
  "ecoBot.ORE_SPREAD": ["int", 2, 8, 1]
}
```

Each key is `"module.CONSTANT_NAME"` — `module` has to be a name the bot's
own `main.py` can actually import bare (its own directory, plus any
subdirectories it splits into — count's `hivemind/`/`roles/`, for instance —
are all flat-importable; see the sys.path setup at the top of `main.py`). So
it's `"guardianBot.GUARDIAN_LEASH"`, not `"roles.guardianBot.GUARDIAN_LEASH"`.
If you get the module or constant name wrong, the bot fails to *load* with a
clear `AttributeError` naming exactly what was wrong — it won't silently run
with the old value. Each value is `[kind, low, high, step]` with `kind` one of
`"int"`/`"float"` — the bounds are the one thing `discover_params.py` can't
pick for you; it can tell you `GUARDIAN_LEASH` is currently `6`, not that
`4-12` is a sane range to search.

Nothing else to touch, and this works for a brand-new file too, not just
today's modules. The injected `tuning.py` imports the target module itself
(`importlib.import_module`) rather than assuming it's already loaded, so
adding `guns.py`'s tenth constant and adding `some_new_role.py`'s first
constant are the same one-line JSON edit; you don't need that new file to
already be imported anywhere else in the bot first.

To point a run at a param-space file that isn't the default location, pass
`--param-space some/other/file.json`.

## Promoting a result

`--show-best` prints the current best trial's parameters. To actually keep
them: hand-edit those values into the bot's real source (they're meant to sit
next to their use, with the comment explaining why — don't centralize them
into a config file just to make this easier, that's the exact pattern
`bots/count/AGENTS.md` already warns against), then:

```bash
python3 optuna/tune_bot.py --freeze-baseline --force
```

to lock in a fresh baseline for the next round of tuning. Skipping this step
means the next study keeps comparing against the *old*, now-beaten baseline.

## What's actually being optimized

Not raw win rate — see `score_batch()`'s docstring in `tune_bot.py`. Win
rate over a handful of maps is a step function and gives Optuna very little to
climb; the objective is a smooth proxy (win/loss, softened by a bounded nudge
from the titanium-delivered margin — the game's own primary tiebreak) that
can't ever rank a real loss above a real win, but gives the sampler a
gradient between two trials that both won or both lost. Real win rate is still
logged per trial (`trial.user_attrs["win_rate"]`) for a human-readable sanity
check.

The map set is fixed for an entire study, not re-sampled per trial (default:
the target bot's live map pool, via the same resolver `scripts/head_to_head.py`
uses) — see `docs/mistakes.md` in `bots/count` on why comparing runs with
different map samples underneath them is close to meaningless.

## `--milestone-weighted` — an objective that can't be fooled by trading wins

The default objective (trial vs a frozen copy of itself) is real, gradient-
friendly signal, but a trial can climb it by trading wins — lose ground
against an older milestone, gain the same amount against the current one —
since a frozen self-copy has no opinion about *which* opponent you're
actually stronger against. This happened for real: a trial scored well
locally but `scripts/ladder/milestone_gauntlet.py --compare-tuned` showed it had
just swapped which milestones it beat, net zero overall.

`--milestone-weighted` replaces the objective with one that plays every
milestone in `scripts/ladder/milestone_gauntlet.py`'s `MILESTONE_VERSIONS` instead
of one baseline, weighted by `MILESTONE_WEIGHTS` (recency/strength — a win
against the current frontier counts far more than a win against an ancient
bot) with an asymmetric penalty for losses (losing to an old, previously-
solved milestone costs more than losing to the current one, which is
expected to be the hardest matchup):

```bash
python3 optuna/tune_bot.py --milestone-weighted --n-trials 30
python3 optuna/tune_bot.py --milestone-weighted --milestone-versions 104 118 --loss-penalty-base 2.0
```

Costs `#milestones` times as many games per trial — budget `--n-trials`/
`--maps` down accordingly, especially while iterating on the weights
themselves. Uses a separate study (`<bot>_tuning_weighted_v1`, not
`<bot>_tuning_v1`) automatically: its objective values are an unbounded
weighted sum, not the baseline objective's roughly-bounded smooth score, and
mixing the two into one study would make "best trial" meaningless. Doesn't
need `--freeze-baseline` first — there's no baseline copy in this mode, only
milestone opponents.

## Tournament — sanity check against real opponents

The optimization loop above only ever measures trial vs. a frozen copy of
*itself*. Beating your own clone climbs a gradient, but doesn't prove
anything about a genuinely different playstyle. `--tournament` plays the
target bot (or a candidate's params, see below) against real opponents —
its own frozen baseline, baron, and bandit by default — on maps sampled
independently per opponent from its own live competitive pool:

```bash
# Sanity-check bots/count exactly as it is today
python3 optuna/tune_bot.py --tournament

# Sanity-check the study's current best trial before promoting it
python3 optuna/tune_bot.py --tournament --tournament-best

# Sanity-check a specific saved params.json (e.g. an earlier trial you kept)
python3 optuna/tune_bot.py --tournament --tournament-params my_candidate.json

# A different bot, opponent roster, more maps per opponent, a fixed map instead
python3 optuna/tune_bot.py --bot baron --tournament --tournament-opponents bots/earl bots/sprint
python3 optuna/tune_bot.py --tournament --sample-size 10
python3 optuna/tune_bot.py --tournament --maps drumlin
```

Prints a per-opponent win/loss breakdown plus an overall total. Like
`--n-trials`, this rebuilds `<bot>_trial` fresh from the current source first
— never a stale copy — and never touches the real bot itself. The default
opponent roster is `<bot>_baseline` plus `bots/baron`/`bots/bandit` — when
`--bot` is already `baron` or `bandit`, override `--tournament-opponents`
since facing yourself twice isn't useful.

## `legacy/`

`baron_tuner.py` and `baron_tournament.py` tuned `bots/baron/policy.py` by
copying the bot per trial and regex-substituting constant assignments in the
copy. `tune_bot.py`'s env-var injection approach replaced this for every bot,
`baron` included — kept here for reference, not maintained day to day.
