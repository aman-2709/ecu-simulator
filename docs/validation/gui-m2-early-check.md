# GUI M2 early check and verification record

**An early check on vcan, conditions 1, 2 and 4 only; not the M4 benchmark. P1–P9 are
judged at M4** ([decisions/0010 §9.2](../decisions/0010-gui-observer-api.md)). It has no
condition 3 or 5, no RSS soak, and no JSONL output. The diagnostic runs add two diagnostic
configurations, r1 and r3. r1 has condition 3's client set (one reader), but it is not
judged as condition 3. The first six runs had one round each,
with no rotation of condition order. The three rotated runs have three rounds each, with the
order rotated ("Rotated runs, 2026-09-28" below). The three diagnostic runs have five rounds
each, over five client configurations ("Client-cost diagnostic, 2026-09-28" below).

**Result: `STOP` in all twelve runs.**

- Six single-round runs, order fixed 1→2→4: three with the script at `cf0cb3a`, three
  after fix round 1. Condition 4's median wire latency exceeded condition 1's median +
  0.10 ms in every run, by 7–16 µs.
- Three runs of three rotated rounds, at `a7431be`: condition 4's median exceeded its own
  round's condition 1 median + 0.10 ms in 7 of the 9 rounds. The other two rounds are not
  a pass. The miss follows condition 4's load, not run order; its cause is not
  established.
- Three diagnostic runs of five rotated rounds, at `b6528b8`: condition 4's median
  exceeded its own round's condition 1 median + 0.10 ms in **15 of the 15 rounds**. Three
  reading clients alone (no stalled client) exceeded it in 3 of 15. The wire cost builds
  up with each client configuration. Most of the clients' cost (condition 2→4: +0.064 ms
  on the wire, +0.010 ms in dispatch) lies outside `dispatch_us`; the API's own
  +0.045 ms cannot be split. Its cause is still not established.

Every other early-check criterion held in all twelve runs: p99 within condition 1 +
0.50 ms, 0 lost replies, no P5 problem among those this script checks ((a), (b), (e), (f),
(h)); (c), (d), (g) and P6 are not checked at M2, and no `delivery_unknown` beyond the
P5(h) allowance. Per the stop rule this is reported before M3. No threshold was tuned, and no
condition was lengthened.

**Condition 4 here is "stalled, not overflowing, never forced off".** The stalled
connection dropped nothing (`client_dropped = 0`) and was never forced off
(`forced_disconnects = 0`), so the §4.3 overflow and 1013 path is **not** exercised by this
check. `forced_disconnects` is 0 in all twelve runs. `client_dropped` is 0 in all nine runs
that print the stalled ledger: three after fix round 1, the three rotated and the three
diagnostic. See "The condition 4 limitation" below, and "M4 forced 1013 closes: a proposal for
the owner". The owner has since decided: 0010's seventh revision adds a forced-close run
in every M4 round ([0010 §9.2](../decisions/0010-gui-observer-api.md), *Forced-close run*).

## Owner decisions (2026-09-28)

After the client-cost diagnostic, the owner decided the open questions. 0010's eighth
revision records the M4 parts ([0010 §9.2](../decisions/0010-gui-observer-api.md)); 0010
is the source for their terms.

- **The `STOP` stays open and is not accepted.** Condition 4's median still exceeds
  condition 1's median + 0.10 ms, and no threshold changes.
- **The next performance investigation is internal timestamps in the simulator:** per
  request, at CAN receive, dispatch start and end, and reply send (H1 in "The cause is
  still not established"). It does not block M3a visual work, which proceeds as an
  offline mockup only; wiring views to live API data waits for the owner's visual
  feedback.
- **M4's timed conditions run without segments.** Segmented runs, such as the three
  diagnostic runs below, are diagnostics only and never judge P1–P9.
- **The forced-close run is also judged on** zero lost diagnostic replies, as P3 defines
  a lost reply, and zero drops for its three reading clients: `client_dropped` = 0 and
  `discarded_on_close` = 0 on their connections, and `handoff_dropped` = 0 over the run,
  as P6 requires of condition 4's reading clients. The stalled client's
  `client_dropped`, `discarded_on_close` and `delivery_unknown` are reported as expected
  and not judged, but they must still reconcile under P5(b) and (f), and P5(h)'s
  allowance applies to its `delivery_unknown`. Its latency stays reported, not judged
  against P1 or P2.
- **The P3/P6 question is closed.** The proposal below applied P3, P5 and P6 to the
  forced-close run; the seventh revision judged it by P5 and P9 only. The decision above
  settles it: P3's lost-reply rule, and P6's rule for the reading clients
  (`handoff_dropped`, `client_dropped` and `discarded_on_close` all 0), now apply.

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
2026-09-28", and the diagnostic runs' in "Client-cost diagnostic, 2026-09-28".

### Commits

| Commit | What |
|---|---|
| `334ecf4` | the tree the first three runs used (product code) |
| `6a04ee5` | `test(observe): fixed clock where a real 1 ms turn budget could truncate a non-timing test`. It **fixed the flaky test** `test_encode_failure_publishes_a_fallback_and_seq_stays_contiguous` and three others, which now use `monotonic=lambda: 0.0`: a real 1 ms turn budget had truncated the turn under load. The diff from `334ecf4` is test-only (`tests/unit/observe/test_publisher.py`), so **the product code is identical and the first three runs' numbers stand** |
| `cf0cb3a` | the scripts and the first version of this record (script "at `cf0cb3a`") |
| `73e9e9f` | fix round 1 of the script: the stalled ledger, reconnects, readers and quiesce are reported. The second three runs and every gate below ran on this tree |
| `5f1f202`, `a7431be` | the harness hardened for rotated rounds (Task 10 and its review round 1): cleanup on every path, a refused stalled connect fails the run, `--rounds` and `--order`, `rate_rps`, dispatch latency, the host line and the summary. `src/` unchanged. The three rotated runs ran on `a7431be` |
| `c8fbe22` | the rotated runs' stdout, verbatim, in `gui-m2-early-check-runs/` |
| `6bbad2c` | the plan for the client-cost diagnostic, the loud harness failures and the 0010 forced-close revision |
| `7b5b7ea`, `b6528b8` | the harness for the diagnostic (Task 12 and its fix round 1): client configurations and `--conditions`, segments and pauses, the full-run dispatch harvest, loud failures, completeness checks. `src/` unchanged. The three diagnostic runs ran on `b6528b8` |
| `c1790bd` | the diagnostic runs' stdout, verbatim, and the committed `candump` captures |
| `ec0839a`, `c6f9a90` | 0010's seventh revision (the owner's decision): a forced-close run in every M4 round |
| `3328868` | the `candump` captures of diagnostic runs 2 and 3, xz-compressed, with sha256 |
| `2404a03`, `e5f3a8b` | the diagnostic's part of this record ("Client-cost diagnostic, 2026-09-28"), and its fix round 1: the dispatch claim scoped to 2→4, the per-client cost, order, outliers, and the captures of all three runs |

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

(Updated 2026-09-28: the owner has decided. See 0010 §9.2, *Forced-close run*. The
diagnostic runs give the same ledger again: see "Client-cost diagnostic, 2026-09-28".)

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
| Load | 1-minute load average 6.37, 6.63 and 5.43 at the start of runs 1, 2 and 3 (file headers). That is higher than the 1.33 recorded just after the first three runs. The operator reports it came from desktop background processes, with the CPUs about 80 % idle, from a `top` snapshot taken just before run 1; neither is recorded in the run files. Each 1-minute average after run 1 includes the preceding run |
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
`/tmp` directories each file names, from each file's "captures in" line: run 1 to
`/tmp/gui-m2-faqya9ej`, run 2 to `/tmp/gui-m2-heyjapm3`, run 3 to `/tmp/gui-m2-v83b5uai`.
**They are not committed**, they are about 4.3 MB per run (9 files, one per condition and
round), and they will not survive a reboot (`/tmp`). Keeping them, or capturing them again
under a committed path, is for the owner to decide. Their `sha256sum` at the time this
section was written:

```text
/tmp/gui-m2-faqya9ej (run 1):
9447e9c599a8f4d773d7a48d306e7cdebc9a6fb9984c0ba2d7473e4a5479a0dc  candump-0-00.log
404a062af56f23c72f1008d1c358315bac784f3d84f103cc61c985b51ba7e12c  candump-0-10.log
d535a96636a45f7c11a49761e5428c8100e909cf5f79be77c33d470b660ca9c5  candump-0-11.log
7936dfa0c704887ba25c934b188c377f9318b882a6cb78dff92e7f4f63953878  candump-1-00.log
7907059dda63e9f4757bf837b5c961189c3243a20af13bf4a9e64fd09d69ea00  candump-1-10.log
7759d8e455da90a9f6b13776333c377102706898026899491557b1bdb68c6792  candump-1-11.log
496e3f1a6b7aa72d1d386c87c0eb73f6336aea0cc7445e3f0e6068fcef371b8e  candump-2-00.log
b7a786328fecaab7188ef1ac5afc791e6830d90f75f4a0e70177fa72a57df0d2  candump-2-10.log
b288a6bd3d7f41d91306564a104d638fbfdae54316b677b71c4a18274d3b93b7  candump-2-11.log

/tmp/gui-m2-heyjapm3 (run 2):
0fa5edb35d8cd44c3806c4b7027f5f6b27e6ef6311c2e323c20d540c77862b37  candump-0-00.log
c6c0d8590214435f9740127d0bd7265432b759deb2e948f0541f6876ceaf08ab  candump-0-10.log
fd0e4095380e09e033160db99e7b007fe3368326c387db49efc0c05dd3198c93  candump-0-11.log
31497f65c3bfb5e1f8c6e6910c2762ec432e39d4ece249409af03c7437897df6  candump-1-00.log
4530d09437d67087ab04d1fa48550f9b3a441fc4315cfa044c8073f0ffefc0f4  candump-1-10.log
aa4a5c0c10bb9673619ffe8458f68a6e9ae7d367d57ef8b34af3f25a155a7d9d  candump-1-11.log
00b222a4335a296b10e73ce919fe390803b8a355bc8eb974e636b6e470ed7b24  candump-2-00.log
e8a72d86c164f96a24b67ecb976ae7570a32d2b7648f5025aada7f99976780db  candump-2-10.log
a6a37949596bba47dbe6fd7032788e90bca74c82b1ca6a034d2b61125b40648d  candump-2-11.log

/tmp/gui-m2-v83b5uai (run 3):
c9e3d70a4f6e8e306de4595fede368ff6f4b98f8152df822aa5e73d579be455e  candump-0-00.log
0f7871365721e59bc16b4611f36b92dc876c7381a5401c5da852be9637a3acf5  candump-0-10.log
53ac3c5e73ddf9a419f56bbb7896cf04bd0ade6209060fa702289a1c4fc53001  candump-0-11.log
17ce3a7b614e190d2868c434c8c9c9798bb14aa558c0bb423aa546f8a397e018  candump-1-00.log
1db6103f28c3dff24311768ceafeb4b33b898be55ad30272ace86222bc8c85a1  candump-1-10.log
2135e989075848724fc11aac3891429ce989ad2704592feae690a9ddc1f60c0f  candump-1-11.log
5b18eddd576167f17a555a4d3efff24592c2bfa3efd852801fd2f1fe46ff69ed  candump-2-00.log
27faf80790dbeed2e7fbc7cdf2ce207c088b28ba623810854e29931951a358e4  candump-2-10.log
b27a1ec31c2f8d15781b6444c6d41cb8441ea435b110b3c7d37cb5ba98eeaec8  candump-2-11.log
```

(Updated 2026-09-28: run 1's nine logs are now committed, xz-compressed, in
`gui-m2-early-check-captures/rotated-run-1/`. Their uncompressed hashes match the run 1
block above. Runs 2 and 3 are not committed. See "Captures" under "Client-cost diagnostic,
2026-09-28".)

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

- **The load test (per position, plan wording): above +0.10 ms, or near it, in every
  position.** Condition 4's per-position mean excess is +0.096 to +0.117 ms — above
  +0.10 ms at two of the three positions, and close to it at the third. Round by round,
  the excess is above +0.10 ms in 7 of the 9 rounds. The other two are +0.065 (run 1
  round 0, position 3) and +0.092 (run 3 round 1, position 2). In both, condition 1 had
  the two highest medians of the nine (0.159 and 0.143 ms, against 0.117–0.127 in the
  other seven). Condition 4 in those rounds was 0.224 ms (the lowest of the nine, but
  only 0.005 ms below the next) and 0.235 ms (mid-range). So those two rounds are within
  the limit mainly because condition 1 was slow, not because condition 4 was fast. None
  of this is a pass: the stop rule applies per round, and two of the nine miss it.
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
| **H3. Host power management.** Governor `powersave` on all 12 CPUs; load average 5.4–6.6 at each run's start, source not recorded; it includes the preceding run (the runs started 24 s apart). If CPU frequency or wake-up latency differs with how busy the simulator is, wire latency moves with it | nothing specific; it cannot be ruled out on this host | a repeat with the `performance` governor. The plan keeps the governor as it is, so this needs the owner's approval |

(Added 2026-09-28: the diagnostic runs did the first part of H1's test, one reader and
three readers without the stalled client beside condition 4. The excess scales with
readers, which H1 and H2 both predict. See "Client-cost diagnostic, 2026-09-28".)

### Gates and CI for this part

This part adds local vcan evidence only. "Gates" gives the gates at `b27cf60` (local,
unpushed), with `73e9e9f` kept as history. "Skips, by reason" is as of `73e9e9f`, with the
expected change noted. "Hosted CI" adds run 36363466270 at `e0c8445`. **No hosted run
covers `7141045` or any later commit**: they are not pushed (`origin/gui` is at
`e0c8445`).

(Updated 2026-09-28: `7141045` to `70c4e9f` are now pushed, and hosted run 36436675557
at `70c4e9f` covers them, including `a7431be`. At that update, no hosted run covered
`6bbad2c` or any later commit. See "Hosted CI".)

## Client-cost diagnostic, 2026-09-28: three runs of five rounds, five client configurations

This part asks where condition 4's excess comes from. It splits condition 4 into its
parts: the API with no clients, one reader, three readers, and three readers with the
stalled client. It adds **local vcan evidence only**.

**Result: `STOP` in all three runs** (exit status 1). Condition 4's median exceeded its
own round's condition 1 median + 0.10 ms in **15 of the 15 rounds**. Three readers without
the stalled client exceeded it in 3 of the 15. A configuration that printed no stop reason
in a round is **not a pass**: the stop rule applies to every round. No threshold was tuned, and no
condition was lengthened. Per the stop rule, this is reported before M3.

**What it finds:** the wire cost builds up with each client configuration (the API, the
first reader, two more readers, the stalled client). Most of the clients' cost (condition
2→4: +0.064 ms on the wire, +0.010 ms in dispatch) lies outside `dispatch_us`; the API's
own +0.045 ms cannot be split. It shows where the time is not. It does not show where the
time is.

### Environment

| | |
|---|---|
| Commit | `b6528b877e22866d3d3d9eb023d67b7077886ca0`, named in each run file's header. `src/` is identical to `334ecf4` (`git diff 334ecf4 b6528b8 -- src` is empty): **the product code is unchanged since the first six runs; the harness changed** |
| Date | 2026-09-28; the runs started at 08:17:19, 08:18:58 and 08:20:36 local (UTC−7) |
| CPUs | 12 (`nproc`: the scheduler affinity count), governor `powersave` on all 12, from the `host:` line each run prints |
| Kernel | `6.8.0-138-generic`, from the same line |
| Load | 1-minute load average 1.25, 1.94 and 2.51 at the start of runs 1, 2 and 3 (5-minute: 1.11, 1.35, 1.67), from the file headers. Each average after run 1 includes the preceding run. The operator reports the CPUs about **92 % idle** in a `top` snapshot taken just before run 1; that is not in the run files. The rotated runs started at a load average of 5.4–6.6 |
| Python, aiohttp | the worktree `.venv`: Python 3.12.12, aiohttp 3.14.3, as checked when this section was written. The run files do not record them |
| Namespace | as before: `unshare -r -n`, `lo` up, a private `vcan0` |

### Method, as changed at `7b5b7ea` and `b6528b8`

- Command, three times:
  `scripts/run_gui_m2_early_check.sh --rounds 5 --conditions 1,2,r1,r3,4 --captures <dir>`.
  5,000 requests per configuration (the default `-n`). `--order` was not given.
- **Client configurations** (`--conditions`), each with its own `candump` and its own
  simulator process, as before:

  | Label | API | Reading clients | Stalled client | What it is |
  |---|---|---|---|---|
  | 1 | off | — | — | condition 1, unchanged |
  | 2 | on | 0 | no | condition 2, unchanged |
  | r1 | on | 1 | no | one reader; the client set of 0010's condition 3 |
  | r3 | on | 3 | no | condition 4 without its stalled client |
  | 4 | on | 3 | yes | condition 4, unchanged: 3 readers, then the stalled raw socket (4 KiB receive buffer, never read) |

- **Rotation.** Round r runs rotation r of (1, 2, r1, r3, 4). So each run puts each
  configuration in each of the five positions once, and the three runs give **three
  samples per (configuration, position)**. As before, a configuration's position is fixed
  by the round index, so a round effect cannot be told from a position effect.
- **Segments and pause.** The 5,000 requests go out in **20 segments of 250**. After every
  segment the harness pauses for a fixed **50 ms**, in **every** configuration, condition
  1 included (condition 1 only sleeps). No request is in flight during a pause. The plan
  said 10 segments of 500; Task 12's fix round capped the segment at 250 (`b6528b8`),
  because 500 equals the 500-event history and leaves no margin.
- **Dispatch, the whole run.** In every API configuration, each pause makes one
  `GET /api/v1/exchanges?after=<last seq>&limit=500` and keeps each event's `dispatch_us`.
  After the last segment and a 1 s drain, one more fetch takes the tail. So
  `dispatch_all_ms` covers **every** exchange (n = 5,000), not the last 500 as in the
  rotated runs. `dispatch_reader0_ms` is kept as a cross-check: in all 45 reader rows it is
  identical to `dispatch_all_ms`. `dispatch_last500_ms` was dropped in the diagnostic
  runs, in favour of `dispatch_all_ms`. `dispatch_us` is dispatcher time only, not wire
  latency (0010 §5).
- **Rate and seconds** count the tester's time inside segments only, not the pauses.
- **Wire latency**, the pairing and the lost-reply rule are unchanged. They cover all
  5,000 requests, including the 19 that follow a pause in each configuration (see "The
  first request after a pause").
- **Limits, unchanged:** each round is judged against its own condition 1. Median ≤
  condition 1 + 0.10 ms, p99 ≤ condition 1 + 0.50 ms, lost = 0, and no P5 problem.
- **Loud failures.** At `7b5b7ea`:
  - `--rounds 0` is a usage error (exit 2), not a run that does nothing and prints
    "within";
  - if the readers never all appear in `/status`, the run raises;
  - a reader that ends any way other than `stopped` is a P5 problem, so a `STOP`.

  At `b6528b8`:
  - a bad `--conditions` or `--order` is a usage error (exit 2); `--conditions` must
    include condition 1, with no label twice;
  - `--captures` refuses a directory that already holds `candump-*.log` files.
- **Completeness checks.** Each is a P5 problem, so a `STOP`, unless noted:
  - each harvest must have no `gap` and consecutive `seq`s (`7b5b7ea`);
  - at the end, the last harvested `seq` and the number of `dispatch_us` values must
    both equal `issued_seq` (`b6528b8`);
  - each reader's received `seq`s must be exactly 1 … `issued_seq`, in order, with no
    gap and no duplicate (`b6528b8`; at `7b5b7ea` only the count was checked);
  - an event without `dispatch_us` (a fallback event) stops the run with an error
    (`b6528b8`).
- **New lines in the output:** per round, an `excess` line per configuration (median and
  p99 excess over condition 1, the verdict, pause overruns) and an `incremental` line per
  step (1→2, 2→r1, r1→r3, r3→4: wire median and p99 and, where both sides have an API,
  dispatch median and p99). At the end, per-position `summary` lines, `incremental
  summary` lines over the run's five rounds, and a `pause overruns` line per
  configuration. The 15-round figures below are computed from the JSON lines of all three
  files.
- **Capture names changed.** The logs are now `candump-<round>-<api><readers><stalled>.log`:
  `000` = 1, `100` = 2, `110` = r1, `130` = r3, `131` = 4. At `a7431be` they were
  `candump-<round>-<api><clients>.log`: `00` = 1, `10` = 2, `11` = 4. The rotated runs'
  hash table above uses the old names.

### The runs

The stdout of each run is committed verbatim (`c1790bd`):
[diag-run-1.txt](gui-m2-early-check-runs/diag-run-1.txt),
[diag-run-2.txt](gui-m2-early-check-runs/diag-run-2.txt),
[diag-run-3.txt](gui-m2-early-check-runs/diag-run-3.txt). Each file starts with two `#`
lines (run, commit, start time, load average; the command) and ends with `# exit status: 1`.
Each printed `pause_s: 0.05` and `segment_size: 250, segments: 20`, and ended with
`STOP: report before M3`.

Every configuration in every round, as printed. The verdict is the one in each `excess`
line. "within limits" is the script's word for a configuration that printed no stop
reason in that round; it is not a pass. Bold: the 18 medians that printed a stop reason.
Condition 1 has no API, so no dispatch figure.

| Run | Round | Pos. | Cond. | Wire median (ms) | Wire p99 (ms) | Dispatch median (ms) | Dispatch p99 (ms) | Rate (req/s) | Verdict, as printed |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 0 | 1 | 1 | 0.117 | 0.238 | — | — | 6,597.1 | within limits |
| 1 | 0 | 2 | 2 | 0.166 | 0.322 | 0.109 | 0.229 | 5,054.6 | within limits |
| 1 | 0 | 3 | r1 | 0.195 | 0.291 | 0.112 | 0.196 | 4,526.0 | within limits |
| 1 | 0 | 4 | r3 | **0.221** | 0.354 | 0.118 | 0.216 | 3,868.8 | `STOP` |
| 1 | 0 | 5 | 4 | **0.231** | 0.459 | 0.119 | 0.263 | 3,525.6 | `STOP` |
| 1 | 1 | 1 | 2 | 0.161 | 0.264 | 0.107 | 0.184 | 5,268.4 | within limits |
| 1 | 1 | 2 | r1 | 0.200 | 0.320 | 0.119 | 0.224 | 4,230.6 | within limits |
| 1 | 1 | 3 | r3 | 0.211 | 0.340 | 0.115 | 0.199 | 4,140.1 | within limits |
| 1 | 1 | 4 | 4 | **0.230** | 0.372 | 0.120 | 0.208 | 3,713.3 | `STOP` |
| 1 | 1 | 5 | 1 | 0.122 | 0.242 | — | — | 6,278.5 | within limits |
| 1 | 2 | 1 | r1 | 0.194 | 0.562 | 0.112 | 0.346 | 4,118.3 | within limits |
| 1 | 2 | 2 | r3 | 0.213 | 0.345 | 0.116 | 0.198 | 3,991.5 | within limits |
| 1 | 2 | 3 | 4 | **0.225** | 0.354 | 0.119 | 0.193 | 3,867.5 | `STOP` |
| 1 | 2 | 4 | 1 | 0.118 | 0.241 | — | — | 6,540.2 | within limits |
| 1 | 2 | 5 | 2 | 0.166 | 0.294 | 0.108 | 0.222 | 5,049.8 | within limits |
| 1 | 3 | 1 | r3 | 0.217 | 0.355 | 0.117 | 0.208 | 3,943.1 | within limits |
| 1 | 3 | 2 | 4 | **0.236** | 0.376 | 0.120 | 0.195 | 3,740.6 | `STOP` |
| 1 | 3 | 3 | 1 | 0.123 | 0.238 | — | — | 6,405.1 | within limits |
| 1 | 3 | 4 | 2 | 0.168 | 0.273 | 0.110 | 0.210 | 5,031.7 | within limits |
| 1 | 3 | 5 | r1 | 0.196 | 0.306 | 0.112 | 0.197 | 4,503.3 | within limits |
| 1 | 4 | 1 | 4 | **0.236** | 0.466 | 0.124 | 0.262 | 3,480.9 | `STOP` |
| 1 | 4 | 2 | 1 | 0.121 | 0.241 | — | — | 6,422.7 | within limits |
| 1 | 4 | 3 | 2 | 0.164 | 0.280 | 0.108 | 0.203 | 5,120.3 | within limits |
| 1 | 4 | 4 | r1 | 0.201 | 0.334 | 0.120 | 0.226 | 4,225.5 | within limits |
| 1 | 4 | 5 | r3 | 0.221 | 0.356 | 0.120 | 0.229 | 3,821.7 | within limits |
| 2 | 0 | 1 | 1 | 0.121 | 0.285 | — | — | 5,773.6 | within limits |
| 2 | 0 | 2 | 2 | 0.170 | 0.261 | 0.111 | 0.186 | 5,011.0 | within limits |
| 2 | 0 | 3 | r1 | 0.189 | 0.312 | 0.110 | 0.199 | 4,657.9 | within limits |
| 2 | 0 | 4 | r3 | 0.218 | 0.358 | 0.116 | 0.207 | 3,983.3 | within limits |
| 2 | 0 | 5 | 4 | **0.227** | 0.355 | 0.118 | 0.204 | 3,813.4 | `STOP` |
| 2 | 1 | 1 | 2 | 0.162 | 0.263 | 0.107 | 0.200 | 5,193.8 | within limits |
| 2 | 1 | 2 | r1 | 0.190 | 0.377 | 0.112 | 0.246 | 4,419.6 | within limits |
| 2 | 1 | 3 | r3 | 0.211 | 0.325 | 0.114 | 0.189 | 4,139.3 | within limits |
| 2 | 1 | 4 | 4 | **0.234** | 0.449 | 0.120 | 0.256 | 3,474.1 | `STOP` |
| 2 | 1 | 5 | 1 | 0.122 | 0.234 | — | — | 6,417.4 | within limits |
| 2 | 2 | 1 | r1 | 0.195 | 0.314 | 0.112 | 0.203 | 4,431.6 | within limits |
| 2 | 2 | 2 | r3 | 0.220 | 0.349 | 0.117 | 0.219 | 3,912.2 | within limits |
| 2 | 2 | 3 | 4 | **0.228** | 0.376 | 0.118 | 0.216 | 3,683.6 | `STOP` |
| 2 | 2 | 4 | 1 | 0.121 | 0.252 | — | — | 6,240.7 | within limits |
| 2 | 2 | 5 | 2 | 0.169 | 0.282 | 0.110 | 0.210 | 5,045.9 | within limits |
| 2 | 3 | 1 | r3 | 0.221 | 0.406 | 0.118 | 0.242 | 3,665.4 | within limits |
| 2 | 3 | 2 | 4 | **0.231** | 0.356 | 0.119 | 0.195 | 3,806.7 | `STOP` |
| 2 | 3 | 3 | 1 | 0.123 | 0.246 | — | — | 6,377.9 | within limits |
| 2 | 3 | 4 | 2 | 0.165 | 0.276 | 0.109 | 0.196 | 5,134.9 | within limits |
| 2 | 3 | 5 | r1 | 0.187 | 0.281 | 0.110 | 0.184 | 4,729.9 | within limits |
| 2 | 4 | 1 | 4 | **0.222** | 0.356 | 0.118 | 0.197 | 3,922.8 | `STOP` |
| 2 | 4 | 2 | 1 | 0.117 | 0.229 | — | — | 6,609.5 | within limits |
| 2 | 4 | 3 | 2 | 0.164 | 0.276 | 0.109 | 0.198 | 5,130.6 | within limits |
| 2 | 4 | 4 | r1 | 0.185 | 0.291 | 0.109 | 0.182 | 4,693.7 | within limits |
| 2 | 4 | 5 | r3 | **0.223** | 0.371 | 0.118 | 0.216 | 3,813.4 | `STOP` |
| 3 | 0 | 1 | 1 | 0.119 | 0.223 | — | — | 6,444.7 | within limits |
| 3 | 0 | 2 | 2 | 0.168 | 0.271 | 0.109 | 0.213 | 5,068.1 | within limits |
| 3 | 0 | 3 | r1 | 0.191 | 0.296 | 0.112 | 0.206 | 4,527.5 | within limits |
| 3 | 0 | 4 | r3 | 0.206 | 0.317 | 0.114 | 0.180 | 4,210.4 | within limits |
| 3 | 0 | 5 | 4 | **0.222** | 0.361 | 0.117 | 0.194 | 3,911.7 | `STOP` |
| 3 | 1 | 1 | 2 | 0.164 | 0.296 | 0.108 | 0.219 | 5,081.5 | within limits |
| 3 | 1 | 2 | r1 | 0.194 | 0.680 | 0.114 | 0.269 | 3,922.2 | within limits |
| 3 | 1 | 3 | r3 | 0.213 | 0.331 | 0.116 | 0.196 | 4,043.2 | within limits |
| 3 | 1 | 4 | 4 | **0.226** | 0.373 | 0.118 | 0.199 | 3,793.2 | `STOP` |
| 3 | 1 | 5 | 1 | 0.118 | 0.246 | — | — | 6,478.2 | within limits |
| 3 | 2 | 1 | r1 | 0.192 | 0.309 | 0.112 | 0.207 | 4,489.8 | within limits |
| 3 | 2 | 2 | r3 | 0.217 | 0.344 | 0.116 | 0.193 | 4,023.5 | within limits |
| 3 | 2 | 3 | 4 | **0.224** | 0.363 | 0.117 | 0.201 | 3,873.6 | `STOP` |
| 3 | 2 | 4 | 1 | 0.119 | 0.236 | — | — | 6,548.8 | within limits |
| 3 | 2 | 5 | 2 | 0.161 | 0.266 | 0.107 | 0.192 | 5,219.7 | within limits |
| 3 | 3 | 1 | r3 | **0.228** | 0.358 | 0.120 | 0.234 | 3,744.6 | `STOP` |
| 3 | 3 | 2 | 4 | **0.229** | 0.359 | 0.118 | 0.210 | 3,794.6 | `STOP` |
| 3 | 3 | 3 | 1 | 0.116 | 0.231 | — | — | 6,704.5 | within limits |
| 3 | 3 | 4 | 2 | 0.162 | 0.265 | 0.107 | 0.191 | 5,184.9 | within limits |
| 3 | 3 | 5 | r1 | 0.196 | 0.327 | 0.113 | 0.221 | 4,421.2 | within limits |
| 3 | 4 | 1 | 4 | **0.231** | 0.365 | 0.118 | 0.204 | 3,711.5 | `STOP` |
| 3 | 4 | 2 | 1 | 0.120 | 0.270 | — | — | 5,978.4 | within limits |
| 3 | 4 | 3 | 2 | 0.166 | 0.331 | 0.109 | 0.237 | 4,953.2 | within limits |
| 3 | 4 | 4 | r1 | 0.189 | 0.313 | 0.110 | 0.204 | 4,720.7 | within limits |
| 3 | 4 | 5 | r3 | 0.211 | 0.332 | 0.115 | 0.189 | 4,071.5 | within limits |

The 18 stop reasons are all "median … > condition 1's … + 0.1": condition 4 in all 15
rounds, and r3 in run 1 round 0 (position 4), run 2 round 4 (position 5) and run 3 round
3 (position 1).

In all 75 rows (15 rounds × 5 configurations):

- Lost replies: 0; 5,000 of 5,000 paired; 0 tester timeouts.
- `p5_problems` and `inconclusive`: empty in every API configuration. So no harvest gap,
  no harvest tail short of `issued_seq`, and no reader sequence other than 1 … 5,000.
- `dispatch_all_ms` has n = 5,000 in all 60 API rows. `reader_seq_ok` is true, and every
  reader received 5,000 exchanges and ended `stopped`.
- Pause overruns: 0 in every configuration of every run (the `pause overruns` lines).
- Allowed `delivery_unknown` (P5(h)), reported explicitly: `{}` for 2, r1 and r3;
  `{"1006": 1}` for condition 4 in every round, from the stalled socket that the harness
  closed at the end of the condition. That is within the allowance of one per 1006 close.
- `forced_disconnects` = 0 and harness `reconnects` = 0 in every condition 4. The stalled
  connection, just before the harness stopped it: `client_dropped` 0, `queued` 49–51,
  `sent` 4,949–4,951 of 5,000 `enqueued`; at quiesce, closed 1006 with `queued` 0 and
  `delivery_unknown` 1. As before, **it never overflowed**, and it was never forced off.
- p99: within condition 1 + 0.50 ms in every row. The largest p99 excess is r1's
  +0.434 ms (run 3 round 1, p99 0.680 ms), 0.066 ms from the limit. Its capture shows a
  transient burst: 7 requests above 2 ms, between requests 525 and 809. A similar burst
  of 9 (requests 4,765–4,978, run 2 round 3) gave r3's largest p99, 0.406 ms. So the near
  miss is transient and not specific to r1. **For M4:** a burst like this, at 20,000
  requests per condition, could decide a p99 verdict; it is noted, not explained.

Each median and p99 above was recomputed from the `candump` captures with the script's own
pairing rule, and all 75 match. All three runs' captures are committed (see "Captures"),
so this, and the first-after-pause check below, can be reproduced from the repository.

### Cost of each configuration, over the 15 rounds

Each configuration's excess over its own round's condition 1 (ms):

| Config. | Wire median, range | Median excess, mean (range) | p99 excess, mean (range) | Stop reasons | Dispatch median, range | Dispatch p99, range | Rate (req/s), range |
|---|---|---|---|---|---|---|---|
| 1 | 0.116–0.123 | — | — | 0 of 15 | — | — | 5,773.6–6,704.5 |
| 2 | 0.161–0.170 | +0.045 (+0.039 to +0.049) | +0.038 (−0.024 to +0.084) | 0 of 15 | 0.107–0.111 | 0.184–0.237 | 4,953.2–5,268.4 |
| r1 | 0.185–0.201 | +0.073 (+0.064 to +0.080) | +0.111 (+0.027 to +0.434) | 0 of 15 | 0.109–0.120 | 0.182–0.346 | 3,922.2–4,729.9 |
| r3 | 0.206–0.228 | +0.097 (+0.087 to +0.112) | +0.106 (+0.062 to +0.160) | **3 of 15** | 0.114–0.120 | 0.180–0.242 | 3,665.4–4,210.4 |
| 4 | 0.222–0.236 | +0.109 (+0.103 to +0.115) | +0.139 (+0.070 to +0.225) | **15 of 15** | 0.117–0.124 | 0.193–0.263 | 3,474.1–3,922.8 |

The incremental cost of each step, from two configurations of the same round (ms). A
"positive" count is the number of rounds in which the step raised the wire median:

| Step | What it adds | Wire median, mean (range) | Positive | Wire p99, mean (range) | Dispatch median, mean (range) | Dispatch p99, mean (range) |
|---|---|---|---|---|---|---|
| 1→2 | the API itself, no clients | +0.045 (+0.039 to +0.049) | 15 of 15 | +0.038 (−0.024 to +0.084) | — | — |
| 2→r1 | the first reader | +0.028 (+0.019 to +0.039) | 15 of 15 | +0.073 (−0.031 to +0.384) | +0.004 (−0.001 to +0.012) | +0.015 (−0.033 to +0.124) |
| r1→r3 | two more readers | +0.024 (+0.011 to +0.038) | 15 of 15 | −0.005 (−0.349 to +0.125) | +0.004 (−0.004 to +0.009) | −0.013 (−0.148 to +0.058) |
| r3→4 | the stalled client | +0.012 (−0.001 to +0.023) | 14 of 15 | +0.033 (−0.050 to +0.124) | +0.002 (−0.002 to +0.006) | +0.005 (−0.047 to +0.067) |
| 2→4 | all four clients, for comparison | +0.064 (+0.054 to +0.072) | 15 of 15 | +0.101 (+0.034 to +0.186) | +0.010 (+0.007 to +0.016) | +0.007 (−0.033 to +0.059) |

- Each run file's `incremental summary` lines cover that run's five rounds only. The
  figures above cover all 15.
- The one negative r3→4 step is run 2 round 4: r3 0.223 ms, condition 4 0.222 ms.
- The p99 steps swing both ways, by up to 0.38 ms. A p99 over 5,000 requests is the
  4,951st sorted value, so a few dozen slow requests move it. r1's two high p99s (0.562
  and 0.680 ms) set the 2→r1 and r1→r3 extremes. The p99 steps show no consistent cost.

### What the data say

- **The cost builds up across configurations.** The first three steps raised the wire
  median in all 15 rounds, and the stalled client in 14. The step means (+0.045, +0.028, +0.024, +0.012 ms) add up to
  condition 4's mean excess (+0.109 ms). That sum holds by construction, since the steps
  chain within each round; the finding is that every part adds, and none carries the
  excess alone. The API with no clients adds the most.
- **Per client, the cost is about equal, except the first reader.** r1→r3 adds two
  clients, so per client: the first reader +0.028 ms, readers 2 and 3 about +0.012 ms
  each, and the stalled client +0.012 ms. The stalled step is above half the r1→r3 step
  in 8 rounds and below it in 7. What stays unexplained is the first reader's premium: it
  costs more than the next two readers together in 12 of 15 rounds, by 0.004 ms on
  average. Equal per-client cost fits H1, and weighs only mildly against H2 alone,
  because the stalled raw socket does no parsing on the harness side. That is not a
  test.
- **Dispatch barely moves.** From condition 2 to condition 4, the full-run dispatch
  median rises by +0.010 ms (mean; +0.007 to +0.016), while the wire median rises by
  +0.064 ms (+0.054 to +0.072). Over the three client steps (2→r1, r1→r3, r3→4), the
  dispatch median moves +0.004, +0.004 and +0.002 ms on average, against +0.028, +0.024
  and +0.012 ms on the wire. So **most of the wire cost of the clients lies
  outside the `dispatch_us` window**. This now covers all 5,000 exchanges of each
  configuration, and agrees with the rotated runs' last-500 comparison. It is a statement
  about medians, not a per-request breakdown. It says nothing about the 1→2 step, since
  condition 1 has no dispatch figure.
- **Three readers alone can miss the limit.** r3 printed a stop reason in 3 of 15 rounds
  (excess +0.104, +0.106 and +0.112 ms), and its mean excess, +0.097 ms, is just under
  the limit. The stalled client is not needed for a miss.
- **No order effect at condition 4** (medians by position below). Its mean median by
  position spans 0.226–0.232 ms, 0.006 ms, against its 15-round range of 0.222–0.236 ms.
  Smaller position spreads, up to 0.010 ms for r3, cannot be resolved with three samples
  and the round/position confound: r3's position means span 0.212–0.222 ms, and all three
  r3 samples at position 1 (0.217, 0.221, 0.228) exceed all three at position 3 (0.211,
  0.211, 0.213). In 14 of the 15 rounds the medians rank 1 < 2 < r1 < r3 < 4, whatever the
  order; the other is the r3→4 round above.
- **The pause brings no configuration-specific bias at the median** ("The first request
  after a pause" below).
- **Condition 1 was steadier than in the rotated runs:** its median spans 0.116–0.123 ms
  over 15 rounds, against 0.117–0.159 ms over 9. Condition 4's excess spans +0.103 to
  +0.115 ms, against +0.065 to +0.139 ms. The load average was also lower (1.1–2.5 against
  5.4–6.6), and the harness differs (segments, pauses, the harvest). The data do not say
  which of these made the difference.

Mean wire median by position (ms), three samples each (one per run):

| Config. | Position 1 | Position 2 | Position 3 | Position 4 | Position 5 |
|---|---|---|---|---|---|
| 1 | 0.119 | 0.119 | 0.121 | 0.119 | 0.121 |
| 2 | 0.162 | 0.168 | 0.165 | 0.165 | 0.165 |
| r1 | 0.194 | 0.195 | 0.192 | 0.192 | 0.193 |
| r3 | 0.222 | 0.217 | 0.212 | 0.215 | 0.218 |
| 4 | 0.230 | 0.232 | 0.226 | 0.230 | 0.227 |

Condition 4's three values per position: 0.236, 0.222, 0.231 (1); 0.236, 0.231, 0.229
(2); 0.225, 0.228, 0.224 (3); 0.230, 0.234, 0.226 (4); 0.231, 0.227, 0.222 (5).

### The first request after a pause

The Task 12 review asked for a check of the request that follows each 50 ms pause. It
could be slow (a cold path, a CPU waking), and that could differ by configuration.
Checked from the committed captures of all three runs, with the script's pairing rule: the first
request of segments 2–20 is request 251, 501, … 4,751 of each configuration, 19 per
configuration and round, **285 per configuration**.

| Config. | First-after-pause median (ms) | Pooled median, all / without them (ms) | Pooled p99, all / without them (ms) |
|---|---|---|---|
| 1 | 0.328 | 0.119 / 0.119 | 0.245 / 0.235 |
| 2 | 0.341 | 0.165 / 0.165 | 0.286 / 0.271 |
| r1 | 0.341 | 0.192 / 0.192 | 0.351 / 0.337 |
| r3 | 0.343 | 0.216 / 0.216 | 0.351 / 0.346 |
| 4 | 0.342 | 0.229 / 0.229 | 0.387 / 0.385 |

- The request after a pause is slow in **every** configuration, condition 1 included:
  its median is 0.328–0.343 ms, against pooled medians of 0.119–0.229 ms.
- Leaving those requests out changes no pooled median, and changes each round's median by
  at most 0.001 ms. So **at the median, the pause adds no configuration-specific bias**.
  Judged without them, every round of every configuration gets the same verdict.
- They raise the pooled p99 by 0.002–0.015 ms in each configuration, condition 1 by
  0.010 ms. That is small beside the 0.50 ms p99 limit, and it enters both sides of the
  comparison.
- Two first-after-pause requests took 9.04 ms (condition 1) and 9.06 ms (condition 4).
  They are not tied to the pause: the 75 captures hold 11 requests of 7.88–9.06 ms, in
  every configuration (condition 1: 1, condition 2: 2, r1: 3, r3: 1, condition 4: 4), and
  9 of them are mid-segment. Run 1's four are all mid-segment: condition 4 requests 894
  and 3,316, condition 2 request 4,427 and r1 request 2,248. All are within the 1 s
  lost-reply limit. The data do not say why.
- Pooled figures mix 15 rounds; the stop rule judges each round alone.

### The cause is still not established

The diagnostic shows where the clients' time is not: inside the dispatcher call. It does
not show where it is. Each hypothesis from the rotated runs is still consistent with the
data, and none has been tested directly. They are not exclusive.

| Hypothesis | What the diagnostic adds | What would isolate it |
|---|---|---|
| **H1. Work on the simulator's loop.** Every client adds a queue offer, a writer task and loopback TCP sends to the simulator's one asyncio loop, which also runs the CAN read callback and the reply's `_send` (0010 §4.2). A request that arrives while that work runs waits before dispatch starts, or its reply waits to be sent | consistent: the cost grows from one reader to three, and lies mostly outside `dispatch_us`. Per client, the stalled client (whose writer sends 4,949–4,951 of 5,000 messages) costs about what readers 2 and 3 cost, which fits. The first reader's premium (+0.004 ms on average over the next two together) is not explained | timestamps inside the simulator, per request, at CAN receive, dispatch start and end, and reply send, written to a trace outside the timed path. They would split the wire interval into waiting before dispatch, dispatch, waiting after it, and the send. Also the publisher's `longest_turn_s` (already in `/status`) per configuration |
| **H2. The harness sharing the host.** The harness process runs the tester (in a thread) and the reader tasks, which parse every event. In condition 4 it also polls `/status` every 0.5 s, which the simulator serves on its loop (`scripts/gui_m2_early_check.py:491`). Nothing pins any process to a CPU. The tester's own delays fall outside the wire interval, so the harness can only reach it through the host (CPU placement, caches, frequency) | consistent: the cost grows from one reader to three, and every reader runs in the harness process. The stalled client costs about as much per client as readers 2 and 3, though it does no parsing on the harness side; that weighs only mildly against H2 alone | the readers in a separate process from the tester; the simulator, the tester and the readers pinned to separate CPUs (`taskset`) |
| **H3. Host power management.** Governor `powersave` on all 12 CPUs. The rate falls as clients are added (5,773.6–6,704.5 req/s for condition 1, 3,474.1–3,922.8 for condition 4), so the gaps between requests change too. If CPU frequency or wake-up latency follows how busy the processes are, wire latency moves with it | not tested; the lower load average did not remove the excess | a repeat with the `performance` governor. The plan keeps the governor as it is, so this **needs the owner's approval** |

The 1→2 step, the API with no clients, is the largest single step (+0.045 ms), and the
diagnostic cannot split it: condition 1 has no `dispatch_us`. The simulator trace under H1
would cover it too.

### The verdict stays `STOP`

- Condition 4 missed the median limit in 15 of 15 rounds, and r3 in 3 of 15. Per the stop
  rule, this is reported before M3.
- No round that printed no stop reason is a pass. No threshold was tuned, and no condition
  was lengthened or changed.
- M3 is not started. What to test next, and whether M3 waits for it, is the owner's
  decision. (Updated 2026-09-28: decided; see "Owner decisions (2026-09-28)" near the
  top.)

### Captures

The `candump` logs are now committed, xz-compressed, in
[`gui-m2-early-check-captures/`](gui-m2-early-check-captures/) (`c1790bd`):

| Directory | What it is | Files | Compressed | Uncompressed |
|---|---|---|---|---|
| `diag-run-1/` | all logs of diagnostic run 1 ([diag-run-1.txt](gui-m2-early-check-runs/diag-run-1.txt)): 5 rounds × 5 configurations, new names (`000`, `100`, `110`, `130`, `131`) | 25 | 504,660 bytes | 11,922,725 bytes |
| `diag-run-2/` | all logs of diagnostic run 2 ([diag-run-2.txt](gui-m2-early-check-runs/diag-run-2.txt)), as above | 25 | 499,436 bytes | 11,922,725 bytes |
| `diag-run-3/` | all logs of diagnostic run 3 ([diag-run-3.txt](gui-m2-early-check-runs/diag-run-3.txt)), as above | 25 | 501,160 bytes | 11,922,725 bytes |
| `rotated-run-1/` | all logs of rotated run 1 ([run-1.txt](gui-m2-early-check-runs/run-1.txt), `/tmp/gui-m2-faqya9ej`): 3 rounds × 3 conditions, old names (`00`, `10`, `11`). Their uncompressed hashes match the run 1 block in "Rotated runs, 2026-09-28" | 9 | 178,392 bytes | 4,292,181 bytes |

- Sizes are sums of the files' sizes. Runs 2 and 3 were committed in `3328868`.
- `SHA256SUMS` holds two blocks: the sha256 of each uncompressed log (84 lines), and of
  each committed `.xz` file (84 lines).
- Every analysis in this record that uses the captures can be reproduced from these
  files. Rotated runs 2 and 3 are not committed; their hashes are in the table in
  "Rotated runs, 2026-09-28".
- To verify, from `docs/validation/gui-m2-early-check-captures/`:

  ```sh
  grep '\.xz$' SHA256SUMS | sha256sum -c
  grep -v -e '^#' -e '^$' -e '\.xz$' SHA256SUMS | while read -r sum file; do
    [ "$(xz -dc "$file.xz" | sha256sum | cut -d' ' -f1)" = "$sum" ] \
      && echo "$file: OK" || echo "$file: FAILED"
  done
  ```

  All 168 checks passed when this section was written.

### Deferred before M4

From the Task 12 review, not fixed, and to be settled before M4 relies on this harness:

- **The segment guard is an `assert`** (`SEGMENT_SIZE <= HARVEST_LIMIT` in
  `build_parser`). `python -O` skips it, and it allows 500, which leaves no margin in the
  500-event history. It should be a real check, with `<`.
- **The `--captures` refusal test runs the real script in a subprocess.** If the refusal
  broke and pytest ran outside a namespace, it could start `candump` and a simulator on
  the host's `vcan0`. This is for the owner to note.
- **The script is 857 lines** (`scripts/gui_m2_early_check.py`).
- **Pause timing at M4 scale is unmeasured.** The 50 ms pause covered every harvest here
  (0 overruns at 5,000 requests, 250 per segment). At 20,000 requests (80 segments), or at
  condition 5's maximum rate, it has not been measured.
- Also deferred: the readers-first wait raises instead of printing a P5 line (it is loud
  either way), and the label of the 2→4 step when r1 and r3 are left out.

### Gates and CI for this part

Local only. "Gates" gives the harness gates at `b6528b8`. "Hosted CI" adds run
36436675557 at `70c4e9f`. When this was written, no hosted run covered `6bbad2c` or any
later commit, including the diagnostic harness (`7b5b7ea`, `b6528b8`): they were not
pushed (`origin/gui` was at `70c4e9f`).

(Updated 2026-09-28.) Run 36477465250 at `e5f3a8b` covers `6bbad2c` to `e5f3a8b`,
including the diagnostic harness. See "Hosted CI".

## M4 forced 1013 closes: a proposal for the owner

**Decided (updated 2026-09-28).** The owner decided on a variant of option (b): 0010's
seventh revision (`ec0839a`, `c6f9a90`) adds a forced-close run in every M4 round. See
[0010 §9.2](../decisions/0010-gui-observer-api.md), *Forced-close run*; 0010 is the
source for its terms. The proposal below is kept as history, unchanged. The owner's
later decisions add criteria to that run: see "Owner decisions (2026-09-28)" near the top.

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
  **22,900–25,400 requests**, or 6.5–6.8 s. This is consistent with the 23,000–26,000
  estimated above.
- M4's condition 4 runs 20,000 requests: 5.2–5.9 s at these rates. So **as specified,
  M4 condition 4 would see no forced close**. This assumes the M2 rates hold at 20,000
  requests, which is not measured.
- **But it would overflow.** While the queue is full, every offer is dropped and counted
  in `client_dropped` (`src/ecu_simulator/observe/connection.py`, `offer`). Overflow
  begins after about 5,974 events (1.5–1.8 s at these rates). At 20,000 requests the
  stalled client then drops about 14,000 events over 3.6–4.1 s, without reaching 5 s of
  overflow. So M4's condition 4, as specified, is **"stalled, overflowing, not forced
  off"**. That is a different condition from M2's "stalled, not overflowing" at 5,000
  requests, under every option below.
- No close falls inside 20,000 requests at any rate above about 2,800 requests/s
  ((20,000 − 5,974) / 5 s). Below that, one close would.
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
  never reads. It overflows sooner than as specified, and any close falls in the timed
  window.
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

- **Condition 4 stays an honest latency condition:** yes. Timed condition 4 is 0010's
  condition 4 at 20,000 requests, unchanged: stalled, overflowing after about 6,000
  messages, not forced off. It is **not** the M2 condition, which never overflowed (see
  above); no option keeps that. What (b) keeps is a timed window with the same content in
  every round, overflow without a close, at any rate above about 2,800 requests/s. 0010
  would say so.
- **What it proves:** on vcan, with the production limits (1,024 messages, 5 s) and the
  real simulator loop:
  - a stalled client is forced off with 1013, `forced_disconnects` counts it, and the
    harness reconnects;
  - P5(e) holds with non-zero counts: `close_codes["1013"]` = `forced_disconnects`, and
    reconnects = `forced_disconnects`;
  - P5(h)'s allowance holds on 1013 closes, and P6 holds for the three readers.
  - The timed condition 4 does judge the latency of overflow (the drops) against P1–P2.
    It does not judge the cost of a close and reconnect. The sub-run can report that
    latency, unjudged, or, if the owner chooses, judge it against P1–P2 as well (see the
    recommendation).
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
  kept out of the capture. Like every option, it is not the M2 condition. Compared with
  (b) and (d), whose stalled client starts dropping after 1.5–1.8 s, the timed window
  differs in two ways:
  - the client is dropping from the first timed request, not from about 30 % in;
  - it holds one forced close, 5 s after overflow began, then the reconnect and the new
    connection's initial history (up to 500 events).

  The close and reconnect are the real difference. They make (c) the only option whose
  **timed** latency includes a close, which is closer to 0010's present text for
  condition 4 ("disconnects … reconnects it at once, so a stalled client is present
  throughout").
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

- **Condition 4 stays an honest latency condition:** yes. It is 0010's condition 4 at
  20,000 requests, unchanged: overflowing after about 6,000 messages, not forced off (not
  the M2 condition). P5(e) would say plainly that conditions 1–4 do not exercise the 1013
  path.
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

None of the options keeps M2's "stalled, not overflowing": at 20,000 requests the stalled
client overflows under all four.

| Option | Honest latency condition? | Timed condition 4 holds | Forced closes | 1013 proven on vcan with production limits? | Extra runtime per M4 run | 0010 text |
|---|---|---|---|---|---|---|
| (a) | yes | overflow, sooner; 0–1 close | 0–1 per round at best | only if a close happens | none | none; to make it work, *Runs* or §4.3 (not proposed) |
| (b) | yes | overflow from ~6,000 messages, no close (the same every round above ~2,800 req/s, if the M2 rates and absorption hold at 20,000 requests (unmeasured; see above)) | k per round, required, in the sub-run | yes, outside the timed window | ~1 min for k = 3 | condition 4 row, *Runs*, P5(e) |
| (c) | yes | overflow from the first timed request; one close, then the reconnect and a fresh connection; timing-dependent | ~1 per round | yes, inside the timed window | ~6 s | condition 4 row, *Runs*, *Measurement* |
| (d) | yes | as (b) | none | no: loopback, with injected limits | none | condition 4 row, P5(e) |

### Recommendation: (b) with (d)

**Awaiting the owner's decision (0010 change).** (Updated 2026-09-28: decided; see the
note at the head of this section.)

- Keep timed condition 4 as 0010 specifies it, and correct its text: at 20,000 requests
  the stalled client overflows, but is not expected to be forced off.
- Add a forced-close sub-run per round, outside the timed conditions. It must reach k
  forced closes, each reconnected; k = 3 is proposed.
- Name the existing unit and loopback tests in P5(e) as the proof of the close logic
  itself.

Why:

- It is the only option that **requires** forced closes on vcan with the production
  limits, and so makes P5(e)'s 1013 reconciliation non-trivial every round.
- The timed condition 4 holds the same thing in every round (overflow, no close) at any
  rate above about 2,800 requests/s, if the M2 rates and absorption hold at 20,000
  requests (unmeasured; see above). Under (c), one close falls at a timing-dependent
  point, so rounds can differ.
- It costs about a minute per M4 run.
- (a) cannot produce closes reliably at 20,000 requests, whatever the buffer. (c) gives
  about one close, at a timing-dependent point. (d) alone leaves the harness's reconnect and P5(e)'s non-zero case
  unexercised on vcan.

What (c) offers that (b) does not is a close and reconnect inside judged latency. (b)
can offer it too, with k closes instead of one and without the timing dependence: judge
the sub-run's wire latency against P1–P2, not only report it. That choice is the owner's;
the wording below reports it, unjudged.

(Fix round 1: the first version of this recommendation said timed condition 4 stays the
condition measured at M2, "not overflowing". That was wrong: at 20,000 requests it
overflows under every option. The recommendation is unchanged, because its first reason,
that (b) alone requires closes, still holds. The second reason is replaced above.)

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
   > 20,000 requests the stalled client is not expected to be forced off: it begins to
   > overflow after about 6,000 messages, and needs 5 s of continuous overflow before a
   > 1013 (`docs/validation/gui-m2-early-check.md`). If the §4.3 rule does disconnect it, the
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
   > count must be ≤ `forced_disconnects`, and any shortfall is reported. HTTP 503
   > refusals seen = `refused_clients`.

   Proposed: insert this after "and any shortfall is reported." and before "HTTP 503
   refusals seen = `refused_clients`.":

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

**`e5f3a8b` — local.** Run by the controller, in this worktree, at `e5f3a8b` on branch
`gui`. These are local results, not a hosted CI run. The owner has since pushed
`6bbad2c..e5f3a8b`, so `origin/gui` is at `e5f3a8b`. This record's statements that
those commits are unpushed, or that `origin/gui` is at `70c4e9f`, were true when written.
The hosted run at `e5f3a8b` is 36477465250, recorded in "Hosted CI" below.

| Gate | Command | Result |
|---|---|---|
| Namespace suite (private namespace, `lo` up) | `unshare -r -n bash -c 'ip link set lo up && .venv/bin/python -m pytest -p no:cacheprovider -q -rsx'` | **1213 passed, 69 skipped, 2 xfailed** |
| Lint | `.venv/bin/ruff check .` | clean |
| Types | `.venv/bin/mypy` | clean |
| vcan integration (private `vcan0`) | `scripts/run_integration_tests.sh` | **69 passed** |

**`b6528b8` — local-only, unpushed.** The harness gates of Task 12's fix round 1, as
reported in its implementation report (fix-round section); not re-run for this record.
Run in this worktree at `b6528b8` on branch `gui` (not on `origin/gui`, which is at
`70c4e9f`). These are local results, not a hosted CI run: see "Hosted CI" and "Local vcan
results" below.

| Gate | Command | Result |
|---|---|---|
| Namespace suite (private namespace, `lo` up) | `unshare -r -n bash -c 'ip link set lo up && .venv/bin/python -m pytest -p no:cacheprovider -q -rsx'`, the command the same report gives for its first run | **1213 passed, 69 skipped, 2 xfailed**. Against `b27cf60`'s 1159: +54 passed, all in `tests/unit/test_gui_m2_early_check.py`; skipped and xfailed unchanged |
| Lint | `ruff check .` | all checks passed |
| Types | `mypy` | no issues in 58 source files; `src/` only, as below, so `scripts/gui_m2_early_check.py` is not type-checked |
| vcan integration | — | **not re-run after Task 12.** The last run is `b27cf60`'s 69 passed, below. Between `b27cf60` and `b6528b8` the only code changes were to `scripts/gui_m2_early_check.py` and `tests/unit/test_gui_m2_early_check.py` (the plan and this record also changed); `src/` did not |

**`b27cf60` — history.** Run by the controller, in this worktree, at commit
`b27cf60` on branch `gui` (not on `origin/gui`, which is at `e0c8445`). These are local
results, not a hosted CI run: see "Hosted CI" and "Local vcan results" below. (Updated
2026-09-28: `b27cf60` is now on `origin/gui`, at `70c4e9f`, and hosted run 36436675557
covers it. The results in this table are still the local ones.)

| Gate | Command | Result |
|---|---|---|
| Namespace suite (private namespace, `lo` up) | `unshare -r -n bash -c 'ip link set lo up && .venv/bin/python -m pytest -p no:cacheprovider -q -rsx'` | **1159 passed, 69 skipped, 2 xfailed** |
| Lint | `ruff check .` | all checks passed |
| Types | `mypy` | no issues in 58 source files. It checks `src/` only (`files = ["src"]` in `pyproject.toml`); `scripts/` and the new `tests/unit/test_gui_m2_early_check.py` are not type-checked |
| vcan integration (private `vcan0`) | `scripts/run_integration_tests.sh` | **69 passed** |

**`73e9e9f` — history.** All on `73e9e9f` (the tree at the time these gates were run; the
commit that added this record changed only this file).

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

**Expected effect on the next hosted `.[dev]` run.** `tests/unit/test_gui_m2_early_check.py`
(committed at `5f1f202`) imports aiohttp via `pytest.importorskip`, one module-level skip,
reason "needs the optional [gui] extra (aiohttp)" — the same reason the three existing
`tests/unit/api` module skips give. Without the `[gui]` extra it is a fourth such module.
So the next hosted `.[dev]` run is **expected** to show 60 skipped with 4x "needs [gui]",
not the 59 skipped and 3x recorded below. This is an expectation, not a result: no hosted
run has confirmed it yet, and the "Hosted CI" figures below are left as recorded.
(Updated 2026-09-28: hosted run 36436675557 at `70c4e9f` confirmed it: `.[dev]` 60
skipped, 4x "needs [gui]".)

## Hosted CI

(Corrected 2026-09-28.) The first version of this section said these were the only
hosted runs, and that none covered `73e9e9f` or the commit that added this record
(`cf0cb3a`). Run 36363466270 at `e0c8445` now covers both: each is an ancestor of
`e0c8445`. At that correction, no hosted run covered `7141045` or any later commit.

(Updated again 2026-09-28.) Run 36436675557 at `70c4e9f` covers `7141045` to `70c4e9f`,
including the rotated runs' harness (`a7431be`). At that update, no hosted run covered
any commit after `70c4e9f`: `6bbad2c` and later, including the diagnostic harness
(`b6528b8`), were not pushed (`origin/gui` was at `70c4e9f`).

(Updated 2026-09-28.) Run 36477465250 at `e5f3a8b` covers `6bbad2c` to `e5f3a8b`,
including the diagnostic harness. **No hosted run covers any commit after `e5f3a8b`**:
`aa8cffe` and later, including 0010's eighth revision and the M3a mockup, are not
pushed.

| Run | `head_sha` | Result |
|---|---|---|
| 36360139096 | `e70e73199c9951f2d08465de65185b0baecb1004` | **FAILURE**: the `.[dev]` job failed a timing-dependent test, then undiagnosable |
| 36361183412 | `334ecf49fe364258166d01e1aad485f3d437c476` | **FAILURE**: `FAILED tests/unit/observe/test_publisher.py::test_encode_failure_publishes_a_fallback_and_seq_stays_contiguous - assert 2 == 4` on 3.12 and 3.13 |
| 36361625692 | `6a04ee5fb985591b34a414573d19ca9d75ec7b68` | **SUCCESS**: `.[dev]` 3.12 and 3.13 each 1044 passed, 59 skipped, 2 xfailed; api `.[dev,gui]` 1094 passed, 57 skipped, 2 xfailed; lint green; can-capabilities green |
| 36363466270 | `e0c84455f1d441fd9ab049c95b4324f57d82e492` (branch `gui`, push) | **SUCCESS**: `.[dev]` 3.12 and 3.13 each 1044 passed, 59 skipped, 2 xfailed; api `.[dev,gui]` 3.12 1094 passed, 57 skipped, 2 xfailed; lint and type check green; the vcan and `can_isotp` probe green, kernel `6.17.0-1022-azure`, `# CONFIG_CAN_ISOTP is not set`, and its vcan integration step 1 passed, 68 skipped (CAN_ISOTP cannot bind). The skip reasons are those listed below |
| 36436675557 | `70c4e9fe9052035e44ac316ffbc21696c773b656` (branch `gui`, push) | **SUCCESS**: `.[dev]` 3.12 and 3.13 each 1044 passed, 60 skipped, 2 xfailed (54x CAN_ISOTP, 4x [gui], 2x [hardware]); api `.[dev,gui]` 3.12 1137 passed (1094 + 43 harness tests), 57 skipped (54x CAN_ISOTP, 2x [hardware], 1x aiohttp installed), 2 xfailed; lint and type check green; can-capabilities green, its vcan integration step 1 passed, 68 skipped (CAN_ISOTP cannot bind on `6.17.0-1022-azure`) |
| 36477465250 | `e5f3a8b1095b0dbd46c1633e8b221a3014849c74` (branch `gui`, push) | **SUCCESS**: `.[dev]` 3.12 and 3.13 each 1044 passed, 60 skipped (54x CAN_ISOTP, 4x [gui], 2x [hardware]), 2 xfailed; api `.[dev,gui]` 3.12 1191 passed, 57 skipped (54x CAN_ISOTP, 2x [hardware], 1x aiohttp installed), 2 xfailed; lint and type check green; the vcan and `can_isotp` probe green, its vcan integration step 1 passed, 68 skipped (CAN_ISOTP cannot bind on `6.17.0-1022-azure`: `modprobe: FATAL: Module can_isotp not found`) |

Hosted skips: 54x "kernel cannot create CAN_ISOTP sockets (CONFIG_CAN_ISOTP not built,
e.g. GitHub-hosted Azure kernels)"; 3x [gui] (`.[dev]` only); 2x [hardware]; 1x
aiohttp-installed, by name (api job only). As recorded for run 36363466270; see "Gates"
above for why the next hosted `.[dev]` run is expected to add a fourth [gui] skip (60
skipped, 4x), not yet confirmed by a hosted run. (Updated 2026-09-28: run 36436675557
confirmed it, with 4x [gui] and 60 skipped in `.[dev]`. Run 36477465250 at `e5f3a8b`
shows the same: 60 skipped in `.[dev]`, with the fourth [gui] skip.)

**The 54 vcan tests are skipped on hosted runners and never validated there**
([decisions/0009](../decisions/0009-self-hosted-vcan-runner.md)).

## Local vcan results

The early check above and `scripts/run_integration_tests.sh` (69 passed) ran on this
host, in private namespaces. **They are local, not CI.** So did the three rotated runs
(2026-09-28, `a7431be`) and the three diagnostic runs (2026-09-28, `b6528b8`); they too
are local, not CI.

## Skips, by reason

Host full suite (1): `tests/unit/test_gui_extra_is_optional.py:57`: aiohttp is installed
(a [gui] environment): the 'not installed' form runs in the .[dev] jobs.

Local CI-shaped `.[dev]` (59):

| Count | Reason |
|---|---|
| 54 | CAN interface 'vcan0' does not exist (no `vcan0` in the namespace) |
| 3 | needs the optional [gui] extra (aiohttp): `tests/unit/api/test_run_with_api.py`, `test_server_http.py`, `test_server_ws.py`, one module skip each — every `tests/unit/api` module |
| 2 | needs the optional [hardware] extra: `tests/unit/test_elm327_serial.py`, `tests/integration/test_elm327_simulated.py` |

As of `73e9e9f`. `tests/unit/test_gui_m2_early_check.py` (committed later, at `5f1f202`)
gives a fourth "needs the optional [gui] extra" module skip; the next hosted `.[dev]` run
is expected to show 60 skipped, 4x, not 59 and 3x (see "Gates" above). Not yet confirmed
by a hosted run. (Updated 2026-09-28: confirmed by hosted run 36436675557 at `70c4e9f`.)

Local CI-shaped `.[dev,gui]` (57):

| Count | Reason |
|---|---|
| 54 | CAN interface 'vcan0' does not exist |
| 2 | needs the optional [hardware] extra (same two modules) |
| 1 | `tests/unit/test_gui_extra_is_optional.py:57`: aiohttp is installed (a [gui] environment): the 'not installed' form runs in the .[dev] jobs |

Locally the 54 are skipped because the namespace has no `vcan0`; on hosted runners
because the kernel lacks CAN_ISOTP. Locally they ran, and passed, through
`scripts/run_integration_tests.sh` and the host full suite.
