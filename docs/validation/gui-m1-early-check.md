# GUI M1 early check and verification record

**An in-process early check, not acceptance. It ran no network, vcan or transport.
P1–P9 are judged at M4** ([decisions/0010 §9.2](../decisions/0010-gui-observer-api.md)).

`scripts/gui_m1_early_check.py` calls the dispatcher and the publisher directly, in one
Python process. It opens no socket, no vcan interface and no ISO-TP transport, and runs
no event loop. Its figures are dispatcher and publisher CPU time on this host, and they
say nothing about wire latency. It is **not** the M2 early check (vcan, in a namespace)
and **not** the M4 benchmark, and a pass here is not evidence for either.

**M1 has no CI result.** `ci.yml` runs on pushes to `master` and `modernization` and on
`pull_request` only. A push to `gui` triggers nothing, and nothing on `gui` has been
pushed for M1. Every result below is local.

## Host

| | |
|---|---|
| Date | 2026-09-26 (runs stamped 2026-09-27 UTC) |
| Host | Intel Core i7-8700 @ 3.20 GHz, 12 CPUs, governor `powersave`, no deliberate other load |
| OS, kernel | Ubuntu 22.04.5 LTS, `6.8.0-138-generic` |
| Python | 3.12.12 (uv), worktree `.venv` with `-e .[dev,hardware]` |
| Commit | `gui` at `8005bf2`, with `scripts/gui_m1_early_check.py` as committed alongside this record |

## The three runs, verbatim

Command: `for i in 1 2 3; do .venv/bin/python scripts/gui_m1_early_check.py; done`.
stderr was empty; stdout follows.

```text
=== run 1 (2026-09-27T06:53:10Z)
bare median 14.93 us  p99 34.78 us
wrapped median 21.55 us  p99 41.42 us
overhead median 6.62 us  p99 6.64 us
full HandOff: 65 turns, longest turn 1.008 ms
within the M1 early-check limits
exit=0
=== run 2 (2026-09-27T06:53:12Z)
bare median 14.70 us  p99 31.40 us
wrapped median 21.69 us  p99 38.48 us
overhead median 6.99 us  p99 7.08 us
full HandOff: 64 turns, longest turn 0.880 ms
within the M1 early-check limits
exit=0
=== run 3 (2026-09-27T06:53:13Z)
bare median 15.05 us  p99 32.62 us
wrapped median 21.94 us  p99 40.20 us
overhead median 6.89 us  p99 7.58 us
full HandOff: 65 turns, longest turn 1.016 ms
within the M1 early-check limits
exit=0
```

Stop rule: median overhead > 10 µs, p99 overhead > 50 µs, or any turn > 2 ms. **No run
hit it.** The margin on the median is small: 6.6–7.0 µs against 10 µs.

### What the ~7 µs overhead is made of (supplementary, not part of the check)

The script's method alternates each timed call with publisher work, so it does not
measure the wrapper alone. A diagnostic run twice, with the same request mix and N,
separates the parts. The diagnostic script was not committed.

| Variant | median, run A | median, run B |
|---|---|---|
| bare dispatcher | 14.94 µs | 15.23 µs |
| wrapped, **no** publisher work between calls | 16.64 µs | 17.04 µs |
| wrapped, publisher draining between calls (the script's method) | 22.02 µs | 22.28 µs |
| **bare**, with the same publisher work between calls | 19.83 µs | 20.80 µs |

So the wrapper's own cost is about **1.7–1.8 µs** median. About **5 µs** of the script's
figure appears even around the bare dispatcher once publisher work runs between calls.
The most likely cause is a cache and warm-state effect of that interleaving, but this
record does not prove the cause. The method is 0010's as written, and it is left
unchanged. M2 measures the same thing on vcan, where a request's I/O runs between
publisher turns anyway.

### Turn length

The longest turn was 0.880–1.016 ms, and a full 4096-record `HandOff` drained in 64–65
turns. One record costs about 15 µs to classify, summarise and encode here, so the 1 ms
budget, not the 64-record limit, ended some turns. The 2 ms stop line was not approached.

## Gates

| Gate | Command | Result |
|---|---|---|
| Full suite, host with `vcan0` | `.venv/bin/python -m pytest -p no:cacheprovider` | **1076 passed, 2 xfailed**, 0 XPASS, 0 skipped. The baseline at `5026296`, before M1 code, was 1026 passed, 2 xfailed: M1 adds 50 tests |
| vcan integration | `scripts/run_integration_tests.sh` | **68 passed**, unchanged |
| CI shape | fresh clone of `gui` at `8005bf2`, venv with only `.[dev]` (aiohttp not installed), `unshare -r -n` with `ip link set lo up` | **987 passed, 55 skipped, 2 xfailed**. That is the previous 937 + 50. All 50 `observe` tests ran and passed; none is skipped |
| Lint | `.venv/bin/ruff check .` | clean |
| Types | `.venv/bin/mypy` (bare) | clean, 54 source files |
| No existing file changed | `git diff --stat a57b98f..HEAD -- app.py cli.py transport protocols ecu vehicle dtc scenario profiles pyproject.toml .github` | empty |

The 55 skips in the CI-shape run are the same as before M1. None of them is new, and
none is an `observe` test:

| Count | File | Reason |
|---|---|---|
| 22 | `tests/integration/test_ecu_dispatch.py` | `vcan0` does not exist in the namespace |
| 19 | `tests/integration/test_obd_isotp.py` | `vcan0` does not exist |
| 8 | `tests/integration/test_scenario_isotp.py` | `vcan0` does not exist |
| 4 | `tests/integration/test_lifecycle.py` | `vcan0` does not exist |
| 1 | `tests/unit/test_elm327_serial.py` | needs the `[hardware]` extra |
| 1 | `tests/integration/test_elm327_simulated.py` | needs the `[hardware]` extra |

## 0010 §9.3 rows

| Row | Status after M1 |
|---|---|
| `observe` unit tests | Written. They need nothing, so they run in ordinary CI once `gui` is merged or pushed to a branch CI runs on. **Not yet run by CI** |
| Ordering O1–O3 and the turn bound O4 | Written, same status. O4 was strengthened in the plan amendment: ready I/O and a `call_soon` marker must run between every pair of turns |
| Ledger tests (§5.1) | Written, same status |
| Two-socket observation (§4.4) | Written, same status. It **observes** the DEV-25 shape and does not characterise DEV-25, which remains separate work |
| API-off proofs (§9.1) | The differential comparison is written (46,125 requests). The two `run()` wiring proofs are **deferred to M2**, because M1 builds no `run()` wiring. The owner accepted this on 2026-09-26 |
| `api` tests, vcan integration, performance | **Not applicable** until M2 and M4 |
