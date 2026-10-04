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
Live; it is now a data fault (its own section below). **Tasks 42-46** changed the exchange log
to a bounded, navigable window of 200 matching exchanges and moved the live lamp's beat to
compositor-only properties; on headless numbers the main-thread finding is **mitigated, and it
stays open** until the owner confirms it in their own browser ("Tasks 42-46" below, and
[gui-m3b-main-thread.md](gui-m3b-main-thread.md), "Outcome"). **Tasks 47-51** (final head `25e6852`) moved
Older / Newer / Jump to newest into the log header, fitted the filter bar on one row, made the log
counter silent and moved every announcement into one hidden announcer; the final runs passed
`--m3b` 36 of 36, `--m3b-log` 22 of 22 and `--m3b-slots` 3 of 3 ("Tasks 47-51" below). The
ordered list of what the owner still has to do is "Owner checklist (ordered)".

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
- **run 14** (at `532e9c5`: case 26 now reads the resync mark it added itself, and requires
  it to name both causes; the review found that the whole-log search could not fail) is the
  record **for case 26**. Its files:
  [run14/m3b-results.json](gui-m3b-live-demo/run14/m3b-results.json),
  [run14/capture.log](gui-m3b-live-demo/run14/capture.log),
  [run14/overflow-check.txt](gui-m3b-live-demo/run14/overflow-check.txt). It passed 26 of 26;
  the page changed only in a comment;
- **run 8** (the code after the final-review fix wave, page and scripts at `0c564c9`/`e7af7ca`)
  and **run 7** (the page at `391a335`) stay in [run8/](gui-m3b-live-demo/run8/) and
  [run7/](gui-m3b-live-demo/run7/) unchanged, and the earlier long run `m3b-long1` (the page at
  `b577afa`) in [long/](gui-m3b-live-demo/long/). Run 9 replaces them everywhere below except
  the screenshots, which stay run 7's but three (see "The screenshots");
- the Task 31–34 reports (`.superpowers/sdd/gui-m2-implementation/task-3[1-4]-report.md`),
  where a measurement is not in the run's files. **Task 34 has four rounds; where its
  rounds disagree, the latest round (fix round 3, run 7) is used**, and the place says so;
- one extra screenshot, the hidden section, taken for this record (see "The screenshots");
- **the windowed log (Tasks 42-46):** the two final `--m3b-log` runs, Task 46a's fix-round runs
  (harness `c18c132`, page `3593be7`), copied unchanged:
  [log-run1/m3b-log-results.json](gui-m3b-live-demo/log-run1/m3b-log-results.json),
  [log-run1/capture.log](gui-m3b-live-demo/log-run1/capture.log),
  [log-run2/m3b-log-results.json](gui-m3b-live-demo/log-run2/m3b-log-results.json) and
  [log-run2/capture.log](gui-m3b-live-demo/log-run2/capture.log); the Task 42-46 reports
  (`.superpowers/sdd/gui-m2-implementation/task-4{2,3,4,5,6a,6b}-report.md`); and the
  screenshots of the new log and the lamp taken for this record (see "The screenshots").
- **the final page (Tasks 47-51, head `25e6852`):** `--m3b` run `m3b-51b` (36 of 36), `--m3b-log`
  run `log51b` (22 of 22) and `--m3b-slots` run `slots-51b` (3 of 3), copied unchanged into
  [m3b-51b/](gui-m3b-live-demo/m3b-51b/), [log51b/](gui-m3b-live-demo/log51b/) and
  [slots-51b/](gui-m3b-live-demo/slots-51b/) (results JSON and `capture.log`); they supersede the
  earlier tallies for every case they run. The Task 47-51 reports are local and untracked too.
  **Those reports are local and untracked** (`.superpowers/` is in the repository's
  `.git/info/exclude`). In the repository the log cases can be checked against the two
  committed `log-run*/` files, and the measurements only as
  [gui-m3b-main-thread.md](gui-m3b-main-thread.md), "Outcome", lists: the committed Task 39
  baselines and Task 46b's committed results. The rest rests only on the local reports (that
  list names which).

## What was run

| Item | Value |
|---|---|
| Branch, commit | Run 9, `m3b-long3`, `m3a-40` and `moving-40`: **`gui` at `f8a8e5f`**. The page is the final-review fix wave's, unchanged since `0c564c9` (`5bdbf4b`, a resync breaks the graphs, the restart note clears, no dead toggle in the fallback; `0c564c9`, the graphs start from the first `state`, not the REST snapshot). `scripts/gui_demo_capture.py` at `f8a8e5f` adds case 21 to run 8's script (`fbadbd5`, `f9fe468`, `f8a8e5f`); nothing else in it changed. Run 8 used `0c564c9` with the scripts at `e7af7ca`; run 7 the page at `391a335` and the scripts at `e479769` |
| Run 12 | **`gui` at `5cd62fe`** (Task 37): the page at `5cd62fe` (`de9972c` the restart note on the shared notice line; `9dca361` the owner's wording; `5cd62fe` no notice bottom margin, plot cap 4.5rem) and `scripts/gui_demo_capture.py` at `45701a5` (the restart checks read the shared line in the new wording; the forced-wrap case). Beside it, at the same commit: the M3a set `m3a-t37b` and the moving set `moving-t37b`, both rc 0 with every overflow line ok |
| Run 13 | **`gui` at `6fca990`** (Task 41): the page at `135f006` (an incomplete `state` is a data fault) and `scripts/gui_demo_capture.py` at `6fca990` (the wrapper's `T.send`, `T.lastState` and `T.incompleteFirst`; cases 24, 25 and 26 in part A). Beside it, at the same commit: the M3a set `m3a-41` and the moving set `moving-41`, both rc 0 with every overflow line ok |
| Run 14 | **`gui` at `532e9c5`**: case 26's resync-mark check (review minor) and a comment in `app.js` (the break listeners' `incomplete` cause); the page's behaviour is `135f006`'s |
| The long run | `m3b-long3`, on the same commit as run 9. (`m3b-long1`, the earlier record, ran on the page at `b577afa`; it also passed.) Not rerun for Task 37: its case reads only the graphs' `data-*` attributes, with no restart and no notice line, and neither the wording nor the plot height changes them |
| Date | Run 14: capture start 2026-10-01T09:43:59Z, 543 s. Run 13: capture start 2026-10-01T09:27:28Z, 543 s. Run 12: capture start 2026-10-01T09:04:33Z, 532 s. Run 9: capture start 2026-10-01T05:51:19Z, 520 s. `m3b-long3`: 2026-10-01T05:59:59Z, 694 s. M3a set (`m3a-40`): 06:11:34Z. Moving set (`moving-40`): 06:13:24Z. Run 8: 2026-10-01T04:28:19Z. Run 7: 2026-10-01T03:34:31Z. The hidden shot: about 03:52 UTC the same day |
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
scripts/run_gui_demo.sh --m3b      <scratchpad>/m3b-run14    # rc=0: 26 of 26 passed (at 532e9c5)
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

**Final tally (Task 51b, head `25e6852`): `--m3b` 36 of 36** in run `m3b-51b`
([m3b-51b/m3b-results.json](gui-m3b-live-demo/m3b-51b/m3b-results.json) `tally`:
`{"passed": 36, "total": 36}`): the 26 cases below, which all passed again, plus 9 announcement
cases and the layout-by-state case ("Tasks 47-51" below), **plus** the bounded-history case of
`m3b-long3` (not rerun: its case reads only the graphs' `data-*` attributes). The table below keeps
the numbers of the runs it cites; where Tasks 47-51 changed what a case reads (the log rows cases,
the banner, the count line), `m3b-51b` is the run of record.

26 cases in run 13 (`run13/m3b-results.json` `tally`: `{"passed": 26, "total": 26}`), plus the
bounded-history case of `m3b-long3`. Run 13 adds Task 41's three cases (24, 25 and 26 below)
to run 12's 23, all in part A. Run 12 had 23 of 23. Case 21, which failed in run 9 (4 rows), passes in run 12
after Task 37's changes, and the new case 22 (a notice line that wraps to two lines) passes
too. Runs 7 and 8 (20 of 20 each) had neither; run 9 had 20 of 21.

Rows 24 and 25 below give run 13's numbers, and row 26 run 14's (run 14 also passed 26 of 26). Rows 11, 17, 18, 19, 21 and 22 give run 12's.
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
| 26 | Incomplete state during an attempt (Task 41) | A | PASS | **Run 14.** An unreadable frame started an episode. The wrapper removed `dtcs` from the first attempt's `state`, which ended that attempt; the second attempt started **2.003 s** later, and its valid `state` recovered. `data-attempts` 1, 2, then 0; Live 3.046 s after the fault. **The resync mark this case added**, the last after its fault, reads "Resynchronised after an unreadable message and an incomplete state message." (run 13 searched the whole log, which case 24 had already written to, so it could not fail) |

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

**The regression cases (`6fca990`), run 13:** cases 24, 25 and 26 above, all PASS. Case 26's
log check was tightened after review (`532e9c5`) and is cited from run 14.

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

## Tasks 42-46: the windowed exchange log, the `--m3b-log` cases and the lamp

The owner's fix for the main-thread finding (plan: "M3b main-thread fix: a bounded, navigable
exchange log"). Browser only; retention stays 2,000 exchanges; no API, diagnostic-path or M2
latency change. The design, every measurement and what they do not show are in
[gui-m3b-main-thread.md](gui-m3b-main-thread.md), "Outcome". This section is what a reader of
the page sees, and the committed browser cases.

### How the log behaved at `3593be7` (the controls and focus changed in Tasks 47-51; see "Tasks 47-51" below)

- **A window of 200 matching exchanges** (`LOG_WINDOW`), chosen after the ECU, service and outcome
  filters are applied to all 2,000 retained. While **following**, it ends at the newest and slides.
  The reader's own scroll pins it at the newest row shown; arrivals are then counted, not drawn.
- **Controls inside the table**, real buttons: a row at the top, "1,800 older exchanges match your
  filters." with **Show older exchanges**; when pinned with newer ones, a row at the bottom,
  "35 newer exchanges match your filters." with **Show newer exchanges** and **Jump to newest**.
  Each press moves the window by up to 100 matching exchanges (`LOG_STEP`) and keeps the row the
  reader was looking at in the window, at its place unless the box reaches a scroll limit; when
  the window's far end is already in view the press scrolls the box there instead, so no press
  does nothing. The header control reads, for example,
  "11 rows below + 35 beyond this window, jump to newest". Jump to newest follows again.
- **The count line:** "200 of 2,000 shown (seq 2,529–2,728), last seq 2728 (live)";
  with a filter, "200 of 250 matching shown (seq 2,062–2,261; 2,000 retained)".
- **Four kinds of absent rows, each in its own words:** outside this window ("… match your
  filters", "beyond this window"); hidden by filters ("634 exchanges hidden by filters (not a
  gap)"); gap markers and connection notes, drawn where they fall in the window and otherwise
  counted ("1 gap marker and 1 connection note in older rows"); and rows that left the 2,000 cap
  (the existing "… older rows left this view." note, which after a pinned reader's rows have left
  too adds "Rows this view was showing left too, so it moved to the oldest exchanges kept.").
- **Pause, Clear view, payload expansion** keep their meaning. Paused or pinned, the row list is
  not touched; only counters change.

(Example texts: fix-round run 1, 1440 × 900, [log-run1/m3b-log-results.json](gui-m3b-live-demo/log-run1/m3b-log-results.json).)

### The `--m3b-log` mode (Task 46a)

```
scripts/run_gui_demo.sh --m3b-log <outdir>      # about 9 min; m3b-log-results.json and capture.log; rc 1 on any failed case
```

Set-up: the real simulator on the stepped demo in a private namespace with its own `vcan0`,
headless Chrome 151; the log filled to 2,000 retained at 50/s; per width (1440 × 900, then
390 × 844 on the same page), 250 OBD mode 0x0A requests sent by the harness (a service the traffic
cycle never uses), then a burst of 400 so they lie older than the window, then the standard rate.
The only harness code in the page is a MutationObserver on `#log-body`, a `focusout` counter and a
WebSocket wrapper that can keep N events from the page (for a real seq gap). Controls are driven
by real clicks, wheel scrolls and Enter on a focused button; the service `<select>` by value and a
`change` event (task-46a-report, "The mode").

**The runs at `3593be7`: 20 of 20 each** (superseded by the final run `log51b`, 22 of 22, in "Tasks 47-51" below; Task 46a fix round 1, `c18c132`; served `app.js`
`4c2419ca…`, the shipped page): run 1 started 2026-10-01T22:56:24Z, run 2 23:05:35Z; both exit 0;
tally `{"1440x900": 9/9, "390x844": 9/9, "page": 2/2}`. The two earlier runs of the same cases
(`d0e4ba7`) also passed 20 of 20 (task-46a-report).

| # | Case (conditions at 1440 / 390) | What it proves (run 1, 1440 unless stated) |
|---|---|---|
| 1 | Live following (10 / 10) | Over 10 s the window slides and holds 200 exchange rows at every sample, the newest drawn and equal to the count line's last seq; one row in and one out per arrival (38 arrivals, 38 adds, 38 removes, nothing else); zero mutations with the traffic stopped; under a filter none of the arrivals match, zero row adds or removes |
| 2 | Pause / resume, Clear view (13 / 13) | Paused 20 s: zero row mutations, identical text, the held count rising 3 → 22 → 41 → 60 → 80 and equal to the arrivals; Resume brings every held row in one batch (81 / 81) and follows; Clear view and Show cleared rows keep their meaning |
| 3 | Filtering across all retained (19 / 19) | Service 0x0A finds its 250 exchanges although all are older than the unfiltered window ("200 of 250 matching shown …"; Older reaches all 250); hidden runs worded "(not a gap)"; one rebuild per change; a focused Reset filters keeps focus through arrivals, then restores every filter |
| 4 | Older-history navigation (16 / 16) | A wheel scroll pins: over 8 s the same rows, every visible row within 1 px, zero row mutations, "beyond this window" 5 → 35 equal to the Newer row; Older / Newer clicks move a full 100 with the anchor within 0.4 px; Jump to newest follows; walking Older to the oldest and Newer back to the newest, 28 presses each way, no press silent, together reaching all 2,000 retained |
| 5 | Eviction past the cap (14 / 14) | Following, the trimmed count rises by exactly the arrivals (1,024 → 1,352 for 328); pinned while the reader's whole window leaves the cap, 200 rows at all 49 samples, contiguous, still pinned, ending at the oldest kept with the re-pin sentence; Jump removes the sentence |
| 6 | Reconnect and gap markers (11 / 11) | A real gap ("Gap: seq 5378–5380 not received (3).") and "Connection lost, then resumed." drawn where they fall; moved outside the window, counted exactly ("1 gap marker and 1 connection note in newer rows", later "in older rows"), never both drawn and counted; a restart drawn between runs, the count line "… across a restart" |
| 7 | Payload expansion kept (5 / 5) | A row opened with "show all" leaves the window with Older and comes back with Newer still expanded, same 20 bytes |
| 8 | The four absent-row wordings (9 / 9) | Each kind appears in its own situation and its own row carries no other kind's phrase; in every state read, matching = older + drawn + newer |
| 9 | Layout (7 / 5) | No horizontal overflow (page, `#log-panel`, `#logwrap`, `#log-state`) following, filtered, with nothing matching and pinned; at 1440 × 900, 5 full log rows with the graphs open |
| — | Page: the wording is the served page's (1) | Every phrase the cases match is in the served `app.js` |
| — | Page: no exception or console message (1) | None over the whole run |

At 390 the cases share the page and buffer with 1440, so several 390 texts carry width 1's
markers (for example "… ; 1 gap marker and 2 connection notes in older rows"); the rules allow
and check for that (task-46a-report, Concerns). The other modes on the new page all passed
(M3a, `--moving`, `--m3b` 26 of 26, `--m3b-slots` 3 of 3; task-46a-report).

### The lamp (Task 45)

The live lamp's beat is the same 2 s rhythm on a ring (`.conn--live .conn__lamp::after`) animated
on `transform: scale(1.2)` and `opacity` instead of the lamp's `box-shadow`, only under
`prefers-reduced-motion: no-preference` (task-45-report Part B; the report and its shots are
local and untracked, so this comparison rests on them only). Task 45 compared shots before
(`84c0637`) and after:
- **The same, byte for byte:** the lamp fully faded (1,000 ms into the beat), last known, refused,
  disconnected, and the change flash at 390.
- **Different pixels, the same look:** the live lamp at 0 and 500 ms and under reduced motion; side
  by side the same glow, ring size and fade, the differences being antialiasing of the ring edge.
  Reduced motion still shows a still 3 px glow.

The cost it removed and the one-frame click-presentation increase at 1440 it brought (headless) are
in [gui-m3b-main-thread.md](gui-m3b-main-thread.md), "Outcome".

## Tasks 47-51: the log header, the filter bar and the announcements (final page, `25e6852`)

The owner's follow-ups of 2026-10-03/04 (plan: "M3b follow-ups after the log-fix review", Tasks
47, 47b, 48, 49, 51; the reports `task-47-49-report.md` and `task-51-report.md` are local and
untracked). The page's `app.js` last changed in `de52666`; the final runs below served it
(SHA-256 `528dbdda…`) at head `25e6852`.

### How the log behaves now

- **The window, the follow / pin rule and the four absent-row kinds are as above.** The step rule
  is unchanged: Older / Newer move by up to 100 matching exchanges in overlapping steps; the step
  shrinks so the reader's anchor row stays in the window, and a press with the window's far end
  already on screen scrolls the box there instead of moving the window.
- **Older, Newer and Jump to newest are together in the log header** (Task 47), in that order, with
  the count beside Jump as plain text (`#log-below`, for example "10 rows below + 29 beyond this
  window"). Visible texts "Older", "Newer", "Jump to newest"; accessible names "Older exchanges",
  "Newer exchanges", "Jump to newest exchange and follow new ones" (the last described by the
  count). An unavailable direction is `aria-disabled="true"`, never `disabled`, and a press on it
  does nothing. The rows at the window's edges are plain text that points at the header ("1,800
  older exchanges match your filters. Use Older in the log header to show them.").
- **Focus** (replaces the earlier rule): after Older or Newer the focus **stays on the pressed
  header button**, however often it is pressed; after Jump to newest, which hides itself when the
  log follows again, the focus goes to the log box (`#logwrap`). After Retry now, when the button
  hides or is disabled, the focus goes to the banner text (`#linkstate-text`), else to the log box
  (Task 51b). Focus is never left on `body` by these actions.
- **The count line is shorter**: "200 of 2,000 shown, last seq 2116"; filtered "200 of 250
  matching, last seq N"; after Clear "13 exchanges, last seq N". It no longer names the window's
  seq range, the retained count or "(live)": the rows show their seqs, the ECU and service filters
  show "All ECUs (2000)", the Connection readout shows "Live" (the Stale tag otherwise), and a
  restart stays visible as the log's "Simulator restarted." marker row and the graphs' restart
  note. `#log-count` is no longer a live region (Task 48).
- **The filter bar fits one row at 1440 × 900** (75 px, was 125; Task 47b) with the visible texts
  "Pause" / "Resume" / "Clear"; their accessible names stay "Pause view" / "Resume view" /
  "Clear view". Tab order follows the visual order: Older, Newer, (Jump to newest), ECU, Service,
  the four outcome chips, Pause, Clear, the log box.
- **Expansions leave with their rows** (Task 49): an expanded payload's state is dropped when its
  exchange leaves the 2,000 cap. With no exchange shown, a scroll no longer leaves the log "not
  following, not pinned": it stays following.
- **Announcements** (Task 51, 51b): one visually hidden `#announce` (`role="status"`,
  `aria-live="polite"`, `aria-atomic="true"`, a child of `body`) carries every transition; each
  announcement replaces the previous one and is cleared 10 s later. The banner `#linkstate` is no
  longer a live region; its retry countdown and "N s ago" still tick visibly, outside any live
  region. The exact strings (task-51-report "Exact announcement strings", 51b numbering):
  "Disconnected. Reason: …", "Reconnecting: attempt N.", "Attempt N failed: …", "Last known
  data: …", "Recovery attempt K of 3 under way.", "Could not recover: …", "Retrying: a new recovery
  of 3 attempts starts.", and on recovery "Connection restored; data current." (after an outage
  that included a disconnect) or "Live data restored." (a data fault without one). Nothing is said
  while the data stays last known, nor on a healthy first load or a healthy poll.

### The final runs (head `25e6852`)

| Run | Started (UTC) | Result | Files |
|---|---|---|---|
| `--m3b` | 2026-10-04T10:32:00Z | exit 0, **36 of 36** (26 earlier cases, 9 announcement cases, the layout-by-state case); the 4 log entries are the expected variant C 503 handshakes | [m3b-51b/m3b-results.json](gui-m3b-live-demo/m3b-51b/m3b-results.json), [capture.log](gui-m3b-live-demo/m3b-51b/capture.log), [overflow-check.txt](gui-m3b-live-demo/m3b-51b/overflow-check.txt) |
| `--m3b-log` | 2026-10-04T10:45:59Z | exit 0, **22 of 22** (1440: 11 of 11, adding case 10 "No exchange shown: nothing to pin" and case 11 "Live regions"; 390: 9 of 9; page 2 of 2); 0 problems | [log51b/m3b-log-results.json](gui-m3b-live-demo/log51b/m3b-log-results.json), [capture.log](gui-m3b-live-demo/log51b/capture.log) |
| `--m3b-slots` | 2026-10-04T10:45:09Z | exit 0, **3 of 3** (B start gaps 2.006 / 4.006 s; C 2.006 s) | [slots-51b/m3b-slots-results.json](gui-m3b-live-demo/slots-51b/m3b-slots-results.json), [capture.log](gui-m3b-live-demo/slots-51b/capture.log) |
| M3a | 2026-10-04T10:56:11Z | exit 0; overflow ok at 390, 2000 and 1440; the 2 expected `ERR_CONNECTION_REFUSED` while its simulator is stopped | scratchpad `m3a-51b/` (not committed) |
| Full suite (namespace, `-rfE`, once) | — | 1339 passed, 69 skipped, 2 xfailed | scratchpad `suite-51b.out` (not committed) |

Task 51b reports these as the final matrix (`task-51-report.md`, "Task 51b"). `--moving` was last
run at `f9ca7cd` (exit 0, `moving-51/`), before Task 51b's page change.

### Layout by state at 1440 × 900 (the committed case)

`--m3b` case "Layout by state at 1440 x 900 (Task 51, final measurement)",
[m3b-51b/m3b-results.json](gui-m3b-live-demo/m3b-51b/m3b-results.json). Graphs open; the fewest
full rows of each state's readings; clearance = the rows region minus the 5 newest full rows.
Part B's buffer is under 2,000 exchanges (count lines "200 of 747 shown" …).

| State | Header px | Full rows (exchange) | Rows region px | Clearance px |
|---|---|---|---|---|
| Steady following | 42.2 | 6 (6) | 359 | +96 |
| Pinned, "beyond this window" count | 42.2 | 6 (6) | 359 | +97 |
| Disconnect-gap note | 42.2 | 6 (6) | 343 | +79 |
| Encoding-gap note (fault server) | 42.2 | 6 (6) | 337 | +74 |
| Restart + gap notes | 42.2 | 6 (6) | 337 | +73 |
| Wrapped three-notice line | 42.2 | 6 (6) | 321 | +57 |
| Filtered + pinned | 42.2 | 6 (5; one hidden-run row) | 359 | +94 |
| Filtered, following | 42.2 | 6 (6) | 359 | +96 |
| Paused (held line, 58 px) | 42.2 | 5 (5) | 301 | +37 |
| Stale (banner, 64 px) | 42.2 | 5 (5) | 295 | **+30** |
| Last known (banner, 64 px) | 42.2 | 5 (5) | 295 | +33 |

**Every listed state shows at least 5 full exchange rows; the minimum clearance is +30 px
(stale).** Recorded, not required (combinations): "Could not recover" banner 5 rows (+17); stale
with a recent gap note 5 (+16); last known with a recent gap note 5 (+15); and two that fall short:

- **REMAINING DEFICIT, for the owner to rule on (no layout change was made):**
  - **paused while stale: 4 full rows** (banner 64 px + held line; rows region 237 px, about
    27 px short of five);
  - **stale while pinned: 4 full rows**: the log header wraps to two lines (72.1 px, the Stale
    tag beside "13 rows below + 1 beyond this window"); the region (265 px) holds five, but only
    four were fully visible at the pinned offset.
- **The instant of a fault:** a "Last known" reading taken in the same task as the fault, before
  the box re-scrolls, shows 4 rows with partial rows at both ends; the case reads every 250 ms
  through a whole episode and keeps the fewest (26 readings), which was 5 (task-47-49-report,
  Task 47b notes; task-51-report).
- **1200 px:** the log table scrolls sideways inside its box (`#logwrap` 895 / 773). This is
  pre-existing: the baseline tree before Task 47 shows the same (task-47-49-report). The page
  itself has no horizontal overflow at 1200, 1440, 2000 or 390.

### Findings resolved (plain lines, not ticked boxes)

Automation verifies markup, DOM mutations, focus owners and the text and timing of each
announcement. **It does not verify what a screen reader says**; the items marked below need an
actual listen with a screen reader (for example NVDA on Windows or Orca on Linux).

- **Keyboard access to Newer.** Automation verified: real Tab order pinned `btn-older, btn-newer,
  btn-follow, f-ecu` and following `btn-older, btn-newer, f-ecu`; focus stays on the pressed
  Older / Newer through 28-press walks each way; an aria-disabled end press changes nothing; Jump
  to newest leaves focus on `#logwrap` (`log51b` case 4). Needs your observation: the keyboard
  walk in a real browser; a screen-reader listen to the three button names and the count
  beside Jump. The earlier "(WCAG 2.4.11)" code comment went with the old focus rule (no longer in
  `app.js`).
- **`#log-count` no longer announces.** Automation verified: the only live region on the page is
  `#announce`; no continuously updating element (26 checked) has a live-region ancestor; over 20 s
  of traffic the counter changed 75 times and `#announce` 0 times (`log51b` case 11). Needs a
  screen-reader listen: silence while the log runs.
- **`view.expanded` pruned on eviction.** Automation verified: the kept-expansions count rises by
  one on "show all", holds while the row is out of the window, and returns to 0 once the row left
  the cap (1 → 1 → 0 at both widths, `log51b` cases 5 and 7). Needs your observation: none.
- **The "not following, not pinned" state.** Automation verified: with nothing matching and
  marker rows scrolled, the log stays following (Jump hidden, Newer unavailable); after Reset
  filters it follows, newest at the bottom, the window sliding (`log51b` case 10). Needs your
  observation: none.
- **The ticking countdown and "N s ago".** Automation verified: during a 24 s outage the banner's
  ticks changed each second while `#announce` had 0 mutations between transitions
  (`m3b-51b`, "Announcements: a disconnect, its retries and the recovery"). Needs a screen-reader
  listen: the countdown is not read out every second.
- **The recovery announcement.** Automation verified: exactly one "Connection restored; data
  current." after a disconnect (also when the outage began at page load), exactly one "Live data
  restored." after a data fault (also on a first state that was incomplete), none while data stays
  last known, none on a healthy load or over 65 s healthy, never the same text twice in a row
  (`m3b-51b` announcement cases). Needs a screen-reader listen: whether a quick pair ("Recovery
  attempt 1 of 3 under way." then "Live data restored." a few ms later) is read in full or only
  the last, and whether clearing after 10 s cuts a long message.
- **Retry now focus.** Automation verified: after Enter on Retry now the focus owner is the banner
  text, `linkstate-text`, not `body` (`m3b-51b`, bounded case). Needs your observation: the
  keyboard flow in the fault check; a later recovery hides the banner with focus on its text, and
  then focus falls to `body` (outside the requirement; review note, Task 51b).

## Open finding: the page's main-thread load (mostly M3a's log rebuild)

**Status (Tasks 42-46): mitigated on headless numbers, still OPEN.** On the shipped page
(`3593be7`) at the 2,000-exchange cap and 3.8 requests/s, the main thread is busy 9.7-10.0 % at
1440 × 900 and 6.3-6.4 % at 390 × 844, with no task over 50 ms, against 99.9-100 % before
(task-46b-report Part A, committed as `gui-m3b-perf/results/perf46b-A-following.json`;
task-44-report baseline, which is local and untracked: the committed before-side figures are
Task 39's, 99.8-100 %). These are headless `--disable-gpu` runs on a
shared host; the visible-browser run was blocked by the locked desktop. The finding stays open
until the owner confirms it in their own browser ("Remaining acceptance items"). The text below
is the finding as recorded at run 9, kept as history.

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
- **Taken for this record:** `hidden-1440.png`; and, on the final page (`25e6852`), the `log-*`,
  `stale-banner-*` and `lamp-*` shots in their own table below. Every other shot predates the log fix,
  so its log's count line and controls are the old ones.
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

### The windowed log, the stale banner and the lamp (retaken on the final page, Task 50)

No `--m3b-log` run saves screenshots, so these were taken for this record on the **final page,
head `25e6852`** (served `app.js` `528dbdda…`, `app.css` `993b33d3…`, the worktree files), on
2026-10-04 at about 11:02-11:08 UTC. They **replace** the Task 46c set of 2026-10-01, which showed
the page at `3593be7` (Older / Newer rows inside the table, "Pause view" / "Clear view" texts, the
earlier banner). A throwaway script (`<scratchpad>/shots50.py`, not committed) reused
`gui_demo_capture.py`'s helpers in the same kind of namespace (`unshare -r -n`, `lo` up, a private
`vcan0`, nothing on the host network): the real simulator on the stepped demo, the log filled to
2,000 retained at 50/s, then the traffic script at 4 Hz; headless Chrome 151 at 1440 × 900 and
390 × 844. **No harness code was injected into the page.** States were entered by real input (a
click on Pause, a real wheel scroll to pin, a burst past the cap), except two harness scrolls of
the log box to its top, made more than 1 s after any input so the page takes them as layout
scrolls. The stale banner and the connection note come from a real SIGSTOP / SIGCONT of the
simulator (traffic stopped, so the note stays at the window's end); the last-known lamp from the
fault server (`--state-fault 15:300`). The live lamp's beat was paused at 0 ms by the Web
Animations API. The page's text was read straight after each shot
([log-shots-facts.json](gui-m3b-live-demo/log-shots-facts.json); step log
[log-shots-capture.log](gui-m3b-live-demo/log-shots-capture.log)); no exception or console
message. Every process the script started was stopped by its PID, and the namespace closed.

The 1440 log shots are clipped to the log panel down to the log box's bottom; the 390 shots are
the viewport. The 390 "top of the window" shot was taken but not committed, to keep the set under
about 1.2 MB (it is in the facts file).

| File | Size | What it shows |
|---|---|---|
| [log-following-1440.png](gui-m3b-live-demo/log-following-1440.png) | 103,740 B | **Following** at the cap: the header "Exchange log", "200 of 2,000 shown, last seq 2036", Older and Newer (Newer unavailable, dashed); the one-row filter bar with "Pause" and "Clear"; the newest row at the bottom |
| [log-following-390.png](gui-m3b-live-demo/log-following-390.png) | 75,061 B | Following at 390: "200 of 2,000 shown, last seq 4190" |
| [log-paused-1440.png](gui-m3b-live-demo/log-paused-1440.png) | 95,104 B | **Paused:** "View paused. 33 new exchanges are held; they appear when you resume. The simulator keeps running.", the button reading "Resume" (name "Resume view"), "200 of 1,967 shown, last seq 2070" |
| [log-paused-390.png](gui-m3b-live-demo/log-paused-390.png) | 73,667 B | Paused at 390: 33 held, "Resume", "200 of 1,967 shown, last seq 4224" |
| [log-marker-1440.png](gui-m3b-live-demo/log-marker-1440.png) | 97,791 B | **A marker row in the window:** "Connection lost, then resumed. Last live 11:03:55 UTC, resumed 11:04:03 UTC after seq 2077." right after seq 2077, following |
| [log-marker-390.png](gui-m3b-live-demo/log-marker-390.png) | 82,378 B | The same at 390: "… resumed 11:05:45 UTC after seq 4231." below seq 4231 (the page scrolled to the log box's end) |
| [log-pinned-1440.png](gui-m3b-live-demo/log-pinned-1440.png) | 97,502 B | **Pinned** by a wheel scroll, **the header controls**: "200 of 2,000 shown, last seq 2116", Older, Newer, "10 rows below + 29 beyond this window" and Jump to newest, all on one header line |
| [log-pinned-390.png](gui-m3b-live-demo/log-pinned-390.png) | 77,314 B | Pinned at 390: "5 rows below + 28 beyond this window" beside Jump to newest (the header wraps at this width) |
| [log-older-row-1440.png](gui-m3b-live-demo/log-older-row-1440.png) | 101,534 B | **The top of a pinned window:** "125 older rows left this view. …", then the plain-text edge row "1,762 older exchanges match your filters. Use Older in the log header to show them.", then seq 1888; header "194 rows below + 38 beyond this window" |
| [log-evicted-repin-1440.png](gui-m3b-live-demo/log-evicted-repin-1440.png) | 112,336 B | **Eviction while pinned:** "2,151 older rows and 1 connection note left this view. The page keeps the newest 2,000 exchanges it received. The rows were received, so their removal is not a gap. Rows this view was showing left too, so it moved to the oldest exchanges kept."; Older unavailable (the oldest kept); "194 rows below + 1,800 beyond this window" |
| [log-evicted-repin-390.png](gui-m3b-live-demo/log-evicted-repin-390.png) | 83,585 B | The same at 390: "… left this view. … so it moved to the oldest exchanges kept.", "195 rows below + 1,800 beyond this window" |
| [stale-banner-1440.png](gui-m3b-live-demo/stale-banner-1440.png) | 45,458 B | **Stale:** the top of the page during a SIGSTOP: Connection "Disconnected, reconnecting" (amber outline lamp), the struck-through polled readouts, and the banner "Disconnected. Last live 11:03:55 UTC (8 s ago). The views below show data as of then. Reason: status request failed: no answer within 5 s. Reconnecting now." with "Retrying"; the banner is not a live region (`aria-live` null); the announcer then held "Reconnecting: attempt 2." |
| [stale-banner-390.png](gui-m3b-live-demo/stale-banner-390.png) | 43,493 B | The same at 390 ("… (9 s ago) …") |
| [lamp-live-1440.png](gui-m3b-live-demo/lamp-live-1440.png) | 5,519 B | **The live lamp** (crop of `#conn`, ×3): the filled lamp with its resting ring glow, "Live"; the beat paused at 0 ms |
| [lamp-live-390.png](gui-m3b-live-demo/lamp-live-390.png) | 5,444 B | The same at 390 |
| [lamp-last-known-1440.png](gui-m3b-live-demo/lamp-last-known-1440.png) | 13,333 B | **Last known** (crop, ×3): the amber outline lamp, no beat (0 `beat` animations), "Connected, last known data" (`conn conn--known`) |
| [lamp-last-known-390.png](gui-m3b-live-demo/lamp-last-known-390.png) | 13,107 B | The same at 390 |

Total: 17 files, 1,126,366 B. They show the page as it is; they do not tick the owner's visual
review.

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
- [ ] **The fault server with `--nonfinite`.** Run by the owner, watching, with
      `scripts/gui_fault_session.sh nonfinite` (Task 40; see "Running the two fault checks
      yourself"). The screenshots do not tick it.
- [ ] **The fault server with `--state-fault`.** Run by the owner, watching, with
      `scripts/gui_fault_session.sh state-fault` (the same section). The screenshots do not tick it.
- [ ] 390 px.
- [ ] 2000 px.
- [ ] The uPlot licence link.
- [ ] The measured cost against the estimates. The graphs come in under them; the page's
      main-thread load is the open finding below.

Findings and untested paths:
- [ ] **The main-thread finding: mitigated on headless numbers, still open** (section above,
      and [gui-m3b-main-thread.md](gui-m3b-main-thread.md), "Outcome"). Before the log fix the
      page was saturated (99.9-100 %) at the 2,000 cap; on the shipped page it is busy about
      10 % at 1440 × 900 and 6 % at 390 × 844, headless `--disable-gpu`. It stays open until
      the owner confirms it in their own browser.
- [ ] **The owner's visual review of the windowed log and the lamp**: following, scroll-to-pin,
      the header's Older / Newer / Jump to newest with the count beside it, the shortened count line
      and the four absent-row wordings, Pause / Resume / Clear, the eviction note, the stale banner
      with its ticking countdown; the lamp live, last known, down and refused. See "Owner
      checklist (ordered)" and the screenshots. The screenshots do not tick it.
- [ ] **The visible-browser measurement: BLOCKED**, it needs an unlocked desktop. Task 46b's
      visible GPU run was invalid because the desktop was locked with the monitor off. Re-run
      `GUI_PERF_VISIBLE=1 GUI_PERF_REPEATS=1 scripts/run_gui_demo.sh --m3b-perf-log <outdir>` at
      an unlocked desktop, or follow the five DevTools steps in
      [gui-m3b-main-thread.md](gui-m3b-main-thread.md), "What the numbers do not show".
- [ ] **The 1440 click-latency question.** Headless Chrome 151 with the GPU disabled, 1440 × 900,
      full buffer, about 3.8 requests/s, p95 of a scripted click's Event Timing, two runs per
      width: 32 ms with the old `box-shadow` beat (`782140e`) against 48 ms with the ring
      (`3593be7`); 390 did not grow. The extra time is presentation; input delay and processing are
      unchanged. **In a visible, GPU-composited browser it remains UNVERIFIED** (blocked by the
      locked desktop). An owner decision if it shows in a real browser; the alternative, a pulse
      only on a state change, is a visible behaviour change and was not made
      ([gui-m3b-main-thread.md](gui-m3b-main-thread.md), "What the numbers do not show").
- [ ] **Owner ruling: the remaining layout deficit at 1440 × 900** (recorded, not required; no
      layout change made): paused while stale shows 4 full rows (rows region 237 px, about 27 px
      short of five); stale while pinned shows 4 (the log header wraps to 72.1 px). See "Layout
      by state" above.
- [ ] **Owner decision: announcement chattiness.** Every retry start and failure is announced. A
      60 s outage produces about **11 announcements with a hung simulator** (SIGSTOP: 1 disconnect,
      5 attempt starts, 5 failures) and **about 15 with a closed port**, plus one on recovery; at
      the 15 s backoff cap, 2 every 15 s (task-51-report, Task 51b, from the backoff in the code;
      the harness observed the same sequence for the first 36 s).
- [ ] **Owner decision: Pause's `aria-pressed` with a changing label.** "Pause" / "Resume" (and
      the graphs' "Pause graphs" / "Resume graphs") change their visible text and also carry
      `aria-pressed`: two signals for one state (a toggle is usually either a pressed state with a
      fixed label, or a changing label). Pre-existing; the design spec §6.9 specifies
      `aria-pressed`; not changed (Task 51 review). What a screen reader says here is unverified.
- [ ] **Owner decision: the visible "Clear" next to the filters.** Since Task 47b the button reads
      "Clear" (accessible name "Clear view"); beside the filter chips it may read as "clear the
      filters". A visual call (Task 51 review; decision j above).
- [ ] **Owner decision: repository size before `gui` merges into `modernization`.**
      `docs/validation/gui-m3b-perf/` is 24 MB, almost all from Task 39 (already pushed). The range
      `3cfefec..b0b1250` adds about 1.8 MB of traces, 1.0 MB of PNGs and 0.4 MB of JSON. The gzip
      traces do not delta-compress, so they stay in the packed history (about 3.9 MB). Decide
      Git LFS or an external store before the merge; add no more traces meanwhile. (Task 50 adds no
      trace; it replaces the log / lamp PNGs and adds about 0.6 MB of results JSON and logs.)
- [ ] **The unidentified single full-suite failure** (Task 44's first run; the failing test's name
      was not captured). By the owner's instruction it is **not chased by repeated runs**: every
      required validation since then saves its complete output (`-rfE`), and a recurrence is to be
      investigated by its identified test. Complete outputs saved (session scratchpad, not
      committed): `suite-47-49.out`, `suite-47-49b.out`, `suite-47b.out`, `suite-51.out`,
      `suite-51b.out` (each 1339 passed, 69 skipped, 2 xfailed, 0 failed).
- [ ] **Deferred findings, reported and not changed:**
      - the "No exchanges match these filters." state line prints its count unformatted
        ("2000 are hidden by the ECU, service or outcome filter.", still so in `log51b`), while
        every other number on the page reads "2,000"; the paused line's held count is likewise
        unformatted, which would show at 1,000 or more (task-46a-report, Page findings);
      - with nothing matching, the count line reads "0 of 0 matching, last seq N" (`log51b`):
        accurate, and the state line explains it, but awkward.
- Resolved, not an acceptance item (Tasks 47-51): keyboard access to Newer; `#log-count`'s
      live region; `view.expanded` pruning; the "not following, not pinned" state; the ticking
      countdown and "N s ago"; the recovery announcement; Retry now focus. Each is listed with
      what automation verified and what still needs your observation under "Findings resolved"
      in "Tasks 47-51" above.
- Resolved, not an acceptance item (Task 37): **case 21**, 4 full log rows while the restart
      note and a gap note showed together at 1440 × 900 (run 9). The fix was the owner's
      wording on the shared notice line, no notice bottom margin, and a 4.5rem plot cap. Run 12
      has 5 rows in case 21 (+24 to +25 px), and 5 rows with a wrapped two-line notice line in
      case 22, with +6 px in the worst reading.
- Resolved, not an acceptance item (Task 41): **an incomplete `state` was ignored** and a
      healthy page stayed Live with its old data. It was reproduced in the browser, fixed in
      `135f006`, and checked by cases 24 and 25 in run 13 and case 26 in run 14.
- Resolved, not an acceptance item (Task 38): "the 503 path of variant A has never been met".
      Variant A's rule does not need a 503. **Variant C** produces one deterministically, and shows automatic
      recovery without "Retry now", the budget reset, and a fresh episode at attempt 1. It
      passed in 3 `--m3b-slots` runs and inside `--m3b` run 10 (see "The four-client cases").

Gates that stay open whatever happens to M3b:
- [ ] **The M2 early-check median-latency `STOP`** ([gui-m2-early-check.md](gui-m2-early-check.md)).
- [ ] **The hosted-CI `CAN_ISOTP` gap.** The vcan tests skip on hosted runners (0010 §9.3).
- [ ] **The Phase 8b gate.**
- [ ] **The V1.0 branch rule:** `gui` is not merged into `modernization` until V1.0 is tagged.

## Rulings made during the log fix (owner approvals, and decisions for the owner to confirm or reverse)

### Approved by the owner (2026-10-03)

The owner approved these behaviours as built (plan, "M3b follow-ups after the log-fix review").
They were controller rulings during Tasks 42-46 and are now owner approvals:
- **Leaving follow pins the window.** The reader's own scroll (or Older) pins it at the newest row
  shown; arrivals are counted ("… beyond this window", the Newer row), not appended below.
- **Reaching the bottom of an older window does not jump to live data.** Scrolling back to the
  bottom of a pinned window does not re-follow while newer matching exchanges wait; Newer or Jump
  to newest brings them in.
- **Clear view resumes following.**
- **Older / Newer move by overlapping 100-row steps, preserving the reading position**
  (`LOG_STEP` = 100 over a `LOG_WINDOW` of 200). The nuance, as built: the step shrinks when needed
  to keep the anchor row (the topmost visible for Older, the bottom-most for Newer) in the window,
  and a press with the window's far end already on screen scrolls the log box to that end instead
  of moving the window.

### Controller decisions, for the owner to confirm or reverse

Not spec and not owner-accepted. Sources: the task reports (local, untracked) and the code at
`25e6852`.

| # | Decision | What it means for a reader | What reversing costs |
|---|---|---|---|
| e | **Focus rule (replaces the earlier "focus goes to the log box after any press").** After Older / Newer the focus stays on the pressed header button; after Jump to newest, which hides itself, it goes to the log box; after Retry now, when the button hides or is disabled, to the banner text (`#linkstate-text`), else the log box (Tasks 47, 51b) | Keyboard users can press Older or Newer repeatedly; Newer is one Tab after Older | One focus rule per control in the click handlers |
| f | **The jump control keeps the owner-ruled "N rows below" meaning and adds "+ M beyond this window", now as plain text beside the button** (`#log-below`, "10 rows below + 29 beyond this window"), while the button reads "Jump to newest" with the accessible name "Jump to newest exchange and follow new ones", described by the count (Task 47, deviation 1) | No number inside a button label; the count is visible next to it and read as the button's description | One `setText` target, if the count should go back into the label |
| g | **The re-pin after eviction shows the oldest full window** (200 exchanges) **with a one-sentence note**, "Rows this view was showing left too, so it moved to the oldest exchanges kept." (task-43-report, fix round 1) | A pinned reader whose rows left the 2,000 cap sees a full window at the oldest kept, never a shrunken one | Task 43's first version re-pinned to a 1-row window; reversing removes `logFullEnd`'s use in `renderLog` and the sentence |
| h | **The lamp's beat moved to a ring animated by `transform` / `opacity`**, the same 2 s rhythm, only under `prefers-reduced-motion: no-preference` (task-45-report Part B) | The same look; pixel differences only at the ring's edge (see "The lamp") | Restoring the `box-shadow` beat in `app.css`. Headless it brings back about 21 points of busy time at 1440 and 13 at 390 (diagnostic B against A). The other candidate, a pulse only on a state change, is a visible behaviour change |
| i | **The diagnostic threshold:** an animation counted as a measurable share if turning it off alone removed more than about 10 % of busy time at both widths, beyond the run-to-run spread (task-45-report "Threshold used") | The lamp (66-71 % of busy time) was changed; the change flash (inside the noise) was not | A lower threshold would still not separate the flash from the run-to-run spread with two runs per width; more runs would be needed before changing it |
| j | **Visible texts "Pause" / "Resume" / "Clear"**; accessible names unchanged ("Pause view" / "Resume view" / "Clear view") (Task 47b) | The filter bar fits one row at 1440 × 900 (79 px of slack); the footer still says "Filters, pause and clear change this view only" | Restoring the longer texts leaves the bar on one row with about 15 px to spare, which is fragile |
| k | **The shortened count line** (Task 47 deviation 2, follow-up `6d84b0d`): no seq range, no "(2,000 retained)", no "(live)", and "matching" without "shown" ("200 of 2,000 shown, last seq 2116"). The facts are shown elsewhere: the filters' "All ECUs (2000)", the Connection readout, the rows' own seqs; restart information stays in the log's restart marker row and the graphs' restart note | The log header stays one line at 1440, filtered and pinned too | Any of the removed parts made the pinned header wrap at 1440 (72.1 px), costing a log row |
| l | **One hidden announcer** (`#announce`): the banner is no longer a live region; each announcement replaces the previous one (with `aria-atomic`, old messages would otherwise be re-read) and is cleared after 10 s (Task 51) | One announcement per transition; ticks are never announced | Re-adding live semantics to the banner's stable message, keeping the announcer for recovery only |
| m | **The recovery wording and "silent on a healthy first load"**: "Connection restored; data current." after an outage that included a disconnect, "Live data restored." after a data fault without one, nothing while the data stays last known, nothing on a healthy load or poll; an outage that began at page load is announced when it ends (Task 51b). The retry and attempt wordings are the controller's (task-51-report, deviation 2) | Each recovery is said once | Wording only (`annText`) |
| n | **The Jump button's accessible name and the count text beside it** (see f) | — | As f |

## Owner checklist (ordered)

Everything here is the owner's alone except what is marked automation verified; automation never
ticks a box. Run from the gui worktree,
`cd /home/aman/dev/personal-projects/ecu-simulator/.claude/worktrees/gui`.

1. **Start the host simulator and traffic** [needs your observation]. Terminal 1:
   `.venv/bin/ecu-simulator --profile docs/examples/ice_drive_cycle_stepped.yaml --interface vcan0 --api 127.0.0.1:8765`;
   terminal 2: `.venv/bin/python scripts/gui_demo_traffic.py --interface vcan0 --rate 4`
   (optionally `--rate 50` until "All ECUs (2000)", then `--rate 4`); open
   `http://127.0.0.1:8765/`. Stop with Ctrl-C in terminal 2, then terminal 1 (details: "Running
   the live GUI for visual review").
2. **Visual review** [needs your observation; automation verified the behaviour, not the look:
   `log51b` 22 of 22, `m3b-51b` 36 of 36]. Look at:
   - the windowed log: following; a scroll up pins it and the header shows "N rows below + M beyond
     this window" beside Jump to newest; Older / Newer in the header (unavailable ones dashed);
     the plain-text edge rows; the short count line; Pause / Resume / Clear; Clear and Jump to
     newest resume following; the eviction note after a burst;
   - the lamp: live (the ring's 2 s beat), last known (amber outline), disconnected;
   - the stale banner with its ticking countdown and "N s ago";
   - the graphs and the visual §13 view items: five graphs with units, the rpm row, the VIN as
     text; steps not ramps and the coolant staircase; the 90 s boundary; at least 5 log rows at
     1440 × 900 and Hide graphs; the three windows; Pause graphs against Pause; 390 px and
     2000 px; the uPlot licence link (the full list: "The owner's manual checklist" below).
3. **Chrome CSP check** [needs your observation]: open the page in Chrome, DevTools > Console,
   watch one full 90 s cycle: no Content-Security-Policy error, and the graphs draw. The
   automation's console log is not this check.
4. **Firefox CSP check** [needs your observation]: the same in Firefox (a snap on this host;
   manual only); record the Firefox version.
5. **The isolated fault checks** [needs your observation; automation verified the same faults
   headless: `m3b-51b` "Non-finite values" and "Encoding failure …"]. From a terminal in your
   desktop session (it needs `DISPLAY` and an unlocked desktop); each opens a **visible Chrome in a
   private user and network namespace** with its own `lo` and `vcan0`, and the page on that
   namespace's `http://127.0.0.1:8080/` (it does not touch the host's network, `vcan0`, port 8765
   or your Chrome profile). The options, from the script's own usage text:
   - `scripts/gui_fault_session.sh nonfinite` — `engine.coolant_temp` is nan for scenario t in
     [40, 60); no traffic. Watch: from the printed open time the coolant row and graph read
     "invalid value" and the line stops, the header stays "Live"; after the close time the line
     resumes with a gap.
   - `scripts/gui_fault_session.sh state-fault` — the snapshot raises for t in [40, 50); the
     traffic script runs. Watch: "Last known, <time> UTC" on the vehicle, DTC and graphs panels
     (not "Stale"), the header "Connected, last known data", the log still adding rows; after the
     close time "Live" again and a gap in the graphs.
   - Options: `--window START:END` (repeatable), `--signal PATH` (nonfinite), `--part
     vehicle|dtcs` (state-fault), `--profile PATH`, `--port N` (default 8080), `--no-traffic`
     (state-fault), `--devtools-port N`, `--duration S`, `--keep-logs DIR`, `-h` / `--help`.
   - Stop: close the Chrome window or press Ctrl-C in the terminal; the launcher stops what it
     started and removes its temp dir.
6. **Optional: the visible-browser performance run** [needs your observation; the headless numbers
   are automation's]: at an unlocked desktop,
   `GUI_PERF_VISIBLE=1 GUI_PERF_REPEATS=1 scripts/run_gui_demo.sh --m3b-perf-log <dir>` (about
   4 min; a Chrome window appears), or the five DevTools steps in
   [gui-m3b-main-thread.md](gui-m3b-main-thread.md), "What the numbers do not show".
7. **A screen-reader listen** [needs your observation; automation verified only the announcer's
   markup and the text and timing of each announcement, `m3b-51b` and `log51b` case 11]. With NVDA
   or Orca, on the page from step 1: nothing while the log runs; on `kill -STOP <simulator pid>`,
   "Disconnected. Reason: …" once, then "Reconnecting: attempt N." and "Attempt N failed: …" per
   retry (the countdown is not read each second); on `kill -CONT`, "Connection restored; data
   current." once. With the `state-fault` session of step 5: "Last known data: …" once when the
   fault opens (any further attempt or polling wording is the controller's; task-51-report lists
   every string), and one recovery announcement after it closes (by the design, "Live data
   restored." for a data fault without a disconnect; this exact session was not automated). An
   exhausted episode ("Could not recover: …") occurs only in the automated bounded case; after it,
   Enter on Retry now should leave focus on the banner text.

## Running the live GUI for visual review

For the owner's own look at the windowed log and the lamp. On the host network, from the gui
worktree, with the host's `vcan0` up. **Never use `can0`.** The host's `vcan0` is shared with
anything else on it, and port 8765 must be free (stop any simulator already running there
first).

```
cd /home/aman/dev/personal-projects/ecu-simulator/.claude/worktrees/gui

# terminal 1: the simulator, stepped demo, observer API
.venv/bin/ecu-simulator --profile docs/examples/ice_drive_cycle_stepped.yaml --interface vcan0 --api 127.0.0.1:8765

# terminal 2, in the same directory: the read-only traffic script
.venv/bin/python scripts/gui_demo_traffic.py --interface vcan0 --rate 4

# browser
http://127.0.0.1:8765/
```

The log reaches its 2,000-exchange cap after about 9 minutes at this rate (Task 39). To see a
full buffer sooner, run the traffic script at `--rate 50` until the ECU filter reads
"All ECUs (2000)", then restart it at `--rate 4`, as the measurements did.

To stop: Ctrl-C in terminal 2 (the traffic), then Ctrl-C in terminal 1 (the simulator), and
close the tab.

**An isolated alternative that does not use the host's `vcan0`: none for the plain demo.**
`scripts/gui_fault_session.sh` (the isolated launcher with a visible Chrome; "Running the two
fault checks yourself" below) takes exactly one of the modes `nonfinite` or `state-fault`, and
always opens a fault window (by default 40-60 s or 40-50 s of scenario time); it has no
fault-free mode. Its `state-fault` mode does run the stepped demo with the traffic script, so the
windowed log can be seen there, with a "last known" interval in the middle. `scripts/run_gui_demo.sh`
is isolated but headless: its page is reachable only inside its namespace.

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

**The two fault-server items are not run from these commands.**
`scripts/gui_fault_server.py` refuses to run outside a private network namespace (it compares
its namespace with `GUI_DEMO_HOST_NETNS`), and a browser on the host cannot reach that
namespace's loopback. Task 40 added a launcher that puts a visible Chrome inside the
namespace with the server: see the next section.

## Running the two fault checks yourself

`scripts/gui_fault_session.sh` starts, in a private user and network namespace with its own
`lo` and its own `vcan0`: the fault-injection server with the chosen fault, and a **visible**
Chrome on your desktop, opened on the page at `http://127.0.0.1:8080/` (the namespace's own
loopback). The `state-fault` mode also starts the read-only traffic script there, so the
exchange log keeps moving. The `nonfinite` mode sends no traffic: a `01 05` read while coolant
is nan would reach the OBD encoder (DEV-26). Run it from a terminal in your desktop session
(it needs `DISPLAY`), from the worktree:

```
cd /home/aman/dev/personal-projects/ecu-simulator/.claude/worktrees/gui

scripts/gui_fault_session.sh nonfinite      # engine.coolant_temp is nan for scenario t in [40, 60)
scripts/gui_fault_session.sh state-fault    # the full snapshot raises for scenario t in [40, 50)
scripts/gui_fault_session.sh --help         # --window START:END (repeatable), --signal, --part vehicle|dtcs,
                                            # --profile, --port, --no-traffic, --devtools-port, --duration, --keep-logs
```

Both use the stepped demo (`docs/examples/ice_drive_cycle_stepped.yaml`) unless `--profile`
says otherwise. Before the window opens, the terminal prints the namespace check (the
network namespace of Chrome and of the server against the host's, and the listening socket
and Chrome's connections in the namespace), the sandbox check (no flag disables it; the
renderers run seccomp-filtered in their own namespaces), and the wall-clock time each fault
window opens and closes. The server's own fault lines ("set to nan", "opens", "closes") are
printed as they happen. Time starts when the server starts, so the default windows leave
about 35 s to see the page Live first. Use `--window` again for another window later in the
same run (for example `--window 40:60 --window 150:170`).

What to watch:
- **`nonfinite`** (§13: "invalid value" in the table and the coolant graph, a gap, and the page
  still "Live"): from the printed open time, the coolant row and the coolant graph's value line
  read "invalid value" and the line stops; the header stays "Live". After the close time, the
  coolant line resumes with a gap; nothing is drawn across the invalid stretch.
- **`state-fault`** (§13: "Last known" on the vehicle, DTC and graphs panels, distinct from
  "Stale", and the exchange log still running; after the fault, "Live" again): during the window
  the three panels show "Last known, <time> UTC" (not "Stale") and the header reads "Connected,
  last known data"; the exchange log keeps adding rows. After the close time, "Live" again and
  the graphs show a gap.

To stop: close the Chrome window, or press Ctrl-C in the terminal. Either way the launcher
stops the processes it started (by their own process groups), the namespace and its `vcan0`
go with them, and it removes its temp dir (Chrome's fresh profile, its `HOME` and `TMPDIR`, and
the logs; `--keep-logs DIR` copies the logs out first).

What it does not do:
- It does not touch the host's network, its `vcan0` or `can0`, a simulator you have running on
  the host, or your own Chrome and its profile. Chrome starts with a new profile in the
  launcher's temp dir.
- It does not weaken anything: Chrome keeps its sandbox (no `--no-sandbox`), X access control is
  unchanged (no `xhost`; your `DISPLAY` and `XAUTHORITY` are passed through). Chrome runs in a
  nested user namespace mapped back to your uid, because Chrome refuses to run as uid 0, which is
  what `unshare -r` makes the launcher. If X or the sandbox fails in the namespace, it prints the
  error and stops.
- It does not tick anything. A screenshot does not complete a behavioural check; you do, by
  watching. The DevTools port (`--devtools-port`, off by default) is only reachable inside the
  namespace.
- It is not the CSP check. The manual Chrome and Firefox CSP items above stay outstanding; this
  Chrome is a fresh profile in a namespace, and Firefox is not run.

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
      coolant graph, a gap, and the page still "Live". (Run with
      `scripts/gui_fault_session.sh nonfinite`; see "Running the two fault checks yourself".)
- [ ] The fault-injection server with `--state-fault`: "Last known" on the vehicle, DTC and
      graphs panels, distinct from "Stale", and the exchange log still running. After the
      fault, "Live" again. (Run with
      `scripts/gui_fault_session.sh state-fault`; the same section.)
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
