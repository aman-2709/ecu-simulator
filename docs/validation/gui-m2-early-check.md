# GUI M2 early check and verification record

**An early check on vcan, conditions 1, 2 and 4 only; not the M4 benchmark. P1–P9 are
judged at M4** ([decisions/0010 §9.2](../decisions/0010-gui-observer-api.md)). It has no
condition 3 or 5, no RSS soak, and no JSONL output. The first six runs had one round each,
with no rotation of condition order. The three later runs have three rounds each, with the
order rotated ("Rotated runs, 2026-09-28" below).

**Result: `STOP` in all nine runs.**

- Six single-round runs, order fixed 1→2→4: three with the script at `cf0cb3a`, three
  after fix round 1. Condition 4's median wire latency exceeded condition 1's median +
  0.10 ms in every run, by 7–16 µs.
- Three runs of three rotated rounds, at `a7431be`: condition 4's median exceeded its own
  round's condition 1 median + 0.10 ms in 7 of the 9 rounds. The other two rounds are not
  a pass. The miss follows condition 4's load, not run order; its cause is not
  established.

Every other early-check criterion held in all nine runs: p99 within condition 1 +
0.50 ms, 0 lost replies, no P5 problem among those this script checks ((a), (b), (e), (f),
(h)); (c), (d), (g) and P6 are not checked at M2, and no `delivery_unknown` beyond the
P5(h) allowance. Per the stop rule this is reported before M3. No threshold was tuned, and no
condition was lengthened.

**Condition 4 here is "stalled, not overflowing, never forced off".** The stalled
connection dropped nothing (`client_dropped = 0`) and was never forced off
(`forced_disconnects = 0`), so the §4.3 overflow and 1013 path is **not** exercised by this
check. `forced_disconnects` is 0 in all nine runs. `client_dropped` is 0 in all six runs
that print the stalled ledger: three after fix round 1, and the three rotated. See "The condition 4 limitation" below, and "M4 forced 1013 closes: a proposal for
the owner", which awaits the owner's decision.

## Host

| | |
|---|---|
| Date | 2026-09-27 (runs 17:09–17:10 and 17:22–17:23 local, UTC−7) |
| Host | Intel Core i7-8700 @ 3.20 GHz, 12 CPUs, governor `powersave`, no deliberate other load (load average 1.33 / 1.83 / 2.33 just after the first runs) |
| OS, kernel | Ubuntu 22.04.5 LTS, `6.8.0-138-generic` |
| Python | 3.12.12 (uv), worktree `.venv` with `-e .[dev,hardware,gui]` |
| aiohttp | 3.14.3 |
| Namespace | `unshare -r -n`, `lo` up, a private `vcan0`; the host's `can0` and `vcan0` are not touched |

This table is for the first six runs. The rotated runs' environment is in "Rotated runs,
2026-09-28".

### Commits

| Commit | What |
|---|---|
| `334ecf4` | the tree the first three runs used (product code) |
| `6a04ee5` | `test(observe): fixed clock where a real 1 ms turn budget could truncate a non-timing test`. It **fixed the flaky test** `test_encode_failure_publishes_a_fallback_and_seq_stays_contiguous` and three others, which now use `monotonic=lambda: 0.0`: a real 1 ms turn budget had truncated the turn under load. The diff from `334ecf4` is test-only (`tests/unit/observe/test_publisher.py`), so **the product code is identical and the first three runs' numbers stand** |
| `cf0cb3a` | the scripts and the first version of this record (script "at `cf0cb3a`") |
| `73e9e9f` | fix round 1 of the script: the stalled ledger, reconnects, readers and quiesce are reported. The second three runs and every gate below ran on this tree |
| `5f1f202`, `a7431be` | the harness hardened for rotated rounds (Task 10 and its review round 1): cleanup on every path, a refused stalled connect fails the run, `--rounds` and `--order`, `rate_rps`, dispatch latency, the host line and the summary. `src/` unchanged. The three rotated runs ran on `a7431be` |
| `c8fbe22` | the rotated runs' stdout, verbatim, in `gui-m2-early-check-runs/` |

## Method

This is the method of the first six runs. What changed for the rotated runs is in
"Rotated runs, 2026-09-28".

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

**Harness bias.** The cause of the excess is undetermined. This check does not isolate it
from several confounders: the condition order was fixed 1→2→4 in every run, with no
rotation, across a single round; the host ran governor `powersave`; the tester's own
request rate roughly halves from condition 1 to condition 4 (5,000 requests in
0.73–0.84 s vs. 1.27–1.48 s), and the longer idle gaps between requests can change the
CPU's wake-up and C-state latency inside the request-to-reply interval being measured;
and condition 4 runs three aiohttp reader tasks sharing the host with the tester process,
where condition 1 runs none. The 7–16 µs miss is also smaller than condition 1's own
19 µs run-to-run spread (see "Noise" above). None of this changes the result: per the
stop rule, STOP stands as reported, and no threshold was tuned. A rotated-order
diagnostic rerun, with the thresholds unchanged, would help the owner distinguish a real
condition-4 cost from an order or host-scheduling artifact.

(Added 2026-09-28: that rerun is "Rotated runs, 2026-09-28" below. The miss follows
condition 4, not its position; the cause is still undetermined.)

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

- condition 4 ran at 3,380–3,940 requests/s (5,000 requests in 1.27–1.48 s), one exchange
  event per request;
- before any overflow, the stalled connection absorbs at least ~4,950 messages into its
  buffers (measured) plus 1,024 in its queue: **≥ ~6,000 messages, ≥ ~1.5–1.8 s**;
- the first forced disconnect then comes 5 s later: **no earlier than ~6.5–6.9 s**, i.e.
  after about **23,000–26,000 requests** (6,000 + 5 s × 3,380–3,940/s);
- M4's conditions 1–4 have 20,000 requests each: **~5.1–5.9 s** — still under the earliest
  possible forced disconnect (~6.5 s), so the conclusion below survives.

So **M4 condition 4, as specified, would most likely see no forced disconnect at all**, and
at best one. The 1013 path, the harness's reconnect and P5(e)'s 1013 reconciliation would
then go unexercised in M4 condition 4 too (condition 5, 60 s at the maximum rate, would
exercise them). This is a design risk for the owner to decide on before M4; this record
does not change the condition.

(Added 2026-09-28: the rotated runs below give the same ledger, and "M4 forced 1013
closes: a proposal for the owner" sets out the options. It awaits the owner's decision.)

## Rotated runs, 2026-09-28: three runs of three rounds

This part answers one question from the plan ("Investigation after the Task 9 STOP",
`docs/plans/gui-m2-implementation.md`): does condition 4's miss follow client load or run
order? It adds **local vcan evidence only**.

**Result: `STOP` in all three runs** (exit status 1). Condition 4's median exceeded its
own round's condition 1 median + 0.10 ms in **7 of the 9 rounds**. The other two rounds
printed no stop reason. That is **not a pass** of the check: the stop rule applies to
every round, as M4's P1 does ("in every round"). No threshold was tuned, and no condition
was lengthened. Per the stop rule, this is reported before M3.

### Environment

| | |
|---|---|
| Commit | `a7431be265346f36225774c003853930344bc4c0`, named in each run file's header. `src/` is identical to `334ecf4` and `73e9e9f` (`git diff 334ecf4 a7431be -- src` is empty): **the product code is unchanged since the first six runs; the harness changed** |
| Date | 2026-09-28; the runs started at 00:42:52, 00:43:16 and 00:43:40 local (UTC−7) |
| CPUs | 12 (`nproc`: the scheduler affinity count), governor `powersave` on all 12, from the `host:` line each run prints |
| Kernel | `6.8.0-138-generic`, from the same line |
| Load | 1-minute load average 6.37, 6.63 and 5.43 at the start of runs 1, 2 and 3 (file headers). That is higher than the 1.33 recorded just after the first three runs. The operator reports it came from desktop background processes, with the CPUs about 80 % idle; neither is recorded in the run files |
| Python, aiohttp | the worktree `.venv`: Python 3.12.12, aiohttp 3.14.3, as checked when this section was written. The run files do not record them |
| Namespace | as before: `unshare -r -n`, `lo` up, a private `vcan0` |

### Method, as changed at `5f1f202` and `a7431be`

- Command, three times: `scripts/run_gui_m2_early_check.sh --rounds 3`. 5,000 requests
  per condition (the default `-n`). `--order` was not given.
- **Rotation.** Round r runs rotation r of (1, 2, 4): round 0 = 1, 2, 4; round 1 = 2, 4,
  1; round 2 = 4, 1, 2. So each run puts each condition in each position once, and the
  three runs give three samples per (condition, position). `--order` would instead fix one
  order for every round, as a control; it was not used.
- Each condition still starts its own `candump` and its own simulator process, and stops
  them after. The harness process (the tester thread and, in condition 4, the readers and
  the stalled client) lives for the whole run.
- Each round is judged against **its own** condition 1, by the same stop rule as before. A
  round that misses prints a `STOP reasons` line; a round within the limits prints none.
- New per condition:
  - `rate_rps`: 5,000 / the tester loop's elapsed time.
  - `dispatch_last500_ms` (conditions 2 and 4): median and p99 of the events'
    `dispatch_us`, from one `GET /api/v1/exchanges?limit=500` made after the tester
    finishes. The history keeps 500 events, so this covers requests 4,501–5,000.
  - `dispatch_reader0_ms` (condition 4): the same, over every exchange event reader 0
    received (5,000).
  - `dispatch_us` is **dispatcher time only**, not wire latency (0010 §5). Condition 1
    has no API, so it has no dispatch figure.
- **Summary lines**: for each (condition, position), the median over the run's rounds of
  the condition's median and of its excess. Each run has one round per (condition,
  position), so every summary line has `n = 1` and repeats one round's values. The pooled
  figures below are computed from the JSON lines.
- In the timed window the harness does the same work as in the first six runs, except that
  reader 0 also appends each event's `dispatch_us` to a list.

### The runs

The stdout of each run is committed verbatim (`c8fbe22`):
[run-1.txt](gui-m2-early-check-runs/run-1.txt),
[run-2.txt](gui-m2-early-check-runs/run-2.txt),
[run-3.txt](gui-m2-early-check-runs/run-3.txt). Each file starts with two `#` lines
(run, commit, start time, load average; the command) and ends with `# exit status: 1`.
The lines between are the script's stdout. The `candump` captures were written to the
`/tmp` directories each file names; they are not committed.

Every round's verdict, as printed:

| Run | Round | Order | Cond. 1 median (ms) | Median limit | Cond. 2 median | Cond. 4 median | Printed |
|---|---|---|---|---|---|---|---|
| 1 | 0 | 1, 2, 4 | 0.159 | 0.259 | 0.196 | 0.224 | no `STOP reasons` line |
| 1 | 1 | 2, 4, 1 | 0.118 | 0.218 | 0.168 | **0.238** | `STOP reasons, round 1 condition 4: ["median 0.238 > condition 1's 0.118 + 0.1"]` |
| 1 | 2 | 4, 1, 2 | 0.122 | 0.222 | 0.164 | **0.231** | `STOP reasons, round 2 condition 4: ["median 0.231 > condition 1's 0.122 + 0.1"]` |
| 2 | 0 | 1, 2, 4 | 0.125 | 0.225 | 0.165 | **0.235** | `STOP reasons, round 0 condition 4: ["median 0.235 > condition 1's 0.125 + 0.1"]` |
| 2 | 1 | 2, 4, 1 | 0.117 | 0.217 | 0.165 | **0.256** | `STOP reasons, round 1 condition 4: ["median 0.256 > condition 1's 0.117 + 0.1"]` |
| 2 | 2 | 4, 1, 2 | 0.127 | 0.227 | 0.163 | **0.230** | `STOP reasons, round 2 condition 4: ["median 0.23 > condition 1's 0.127 + 0.1"]` |
| 3 | 0 | 1, 2, 4 | 0.118 | 0.218 | 0.168 | **0.230** | `STOP reasons, round 0 condition 4: ["median 0.23 > condition 1's 0.118 + 0.1"]` |
| 3 | 1 | 2, 4, 1 | 0.143 | 0.243 | 0.170 | 0.235 | no `STOP reasons` line |
| 3 | 2 | 4, 1, 2 | 0.123 | 0.223 | 0.169 | **0.229** | `STOP reasons, round 2 condition 4: ["median 0.229 > condition 1's 0.123 + 0.1"]` |

Each run then printed `STOP: report before M3` and exited 1.

### Against the limits

Wire latency, dispatch latency and rate, per condition, round and position. The excess
columns are this condition's value minus its own round's condition 1 value, computed here
(the script prints the median excess only in its summary lines). Bold: the seven medians
that printed a stop reason. The limits are condition 1 + 0.10 ms (median) and + 0.50 ms
(p99).

| Run | Round | Pos. | Cond. | Median (ms) | p99 (ms) | Median excess | p99 excess | Dispatch last 500, median / p99 (ms) | Dispatch reader 0, median / p99 (ms) | Rate (req/s) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0 | 1 | 1 | 0.159 | 0.267 | — | — | — | — | 5,490.5 |
| 1 | 0 | 2 | 2 | 0.196 | 0.463 | +0.037 | +0.196 | 0.155 / 0.380 | — | 4,253.4 |
| 1 | 0 | 3 | 4 | 0.224 | 0.383 | +0.065 | +0.116 | 0.116 / 0.197 | 0.116 / 0.201 | 3,874.3 |
| 1 | 1 | 1 | 2 | 0.168 | 0.365 | +0.050 | +0.134 | 0.113 / 0.206 | — | 4,811.0 |
| 1 | 1 | 2 | 4 | **0.238** | 0.418 | +0.120 | +0.187 | 0.168 / 0.328 | 0.122 / 0.240 | 3,581.0 |
| 1 | 1 | 3 | 1 | 0.118 | 0.231 | — | — | — | — | 6,469.5 |
| 1 | 2 | 1 | 4 | **0.231** | 0.358 | +0.109 | +0.113 | 0.120 / 0.234 | 0.120 / 0.197 | 3,766.7 |
| 1 | 2 | 2 | 1 | 0.122 | 0.245 | — | — | — | — | 6,263.4 |
| 1 | 2 | 3 | 2 | 0.164 | 0.277 | +0.042 | +0.032 | 0.121 / 0.256 | — | 5,206.9 |
| 2 | 0 | 1 | 1 | 0.125 | 0.235 | — | — | — | — | 6,280.4 |
| 2 | 0 | 2 | 2 | 0.165 | 0.263 | +0.040 | +0.028 | 0.108 / 0.190 | — | 5,160.2 |
| 2 | 0 | 3 | 4 | **0.235** | 0.393 | +0.110 | +0.158 | 0.119 / 0.238 | 0.121 / 0.216 | 3,626.9 |
| 2 | 1 | 1 | 2 | 0.165 | 0.282 | +0.048 | +0.046 | 0.103 / 0.188 | — | 5,091.2 |
| 2 | 1 | 2 | 4 | **0.256** | 0.458 | +0.139 | +0.222 | 0.122 / 0.205 | 0.137 / 0.256 | 3,392.6 |
| 2 | 1 | 3 | 1 | 0.117 | 0.236 | — | — | — | — | 6,535.5 |
| 2 | 2 | 1 | 4 | **0.230** | 0.379 | +0.103 | +0.138 | 0.118 / 0.205 | 0.118 / 0.205 | 3,758.4 |
| 2 | 2 | 2 | 1 | 0.127 | 0.241 | — | — | — | — | 6,153.1 |
| 2 | 2 | 3 | 2 | 0.163 | 0.282 | +0.036 | +0.041 | 0.108 / 0.205 | — | 5,192.6 |
| 3 | 0 | 1 | 1 | 0.118 | 0.214 | — | — | — | — | 6,790.6 |
| 3 | 0 | 2 | 2 | 0.168 | 0.291 | +0.050 | +0.077 | 0.108 / 0.229 | — | 4,944.2 |
| 3 | 0 | 3 | 4 | **0.230** | 0.409 | +0.112 | +0.195 | 0.145 / 0.349 | 0.119 / 0.236 | 3,641.0 |
| 3 | 1 | 1 | 2 | 0.170 | 0.291 | +0.027 | +0.039 | 0.114 / 0.230 | — | 4,999.6 |
| 3 | 1 | 2 | 4 | 0.235 | 0.398 | +0.092 | +0.146 | 0.117 / 0.171 | 0.124 / 0.229 | 3,552.6 |
| 3 | 1 | 3 | 1 | 0.143 | 0.252 | — | — | — | — | 6,038.8 |
| 3 | 2 | 1 | 4 | **0.229** | 0.373 | +0.106 | +0.137 | 0.119 / 0.191 | 0.119 / 0.205 | 3,784.6 |
| 3 | 2 | 2 | 1 | 0.123 | 0.236 | — | — | — | — | 6,569.8 |
| 3 | 2 | 3 | 2 | 0.169 | 0.295 | +0.046 | +0.059 | 0.104 / 0.179 | — | 4,919.5 |

In all nine rounds:

- **Condition 2** is within both limits: median +0.027 to +0.050 ms, p99 +0.028 to
  +0.196 ms.
- **Condition 4**'s median is 0.224–0.256 ms. Its excess is +0.065 to +0.139 ms, over the
  limit in 7 rounds. Its p99 is within the limit: +0.113 to +0.222 ms.
- Lost replies: 0 in every condition; 5,000 of 5,000 paired; 0 tester timeouts.
- `p5_problems` and `inconclusive`: empty in every condition 2 and 4. `reader_seq_ok`:
  true.
- Allowed `delivery_unknown` (P5(h)), reported explicitly: condition 2 `{}`; condition 4
  `{"1006": 1}` in every round, from the stalled socket that the harness closed at the end
  of the condition. That is within the allowance of one per 1006 close.
- `forced_disconnects` = 0 and harness `reconnects` = 0 in every condition 4. Every reader
  received 5,000 exchanges and ended `stopped`.
- The stalled connection, just before the harness stopped it: `client_dropped` 0,
  `queued` 46–48, `sent` 4,952–4,954 of 5,000 `enqueued`. As in the first runs, **it
  never overflowed**, and it was never forced off.

### Load or order

The plan's two tests:

- **load**: condition 4's excess over its round's condition 1 stays above +0.10 ms, or
  near it, in every position;
- **order**: the excess tracks position (for example, the condition run first is slower,
  whatever it is).

Medians and excesses (ms) by position, in run order 1, 2, 3:

| Condition | Position 1 | Position 2 | Position 3 |
|---|---|---|---|
| 1, median | 0.159, 0.125, 0.118 | 0.122, 0.127, 0.123 | 0.118, 0.117, 0.143 |
| 2, median | 0.168, 0.165, 0.170 | 0.196, 0.165, 0.168 | 0.164, 0.163, 0.169 |
| 2, excess | +0.050, +0.048, +0.027 | +0.037, +0.040, +0.050 | +0.042, +0.036, +0.046 |
| 4, median | 0.231, 0.230, 0.229 | 0.238, 0.256, 0.235 | 0.224, 0.235, 0.230 |
| 4, excess | +0.109, +0.103, +0.106 | +0.120, +0.139, +0.092 | +0.065, +0.110, +0.112 |
| 4, mean excess | **+0.106** | **+0.117** | **+0.096** |

- **The load test is met.** Condition 4's excess is above +0.10 ms in 7 of the 9 rounds,
  and its mean per position is +0.096 to +0.117 ms. The two rounds below it are run 1
  round 0 (+0.065, position 3) and run 3 round 1 (+0.092, position 2). In both, condition
  1 had the two highest medians of the nine (0.159 and 0.143 ms, against 0.117–0.127 in
  the other seven). Condition 4 in those rounds was 0.224 ms (the lowest of the nine, but
  only 0.005 ms below the next) and 0.235 ms (mid-range). So those two rounds are within
  the limit mainly because condition 1 was slow, not because condition 4 was fast.
- **The order test is not met.** No condition is consistently slower in any position.
  Condition 1's two high medians came at position 1 (the first condition of run 1) and
  at position 3. In **every** one of the nine rounds, whatever the order, the medians rank
  condition 1 < condition 2 < condition 4.
- **Verdict: load, not order.** The miss goes with condition 4, not with its position.
  "Load" here means only that the excess comes with condition 4's setup: the API with
  three reading clients and one stalled. It does not say which part of that setup, or
  which process, costs the time (see "The cause is not established" below).

**Noise, beside it.** Condition 1's median ranges over 0.117–0.159 ms across the nine
rounds (0.042 ms). Seven of the nine lie within 0.117–0.127 ms. Condition 1's p99 ranges
over 0.214–0.267 ms. Condition 4's median ranges over 0.224–0.256 ms (0.032 ms). The
per-round verdict turned on condition 1's noise in the two rounds within the limit.

**What this test cannot show.**

- Three samples per (condition, position), from three runs started 24 s apart on one
  host.
- In the rotation, a condition's position is fixed by the round index. Condition 4 is at
  position 3 in round 0, 2 in round 1 and 1 in round 2. A round effect cannot be told
  from a position effect.
- Condition 4's position-2 medians (0.235–0.256 ms) are all at or above its other six
  (0.224–0.235 ms), by up to about 0.02 ms. That is small beside the ~0.10 ms excess.
  Three samples cannot say whether it is real.

### Dispatch latency and rate

Dispatch latency (`dispatch_us`, dispatcher time only), over the nine rounds:

| | Cond. 2, last 500 | Cond. 4, last 500 | Cond. 4, reader 0 (all 5,000) |
|---|---|---|---|
| Median, range (ms) | 0.103–0.155 | 0.116–0.168 | 0.116–0.137 |
| Median, the middle of the nine (ms) | 0.108 | 0.119 | 0.120 |
| p99, range (ms) | 0.179–0.380 | 0.171–0.349 | 0.197–0.256 |

- Compare like for like, in the same last-500 window and round. Condition 4's dispatch
  median minus condition 2's is +0.011 ms (the middle of the nine; range −0.039 to
  +0.055). Condition 4's wire median minus condition 2's, in the same rounds, is +0.067 ms
  (range +0.028 to +0.091).
- So from condition 2 to condition 4, the dispatch medians move much less than the wire
  medians. **Most of the condition 2 → 4 wire difference is not time inside the
  dispatcher call.** A difference of medians is not a per-request breakdown, so this is a
  statement about medians only.
- It says nothing about condition 2's own excess over condition 1 (+0.027 to +0.050 ms).
  Condition 1 has no API and no dispatch figure.

Rate:

| | Cond. 1 | Cond. 2 | Cond. 4 |
|---|---|---|---|
| Rate (req/s) | 5,490.5–6,790.6 | 4,253.4–5,206.9 | 3,392.6–3,874.3 |
| 5,000 requests in | 0.74–0.91 s | 0.96–1.18 s | 1.29–1.47 s |

- The tester is closed-loop: it sends each request after the previous reply. A longer
  reply time lengthens every cycle, so a lower rate goes with a higher latency by
  construction.
- A rough derived figure, not a measurement: the mean cycle (1 / rate) minus the median
  wire latency is 0.023–0.038 ms for condition 1, 0.028–0.040 for condition 2 and
  0.034–0.046 for condition 4. It mixes a mean with a median. Most of the drop in rate is
  matched by the rise in wire latency.
- The data do not show whether the two are linked in any other way. For example, the
  "Harness bias" paragraph above notes that longer gaps between requests can change the
  host's wake-up latency.

### The cause is not established

The rotated runs remove two confounders listed in "Harness bias": the fixed order and the
single round. They do not identify the cause of condition 4's excess. The data are consistent with each
hypothesis below. None has been tested, and they are not exclusive.

| Hypothesis | Consistent with | What would test it |
|---|---|---|
| **H1. Work on the simulator's loop.** The publisher offers each event to four client queues, and four writer tasks send them over loopback TCP. All of it runs on the simulator's one asyncio loop, which also runs the CAN read callback and the reply's `_send` (0010 §4.2). A request that arrives while that work runs waits, before dispatch starts or before the reply is sent | the excess lying mostly outside `dispatch_us`; condition 2's smaller excess, with no clients | condition 3 (one reader) beside condition 4, and a diagnostic run with three readers and no stalled client, to see whether the excess scales with clients; the publisher's `longest_turn_s` (already in `/status`) printed per condition; the simulator pinned to its own CPU |
| **H2. The harness sharing the host.** The harness process runs the tester (in a thread) and, in condition 4, three aiohttp readers that parse every event, and the stalled client's `/status` poll every 0.5 s. Nothing pins the harness or the simulator to CPUs. The wire interval runs from the request frame to the first reply frame, so the tester's own delays fall outside it. The harness can reach it only through the host (CPU placement, caches, frequency), or through `/status`, which the simulator serves on its loop. That is two or three polls per condition, too few to move a median of 5,000 | the slightly larger non-wire cycle time in condition 4 (derived above) | the readers in a separate process; the simulator, tester and readers pinned to separate CPUs (`taskset`) |
| **H3. Host power management.** Governor `powersave` on all 12 CPUs; load average 5.4–6.6 from other processes. If CPU frequency or wake-up latency differs with how busy the simulator is, wire latency moves with it | nothing specific; it cannot be ruled out on this host | a repeat with the `performance` governor. The plan keeps the governor as it is, so this needs the owner's approval |

### Gates and CI for this part

This part re-ran no gate, and it adds local vcan evidence only. "Gates", "Hosted CI" and
"Skips, by reason" below are as of `73e9e9f` and are not updated here. No hosted run
covers `7141045` or any later commit: they are not pushed (`origin/gui` is at
`e0c8445`).

## M4 forced 1013 closes: a proposal for the owner

**This is a proposal, not a decision.** This record does not change 0010, and 0010 is
the owner's to change.

### The problem, from measurement

- In all 12 condition 4 rounds that print the stalled ledger (three after fix round 1,
  nine rotated), the stalled connection took 4,952–4,954 of 5,000 messages as `sent`,
  with 46–48 `queued`, and never overflowed.
- Overflow begins only when the 1,024-message queue is full, after that absorption. The
  §4.3 close then comes on the first offer after 5 s of continuous overflow. While the
  buffers absorb, the writer keeps up and the queue stays short (46–48 when the harness
  stopped). It fills only once the writer's send stops returning.
- At condition 4's rotated-run rates (3,392.6–3,874.3 requests/s, one exchange event per
  request), the first forced close needs about (4,950 + 1,024) + 5 s × rate ≈
  **22,900–25,400 requests**, or 6.5–6.8 s. This is inside the 23,000–26,000 estimated
  above.
- M4's condition 4 runs 20,000 requests: 5.2–5.9 s at these rates. So **as specified,
  M4 condition 4 would see no forced close**. This assumes the M2 rates hold at 20,000
  requests, which is not measured.
- Where the ~4,950 messages sit was not measured. The stalled socket's receive buffer is
  already set to 4 KiB (`SO_RCVBUF 4096`). One exchange message is about 380 bytes (0010
  §5's example, compactly encoded), so ~4,950 of them are about 1.9 MB. Most of them
  therefore cannot be in the client's receive buffer. Presumably they are on the server's
  side of the loopback connection: its socket send buffer, and aiohttp's writer
  (`writer_limit` 64 KiB, `src/ecu_simulator/api/server.py`).
- At 380 bytes, 1,024 queued messages are about 0.4 MB. So the queue's count limit
  (`CLIENT_MAX_MESSAGES` 1024) binds long before its byte limit (`CLIENT_MAX_BYTES`
  4 MiB).
- M4 as specified still has condition 5 (60 s per round). At these rates it would be
  expected to reach forced closes. But nothing in 0010 requires one there, and condition
  5's rate has not been measured.

### (a) A smaller receive buffer for the stalled client

- **Condition 4 stays an honest latency condition:** yes. It is still a client that
  never reads, and any overflow would fall in the timed window.
- **What it proves about the 1013 path and P5(e):** little.
  - The buffer is already 4 KiB, and most of the absorption lies outside it (above).
  - Even with **no** absorption at all, the first close needs 1,024 messages + 5 s of
    overflow: about 18,000–20,400 requests (5.3 s) at these rates, against 20,000. So at
    best one close per round, and none at the faster rates.
  - It cannot make P5(e)'s 1013 reconciliation reliably non-trivial.
- **Cost and runtime:** none in runtime; a harness setting.
- **0010 text:** none for the buffer alone, which is a harness detail. To make it work,
  0010 would also have to change condition 4's 20,000 requests (*Runs*) or §4.3's 5 s
  (`OVERFLOW_DISCONNECT_S`, a product constant). Shrinking the server's send side instead
  would be a `src/` change. None of these is proposed: the first breaks the like-for-like
  comparison with conditions 1–3, and the others change the product to suit a test.

### (b) A dedicated forced-close sub-run, outside the timed conditions

- **Condition 4 stays an honest latency condition:** yes. Timed condition 4 stays exactly
  the condition measured at M2, "stalled, not overflowing", and 0010 would say so. Its
  latency does not mix rounds with a close and rounds without one.
- **What it proves:** on vcan, with the production limits (1,024 messages, 5 s) and the
  real simulator loop:
  - a stalled client is forced off with 1013, `forced_disconnects` counts it, and the
    harness reconnects;
  - P5(e) holds with non-zero counts: `close_codes["1013"]` = `forced_disconnects`, and
    reconnects = `forced_disconnects`;
  - P5(h)'s allowance holds on 1013 closes, and P6 holds for the three readers.
  - It does not test the latency cost of overflow and reconnect against P1–P2. It can
    report that latency, unjudged.
- **Cost and runtime:** harness code for a loop that sends requests until the k-th forced
  close has been reconnected, with a deadline. Each cycle is about 6.5–6.8 s at these
  rates (absorb, fill the queue, 5 s), plus up to 0.5 s for the harness to notice. For
  k = 3, that is about 20–22 s and 69,000–82,000 requests per round, or about 1 minute
  for three rounds.
- **0010 text:** the condition 4 row, *Runs* and P5(e). The wording is proposed below.
- **Variant:** require forced closes in condition 5 instead. This costs no extra runtime,
  but it ties the proof to a condition judged on throughput, whose rate is not yet
  measured.

### (c) Driving the stalled client to overflow before timing starts

- **Condition 4 stays an honest latency condition:** yes, if the warm-up is declared and
  kept out of the capture. But it becomes **a different condition** from the one measured
  at M2. The timed window would then hold:
  - a client that is already dropping;
  - one forced close, 5 s after overflow began;
  - the reconnect, and the new connection's initial history (up to 500 events).

  That is closer to 0010's present text for condition 4 ("disconnects … reconnects it at
  once, so a stalled client is present throughout"). But M4's condition 4 could then not
  be read directly against this M2 evidence, while the M2 STOP is unexplained.
- **What it proves:** the same 1013 path as (b), under timed load. But typically there is
  **only one close per round**:
  - after the reconnect, the new connection must absorb ~6,000 messages and overflow for
    5 s again (~6.5 s), longer than what remains of a 5.2–5.9 s window;
  - where the close falls depends on the warm-up's timing: 5 s after overflow began, less
    any time spent before timing started. So it is fragile.
- **Cost and runtime:**
  - an untimed warm-up of ~6,000 requests (1.5–1.8 s) per round, polling `/status` for
    `client_dropped` > 0;
  - a boundary in the capture between warm-up and timed requests;
  - a decision on whether conditions 1–3 get the same warm-up, so that the comparison
    stays like for like.
  - It adds about 2 s per round.
- **0010 text:** the condition 4 row (add the warm-up); *Measurement* (the warm-up
  requests are excluded from wire latency); *Runs* (whether conditions 1–3 get a matching
  warm-up).

### (d) Unit and integration tests only, and saying so in P5(e)

- **Condition 4 stays an honest latency condition:** yes, unchanged, and P5(e) would say
  plainly that conditions 1–4 do not exercise the 1013 path.
- **What it proves:** what these tests already prove, in ordinary CI. The `observe` tests
  run in every job; the `api` tests run in the `.[dev,gui]` job, over loopback:
  - `tests/unit/observe/test_connection.py`: 5 s of continuous overflow closes with 1013
    (a fake clock, `max_messages=1`), and the overflow timer resets when the queue
    drains;
  - `tests/unit/observe/test_writer.py`: a forced close during a blocked send resolves
    when the send returns;
  - `tests/unit/api/test_server_ws.py`: three tests with a real aiohttp server over
    loopback and a raw socket that never reads (`SO_RCVBUF 4096`), with
    `overflow_disconnect_s` 0.2 s (and `max_messages` 4 in two of them). They cover the
    forced 1013, the freed slot, `close_codes == {"1013": 1}`, the §5.1 identities, the
    P5(h) allowance, and a data frame during a forced close.

  They do **not** prove the path on vcan, with the production 5 s and 1,024 limits, or
  under CAN load on the shared loop. Nor do they prove the M4 harness's reconnect, or
  P5(e) with non-zero counts. No vcan integration test exercises a forced close.
- **Cost and runtime:** none new.
- **0010 text:** P5(e) gets a sentence saying where the 1013 path is proven. The
  condition 4 row gets its "disconnects … reconnects" sentence qualified. §9.3 already
  lists "a forced 1013 for a stalled client" among the `api` tests.

### Side by side

| Option | Condition 4 an honest latency condition? | Forced closes in M4 | 1013 proven on vcan with production limits? | Extra runtime per M4 run | 0010 text |
|---|---|---|---|---|---|
| (a) | yes | 0–1 per round at best | only if a close happens | none | none; to make it work, *Runs* or §4.3 (not proposed) |
| (b) | yes, unchanged from M2 | k per round, required | yes, outside the timed window | ~1 min for k = 3 | condition 4 row, *Runs*, P5(e) |
| (c) | yes, but a different condition from M2's | ~1 per round, timing-dependent | yes, inside the timed window | ~6 s | condition 4 row, *Runs*, *Measurement* |
| (d) | yes, unchanged | none | no: loopback, with injected limits | none | condition 4 row, P5(e) |

### Recommendation: (b) with (d)

**Awaiting the owner's decision (0010 change).**

- Keep timed condition 4 as it was measured at M2, and say so in 0010.
- Add a forced-close sub-run per round, outside the timed conditions. It must reach k
  forced closes, each reconnected; k = 3 is proposed.
- Name the existing unit and loopback tests in P5(e) as the proof of the close logic
  itself.

Why:

- It is the only option that **requires** forced closes on vcan with the production
  limits, and so makes P5(e)'s 1013 reconciliation non-trivial every round.
- It leaves the timed condition 4 comparable with the M2 evidence behind the present
  STOP.
- It costs about a minute per M4 run.
- (a) cannot produce closes reliably at 20,000 requests, whatever the buffer. (c) gives
  about one close, at a timing-dependent point, and changes condition 4 while its excess
  is unexplained. (d) alone leaves the harness's reconnect and P5(e)'s non-zero case
  unexercised on vcan.

This proposal is about proving the 1013 path. It does not address condition 4's latency
excess, which stays a `STOP`.

Proposed 0010 wording, for the owner to accept, edit or reject:

1. **§9.2, condition 4 row.** Now:

   > API on, 4 clients: 3 reading normally and 1 stalled (connected, never reading). The
   > §4.3 rule disconnects the stalled client after 5 s of overflow, and the harness
   > reconnects it at once, so a stalled client is present throughout. Each forced
   > disconnect is counted

   Proposed:

   > API on, 4 clients: 3 reading normally and 1 stalled (connected, never reading). At
   > 20,000 requests the stalled client is not expected to overflow: at M2 its connection
   > absorbed about 4,950 messages without overflowing
   > (`docs/validation/gui-m2-early-check.md`). If the §4.3 rule does disconnect it, the
   > harness reconnects it at once, and each forced disconnect is counted. The
   > forced-close path is exercised by the forced-close sub-run (*Runs*)

2. **§9.2, *Runs*.** Now:

   > *Runs.* Conditions 1–4: 20,000 requests each, repeated in **3 rounds**, with the
   > order of conditions rotated in each round. Condition 5: 60 s per round, 3 rounds.

   Proposed: the same, followed by:

   > **Forced-close sub-run**, once per round, after that round's timed conditions and
   > outside them: condition 4's clients and tester, run until the harness has reconnected
   > after the **3rd** forced disconnect, or for 60 s, whichever comes first. Its wire
   > latency is reported, not judged against P1 or P2. P3, P5 and P6 apply to it as they
   > do to condition 4.

3. **§9.2, P5(e).** Now it ends:

   > The harness also records each time it had to reconnect its stalled client; that
   > count must be ≤ `forced_disconnects`, and any shortfall is reported.

   Proposed: the same, followed by:

   > In the forced-close sub-run, `forced_disconnects` must be ≥ 3, and the harness's
   > reconnects must equal `forced_disconnects`; fewer is a failure, not a pass. The close
   > logic itself is also covered by the §9.3 `observe` and `api` tests, with shorter
   > injected limits.

Open details for the owner, if (b) is chosen:

- k (3 is proposed);
- the 60 s deadline;
- whether the sub-run runs once per round or once per M4 run;
- whether P9 (loop hold time, "conditions 2–5") also covers the sub-run.

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
host, in private namespaces. **They are local, not CI.** So did the three rotated runs
(2026-09-28, `a7431be`); they too are local, not CI.

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
