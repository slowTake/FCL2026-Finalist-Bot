# Turtle

Portable Florent Code League bot based on the official starter, plus last-mile **splitters** and gunners on splitter side-outputs so turrets get conveyor-fed ammo (fcode 2.2).

This folder is gitignored — copy or move it out of the repo anytime.

> Note: some online tutorials mention `convert_ammo` / a global ammo pool. That API is **not** in fcode 2.2.0 — ammo is titanium stacks delivered into turrets via conveyors/splitters.

## Run (from repo root)

```bash
source .venv/bin/activate
fcode run local_bots/turtle local_bots/turtle
fcode watch replay.replay26
```

Mirror match vs the shared starter:

```bash
fcode run local_bots/turtle starter
```

## Lift out

```bash
cp -r local_bots/turtle ~/somewhere/my-bot
# then point fcode at that folder, or drop it into another project's bots/
```
