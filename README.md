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

## CI bot test

When your bot is ready, commit it under `bots/<your-bot>/main.py` and open a PR.

CI will automatically run your bot against `starter` on all 15 maps. The check passes only on **15/15 wins**.

Run the same test locally before pushing:

```bash
source .venv/bin/activate
pip install -r requirements-ci.txt
python scripts/bot_gauntlet.py --bot <your-bot>
```

## Notes

- Do not commit `.venv/` — each person creates their own.
- 
