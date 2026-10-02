# The observer page's main-thread load: investigation (Task 39)

## Outcome (Tasks 42-46)

**Status: mitigated on headless numbers; the finding stays open** until the owner confirms it in
their own browser. M3b stays **implemented, not accepted**. **The M2 early-check latency `STOP` is
separate and unaffected** ([gui-m2-early-check.md](gui-m2-early-check.md)): nothing in Tasks 42-46
measures or changes the simulator's answer latency, the API rate or the diagnostic path.

The owner's instruction (2026-10-01; [plan](../plans/gui-m2-implementation.md), "M3b main-thread fix:
a bounded, navigable exchange log") set the design and the measurement protocol. Everything below
was measured in **headless Chrome 151 with `--disable-gpu`**, in a private network namespace, on a
shared host. Sources are named beside each number: the task reports under
`.superpowers/sdd/gui-m2-implementation/` (`task-4N-report.md`) and their scratchpad run
directories (not durable; the committed copies are named where they exist).

**Those task reports are local and untracked** (`.superpowers/` is listed in the repository's
`.git/info/exclude`), and so are the scratchpad runs. In the repository, the before-side figures can
be checked only against the committed Task 39 baselines ([gui-m3b-perf/](gui-m3b-perf/), below),
and the after-side figures only where results are committed: Task 46b Part A and Part B
(`gui-m3b-perf/results/`, and Part A's run-1 traces) and the two `--m3b-log` runs
(`gui-m3b-live-demo/log-run1/`, `log-run2/`). **These numbers rest only on the local reports:**
- the uncommitted `3cfefec` baseline, the `782140e` and Task-43-only (`4a78120`) runs, and their
  Event Timing (Task 44);
- Task 44's mutation-count check;
- the lamp diagnostic A-D, Task 45's after runs (9.5 % / 6.4 %, idle 3.8 % / 2.7 %), the
  per-thread table and Task 45's click-latency figures (p50 48 against 32);
- the invalid visible run (Part C) and its lock-screen evidence;
- the CPU readings of the owner's Chrome renderer.

### What was implemented

Commits on `gui`: `7f7aa84`, `9afdef6` (Task 42); `ebac21f`, `bd01d8f`, `330620a`, `b46f843`,
`4a78120` (Task 43); `782140e` (Task 44); `3593be7` (Task 45). The browser cases and measurements
are `e506632`, `d0e4ba7`, `c18c132` (Task 46a) and `84c0637`, `7676b32`, `9c7afd2`, `e903a7b`.
Retention is unchanged: the page keeps 2,000 exchanges (`MAX_ROWS`).

- **A window over the filtered list (Task 42, 43).** The log draws at most `LOG_WINDOW` = 200
  *matching* exchanges, chosen after the filters are applied to all 2,000 retained;
  `LOG_STEP` = 100. The selection is a pure, DOM-free block in `app.js`
  (`selectLog`, `logOlderEnd`, `logNewerEnd`, `logAnchorEnd`, `logFullEnd`, `logRows`), checked by
  `scripts/gui_log_check.js` (`LOGCHECK PASS (24 checks)`, task-44-report "Verification").
- **Follow / pin.** The window *follows* (it ends at the newest matching exchange) or is *pinned*
  at a fixed exchange id. The reader's own scroll, or Older, pins it at the newest exchange shown;
  arrivals while pinned are counted, not drawn. A scroll back to the end resumes following only
  when no matching exchange waits beyond the window (task-43-report, decision 1). Clear view
  follows again (decision 2). When eviction past the cap reaches a pinned window, it is re-pinned
  to a full window at the oldest exchanges kept (task-43-report, fix round 1).
- **Older / Newer / Jump to newest.** "Show older exchanges" and "Show newer exchanges" are real
  buttons in rows at the top and bottom of the table; each moves the window by up to 100 matching
  exchanges while keeping the topmost (Older) or bottom-most (Newer) visible row in the window, at
  its offset unless the box reaches a scroll limit; when the far end
  is already in view the step is 0 and the box scrolls to that end instead, so a press never does
  nothing (fix round 2). The existing header control and an in-row "Jump to newest" resume
  following. Focus goes to the log box after a press. Payload expansion is kept for rows that leave
  and re-enter the window.
- **Four kinds of absent rows, each worded differently** (exact strings, task-43-report "Strings"
  and fix round 2; seen in the served page by `--m3b-log` case 8):
  1. *outside this window*: the Older row "1,800 older exchanges match your filters." (and "N newer
     exchanges match your filters" in the Newer row); the header control
     "11 rows below + 35 beyond this window, jump to newest";
  2. *hidden by filters*: "634 exchanges hidden by filters (not a gap)", and beside Older / Newer
     "1,116 older exchanges hidden by filters (not a gap)";
  3. *sequence gaps and connection notes*: drawn where they fall in the window ("Gap: seq 5378–5380
     not received (3).", "Connection lost, then resumed."); outside it, counted as "1 gap marker and
     1 connection note in older rows" (or "in newer rows");
  4. *left the page's 2,000-row cap*: the existing note, "1,352 older rows left this view. The page
     keeps the newest 2,000 exchanges it received. The rows were received, so their removal is not
     a gap.", plus after a re-pin "Rows this view was showing left too, so it moved to the oldest
     exchanges kept."

  The count line reads, for example, "200 of 2,000 shown (seq 2,529–2,728), last seq 2728 (live)"
  or "200 of 250 matching shown (seq 2,062–2,261; 2,000 retained)". The example numbers are from
  `--m3b-log` fix-round run 1, 1440 × 900
  ([log-run1/m3b-log-results.json](gui-m3b-live-demo/log-run1/m3b-log-results.json)).
- **Render only what changed (Task 44).** A row key (`logRows`) decides whether the drawn rows
  changed; only then are rows inserted or removed (`syncChildren` moves only the rows that came or
  went). Counters (held count, Older / Newer counts, trimmed count, filter counts, count line) are
  written only when their text changes, and the state box keeps stable nodes. While following,
  each arrival moves one row in and one out; paused or pinned, the row list is not touched.
- **The lamp (Task 45).** The live lamp's 2 s `beat` moved from an animated `box-shadow` on the lamp
  to a ring (`::after`) animated on `transform` and `opacity` only, under
  `prefers-reduced-motion: no-preference`. The resting glow, the "last known", down and refused
  looks, every status text and the change flash are unchanged (task-45-report Part B).

### How it differs from the Task 39 proposal

The proposal (below, kept as history) was "render at most the newest *N* rows" (counted with the
filters applied) and one note row saying the older ones are kept but not shown. Alone, that would
have left every older retained exchange unreachable from the page: with no filter, 1,800 of the
2,000. The owner's design therefore added pinning, Older / Newer and Jump to newest over a window
chosen after filtering, a reader's position kept while the window shifts, the four distinct
absent-row wordings, and render-only-what-changed. The lamp change was the
proposal's separate, optional item, made only after a diagnostic attributed the cost.

### Results: before and after, same protocol

Protocol (task-44-report "Protocol as run"; the same in Tasks 45 and 46b): the stepped demo, the
2 min graph window, the log filled to 2,000 retained at 50/s, then the traffic script at `--rate 4`;
two 60 s traced runs per width, log following, with ten real clicks per run (Pause, Resume, the
"no response" chip off / on twice each, a wheel scroll up and Older, Jump to newest). Older and Jump
to newest exist only on the new page. **The interaction runs are not identical in state:** on the
new page the wheel scroll at 47 s pins the window, so about 5 s of each run (47-54 s) is pinned,
while the baseline page follows throughout (apart from the 8 s paused on both pages).

"rL" is the `renderLog` timer task; LT is top-level tasks over 50 ms; times per 60 s. Run 1 / run 2.

| Page | Width | Busy % | rL sum s | rL p95 ms | rL max ms | LT n (max ms) | Rows drawn / retained | Req/s | Source |
|---|---|---|---|---|---|---|---|---|---|
| **Before**, `3cfefec` | 1440 × 900 | 99.9 / 99.9 | 43.69 / 43.56 | 326 / 331 | 361 / 352 | 323 (396) / 320 (369) | 2,000 / 2,000 (+ the trimmed note) | 3.82 / 3.78 | task-44-report; `perf44-base-1/` |
| Before | 390 × 844 | 99.9 / 100.0 | 44.32 / 45.53 | 394 / 400 | 429 / 441 | 264 (460) / 265 (455) | 2,000 / 2,000 | 3.80 / 3.78 | same |
| **Tasks 43 + 44**, `782140e` | 1440 × 900 | 29.3 / 28.8 | 0.73 / 0.69 | 4.4 / 4.5 | 7.7 / 5.4 | 0 / 0 | 200 / 2,000 | 3.80 / 3.82 | task-44-report; `perf44-after-1/` |
| Tasks 43 + 44 | 390 × 844 | 19.9 / 19.7 | 0.34 / 0.34 | 1.8 / 1.9 | 2.7 / 3.1 | 0 / 0 | 200 / 2,000 | 3.80 / 3.78 | same |
| Task 43 only, `4a78120` (attribution) | 1440 × 900 | 29.1 / 28.3 | 0.76 / 0.76 | 4.0 / 4.6 | 6.2 / 6.4 | 0 / 0 | 200 / 2,000 | 3.80 / 3.78 | task-44-report; `perf44-t43-1/` |
| Task 43 only | 390 × 844 | 19.8 / 20.2 | 0.41 / 0.41 | 2.5 / 2.5 | 3.4 / 3.0 | 0 / 0 | 200 / 2,000 | 3.80 / 3.80 | same |
| **After Task 45, shipped `3593be7`** | 1440 × 900 | 10.0 / 9.7 | 0.74 / 0.72 | 4.68 / 4.09 | 8.69 / 8.92 | 0 / 0 | 200 / 2,000 | 3.80 / 3.78 | task-46b-report Part A; `perf46b-A/`, [results/perf46b-A-following.json](gui-m3b-perf/results/perf46b-A-following.json), run 1 traces committed |
| After Task 45 | 390 × 844 | 6.4 / 6.3 | 0.37 / 0.37 | 1.94 / 2.10 | 4.92 / 3.18 | 0 / 0 | 200 / 2,000 | 3.80 / 3.80 | same |

The shipped-page rows use Task 46b's runs, the latest on that page. Task 45's own after runs on the
same page agree (means 9.5 % at 1440 and 6.4 % at 390 over four runs, task-45-report
`after45-1/`, `after45-2/`). The uncommitted baseline at `3cfefec` is close to the committed Task 39
baselines ("Runs" below): busy 99.9-100 % against 99.8-100 %, `renderLog` 43.6-45.5 s a minute
against 45.8-48.7 s, so they remain comparable.

**Event Timing** (ms; Chrome rounds durations to 8 ms; Pause and Resume n = 4, the filter n = 8,
Older and Jump n = 2, so a p95 is close to a maximum). Duration p50 / p95:

| Width | Interaction | Before `3cfefec` | Tasks 43 + 44 `782140e` | After Task 45 `3593be7` |
|---|---|---|---|---|
| 1440 × 900 | Pause | 404 / 448 | 32 / 32 | 32 / 48 |
| 1440 × 900 | filter change | 424 / 480 | 32 / 32 | 32 / 48 |
| 1440 × 900 | Older | (not on the page) | 36 / 40 | 48 / 48 |
| 1440 × 900 | Jump to newest | (not on the page) | 40 / 40 | 48 / 48 |
| 390 × 844 | Pause | 424 / 480 | 32 / 32 | 24 / 24 |
| 390 × 844 | filter change | 456 / 544 | 32 / 32 | 24 / 32 |
| 390 × 844 | Older | (not on the page) | 32 / 32 | 32 / 32 |
| 390 × 844 | Jump to newest | (not on the page) | 40 / 40 | 32 / 40 |

Sources: task-44-report "Event Timing" (before and `782140e`); task-46b-report "Event Timing,
Part A" (`3593be7`). Task 43 alone gave the same Event Timing as `782140e`, every duration 32-40 ms
(task-44-report). Before the fix, the median processing of a click was 253-257 ms at 1440 and
329-338 ms at 390; after, input delay and processing are a few ms (Older up to about 18 ms) and the
rest is presentation.

**Attribution between Tasks 43 and 44** (task-44-report): nearly all of the change is Task 43's
window. Task 44's measurable effect under live following is `renderLog` 7-10 % lower at 1440 and
about 17 % lower at 390; its busy-% difference (at most 0.6 points) is inside the run-to-run spread.
Its effect is in the frozen states (below).

**The remaining paint after Task 44.** At `782140e`, paint was 13.2-13.5 s of 17.3-17.6 s busy at
1440 (about 77 %) and 9.1 of 11.9 s at 390, at a steady 3,601 frames a minute. Task 44's report
named the lamp's continuous `beat` as the likely cause; **that was a hypothesis until Task 45's
diagnostic attributed it** (next table).

### The lamp diagnostic (Task 45, labelled diagnostic, page `84c0637`)

One animation was turned off by a harness stylesheet; otherwise the same protocol. Busy %, mean of
two runs following with clicks (task-45-report "Attribution"; `diag45-A/` … `diag45-D/`):

| Condition | 1440 × 900 | 390 × 844 | Idle, no traffic (1440 / 390) |
|---|---|---|---|
| A, as shipped (`84c0637`) | 30.4 | 19.4 | 23.8 / 16.4 |
| B, lamp `beat` off | 8.8 | 6.5 | 3.3 / 2.9 |
| C, change flash off | 28.4 | 19.7 | — |
| D, both off | 9.6 | 6.6 | — |

- **The lamp was the cost:** turning it off removed 71 % of busy time at 1440 and 66 % at 390
  (86 % / 82 % idle); frames fell from about 3,600 a minute to 400-900.
- **The change flash is not measurable:** C and D differ from A and B by −2.0 to +0.8 points, inside
  A's own run-to-run spread (29.2 against 31.7).
- **After the change** (shipped `3593be7`, four runs; task-45-report "Before / after"): 9.5 % at
  1440 and 6.4 % at 390 (idle 3.8 % / 2.7 %), within about 0.7 points of no lamp animation at all.
  The beat's per-frame work is now on the compositor and viz threads, at about the cost it already
  had there (renderer compositor 1.96 → 1.52 s, viz compositor 1.64 → 1.56 s a minute at 1440).

### Frozen states (Task 46b Part B, one 60 s run per state, no clicks)

Busy % and `renderLog` sum, against Part A's following runs (mean of 2) on the same page
(task-46b-report Part B; `perf46b-B/`, [results/perf46b-B-states.json](gui-m3b-perf/results/perf46b-B-states.json)):

| Width | Following (with clicks) | Paused | Pinned | Graphs hidden, following |
|---|---|---|---|---|
| 1440 × 900 | 9.9 %, 0.73 s | 7.5 %, 0.62 s | 7.4 %, 0.58 s | 7.6 %, 0.72 s |
| 390 × 844 | 6.4 %, 0.37 s | 4.7 %, 0.26 s | 4.4 %, 0.27 s | 5.6 %, 0.40 s |

A MutationObserver (these Part B runs only) counted **zero row-list mutations in 60 s** while paused
or pinned at both widths. The "following" mutation counts, one row in and one out per arrival
(240 / 237 adds and removes a minute at 1440 / 390), come from Part B's **graphs-hidden following
runs**: Part A's following runs had no MutationObserver.

### What the numbers do not show

- **A real user's browser.** Every number is headless `--disable-gpu` (GPU compositing
  `disabled_software`). Nothing here is the owner's desktop, a GPU-composited window or Firefox.
- **A quiet host.** One of the owner's own Chrome renderers ran at about 99 % CPU during the Task 44
  runs (task-44-report; about 97 % during Task 46b's, task-46b-report). It was not touched. The
  run-to-run spread stayed small (busy within 0.5 points at `782140e`).
- **Many samples.** Two runs per width (one per state in Part B); Event Timing has 2-8 samples per
  interaction and width.
- **The visible-browser measurement: BLOCKED.** Task 46b's visible GPU run (Part C) was invalid:
  the owner's desktop was locked with the monitor off (GNOME screensaver active, DPMS off), so the
  window got about 1 frame a second and its numbers measure nothing. GPU compositing was on; the
  launch mechanism worked. It was not retried and the session was not touched. The owner can run
  it at an unlocked desktop (about 4 min; a Chrome window appears for that time):

  ```
  GUI_PERF_VISIBLE=1 GUI_PERF_REPEATS=1 scripts/run_gui_demo.sh --m3b-perf-log <outdir>
  ```

  Or in their own Chrome, DevTools > Performance (task-46b-report, Part C):
  1. Start the simulator with the stepped demo and the API (`--api 127.0.0.1:8765`), and run
     `scripts/gui_demo_traffic.py --rate 50` until the page's ECU filter shows "All ECUs (2000)".
     Then restart it at `--rate 4`.
  2. Open the page in a normal Chrome window with the page area at 1440 × 900 (DevTools undocked,
     so it does not shrink the page). Leave the log following.
  3. DevTools > Performance: open the capture settings. Leave CPU and network throttling off and
     turn "Screenshots" off. Press Record.
  4. Over 60 s, click Pause, Resume, a filter chip off and on, scroll the log up, press Older, then
     Jump to newest. Stop the recording.
  5. Read the Summary pie (scripting / rendering / painting / idle) and the "Interactions" track.
     Each click's duration splits into input delay / processing / presentation, which compares
     with the tables above. Note chrome://gpu's "GPU compositing" line. Repeat with the window
     about 390 px wide, or the narrowest Chrome allows.
- **That the lamp change is free for clicks at 1440.** After Task 45 a click's next paint at
  1440 × 900 lands about one frame later: duration p50 48 ms against 32 before the change
  (task-45-report, both sessions and a no-`will-change` variant; reproduced in Task 46b, p95 48).
  Input delay and processing are unchanged; it is presentation. At 390 it is unchanged. A likely
  cause, not proven, is software compositing of the beat under `--disable-gpu`. **With a GPU it is
  unknown** (the visible run above was blocked).
- **That paused and pinned cost nothing.** `renderLog` still runs on every arrival (227-228 times a
  minute) to update the counters: about 0.6 s a minute at 1440 (0.58-0.62 s) and 0.26-0.27 s at
  390. The rows are untouched; the cost is the counter text writes and the layout they cause.
- **A split of the following-vs-frozen gap.** The following reference includes the clicks
  (a filter rebuild, 13 s paused or pinned), so the 2.5-point gap at 1440 cannot be divided between
  "frozen" and "no clicks".
- **The 40 % figure** is Task 39's earlier estimate from an analogue (the "Clear view" runs). It was
  neither a target nor a result, and nothing here is judged against it or against any threshold.

**Open, for the owner:** confirm the finding in their own browser (the measurement above), decide on
the 1440 click-latency question, and review the windowed log and the lamp visually
([gui-m3b-live-demo.md](gui-m3b-live-demo.md), "Remaining acceptance items").

## Task 39's investigation (history: unchanged but for the Proposal heading)

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

## Proposal (implemented, differently — see "Outcome" at the top)

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
