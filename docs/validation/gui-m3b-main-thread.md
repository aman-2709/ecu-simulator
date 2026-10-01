# The observer page's main-thread load: investigation (Task 39)

**An investigation, not a change.** Nothing under `src/` changed. It ends in a fix
**proposal**, which is **not implemented**. It follows the open finding of the M3b record
([gui-m3b-live-demo.md](gui-m3b-live-demo.md), "Open finding: the page's main-thread load").

**The M2 early-check latency `STOP` is separate and unaffected**
([gui-m2-early-check.md](gui-m2-early-check.md)). It is about the simulator's answer latency on
the bus. Nothing here measures it, and nothing proposed here touches it.

**In short:**
- Once the log holds its 2,000-exchange cap, the page keeps Chrome's main thread **busy 99.8–100 %**
  at both 1440 × 900 and 390 × 844, with the traffic script at 3.8 requests/s. The cap is reached
  after about 9 minutes of that traffic.
- **M3a's log render (`renderLog`) costs 0.32–0.40 s a call at 2,000 rows.** It runs 119–144 times
  a minute and takes 45–49 s of each 60 s. Its cost follows the **number of rows it renders**, not
  the number of new rows. "Pause view" does not reduce it, and neither does "Hide graphs".
- **A second cost multiplies the first.** Two CSS animations together repaint on the main thread about 59
  times a second: the live lamp's endless `beat` and the signal table's change flash. Each repaint
  costs about 13 ms when 2,000 rows are rendered. With no traffic at all, the page is still busy
  **83 % / 79 %**.
- **The graphs cost 0.06–0.16 s a minute in every run where they are shown**, at most 1.2 % of the busy time (0.1–0.2 % in the saturated runs).
- **Proposal, not implemented:** render at most the newest *N* log rows, and keep all 2,000 for the
  counts and filters. Its expected effect comes from an analogue, a different condition, not
  from the fix: the page's own "Clear view", with about 210 rows rendered, measured **about 40 %**
  busy at both widths, against 100 % today (see "Proposal" for how the two differ).

**Not judged here.** These are headless Chrome measurements, `--disable-gpu`, on a shared host.
No target exists for the page's main-thread load, so this record judges nothing against M4 or
P1–P9.

## Method

**Runs.** `scripts/run_gui_demo.sh --m3b-perf` ran at `f10501d`. The page has been unchanged
since `de9972c`. The run used a private network namespace with its own `vcan0` and loopback, so
the host's `vcan0`, `can0` and simulator were not touched. The capture took one fresh headless
Chrome 151 and the real simulator on the stepped demo
(`docs/examples/ice_drive_cycle_stepped.yaml`), with the 2 min graph window.

Unlike `--m3b`, this mode injects no WebSocket wrapper and no MutationObserver. The capture
evaluates in the page only at each run's start and end.

**Equivalence.** Every run was set up the same way:
- **Duration:** 60 s, with one trace each.
- **Rows:** the log was filled to its cap first. `MAX_ROWS = 2000`, `app.js` line 22; `trim()`,
  lines 527–541, drops the oldest exchange as each new one arrives past it. So **every run has
  2,000 retained exchanges at its start and at its end**. The script read the retained count as
  the ECU filter's "All ECUs (N)", which is the page's `S.exCount`. The rendered rows are recorded
  as well.
- **Traffic:** the same script, `scripts/gui_demo_traffic.py --rate 4`. Observed: **3.77–3.81
  requests/s**, from the script's own counter, and the same rate of exchanges by the page's seq.
  The rate is below 4/s because the cycle's OBD 07 request waits out its 0.5 s timeout.
- **Viewports:** 1440 × 900, and 390 × 844 with the graphs scrolled to the top, as in run 9.

Each viewport had eight conditions, run in this order:

| Condition | What differs from the baseline |
|---|---|
| baseline-a, baseline-b | Nothing: the log running, the graphs shown. The two runs show the spread. |
| log-paused | "Pause view" pressed. |
| graphs-hidden | "Hide graphs" pressed. |
| no-traffic | The traffic script stopped. 2,000 rows are retained and rendered; the log does not change. |
| no-traffic-reduced-motion | As no-traffic, with `prefers-reduced-motion: reduce` emulated. `app.css` lines 485–487 then turn every animation off. |
| log-cleared | "Clear view" pressed at the start. 2,000 rows are still retained, but only the exchanges that arrive after the clear are rendered: 12 → 239. |
| log-cleared-reduced-motion | As log-cleared, with reduced motion. |

Each control was clicked for real, and the script checked that the page took the click.

**Trace analysis.** The traces were analysed with `scripts/gui_trace_breakdown.py`, on the page's
main thread (`CrRendererMain`):
- **Busy total:** the union of the outermost complete events on `CrRendererMain`, over the
  thread's own span. These are almost all scheduler tasks (`ThreadControllerImpl::RunTask`; the
  trace has the `toplevel` category for this), but not only. In 1440 × 900 baseline-a, the
  RunTask events sum to 59.70 s against a busy total of 59.99 s. The rest is events outside any
  task, among them a 0.20 s `Layout`.
- **Breakdown by self time:** every complete event is placed in one tree by containment. Each
  event is charged only the time its children do not cover, so nested events are never counted
  twice. The parts add up to the busy total exactly ("parts sum" below).
- **Buckets:**
  - JavaScript includes GC (shown in brackets);
  - style is `UpdateLayoutTree`;
  - layout is `Layout`;
  - paint is `PrePaint` (shown in brackets) plus `Paint`;
  - composite is `Layerize`, `UpdateLayer` and `ScrollLayer`;
  - other is the rest.
- **Other** is almost all the scheduler task's own self time, 0.4–1.6 s: the time inside a
  task that no timeline event describes, such as scheduling and IPC. The rest is microtask
  checkpoints, mojo receive and hit tests, each under 0.1 s.
- **`renderLog`:** each firing of the timer at `app.js` line 1434, `scheduleRender`, counted as
  its whole `TimerFire`. That includes the style and layout forced inside the call.

**Cross-check.** Chrome's own `Performance.getMetrics` `TaskDuration` agrees with the trace's busy
total within 0.2 s in every run. For example, 60.06 s against 59.99 s, 7.24 against 7.17, and 19.29
against 19.17.

**For the owner: the traces kept are copies.** The committed traces are main-thread-only copies
(22.2 MB). They reproduce every number in this document, but they drop the raster, compositor and
GPU threads. The raw traces (61.5 MB) were kept only in a session scratchpad, which is not
durable, so their recorded SHA-256 values cannot be re-checked later.

**Evidence.** The evidence is in [gui-m3b-perf/](gui-m3b-perf/):
- the sixteen traces, the page's main thread only, with the raw traces' SHA-256;
- `m3b-perf-results.json`;
- `capture.log`;
- an earlier session's 1440 × 900 repeat.

## Runs

All runs are 60 s, with 2,000 exchanges retained at the start and at the end. Times are in
seconds. "Rendered" is the exchange rows in the table at the start and end.

| Viewport | Condition | Req/s | Rendered | Busy % | Top-level total | JS (GC) | Style | Layout | Paint (PrePaint) | Composite | Other | Parts sum | `renderLog` n | `renderLog` sum | ms / call | Frames |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1440 × 900 | baseline-a | 3.81 | 2000→2000 | 99.8 | 59.99 | 8.68 (2.50) | 9.25 | 29.94 | 11.06 (8.95) | 0.10 | 0.96 | 59.99 | 135 | 45.77 | 339 | 186 |
| 1440 × 900 | baseline-b | 3.80 | 2000→2000 | 99.9 | 60.19 | 8.54 (2.48) | 9.63 | 29.10 | 11.81 (9.36) | 0.11 | 1.00 | 60.19 | 144 | 46.08 | 320 | 266 |
| 1440 × 900 | log-paused | 3.77 | 1982→1753 | 99.9 | 60.15 | 8.92 (2.63) | 9.11 | 29.24 | 11.74 (9.59) | 0.07 | 1.07 | 60.15 | 139 | 45.40 | 327 | 197 |
| 1440 × 900 | graphs-hidden | 3.79 | 2000→2000 | 99.9 | 60.09 | 8.59 (2.59) | 9.31 | 29.51 | 11.65 (9.21) | 0.12 | 0.92 | 60.09 | 138 | 46.31 | 336 | 254 |
| 1440 × 900 | no-traffic | 0 | 2000→2000 | 83.0 | 49.89 | 0.53 (0.30) | 0.38 | 0.82 | 46.01 (31.47) | 0.74 | 1.42 | 49.89 | 0 | 0 | — | 3518 |
| 1440 × 900 | no-traffic-reduced-motion | 0 | 2000→2000 | 11.9 | 7.17 | 0.55 (0.32) | 0.05 | 0.90 | 5.06 (3.41) | 0.09 | 0.53 | 7.17 | 0 | 0 | — | 389 |
| 1440 × 900 | log-cleared | 3.78 | 12→239 | 31.9 | 19.17 | 1.19 (0.18) | 1.30 | 3.01 | 11.27 (3.51) | 0.67 | 1.73 | 19.17 | 227 | 4.44 | 20 | 3527 |
| 1440 × 900 | log-cleared-reduced-motion | 3.78 | 14→241 | 17.1 | 10.30 | 1.23 (0.20) | 1.36 | 3.18 | 3.32 (1.37) | 0.17 | 1.04 | 10.30 | 227 | 4.88 | 22 | 883 |
| 390 × 844 | baseline-a | 3.80 | 2000→2000 | 99.9 | 60.26 | 7.65 (1.93) | 10.23 | 32.04 | 9.43 (8.56) | 0.09 | 0.81 | 60.26 | 121 | 48.65 | 402 | 228 |
| 390 × 844 | baseline-b | 3.81 | 2000→2000 | 99.9 | 60.04 | 7.67 (1.91) | 10.32 | 31.68 | 9.41 (8.59) | 0.09 | 0.87 | 60.04 | 128 | 48.03 | 375 | 241 |
| 390 × 844 | log-paused | 3.77 | 1983→1754 | 99.9 | 60.15 | 7.70 (1.97) | 10.18 | 31.58 | 9.72 (8.85) | 0.10 | 0.88 | 60.15 | 128 | 48.11 | 376 | 241 |
| 390 × 844 | graphs-hidden | 3.80 | 2000→2000 | 100.0 | 60.04 | 7.55 (1.93) | 10.08 | 31.90 | 9.64 (8.72) | 0.08 | 0.79 | 60.04 | 119 | 48.39 | 407 | 252 |
| 390 × 844 | no-traffic | 0 | 2000→2000 | 79.1 | 47.53 | 0.58 (0.35) | 0.33 | 0.97 | 43.94 (33.28) | 0.24 | 1.47 | 47.53 | 0 | 0 | — | 3527 |
| 390 × 844 | no-traffic-reduced-motion | 0 | 2000→2000 | 11.1 | 6.67 | 0.52 (0.27) | 0.05 | 0.99 | 4.56 (3.43) | 0.03 | 0.53 | 6.67 | 0 | 0 | — | 387 |
| 390 × 844 | log-cleared | 3.78 | 11→238 | 28.4 | 17.08 | 1.27 (0.23) | 1.55 | 3.81 | 8.38 (3.49) | 0.32 | 1.75 | 17.08 | 227 | 5.59 | 25 | 3517 |
| 390 × 844 | log-cleared-reduced-motion | 3.78 | 13→240 | 17.5 | 10.50 | 1.31 (0.22) | 1.71 | 3.96 | 2.37 (1.29) | 0.16 | 1.00 | 10.50 | 227 | 6.13 | 27 | 860 |

"Frames" is the number of rendering frames in the window (`AnimationFrame` begin events).

**Inside `renderLog`, at 2,000 rows (baseline-a):**

| Viewport | JavaScript | Style | Layout | Total |
|---|---|---|---|---|
| 1440 × 900 | 8.31 s | 9.12 s | 28.34 s | 45.77 s |
| 390 × 844 | 7.29 s | 10.17 s | 31.18 s | 48.65 s |

The layout is forced inside the call. After the rebuild, `toBottom()` reads `scrollHeight`
(`app.js` lines 1476, 1485).

**The rest of the busy time at 2,000 rows:**
- **The paint of the frames:** 186 frames, 11.06 s of paint. That is about 59 ms a frame, but
  only as the total paint divided by all 186 frames. The frames that follow a rebuild are not
  separated from the others.
- **The WebSocket handler:** 0.35–0.97 s.
- **The graphs' redraw:** 0.08–0.09 s. That figure includes uPlot's own commit callbacks (see
  "Reconciliation").

**Spread.** The busy time is saturated in every baseline-like run, so it cannot spread. The log
render's figures do spread:

| Viewport | Session | `renderLog` calls (a / b) | `renderLog` sum (a / b) |
|---|---|---|---|
| 1440 × 900 | this one | 135 / 144 | 45.77 / 46.08 s |
| 1440 × 900 | earlier ([session1/](gui-m3b-perf/session1/)) | 135 / 141 | 46.21 / 45.73 s |
| 390 × 844 | this one | 121 / 128 | 48.65 / 48.03 s |

That is under 1 % in the sum and ±4 % in the count. The unsaturated runs repeat across the two
sessions at 1440 × 900:
- log-cleared: 31.9 % and 32.8 %;
- no-traffic: 83.0 % and 83.0 %.

**Cost per rendered row (quartiles of the log-cleared runs, about 15 s each):**

| Rendered rows (approx.) | 1440 busy | 1440 ms / `renderLog` | 1440 paint / frame | 390 busy | 390 ms / `renderLog` | 390 paint / frame |
|---|---|---|---|---|---|---|
| 40 | 18.6 % | 6.9 | 1.8 ms | 17.3 % | 8.0 | 1.6 ms |
| 97 | 32.2 % | 16.6 | 3.3 ms | 25.2 % | 18.7 | 2.2 ms |
| 154 | 37.2 % | 24.0 | 3.8 ms | 31.6 % | 30.2 | 2.6 ms |
| 211 | 39.7 % | 30.8 | 3.9 ms | 39.7 % | 41.5 | 3.3 ms |
| 2000 (baseline-a) | 99.8 % | 339 | 59 ms¹ | 99.9 % | 402 | 41 ms¹ |

¹ The paint of a frame that follows a rebuild. On an unchanged table of 2,000 rows (no-traffic) a
frame paints in 13.1 ms at 1440 and 12.5 ms at 390.

With reduced motion, the same quartiles are:
- 1440 × 900: 9.4 %, 15.2 %, 20.3 % and 23.7 %;
- 390 × 844: 9.4 %, 14.2 %, 21.4 % and 25.0 %.

## Reconciliation

**The arithmetic at 2,000 rows.**
- **The log demands more time than exists.** Exchanges arrive at 3.8 a second, 228 a minute.
  Rendering each at 339 ms would need 77 s per 60 s at 1440 × 900, and at 402 ms, 92 s at 390.
- **So the page saturates.** After each render, the timer's delay of `RENDER_MIN_MS − elapsed`
  (`app.js` line 1434) is already negative. The next render starts as soon as the next exchange
  is in, and takes in about 1.6–1.9 exchanges.
- **The count is therefore set by the cost:** 135 calls × 339 ms = 45.8 s (76 % of the window).
- **The busy total adds up:** the render (45.77 s), plus the frames that paint each rebuilt table
  (11.06 s of paint), plus the WebSocket handling and the rest. That makes 59.99 s, the parts sum
  in the table.

**Run 9, re-read from its committed traces with the same script** (`--rev f8a8e5f`, where the log
timer is line 1431):

| | 1440 × 900 | 390 × 844 |
|---|---|---|
| Busy (outermost events; run 9 has no `toplevel` category) | 30.98 s, 51.6 % | 42.35 s, 70.5 % |
| JS / style / layout / paint (PrePaint) / composite / other, self time | 2.80 / 3.18 / 8.46 / 15.85 (8.16) / 0.64 / 0.06 | 4.59 / 6.05 / 17.49 / 13.96 (10.44) / 0.24 / 0.03 |
| Parts sum | 30.98 s | 42.35 s |
| `renderLog`: calls, sum, mean | 227, 12.56 s, 55 ms | 227, 26.19 s, 115 ms |
| `renderLog` mean by quarter of the run | 44 → 51 → 59 → 67 ms | 96 → 107 → 121 → 136 ms |
| Rows in the log during the run (from `capture.log`) | about 253 → 500 | about 500 → 747 |
| Frames | 2,982 | 1,850 |
| The graphs' redraw, with uPlot's commit callbacks | 0.15 s | 0.16 s |

**What changes in the record's reading:**
1. **Its table's parts overlap.**
   - The listed parts (12.52 + 15.31 + 8.16 + 8.72 s, and more) add up to over 44 s, against 30.98
     s busy.
   - **Paint was counted twice:** each frame's `Paint` event holds a nested `Paint` (2,980 nested
     pairs at 1440). Paint's self time is 7.69 s, not 15.31 s.
   - **Most of the layout is inside `renderLog`:** 7.61 s of the 8.72 s, already in its 12.56 s.
   - **Self time adds up:** 2.80 + 3.18 + 8.46 + 15.85 + 0.64 + 0.06 = 30.98 s.
2. **`renderLog` is not a fixed 55 ms a call.** It grew with the rows, by about 0.125 ms per row at
   1440 and 0.22 ms per row at 390.
3. **The 390 run's "doubling" was mostly its larger log.** The 390 run started with about twice
   the rows of the 1440 run. With 2,000 rows at both widths, 390 costs 1.1–1.25 times as much
   (402 / 339 ms and 375 / 320 ms), not 2 times.
4. **The trace busy (51.6 %) and `TaskDuration` (55.3 %) differ** because run 9's trace has no
   task events. Its outermost timeline events miss the task time between them. With the
   `toplevel` category the two agree, as shown under Method.
5. **The graphs are 0.15 s, not 0.03 s.**
   - The record counted the `app.js` call alone.
   - uPlot's commit callbacks run after it returns, inside the same `v8.callFunction`, and the
     script now counts them.
   - It is still 0.5 % / 0.4 % of run 9's busy time.
6. **Run 9's paint (15.85 s) is mostly per-frame paint on a table that did not change.** The
   frames come at about 50 a second at 1440, from the animations (finding 3). Each frame's cost
   grows with the rendered rows.
7. **Run 9's instrumentation is negligible.** The capture's WebSocket wrapper and MutationObserver
   took 45 ms (1440) and 29 ms (390).

## Findings

1. **At the cap the page is saturated at both widths.**
   - The log reaches its 2,000-exchange cap after about 9 minutes of traffic at 3.8/s.
   - From then on the main thread is busy 99.8–100 %.
   - Run 9's 52 % / 71 % were measured with 250–750 rows, on the way there.
2. **The log render costs about 0.15–0.20 ms per rendered row, at every call.**
   - The cause in the code: `renderLog()` (`app.js` lines 1437–1477) rebuilds the whole table with
     `body.replaceChildren(frag)` (line 1469) whenever any entry arrives. `addEntry` calls
     `scheduleRender()` at line 500.
   - The rows themselves are cached (`rowFor`, line 1558), so the cost is not row creation. It is
     re-inserting every row: style for every inserted row, then a forced layout of the whole table,
     which is 62–64 % of the call.
   - **"Pause view" does not help.** Line 1458 holds back new entries, but every arriving
     exchange still triggers a full rebuild of the 1,750–2,000 rows still shown: 139 calls, 45.40 s.
   - **"Hide graphs" does not help:** 138 calls, 46.31 s, within the spread.
3. **The animations multiply a cost that also grows with the rendered rows.**
   - Two CSS animations repaint on the main thread:
     - the live lamp's endless `beat`, a box-shadow animation (`app.css` lines 119 and 127);
     - the 1.2 s change flash on signal cells (`app.css` lines 212 and 216; `app.js` line 1277).
   - **Together they keep about 59 frames a second** in the no-traffic runs: 3,518 and 3,527
     frames in 60 s, where reduced motion leaves 389 and 387.
   - **The lamp's own share is not established.** An ad-hoc reading of the no-traffic traces
     found frames continuing at about the same rate outside every change flash. That reading is
     not part of the committed script, so it is not reproducible from it, and no number from it is
     relied on here. The reduced-motion runs turn both animations off together, so they cannot
     separate the lamp from the flash either.
   - **Each frame costs about 13 ms with 2,000 rendered rows.** With no traffic at all, that makes
     83 % / 79 % busy.
   - **With reduced motion, 389 frames remain**, from data updates, still at about 13 ms each:
     11.9 % / 11.1 % busy.
   - The mechanism is not established, only measured. Why the per-frame `PrePaint` grows with
     rows on a table that did not change is not explained here.
4. **The graphs are not the problem.** Their redraw is 0.06–0.16 s a minute in every run where they are shown.
   Hiding them changes nothing measurable.

## Proposal (not implemented)

**Render at most the newest *N* log rows. Keep `MAX_ROWS` (2,000) retained** for the counts,
filters, gaps and clear/pause, as now.

**Where it goes.** In `renderLog()`:
1. Before the loop at line 1456, walk `S.entries` from the end. Count the entries that would be
   shown, using the same tests as lines 1457–1458 and `passes()`, until there are *N*.
2. In the existing loop, skip the entries before that point.
3. Add one note row at the top saying that the older exchanges are kept but not shown, as the
   trimmed note above line 1454 does.

Nothing else changes: the follow logic, `rowsBelow()` and the counts read `view.shownRows`, which
then holds at most *N*. It is about a dozen lines in one function.

**Expected effect, from an analogue.** No run measured the fix. The log-cleared runs are the
nearest measured condition. In both, all 2,000 exchanges are retained and walked by the same loop
at every call, and only the newest rows are rendered. But the cleared condition differs from the
fix in four ways:
- **Its rendered rows grow** from 12 to 239 over the run, rather than holding at *N*.
- **It renders no note row.** The "older rows left this view" note is not rendered after a clear
  (`app.js` line 1446); the fix would add one.
- **No oldest row leaves the rendered set at each render,** as one would at a steady *N*.
- **The ≈ 40 % below is one quarter of a run,** about 15 s at about 211 rendered rows, not a full
  60 s run held at that count.

With those differences, the analogue for a choice of *N* = 200 is:

| | 1440 × 900 now | with *N* ≈ 200 | 390 × 844 now | with *N* ≈ 200 |
|---|---|---|---|---|
| Main thread busy | 99.8–99.9 % (saturated) | **≈ 40 %** | 99.9–100 % (saturated) | **≈ 40 %** |
| `renderLog` per call | 320–339 ms | ≈ 31 ms (≈ 10–11× less) | 375–407 ms | ≈ 42 ms (≈ 9–10× less) |
| `renderLog` per minute | 45.4–46.3 s | ≈ 7 s (227 calls) | 48.0–48.7 s | ≈ 9.4 s (227 calls) |
| Paint per animation frame | 13 ms (unchanged table) | ≈ 3.9 ms | 12.5 ms | ≈ 3.3 ms |

A smaller *N* costs less: about 100 rows measured 32 % / 25 %. The value of *N*, and what a
reader may scroll back to, is the owner's decision.

**Why this one, against the other candidates:**
- **Render only when the log changed.** It already does. Even while paused, the rendered set
  still changes, because old rows are trimmed from the top (1,982 → 1,753 rendered in the paused
  1440 run), though the new rows are held back. Skipping those renders would change what the
  paused view shows. The paused runs cost the same as running (99.9 %), and pausing is not the
  normal state.
- **Coalesce to `requestAnimationFrame`.** The timer already coalesces to at most 5 a second, and
  exchanges arrive at 3.8 a second. An rAF runs more often, not less, so the count would not fall.
- **A longer throttle, for example 1 s.**
  - Below the cap: at most 60 calls a minute instead of 227.
  - At the cap the page is already saturated, so the throttle has no effect: renders already
    follow each other (135 a minute, about 1.7 exchanges each). It would only gather more
    exchanges into each one, and 60 rebuilds × 339 ms is still 20.3 s a minute.
  - It leaves the per-frame paint untouched: 83 % on its own (the no-traffic runs).
- **Incremental append and trim instead of `replaceChildren`.**
  - It would remove most of the per-call style (9.1 s) and part of the JavaScript (8.3 s).
  - What a forced layout of a 2,000-row auto-layout table costs after an append (28.3 s of
    layout today) is not measured here.
  - It leaves 2,000 rows rendered, so the per-frame paint stays. The no-traffic runs, where the
    log costs nothing, are already 83 % / 79 %. So by the numbers it is not enough on its own, and
    it is a larger change: filters, pause, clear, the hidden-row summaries and the gap notes all
    rebuild today.

**Separate and optional, not part of the log fix: the endless lamp animation.**
- Turning the animations off, as the reduced-motion runs did, takes:
  - the *N* ≈ 200 condition from about 40 % to about 24–25 %;
  - an idle page at the cap from 83 % to 12 %.
- Those runs turned the lamp and the change flash off together. How much of the effect is the
  lamp's alone is not measured.
- A change there means either no endless animation, or one on a property the compositor can run
  without a main-thread repaint. It is a visual of the M2/M3a status bar and needs the owner's
  decision. Its effect on a GPU-composited real browser is not measured here.

## Open items

- [ ] **Owner decision:** whether the log gets the *N*-row change, the value of *N*, and how older
  rows are reached. It is an M3a log change, outside M3b's scope, and needs its own acceptance.
- [ ] **Owner decision:** the lamp animation, separately.
- [ ] **After any change,** run the same `--m3b-perf` conditions and compare against this table.
  The figures above are a measured analogue of the fix, not a measurement of it.
- [ ] **The browsers measured.** These are headless Chrome measurements with `--disable-gpu`. A
  GPU-enabled Chrome and Firefox (the owner's manual checks) may paint differently.
- [ ] **Not explained:** the mechanism by which per-frame `PrePaint` grows with rendered rows on an
  unchanged table.
- The M2 latency `STOP` stays open and is untouched by this record.
