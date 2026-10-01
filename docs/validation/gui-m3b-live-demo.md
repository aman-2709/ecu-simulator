# GUI M3b live demo on vcan: record of checkpoint 2

**M3b is implemented, not accepted.** This record is checkpoint 2's evidence: the graphs, the page's
health model and the automated browser checks of
[gui-m3b-graphs-design.md §12.2](../plans/gui-m3b-graphs-design.md). M3b's exit is the
owner's §13 checklist below, **every box of which is unticked**. Both CSP items in it,
Chrome and Firefox, are **unverified**: the automation drives Chrome for function, not for
the owner's CSP console check, and Firefox was not run at all.

**This is a demonstration, not a benchmark.** Nothing here judges M4 performance or any of
P1–P9 ([decisions/0010 §9.2](../decisions/0010-gui-observer-api.md)). The §11.3 cost figures
below are browser measurements in headless Chrome. **The page's main-thread load is an open
finding** (its own section below). Case 21 (the restart note and a gap note together) now
keeps 5 full log rows at 1440 × 900, after two owner-authorized layout changes (Task 37, run
12; "Task 37: the shared notice line and two small layout changes" below). The full list of
what M3b still needs is under "Remaining acceptance items". **Task 41** (run 13) fixed an
owner finding: a `state` message missing `vehicle` or `dtcs` was ignored and the page stayed
Live; it is now a data fault (its own section below).

**Still open, and not touched by this record:**
- **the M2 early-check latency `STOP`** ([gui-m2-early-check.md](gui-m2-early-check.md)).
  It stays open and is not accepted; nothing here measures it;
- **the hosted-CI `CAN_ISOTP` gap.** Hosted runners have no `can_isotp`, so the vcan tests
  skip there (0010 §9.3); every check below ran on a local vcan, inside a namespace;
- **the Phase 8b gate**, unchanged;
- **the V1.0 branch rule**: `gui` is not merged into `modernization` until V1.0 is tagged.

Where the numbers come from:
- the run of record is **run 9** of `scripts/run_gui_demo.sh --m3b` plus **`m3b-long3`**, the
  `--m3b-long` run, **both on one commit, `f8a8e5f`** (Task 36). Their results are copied here,
  unchanged:
  [run9/m3b-results.json](gui-m3b-live-demo/run9/m3b-results.json),
  [run9/capture.log](gui-m3b-live-demo/run9/capture.log),
  [run9/overflow-check.txt](gui-m3b-live-demo/run9/overflow-check.txt),
  [long3/m3b-long-results.json](gui-m3b-live-demo/long3/m3b-long-results.json) and
  [long3/capture.log](gui-m3b-live-demo/long3/capture.log). Run 9's two performance traces
  are committed in [traces/](gui-m3b-live-demo/traces/) with a README;
- **run 12** (Task 37, at `5cd62fe`: the restart note on the shared notice line in the owner's
  wording, no notice bottom margin, a 4.5rem plot cap) is the record **for the cases Task 37
  changed**: "Restart reset" (11), the log-rows cases (17, 18, 19), case 21 and the new
  forced-wrap case (22), and the tally. Its files:
  [run12/m3b-results.json](gui-m3b-live-demo/run12/m3b-results.json),
  [run12/capture.log](gui-m3b-live-demo/run12/capture.log),
  [run12/overflow-check.txt](gui-m3b-live-demo/run12/overflow-check.txt). Every other case
  passed in run 12 too; its numbers below stay run 9's, which run 12 repeated;
- **run 13** (Task 41, at `6fca990`: an incomplete `state` is a data fault) is the record
  **for the three cases Task 41 added** (24, 25 and 26) and for the tally. Its files:
  [run13/m3b-results.json](gui-m3b-live-demo/run13/m3b-results.json),
  [run13/capture.log](gui-m3b-live-demo/run13/capture.log),
  [run13/overflow-check.txt](gui-m3b-live-demo/run13/overflow-check.txt), and the
  reproduction logs before and after the fix,
  [run13/task41-repro-before.log](gui-m3b-live-demo/run13/task41-repro-before.log) and
  [run13/task41-repro-after.log](gui-m3b-live-demo/run13/task41-repro-after.log). Every
  other case passed in run 13 too; their rows keep the numbers of the run they cite;
- **run 8** (the code after the final-review fix wave, page and scripts at `0c564c9`/`e7af7ca`)
  and **run 7** (the page at `391a335`) stay in [run8/](gui-m3b-live-demo/run8/) and
  [run7/](gui-m3b-live-demo/run7/) unchanged, and the earlier long run `m3b-long1` (the page at
  `b577afa`) in [long/](gui-m3b-live-demo/long/). Run 9 replaces them everywhere below except
  the screenshots, which stay run 7's but three (see "The screenshots");
- the Task 31–34 reports (`.superpowers/sdd/gui-m2-implementation/task-3[1-4]-report.md`),
  where a measurement is not in the run's files. **Task 34 has four rounds; where its
  rounds disagree, the latest round (fix round 3, run 7) is used**, and the place says so;
- one extra screenshot, the hidden section, taken for this record (see "The screenshots").

## What was run

| Item | Value |
|---|---|
| Branch, commit | Run 9, `m3b-long3`, `m3a-40` and `moving-40`: **`gui` at `f8a8e5f`**. The page is the final-review fix wave's, unchanged since `0c564c9` (`5bdbf4b`, a resync breaks the graphs, the restart note clears, no dead toggle in the fallback; `0c564c9`, the graphs start from the first `state`, not the REST snapshot). `scripts/gui_demo_capture.py` at `f8a8e5f` adds case 21 to run 8's script (`fbadbd5`, `f9fe468`, `f8a8e5f`); nothing else in it changed. Run 8 used `0c564c9` with the scripts at `e7af7ca`; run 7 the page at `391a335` and the scripts at `e479769` |
| Run 12 | **`gui` at `5cd62fe`** (Task 37): the page at `5cd62fe` (`de9972c` the restart note on the shared notice line; `9dca361` the owner's wording; `5cd62fe` no notice bottom margin, plot cap 4.5rem) and `scripts/gui_demo_capture.py` at `45701a5` (the restart checks read the shared line in the new wording; the forced-wrap case). Beside it, at the same commit: the M3a set `m3a-t37b` and the moving set `moving-t37b`, both rc 0 with every overflow line ok |
| Run 13 | **`gui` at `6fca990`** (Task 41): the page at `135f006` (an incomplete `state` is a data fault) and `scripts/gui_demo_capture.py` at `6fca990` (the wrapper's `T.send`, `T.lastState` and `T.incompleteFirst`; cases 24, 25 and 26 in part A). Beside it, at the same commit: the M3a set `m3a-41` and the moving set `moving-41`, both rc 0 with every overflow line ok |
| The long run | `m3b-long3`, on the same commit as run 9. (`m3b-long1`, the earlier record, ran on the page at `b577afa`; it also passed.) Not rerun for Task 37: its case reads only the graphs' `data-*` attributes, with no restart and no notice line, and neither the wording nor the plot height changes them |
| Date | Run 13: capture start 2026-10-01T09:27:28Z, 543 s. Run 12: capture start 2026-10-01T09:04:33Z, 532 s. Run 9: capture start 2026-10-01T05:51:19Z, 520 s. `m3b-long3`: 2026-10-01T05:59:59Z, 694 s. M3a set (`m3a-40`): 06:11:34Z. Moving set (`moving-40`): 06:13:24Z. Run 8: 2026-10-01T04:28:19Z. Run 7: 2026-10-01T03:34:31Z. The hidden shot: about 03:52 UTC the same day |
| Host | Intel Core i7-8700, 12 threads ([gui-m3b-overhead.md](gui-m3b-overhead.md), same host); kernel 6.8.0-138-generic; CPU governor `powersave` (not pinned; not a timing run) |
| Python | 3.12.12 (worktree `.venv`) |
| Browser | **Google Chrome 151.0.7922.173**, `--headless=new`, `--disable-gpu`, dpr 1, driven over the DevTools protocol. The M3b modes add `--enable-precise-memory-info`. **Chrome only**; Firefox was not run |
| Isolation | `scripts/run_gui_demo.sh`: `unshare -r -n`, a private user and network namespace with its own `lo` and `vcan0`. The host's `vcan0` and `can0` were never used. The capture script and the fault server refuse to run unless their network namespace differs from the one the launcher recorded before `unshare` |
| Servers | The real simulator (`ecu-simulator --api 127.0.0.1:8765`) for parts A and B; the fault-injection test server `scripts/gui_fault_server.py` (§12.3) for parts C, D and E |

The commands (Task 34 report, "Task 36"; all four at `f8a8e5f`, one after the other. `<scratchpad>`
is this session's scratchpad,
`/tmp/claude-1000/-home-aman-dev-personal-projects-ecu-simulator/7a26b273-9b73-4a07-94b9-2bf1f35b0923/scratchpad`):

```
scripts/run_gui_demo.sh --m3b      <scratchpad>/m3b-run9     # rc=1: 20 of 21 passed; case 21 is the finding
scripts/run_gui_demo.sh --m3b-long <scratchpad>/m3b-long3    # rc=0, 1 of 1 passed
scripts/run_gui_demo.sh            <scratchpad>/m3a-40       # rc=0, the M3a set
scripts/run_gui_demo.sh --moving   <scratchpad>/moving-40    # rc=0
```

Run 9's `--m3b` exited 1 because case 21's requirement (at least 5 rows) was not met. Run 12
(Task 37) is below:

```
scripts/run_gui_demo.sh --m3b      <scratchpad>/m3b-run12    # rc=0: 23 of 23 passed (at 5cd62fe)
scripts/run_gui_demo.sh            <scratchpad>/m3a-t37b     # rc=0 (at 5cd62fe)
scripts/run_gui_demo.sh --moving   <scratchpad>/moving-t37b  # rc=0 (at 5cd62fe)
```

Run 13 (Task 41), each into a fresh directory:

```
scripts/run_gui_demo.sh --m3b      <scratchpad>/m3b-run13    # rc=0: 26 of 26 passed (at 6fca990)
scripts/run_gui_demo.sh            <scratchpad>/m3a-41       # rc=0 (at 6fca990)
scripts/run_gui_demo.sh --moving   <scratchpad>/moving-41    # rc=0 (at 6fca990)
```

The parts of the `--m3b` run (Task 34 report, "Layout of the `--m3b` run"):
- **A:** real simulator, `ice_default.yaml` (no scenario), no traffic;
- **B:** real simulator, the stepped demo `docs/examples/ice_drive_cycle_stepped.yaml`, with
  the traffic script at 4 Hz;
- **C:** fault server, `ice_scenario.yaml`, `--nonfinite engine.coolant_temp:10:20`, no
  traffic;
- **D:** fault server, stepped demo, `--state-fault 30:35`, with traffic;
- **E:** fault server, `ice_default.yaml`,
  `--state-fault 20:25 --state-fault 45:55 --state-fault 75:85:dtcs`.

The fault server's seams are public only (controller ruling, below). Nothing under `src/`
changed for it, and no profile file holds a non-finite value.

## Result: 27 of 27 cases passed (run 13, plus the long run)

26 cases in run 13 (`run13/m3b-results.json` `tally`: `{"passed": 26, "total": 26}`), plus the
bounded-history case of `m3b-long3`. Run 13 adds Task 41's three cases (24, 25 and 26 below)
to run 12's 23, all in part A. Run 12 had 23 of 23. Case 21, which failed in run 9 (4 rows), passes in run 12
after Task 37's changes, and the new case 22 (a notice line that wraps to two lines) passes
too. Runs 7 and 8 (20 of 20 each) had neither; run 9 had 20 of 21.

Rows 24, 25 and 26 below give run 13's numbers. Rows 11, 17, 18, 19, 21 and 22 give run 12's.
Every other row gives run 9's. Runs 12 and 13 passed each of those cases too.

Every check reads only what §12.2 allows: visible text, the tags, the banner, the
read-only `data-*` attributes, the test-only WebSocket and `fetch` wrapper's records, and
`GET /status`. The wrapper is added with `Page.addScriptToEvaluateOnNewDocument`; the
shipped page contains no test code.

| # | Case (§12.2) | Part | Result | Observed in run 9, or **run 12** / **run 13** where marked (or `m3b-long3`) |
|---|---|---|---|---|
| 1 | No false invalidation | A | PASS | 14 samples over 65.08 s, every one "Live", `live`/`current`/`live`, no tag, no banner, malformed 0, episode `none`. `data-polls` rose by **32** (the rule: about 30). **1** `state` received in the whole period, on 1 socket |
| 2 | Malformed state, then unchanged data | A | PASS | Immediately: `live`/`last-known`/`malformed`/`active`, the "Last known" tags, malformed 1. **Live after 1.021 s**, on a new socket with its `hello` and `state`; episode `none`, attempts 0, tag gone; "Resynchronised after an unreadable message."; `started_at` equal; no restart marker. Seq continuity is vacuous: no traffic, no exchanges |
| 3 | Malformed, bounded | A | PASS | **3 attempts, waits 1.003 / 2.003 / 4.003 s** (± 0.3 s allowed). Then `exhausted`, "Could not recover: …" with "Retry now". Sockets 5 at exhaustion and 5 after 30 s. `data-timers` ≤ 1 at all 367 samples and every change, which holds by construction (the page has one retry-timer slot); **the evidence for timer discipline is the measured attempt spacing**. Never live meanwhile. "Retry now" with the fault removed: attempts 1, then live with attempts 0 (a fresh budget) |
| 4 | All four client slots, variant A | A | PASS | 1 attempt; its socket opened, so no 503. `data-attempts` 0→1→0. `refused_clients` 0→0. Live after 1.016 s, the budget reset. Variant A may or may not meet a 503, and its rule does not need one (see "The four-client cases"); the deterministic 503 followed by automatic recovery is variant C |
| 5 | All four client slots, variant B | A | PASS | The script's 4th client took the freed slot 0.001 s after the page's socket left (clients 4, no page socket open). **3 attempts, all refused**, starts 2.006 s and 4.006 s apart. Attempts 0→1→2→3. `refused_clients` 0→**3**, equal to the 3 attempts not opened, and unchanged over the next 30 s. Exhausted with "Retry now"; slot released, "Retry now" recovered |
| 6 | Window persists | B | PASS | `localStorage` "30". After the reload, `data-window-s` 30 on all 5 graphs; "30 s" `aria-pressed` true, 2 min and 10 min false |
| 7 | Held value at the left edge | B | PASS | 30 s window at as-of 55.01: `data-left-value` 80, line 2 "min 80 · max 80 in 30 s", segments 1, oldest-t 0.59 < 55.01 − 30 |
| 8 | Pause while buffering | B | PASS | Speed: `data-paused` true, drawn-to held at 55.01 while as-of went 55.51 → 65.51, points 17 → 22, line 1 unchanged; meta "Paused at t = 55.0 s". After Resume: drawn-to = as-of (67.01). The log ran on: last seq 206 → 248 |
| 9 | Disconnect without restart (SIGSTOP) | B | PASS | **Down after 6.44 s** (the rule: within 8 s): "Disconnected, retry in 1 s", `down`/`stale`/episode `none`, the banner, "Stale, as of 05:57:07 UTC" on 4 panels |
| 10 | … then SIGCONT | B | PASS | **Live after 1.02 s**. `started_at` equal before and after. No restart marker; "Connection lost, then resumed." present. `data-run` unchanged. All 5 graphs: gaps 0→1, segments 1→2. Shared line: "No data from t = 200.0 to t = 207.7 s (disconnected)"; every line 2 is min/max only. Seq continuity held; 752 exchanges |
| 11 | Restart reset | B | PASS | **Run 12.** `data-run` 1790845629.27 → 1790845838.35. After the first new point: points 1, oldest-t 0.583, as-of 1.08, segments 1. The shared notice line reads, matched in full: "Simulator restarted at 09:10:38 UTC; previous graph history cleared."; the log's "Simulator restarted." marker shown |
| 12 | Non-finite values | C | PASS | 97 invalid samples, at as-of 10.10 to 19.62 only. Line 1 and the table cell, read in one evaluation: "invalid value"; "Live"; malformed 0. While invalid, newest-t held at 10.10 with points 20; the next stored point was at t = 20.12 with points 21, then gaps 1, segments 2. Shared line while invalid: "Coolant: invalid value from t = 9.6 to t = 19.6 s (still invalid)"; after: "… from t = 9.6 to t = 20.1 s" |
| 13 | Encoding failure, then recovery to changed data | D | PASS | **last-known/encoding 0.25 s** after scenario t = 30; the banner names `state_encode_failed 1`. `data-conn` live for all 69 fault samples; polls 15 → 18. The fault's gap and the resync's own (see "Stated plainly"); the shared line names the latest: "No data from t = 36.2 to t = 37.2 s (reconnecting to resynchronise)". **Live 7.33 s after t = 30**, with no earlier change to live. Page ms: last `ok:false` answer 1790834307843; the qualifying `ok:true` answer 1790834310848; the recovering socket's first `state` 1790834310879; `t_live` 1790834310880 |
| 14 | Same-value recovery | E | PASS | 69 fault samples with reason `encoding`; polls 11 → 14. **Live 2.13 s** after the fault ended. 2 `state`s after the recovery (the forced push on the old socket, and the one after `hello`). A page loaded during the 45–55 s fault: health `stale` (loading) → `last-known` → `live` at 1790834368168, 1.07 s after the fault ended (1790834367098), with no change to live before it, although it had received a `state` after `hello` |
| 15 | REST is not proof (dtcs fault) | E | PASS | 49 `GET /vehicle`, all **200**. The page went last-known (encoding) and stayed there until the fault ended; live 2.18 s after t = 85 |
| 16 | Overflow (1440, 1200, 390, 2000) | B | PASS | No `scrollWidth` above `clientWidth`; table below |
| 17 | Log rows at 1440 × 900 | B | PASS | **Run 12.** **5** full rows, all exchanges (53, 53, 52, 52, 53 px). Rows region 302 px, 5 rows need 263 px: clearance **+39 px**. Graphs section 172 px, plot 68.75 px, status bar 99 px |
| 18 | Extra: log rows, disconnect break note showing | B | PASS | **Run 12.** **5** full rows (4 exchanges and the 33 px "Connection lost, then resumed." marker; traffic was stopped). Rows region 293 px against 243 px for these five: **+50 px** (about +28 px for five exchange rows). Section 181 px; the notice line one line, 16 px; status bar 99 px |
| 19 | Extra: log rows, encoding break note showing | D | PASS | **Run 12.** **5** full rows, all exchanges; rows region 287 px, 5 rows need 263 px: **+24 px**. Section 188 px; the notice line one line, 16 px, naming the resync gap; status bar 99 px, with "State encode failed" shown |
| 20 | Agreement | all | PASS | **Run 12: 16** live shots with the graphs unpaused, all agree, each read in one evaluation (run 9 had 15; run 12 adds the case 22 shot, where line 1 and the table both read "invalid value" for throttle). `m3b-b-paused.png` is listed as paused and not checked (ruling 1). 9 other shots were not checked: not live, or no graphs drawn (no scenario) |
| 21 | Log rows at 1440 × 900, the restart note and a gap note together (measured, Task 36) | B | PASS | **Run 12.** **5** full rows, all exchanges, in all four readings (three 1 s apart and one after the shot). Rows region **287 px**, 5 rows need 262–263 px: clearance **+24 to +25 px**. Graphs section 188 px; the shared notice line is **one line, 16 px**, both notices complete: "Simulator restarted at 09:10:38 UTC; previous graph history cleared. · No data from t = 3.1 to t = 9.1 s (disconnected)" (a SIGSTOP/SIGCONT of the new run at t ≈ 3, traffic running). Status bar 99 px. Screenshot: [restart-and-gap-1440.png](gui-m3b-live-demo/restart-and-gap-1440.png). (Run 9, before Task 37: 4 rows, rows region 255 px, section 219 px, the restart note on its own line) |
| 22 | Log rows at 1440 × 900, a notice line that wraps (measured, Task 37) | B | PASS | **Run 12.** The restart note, the gap and an open invalid run on the shared line, which **wraps to two lines (31 px)**, every notice complete: "Simulator restarted at 09:10:38 UTC; previous graph history cleared. · No data from t = 3.1 to t = 9.1 s (disconnected) · Throttle: invalid value from t = 12.4 to t = 15.4 s (still invalid)" (as the screenshot shows; the case's reading was taken at t = 13.1). **5** full rows, all exchanges, in all four readings. Rows region **271 px**; clearance **+6, +8, +8, +7 px** (5 rows need 263–265 px, as rows are 52 or 53 px). **The worst case is +6 px**, with all five rows at 53 px. Graphs section 203 px, status bar 99 px. The invalid run comes from the test-only WebSocket wrapper rewriting each state (throttle sent as `null` and listed in `nonfinite`, as the API sends a non-finite value), so the simulator stays finite and the traffic can run; the fault server's `--nonfinite` cannot run beside the traffic script (DEV-26). Screenshot: [restart-gap-invalid-wrap-1440.png](gui-m3b-live-demo/restart-gap-invalid-wrap-1440.png) |
| 23 | Bounded history (`--m3b-long`) | `ice_scenario.yaml`, traffic | PASS | `m3b-long3`. 70 samples to as-of 690.6, the 10 min window on all graphs. Every sample: points ≤ cap 4096 (largest: engine_load 2330), points-before-window ≤ 1. 9 samples at as-of ≥ 610: before-window 1 and left-value set on every graph. Coolant, constant from t = 60 in this profile: oldest-t 60.25, older than the window in all 9, with 1 point and before-window 1. No page console entry |
| 24 | Incomplete state, then unchanged data (Task 41) | A | PASS | **Run 13.** A healthy page was given its latest real `state` without `dtcs`, then one without `vehicle`. Each time, immediately: `live`/`last-known`/`malformed`/`active`, malformed-total +1 (2, then 3), the "Last known" tags, and a banner beginning "Last known data. The simulator sent an incomplete state message (2 malformed so far) …". **Live after 1.015 s** both times, on 1 new socket. The first `health=live` change has the same page millisecond as the new socket's first `state` (the rule is "not before"; the clock resolves 1 ms). Then episode `none`, attempts 0, no tag. The log has "Resynchronised after an incomplete state message." twice; no restart marker; `started_at` equal |
| 25 | Unknown message type ignored (Task 41) | A | PASS | **Run 13.** `{"type":"future"}` on a healthy page: immediately and 2.5 s later "Live", `live`, episode `none`, malformed-total 3 (unchanged), 4 sockets (unchanged), no banner. No change to `data-health` or `data-episode` was recorded |
| 26 | Incomplete state during an attempt (Task 41) | A | PASS | **Run 13.** An unreadable frame started an episode. The wrapper removed `dtcs` from the first attempt's `state`, which ended that attempt; the second attempt started **2.003 s** later, and its valid `state` recovered. `data-attempts` 1, 2, then 0; Live 3.045 s after the fault. The resync line read "Resynchronised after an unreadable message and an incomplete state message." |

Page console, run 13: 4 entries, the same 503 handshakes (variant B 3, variant C 1); the
M3a set `m3a-41` has 2 `ERR_CONNECTION_REFUSED` entries while its simulator is stopped, and
`moving-41` none. Page console, run 12: 4 entries, the 503 handshakes of variant B (3) and variant C (1, Task 38), which the cases expect. Run 9: 3 entries, variant B's;
no page exception. The console log holds no Content-Security-Policy entry, which is **not**
the owner's CSP check (§13). `m3b-long3`: no entries. The M3a set (`m3a-40`) has 3 `ERR_CONNECTION_REFUSED` entries, all while its simulator is stopped (earlier M3a runs had 2: how many reconnect attempts fall in the stopped interval varies); `moving-40` has none. Every overflow line of both is ok.

**Overflow and status bar** (run 9, `overflow-check.txt`, the same values as runs 7, 8 and 12;
scrollWidth/clientWidth; status bar height from the `header` element):

| Viewport | `#graphs` | each `.uplot` | html, body | `#vehicle` | Status bar |
|---|---|---|---|---|---|
| 1440 × 900 | 1010/1010 | 179/179 | 1440/1440 | 378/378 | 99 px |
| 1200 × 900 | 773/773 | 171/171 | 1200/1200 | 378/378 | 92 px |
| 2000 × 1100 | 1442/1442 | 262/262 | 2000/2000 | 498/498 | 115 px |
| 390 × 844 | 372/372 | 342/342 | 390/390 | 372/372 | 372 px (stacked, one column) |

The 1200 px status bar was read in steady state only (Task 34, fix round 3). Task 33
measured it at 1200 with long values and both health readouts: 92 px throughout.

## Task 41: an incomplete `state` message is a data fault

**The finding (owner, 2026-10-01, on the pushed `0a3f14f`).** `onState()` returned without
a word when a `state` message's `vehicle` or `dtcs` was not an object, unless a recovery
attempt was in flight. A healthy page stayed "Live" and kept showing its previous data. This
was Task 32's interpretation 3, accepted then; the owner overrides it.

**Reproduced first, through the capture's own WebSocket wrapper** (`T.send`, added for this
task). A throwaway driver, `<scratchpad>/repro41.py` (not committed), reused
`gui_demo_capture.py`'s helpers in the usual namespace: the real simulator on
`ice_default.yaml`, no traffic, headless Chrome. On a healthy page it sent the latest real
`state` frame with `dtcs` removed, then with `vehicle` removed, then `{"type":"future"}`. In
the page before the fix (`df64091`), every reading, immediately and 3 s later, was the
healthy one: `conn` live, `data` current, `reason` empty, `health` live, `episode` none,
`attempts` 0, `malformed-total` 0, "Live", no tag, no banner, 1 socket, and no log marker
([run13/task41-repro-before.log](gui-m3b-live-demo/run13/task41-repro-before.log)).

**The fix (`135f006`).** A recognised `state` that cannot be applied takes the malformed path.
By the controller's ruling it uses the same requirement kind and episode as an unreadable
frame (ruling 9 below):
- it is counted in `data-malformed-total`, and the data is last known at once (reason
  `malformed`);
- it starts a resync, with the same budget and "Retry now" on exhaustion;
- during a recovery attempt it ends that attempt, as before;
- the banner, the log's resync line and the graphs' break note say "an incomplete state
  message", where an unreadable frame gives "an unreadable message";
- unknown message types are still ignored, for compatibility.

The malformed readout's tooltip now names both kinds. The same driver after the fix
([run13/task41-repro-after.log](gui-m3b-live-demo/run13/task41-repro-after.log)):
- each incomplete frame gave `last-known`/`malformed`/`active` at once, with malformed-total 1,
  then 2;
- each was "Live" again within 3 s on a new socket, with episode `none` and attempts 0;
- `{"type":"future"}` changed nothing.

**The regression cases (`6fca990`), run 13:** cases 24, 25 and 26 above, all PASS.

## Task 37: the shared notice line and two small layout changes

**Why.** In run 9, case 21 failed: with the restart note and a gap note shown together at
1440 × 900, the log kept 4 full rows. The owner asked for a fix, with 5 rows **including when
the notice line wraps**, and no notice hidden, truncated or clipped. The owner authorized a
small layout change with reasonable clearance. Three changes were made, all on the page:
1. **The restart note joins the shared notice line** (`de9972c`). It leads the line, and the
   gap and invalid-run notices follow, joined with " · ". It no longer takes a line of its own.
2. **The owner's wording** (`9dca361`): "Simulator restarted at HH:MM:SS UTC; previous graph
   history cleared." For an `as_of` that went back, the controller's parallel form is "Scenario
   time went back at HH:MM:SS UTC; previous graph history cleared." These replace §6.8's longer
   sentence. They are short enough to share one line with a gap note at 1440. The gap notices
   are unchanged and complete.
3. **Two small layout changes** (`5cd62fe`): the notice line has **no bottom margin** (it was
   0.286 rem, about 4.4 px), and the plot height's cap is **4.5rem**, down from 4.75rem
   (`clamp(3rem, 9vh, 4.5rem)`; the min and the vh are unchanged).
   - Without them, a notice line that wraps to two lines (the restart note, a gap and an open
     invalid run) left 4 full rows in 2 of 4 readings (run 11, at `45701a5`).
   - Nothing else in the desktop layout changed, and the line still wraps.

**The plot height now:**
- **68.75 px at 1440 × 900** (canvas 68 px; it was 72.6 px), from run 12's readings;
- **80.1 px at 2000 × 1100** (canvas 80 px; it was 84.5 px), from a **separate measurement**
  at `5cd62fe` (a throwaway script, `<scratchpad>/plot37.py`, not committed), because no case
  of the run reads it at 2000;
- each card is 110.9 px tall at 1440 (it was 114.8 px).

**Log rows at 1440 × 900, run 12.** The clearance is the rows region minus what the 5 newest
full rows take. Where a case takes four readings, all are given.

| Case | Full rows | Rows region | 5 rows need | Clearance | Graphs section | Notice line |
|---|---|---|---|---|---|---|
| 17, steady | 5 | 302 px | 263 px | **+39 px** | 172 px | none |
| 18, disconnect gap | 5 | 293 px | 243 px (one 33 px marker row) | **+50 px** (about +28 px for five exchange rows) | 181 px | 1 line, 16 px |
| 19, encoding gap | 5 | 287 px | 263 px | **+24 px** | 188 px | 1 line, 16 px |
| 21, restart + gap | 5, 5, 5, 5 | 287 px | 262–263 px | **+24 to +25 px** | 188 px | 1 line, 16 px |
| 22, restart + gap + open invalid run | 5, 5, 5, 5 | 271 px | 263–265 px | **+6, +8, +8, +7 px** | 203 px | **2 lines, 31 px** |

**The worst case is +6 px**: the notice line wraps to two lines and all five rows are 53 px. Every
reading still had 5 full rows. The controller accepted it; an earlier +8 px target was the
controller's, not the owner's.

## The four-client cases: what each variant proves

The capture script holds three WebSocket clients, so the page is the fourth. Then it sends the
page a malformed frame, and the page closes its own socket to resync. The three variants differ
only in what happens to the slot that close frees.

| Variant | What the script does with the freed slot | What it proves | Evidence |
|---|---|---|---|
| **A** | Leaves it free | <ul><li>A resync under full load recovers inside its budget, and the budget then resets.</li><li>`data-timers` stays at 1 or below, attempts are at least 0.9 s apart, and `data-attempts` rises by one per attempt.</li><li>**A 503 is possible but not required.** The page's own close usually frees its slot before the 1 s first wait, so A has met no 503 in runs 3 to 10. Its rule does not need one: "any 503 is counted … and matches one attempt" holds just as well when there are none.</li></ul> | Case 4, run 9 |
| **B** | Takes it at once and keeps it | <ul><li>Every attempt is refused, so 3 attempts get a 503.</li><li>The episode then ends "exhausted" with "Retry now", and no further upgrade request is made for 30 s, so `refused_clients` stops rising.</li><li>Releasing the slot and pressing "Retry now" recovers. This is the manual path.</li></ul> | Case 5, run 9 |
| **C** (Task 38) | Takes it at once, waits until the server's `refused_clients` rises (the page's first attempt got a 503), then releases it | <ul><li>**The refusal is deterministic, not raced:** the slot is held before the attempt, and released only once the refusal has been observed.</li><li>**The page recovers automatically inside the same episode, without "Retry now":** its next scheduled attempt, 2 s after the refused one ended, succeeds.</li><li>The budget then resets (`data-episode` `none`, `data-attempts` 0). A second malformed frame starts a fresh episode at attempt 1.</li><li>Timers, spacing and the refusal count are checked as in A and B.</li></ul> | `--m3b-slots`, three runs at `f2b4ef9`, and inside `--m3b` run 10 (below) |

**Variant C's evidence.** The commit is `f2b4ef9`, with the page unchanged since `de9972c`. The
results are in [variant-c/](gui-m3b-live-demo/variant-c/): `slots-1`, `slots-2` and `slots-3`,
each `scripts/run_gui_demo.sh --m3b-slots`, all 3 of 3 passed. It also passed inside a full
`--m3b`, run 10. Every run gave the same picture:

| Run | `refused_clients` | Attempt starts after the fault | Refused / recovered | Wait after the refusal | Live after the fault | Afterwards | Second fault |
|---|---|---|---|---|---|---|---|
| slots-1 | 3 → 4 (seen at +1.023 s; slot released at +1.024 s) | +1.000 s, +3.007 s | attempt 1 refused (1006) / **attempt 2 recovered** | 2.005 s | 3.056 s | live, episode `none`, attempts 0 | episode `active`, attempts 1, then `none`/0; live after 1.020 s |
| slots-2 | 3 → 4 (+1.025 / +1.026 s) | +1.002 s, +3.008 s | 1 refused / **2 recovered** | 2.005 s | 3.059 s | the same | the same; live after 1.020 s |
| slots-3 | 3 → 4 (+1.026 / +1.027 s) | +1.001 s, +3.007 s | 1 refused / **2 recovered** | 2.005 s | 3.061 s | the same | the same; live after 1.022 s |
| run 10 (`--m3b`) | 3 → 4 (+1.036 / +1.036 s) | +1.000 s, +3.006 s | 1 refused / **2 recovered** | 2.005 s | 3.074 s | the same | the same; live after 1.025 s |

Across all four runs:
- the script took the slot 0.002 s after the page's socket left, with 4 clients and no page socket open;
- `data-attempts` went 0 → 1 → 2 → 0;
- `data-timers` was never above 1;
- the refusal shows in the page console as one 503 handshake entry.

Run 10 is not the run of record. Run 10's other cases repeated run 9's results: 21 of 22
passed, with the same case 21 failing at 4 rows. (Task 37's run 12, above, is the record for the
cases it changed.)

## Cost in the browser (§11.3): measured next to §11's estimates

Stepped demo, traffic at 4 Hz, the 2 min window, dpr 1, 60 s at each size, headless
`--disable-gpu`. **The measured values are run 9's** (`m3b-results.json` `cost`, and the
committed traces). Run 8's are in brackets where the controller's notes quote them.

| | §11 estimate (**estimate**) | Measured, 1440 × 900 | Measured, 390 × 844 |
|---|---|---|---|
| Rings | 320 KiB fixed (5 × 4096 × 2 × 8 B) | Not measured separately. The code allocates two `Float64Array(4096)` per ring, five rings (Task 33), which is the estimate's arithmetic | same |
| Canvases | 1440, dpr 1: 190 × 73 px, about 0.27 MiB. dpr 2: about 1.1 MiB. 390 at dpr 3: about 5.2 MiB | 5 × 179 × 72 px = **0.246 MiB** (dpr 1, run 9, at the 4.75rem cap). Since Task 37 (4.5rem cap, run 12): 5 × 179 × 68 px = 0.232 MiB | 5 × 342 × 66 px = **0.431 MiB** (dpr 1, run 9). Run 12: 5 × 342 × 63 px = 0.411 MiB. dpr 2 and 3 were not measured |
| JS heap used (start, every 10 s, end) | Graphs' share: under 2 MiB at dpr 1 (whole graphs feature, not the page) | 2.58, 2.62, 2.65, 3.17, 2.96, 3.08, 3.01 MiB (whole page) | 2.86, 3.14, 3.06, 3.31, 3.13, 3.05, 3.33 MiB (whole page) |
| JS heap total | — | 3.95–5.21 MiB | 4.45–4.71 MiB |
| Graph redraws (trace: the rAF callback, `app.js` line 857) | About 1 % of one core at 4 redraws a second | 234 redraws, **29.9 ms in 60 s** (about 0.05 % of one core); median 0.121 ms, max 0.23 ms [run 8: 233, 26.6 ms] | 235 redraws, **31.1 ms**; median 0.127 ms, max 0.26 ms [run 8: 232, 26.8 ms] |
| `FireAnimationFrame` total | — | 167.3 ms | 177.4 ms |
| Main thread busy (trace, union of events) | No estimate (§11 estimates the graphs only) | **30.98 s, 51.6 %** of the window [run 8: 47.7 %] | **42.35 s, 70.5 %** [run 8: 67.2 %] |
| `Performance.getMetrics` `TaskDuration` Δ (cross-check) | — | 33.19 s, 55.3 % | 44.82 s, 74.6 % |
| `ScriptDuration` / `LayoutDuration` / `RecalcStyleDuration` Δ | — | 2.23 / 8.74 / 3.18 s | 3.79 / 18.11 / 6.04 s |
| Trace file (gzip, committed) | — | [m3b-trace-1440x900.json.gz](gui-m3b-live-demo/traces/m3b-trace-1440x900.json.gz), 1,504,837 B | [m3b-trace-390x844.json.gz](gui-m3b-live-demo/traces/m3b-trace-390x844.json.gz), 1,240,608 B |

- **The graphs cost far less than estimated:** about 30 ms of drawing per 60 s at either
  size, against §11.2's estimate of about 1 % of one core.
- The heap readings are for the whole page; the graphs' share of it was not separated.
- The browser shared the host's CPUs with the simulator, the traffic script and Chrome.
- **Server side:** checkpoint 1's overhead is recorded in
  [gui-m3b-overhead.md](gui-m3b-overhead.md), measured in-process; nothing here adds to it.
- **The traces are in git** (Task 36): [gui-m3b-live-demo/traces/](gui-m3b-live-demo/traces/),
  with a README naming the commit (`f8a8e5f`), their sizes and SHA-256, and how to open them
  (Perfetto, DevTools, `chrome://tracing`). Run 8's traces were never committed and stay only in
  the session scratchpad.

## Open finding: the page's main-thread load (mostly M3a's log rebuild)

**This is an open finding, not a measurement to note and move past.** With the traffic script
at 4 Hz, the page keeps Chrome's main thread busy **about half the time at 1440 × 900 and about
70 % at 390 × 844** (run 9: 51.6 % and 70.5 % by the trace; 55.3 % and 74.6 % by
`TaskDuration`). Nearly all of it is M3a's exchange log, not the graphs.

From the committed traces (`CrRendererMain`, 60 s each):

| | 1440 × 900 | 390 × 844 |
|---|---|---|
| Main thread busy | 30.98 s | 42.35 s |
| M3a's log rebuild: the `renderLog` timer (`app.js` line 1431), 227 calls, about 3.8 a second | **12.52 s** (55 ms a call) | **26.12 s** (115 ms a call) |
| Paint / PrePaint / Layout (whole page, most of it the log's table) | 15.31 / 8.16 / 8.72 s | 6.99 / 10.44 / 18.10 s |
| The WebSocket `onmessage` handler (`app.js` line 246) | 0.39 s | 0.48 s |
| **The graphs**: the rAF redraw (line 857) | **0.03 s** | **0.03 s** |
| uPlot's own code (`uPlot.iife.min.js`) | 0.12 s | 0.13 s |

- **The cause in the code:** `scheduleRender()` rebuilds the whole log table, up to 2,000 rows,
  with `replaceChildren` at most every 200 ms whenever anything arrives (`renderLog`). That is
  M3a behaviour, unchanged by M3b. The time inside the call includes the forced layout of the
  rebuilt table, which is why it doubles in the narrower, taller 390 px layout.
- **The graphs' share is about 0.1 % of the busy time** (30 ms of 31 s).
- **Not judged here, and not fixed:** no target exists for it (§11 estimates the graphs only),
  headless `--disable-gpu` Chrome on a shared host is not a real user's browser, and a change
  would be to M3a's log, outside M3b's scope. It needs an owner decision: accept it as M3a
  behaviour, or schedule a log change (for example appending rows instead of rebuilding the
  table) with its own acceptance.
- **Investigated in [gui-m3b-main-thread.md](gui-m3b-main-thread.md) (Task 39):** at the log's 2,000-row cap the page is saturated (about 100 %) at both widths; it proposes a fix, not implemented.

## Rulings made during checkpoint 2 (controller, for the owner to confirm or reverse)

Each of these was decided by the controller during checkpoint 2, not by the owner.

1. **Agreement covers unpaused live screenshots only.** §6.9 freezes line 1 while the
   graphs are paused and the signal table moves on, so §12.2's Agreement rule and its
   Pause rule cannot both hold for a paused shot (Task 34, "Contradictory pass rules":
   speed 80 against 48 in `m3b-b-paused.png`). Paused shots are covered by the Pause case.
2. **Break and gap notes are on one shared line under the graphs head**, not in each
   card's line 2. Since Task 37 the restart note leads the same line (owner's decision; see
   "Task 37"). This departs from §6.7's wording. It was made for §5.2's 5-log-rows
   requirement: per-card notes wrapped every card to about 161.6 px and left 3 full rows
   at 1440 × 900 (Task 34, run 3). The shared line shows the latest ended break, then any
   invalid run still open (`4271882`, `391a335`); see "Stated plainly" for what it does not
   describe.
3. **The status bar, from 1101 px wide:** the polled readouts are one-line items in a
   reserved space, and the malformed count sits under Connection (`c359440`). Measured:
   99 px at 1440 and 92 px at 1200, stable with long values and both health readouts
   (Task 33). Before it, the bar grew from 91 px to 142 px (malformed readout) and 187 px
   (encode-failed readout), leaving 4 and 3 full rows. This changes M3a's layout, for the
   same requirement.
4. **From 1101 to about 1260 px wide the five cards wrap 4 + 1** (the specified
   `minmax(11rem, 1fr)`), which leaves 2–3 log rows at 1200 × 900 (Task 33: 3 rows steady,
   2 with a health readout or the gap line). Accepted because §5.2 states the 5-row
   requirement at 1440 × 900 only. **Option for the owner:** a 9.5rem minimum, which keeps
   one row of cards from about 1150 px.
5. **Spec gap, accepted interpretation:** a malformed frame that arrives while only an
   encoding requirement is pending adds the reason and defers the resync to the `ok:true`
   poll, whose resync must meet both rules (Task 32, interpretation 6). Since the final
   review it also breaks the graphs at once (`5bdbf4b`). No §12.2 case exercises it.
6. **Harness seams:** `--state-fault` replaces the public module functions
   `snapshots.state_message`, `snapshots.dtcs` or `snapshots.vehicle`; `--nonfinite` wraps
   `runtime.runner.apply` on the instance captured through the public `app.build_runtime`.
   Nothing under `src/` changed for the harness.
7. **Measured:**
   - the 5-row margin at 1440 × 900 (run 12): +24 px with the encoding gap note, +24 to +25 px
     with the restart note and a gap note together (case 21), and **+6 px in the worst case**,
     when the notice line wraps to two lines (case 22). Before Task 37 (run 9), case 21 had
     4 rows;
   - the main-thread load is now an **open finding** in its own section above (Task 36);
   - variant A has met no 503 (runs 3 to 10), and its rule does not need one. Since Task 38,
     variant C produces a 503 deterministically and checks the automatic recovery after it.
8. **Local environment only:** for §12.4's wheel check, Task 31 bootstrapped `pip` into the
   worktree `.venv` with `ensurepip` (it had none). No project file changed.
9. **An incomplete `state` is a malformed message (Task 41).** The owner required that a
   recognised `state` which cannot be applied be a data fault through the existing bounded
   recovery. The controller ruled that it uses the same requirement kind and episode as an
   unreadable frame: reason `malformed`, one count, one budget. The wording ("an incomplete
   state message" against "an unreadable message") tells them apart. This overrides Task 32's
   interpretation 3.

## Stated plainly (final review)

- **Where the graphs break.** A gap, drawn as a break and never as a held line, starts:
  - when the page goes down (§6.7);
  - when a malformed or encoding fault puts the data in "last known" (§8.4), or a malformed
    frame arrives while only an encoding fault is pending;
  - **and at every resync**: when the page closes its socket to reconnect for a complete
    state, nothing arrives until the next socket's `state`, so that interval is a gap too,
    "No data from t = A to t = B s (reconnecting to resynchronise)" (`5bdbf4b`).
  - A break already pending is kept, so a resync straight after a fault adds no second gap.
    When the fault's gap has already ended before the resync (the encoding recovery's first
    good state on the old socket, case 13), the resync makes its own: two gaps.
- **The shared line names only the latest ended gap, then each invalid run still open.**
  Earlier gaps in a long window are drawn but not described in words.
- **After an exhausted episode whose socket was left open**, a later close of that socket does
  not reconnect automatically. The page stays at "Retry now", the manual state of §14.1 C9
  (owner-accepted): it reconnects only when "Retry now" is pressed.
- **`data-timers` ≤ 1 holds by construction:** the page has one retry-timer slot, so that
  reading cannot fail. The evidence that retries are disciplined is the measured attempt
  spacing: 1.003 / 2.003 / 4.003 s in case 3, and 2.006 / 4.006 s in case 5 (run 9).
- **Found and fixed in the final review (`0c564c9`):** the graphs used to take the first REST
  snapshot (`GET /vehicle`). It is read live, so at page load it can be newer than the
  published `state` that follows `hello`; that `state`'s `as_of` then went back, and the
  graphs cleared themselves with "Scenario time went back …" on an ordinary load or reload.
  The graphs now start from the first `state`. Within a run, published `state`s never go
  back, and a real restart is still caught by `started_at`.
- **The restart note** ("Simulator restarted at HH:MM:SS UTC; previous graph history
  cleared.", or "Scenario time went back at HH:MM:SS UTC; previous graph history cleared.")
  clears once the window no longer reaches back to the new run's start (`5bdbf4b`). Since
  Task 37 it leads the shared notice line, " · "-joined with any gap.
  - With a gap note beside it, the line is one line at 1440, and 5 full rows remain (case 21,
    run 12: +24 to +25 px).
  - When the line wraps to two lines (with an open invalid run as well), 5 full rows still
    remain, at +6 px in the worst reading (case 22).
  - Before Task 37 the note took its own line, and case 21 had 4 rows (run 9).

## §12.4, the wheel (Task 31)

`.venv/bin/python -m pip wheel --no-deps -w <scratch>/wheel .` built
`ecu_simulator-0.1.0.dev0-py3-none-any.whl` (157,493 B, not committed). `unzip -l` lists
`ecu_simulator/api/static/uPlot-LICENSE.txt` (1,078 B), `uPlot.iife.min.js` (51,081 B) and
`uPlot.min.css` (1,857 B), matching §5.1. Their SHA-256 values match §5.1 and are pinned by
`tests/unit/api/test_frontend_files.py`.

## The screenshots

All in [gui-m3b-live-demo/](gui-m3b-live-demo/), copied unchanged and renamed. All are at
1440 × 900 unless the name says otherwise. Each shot's run is in its "Source" column:
- **Run 12** (the current page, `5cd62fe`): `restart-note-1440.png`, `restart-and-gap-1440.png`
  and `restart-gap-invalid-wrap-1440.png`. Task 37 changed what these show (the note's
  wording and place).
- **Run 8:** `break-note-encoding-1440.png`.
- **Taken for this record:** `hidden-1440.png`.
- **Run 7** (the page at `391a335`): all the others.

**Shots from runs 7 and 8, and the hidden shot, show the earlier plot-height cap** (4.75rem: a
72.6 px plot at 1440, against 68.75 px now) and the notice line's old bottom margin. Otherwise
they match the current page; none of them shows a restart note.

| File | Size | Source | What it shows |
|---|---|---|---|
| [normal-1440.png](gui-m3b-live-demo/normal-1440.png) | 226,743 B | run 7 `m3b-b-live-1440.png` | **Normal, 1440 × 900.** Live, scenario t ≈ 199 s, the 2 min window. Five cards in one row: speed 70, rpm 2100, throttle 45, load 75, coolant 78.04, each line 1 equal to the table |
| [normal-1200.png](gui-m3b-live-demo/normal-1200.png) | 188,463 B | run 7 `m3b-b-graphs-1200.png` | **1200 × 900** (ruling 4): the cards wrap 4 + 1; 3 log rows. 30 s window |
| [normal-2000.png](gui-m3b-live-demo/normal-2000.png) | 332,238 B | run 7 `m3b-b-graphs-2000.png` | **2000 × 1100:** one row of five; the head on one line. 30 s window, t ≈ 29 s |
| [normal-390.png](gui-m3b-live-demo/normal-390.png) | 44,606 B | run 7 `m3b-b-graphs-390.png` | **390 × 844**, scrolled to the graphs: one graph per row; the head on two rows plus the "history starts at t = 0.6 s" line |
| [hidden-1440.png](gui-m3b-live-demo/hidden-1440.png) | 231,004 B | taken for this record | **Hidden:** "Signal graphs — hidden, still recording" and "Show graphs"; the log takes the space. Read at the shot: `#graphs` hidden, `aria-expanded` false, as-of 32.65 rising while drawn-to stayed 30.14, `data-health` live, no page exception ([hidden-capture.log](gui-m3b-live-demo/hidden-capture.log)) |
| [paused-1440.png](gui-m3b-live-demo/paused-1440.png) | 228,305 B | run 7 `m3b-b-paused.png` | **Paused:** "Paused at t = 55.2 s", "Resume graphs"; the graphs frozen (speed line 1 80) while the table moved on (speed 48) and the log runs to seq 248. Not in the Agreement check (ruling 1) |
| [stale-sigstop-1440.png](gui-m3b-live-demo/stale-sigstop-1440.png) | 227,313 B | run 7 `m3b-b-sigstop-stale.png` | **Stale:** after SIGSTOP, "Disconnected, retry in 1 s", the banner with "Retry now", "Stale, as of 03:40:19 UTC" on the trouble codes, vehicle, graphs and log panels; the graphs stay drawn |
| [gap-after-sigcont-1440.png](gui-m3b-live-demo/gap-after-sigcont-1440.png) | 229,440 B | run 7 `m3b-b-sigcont-break.png` | **After SIGCONT:** Live; the shared line "No data from t = 200.0 to t = 208.0 s (disconnected)"; a break in every graph; "Connection lost, then resumed." in the log; no restart marker |
| [last-known-encoding-1440.png](gui-m3b-live-demo/last-known-encoding-1440.png) | 171,846 B | run 7 `m3b-e-loaded-during-fault.png` | **Last known:** a page loaded during an encoding fault (part E, no scenario). "Connected, last known data" with an amber outline lamp, the banner naming `state_encode_failed 52`, "State encode failed 52" in the status bar, "Last known, 03:42:20 UTC" on the trouble codes, vehicle and graphs panels, distinct from "Stale"; the no-scenario note in the graphs |
| [last-known-exhausted-1440.png](gui-m3b-live-demo/last-known-exhausted-1440.png) | 192,161 B | run 7 `m3b-a3-malformed-exhausted.png` | **Last known, budget exhausted:** "Could not recover: … The page made 3 attempts …" with "Retry now"; "Malformed messages 5, last 03:35:45 UTC" under Connection |
| [invalid-value-1440.png](gui-m3b-live-demo/invalid-value-1440.png) | 171,450 B | run 7 `m3b-c-nonfinite-invalid.png` | **Invalid value:** coolant "invalid value" in its card and in the table, the shared line "Coolant: invalid value from t = 9.6 to t = 14.1 s (still invalid)", the page "Live" |
| [break-note-encoding-1440.png](gui-m3b-live-demo/break-note-encoding-1440.png) | 226,510 B | **run 8** `m3b-d-log-rows-with-break-note.png` | **After the encoding fault:** Live; two gaps in every graph, the fault's and the resync's; the shared line names the latest, "No data from t = 35.9 to t = 37.2 s (reconnecting to resynchronise)"; "State encode failed 20" in the status bar; 5 full log rows (case 19) |
| [restart-note-1440.png](gui-m3b-live-demo/restart-note-1440.png) | 229,412 B | **run 12** `m3b-b-restart.png` | **Restart note:** the shared notice line "Simulator restarted at 09:10:38 UTC; previous graph history cleared.", the graphs cleared and starting again, "history starts at t = 0.6 s (when this run started)", and the log's "Simulator restarted." marker |
| [restart-and-gap-1440.png](gui-m3b-live-demo/restart-and-gap-1440.png) | 230,227 B | **run 12** `m3b-b-restart-and-gap-1440.png` | **Restart note and a gap together (case 21):** one notice line, "Simulator restarted at 09:10:38 UTC; previous graph history cleared. · No data from t = 3.1 to t = 9.1 s (disconnected)"; a gap in every graph; 5 full log rows |
| [restart-gap-invalid-wrap-1440.png](gui-m3b-live-demo/restart-gap-invalid-wrap-1440.png) | 229,381 B | **run 12** `m3b-b-restart-gap-invalid-1440.png` | **A notice line that wraps (case 22):** the restart note, the gap and "Throttle: invalid value from t = 12.4 to t = 15.4 s (still invalid)" on two lines, every notice complete; throttle reads "invalid value" in its card and the table; 5 full log rows (the open invalid run comes from the test-only wrapper, see case 22) |
| [no-scenario-1440.png](gui-m3b-live-demo/no-scenario-1440.png) | 150,769 B | run 7 `m3b-a1-no-scenario-65s.png` | **No scenario:** `ice_default.yaml` after 65 s alone: "No scenario: the values are constant, as configured. Graphs follow scenario time.", no plots, "Live" |

Total: 16 screenshots, 3,309,868 B. The directory, with runs 7, 8, 9 and 12's results and
logs, both long runs' results and run 9's two traces, is 6,773,999 B. Every other run-7,
run-8, run-9 and run-12 shot, and the 10 min shots of `m3b-long1` and `m3b-long3`, stay in the
scratchpad (not durable). The record's numbers are all in the committed results files, except
the 2000 × 1100 plot height (a separate measurement, "Task 37" above).

**How the hidden shot was taken.** No run of record has one. A throwaway script,
`<scratchpad>/hide35.py` (not committed), reused `gui_demo_capture.py`'s helpers in the
same kind of namespace (`unshare -r -n`, `lo` up, a private `vcan0`, under `setsid`): the
real simulator on the stepped demo, the traffic script at 4 Hz, headless Chrome at
1440 × 900, at commit `e479769`. At as-of ≥ 30 it clicked "Hide graphs", waited 2 s and
took the shot. It printed `HIDE PASS`, rc 0, and every process it started was stopped.

## Remaining acceptance items

**M3b is implemented, not accepted.** Every item below is still open. None is ticked by this
record or by the automated run.

The owner's checklist (§13), every box. Each is copied in full under "The owner's manual
checklist" below:
- [ ] **CSP, Chrome:** load the page, open the console, and watch one full 90 s cycle. There
      must be no Content-Security-Policy error. The automation's console log is not this check.
- [ ] **CSP, Firefox:** the same, with the Firefox version recorded. Manual only; Firefox has
      not been run at all.
- [ ] The five graphs, their units, the rpm row and the VIN as text.
- [ ] Steps, not ramps; the coolant staircase.
- [ ] The 90 s boundary.
- [ ] At least 5 usable log rows at 1440 × 900; Hide graphs; the section open again after a reload.
- [ ] The three windows; the choice survives a reload; the page works at 2 min with site data blocked.
- [ ] Pause graphs against Pause view.
- [ ] Stop and start: stale, then the restart note, with no joined line.
- [ ] `kill -STOP` / `kill -CONT`.
- [ ] No scenario, left alone for a few minutes.
- [ ] **The fault server with `--nonfinite`.** It cannot be run from a host browser as written:
      the harness refuses to run outside the capture's private namespace, which a host browser
      cannot reach. **The owner decides how** (for example, by accepting the screenshots, or by
      allowing a host run on `vcan0`).
- [ ] **The fault server with `--state-fault`.** Same as the `--nonfinite` item: it cannot be run
      as written, and the owner decides how.
- [ ] 390 px.
- [ ] 2000 px.
- [ ] The uPlot licence link.
- [ ] The measured cost against the estimates. The graphs come in under them; the page's
      main-thread load is the open finding below.

Findings and untested paths:
- [ ] **The open main-thread finding** (section above). The page is busy about 52 % at
      1440 × 900 and 71 % at 390 × 844 with traffic, almost all of it in M3a's log rebuild. It
      needs an owner decision: accept it as M3a behaviour, or schedule a log change.
- Resolved, not an acceptance item (Task 37): **case 21**, 4 full log rows while the restart
      note and a gap note showed together at 1440 × 900 (run 9). The fix was the owner's
      wording on the shared notice line, no notice bottom margin, and a 4.5rem plot cap. Run 12
      has 5 rows in case 21 (+24 to +25 px), and 5 rows with a wrapped two-line notice line in
      case 22, with +6 px in the worst reading.
- Resolved, not an acceptance item (Task 41): **an incomplete `state` was ignored** and a
      healthy page stayed Live with its old data. It was reproduced in the browser, fixed in
      `135f006`, and checked by cases 24 to 26 in run 13.
- Resolved, not an acceptance item (Task 38): "the 503 path of variant A has never been met".
      Variant A's rule does not need a 503. **Variant C** produces one deterministically, and shows automatic
      recovery without "Retry now", the budget reset, and a fresh episode at attempt 1. It
      passed in 3 `--m3b-slots` runs and inside `--m3b` run 10 (see "The four-client cases").

Gates that stay open whatever happens to M3b:
- [ ] **The M2 early-check median-latency `STOP`** ([gui-m2-early-check.md](gui-m2-early-check.md)).
- [ ] **The hosted-CI `CAN_ISOTP` gap.** The vcan tests skip on hosted runners (0010 §9.3).
- [ ] **The Phase 8b gate.**
- [ ] **The V1.0 branch rule:** `gui` is not merged into `modernization` until V1.0 is tagged.

## Owner startup commands for the §13 checklist

§13 asks for a vcan host, `--api 127.0.0.1:8080`, the stepped demo and the traffic script.
On the host network, from the worktree. **Never use `can0`.** The host's `vcan0` is shared
with anything else on it, including any simulator already running there.

```
cd /home/aman/dev/personal-projects/ecu-simulator/.claude/worktrees/gui

# terminal 1
.venv/bin/ecu-simulator --profile docs/examples/ice_drive_cycle_stepped.yaml --interface vcan0 --api 127.0.0.1:8080

# terminal 2
.venv/bin/python scripts/gui_demo_traffic.py --interface vcan0

# browser (Chrome, then Firefox)
http://127.0.0.1:8080/
```

For the no-scenario item, restart terminal 1 with
`--profile src/ecu_simulator/profiles/ice_default.yaml`.

**Finding: the two fault-server items cannot be run as written.**
`scripts/gui_fault_server.py` refuses to run outside `scripts/run_gui_demo.sh`'s private
network namespace (it compares its namespace with `GUI_DEMO_HOST_NETNS`), and a browser
on the host cannot reach that namespace's loopback. So the `--nonfinite` and
`--state-fault` items of §13 can be seen only in the screenshots above
(`invalid-value-1440.png`, `last-known-encoding-1440.png`, `break-note-encoding-1440.png`).
Running them in the owner's own browser needs a decision; nothing was changed here.

## The owner's manual checklist (§13, the M3b exit): every box unticked

Copied from [gui-m3b-graphs-design.md §13](../plans/gui-m3b-graphs-design.md). **None of
these was ticked by this record, and none is ticked on the strength of the automated run.**

On a vcan host, `--api 127.0.0.1:8080`, the stepped demo, with the traffic script running
unless stated.

**CSP, both browsers. Unverified until someone actually runs it. M3b is not accepted
before these two items are ticked:**
- [ ] **Chrome:** load the page, open the console, watch one full 90 s cycle. No
      Content-Security-Policy error; the graphs draw. **Unverified.** The automation drove
      headless Chrome 151 for function; it did not perform this console check.
- [ ] **Firefox:** the same. Record the Firefox version. (Firefox is a snap on this host
      and cannot run in the namespace automation, so this is manual only.) **Unverified.**
      Firefox was not run at all.

**Views:**
- [ ] Five graphs: speed km/h, engine speed rpm, throttle %, engine load %, coolant °C. The
      signal table's rpm row shows "rpm". The VIN is still text in the vehicle header.
- [ ] Speed and rpm are drawn as steps, not ramps. Coolant is a fine staircase to about
      240 s, then flat.
- [ ] Across the 90 s boundary, the idle lines run flat; the next rise is at 96 s.
- [ ] At 1440 × 900 with the section open, at least 5 log rows are visible and usable.
      "Hide graphs" gives the log the space back; a reload opens the section again.
- [ ] 30 s, 2 min and 10 min change all five graphs; the choice survives a reload. With
      site data blocked, the page still works at 2 min.
- [ ] "Pause graphs" freezes the graphs only; the log keeps running; after 30 s, resume
      shows the latest data, with nothing missing inside the window. "Pause view" on the
      log does not pause the graphs.
- [ ] Stop the simulator: the graphs stay, marked stale. Start it again: they clear with
      the restart note; no line joins the runs.
- [ ] `kill -STOP` for 12 s: within about 8 s the page says "Disconnected". `kill -CONT`:
      it comes back "Live" with a gap in the graphs, the log says "Connection lost, then
      resumed", and there is no restart marker.
- [ ] `ice_default.yaml` (no scenario), left alone for a few minutes: "No scenario: the
      values are constant, as configured", no plots, and the page stays "Live".
- [ ] The fault-injection server with `--nonfinite`: "invalid value" in the table and the
      coolant graph, a gap, and the page still "Live". (See the finding above: the harness
      runs only inside the namespace.)
- [ ] The fault-injection server with `--state-fault`: "Last known" on the vehicle, DTC and
      graphs panels, distinct from "Stale", and the exchange log still running. After the
      fault, "Live" again. (The same finding.)
- [ ] 390 px: one graph per row, the head on two rows, no horizontal scroll.
- [ ] 2000 px: one row of five.
- [ ] The footer's uPlot licence link opens the MIT text.
- [ ] The measured cost in the live-demo record (§11.3), and the server overhead from
      checkpoint 1 (§17), are in line with the estimates, or the difference is explained.

## Not exercised by any automated check

- **Firefox**, and the CSP in either browser (the two items above).
- **The `unavailable` card** ("unavailable, no source" in a graph): the shipped profiles
  cannot reach it (Task 33); its code was reviewed, not run.
- **The uPlot fallback** ("Graphs unavailable: the chart library did not load …", with no
  "Hide graphs" button since `5bdbf4b`) was run only in Task 33's throwaway smoke, with
  `Network.setBlockedURLs`, not in the run of record.
- **The malformed-while-encoding-pending path** (ruling 5): no case exercises it; its graph
  break was checked only in Task 33's node ring check.
- **A resync after an exhausted episode's "Retry now"** with graphs drawn: the resync gap is
  exercised in the browser only in the encoding case (13); the "Retry now" cases run without
  a scenario, so no graphs.
- **dpr 2 and dpr 3** canvas sizes: every measurement is at dpr 1.
- **Variant A's 503 path** (case 4): never met, and not needed by its rule. The 503 followed by
  automatic recovery is exercised deterministically by variant C (Task 38).
