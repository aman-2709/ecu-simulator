# GUI M2 early check and verification record

**An early check on vcan, one round, conditions 1, 2 and 4 only; not the M4 benchmark.
P1–P9 are judged at M4** ([decisions/0010 §9.2](../decisions/0010-gui-observer-api.md)).
It has no condition 3 or 5, no rotation of condition order, no RSS soak, and no JSONL
output.

**Result: `STOP` in all three runs.** Condition 4's median wire latency exceeded condition
1's median + 0.10 ms by 7–11 µs in every run. Every other early-check criterion held:
p99 within condition 1 + 0.50 ms, 0 lost replies, no P5 problem, and no `delivery_unknown`
beyond the P5(h) allowance. Per the stop rule this is reported before M3, and nothing was
tuned.

## Host

| | |
|---|---|
| Date | 2026-09-27 (runs 17:09–17:10 local, UTC−7) |
| Host | Intel Core i7-8700 @ 3.20 GHz, 12 CPUs, governor `powersave`, no deliberate other load (load average 1.33 / 1.83 / 2.33 just after the runs) |
| OS, kernel | Ubuntu 22.04.5 LTS, `6.8.0-138-generic` |
| Python | 3.12.12 (uv), worktree `.venv` with `-e .[dev,hardware,gui]` |
| aiohttp | 3.14.3 |
| Commit | `gui` at `334ecf4`, with `scripts/run_gui_m2_early_check.sh` and `scripts/gui_m2_early_check.py` as committed alongside this record |
| Namespace | `unshare -r -n`, `lo` up, a private `vcan0`; the host's `can0` and `vcan0` are not touched |

## Method

`scripts/run_gui_m2_early_check.sh` creates the namespace and runs
`scripts/gui_m2_early_check.py`. For each condition it starts `candump -L vcan0`, then the
simulator (default profile, waiting for its "ecu-simulator ready on" line), then 5,000
requests from a functional ISO-TP tester (0x7DF out, 0x7E8 in), each sent after the
previous reply or a 1 s timeout. The mix is `01 0D` / `01 10` alternating, with `01 00`,
`01 20`, `01 40` and `09 02` once every 100 requests.

- Condition 1: no `--api`. Condition 2: `--api 127.0.0.1:8765`, no clients. Condition 4:
  `--api`, three reading WebSocket clients and one stalled raw socket (4 KiB receive
  buffer, never read), reconnected whenever `forced_disconnects` rises.
- Wire latency: a 0x7DF request frame to the **first** 0x7E8 frame before the next
  request (the `analyze.py` pairing rule). No such frame, or one later than 1 s, is a
  lost reply.
- Stop rule (the M4 P1–P3 thresholds, applied early): median ≤ condition 1 + 0.10 ms,
  p99 ≤ condition 1 + 0.50 ms, lost = 0; and any P5 problem.
- P5 at quiesce, conditions 2 and 4: (a); the §5.1 identities on every open and retained
  ledger and on `closed_totals`; `connections_opened = clients + closed_totals.connections
  + closed_unresolved`; P5(e) `close_codes["1013"] = forced_disconnects` and
  `close_codes["1011"] = fanout_failed + writer_failed`; P5(h): the allowed
  `delivery_unknown` counts are printed per condition, and anything beyond the allowance
  (per code, per ledger, `delivery_unknown_over_allowance`, or `closed_unresolved` > 0)
  makes the result `INCONCLUSIVE`.

## The three runs, verbatim

Command: `for i in 1 2 3; do scripts/run_gui_m2_early_check.sh; done`, stdout and
stderr together; the `=== run` and `exit=` lines were added by the loop.

```text
=== run 1 2026-09-27T17:09:55-07:00
condition 1: {"api": false, "clients": false, "requests": 5000, "seconds": 0.79, "tester_timeouts": 0, "median_ms": 0.126, "p99_ms": 0.238, "lost": 0, "paired": 5000}
condition 2: {"api": true, "clients": false, "requests": 5000, "seconds": 0.96, "tester_timeouts": 0, "p5_problems": [], "inconclusive": [], "delivery_unknown_allowed": {}, "forced_disconnects": 0, "connections_opened": 0, "reader_seq_ok": true, "median_ms": 0.162, "p99_ms": 0.273, "lost": 0, "paired": 5000}
condition 4: {"api": true, "clients": true, "requests": 5000, "seconds": 1.48, "tester_timeouts": 0, "p5_problems": [], "inconclusive": [], "delivery_unknown_allowed": {"1006": 1}, "forced_disconnects": 0, "connections_opened": 4, "reader_seq_ok": true, "median_ms": 0.237, "p99_ms": 0.51, "lost": 0, "paired": 5000}
captures in /tmp/gui-m2-rqerpk7m
STOP: report before M3
exit=1
=== run 2 2026-09-27T17:10:03-07:00
condition 1: {"api": false, "clients": false, "requests": 5000, "seconds": 0.84, "tester_timeouts": 0, "median_ms": 0.145, "p99_ms": 0.243, "lost": 0, "paired": 5000}
condition 2: {"api": true, "clients": false, "requests": 5000, "seconds": 0.93, "tester_timeouts": 0, "p5_problems": [], "inconclusive": [], "delivery_unknown_allowed": {}, "forced_disconnects": 0, "connections_opened": 0, "reader_seq_ok": true, "median_ms": 0.163, "p99_ms": 0.254, "lost": 0, "paired": 5000}
condition 4: {"api": true, "clients": true, "requests": 5000, "seconds": 1.46, "tester_timeouts": 0, "p5_problems": [], "inconclusive": [], "delivery_unknown_allowed": {"1006": 1}, "forced_disconnects": 0, "connections_opened": 4, "reader_seq_ok": true, "median_ms": 0.252, "p99_ms": 0.396, "lost": 0, "paired": 5000}
captures in /tmp/gui-m2-b687y25q
STOP: report before M3
exit=1
=== run 3 2026-09-27T17:10:11-07:00
condition 1: {"api": false, "clients": false, "requests": 5000, "seconds": 0.8, "tester_timeouts": 0, "median_ms": 0.128, "p99_ms": 0.244, "lost": 0, "paired": 5000}
condition 2: {"api": true, "clients": false, "requests": 5000, "seconds": 0.96, "tester_timeouts": 0, "p5_problems": [], "inconclusive": [], "delivery_unknown_allowed": {}, "forced_disconnects": 0, "connections_opened": 0, "reader_seq_ok": true, "median_ms": 0.165, "p99_ms": 0.264, "lost": 0, "paired": 5000}
condition 4: {"api": true, "clients": true, "requests": 5000, "seconds": 1.42, "tester_timeouts": 0, "p5_problems": [], "inconclusive": [], "delivery_unknown_allowed": {"1006": 1}, "forced_disconnects": 0, "connections_opened": 4, "reader_seq_ok": true, "median_ms": 0.236, "p99_ms": 0.411, "lost": 0, "paired": 5000}
captures in /tmp/gui-m2-wk92x6ke
STOP: report before M3
exit=1
```

The `candump` captures were written to the `/tmp` directories named above. They are not
committed; M4 commits its captures.

### Against the limits

| Run | Cond. 1 median / p99 (ms) | Median limit | Cond. 2 median / p99 | Cond. 4 median / p99 | Median miss | p99 limit |
|---|---|---|---|---|---|---|
| 1 | 0.126 / 0.238 | 0.226 | 0.162 / 0.273 | **0.237** / 0.510 | cond. 4 by **0.011** | 0.738 |
| 2 | 0.145 / 0.243 | 0.245 | 0.163 / 0.254 | **0.252** / 0.396 | cond. 4 by **0.007** | 0.743 |
| 3 | 0.128 / 0.244 | 0.228 | 0.165 / 0.264 | **0.236** / 0.411 | cond. 4 by **0.008** | 0.744 |

- Condition 2 is within every limit in every run: median +0.018 to +0.037 ms, p99 +0.011
  to +0.035 ms.
- Condition 4 is within the p99 limit in every run (+0.153 to +0.272 ms against +0.50 ms),
  and **misses the median limit in every run**, by 0.007–0.011 ms.
- Lost replies: 0 in every condition of every run; 5,000 of 5,000 paired; 0 tester
  timeouts.
- P5 problems: none. `INCONCLUSIVE` notes: none.
- Allowed `delivery_unknown` (P5(h)), reported explicitly: condition 2 `{}` in every
  run; condition 4 `{"1006": 1}` in every run. That is the stalled socket, closed by the
  harness at the end of the condition, carrying the one frame that was in its transport
  buffer. It is within the allowance of one per 1006 close.

### What this check did not exercise

- **No forced disconnect happened** (`forced_disconnects = 0` in every condition 4). A
  condition lasted about 1.5 s, and §4.3 forces a stalled client off only after 5 s of
  continuous overflow. So the 1013 path, the harness's reconnect, and P5(e)'s 1013
  reconciliation were not exercised here (0 = 0). M4's 20,000-request conditions will
  last long enough to exercise them.
- P5(c), (d) (received versus `sent`), (f) as a union across polls, and (g) (JSONL) are
  M4 harness work. `reader_seq_ok` only checks that each reader's `seq` values are
  strictly increasing without duplicates.
- The cause of the condition 4 median excess was not investigated. The readers and the
  status poller run on the same host (the readers in the tester's own process), and the
  governor is `powersave`; neither is shown to be the cause.

## Gates

| Gate | Command | Result |
|---|---|---|
| Full suite, host (`[dev,hardware,gui]`, host `vcan0`) | `.venv/bin/python -m pytest -p no:cacheprovider -rsx` | First run: **1 failed**, 1183 passed, 1 skipped, 2 xfailed. Second run: **1184 passed, 1 skipped, 2 xfailed**. See below |
| vcan integration, in a namespace | `scripts/run_integration_tests.sh -q -p no:cacheprovider` | **69 passed** |
| Lint | `.venv/bin/ruff check .` | clean |
| Types | `.venv/bin/mypy` (bare) | clean, 58 source files |
| CI shape, `.[dev]` | fresh clone of `gui` at `334ecf4`, new venv with `-e .[dev]` only (aiohttp not installed), `unshare -r -n` with `lo` up, `pytest --color=no -p no:cacheprovider -rsx` | **1044 passed, 59 skipped, 2 xfailed** |
| CI shape, `.[dev,gui]` | as above, with `-e .[dev,gui]` (aiohttp 3.14.3) | **1094 passed, 57 skipped, 2 xfailed** |

The first full-suite failure was
`tests/unit/observe/test_publisher.py::test_encode_failure_publishes_a_fallback_and_seq_stays_contiguous`:
`drain_turn()` returned 2, not 4. The first encode failure logs a traceback, and on that
run it took the turn past its 1 ms budget (`TURN_MAX_S`), so the turn stopped early. The
test passed in the second full run and in three runs of the module alone. It is a
timing-sensitive test, not an early-check file, and it was not changed. The two xfails are
DEV-11 and DEV-03 (characterization); the host's one skip is the "not installed" test,
because aiohttp is installed.

### Skips in the CI-shaped runs, by reason

`.[dev]` (59):

| Count | Reason |
|---|---|
| 54 | CAN interface 'vcan0' does not exist (no `vcan0` in the namespace) |
| 3 | needs the optional [gui] extra (aiohttp): `tests/unit/api/test_run_with_api.py`, `test_server_http.py`, `test_server_ws.py`, one module skip each — every `tests/unit/api` module |
| 2 | needs the optional [hardware] extra: `tests/unit/test_elm327_serial.py`, `tests/integration/test_elm327_simulated.py` |

`test_aiohttp_is_not_installed_in_this_environment` **ran and passed** in `.[dev]`.

`.[dev,gui]` (57):

| Count | Reason |
|---|---|
| 54 | CAN interface 'vcan0' does not exist |
| 2 | needs the optional [hardware] extra (same two modules) |
| 1 | `tests/unit/test_gui_extra_is_optional.py:57`: aiohttp is installed (a [gui] environment): the 'not installed' form runs in the .[dev] jobs |

The `api` tests ran in `.[dev,gui]`; none of them is skipped.

## Where each result comes from

1. **Hosted CI.** One hosted run exists so far: run `36360139096` on `e70e731`. For its
   results, see the controller's report. This record adds no other CI result, and
   nothing in it was produced by CI.
2. **Local vcan results.** The early check above and `scripts/run_integration_tests.sh`
   (69 passed) ran on this host, in private namespaces. **They are local, not CI.**
3. **Skips.** Listed above with their reasons and counts, for the host run and both
   CI-shaped local runs.
