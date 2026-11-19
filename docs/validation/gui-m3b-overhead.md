# GUI M3b checkpoint 1: overhead measurement (design §17, task 1.5)

**In-process only, same host and same `.venv` Python, before and after checkpoint 1's
code.** This is **not** the M2 early check, **not** the M4 benchmark, and **not** an
acceptance run. The M2 latency `STOP` ([0010 §9.2](../decisions/0010-gui-observer-api.md))
**stays open and is not affected** by anything here. The optional rotated M2 early check
(design §17, 1.5, "Optional") **was not run**.

Checkpoint 1 added non-finite sanitising to `snapshots.vehicle`, made
`snapshots.state_message`'s JSON encode strict (`allow_nan=False`), contained the state
task inside `Publisher.run_state`, and made `GET /vehicle` strict. This record answers one
question: what did that cost, reported in two separate kinds, never combined:

1. **State build and encode** -- CPU time inside `snapshots.vehicle` / `snapshots.dtcs` /
   `snapshots.state_message`, called directly, with no event loop, no socket and no
   publisher turn around it. This is not what the publisher does per turn; it is the
   function cost those calls have when something calls them.
2. **The publisher's longest turn** -- `scripts/gui_m1_early_check.py`'s full-`HandOff`
   drain, which shows whether anything in the publisher's own turn handling moved.

## Host

| | |
|---|---|
| Date | 2026-09-30 |
| Host | Intel Core i7-8700 @ 3.20 GHz (4.6 GHz max), 12 threads, no deliberate other load |
| OS, kernel | `6.8.0-138-generic` |
| Python | 3.12.12, worktree `.venv` |
| Commits | before = `c63a9c4` (exported with `git archive` into the scratchpad, put first on `PYTHONPATH`); after = `17d6e3b` (this checkpoint-1 head, tasks 26-28 committed, the ordinary editable install) |
| Namespace | every run inside `unshare -r -n bash -c 'ip link set lo up && ...'` |
| Order | three runs each, alternating B, A, B, A, B, A, to spread host noise |

**Provenance (task 30):** the measurements above were taken at `17d6e3b` (after) against
`c63a9c4` (before). The commits after `17d6e3b` through `1f5a90e` changed, in code, only
`snapshots.status` (a non-finite `t_last_applied` is now sent as `null`), one comment in
`api/server.py`'s `_vehicle`, and added the measurement script (`scripts/gui_m3b_state_cost.py`)
and tests. None of these is on a measured path (`snapshots.vehicle`, `snapshots.dtcs`,
`snapshots.state_message`; `Publisher.drain_turn`, `Publisher._publish`, HandOff,
ObservedDispatcher), confirmed with `git diff 17d6e3b 1f5a90e -- src scripts tests`. So the
measurements were not rerun, and no number below changed.

## Commands

State build/encode, before (`BEFORE` = the `c63a9c4` export's `src`):

```
unshare -r -n bash -c "ip link set lo up && PYTHONPATH=$BEFORE .venv/bin/python scripts/gui_m3b_state_cost.py"
```

State build/encode, after:

```
unshare -r -n bash -c "ip link set lo up && .venv/bin/python scripts/gui_m3b_state_cost.py"
```

Publisher turn, before and after (same `PYTHONPATH` pattern):

```
unshare -r -n bash -c "ip link set lo up && [PYTHONPATH=$BEFORE] .venv/bin/python scripts/gui_m1_early_check.py"
```

`scripts/gui_m3b_state_cost.py` prints `ecu_simulator.__file__` as proof of which source
tree it ran against; both before and after runs confirmed the expected path (see the raw
output). `scripts/gui_m1_early_check.py` prints no such path, so only the state-cost runs
prove which tree ran; this is not a gap for that script, because the code on its path
(`Publisher.drain_turn`, `Publisher._publish`, `HandOff`, `ObservedDispatcher`) is
byte-identical between `c63a9c4` and `17d6e3b` (§2 below), so which tree it imported does
not affect what it measures. `gui_m3b_state_cost.py` builds the runtime from the shipped
`ice_scenario.yaml` profile on interface
`vcan0` (`app.RuntimeConfig.build` / `app.build_runtime`, no socket opened -- as
`gui_m1_early_check.py` builds), computes `unavailable = availability.unavailable(runtime.config.profile)`,
applies the scenario once (`runtime.runner.apply(1.0)`) so `as_of` is set, warms up 200
calls, then times 10,000 calls with `perf_counter_ns`. It uses `vehicle(runtime,
unavailable)`, `dtcs(runtime)` and `state_message(runtime, unavailable)`, whose signatures
are unchanged across checkpoint 1, and it does not use `check_state_size`, whose signature
changed. Raw output for every run is under `docs/validation/gui-m3b-overhead-runs/`.

## 1. State build and encode (not a publisher turn)

10,000 calls per run, `snapshots.state_message` end to end, plus `vehicle`+`dtcs` build
alone and JSON encode alone (both `allow_nan` values, so the flag's own cost is visible
separately from the sanitising added in `vehicle`). All figures in microseconds (µs).

### `state_message` (end-to-end: build + encode), the authoritative before/after number

| Run | Before median | Before p99 | Before max | After median | After p99 | After max |
|---|---|---|---|---|---|---|
| 1 | 15.570 | 35.440 | 67.674 | 17.247 | 34.069 | 149.054 |
| 2 | 15.692 | 35.577 | 69.484 | 17.596 | 38.455 | 49.835 |
| 3 | 15.663 | 32.271 | 59.207 | 17.378 | 37.801 | 159.827 |
| **median of runs** | **15.663** | **35.440** | **67.674** | **17.378** | **37.801** | **149.054** |

**Diff (after − before): median +1.715 µs, p99 +2.361 µs, max +81.380 µs.** The max is a
single sample per run and is noisy (two of the three after-runs show a >130 µs outlier):
single-sample tail, cause not measured. The median is the figure to rely on. The p99
difference is given as measured, but the per-run p99 ranges overlap (before 32.27-35.58 µs,
after 34.07-38.46 µs), so it is within run-to-run variation.

### Build alone (`vehicle` + `dtcs`)

| Run | Before median | Before p99 | Before max | After median | After p99 | After max |
|---|---|---|---|---|---|---|
| 1 | 6.141 | 13.805 | 20.771 | 7.756 | 18.429 | 52.000 |
| 2 | 6.213 | 13.999 | 136.214 | 7.757 | 18.283 | 26.238 |
| 3 | 6.099 | 13.875 | 22.315 | 7.679 | 18.967 | 50.703 |
| **median of runs** | **6.141** | **13.875** | **22.315** | **7.756** | **18.429** | **50.703** |

Diff: median +1.615 µs, p99 +4.554 µs. This is the checkpoint-1 cost that is actually new
work: `vehicle()` now iterates every signal, checks `math.isfinite` on each float, and
builds a new `signals` dict and a sorted `nonfinite` list, where before it copied the
stored dict directly. The end-to-end diff above (+1.715 µs median) is consistent with
this build diff; encode alone (next) is not where the cost went.

### Encode alone

Built once per run (the payload from that run's own `vehicle`/`dtcs` build), then
`json.dumps(payload, separators=(",", ":"), allow_nan=...)` timed 10,000 times per flag
value. The after code always calls `state_message`'s encode with `allow_nan=False`
(design §8.3, §14.1 C11); the before code calls it with the default `allow_nan=True`. Both
flag values are measured here on both sides so the flag's own cost is visible; the
end-to-end `state_message` number above already reflects each side's own actual flag.

`allow_nan=False`:

| Run | Before median | Before p99 | After median | After p99 |
|---|---|---|---|---|
| 1 | 8.369 | 16.833 | 8.347 | 15.950 |
| 2 | 8.415 | 16.818 | 8.483 | 21.059 |
| 3 | 8.419 | 16.312 | 8.398 | 16.574 |
| **median of runs** | **8.415** | **16.818** | **8.398** | **16.574** |

`allow_nan=True`:

| Run | Before median | Before p99 | After median | After p99 |
|---|---|---|---|---|
| 1 | 8.400 | 16.280 | 8.342 | 15.906 |
| 2 | 8.404 | 16.530 | 8.475 | 18.164 |
| 3 | 8.424 | 20.503 | 8.413 | 17.458 |
| **median of runs** | **8.404** | **16.530** | **8.413** | **17.458** |

Diff, `allow_nan=False`: median −0.017 µs, p99 −0.244 µs. Diff, `allow_nan=True`: median
+0.009 µs, p99 +0.928 µs. Both are within run-to-run noise (compare the ±0.1 µs spread
across a side's own three runs): **encode itself did not get measurably more expensive**,
with either flag value -- noting that the two sides' payloads are not byte-identical (the
after payload's `vehicle` object carries an extra `"nonfinite":[]` key that the before
payload does not have), so this is the cost of each side's own actual payload, not of a
held-constant one -- and the `allow_nan` flag itself costs nothing measurable on a payload
with no non-finite values to reject. The checkpoint-1 overhead is in the build, not the
encode.

## 2. Publisher turn (M1 early check)

`scripts/gui_m1_early_check.py`, unchanged, run the same way (§ commands above). It
dispatches 20,000 requests through a bare `Dispatcher` and the same mix through
`ObservedDispatcher` with the `Publisher` draining between calls, then fills a full
4096-record `HandOff` and drains it, reporting the longest single turn. **The state task
is not part of this path** -- `run_state` runs on its own interval on a separate task, and
nothing in this measurement calls it. This shows whether checkpoint 1 moved anything in
the publisher's own dispatch/drain handling; it does not measure the state task's cost,
which section 1 already covers separately.

| Run | Before overhead median | Before overhead p99 | Before longest turn | Before turns | After overhead median | After overhead p99 | After longest turn | After turns |
|---|---|---|---|---|---|---|---|---|
| 1 | 6.68 µs | 9.25 µs | 1.004 ms | 65 | 7.32 µs | 11.80 µs | 1.022 ms | 66 |
| 2 | 6.89 µs | 11.53 µs | 0.905 ms | 64 | 7.17 µs | 12.29 µs | 0.943 ms | 64 |
| 3 | 7.20 µs | 0.11 µs | 0.927 ms | 64 | 7.16 µs | 9.61 µs | 1.020 ms | 67 |
| **median of runs** | **6.89 µs** | **9.25 µs** | **0.927 ms** | | **7.17 µs** | **11.80 µs** | **1.020 ms** | |

Diff: overhead median +0.28 µs, overhead p99 +2.55 µs, longest turn +0.093 ms (93 µs).
Every one of the six runs printed `within the M1 early-check limits` and exited 0; the
stop rule (median overhead > 10 µs, p99 overhead > 50 µs, or any turn > 2 ms) did not trip
before or after. The before run 3 overhead p99 of 0.11 µs is the same diff-of-two-p99s
noise `docs/validation/gui-m1-early-check.md` already documents for this script's method
(a difference of two independently-computed p99s, not a p99 of differences); it is
reported as measured, not smoothed over.

The run medians were 0.927 ms before and 1.020 ms after, still well under the 2 ms stop
line. The per-run ranges overlap (before 0.905/0.927/1.004 ms, after 0.943/1.020/1.022
ms), and `git diff c63a9c4 17d6e3b` shows no change to any code on this script's path:
`Publisher.drain_turn` and `Publisher._publish` are untouched, and `handoff.py`,
`wrapper.py`, `events.py` and `connection.py` are byte-identical between the two commits
(`publisher.py`'s changes are to `__init__`, `stats()` and `run_state`, plus two new
methods, `push_initial_state` and `vehicle_encode_failure`, called only from
`api/server.py`; none is called by this script's drain loop). With three runs a side,
this record cannot separate the
+93 µs from run-to-run variation on unchanged code; it is reported as measured, not
explained. The +0.28 µs overhead-median change is, by the same evidence, also a change on
code this script does not exercise differently between the two commits.

## Raw output

Every run's full stdout/stderr (with a UTC timestamp header and `exit=`) is committed
verbatim under `docs/validation/gui-m3b-overhead-runs/`:

- `state-cost-before-{1,2,3}.txt`, `state-cost-after-{1,2,3}.txt`
- `m1-check-before-{1,2,3}.txt`, `m1-check-after-{1,2,3}.txt`

## Gates (after / HEAD `17d6e3b`)

| Gate | Command | Result |
|---|---|---|
| Lint | `.venv/bin/ruff check .` | clean |
| Types | `.venv/bin/mypy` (bare) | clean, 59 source files (`scripts/` is outside `[tool.mypy] files = ["src"]`, so this script is not in mypy's scope) |
| Full suite | `unshare -r -n bash -c 'ip link set lo up && .venv/bin/python -m pytest -p no:cacheprovider -q -rsx'` | **1318 passed, 69 skipped, 2 xfailed** (the c63a9c4 baseline was 1288 passed, 69 skipped, 2 xfailed; tasks 26-28 added 30 tests) |

## What this does and does not show

- This measures **function-call CPU cost in one process**, not wire latency, not
  end-to-end publish latency, and not anything about `vcan0` or a real client connection.
- It is **not** the M2 early check (`scripts/gui_m2_early_check.py`, vcan, in a namespace,
  with real clients) and **not** the M4 benchmark. Neither was run as part of this record;
  the optional M2 early check that design §17 (1.5) allows as a non-acceptance regression
  comparison was **not run**.
- **The M2 latency `STOP` stays open.** Nothing here is evidence toward or against its
  acceptance criteria; it stays unresolved unless its acceptance criteria actually pass.
- All figures are reported as measured. No claim is made beyond what is printed in the
  raw output files.
