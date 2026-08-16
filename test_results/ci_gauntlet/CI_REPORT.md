# 🤖 CI/CD Bot Regression & Benchmark Report

**Bot Under Test**: `bots/sprint` vs `bots/baron`
**Total Maps Tested**: 5

| Map | Winner | End Turn | Bot A First Belt | Bot B First Belt | Bot A Crashes | Bot B Crashes | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `antler` | `bots/sprint` | 193 | Turn 8 | Turn 10 | 23 | 42 | ❌ CRASH FAIL |
| `archipelago` | `bots/baron` | 496 | Turn 17 | Turn 14 | 418 | 1495 | ❌ CRASH FAIL |
| `atoll` | `DRAW` | 1000 | Turn 6 | Turn 9 | 122 | 497 | ❌ CRASH FAIL |
| `aurora` | `bots/sprint` | 395 | Turn 8 | Turn 10 | 1179 | 618 | ❌ CRASH FAIL |
| `bridge` | `DRAW` | 1000 | Turn 13 | Turn 16 | 0 | 2 | ⚠️ TIMEOUT/LOSS |

## Summary Verdict
- **Overall Win Rate**: `40.0%` (2/5)
- **Total Unit Crashes**: `1742`
- **CI/CD Build Verdict**: **`FAIL`**