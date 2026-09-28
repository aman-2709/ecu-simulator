# GUI M3a live demo on vcan: record and startup commands

**This is a demonstration, not a benchmark.** Nothing here judges M4 performance or any of
P1–P9 ([decisions/0010 §9.2](../decisions/0010-gui-observer-api.md)). The M2 latency `STOP`
([gui-m2-early-check.md](gui-m2-early-check.md)) stays open; this run neither tests nor
relieves it. The `dispatch_us` values visible in the screenshots are dispatcher time from a
single run at a human request rate, and are not evidence about latency.

**The V1.0 and Phase 8b gates are untouched.** No simulator, protocol, profile or API code
changed for this record. The page was fixed in `4b92c9a` (see "Findings"), and these
screenshots were then retaken against it. The other new files are the demo scripts and
this record.

## What was run

| Item | Value |
|---|---|
| Branch, commit | `gui` at `4b92c9a14709ae4f6bf779fd07d98fcf71e3a8f3` (page fix round 2). The capture script's update was committed on top; it changes only the capture steps |
| Date | 2026-09-28, 22:02:17 UTC (capture start) |
| Kernel | Linux 6.8.0-138-generic |
| CPU governor | `powersave` on all 12 CPUs (not pinned; this is not a timing run) |
| Python | 3.12.12 (worktree `.venv`) |
| Browser | Google Chrome 151.0.7922.173, `--headless=new --no-sandbox`, driven over the DevTools protocol |
| Isolation | `unshare -r -n`: a private user and network namespace with its own `lo` and `vcan0`. The host's `vcan0` and `can0` were never used |

The one command:

```
scripts/run_gui_demo.sh <scratchpad>/run4
```

The screenshots in [gui-m3a-live-demo/](gui-m3a-live-demo/) are all from that one run,
copied unchanged, with its `capture.log` (the step log) and `traffic.log` (every request
and answer the generator saw). The same command had been run three times before, to develop
the capture script and against the page before `4b92c9a`. Those runs are not the record,
and none of their screenshots is kept.

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
0x10. A module-level assertion rejects any of those service IDs in the cycle.

Two requests are deliberately ones today's simulator does not serve:

- UDS 0x22 gets NRC 0x11 (`7F 22 11`). The page shows this as outcome "responded", because
  a negative response is still a response.
- OBD mode 07 gets no answer (DEV-11). The page shows "no response".

They show the page's negative and unanswered cases with real traffic.

## The screenshots

The scenario times are the page's own "Scenario t". The scenario raises P0128 pending at
t = 40 s, then confirmed with the MIL requested at t = 75 s. Its speed and rpm timelines
hold each value until the next point; they do not interpolate.

| File | What it shows |
|---|---|
| [a-loading.png](gui-m3a-live-demo/a-loading.png) | **Loading.** "Connecting"; "Loading trouble codes", "Loading vehicle signals", "Loading the exchange history". The first `/api/v1/` request was held in DevTools to capture this; otherwise it lasts well under a second |
| [h1-empty-fresh-start.png](gui-m3a-live-demo/h1-empty-fresh-start.png) | **Empty: a fresh start before any traffic.** Live, t = 1.08 s, seq issued 0. "No exchanges yet. The simulator has not handled a diagnostic request since it started." The scenario is already moving `coolant_temp` and `engine_load` (flash highlights) |
| [b1-live.png](gui-m3a-live-demo/b1-live.png) | **Live, t ≈ 23 s.** 81 exchanges: speed 30, rpm 1600, coolant 48.45 °C, load 30.99 %, throttle 24 %. The rows include OBD 01 PIDs, UDS 19 on 7E1, the 0x22 NRC and the 20-byte 09 02 VIN answer. The log shows the newest row, 81 |
| [b2-live-7s-later.png](gui-m3a-live-demo/b2-live-7s-later.png) | **Live, t ≈ 29–31 s, about 7 s later.** 107 exchanges: speed 55, rpm 1900, coolant 56.85 °C, load 59.8 %, throttle 41 %. The controls have wrapped to a second line, and the log still shows the newest row, 107 |
| [d1-filter-service-0x19.png](gui-m3a-live-demo/d1-filter-service-0x19.png) | **The service filter 0x19.** "12 of 112 shown". UDS 19 rows from 7E0 and 7E1, with "N exchanges hidden by filters (not a gap)" between them |
| [d2-paused-held-rows.png](gui-m3a-live-demo/d2-paused-held-rows.png) | **Paused, after the filter was reset.** "View paused. 19 new exchanges are held"; the header reads "112 exchanges, last seq 131"; the button reads "Resume view". The log is at row 112, the last one before the pause |
| [d3-resumed-log-following.png](gui-m3a-live-demo/d3-resumed-log-following.png) | **3 s after Resume: the log follows.** It reads "143 exchanges, last seq 143", and row 143 is the last visible row. There was no scrolling |
| [d4-scrolled-up-new-rows-below.png](gui-m3a-live-demo/d4-scrolled-up-new-rows-below.png) | **After a real wheel scroll up, with 700 px of deltaY.** The log stays at rows 119–129 while exchanges arrive (156). The page's control reads "12 new rows below, jump to newest" |
| [d5-jumped-to-newest.png](gui-m3a-live-demo/d5-jumped-to-newest.png) | **After a real click on that control.** The control is gone, and the log shows the newest row, 161 of 161 |
| [c1-dtc-pending.png](gui-m3a-live-demo/c1-dtc-pending.png) | **DTC pending, t ≈ 45 s.** P0128 "Pending", "1 of 3 codes set", MIL off. UDS 19 02 FF now answers `59 02 8C 01 28 01` (rows 155 and 160: P0128 in the record). The log is following, at row 162 |
| [c2-dtc-confirmed-mil.png](gui-m3a-live-demo/c2-dtc-confirmed-mil.png) | **DTC confirmed with the MIL, t ≈ 79 s.** P0128 "Confirmed", with pending, confirmed and lamp all yes; "MIL on". speed 80, rpm 2100, coolant 92 °C (the ramp's end). The log is at row 293 |
| [g1-narrow-390.png](gui-m3a-live-demo/g1-narrow-390.png) | **390 px wide, viewport.** The status bar as a two-column grid; the trouble codes with MIL on |
| [g2-narrow-390-full-page.png](gui-m3a-live-demo/g2-narrow-390-full-page.png) | **390 px wide, full page.** Panels stacked; log rows as blocks, rows 296–301, ending at the newest |
| [e-disconnected-stale.png](gui-m3a-live-demo/e-disconnected-stale.png) | **Disconnected, about 4 s after SIGTERM, at desktop width.** "Disconnected, retry in 4 s". The banner reads "Last live 22:03:41 UTC (3 s ago) … the simulator is shutting down (1001). Latest retry: status request failed …", with "Retry now". Each panel shows "Stale, as of 22:03:41 UTC", and the status readouts are struck through. **All three DTC rows (P0128, P0171, B1477) and the endpoints line are visible, unclipped.** The data (304 exchanges, MIL on) is kept |
| [f-reconnected-after-restart.png](gui-m3a-live-demo/f-reconnected-after-restart.png) | **Reconnected after a restart.** Live at t = 4.63 s, MIL off, P0128 not set. A real wheel scroll up (about 810 px) brought the page's marker into view: "Simulator restarted. A new run started at 22:03:45 UTC. Rows above are from the previous run; seq starts again at 1." Rows 300–304 of the old run are above it, and the new run's rows 1–6 below. The page's control reads "2 new rows below, jump to newest", counting the rows that arrived after the scroll |
| [h2-no-scenario.png](gui-m3a-live-demo/h2-no-scenario.png) | **No scenario.** `ice_default.yaml`, on a fresh page load with no traffic. Scenario t reads "no scenario"; the vehicle signals read "no scenario: values as configured"; "No exchanges yet". The profile's own confirmed B1477 and P0001 are shown with MIL off, as configured |

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
   scroll in d4 and f; and resumed by a click in d5.
2. **In the stale view the trouble-codes panel clipped its third row** at desktop width,
   under the disconnected banner. **Fixed in `4b92c9a`:** e shows all three rows.

One observation remains, not a defect: the API's `summary` strings for OBD 01 00 and 01 20
read "unknown parameter", and the page shows them as given.

## Test suite at the same commit

These are recorded for completeness. The demo adds no tests.

- **The namespace suite without vcan0**, the baseline form:
  `unshare -r -n bash -c 'ip link set lo up && .venv/bin/python -m pytest -p no:cacheprovider -q -rsx'`
  gave **1247 passed, 69 skipped, 2 xfailed**. The 69 skips are the vcan integration
  tests, which need a `vcan0`.
- **A separate note, not the baseline:** the same suite run in a namespace that also had a
  `vcan0`, at `aafdac2` before the page fix, gave 1315 passed, 1 skipped, 2 xfailed. The
  vcan0 lets 68 of the 69 integration tests run; the total is the same.

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
`chrome.log`, to `<outdir>`. The capture script stops every process it started by exact
PID. A trap in the wrapper stops the capture script's own process group if anything
remains, on Ctrl-C, an error, or a normal exit.

## Not exercised by this live session: a manual checklist for the owner

These were not produced live here, nor in Task 17's checks (task-17 report, "Known limits").
Please check them by hand, or accept them as reviewed from code only:

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
