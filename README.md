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

## Notes

- Do not commit `.venv/` — each person creates their own.
- Do not commit login tokens or secrets.
- Once deps stabilize, share them with `pip freeze > requirements.txt` and install via `pip install -r requirements.txt`.
