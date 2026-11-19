# GUI M3a live demo on vcan: record and startup commands

**This is a demonstration, not a benchmark.** Nothing here judges M4 performance or any of
P1–P9 ([decisions/0010 §9.2](../decisions/0010-gui-observer-api.md)). The M2 latency `STOP`
([gui-m2-early-check.md](gui-m2-early-check.md)) stays open; this run neither tests nor
relieves it. The `dispatch_us` values visible in the screenshots are dispatcher time from a
single run at a human request rate, and are not evidence about latency.

**Still explicitly open, and not touched by this record:**
- the M2 latency `STOP`;
- the 68 CAN_ISOTP tests that hosted CI skips, because its runners have no `vcan`/`can-isotp`. They run only in a local namespace with a `vcan0`.

This demo neither closes nor narrows either of them.

**The V1.0 and Phase 8b gates are untouched.** No simulator, protocol, profile, API
endpoint or server code changed in this task. The page fixes are in the static files under
`src/ecu_simulator/api/static/` (`app.css`, `app.js`, `index.html`): `4b92c9a`, `0355164`
and `106b58a` (see "Findings"), and Task 20's `8d953d3`. The supported-PID summaries come from Task 19's
`5e9361b` and `cfd8c24` in `observe/`. These screenshots were retaken against all of
them. Apart from those, the changes are the demo scripts and this record.

## What was run

| Item | Value |
|---|---|
| Branch, commit | `gui` at `e9475e0f65fa055761f62ca448a322ea9716fde8` (Task 25: the VIN shown once, in the panel header, not again as a table row; the capture script's horizontal-overflow check). Retaken so that no shot shows a VIN row. The odometer shows "—" (Task 24). The Task 21 jump control is unchanged. The capture script is used as committed at `e9475e0`, unchanged |
| Date | 2026-09-30, 17:56:48 UTC (capture start) |
| Kernel | Linux 6.8.0-138-generic |
| CPU governor | `powersave` on all 12 CPUs (not pinned; this is not a timing run) |
| Python | 3.12.12 (worktree `.venv`) |
| Browser | Google Chrome 151.0.7922.173, `--headless=new --no-sandbox`, driven over the DevTools protocol |
| Isolation | `unshare -r -n`: a private user and network namespace with its own `lo` and `vcan0`. The host's `vcan0` and `can0` were never used. The owner's own simulator was running on the host's `vcan0` and `127.0.0.1:8765` at the same time, untouched: the namespace has its own loopback. `gui_demo_capture.py` refuses to start unless its network namespace differs from the one `run_gui_demo.sh` recorded before `unshare` |

The one command:

```
scripts/run_gui_demo.sh <scratchpad>/run11
```

The screenshots in [gui-m3a-live-demo/](gui-m3a-live-demo/) are all from that one run,
copied unchanged, with its `capture.log` (the step log) and `traffic.log` (every request
and answer the generator saw). The same command had been run six times before, to develop
the capture script and against earlier pages. Those runs are not the record, and none of
their screenshots is kept.

Inside the namespace, `scripts/gui_demo_capture.py`:

1. starts the real simulator:
   `python -m ecu_simulator --profile src/ecu_simulator/profiles/ice_scenario.yaml --interface vcan0 --api 127.0.0.1:8765 --log-level INFO`;
2. starts headless Chrome and loads `http://127.0.0.1:8765/`;
3. starts `scripts/gui_demo_traffic.py --interface vcan0 --rate 4`: real ISO-TP requests on
   vcan0 through the kernel's `can-isotp`, answered by the simulator;
4. uses only view controls on the page:
   - the service filter, Pause and Resume;
   - real mouse-wheel scrolls (`Input.dispatchMouseEvent`, type `mouseWheel`) over the log;
   - a real mouse click on "jump to newest".

   It also records every console message, exception and browser log entry;
5. stops the traffic and sends SIGTERM to the simulator, starts it again, then starts the
   no-scenario profile `ice_default.yaml`.

The page's code was not changed. There is one interception: for the loading shot, the
page's first API request was held in DevTools (`Fetch.enable`) for about 0.5 s, then
released unchanged.

### The traffic

`scripts/gui_demo_traffic.py` sends reads only, four a second in this run, in an 18-request
cycle:

- **OBD, functional** on 0x7DF, answered on 0x7E8: service 01 PIDs 00, 04, 05, 0C, 0D, 0F,
  10, 11, 20 and 2F; mode 03; mode 07; and mode 09 PID 02 (the VIN, a multi-frame answer).
- **UDS, physical**:
  - 0x22 F190 on 0x7E0;
  - 0x19 02 FF on 0x7E0 and on 0x7E1 (answered on 0x7E9), which are the two physical
    endpoints the profile routes UDS to.

It sends no write, clear or session change: no OBD 04, and no UDS 0x14, 0x2E, 0x31 or
0x10. At import, an allowlist check (`if`/`raise`, so `python -O` keeps it) rejects any
service outside 0x01, 0x03, 0x07, 0x09, 0x19 and 0x22.

Two requests are deliberately ones today's simulator does not serve:

- UDS 0x22 gets NRC 0x11 (`7F 22 11`). The page shows this as outcome "responded", because
  a negative response is still a response.
- OBD mode 07 gets no answer (DEV-11). The page shows "no response".

They show the page's negative and unanswered cases with real traffic.

## The screenshots

The scenario times are the page's own "Scenario t". The scenario raises P0128 pending at
t = 40 s, then confirmed with the MIL requested at t = 75 s. Its speed and rpm timelines
hold each value until the next point; they do not interpolate.

All shots are at 1440×900 unless the name says otherwise. From the first live shot on,
the status bar shows the polled label: "Polled every 2 s from /status, last at HH:MM:SS;
may trail the live log".

In the trouble-codes table the flag columns are now marks under short headers ("Pend.",
"Conf.", "Lamp"): a filled dot is set, a ring is not.

In every shot with the vehicle table, the odometer row reads **"—"** with "unavailable,
no source" under its name: neither `ice_scenario.yaml` nor `ice_default.yaml` sets
`vehicle.odometer`, and `/vehicle` lists it in `unavailable`.

| File | What it shows |
|---|---|
| [a-loading.png](gui-m3a-live-demo/a-loading.png) | **Loading.** "Connecting"; "Loading trouble codes", "Loading vehicle signals", "Loading the exchange history". The first `/api/v1/` request was held in DevTools to capture this; otherwise it lasts well under a second |
| [h1-empty-fresh-start.png](gui-m3a-live-demo/h1-empty-fresh-start.png) | **Empty: a fresh start before any traffic.** Live, t = 1.07 s, seq issued 0, the polled label. "No exchanges yet …" The scenario is already moving `coolant_temp` and `engine_load` |
| [b1-live.png](gui-m3a-live-demo/b1-live.png) | **Live, t ≈ 23 s.** 81 exchanges: speed 30, rpm 1600, coolant 48.94 °C. Includes row 73, "OBD 01 00 — supported PIDs 01–20", and the 0x22 NRC (row 78) |
| [b2-live-7s-later.png](gui-m3a-live-demo/b2-live-7s-later.png) | **Live, t ≈ 29–31 s, about 7 s later.** 107 exchanges: speed 55, rpm 1900, coolant 56.89 °C. The log follows to the newest row, 107 ("01 20 — supported PIDs 21–40") |
| [b3-live-supported-pid-ranges.png](gui-m3a-live-demo/b3-live-supported-pid-ranges.png) | **The supported-PID summaries.** Row 107 "OBD 01 20 — supported PIDs 21–40", row 109 "OBD 01 00 — supported PIDs 01–20". The polled label; the status Seq "issued 107" trails the log's "last seq 112", as the label says it may |
| [d1-filter-service-0x19.png](gui-m3a-live-demo/d1-filter-service-0x19.png) | **The service filter 0x19.** "12 of 116 shown", UDS 19 rows from 7E0 and 7E1 with "N exchanges hidden by filters (not a gap)" between them |
| [d2-paused-held-rows.png](gui-m3a-live-demo/d2-paused-held-rows.png) | **Paused, after the filter was reset.** "View paused. 19 new exchanges are held"; "117 exchanges, last seq 136"; "Resume view" |
| [d3-resumed-log-following.png](gui-m3a-live-demo/d3-resumed-log-following.png) | **3 s after Resume: the log follows** to row 147, with no scrolling. P0128 has just turned "Pending" |
| [j-1440-rows-below.png](gui-m3a-live-demo/j-1440-rows-below.png) | **1440: after a real wheel scroll up (deltaY −700).** The log stays at rows 128–137; the header shows "159 exchanges, last seq 159 (live)" and, beside it, **"22 rows below, jump to newest"**. Counted from the DOM: **22** rows wholly below the log box (138–159) just before the shot, **23** just after (one exchange arrived during the capture; the control then read 23), plus 1 cut by its edge. The control does not intersect the log box |
| [j-1440-jumped.png](gui-m3a-live-demo/j-1440-jumped.png) | **1440: after a real click on it.** Back at the newest row, 166; the control is hidden, and 0 rows are below |
| [c1-dtc-pending.png](gui-m3a-live-demo/c1-dtc-pending.png) | **DTC pending, t ≈ 45 s.** P0128 "Pending": a filled Pend. mark, rings under Conf. and Lamp; MIL off. UDS 19 on 7E1 answers `59 02 8c 01 28 01`. The log follows, at row 166 |
| [c2-dtc-confirmed-mil.png](gui-m3a-live-demo/c2-dtc-confirmed-mil.png) | **DTC confirmed with the MIL, t ≈ 79 s.** P0128 "Confirmed" with filled Pend., Conf. and **Lamp marks, all fully visible**; "MIL on"; speed 40, rpm 1400, coolant 92 °C |
| [g1-narrow-390.png](gui-m3a-live-demo/g1-narrow-390.png) | **390 px, viewport.** The status grid with the polled label; the trouble codes with MIL on and every column visible |
| [g2-narrow-390-full-page.png](gui-m3a-live-demo/g2-narrow-390-full-page.png) | **390 px, full page.** Panels stacked; every vehicle signal; log rows as blocks, 295–300, ending at the newest |
| [j-390-rows-below.png](gui-m3a-live-demo/j-390-rows-below.png) | **390: the page scrolled to the log panel, then a real wheel scroll up over the log.** The header reads "312 exchanges, last seq 312 (live)" and, on its own line under it, **"17 rows below, jump to newest"** (the screenshot caught the control after one more exchange arrived). Counted from the DOM: **16** just before the shot while the control read 16, **17** just after while it read 17, plus 1 cut by its edge each time. The control does not intersect the log box. At this width the log box extends past the bottom of the phone's screen, so the rows between 295 and the box's edge are inside the box but off-screen; both N and the count measure against the log box |
| [j-390-jumped.png](gui-m3a-live-demo/j-390-jumped.png) | **390: after a real click on it.** The log is at the newest rows (314 onward, last 319); the control is hidden, and 0 rows are below |
| [w-desktop-2000x1100.png](gui-m3a-live-demo/w-desktop-2000x1100.png) | **2000×1100, the owner's width.** The whole DTC table; one-line summaries, including "01 20 — supported PIDs 21–40" and "01 00 — supported PIDs 01–20"; the polled label; the log at the newest row, 325 |
| [j-2000-rows-below.png](gui-m3a-live-demo/j-2000-rows-below.png) | **2000: after a real wheel scroll up.** Rows 306–316 are in view; the header shows "338 exchanges, last seq 338 (live)" and **"21 rows below, jump to newest"**. Counted from the DOM: **21** just before and just after the shot, plus 1 cut by the edge each time. Rows 318–338 are the 21 below. The control does not intersect the log box |
| [j-2000-jumped.png](gui-m3a-live-demo/j-2000-jumped.png) | **2000: after a real click on it.** Back at the newest row, 344; the control is hidden, and 0 rows are below |
| [e-disconnected-stale.png](gui-m3a-live-demo/e-disconnected-stale.png) | **Disconnected, about 4 s after SIGTERM.** The banner reads "Last live 21:54:21 UTC (4 s ago) … the simulator is shutting down (1001) …", with "Retry now". Each panel shows "Stale, as of 21:54:21 UTC", and the status readouts are struck through. **All three DTC rows and the Lamp column are fully visible.** The log keeps 349 exchanges |
| [f-reconnected-after-restart.png](gui-m3a-live-demo/f-reconnected-after-restart.png) | **Reconnected after a restart.** Live, t = 4.66 s, MIL off. A real wheel scroll up of about 961 px shows the marker: "Simulator restarted. A new run started at 21:54:25 UTC …", with old rows 346–349 above it and new rows 1–5 below. The header control reads **"20 rows below, jump to newest"**, which is the new rows 6–25, now counted in full (Task 21) |
| [h2-no-scenario.png](gui-m3a-live-demo/h2-no-scenario.png) | **No scenario.** `ice_default.yaml`, on a fresh page load with no traffic. "no scenario: values as configured"; "No exchanges yet". B1477 and P0001 are confirmed, with filled Pend. and Conf. marks and a ring under Lamp (fully visible); MIL off |

In every shot the jump control sits in the log header. When it is hidden (every shot
except the three `j-*-rows-below` and `f`), it keeps its place without showing. It never
covers a log row: `overlap` was false in all nine DOM checks.

On the final page load, `performance.getEntriesByType('resource')` listed no resource from
any origin other than `http://127.0.0.1:8765`. The whole run produced **no page exception
and no console message**. There were two browser log entries, both
`net::ERR_CONNECTION_REFUSED`: the page's retries while the simulator was stopped, as
expected. `ps` afterwards showed no simulator, traffic or Chrome process from the run.

## Findings from the live session

Two page defects were found in the first live captures, against `654557c`. Both were fixed
in `4b92c9a`, and the screenshots above were retaken against the fix:

1. **The exchange log stopped following new rows after a view change or reflow.** Resetting
   a filter, the "View paused" line, or the controls wrapping to a second line moved the
   log more than 40 px from its bottom without the reader scrolling. It then stayed put
   while new rows arrived. **Fixed in `4b92c9a`:** only the reader's own scroll stops or
   resumes following, and a "N new rows below, jump to newest" control brings it back.
   The retake shows it following in b2, d3, c1, c2 and g2; stopped by a real wheel
   scroll in the `j-*-rows-below` shots and f; and resumed by a click in the
   `j-*-jumped` shots.
2. **In the stale view the trouble-codes panel clipped its third row** at desktop width,
   under the disconnected banner. **Fixed in `4b92c9a`:** e shows all three rows.

The API's `summary` strings for OBD 01 00 and 01 20 read "unknown parameter" in the earlier
captures. Task 19 (`5e9361b`, `cfd8c24`) changed them to "supported PIDs 01–20" and
"supported PIDs 21–40" (b3).

3. **At 1440×900 the trouble-codes table clipped its Lamp column** whenever a code read
   "Confirmed": the table was wider than its panel. Found in the recapture at `8d953d3`
   (Task 20). **Fixed in `0355164`:** the flag columns show marks under short headers,
   and a safety net scrolls the table sideways instead of clipping it. The retake shows
   the Lamp column fully visible in c2, e and h2, and at 390 px and 2000 px.
4. **The jump control counted only rows that arrived after the scroll, and lay over the
   bottom of the log.** In the earlier captures, f read "2 new rows below" although rows
   7–25 were below, and the control covered the last visible row. Found in the owner's
   second review. **Fixed in `106b58a` (Task 21):** the control counts every shown row
   below the viewport, and sits in the log header. The retake checks N against a
   separate DOM count at 1440, 2000 and 390 px (the `j-*` shots), and f now reads 20.

## Test suite (at `0355164`)

These are recorded for completeness. The demo adds no tests.

- **The namespace suite without vcan0, at `0355164`**, the baseline form:
  `unshare -r -n bash -c 'ip link set lo up && .venv/bin/python -m pytest -p no:cacheprovider -q -rsx'`
  gave **1248 passed, 69 skipped, 2 xfailed**, as reported by the page implementer for
  that commit. It was not rerun for this recapture. The 69 skips are the vcan integration
  tests, which need a `vcan0`.
- Earlier, at `4b92c9a`, the same command gave 1247 passed, 69 skipped, 2 xfailed (run for
  this task in Fix round 1).
- **A separate note, not the baseline:** the same suite run in a namespace that also had a
  `vcan0`, at `aafdac2` before the page fix, gave 1315 passed, 1 skipped, 2 xfailed. The
  vcan0 lets 68 of the 69 integration tests run; the total is the same.

## Moving-vehicle demo (Task 24)

**Not a benchmark, and nothing here closes anything.** The M2 latency `STOP` stays open.
The hosted CI still skips the 68 CAN_ISOTP tests, because its runners have no
`vcan`/`can-isotp`. The timeline extension (a `repeat` for `timeline`), a distance
generator for the odometer, and any new PIDs all stay deferred
(`docs/plans/moving-vehicle-demo-design.md`). This demo uses only today's `stepped` and
`ramp` generators.

| Item | Value |
|---|---|
| Commit | `gui` at `e9475e0f65fa055761f62ca448a322ea9716fde8` (Task 25: no VIN row; overflow check in the capture script) |
| Date | 2026-09-30, 17:55:10 UTC (capture start) |
| Kernel, governor, browser | as above: 6.8.0-138-generic, `powersave`, Chrome 151 headless |
| Isolation | `unshare -r -n`, a private `lo` and `vcan0`, behind the same namespace guard. The owner's own simulator on the host `vcan0` was not touched |

The command:

```
scripts/run_gui_demo.sh --moving <scratchpad>/mv2
```

Inside the namespace it runs:
- the real simulator, with `--profile docs/examples/ice_drive_cycle_stepped.yaml --interface vcan0 --api 127.0.0.1:8765`;
- `scripts/gui_demo_traffic.py --interface vcan0 --rate 4`;
- headless Chrome.

**How the shots are timed.** The capture script estimates when scenario t was 0 from 40
polls of `/status` `scenario.t_last_applied`, and times each shot by that estimate. Each
shot's t below is the one the page itself shows, "as of scenario t", read from the
screenshot.

The logs are in [gui-m3a-live-demo/moving/](gui-m3a-live-demo/moving/): `capture.log`,
`traffic.log` and `obd-crosscheck.txt`.

### The shots

All shots are at 1440×900 unless the name says otherwise. In every shot the odometer reads **"—"**
with "unavailable, no source".

| File | Page's scenario t | Speed (km/h) | rpm | Coolant (°C) | OBD rows in view (reply) |
|---|---|---|---|---|---|
| [m-idle-odometer-unavailable.png](gui-m3a-live-demo/m-idle-odometer-unavailable.png) | 3.09 s | 0 | 800 | 20.9 | 01 0C `41 0c 0c 80` (800), 01 0D `41 0d 00` (0) |
| [m-accelerating.png](gui-m3a-live-demo/m-accelerating.png) | 10.34 s | 25 | 1400 | 23.02 | 01 0C `41 0c 15 e0` (1400), row 30 |
| [m-cruising.png](gui-m3a-live-demo/m-cruising.png) | 40.34 s | 80 | 2400 | 31.77 | 01 0C `41 0c 25 80` (2400), 01 0D `41 0d 50` (80) |
| [m-2000-cruising.png](gui-m3a-live-demo/m-2000-cruising.png) (2000×1100) | 45.34 s | 80 | 2400 | 33.23 | 01 0C `41 0c 25 80`, 01 0D `41 0d 50` |
| [m-390-vehicle.png](gui-m3a-live-demo/m-390-vehicle.png) (390 px, scrolled to the vehicle panel) | 50.34 s | 80 | 2400 | 34.68 | (log below the fold) |
| [m-braking.png](gui-m3a-live-demo/m-braking.png) | 65.20 s | 53 | 1600 | 39.02 | 01 0C `41 0c 1b 80` (1760, sent at t ≈ 64.84, step 64), 01 0D `41 0d 35` (53, t ≈ 65.09) |
| [m-after-loop-idle.png](gui-m3a-live-demo/m-after-loop-idle.png) | 92.35 s | 0 | 800 | **46.94** | 01 0C `41 0c 0c 80` (800), 01 0D `41 0d 00` (0) |
| [m-disconnected-stale.png](gui-m3a-live-demo/m-disconnected-stale.png) | 92.60 s (stale) | 0 | 800 | 47.01 | stale view after SIGTERM; odometer **still "—"** |

What the shots show:

- **The phases move as designed.** The design (§4.2) gives these step values:
  - step 3 (idle): 0 km/h and 800 rpm;
  - step 10 (accelerating): 25 and 1400;
  - steps 40, 45 and 50 (cruise): 80 and 2400;
  - step 65 (braking): 53 and 1600;
  - step 92 − 90 = 2 (idle again after the loop): 0 and 800.

  Every shot's speed and rpm equal the value for its step.
- **The loop is crossed.** At t = 92.35 s the vehicle is back at idle, and the coolant reads
  46.94 °C, against 20.9 °C at t = 3.09 s in the first cycle. The ramp gives
  20 + 70 × 92.35 / 240 = 20 + 26.94 = 46.94 °C. The coolant does not repeat with the
  cycle, as §4.1 says.
- **The two "coolant" values in the disconnected shot differ** (47.01 there, 46.94 in the live
  shot). That is because the stale view shows the page's last state, at t = 92.60 s, where
  the ramp is 47.01.

### OBD cross-check (01 0C and 01 0D)

`obd-crosscheck.txt` decodes every 01 0C and 01 0D reply the generator received in the
run: 76 replies from t = 3 s to t = 91 s. It compares each with the profile's `stepped`
value for the scenario second in which the request was sent. The send time is the
exchange's `t`, less the estimated scenario origin.

The rule:
- a request within 0.1 s of a whole second counts as at an edge, because the scenario may
  have been applied on either side of it;
- an edge reply may match either neighbouring step.

**The result:**

| Verdict | Count |
|---|---|
| matched their step | **57** |
| near an edge, and matched the step they fell in | **19** |
| matched only the other side of an edge | **0** |
| mismatched | **0** |

Two examples:
- the braking shot's `41 0c 1b 80` is 1760 rpm. It was sent at t ≈ 64.84 s, which is step
  64, where the design has 1760. The page's 1600 at t = 65.20 is step 65. Both are right
  for their own moment.
- the reply at t ≈ 15.05 s, `41 0c 2b c0`, is 2800 rpm: step 15, the upshift, as in the
  design's §4.3 table (`0C 2B C0` at t = 15.0 and 15.3).

**Every reply equals the design's value for its moment.**

This checks the page and the wire against the profile's own lists, read from the YAML. It
is not an independent re-derivation of the design. §4.3's byte table was produced through
the real generators, and the replies seen here agree with it where they coincide:
- `0C 0C 80`, `0D 00` at idle;
- `0C 2B C0` at step 15;
- `0C 25 80`, `0D 50` at cruise;
- `0D 35` at step 65.

### Owner startup commands for the moving-vehicle demo

On the host network, on an interface of your choice. **Never use `can0`.**

```
cd /home/aman/dev/personal-projects/ecu-simulator/.claude/worktrees/gui

# terminal 1
.venv/bin/ecu-simulator --profile docs/examples/ice_drive_cycle_stepped.yaml --interface vcan0 --api 127.0.0.1:8765

# terminal 2
.venv/bin/python scripts/gui_demo_traffic.py --interface vcan0

# browser
http://127.0.0.1:8765/
```

The caveats are the same as for Option A below:
- the host's `vcan0` is shared with anything else on it;
- a dedicated `vcan1` needs sudo (`sudo ip link add dev vcan1 type vcan && sudo ip link set up vcan1`), and then `--interface vcan1` on both commands;
- port 8765 must be free.

The cycle is 90 s long, and the coolant keeps warming for 240 s. To get screenshots only,
fully isolated, run `scripts/run_gui_demo.sh --moving <outdir>`.

## Task 25: the VIN row removed, and the horizontal-overflow check

Both sets above were retaken at `e9475e0` so that no shot shows a VIN row.

- **`9a417a4`** removes the duplicate VIN row from the vehicle table. The full VIN stays in
  the panel header, where it can wrap and be selected. That row was what made `#vehicle`
  overflow sideways at 1440. The implementer measured `#vehicle` scrollWidth/clientWidth
  **with normal scrollbars**:

  | Width | Before | After |
  |---|---|---|
  | 1440 | 387/363 (24 px overflow) | 363/363 |
  | 390 | | 372/372 |
  | 2000 | | 483/483 |

- **`e9475e0`** adds a check to `scripts/gui_demo_capture.py`. At each width it shoots, live and stale, it
  compares scrollWidth with clientWidth for `#vehicle`, `html` and `body`, writes
  `overflow-check.txt`, and exits 1 on any overflow. Both runs exited 0, and every line reads `ok`:

  | Run | Check | `#vehicle` | `html` | `body` |
  |---|---|---|---|---|
  | M3a ([overflow-check.txt](gui-m3a-live-demo/overflow-check.txt)) | 390 | 372/372 | 390/390 | 390/390 |
  | | 2000 | 498/498 | 2000/2000 | 2000/2000 |
  | | 1440 | 378/378 | 1440/1440 | 1440/1440 |
  | moving ([moving/overflow-check.txt](gui-m3a-live-demo/moving/overflow-check.txt)) | 1440 | 378/378 | 1440/1440 | 1440/1440 |
  | | 2000 | 498/498 | 2000/2000 | 2000/2000 |
  | | 390 | 372/372 | 390/390 | 390/390 |
  | | 1440, stale | 378/378 | 1440/1440 | 1440/1440 |

- **Limit of this check.** The capture script runs Chrome with `--hide-scrollbars`, so the
  panel's clientWidth includes the space a scrollbar would take: 378 and 498 here, against
  363 and 483 with normal scrollbars. An overflow smaller than the scrollbar width, about
  15 px, can therefore pass this check and still show a horizontal scrollbar in a normal
  browser. The implementer's measurements above were taken with normal scrollbars.

The M2 latency `STOP` and the 68 CAN_ISOTP tests skipped by hosted CI remain open. This
retake does not touch either.

## Owner startup commands (for your own browser)

A browser on the host cannot reach a namespace's loopback, so there are two options.

### Option A: host network, your browser

From the worktree, with its `.venv`. The `.venv` has the `ecu-simulator` console script
and the `[gui]` extra.

```
cd /home/aman/dev/personal-projects/ecu-simulator/.claude/worktrees/gui

# terminal 1: the simulator with the scenario and the observer API
.venv/bin/ecu-simulator --profile src/ecu_simulator/profiles/ice_scenario.yaml --interface vcan0 --api 127.0.0.1:8765

# terminal 2: read-only traffic, 3 requests a second by default (Ctrl-C to stop)
.venv/bin/python scripts/gui_demo_traffic.py --interface vcan0

# browser
http://127.0.0.1:8765/
```

The real CLI flags are `--interface`, `--profile`, `--log-level` and `--api HOST:PORT`
(`.venv/bin/python -m ecu_simulator --help`). The generator takes `--interface` (default
`vcan0`), `--rate` (requests a second, default 3, at most 50) and `--timeout` (default
0.5 s).

Caveats:

- **The host's `vcan0` is shared** with whatever else uses it: other tests, the integration
  suite, another simulator. Another simulator on the same addresses would answer too, and
  another tool's traffic would appear in the log. If `vcan0` does not exist yet, create it
  with `sudo scripts/setup_vcan.sh`.
- **For a dedicated interface**, which needs sudo:
  `sudo ip link add dev vcan1 type vcan && sudo ip link set up vcan1`. Then pass
  `--interface vcan1` to both commands.
- **Never use `can0`.** The generator is read-only, but `can0` is the physical bus.
- Port 8765 must be free on the host's loopback.
- For the no-scenario view, restart terminal 1 with
  `--profile src/ecu_simulator/profiles/ice_default.yaml`.
- To see the stale view, stop terminal 1 (Ctrl-C). To see reconnect, start it again.

### Option B: fully isolated, screenshots only

```
scripts/run_gui_demo.sh <outdir>
```

It takes about 100 s and needs `unshare -r -n` (unprivileged user namespaces) and Google
Chrome or Chromium; set `CHROME=/path/to/chrome` if it is not on `PATH`. It writes the
screenshots above, plus `capture.log`, `traffic.log`, `simulator-{1,2,3}.log` and
`chrome.log`, to `<outdir>`.

Process cleanup:

- The capture script stops every process it started, by exact PID.
- The capture script runs in its own session (`setsid`), so its process-group id is its
  PID.
- On every exit of the wrapper (normal, an error, Ctrl-C, or the capture script itself
  killed), the wrapper's trap signals that whole group unconditionally: TERM, a wait of up
  to 5 s for the group to empty, then KILL. A process group outlives its leader, so this
  also stops the simulator, the traffic generator and Chrome if the capture script dies
  hard.
- This was checked once: the capture script was sent SIGKILL mid-run, and afterwards `ps`
  showed no process left in its group, and no demo simulator, traffic or Chrome process.

One thing is left behind in that case: the Chrome profile directory
(`$TMPDIR/gui-demo-chrome-*`), which the capture script removes only on its own exit.

## Not exercised by this live session: a manual checklist for the owner

Please check these by hand, or accept them as reviewed from code only:

- The first six items were not produced live here, nor in Task 17's checks (task-17
  report, "Known limits").
- The last item was produced live in Task 17, but not in this session.

- [ ] A **1013** close (the simulator forces off a slow client). The message, and the
      backoff that keeps doubling until a connection lasts 30 s.
- [ ] A **1011** close. Its message and backoff.
- [ ] A **1008** close. Its message and backoff.
- [ ] A **403 Origin refusal.** The page cannot cause one, because it always connects to
      its own `location.host`; only the wording was reviewed.
- [ ] A **seq-jump gap** from a `HandOff` or client-queue drop, as opposed to the eviction
      gap Task 17 produced. It shares the `addGap` code.
- [ ] **Firefox**: the page, and that the CSP (`connect-src 'self'` for the same-host
      `ws://`) is accepted. Only Chrome and Chromium were used.
- [ ] The **503 too-many-clients** state and the **eviction gap after an offline spell**:
      Task 17 produced both live, but they are not in these screenshots.
