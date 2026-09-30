# GUI M3b: live signal graphs — design

Status: **revised after the owner's review of 2026-09-30. Design only: nothing is
implemented, and nothing is vendored.** Implementation approval follows the owner's review
of this revision.
- **Approved as designed** (owner, 2026-09-30, §2): five separate graphs with the VIN kept
  as text; the 30 s / 2 min / 10 min windows; stepped rendering; bounded browser history;
  gaps at restart and disconnect; "rpm" as the unit in the graph and the signal table; no
  sample data in the shipped page.
- **Revised here, as the owner required:** the layout and its pixel budget (§5.2, §7); the
  graph pause (§6.9); the non-finite rule, its containment on the server, and the page's
  handling of malformed messages (§8); the acceptance checks (§12); Firefox (§13); the
  estimates (§11); and decision 0010, amended in the same commit (§15).
- The first version of this document is `9a69d2e`.

Written on branch `gui` (worktree `.claude/worktrees/gui`), against the code at `4f00694`.
- Every "fact" was checked against that code, with file:line.
- Everything under a "Proposal" heading, or written as "M3b will", is a proposal.
- uPlot facts come from the npm tarball `uplot-1.6.32.tgz` (downloaded 2026-09-30, npm
  `shasum` `c800a63b432bad692d6d746f44f0882aa73a49ae`) and from uPlot's documentation via
  Context7 (`/leeoniya/uplot`).
- **Every cost figure is an estimate until it is measured** (§11). Pixel figures for
  1440 × 900 were measured from the M3a screenshot
  `docs/validation/gui-m3a-live-demo/m-cruising.png`; the rest are arithmetic from CSS.

**Unaffected and still open:**
- **The M2 early-check latency `STOP` stays open and is not accepted**
  (`docs/decisions/0010-gui-observer-api.md`, status; `docs/validation/gui-m2-early-check.md`).
  M3b does not measure it. §8.3 changes the state task, which is server code; see §16.
- **The hosted-CI `CAN_ISOTP` gap stays open.** GitHub-hosted runners have no `can_isotp`,
  so the vcan integration test skips there (0010 §9.3). Decision 0009 proposes the runner
  that would close it. Every live check below runs on a vcan host only.
- **The Phase 8b gate and the V1.0 branch rule are unchanged.** `gui` is not merged into
  `modernization` until V1.0 is tagged (0010, status).

## 1. The request

The owner's words, first review:

> "Design M3b live signal graphs for the existing read-only GUI. Use only real WebSocket
> state updates and a bounded browser-side history; start with speed, RPM, throttle/load
> and coolant temperature, with units and a selectable time window. Keep VIN as text.
> Specify behavior for missing signals, disconnect/reconnect, simulator restart, scenario
> loop boundaries and narrow screens. Work offline with packaged assets and no CDN or build
> step. Do not change the API state rate or diagnostic path. Estimate browser memory/CPU
> cost and identify any new tests and manual checks. Write and commit a design for my
> review; do not implement M3b yet. Keep the M2 latency STOP and hosted CAN_ISOTP gap open."

**Not proposed:** a new endpoint, a new message type, a state history on the server, a
change to the state rate or to the diagnostic path, a build step, a JavaScript test
framework, any control from the browser.

**Proposed, and new since `9a69d2e`, at the owner's request:** one additive field,
`nonfinite`, on `GET /vehicle` and the WS `state` message; one additive counter,
`state_encode_failed`, on `GET /status`; and containment of state-encoding failures in the
server (§8). These are API and server changes. They touch neither the state rate nor the
diagnostic path.

## 2. Owner decisions (2026-09-30)

**Approved as designed:**
- five separate live graphs (speed, rpm, throttle, load, coolant), with the VIN kept as
  text;
- the 30 s / 2 min / 10 min windows;
- stepped rendering;
- bounded browser history;
- gaps at restart and disconnect;
- "rpm" as the unit in both the graph and the signal table;
- no sample data in the shipped page.

**Revisions required** (summarised; each is answered in the section named):
1. **Layout:** a collapsible section above the log, open by default, with the graph
   height capped so the log stays usable at 1440 × 900. A pixel budget, and the 390 px
   layout (§5.2, §7).
2. **Pause:** "Pause graphs" is separate from the log's pause. Paused graphs keep
   buffering within the limits; resume jumps to the latest data (§6.9).
3. **The non-finite rule,** kept separate from DEV-26's profile validation on
   `modernization`, which is a different layer: `null` plus a sorted `nonfinite` list;
   containment of any encoding failure in the state task, `GET /vehicle` and the startup
   check; and a page that reports malformed messages and stops claiming Live (§8).
4. **Acceptance checks** through visible readouts and read-only `data-*` attributes, never
   uPlot internals, for the behaviours most likely to regress (§12).
5. **Firefox:** the CSP check stays manual, for Chrome and Firefox; unverified until run
   (§13).
6. **Estimates** labelled as such, with the method of measurement (§11).
7. **Decision 0010** amended: status line, M3b wording, `nonfinite` and containment as
   specified-not-implemented, a tenth-revision note (§15).

## 3. What 0010 said about M3b before this revision (facts)

| Where (0010 at `4f00694`) | What it said | What M3b does |
|---|---|---|
| §7, `:563-564` | "Milestone 3b vendors **uPlot** (MIT) for per-signal sparklines, with its licence file and pinned version. The MVP (3a) works without it." | Vendors uPlot 1.6.32 with its licence, pinned by hash. The page still works if the graphs fail |
| §7, `:555-556` | "No build step, no npm, and no CDN at runtime" | The prebuilt IIFE file is vendored as published |
| §7, `:565-570` | Decoding stays in Python; frontend file tests and the owner's manual checklist are "the M3a and M3b exit"; no JavaScript test framework | Graphs only what the API decodes; no JS tests |
| §6, `:550-551` | Static files are package data, from a fixed directory, no listing, no path parameters | Each new file gets its own fixed route |
| §9.3, `:728` | "every file M3 adds is served with its content type, and nothing else is" | Extended to the vendored files |
| §10, `:747` | M3b: "Sparklines with vendored uPlot"; exit "Owner runs the manual view checklist for sparklines" | Rewritten in the tenth revision (§15) |
| §4.3, `:332` | "`state` push rate: at most 4 Hz" | Unchanged |
| §5, `:415` | `GET /vehicle`: `kind`, `vin`, `signals`, `as_of` (`null` without a scenario), `unavailable` | Gains `nonfinite` (§8.2) |

## 4. How state reaches the page (facts)

### 4.1 The server side

- **The push loop.** `Publisher.run_state` takes a snapshot, pushes it only if its text
  differs from the last one, then sleeps (`observe/publisher.py:265-272`). The interval is
  `STATE_MIN_INTERVAL_S = 0.25` (`observe/limits.py:16`), passed in by `ApiServer`
  (`api/server.py:179`, `:247`). So the page receives **change events at most every
  0.25 s**, not a fixed sample rate.
- **`run_state` has no `try`.** An exception from `snapshot()` ends the task. `ApiServer`
  then logs "observer state task failed; the observer API is degraded until restart"
  (`api/server.py:146-149`, `:249-250`), and no client gets another `state` for the rest
  of the run.
- **The message.** `snapshots.state_message` is `json.dumps({"type": "state", "vehicle": …,
  "dtcs": …})` (`observe/snapshots.py:56-58`). `json.dumps` is called with its default
  `allow_nan=True`. `vehicle` holds `kind`, `vin`, `signals` (every stored value), `as_of`
  and `unavailable` (`snapshots.py:16-26`).
- **`GET /vehicle`** is `web.json_response(snapshots.vehicle(...))` (`api/server.py:283-284`),
  which also uses `json.dumps` with its defaults.
- **Startup.** `ApiServer.__init__` runs `snapshots.check_state_size` (`api/server.py:165-168`,
  `snapshots.py:61-64`), which encodes the state once, then pushes the initial state
  (`api/server.py:176`). Only the size `ValueError` becomes `ApiStartupError`; any other
  exception propagates out of the constructor.
- **`as_of`** is `runner.last_applied`, or `None` without a scenario (`snapshots.py:24`).
  `apply` refuses a `t` earlier than the last one (`scenario/runner.py:136-146`), so within
  one run **`as_of` never decreases**.
- **What moves `as_of`:** the dispatcher, before every request (0010 §12 E3), and the tick,
  which sleeps `period` then calls `sync()` (`app.py:261-265`). `period` is
  `scenario.tick`, default 1.0 s (`config/schema.py:194`); the stepped demo uses 0.5 s
  (`docs/examples/ice_drive_cycle_stepped.yaml:48`).
- **Consequence:** with a scenario, a state message arrives on every push turn in which
  anything applied: up to 4 Hz with a tester, about once per tick on an idle bus. A value
  changes only when its generator's output changes; under the stepped demo, speed and rpm
  change once a second.
- **Without a scenario** the runner is `None` (`app.py:184-193`), nothing changes the
  state, and a connection receives **no state message after its first**.
- **Before the first apply** (up to one `period` after start, `app.py:261-265`, `:352`),
  `as_of` is `null` even though a scenario exists.
- **Restart.** A new process has a new `started_at` (`api/server.py:177`), reported by
  `GET /status` (`snapshots.py:76`). Its scenario time starts again at 0
  (`scenario/sync.py:8-10`).
- **No history of state.** The server keeps one latest `state` per client
  (`publisher.py:254-257`, 0010 §4.3). `GET /exchanges` holds exchanges only.

### 4.2 What a non-finite value does today (facts and standard behaviour)

- The state model's floats accept `NaN` and `±inf`. DEV-26 (open, on `modernization`)
  records that a profile can put them there, through the schema's unconstrained floats
  (for example `config/schema.py:65`, `engine.coolant_temp`) or a scenario generator.
- `json.dumps` writes them as the bare tokens `NaN`, `Infinity` and `-Infinity`, which are
  not JSON. That is Python's documented default. So today the WS `state` and
  `GET /vehicle` would carry them.
- `JSON.parse` rejects those tokens. That is the ECMAScript definition. So:
  - the page's `onMessage` drops the whole frame **silently**: `try { m = JSON.parse(text); }
    catch (e) { return; }` (`app.js:260`). Nothing is counted or shown. With a scenario
    that keeps producing a non-finite value, every `state` is dropped, and the page keeps
    showing its last good values with the lamp still "Live";
  - `GET /vehicle` fails in `getJSON`, so the first load of a run fails as a request
    error.
- Not measured in the page. The two JSON behaviours are standard; §12 adds a live check.

### 4.3 The page today (`src/ecu_simulator/api/static/`)

- **Files.** `index.html` loads `app.css` and `app.js` (`index.html:9-10`). `app.js` is one
  IIFE (`app.js:9`).
- **State in.** `onMessage` parses each frame (`app.js:258-267`). A `state` message calls
  `applyState` (`app.js:300-304`). The first data of a run comes from `GET /vehicle`,
  `/dtcs` and `/ecus` in `connect()` (`app.js:156-170`).
- **Restart detection.** `connect()` compares `GET /status`'s `started_at` with the stored
  one and, if it changed, adds "Simulator restarted." to the log and refetches the
  snapshots (`app.js:147-160`).
- **Resume.** A reconnect opens `/events?after=lastSeq` (`app.js:188`); `hello` and a
  `state` follow (0010 §4.5); `onHello` marks "Connection lost, then resumed."
  (`app.js:269-297`).
- **Going down.** `fail()` sets the phase to `down` or `refused` (`app.js:223-240`). The
  status poll runs every 2 s with a 5 s timeout, and a failed poll calls `fail()`
  (`app.js:14`, `:19`, `:243-256`). **So a simulator that stops answering, for example
  under `SIGSTOP`, takes the page down within about 7 s**, although its socket stays open.
- **Stale.** `renderLink()` sets `body.is-stale`, shows every `.stale-tag` with "Stale, as
  of HH:MM:SS UTC", and fills the banner (`app.js:408-452`); CSS hatches each panel's top
  edge and fades `.panel__body` (`app.css:116-125`). Stale data is kept and labelled.
- **Vehicle panel.** Header "as of scenario t = N s" or "no scenario: values as configured"
  (`app.js:457`). The VIN is shown once, as text (`app.js:458-472`). A path in
  `unavailable` shows "—" and "unavailable, no source" (`app.js:461-464`, `:491-500`).
- **Units.** `UNITS` (`app.js:39-47`) has km/h, %, % and °C for the other four signals,
  and **no entry for `engine.rpm`**.
- **Log controls** act on the log only (`app.js:864-875`; `index.html:67`).
- **Layout** (`app.css:128-136`, `:320-398`): above 1100 px wide and 640 px high, a
  fixed-height shell with a side column `clamp(380px, 25vw, 520px)` and the log filling the
  rest; up to 1100 px, one column; up to 700 px, log rows become blocks. The root font is
  `clamp(0.875rem, 0.45vw + 0.55rem, 1.125rem)` (`app.css:34`): about 15.3 px at 1440 px
  wide and 14 px at 390 px, at the browser's default 16 px.

### 4.4 The served file list (facts)

- `api/server.py:44-48` `FRONTEND` maps each route to `(file, content type)`. Each body is
  read once, at construction (`server.py:186-188`), with its own handler
  (`server.py:268-275`).
- `FRONTEND_HEADERS` (`server.py:51-59`) set the CSP `default-src 'self'; script-src 'self';
  style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none';
  form-action 'none'; frame-ancestors 'none'; object-src 'none'`, with `nosniff`,
  `no-referrer` and `no-cache`.
- `tests/unit/api/test_frontend_files.py` keeps its own copy of the list (`:14-18`) and
  asserts: body, type and charset per route (`:34-41`); the headers (`:44-49`); **the static
  directory holds exactly the served files** (`:60-63`); read once (`:66-83`); other paths
  404 (`:86-134`); other methods 405 (`:137-142`); the Host guard (`:145-149`); and no
  `http://`, `https://`, `//cdn` or **"sample"** in `index.html`, `app.js` and `app.css`
  (`:152-157`).
- **Packaging.** `pyproject.toml:63` ships `src/ecu_simulator/api/static/*`. The glob
  covers new files, so `pyproject.toml` needs no change. CI installs editable
  (`.github/workflows/ci.yml:88`).

## 5. The renderer and the layout

### 5.1 uPlot 1.6.32 (facts), and the choice

| | |
|---|---|
| Version | **1.6.32**, npm `latest` on 2026-09-30. MIT, "Copyright (c) 2022 Leon Sorokin" |
| `uPlot.iife.min.js` | 51,081 B raw, 22,009 B gzip -9. SHA-256 `19c8d4c6ad88929a79f4ae49d6f7161566dfd0ba3d15cc495e974f787eb78f1f`. First line `/*! https://github.com/leeoniya/uPlot (v1.6.32) */` |
| `uPlot.min.css` | 1,857 B raw, 772 B gzip. SHA-256 `df630c6a8d6f8eeaff264b50f73ce5b114f646ffd9a0bb74f049b0a00135fa04` |
| `LICENSE` | 1,078 B raw, 660 B gzip. SHA-256 `8f989229699b4fe2f1a0432d0e9edc338a8a911e250e2d1b01ecd770a5f5b1bd` |

The server does not compress, so each page load fetches 54,016 B more over loopback.

**What it needs:** a container and pixel sizes (`new uPlot(opts, data, target)`,
`u.setSize`); one canvas per chart (one `getContext` in the build); columnar data with
ascending unique x and numbers or `null` for y, length ≥ 2; `null` breaks the line with
`spanGaps: false`; `paths: uPlot.paths.stepped({align: 1})` holds each value to the next x.
It follows `devicePixelRatio` itself (`matchMedia`) but has no ResizeObserver.
`.uplot { width: min-content }`, so a chart is exactly as wide as it is told.

**CSP:** a scan of the minified file found 0 × `setAttribute("style"`, `cssText`,
`createElement("style")`, `innerHTML`, `insertAdjacentHTML`, `eval(`, `new Function`,
`fetch(`, `XMLHttpRequest` and `localStorage`; and 12 × `.style.` property writes. CSSOM
property writes are not inline style under CSP; the stylesheet is same-origin. **So the
CSP stays exactly as it is.** This is an inference; the manual Chrome and Firefox checks
confirm it (§13).

**Published performance** (uPlot README; Chrome 113, Ryzen 7 PRO 5850U, 2023-03-11):
166,650 points in 34 ms cold, heap peak 21 MB and final 3 MB (v1.6.24); "~31,000 pts/ms".

**Against a hand-written renderer:** a canvas or SVG renderer would be about 200–300 lines,
fully reviewable, with no vendoring. But it must reinvent axes, tick spacing, HiDPI and
resize handling, which is where hand-written charts break. uPlot is 22 KB gzip, pinned by
hash, and already named in 0010. **Recommendation, unchanged: uPlot.**

### 5.2 Proposal: the graphs section at 1440 × 900 (revised)

**Placement.** A collapsible section, **"Signal graphs", above the exchange log in the
main column, open on every page load.** The open or closed state is not saved, so the
default is always open.
- The toggle is a button in the section head, "Hide graphs" / "Show graphs", with
  `aria-expanded` and `aria-controls`. A `<details>` element is not used, because the head
  also holds buttons, and interactive content inside `<summary>` is not reliable.
- Closed, the section keeps only its head, which reads "Signal graphs — hidden, still
  recording". The rings keep buffering (§6.3), and nothing is drawn.
- **Wide shell:** the second grid column becomes a flex column: the graphs section
  (natural height, `flex: none`) above the log panel (`flex: 1 1 auto; min-height: 0`), so
  the log keeps the rest and scrolls inside, as now.

**Cards.** Five cards in a grid, `repeat(auto-fit, minmax(11rem, 1fr))`: one row at 1440
and above.
- Line 1: the name and unit on the left, the current value on the right: "Speed · km/h …
  **80**".
- The plot.
- Line 2: "min 0 · max 80 in 2 min".

**The height cap.** Plot height is `clamp(3rem, 9vh, 4.75rem)`: about 73 px at 1440 × 900,
81 px at 2000 × 1100, never above 4.75 rem. The head uses the compact button style of the
log's "Jump to newest" control (`app.css:259-263`), so it is one short row.

**Pixel budget at 1440 × 900, dpr 1.** "Measured" rows come from the M3a screenshot
`m-cruising.png`. "Arithmetic" rows are CSS at a 15.3 px rem, to be confirmed by the
capture run (§12.2).

| Part | Height | Source |
|---|---|---|
| Status bar | 90 px | measured |
| Layout top padding | 13 px | measured |
| **Graphs section** | **≈ 186 px** | arithmetic, below |
| — top border, head (compact buttons, 0.571 rem padding), rule | 3 + 40 + 1 px | arithmetic |
| — body padding (0.571 rem top, 0.857 rem bottom) | 22 px | arithmetic |
| — card: line 1 (20), plot (73), line 2 (17), card padding (10) | 120 px | arithmetic |
| Gap between section and log | 13 px | the layout gap, 0.857 rem |
| Log head | 45 px | measured |
| Log filter bar (wraps to two rows at this width) | 124 px | measured |
| Log table header | 27 px | measured |
| **Log rows region** | **≈ 303 px** (502 px today) | 502 measured, minus 199 |
| Log footer | 80 px | measured |
| Bottom padding | 18 px | measured |
| **Total** | 900 px | |

- **Log rows left visible: at least 5 full rows**. The M3a rows at this width are about
  52 px (two lines: summary and endpoint); single-line rows are about 30 px, so 5–10 rows
  show, against about 9–16 today.
- **Requirement:** with the section open at 1440 × 900, **at least 5 full log rows are
  visible**. The capture run checks it (§12.2). If it fails, the plot height is reduced
  before anything else is changed.
- Closing the section returns about 145 px to the log.
- Not proposed, but noted: the log's filter bar wraps to 124 px at this width. Fitting it
  on one row would return about 50 px. That is a separate change, if the owner wants it.

### 5.3 Signals, units and scales

| Graph | Path | Unit | y scale |
|---|---|---|---|
| Speed | `vehicle.speed` | km/h | 0 to a nice value above the window's maximum, at least 20 |
| Engine speed | `engine.rpm` | **rpm** (new `UNITS` entry, approved) | 0 to a nice value above the window's maximum, at least 1000 |
| Throttle | `engine.throttle` | % | fixed 0–100 |
| Engine load | `engine.engine_load` | % | fixed 0–100 |
| Coolant | `engine.coolant_temp` | °C | window minimum and maximum, padded by 2 °C, at least 10 °C wide |

- Five separate graphs, as approved. Throttle and load sit next to each other on the same
  fixed scale.
- The VIN stays text (`app.js:469-472`). Only numbers are graphed.
- `"engine.rpm": "rpm"` is added to `UNITS`, so the signal table's rpm row shows "rpm"
  too.

## 6. Proposal: data model and behaviour

### 6.1 The time base: scenario time (`as_of`)

Each x value is **`as_of`**, the scenario time the values were applied at, not the
browser's receive time.
- It has no network or coalescing jitter; it never decreases within a run
  (`runner.py:136-146`); the demo's steps land on whole seconds and its loop on multiples of
  90 s; and the vehicle panel already speaks in it (`app.js:457`).
- Its costs are handled below: `null` without a scenario (§6.5), a freeze while
  disconnected (§6.7), and a reset on restart (§6.8).
- The x-axis is "scenario t, s" (`scales.x.time: false`).
- **What `as_of` does not say.** `apply` sets every driven signal to its value at `t`
  (`runner.py:128-148`), and the publisher sees only the latest state per turn. A step is
  therefore drawn at the `as_of` of the first message that shows it, which can be up to one
  push interval (0.25 s) or one tick after the apply that made it.

### 6.2 Step-hold

A stored value **holds until the next apply**; nothing interpolates. So the graphs draw
steps, never ramps (`paths.stepped({align: 1})`). A continuous generator, such as the
coolant ramp or `ice_scenario.yaml`'s sine on `engine.engine_load`
(`src/ecu_simulator/profiles/ice_scenario.yaml:103-104`), is drawn as a fine staircase,
because that is what the stored value did. At draw time one point, (latest `as_of`, last
value), is added so the hold reaches the right edge; it is not stored.

### 6.3 The ring buffer

One ring per graphed signal, preallocated: `t` and `v` as `Float64Array(4096)`, plus
`start` and `count`.
- A point is stored **only when the value changes**, or around a gap. A message that moves
  only `as_of` updates `lastAsOf`.
- A gap is a point whose value is `NaN`, turned into `null` for uPlot.
- An `as_of` equal to the last stored `t` replaces that point, so x stays unique.
- **Caps, enforced on every append:** points older than the latest `as_of` − 600 s are
  dropped, **except the newest of them, which is kept so the held value enters the window
  from its left edge**; and never more than **4096** points (10 min at 4 Hz is 2,400).
- **Bytes:** 64 KiB per signal, 320 KiB for five, allocated once.
- History beyond 10 min is discarded, and the panel's fine print says so.

### 6.4 The window

- **30 s, 2 min (default), 10 min**, as approved: a three-button group in the section
  head, `aria-pressed`, applying to all five graphs.
- The x range is always the full window, `[latest as_of − W, latest as_of]`. While history
  is shorter, the left part is empty, with "history starts at t = N s (when this page
  connected)".
- Saved in `localStorage` under `ecu-simulator.graphs.window` (`30`, `120` or `600`), every
  access in `try/catch`; any failure or unknown value means 2 min.

### 6.5 Missing signals and missing time

| Case | How the page knows | What it shows |
|---|---|---|
| Path in `unavailable` (no source) | `vehicle.unavailable` | A card with no plot: "— unavailable, no source", the table's words. Not reachable for these five paths with the shipped profiles |
| Path not on this vehicle kind (a BEV has no `engine.*`, `vehicle/state.py:104-110`) | Absent from `signals` | No card; one line: "Not on this vehicle (bev): engine.rpm, …" |
| Path in `nonfinite`, or its value is not a finite number | §8.4 | "invalid value" as text, and a gap |
| `as_of` null, `scenario.enabled` false (`snapshots.py:77`) | `GET /status` | No plots: "No scenario: the values are constant, as configured. Graphs follow scenario time." |
| `as_of` null, a scenario enabled | The same fields | No plots yet: "Waiting for the first scenario tick." |

### 6.6 Scenario loop boundaries

- `stepped` returns `values[floor(t / interval) mod len(values)]`
  (`scenario/generators.py:96-103`). `t` is elapsed time and never wraps
  (`scenario/sync.py:30-35`), so at the demo's 90 s boundary `as_of` keeps increasing and
  only the index wraps.
- **The demo wraps idle to idle.** At t = 89 and t = 90: speed 0 and 0, rpm 800 and 800,
  throttle 0 and 0, load 20 and 20 (`ice_drive_cycle_stepped.yaml:62`/`:54`, `:76`/`:68`,
  `:90`/`:82`, `:104`/`:96`). No value changes, so no point is stored, and the line runs
  flat through 90 s. The next steps come at 96 s.
- Coolant ignores the loop: a ramp from 20 to 90 °C over 240 s, then held
  (`ice_drive_cycle_stepped.yaml:107-111`).
- **No cycle markers.** Neither `GET /status` (`snapshots.py:67-81`) nor `state` carries
  the cycle length, and the design does not guess one.

### 6.7 Disconnect and reconnect

- While down, the graphs keep their data with the page's stale styling, and the right edge
  stops at the last `as_of` received.
- **The line breaks; nothing is interpolated.** `fail()` on a live page sets a pending break.
  On the first message of the next connection, each ring adds: the last value at the last
  known `as_of` (so the hold stops where knowledge stops), then a `NaN` point, then the new
  value.
- On reconnect, `GET /status`, then `hello` and a `state` resume the series (§4.3).
- **No backfill. The API keeps no history of state**, so the outage is unknown to the page.
  The readout says "No data from t = A to t = B s (disconnected)". Rebuilding values from
  logged OBD responses would put decoding in the browser, which 0010 §5 and §7 forbid.

### 6.8 Simulator restart

- Detected by the existing `started_at` check (`app.js:149-155`); an `as_of` below the
  ring's last `t` is treated the same way, as it cannot happen within a run.
- **The graphs are cleared**, with the note "Simulator restarted at HH:MM:SS UTC. Graphs
  start again from scenario t = 0; the previous run's graphs were cleared." Two runs are
  never joined. The log keeps its own marker.

### 6.9 Pause (revised)

- **"Pause graphs" is its own toggle** in the section head, `aria-pressed`, separate from
  the log's "Pause view". Neither affects the other, and the log's filters and clear do not
  touch the graphs.
- **While paused:** drawing, the current values and the min/max freeze, and the head says
  "Paused at t = N s". **Every message is still stored in the rings, within the same caps**
  (§6.3): 600 s and 4096 points. Nothing is dropped beyond what those caps drop.
- **Resume jumps to the latest data**: the window ends at the latest `as_of` received, not
  where the pause began.
- If a resize happens while paused, the frozen window is redrawn from the rings. Points the
  600 s cap has trimmed are gone, and the readout says "history trimmed while paused".
- Hiding the section (§5.2) does not pause; it only stops drawing.
- No "clear graphs": a restart clears them.

## 7. Proposal: 390 px (revised)

- **Order:** status bar, the two side panels, the graphs section, then the log, as the
  medium and narrow layouts already stack (`app.css:335-398`). The section is open by
  default here too.
- **Head:** two rows. "Signal graphs" and "Hide graphs" on the first, the three window
  buttons and "Pause graphs" on the second, full width, like the log's buttons
  (`app.css:358-359`).
- **Cards:** one per row. With a 14 px rem, the layout padding is 8 px each side and the
  panel body's 10 px (`app.css:353-355`), so each plot is about **352 px wide**. The y-axis
  takes at most 3 rem (42 px), leaving about 310 px of plot. Plot height at 390 × 844:
  `clamp(3rem, 9vh, 4.75rem)` gives 66 px.
- **Height:** a card about 115 px; five cards and gaps about 610 px; with the head, about
  690 px. The page scrolls vertically at this width anyway.
- **No horizontal overflow:** a ResizeObserver on the card grid (as `app.js:791-797` already
  uses one) calls `u.setSize({width: floor(card content width), height})`, coalesced into
  one animation frame. A `min-content` chart given its card's width cannot be wider.
- Axis text is the page's `--sans` at 0.786 rem, read from computed style; x ticks at least
  50 px apart, so about five labels.

## 8. Proposal: the non-finite rule, containment, and malformed messages (new)

### 8.1 Scope and layers

- **This is the observer API's rule, not profile validation.** DEV-26, open on
  `modernization`, is about rejecting non-finite values **at profile load**. That is the
  config layer, and it is recorded and scheduled there. Nothing here depends on it, fixes
  it, or is mixed into it.
- The observer rule holds **whatever the source**: a profile today, or any future writer.
  The API never emits a token that is not JSON, and the state task never dies from
  encoding.
- The diagnostic path is unchanged. DEV-26's crash in the OBD encoder
  (`protocols/obd/pids.py:80`, recorded in DEV-26) is not touched.

### 8.2 The API: `null` and `nonfinite`

- **In `signals`**, a value that is a Python `float` and not `math.isfinite` is sent as
  JSON **`null`**. Other values are unchanged (`int` and `bool` cannot be non-finite;
  strings are sent as they are).
- **A new field, `nonfinite`:** the dotted paths whose value was sent as `null` for that
  reason, **sorted, always present, possibly empty.**
- **Where:** `GET /vehicle`, every WS `state` message (including the one sent on connect),
  and so the initial state and the startup size check, because all of them are built by the
  same `snapshots.vehicle`.
- **Per snapshot, not per run.** Unlike `unavailable`, which is fixed at startup
  (`observe/availability.py`), `nonfinite` describes the values in that snapshot. It costs
  one `isfinite` per float signal, about 20 per snapshot, at most 4 times a second
  (**estimate:** a few microseconds).
- **`unavailable` and `nonfinite` stay distinct.** *No source* means nothing in the profile
  can set the path, so its stored value is a default and not a reading. *Invalid reading*
  means a source produced a value that is not a finite number.
- **A path is never in both. If both would apply, `unavailable` wins:** the path is listed
  only in `unavailable`, and its value is still sent as `null`, because JSON cannot carry
  it.
  - Why: `unavailable` is a static fact about the profile. A path with no source has
    produced no reading, so it cannot have produced an invalid one; listing it in
    `nonfinite` would suggest a source that does not exist.
  - Today this case cannot arise: a path with no source holds its state-model default, and
    every default is finite (`vehicle/state.py:19-81`). The rule makes the answer explicit
    in case a future default changes.
- **Example:**
  `{"kind": "ice", "vin": "…", "signals": {"engine.coolant_temp": null, …}, "as_of": 12.5,
  "unavailable": ["vehicle.odometer"], "nonfinite": ["engine.coolant_temp"]}`.

### 8.3 Containing encoding failures on the server

The mechanism is **sanitise, then encode strictly, then contain**:
1. **Sanitise before `json.dumps`:** `snapshots.vehicle` builds `signals` with non-finite
   floats replaced by `None`, and builds `nonfinite` (§8.2).
2. **Encode strictly:** `state_message` calls `json.dumps(..., allow_nan=False)`, and
   `GET /vehicle` uses `web.json_response` with `dumps` bound to the same strict call. After
   step 1, a non-finite value can no longer reach the encoder. The strict flag is the guard
   that turns any that still does, anywhere in the message, into an exception instead of
   invalid JSON.
3. **Contain any residual failure:**
   - **The periodic state task** (`publisher.py:265-272`): `snapshot()` runs inside
     `try/except Exception`. On failure, **that push is skipped**, the previous state
     stays current for every client, the new counter **`state_encode_failed`** increments,
     and the failure is logged **once per exception type**, through the same `_log_once`
     that `_publish` uses for `encode_failed` (`publisher.py:113-116`, `:151-156`). The
     loop then continues: `push_dropped()` still runs and the next interval is tried as
     usual. `CancelledError` is not caught, so shutdown is unchanged.
   - **`GET /vehicle`:** a residual failure answers **HTTP 500** with the text "vehicle
     state could not be encoded (decisions/0010 §4.3)", increments `state_encode_failed`,
     and is logged once per type. Every other route keeps working.
   - **Startup** (`check_state_size` and the initial `push_state`,
     `api/server.py:165-176`): non-finite values are already sanitised, so they start
     normally. Any **other** failure becomes `ApiStartupError`, so `--api` refuses to start
     with exit 2 and a message naming the exception type, as the 256 KiB rule does. There
     is no earlier state to fall back on, and 0010 §4.5 requires one before the first
     client.
- **`state_encode_failed`** is a new field of `GET /status` `api`: failed state encodes,
  cumulative, never reset, counting both skipped pushes and failed `GET /vehicle` answers.
  The log line names which.
- **Why skip rather than send a fallback:** exchanges need a fallback event to keep `seq`
  contiguous (0010 §5). `state` has no sequence and is replaced, not queued (0010 §4.3), so
  the latest good state is the honest thing to keep. The page learns of the failure from
  the counter (§8.4).

### 8.4 The page: invalid values and malformed messages

**Invalid values.**
- A path in `nonfinite`, or any graphed or tabled value that is not a finite number and not
  in `unavailable`, is shown as **"invalid value"** as text, in the signal table and in
  that graph's line 1. Not colour alone: the words are there.
- **The graph leaves a gap, never a line to or from it.** When an invalid value arrives at
  `as_of = t`, the ring stores the last valid value at the last `as_of` it was known valid,
  then a `NaN` point at `t`. When a valid value returns at a later `as_of`, it starts a new
  segment there. Nothing is drawn between the last valid point and the next valid point.
  uPlot's gap handling for stepped paths is checked through the `data-*` attributes and by
  eye (§12, §13); if its default draws the hold past the last valid point, the
  implementation sets the series' gap alignment so it does not.

**Malformed messages** (a WS frame that fails `JSON.parse`, or parses to something that is
not an object). Today they are swallowed (`app.js:260`). M3b will:
- **Count them.** `malformedTotal` for the page's lifetime, and `malformedRun`, the number
  in a row since the last valid frame.
- **Show them.** When `malformedTotal > 0`, the status bar gains a readout "Malformed
  messages **N**, last HH:MM:SS UTC". It is not hidden again.
- **Stop claiming Live** (a new "degraded" state) when any of these holds:
  1. `malformedRun ≥ 3`;
  2. a malformed frame arrived, and **10 s** passed with no valid `state` after it;
  3. the polled `state_encode_failed` (§8.3) is higher than when the last valid `state`
     arrived, and **10 s** passed with no valid `state` since the page saw the increase.
  - The 10 s wait is long compared with a 0.25 s push and the demo's 0.5 s tick. Only a
    scenario with a tick above 10 s and no traffic could wait that long between states,
    and then only after a malformed frame or a failed encode, which is already an anomaly.
- **While degraded:**
  - the lamp is not green, and reads "Connected, data invalid";
  - a banner says why: "N malformed messages from the simulator; the vehicle, trouble-code
    and graph panels show the last valid state, from HH:MM:SS UTC", or "The simulator could
    not encode N state updates (`state_encode_failed`)";
  - the vehicle, DTC and graphs panels get the stale marking, with the tag "Not current:
    last valid state HH:MM:SS UTC". The exchange log is not marked, because its events
    still arrive and are valid;
  - each ring gets a pending break, as for a disconnect (§6.7).
- **Leaving degraded:** the next valid `state` resets `malformedRun`, restores "Live", and
  clears the stale marking. The total stays in the status bar.
- The page never closes the socket over this and never sends anything on it.

## 9. Proposal: accessibility

- Each card is a `<figure>`: `<figcaption>` has the name and unit; line 1 has the current
  value; line 2 has the min and max in the window. The plot container is
  `aria-hidden="true"`; the text carries the content.
- **Not colour alone:** one line per graph in the ink colour. Stale and degraded states
  use the hatch, the tag and the banner. A gap is a break **and** words. A restart is a
  note. "unavailable" and "invalid value" are words.
- Readouts are not `aria-live`, because they change up to four times a second.
- Buttons are ordinary buttons. uPlot's cursor, legend and selection are off, so the plot
  has no pointer-only content.
- Nothing is animated.

## 10. Constraints kept, and files

| Constraint | How |
|---|---|
| API state rate | Unchanged: `STATE_MIN_INTERVAL_S` and the push rule stay; §8.3 adds a `try` around the snapshot only |
| Diagnostic path | Unchanged: nothing in `ecu/`, `transport/`, `protocols/` or `scenario/`, and nothing on the hot path, changes |
| Endpoints | None added. `nonfinite` and `state_encode_failed` are additive fields |
| Build, npm, CDN | None: `<script src="uPlot.iife.min.js" defer>` before `app.js` |
| CSP | Unchanged (§5.1) |
| Offline | Package data (`pyproject.toml:63`) |
| Without uPlot | If `typeof uPlot !== "function"`, the section says "Graphs unavailable: the chart library did not load", and the rest of the page works |
| No sample data | The shipped page never contains or draws sample data; the test at `test_frontend_files.py:152-157` keeps banning the word |

| Route | File under `static/` | Content type |
|---|---|---|
| `/uPlot.iife.min.js` | `uPlot.iife.min.js`, byte-identical to the tarball | `text/javascript` |
| `/uPlot.min.css` | `uPlot.min.css`, byte-identical | `text/css` |
| `/uPlot-LICENSE.txt` | the tarball's `LICENSE`, byte-identical | `text/plain` |

The licence is served, so the "exactly the served files" rule needs no exception, and the
footer links to it. `uPlot.min.css` is linked before `app.css`.

## 11. Cost in the browser: estimates until measured

**Every figure in this section is an estimate.** None has been measured. §11.3 says how
they will be.

### 11.1 Memory (estimate)

| Item | Estimate | Basis |
|---|---|---|
| Rings | 320 KiB, fixed | 5 × 4096 × 2 × 8 B |
| Worst-case live data in them | 188 KiB | 2,400 × 5 × 2 × 8 B |
| Arrays handed to uPlot per redraw | about 190–380 KB of short-lived garbage at the worst case | 2,403 × 2 × 5 elements at an assumed 8–16 B each |
| Canvases, 5 × width × height × dpr² × 4 B | 1440 × 900, dpr 1: 190 × 73 px → about 0.27 MiB. dpr 2: about 1.1 MiB. 390 px at dpr 3: 352 × 66 px → about 5.2 MiB | §5.2, §7 sizes |
| uPlot code and instances | small; the README's 166,650-point bench ends at a 3 MB heap | README |

Total: under 2 MiB on a desktop at dpr 1, under about 7 MiB on a dpr-3 phone. Fixed
rings, so no growth over time.

### 11.2 CPU (estimate)

- Per message (≤ 4/s): five compare-and-append steps, and a dirty flag.
- Redraws are coalesced by `requestAnimationFrame`, at most one per message. Worst case 5
  × about 2,400 points: about 0.4 ms of path building at the published ~31,000 points/ms;
  1–3 ms with axes and text; at 4 per second, **about 1 % of one core**.
- A background or hidden section draws nothing; one redraw follows on return.
- **Server side:** §8.2's `isfinite` pass, a few microseconds per snapshot at ≤ 4 Hz.

### 11.3 How they will be measured

In the capture run (§12.2), at 1440 × 900 and at 390 × 844, over a 60 s live stretch with
the traffic script running:
- **Heap:** `performance.memory.usedJSHeapSize` and `totalJSHeapSize` (Chrome only),
  read at the start, every 10 s, and at the end;
- **CPU:** a DevTools trace (`Tracing.start` / `Tracing.end`, categories
  `devtools.timeline` and `v8`), saved with the run, from which the total main-thread task
  time and the time in the graphs' redraw are read. `Performance.getMetrics`
  `TaskDuration` is recorded as a cross-check;
- **Canvas memory** is arithmetic from the measured canvas sizes, read from the `data-*`
  attributes and each canvas's `width` and `height`.

The results go in `docs/validation/gui-m3b-live-demo.md`, next to these estimates. Until
then, §11.1 and §11.2 stay labelled as estimates. The browser shares the host's CPUs with
the simulator when both run on one machine; M4 uses harness clients, not a browser, so no
M4 condition changes.

## 12. Tests and acceptance checks (revised)

### 12.1 Python (`.[dev,gui]` job, and the `observe` tests in every job)

**Frontend files** (`tests/unit/api/test_frontend_files.py`):
- the list (`:14-18`) gains the three rows of §10, so every existing parametrised test
  covers them: body and type, headers, 405, the Host guard, read once, and **exactly the
  served files**;
- new `test_vendored_uplot_is_the_pinned_release`: each file's SHA-256 is §5.1's, and the
  JS starts with the v1.6.32 banner;
- new `test_the_uplot_licence_is_shipped_and_linked`: "The MIT License (MIT)", "Copyright
  (c) 2022 Leon Sorokin", and a relative link in `index.html`;
- new `test_the_page_loads_uplot_before_app_js`: CSS before `app.css`, JS before `app.js`,
  relative, `defer`;
- new `test_vendored_uplot_makes_no_network_request`: no `fetch(`, `XMLHttpRequest`,
  `WebSocket`, `http://` or `//cdn`, and only the banner's `https://`.

**The non-finite rule** (`tests/unit/observe/`, every job, no aiohttp):
- `snapshots.vehicle` with `nan`, `inf` and `-inf` in float signals: each value is `None`,
  `nonfinite` lists exactly those paths, sorted; finite floats, ints, bools and strings are
  unchanged; with none, `nonfinite == []`;
- **precedence:** a path forced into both `unavailable` and a non-finite value appears only
  in `unavailable`, with `null`;
- `state_message` output parses under `json.loads` with a `parse_constant` hook that raises,
  so it contains no `NaN` or `Infinity` token;
- `state_message` raises on an unserialisable value, which pins `allow_nan=False` and
  strictness;
- **the state task survives:** a `snapshot` that raises once, then succeeds. The task is
  still running; `state_encode_failed == 1`; the previous state stayed current; the next
  good state is pushed; `push_dropped` ran in the failed turn; two failures of one type log
  once (`caplog`), a second type logs again;
- `CancelledError` still ends the task.

**The API** (`tests/unit/api/`, `.[dev,gui]` job):
- `GET /vehicle` with a non-finite signal: 200, `null`, `nonfinite` set;
- the WS initial `state` and a later pushed `state` carry `null` and `nonfinite`;
- `GET /vehicle` with a residual failure (a monkeypatched snapshot with an unserialisable
  value): 500 with the stated text, `state_encode_failed` incremented, and `GET /status`
  still 200;
- `GET /status` carries `state_encode_failed`;
- **startup:** a non-finite initial value starts the API with the value sanitised; a
  residual failure in `check_state_size` raises `ApiStartupError` naming the exception
  type.

**Unchanged:** the API-off proofs, the differential comparison, the ordering, ledger and
publisher-turn tests. The state task is not a publisher turn, and nothing on the hot path
changes.

### 12.2 Acceptance checks in the capture run (`scripts/gui_demo_capture.py`)

**A canvas count alone proves nothing.** A canvas can exist and be blank, stale, joined
across a gap or unbounded. So every check below reads two things only:
- the **visible readouts**, as text: line 1's current value, and line 2's min and max;
- a small set of **read-only diagnostic `data-*` attributes** on each graph container.
  The page writes them after each ring update and draw; nothing in the page reads them;
  they describe the page's own rings and drawing, never uPlot internals.

| Attribute | Meaning |
|---|---|
| `data-path` | The signal's dotted path |
| `data-state` | `ok`, `invalid`, `unavailable`, `absent`, `waiting` or `no-scenario` |
| `data-points` | Points held in the ring now |
| `data-cap` | The ring's capacity, 4096 |
| `data-oldest-t` | `t` of the oldest point held |
| `data-newest-t` | `t` of the newest point held |
| `data-as-of` | The latest `as_of` received |
| `data-drawn-to` | The right edge of the last draw (differs from `data-as-of` while paused) |
| `data-window-s` | 30, 120 or 600 |
| `data-left-value` | The value held at the window's left edge, or empty if none |
| `data-segments` | Contiguous non-gap runs in the drawn window |
| `data-gaps` | Gaps in the drawn window |
| `data-run` | The `started_at` of the run the ring belongs to |
| `data-paused` | `true` or `false` |

Checks, each with its pass rule:

| Behaviour | How the capture run exercises it | Passes if |
|---|---|---|
| **Held value at the left edge of a trimmed window** | Stepped demo, 30 s window, read at t ≈ 55 s: speed has been 80 since t = 21, so the last change is outside the window | `data-left-value` = 80; line 2 reads "min 80 · max 80 in 30 s"; `data-segments` = 1; `data-oldest-t` < `data-as-of` − 30 |
| **Bounded history** | A long session (`--long`, about 11 min) on `ice_scenario.yaml`, whose sine on `engine.engine_load` changes on every message, with the traffic script running | For every graph at every 10 s sample: `data-points` ≤ `data-cap`; after 600 s, `data-oldest-t` ≥ `data-as-of` − 600 − 1 (the one retained point) and `data-left-value` is set |
| **Disconnect gap** | Stop the traffic, `SIGSTOP` the simulator for 12 s, then `SIGCONT`. The status poll's 5 s timeout takes the page down (§4.3); the reconnect finds the same `started_at` | `data-run` unchanged; `data-gaps` rose by 1 and `data-segments` by 1; line 2 names the gap; the conn text returned to "Live" |
| **Restart reset** | The existing SIGTERM (`gui_demo_capture.py:615-622`), then a new simulator | `data-run` changed; `data-oldest-t` ≥ 0 and `data-points` counts only the new run; the restart note is visible; no gap joins runs (`data-segments` = 1 after the first new point) |
| **Window persists** | Click "30 s", reload | `data-window-s` = 30 on every graph; the "30 s" button has `aria-pressed="true"` |
| **Pause while buffering** | Click "Pause graphs" for 10 s, then resume | While paused: `data-paused` = true, `data-drawn-to` and line 1 unchanged, `data-as-of` rising, `data-points` not falling except by the caps. After resume: `data-drawn-to` = `data-as-of`. The log kept running (its seq count rose) |
| **Non-finite handling** | A capture-only profile, outside `src/`, whose `engine.coolant_temp` is `stepped` over `[20, .nan, 30]` every 10 s, run **without** traffic (a `01 05` request would hit DEV-26's encoder crash) | While invalid: `data-state` = `invalid`, line 1 reads "invalid value", and the table cell reads "invalid value". After: `data-gaps` ≥ 1 and `data-segments` ≥ 2; the conn text stayed "Live"; the malformed readout is absent (the API sent valid JSON) |
| **Malformed messages** | `Page.addScriptToEvaluateOnNewDocument` wraps `window.WebSocket` so the script can reach the page's socket, then dispatches three `MessageEvent`s with the text `{bad` on it. Test instrumentation only; the page is unchanged | The "Malformed messages 3" readout is visible; the conn text is not "Live"; the banner is shown; the vehicle panel has the "Not current" tag. After the next real `state`: "Live" again, and the count still reads 3 |
| **Overflow** | `#graphs` and each `.uplot` join the `OVERFLOW` expression (`gui_demo_capture.py:213-220`) at 1440, 390 and 2000 | No `scrollWidth` above its `clientWidth`, as today (`:223-233`) |
| **Log rows at 1440 × 900** | Section open, log filled | At least 5 full log rows inside `#logwrap` |
| **Agreement** | Every live screenshot | Each line-1 value equals the signal table's cell, read in one evaluation |
| **Cost** | §11.3 | Recorded, not judged |

**Two limits, stated:**
- **DEV-26.** When DEV-26 is fixed on `modernization` and that fix reaches `gui`, the
  non-finite profile will be refused at load. The browser check then needs another way to
  put a non-finite value into the state, for example an in-process harness that serves the
  API over a runtime whose value is set directly. The Python tests of §12.1 set it directly
  already, so they do not depend on DEV-26.
- **Chrome only.** The capture script drives Chrome over the DevTools protocol. Firefox is
  a snap here, cannot run in the namespace automation, and no Playwright browser is to be
  downloaded. So nothing in §12.2 covers Firefox (§13).

### 12.3 By hand, at implementation

`python -m build --wheel` and `unzip -l` show the three files under
`ecu_simulator/api/static/`, since CI installs editable.

## 13. The owner's manual checklist (the M3b exit)

On a vcan host, `--api 127.0.0.1:8080`, the stepped demo, with the traffic script running
unless stated.

**CSP, both browsers. Unverified until someone actually runs it. M3b is not accepted
before these two items are ticked:**
- [ ] **Chrome:** load the page, open the console, watch one full 90 s cycle. No
      Content-Security-Policy error; the graphs draw.
- [ ] **Firefox:** the same. Record the Firefox version. (Firefox is a snap on this host
      and cannot run in the namespace automation, so this is manual only.)

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
- [ ] Stop the simulator: the graphs stay, marked stale. Start it again: they clear with the
      restart note; no line joins the runs.
- [ ] `kill -STOP` the simulator for 12 s, then `kill -CONT`: the page goes down within
      about 7 s, then comes back; the graphs show a gap with nothing drawn across it.
- [ ] `ice_default.yaml` (no scenario): "No scenario: the values are constant, as
      configured", with no plots.
- [ ] The non-finite capture profile, without traffic: "invalid value" in the table and
      the coolant graph, a gap in the line, and the page still "Live".
- [ ] 390 px: one graph per row, the head on two rows, no horizontal scroll.
- [ ] 2000 px: one row of five.
- [ ] The footer's uPlot licence link opens the MIT text.
- [ ] The measured cost in the live-demo record (§11.3) is in line with the estimates, or
      the difference is explained.

## 14. Open questions for the owner

Answered in this review: throttle and load separate (Q1 of `9a69d2e`); windows (Q3); pause
(Q4); unparseable messages (Q6, now §8.4); "rpm" (Q7); placement (Q9). Still open:
1. **Restart:** clear the graphs (proposed), or keep the previous run as a separate segment
   with a marker?
2. **Cost on the bench host:** measure the simulator host's CPU with the page open during
   traffic in the M3b live demo? It is not an M4 condition.
3. **Cycle markers:** leave them out (proposed), or record a future API field for a later
   decision?
4. **The 10 s degraded threshold** (§8.4): acceptable, or should it follow the tick?

## 15. Decision 0010: the tenth revision (made in the same commit)

`docs/decisions/0010-gui-observer-api.md` is amended, documentation only:
- **Status:** M3a is built and live on `gui`; M3b is designed and not implemented; the
  `nonfinite` field and the state-encoding containment are specified and not implemented.
  The M2 `STOP`, the hosted `CAN_ISOTP` gap, the Phase 8b gate and the V1.0 branch rule
  stay explicitly open.
- **A tenth-revision note, 2026-09-30**, recording the owner's decisions of §2 and the
  scope of the approval: design only, with implementation approval to follow review.
- **§4.3:** a row for state encoding (§8.3), marked specified, not implemented.
- **§5:** `nonfinite` on `GET /vehicle` and `state`, `state_encode_failed` on `GET /status`,
  and the non-finite rule (§8.2), marked specified, not implemented.
- **§7:** the M3b bullet rewritten to this design; the exit wording names the Firefox and
  Chrome CSP check.
- **§9.3:** the frontend-file row adds the SHA-256 pin; a row for the non-finite and
  containment tests.
- **§10:** the M3b row's deliverable and exit rewritten.

## 16. Concerns for the owner

- **M3b now changes server code.** §8.3 edits `observe/snapshots.py`, `observe/publisher.py`
  and `api/server.py`. The change is off the hot path and adds a few microseconds per state
  snapshot (estimate). But the M2 latency `STOP` is open, the state task shares the loop,
  and P9 measures publisher turns, not the state task. The implementation should report the
  snapshot's cost, and M4 measures the loop as a whole.
- **The non-finite browser check depends on DEV-26 being unfixed on `gui`** (§12.2).

## 17. Implementation outline (only after approval)

Each task is committed on `gui`, and stops for review where the owner asks.

| # | Task | Files |
|---|---|---|
| 1 | The non-finite rule and containment, tests first (§8.2, §8.3, §12.1) | `src/ecu_simulator/observe/snapshots.py`, `observe/publisher.py`, `api/server.py`; `tests/unit/observe/`, `tests/unit/api/` |
| 2 | Vendor uPlot 1.6.32 byte-identical; the three `FRONTEND` rows; the file tests | `src/ecu_simulator/api/static/uPlot.iife.min.js`, `uPlot.min.css`, `uPlot-LICENSE.txt`; `api/server.py`; `tests/unit/api/test_frontend_files.py` |
| 3 | Markup and layout: the collapsible section, the flex main column, the height cap, 390 px, the links | `index.html`, `app.css` |
| 4 | Page data: rings, ingest, breaks, restart reset, `as_of` null, invalid values, the rpm unit, malformed counting and the degraded state | `app.js`, `app.css` |
| 5 | Drawing: uPlot instances, stepped paths and gaps, scales, windows and `localStorage`, readouts, pause, hide, resize, the fallback, the `data-*` attributes | `app.js`, `app.css` |
| 6 | Capture-run checks (§12.2), the non-finite capture profile, the cost measurement (§11.3) | `scripts/gui_demo_capture.py`, a profile under `scripts/` |
| 7 | Live-demo record with screenshots, measurements and the §13 checklist | `docs/validation/gui-m3b-live-demo.md` |

**Still open after M3b:** the M2 latency `STOP` and the hosted `CAN_ISOTP` gap. M3b closes
neither, and does not change the Phase 8b gate or the V1.0 branch rule.
