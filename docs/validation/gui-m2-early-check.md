# GUI M2 early check and verification record

**An early check on vcan, one round, conditions 1, 2 and 4 only; not the M4 benchmark.
P1–P9 are judged at M4** ([decisions/0010 §9.2](../decisions/0010-gui-observer-api.md)).
It has no condition 3 or 5, no rotation of condition order, no RSS soak, and no JSONL
output.

**Result: `STOP` in all six runs** (three with the script at `cf0cb3a`, three after fix
round 1). Condition 4's median wire latency exceeded condition 1's median + 0.10 ms in
every run, by 7–16 µs. Every other early-check criterion held: p99 within condition 1 +
0.50 ms, 0 lost replies, no P5 problem, and no `delivery_unknown` beyond the P5(h)
allowance. Per the stop rule this is reported before M3. No threshold was tuned, and no
condition was lengthened.

**Condition 4 here is "stalled, not overflowing, never forced off".** The stalled
connection dropped nothing (`client_dropped = 0`) and was never forced off
(`forced_disconnects = 0`), so the §4.3 overflow and 1013 path is **not** exercised by this
check. See "The condition 4 limitation" below.

## Host

| | |
|---|---|
| Date | 2026-09-27 (runs 17:09–17:10 and 17:22–17:23 local, UTC−7) |
| Host | Intel Core i7-8700 @ 3.20 GHz, 12 CPUs, governor `powersave`, no deliberate other load (load average 1.33 / 1.83 / 2.33 just after the first runs) |
| OS, kernel | Ubuntu 22.04.5 LTS, `6.8.0-138-generic` |
| Python | 3.12.12 (uv), worktree `.venv` with `-e .[dev,hardware,gui]` |
| aiohttp | 3.14.3 |
| Namespace | `unshare -r -n`, `lo` up, a private `vcan0`; the host's `can0` and `vcan0` are not touched |

### Commits

| Commit | What |
|---|---|
| `334ecf4` | the tree the first three runs used (product code) |
| `6a04ee5` | `test(observe): fixed clock where a real 1 ms turn budget could truncate a non-timing test`. It **fixed the flaky test** `test_encode_failure_publishes_a_fallback_and_seq_stays_contiguous` and three others, which now use `monotonic=lambda: 0.0`: a real 1 ms turn budget had truncated the turn under load. The diff from `334ecf4` is test-only (`tests/unit/observe/test_publisher.py`), so **the product code is identical and the first three runs' numbers stand** |
| `cf0cb3a` | the scripts and the first version of this record (script "at `cf0cb3a`") |
| `73e9e9f` | fix round 1 of the script: the stalled ledger, reconnects, readers and quiesce are reported. The second three runs and every gate below ran on this tree |

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
- Added in fix round 1 (`73e9e9f`):
  - the readers connect first, and the stalled socket's connection id is learnt from
    `/status` as the id that appears when it connects; its ledger (`id`, `close_code`,
    `client_dropped`, `queued`, `enqueued`, `sent`, `delivery_unknown`) is printed just
    before the harness stops (`stalled_before_stop`) and at quiesce (`stalled_at_quiesce`);
  - `reconnects` (harness reconnects of the stalled client) is printed, and
    `reconnects > forced_disconnects` is a P5(e) problem;
  - a quiesce that ends with `clients != 0` is a problem;
  - each reader's received-exchange count and how it ended (`stopped`, closed early, or
    the exception) are printed from the `gather` results; a stalled-client task that
    raised is a problem.

## The runs, verbatim

Command for both sets: `for i in 1 2 3; do scripts/run_gui_m2_early_check.sh; done`,
stdout and stderr together; the `=== run` and `exit=` lines were added by the loop. The
`candump` captures were written to the `/tmp` directories named in each run. They are not
committed; M4 commits its captures.

### Script at `cf0cb3a` (product code `334ecf4`)

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

### Script after fix round 1 (`73e9e9f`)

```text
=== run 1 2026-09-27T17:22:29-07:00
condition 1: {"api": false, "clients": false, "requests": 5000, "seconds": 0.73, "tester_timeouts": 0, "median_ms": 0.117, "p99_ms": 0.208, "lost": 0, "paired": 5000}
condition 2: {"api": true, "clients": false, "requests": 5000, "seconds": 0.94, "tester_timeouts": 0, "p5_problems": [], "inconclusive": [], "delivery_unknown_allowed": {}, "forced_disconnects": 0, "connections_opened": 0, "reader_seq_ok": true, "median_ms": 0.161, "p99_ms": 0.272, "lost": 0, "paired": 5000}
condition 4: {"api": true, "clients": true, "requests": 5000, "seconds": 1.38, "tester_timeouts": 0, "stalled_before_stop": [{"id": 4, "close_code": null, "client_dropped": 0, "queued": 47, "enqueued": 5000, "sent": 4953, "delivery_unknown": 0}], "p5_problems": [], "inconclusive": [], "delivery_unknown_allowed": {"1006": 1}, "forced_disconnects": 0, "connections_opened": 4, "reader_seq_ok": true, "reconnects": 0, "readers": [{"exchanges": 5000, "ended": "stopped"}, {"exchanges": 5000, "ended": "stopped"}, {"exchanges": 5000, "ended": "stopped"}], "stalled_ids": [4], "stalled_at_quiesce": [{"id": 4, "close_code": 1006, "client_dropped": 0, "queued": 0, "enqueued": 5000, "sent": 4953, "delivery_unknown": 1}], "median_ms": 0.233, "p99_ms": 0.37, "lost": 0, "paired": 5000}
captures in /tmp/gui-m2-a3m_8k5b
STOP: report before M3
exit=1
=== run 2 2026-09-27T17:22:37-07:00
condition 1: {"api": false, "clients": false, "requests": 5000, "seconds": 0.73, "tester_timeouts": 0, "median_ms": 0.118, "p99_ms": 0.22, "lost": 0, "paired": 5000}
condition 2: {"api": true, "clients": false, "requests": 5000, "seconds": 0.92, "tester_timeouts": 0, "p5_problems": [], "inconclusive": [], "delivery_unknown_allowed": {}, "forced_disconnects": 0, "connections_opened": 0, "reader_seq_ok": true, "median_ms": 0.161, "p99_ms": 0.23, "lost": 0, "paired": 5000}
condition 4: {"api": true, "clients": true, "requests": 5000, "seconds": 1.27, "tester_timeouts": 0, "stalled_before_stop": [{"id": 4, "close_code": null, "client_dropped": 0, "queued": 46, "enqueued": 5000, "sent": 4954, "delivery_unknown": 0}], "p5_problems": [], "inconclusive": [], "delivery_unknown_allowed": {"1006": 1}, "forced_disconnects": 0, "connections_opened": 4, "reader_seq_ok": true, "reconnects": 0, "readers": [{"exchanges": 5000, "ended": "stopped"}, {"exchanges": 5000, "ended": "stopped"}, {"exchanges": 5000, "ended": "stopped"}], "stalled_ids": [4], "stalled_at_quiesce": [{"id": 4, "close_code": 1006, "client_dropped": 0, "queued": 0, "enqueued": 5000, "sent": 4954, "delivery_unknown": 1}], "median_ms": 0.229, "p99_ms": 0.38, "lost": 0, "paired": 5000}
captures in /tmp/gui-m2-rmzrdyl_
STOP: report before M3
exit=1
=== run 3 2026-09-27T17:22:45-07:00
condition 1: {"api": false, "clients": false, "requests": 5000, "seconds": 0.82, "tester_timeouts": 0, "median_ms": 0.137, "p99_ms": 0.275, "lost": 0, "paired": 5000}
condition 2: {"api": true, "clients": false, "requests": 5000, "seconds": 1.13, "tester_timeouts": 0, "p5_problems": [], "inconclusive": [], "delivery_unknown_allowed": {}, "forced_disconnects": 0, "connections_opened": 0, "reader_seq_ok": true, "median_ms": 0.191, "p99_ms": 0.348, "lost": 0, "paired": 5000}
condition 4: {"api": true, "clients": true, "requests": 5000, "seconds": 1.45, "tester_timeouts": 0, "stalled_before_stop": [{"id": 4, "close_code": null, "client_dropped": 0, "queued": 47, "enqueued": 5000, "sent": 4953, "delivery_unknown": 0}], "p5_problems": [], "inconclusive": [], "delivery_unknown_allowed": {"1006": 1}, "forced_disconnects": 0, "connections_opened": 4, "reader_seq_ok": true, "reconnects": 0, "readers": [{"exchanges": 5000, "ended": "stopped"}, {"exchanges": 5000, "ended": "stopped"}, {"exchanges": 5000, "ended": "stopped"}], "stalled_ids": [4], "stalled_at_quiesce": [{"id": 4, "close_code": 1006, "client_dropped": 0, "queued": 0, "enqueued": 5000, "sent": 4953, "delivery_unknown": 1}], "median_ms": 0.249, "p99_ms": 0.415, "lost": 0, "paired": 5000}
captures in /tmp/gui-m2-8if03v3k
STOP: report before M3
exit=1
```

### Against the limits

| Set, run | Cond. 1 median / p99 (ms) | Median limit | Cond. 2 median / p99 | Cond. 4 median / p99 | Median miss | p99 limit | Verdict |
|---|---|---|---|---|---|---|---|
| `cf0cb3a` 1 | 0.126 / 0.238 | 0.226 | 0.162 / 0.273 | **0.237** / 0.510 | cond. 4 by **0.011** | 0.738 | STOP |
| `cf0cb3a` 2 | 0.145 / 0.243 | 0.245 | 0.163 / 0.254 | **0.252** / 0.396 | cond. 4 by **0.007** | 0.743 | STOP |
| `cf0cb3a` 3 | 0.128 / 0.244 | 0.228 | 0.165 / 0.264 | **0.236** / 0.411 | cond. 4 by **0.008** | 0.744 | STOP |
| fix 1 | 0.117 / 0.208 | 0.217 | 0.161 / 0.272 | **0.233** / 0.370 | cond. 4 by **0.016** | 0.708 | STOP |
| fix 2 | 0.118 / 0.220 | 0.218 | 0.161 / 0.230 | **0.229** / 0.380 | cond. 4 by **0.011** | 0.720 | STOP |
| fix 3 | 0.137 / 0.275 | 0.237 | 0.191 / 0.348 | **0.249** / 0.415 | cond. 4 by **0.012** | 0.775 | STOP |

- Condition 2 is within every limit in every run: median +0.018 to +0.054 ms, p99 well
  within +0.50 ms.
- Condition 4 is within the p99 limit in every run, and **misses the median limit in
  every run**, by 0.007–0.016 ms.
- Lost replies: 0 in every condition of every run; 5,000 of 5,000 paired; 0 tester
  timeouts.
- P5 problems: none. `INCONCLUSIVE` notes: none. Harness reconnects: 0, equal to
  `forced_disconnects` = 0. Every reader received all 5,000 exchanges and ended `stopped`.
- Allowed `delivery_unknown` (P5(h)), reported explicitly: condition 2 `{}` in every run;
  condition 4 `{"1006": 1}` in every run. That is the stalled socket (connection 4),
  closed by the harness at the end of the condition, carrying the one frame that was in
  its transport buffer. It is within the allowance of one per 1006 close.

**Noise.** Condition 1's median varies by 19 µs across the first three runs (0.126 /
0.145 / 0.128 ms), more than their 7–11 µs miss. But the direction held in 3 of 3 runs
(and in a 300-request smoke run before them, and in 3 of 3 runs after fix round 1), so
the result stays STOP.

**Harness bias.** Wire latency is request frame to reply frame, and that interval lies
entirely inside the simulator and the kernel. The tester's GIL contention with the reader
tasks in its own process only delays the **next** request. So the harness is unlikely to
have inflated condition 4 against condition 1. The cause of the excess was not
investigated.

### The stalled connection's ledger

After fix round 1, in all three runs, the stalled connection (id 4) had, just before the
harness stopped it:

| Run | `client_dropped` | `queued` | `enqueued` | `sent` | `delivery_unknown` |
|---|---|---|---|---|---|
| fix 1 | 0 | 47 | 5000 | 4953 | 0 |
| fix 2 | 0 | 46 | 5000 | 4954 | 0 |
| fix 3 | 0 | 47 | 5000 | 4953 | 0 |

At quiesce it was closed 1006 with `queued` 0, `sent` unchanged and `delivery_unknown` 1.
So the socket that never read accepted about 4,950 messages (the writer's `send` returned
for them) into the transport and kernel buffers, and its 1,024-message queue held fewer
than 50. **It never overflowed**, and nothing was dropped.

## The condition 4 limitation, and a design risk for M4

Condition 4 here is **stalled, not overflowing, never forced off**. The §4.3 rule
disconnects a stalled client only after 5 s of **continuous overflow**, and overflow
begins only after the transport and kernel buffers and the 1,024-message queue are full.

The arithmetic, from the runs above:

- condition 4 ran at 3,450–3,940 requests/s (5,000 requests in 1.27–1.45 s), one exchange
  event per request;
- before any overflow, the stalled connection absorbs at least ~4,950 messages into its
  buffers (measured) plus 1,024 in its queue: **≥ ~6,000 messages, ≥ ~1.5–1.7 s**;
- the first forced disconnect then comes 5 s later: **no earlier than ~6.5–6.9 s**, i.e.
  after about **23,000–26,000 requests** (6,000 + 5 s × 3,450–3,940/s);
- M4's conditions 1–4 have 20,000 requests each: **~5.1–5.8 s**.

So **M4 condition 4, as specified, would most likely see no forced disconnect at all**, and
at best one. The 1013 path, the harness's reconnect and P5(e)'s 1013 reconciliation would
then go unexercised in M4 condition 4 too (condition 5, 60 s at the maximum rate, would
exercise them). This is a design risk for the owner to decide on before M4; this record
does not change the condition.

## Gates

All on `73e9e9f` (the final tree; the commit that adds this record changes only this
file).

| Gate | Command | Result |
|---|---|---|
| Full suite, host (`[dev,hardware,gui]`, host `vcan0`) | `.venv/bin/python -m pytest -p no:cacheprovider -rsx` | **1184 passed, 1 skipped, 2 xfailed**. XFAIL: DEV-11 and DEV-03 (characterization). Skip: `tests/unit/test_gui_extra_is_optional.py:57`, the "not installed" test, because aiohttp is installed |
| vcan integration, in a namespace | `scripts/run_integration_tests.sh -q -p no:cacheprovider` | **69 passed** |
| Lint | `.venv/bin/ruff check .` | clean |
| Types | `.venv/bin/mypy` (bare) | clean, 58 source files |
| CI shape, `.[dev]` | fresh clone at `73e9e9f`, new venv with `-e .[dev]` only (aiohttp not installed), `unshare -r -n` with `lo` up, `pytest --color=no -p no:cacheprovider -rsx` | **1044 passed, 59 skipped, 2 xfailed**; `test_aiohttp_is_not_installed_in_this_environment` ran and passed |
| CI shape, `.[dev,gui]` | as above, with `-e .[dev,gui]` (aiohttp 3.14.3) | **1094 passed, 57 skipped, 2 xfailed**; the `api` tests ran, none skipped |

Earlier, on `334ecf4` before `6a04ee5`, the first full-suite run had 1 failure,
`test_encode_failure_publishes_a_fallback_and_seq_stays_contiguous` (`drain_turn()`
returned 2, not 4, when the real 1 ms turn budget truncated the turn under load); a rerun
passed. `6a04ee5` fixed that test with a fixed clock, and it passed in every run above.

## Hosted CI

These are the only hosted runs. **No hosted run covers the commit that adds this record**,
or `73e9e9f`.

| Run | `head_sha` | Result |
|---|---|---|
| 36360139096 | `e70e73199c9951f2d08465de65185b0baecb1004` | **FAILURE**: the `.[dev]` job failed a timing-dependent test, then undiagnosable |
| 36361183412 | `334ecf49fe364258166d01e1aad485f3d437c476` | **FAILURE**: `FAILED tests/unit/observe/test_publisher.py::test_encode_failure_publishes_a_fallback_and_seq_stays_contiguous - assert 2 == 4` on 3.12 and 3.13 |
| 36361625692 | `6a04ee5fb985591b34a414573d19ca9d75ec7b68` | **SUCCESS**: `.[dev]` 3.12 and 3.13 each 1044 passed, 59 skipped, 2 xfailed; api `.[dev,gui]` 1094 passed, 57 skipped, 2 xfailed; lint green; can-capabilities green |

Hosted skips: 54x "kernel cannot create CAN_ISOTP sockets (CONFIG_CAN_ISOTP not built,
e.g. GitHub-hosted Azure kernels)"; 3x [gui] (`.[dev]` only); 2x [hardware]; 1x
aiohttp-installed, by name (api job only).

**The 54 vcan tests are skipped on hosted runners and never validated there**
([decisions/0009](../decisions/0009-self-hosted-vcan-runner.md)).

## Local vcan results

The early check above and `scripts/run_integration_tests.sh` (69 passed) ran on this
host, in private namespaces. **They are local, not CI.**

## Skips, by reason

Host full suite (1): `tests/unit/test_gui_extra_is_optional.py:57`: aiohttp is installed
(a [gui] environment): the 'not installed' form runs in the .[dev] jobs.

Local CI-shaped `.[dev]` (59):

| Count | Reason |
|---|---|
| 54 | CAN interface 'vcan0' does not exist (no `vcan0` in the namespace) |
| 3 | needs the optional [gui] extra (aiohttp): `tests/unit/api/test_run_with_api.py`, `test_server_http.py`, `test_server_ws.py`, one module skip each — every `tests/unit/api` module |
| 2 | needs the optional [hardware] extra: `tests/unit/test_elm327_serial.py`, `tests/integration/test_elm327_simulated.py` |

Local CI-shaped `.[dev,gui]` (57):

| Count | Reason |
|---|---|
| 54 | CAN interface 'vcan0' does not exist |
| 2 | needs the optional [hardware] extra (same two modules) |
| 1 | `tests/unit/test_gui_extra_is_optional.py:57`: aiohttp is installed (a [gui] environment): the 'not installed' form runs in the .[dev] jobs |

Locally the 54 are skipped because the namespace has no `vcan0`; on hosted runners
because the kernel lacks CAN_ISOTP. Locally they ran, and passed, through
`scripts/run_integration_tests.sh` and the host full suite.
