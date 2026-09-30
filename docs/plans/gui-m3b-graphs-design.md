# GUI M3b: live signal graphs — design

Status: **design only, for the owner's review. Nothing here is implemented.** No file under
`src/` or `tests/` is changed, and nothing is vendored, until the owner approves this
document and the 0010 amendment it needs (§14).

Written on branch `gui` at `4f00694` (worktree `.claude/worktrees/gui`).
- Every "fact" was checked against the code at that commit, with file:line.
- Everything under a "Proposal" heading, or written as "M3b will", is a proposal.
- uPlot facts come from the npm tarball `uplot-1.6.32.tgz` (downloaded 2026-09-30, npm
  `shasum` `c800a63b432bad692d6d746f44f0882aa73a49ae`) and from uPlot's documentation via
  Context7 (`/leeoniya/uplot`). Figures marked **estimate** were not measured.

**Unaffected and still open:**
- **The M2 early-check latency `STOP` stays open and is not accepted**
  (`docs/decisions/0010-gui-observer-api.md:8-10`, `docs/validation/gui-m2-early-check.md`).
  M3b changes nothing on the server, so it neither measures nor changes that result.
- **The hosted-CI `CAN_ISOTP` gap stays open.** GitHub-hosted runners have no `can_isotp`,
  so the vcan integration test skips there (0010 §9.3, `:725`). Decision 0009 proposes the
  runner that would close the gap. Every live check below runs on a vcan host only.

## 1. The request

The owner's words:

> "Design M3b live signal graphs for the existing read-only GUI. Use only real WebSocket
> state updates and a bounded browser-side history; start with speed, RPM, throttle/load
> and coolant temperature, with units and a selectable time window. Keep VIN as text.
> Specify behavior for missing signals, disconnect/reconnect, simulator restart, scenario
> loop boundaries and narrow screens. Work offline with packaged assets and no CDN or build
> step. Do not change the API state rate or diagnostic path. Estimate browser memory/CPU
> cost and identify any new tests and manual checks. Write and commit a design for my
> review; do not implement M3b yet. Keep the M2 latency STOP and hosted CAN_ISOTP gap open."

**Not proposed:** a new endpoint, a new message type, a state history on the server, a
change to the state rate or to any `observe/` code, a build step, a JavaScript test
framework, any control from the browser.

## 2. What 0010 already says (facts)

| Where | What it says | What M3b must do |
|---|---|---|
| `0010:563-564` (§7) | "Milestone 3b vendors **uPlot** (MIT) for per-signal sparklines, with its licence file and pinned version. The MVP (3a) works without it." | Vendor uPlot with its licence and a pinned version. The page must still work if the graphs fail |
| `0010:555-556` (§7) | "No build step, no npm, and no CDN at runtime … The page is static HTML, CSS and plain JavaScript modules, served by `ApiServer`." | Vendor the prebuilt IIFE file. No bundler, no `npm install` at build or run time |
| `0010:565-570` (§7) | Decoding stays in Python. Frontend checked by file tests and the owner's manual checklist, "the M3a and M3b exit". "There is no JavaScript test framework in v1." | Graph only what the API already decodes. No JS tests |
| `0010:550-551` (§6) | "The static files are package data, served from a fixed directory, with no directory listing and no path parameters." | Each new file gets its own fixed route |
| `0010:728` (§9.3) | "Frontend files (M3): every file M3 adds is served with its content type, and nothing else is." Runs in the `.[dev,gui]` job | Extend those tests to the vendored files |
| `0010:747` (§10) | M3b: "Sparklines with vendored uPlot"; exit: "Owner runs the manual view checklist for sparklines" | §12 is that checklist |
| `0010:332` (§4.3) | "`state` push rate: at most 4 Hz … Coalesced into the one-slot latest `state`, which is not a drop" | Unchanged. The graphs take what arrives |
| `0010:415` (§5) | `GET /vehicle`: `kind`, `vin`, `signals`, `as_of` ("`null` without a scenario"), `unavailable` | The graphs read exactly these fields |

**Fit.** This design fits §6, §7 and §9.3 as written, and keeps the CSP unchanged (§9).
It needs a small **wording amendment** to 0010, not a change of direction: the graphs have
axes and a time-window selector, so they are more than sparklines; and the pinned version
and files should be named (§14).

## 3. How state reaches the page (facts)

### 3.1 The server side

- **The push loop.** `Publisher.run_state` takes a snapshot, pushes it only if its text
  differs from the last one pushed, then sleeps `interval_s` (`observe/publisher.py:265-272`).
  The interval is `STATE_MIN_INTERVAL_S = 0.25` (`observe/limits.py:16`), passed in by
  `ApiServer` (`api/server.py:179`, `:247`). So the page receives **change events at most
  every 0.25 s**, not a fixed sample rate.
- **The message.** `snapshots.state_message` is `{"type": "state", "vehicle": …, "dtcs": …}`
  (`observe/snapshots.py:75-77`). `vehicle` holds `kind`, `vin`, `signals` (every stored
  value), `as_of` and `unavailable` (`snapshots.py:35-45`).
- **`as_of`** is `runner.last_applied`, or `None` when the profile has no scenario
  (`snapshots.py:43`). `last_applied` is the highest scenario time applied
  (`scenario/runner.py:116-119`). `apply` refuses a `t` earlier than the last one
  (`runner.py:136-146`), so within one run **`as_of` never decreases**.
- **What moves `as_of`.** Two callers apply the scenario:
  - the dispatcher, before every request (0010 §12 E3);
  - the periodic tick, which sleeps `period` and then calls `sync()` (`app.py:261-265`).
    `period` is the profile's `scenario.tick`, default 1.0 s (`config/schema.py:194`); the
    stepped demo uses 0.5 s (`docs/examples/ice_drive_cycle_stepped.yaml:48`).
- **Consequence: with a scenario, a state message arrives on every push turn in which
  anything applied.** `as_of` is part of the message, so any apply changes the text.
  - With a tester polling: up to 4 Hz.
  - On an idle bus: about once per tick (1 Hz by default, 2 Hz in the demo).
  - A **value** changes only when its generator's output changes. Under the stepped demo,
    speed and rpm change once a second.
- **Without a scenario** the runner is `None` (`app.py:184-193`) and nothing changes the
  state, so after its first `state` a connection receives **no further state messages**.
- **Before the first apply.** The scenario origin is read once when the runtime is built
  (`app.py:222-224`). The tick first applies one `period` after `run()` enters
  `scenario_tick` (`app.py:261-265`, `:352`), unless a request applies it earlier. Until
  then `as_of` is `null` **even though a scenario exists**, and `signals` hold the profile's
  initial values.
- **Restart.** A new process has a new `started_at`, set when `ApiServer` is built
  (`api/server.py:177`), and reported by `GET /status` (`snapshots.py:95`). Its scenario
  time starts again at 0 (`scenario/sync.py:8-10`).
- **No history of state.** The server keeps one latest `state` per client
  (`publisher.py:254-257`, 0010 §4.3). There is no endpoint for past values.
  `GET /exchanges` holds exchanges only (0010 §5).

### 3.2 The page today (`src/ecu_simulator/api/static/`)

- **Files.** `index.html` loads `app.css` and `app.js` (`index.html:9-10`). `app.js` is one
  IIFE, no modules (`app.js:9`).
- **State in.** `onMessage` parses each frame. A frame that is not valid JSON is **dropped
  silently** (`app.js:258-267`, the `try` at `:260`). A `state` message calls
  `applyState(vehicle, dtcs)`, which stores it and re-renders the vehicle and DTC panels
  (`app.js:300-304`). The first data of a run comes from `GET /vehicle`, `/dtcs` and
  `/ecus` in `connect()` (`app.js:156-170`).
- **Restart detection.** `connect()` fetches `GET /status` first. If `started_at` differs
  from the stored `S.runStartedAt`, it adds a "Simulator restarted." marker to the log,
  resets `lastSeq`, and refetches the snapshots (`app.js:147-160`). A restart can only be
  seen on a (re)connect: the old process's socket has closed first.
- **Resume.** A reconnect opens `/events?after=lastSeq` (`app.js:188`). The server then
  sends `hello` and a `state` (0010 §4.5). `onHello` adds a "Connection lost, then
  resumed." marker (`app.js:269-297`).
- **Stale.** `fail()` sets the phase to `down` or `refused` (`app.js:223-240`).
  `renderLink()` sets `body.is-stale`, shows every `.stale-tag` with "Stale, as of HH:MM:SS
  UTC", and fills the link banner (`app.js:408-452`). CSS hatches every panel's top edge
  and fades `.panel__body` to 0.62 opacity (`app.css:116-125`). **Stale data is kept and
  labelled, never cleared.**
- **Vehicle panel.** The header shows "as of scenario t = N s", or "no scenario: values as
  configured" when `as_of` is null (`app.js:457`). The VIN is shown once, as text, in the
  panel's key-value line, and its signal row is left out (`app.js:458-472`). A path in
  `unavailable` is shown as "—" with "unavailable, no source" (`app.js:461-464`,
  `:491-500`).
- **Units.** `UNITS` is a display map from the bundled profiles' comments
  (`app.js:39-47`). **It has no entry for `engine.rpm`**, so the rpm row shows no unit
  today. The other four M3b signals are there: `vehicle.speed` km/h, `engine.throttle` %,
  `engine.engine_load` %, `engine.coolant_temp` °C.
- **Log controls.** Pause, clear and the filters act on the log view only
  (`app.js:864-875`; `index.html:67-83`, "Log view controls (this view only)").
- **Layout** (`app.css:128-136`, `:320-398`):
  - above 1100 px wide and 640 px high: a fixed-height shell with a side column
    `clamp(380px, 25vw, 520px)` (DTCs, then vehicle signals) and the log filling the rest;
  - up to 1100 px: one column, the side panels first;
  - up to 700 px: log rows become blocks.
  The root font grows with the viewport (`app.css:31-34`), and sizes are in rem.
- **Existing ResizeObserver use**: `app.js:791-797`.

### 3.3 The served file list (facts)

- `api/server.py:44-48` `FRONTEND` maps each route to `(file, content type)`: `/` →
  `index.html`, `/app.css`, `/app.js`. Every body is read once, at construction, from
  `importlib.resources` (`server.py:186-188`), and each route gets its own handler
  (`server.py:268-275`). Nothing from the request selects a file.
- `FRONTEND_HEADERS` (`server.py:51-59`) put this CSP on every frontend response:
  `default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self';
  img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none';
  object-src 'none'`, with `nosniff`, `no-referrer` and `no-cache`.
- `tests/unit/api/test_frontend_files.py` keeps its own copy of the list (`:14-18`) and of
  the headers (`:19-27`), and asserts that:
  - each route serves its file with its type, charset and body (`:34-41`), with the
    headers (`:44-49`);
  - **the static directory holds exactly the served files** (`:60-63`);
  - files are read once (`:66-83`), other paths are 404 (`:86-134`), other methods 405
    (`:137-142`), the Host guard applies (`:145-149`);
  - `index.html`, `app.js` and `app.css` contain no `http://`, `https://` or `//cdn`, and
    **not the word "sample"** (`:152-157`).
- **Packaging.** `pyproject.toml:63` puts `src/ecu_simulator/api/static/*` in the wheel as
  artifacts. The glob already covers any new file there, so **`pyproject.toml` needs no
  change**. CI installs editable (`.github/workflows/ci.yml:88`), so CI reads the source
  tree; the wheel is checked by hand (§11.3).

## 4. The renderer

### 4.1 uPlot 1.6.32 (facts)

| | |
|---|---|
| Version | **1.6.32**, the npm `latest` on 2026-09-30. Licence MIT, "Copyright (c) 2022 Leon Sorokin" |
| Files to vendor | `dist/uPlot.iife.min.js` (defines the global `uPlot`), `dist/uPlot.min.css`, `LICENSE` |
| `uPlot.iife.min.js` | 51,081 B raw, 22,009 B gzip -9. SHA-256 `19c8d4c6ad88929a79f4ae49d6f7161566dfd0ba3d15cc495e974f787eb78f1f` |
| `uPlot.min.css` | 1,857 B raw, 772 B gzip. SHA-256 `df630c6a8d6f8eeaff264b50f73ce5b114f646ffd9a0bb74f049b0a00135fa04` |
| `LICENSE` | 1,078 B raw, 660 B gzip. SHA-256 `8f989229699b4fe2f1a0432d0e9edc338a8a911e250e2d1b01ecd770a5f5b1bd` |
| First line of the JS | `/*! https://github.com/leeoniya/uPlot (v1.6.32) */` |

The server does not compress, so the page will fetch **54,016 B more**, raw, on each load
over loopback.

**What it needs from the page** (from the uPlot docs, and a scan of the minified file):
- **A container element and pixel sizes.** `new uPlot(opts, data, target)` with
  `opts.width` and `opts.height` in CSS px. Resizing is the page's job, with
  `u.setSize({width, height})`. uPlot has no ResizeObserver of its own (0 occurrences).
  It follows `devicePixelRatio` changes itself, via `matchMedia` (1 occurrence).
- **One canvas per chart** (one `getContext` call in the build), plus a few positioned
  `div`s. Its stylesheet sets `.uplot { width: min-content }`, so the chart is exactly
  as wide as the width it is given.
- **Data** is columnar: `[xs, ys]`. The x values must be ascending and unique; y values are
  numbers or `null`. The docs ask for arrays of length ≥ 2. `null` makes a gap when the
  series has `spanGaps: false`.
- **Stepped paths**: `paths: uPlot.paths.stepped({align: 1})`. `align: 1` holds each value
  until the next x, then steps.
- **Fixed scales** (`auto: false, range: [0, 100]`) are also a speed-up: the data is not
  scanned for the range.

**CSP.** The scan of `uPlot.iife.min.js` found:
- 0 × `setAttribute("style"`, 0 × `cssText`, 0 × `createElement("style")`,
  0 × `innerHTML`, 0 × `insertAdjacentHTML`;
- 0 × `eval(`, 0 × `new Function`;
- 0 × `fetch(`, 0 × `XMLHttpRequest`, 0 × `localStorage`;
- 12 × `.style.` property writes, such as `l.style[e]=t+"px"`, and `el.style.transform`.

Property writes through the CSSOM are not "inline style" as CSP defines it; `style-src`
restricts `<style>` elements, `style="…"` in markup and `setAttribute('style', …)`. The
stylesheet is a same-origin file. **So uPlot needs no CSP change**, and the CSP stays
exactly as in `server.py:51-59`. This is an inference from the scan and the CSP rules; the
capture script (§11.2) and the owner's Firefox check (§12) confirm it in real browsers.

The only URL in the file is the banner comment above. It makes no network request.

**Published performance** (uPlot README, "Performance"; Chrome 113, AMD Ryzen 7 PRO 5850U,
2023-03-11):
- uPlot v1.6.24 draws the 166,650-point benchmark in **34 ms** cold, with heap peak
  **21 MB** and final **3 MB**;
- "scaling linearly at ~31,000 pts/ms" (its 10M-point bench page).

### 4.2 uPlot against a hand-written renderer

| | uPlot 1.6.32 | Hand-written canvas | Hand-written SVG |
|---|---|---|---|
| Code added | 51 KB vendored (22 KB gzip), not reviewed line by line; pinned by hash | About 200–300 lines in `app.js` (**estimate**), all reviewable | About the same, plus DOM paths |
| Axes, tick spacing, nice numbers, HiDPI, resize | Built in and widely used | Must be written and tested by hand. This is where most chart bugs live | Ticks by hand; HiDPI free |
| Step-hold and gaps | `paths.stepped({align: 1})`, `null` gaps | Easy to write | Easy to write |
| CSP | No change (§4.1) | No change | No change, if attributes are set through the DOM |
| Licence and vendoring | MIT licence file shipped, version and hash pinned, three more served files | None | None |
| 0010 | Named in §7 already: no change of direction | Departure: needs its own amendment and a reason | Same |
| Tests | Python file tests only; no JS tests (0010 §7) | Same, and more untested JS | Same |
| Per-draw cost at our sizes | Well under 1 ms of path work (§10) | Similar | A 2,400-point `d` string rebuilt up to 4 times a second per graph; fine, but more garbage |

**Recommendation: uPlot, as 0010 already says.** A hand-written renderer would be smaller,
and fully reviewable. But the part it would have to reinvent — axes, ticks and HiDPI
handling — is where hand-written charts usually break. uPlot's cost is 22 KB gzip on
loopback, plus a pinned, hashed file that nobody reviews line by line. That is the same
trade 0010 made when it named uPlot. Not recommended: a departure, which would need a
reason that the owner does not have today, and an amendment.

## 5. Proposal: which signals, and how they are shown

### 5.1 The graphs

| Graph | Path | Unit (display map) | y scale |
|---|---|---|---|
| Speed | `vehicle.speed` | km/h | from 0 to a nice value above the window's maximum, at least 20 |
| Engine speed | `engine.rpm` | **rpm** (new map entry, §5.3) | from 0 to a nice value above the window's maximum, at least 1000 |
| Throttle | `engine.throttle` | % | fixed 0–100 |
| Engine load | `engine.engine_load` | % | fixed 0–100 |
| Coolant | `engine.coolant_temp` | °C | the window's minimum and maximum, padded by 2 °C, never narrower than 10 °C |

**Throttle and load are two separate graphs**, one directly above the other, both on a
fixed 0–100 % axis.
- Why separate:
  - 0010 §7 says "per-signal sparklines";
  - one line per graph needs no legend and no colour or dash to tell lines apart, which
    keeps "not colour alone" trivially true;
  - each has its own text readout (§8).
- Why this is not a loss: the two share the same unit and the same fixed scale, and they
  sit next to each other (above one another on narrow screens), so they can be compared by
  eye.
- The alternative, one graph with two lines (solid and dashed) and a text legend, saves
  one panel. It is open question 1.

**The VIN stays text**, exactly where it is (`app.js:469-472`). Only numbers are graphed:
the page never graphs a string or a boolean.

Other signals (fuel level, HEV and BEV battery and motor) are not in M3b. Adding one later
is one row in the table above.

### 5.2 Where the graphs go

A new panel, **"Signal graphs"**, at the top of the main column, above the exchange log.
- **Wide** (above 1100 px, fixed-height shell): the second grid column becomes a flex
  column holding the graphs panel (its natural height) and the log panel (the rest,
  `min-height: 0`, scrolling inside as now). The graphs sit in a grid,
  `repeat(auto-fit, minmax(11rem, 1fr))`. At 1440 px the main column is about 1000 px, so
  the five graphs fit in one row of about 190 px each; at 2000 px, one wider row.
- **Medium** (up to 1100 px): the order is side panels, graphs, log, as a single column.
- **Narrow** (up to 700 px, including 390 px): one graph per row, full panel width (§7).
- Each graph is a small card:
  - a caption with the name and unit, "Speed, km/h";
  - the plot, **6 rem** high (about 84 px at 1440, 108 px at 2000);
  - the readout line (§8).

The side column is not used: it is already full at 1440 (DTCs and the signal table), and
graphs 380 px wide are no better than the main column's.

### 5.3 A unit for rpm

- **Fact:** `UNITS` has no `engine.rpm` (`app.js:41-46`).
- **Proposal:** add `"engine.rpm": "rpm"`. It is presentation only, like the rest of the
  map. It also gives the signal table's rpm row a unit. Whether to write "rpm" or "1/min"
  is open question 7.

## 6. Proposal: data model and time axis

### 6.1 The time base: scenario time (`as_of`)

Each graph's x value is **`as_of`, the scenario time the values were applied at**. It is
not the time the browser received them.

| | Scenario time `as_of` (chosen) | Browser receive time |
|---|---|---|
| Meaning | The exact scenario time the stored value was applied (`snapshots.py:43`) | When this page happened to receive it |
| Jitter | None from the network or the 0.25 s coalescing | Up to 0.25 s of coalescing, plus delivery delay and timer throttling in background tabs |
| Order | Never decreases within a run (`runner.py:136-146`) | Monotonic if `performance.now()` is used |
| Scenario steps | Land on the profile's own times: the demo's 1 s steps at whole seconds, its loop at 90 s, 180 s … | Shifted by delivery delay |
| Loop boundary | Keeps increasing; not a reset (§6.6) | Keeps increasing |
| Paused page | Not affected | Not affected |
| No scenario | `null`: nothing to graph (§6.5) | Could draw a flat line, but the values never change |
| Disconnect | Frozen while down; the gap is drawn explicitly (§6.7) | Gap drawn by elapsed wall time |
| Restart | Starts again at 0: the graphs must be cleared (§6.8) | Continuous, so two runs could share an axis |
| Matches the page | The vehicle panel already says "as of scenario t = N s" (`app.js:457`) | — |

The receive-time column's one real advantage, keeping two runs on one axis, is not wanted:
the design never joins two runs (§6.8). So scenario time wins.

**The x-axis is labelled "scenario t, s"**, with ticks in whole seconds. uPlot's
`scales.x.time` is `false` (a numeric axis).

**What `as_of` does not say.** A value in a message was applied at `as_of`, but it may have
**changed** at any apply since the previous message: `apply` sets every driven signal to
its generator's value at `t` (`runner.py:128-148`), and the publisher sees only the latest
state per turn. So a step on the graph is drawn at the message's `as_of`, which can be up to
one push interval (0.25 s under load) or one tick (on an idle bus) after the apply that
changed it. Under the stepped demo on an idle bus, steps fall on ticks at 0.5 s multiples,
so each step is drawn at the whole second or half a second after it.

### 6.2 Step-hold semantics

A stored signal **holds its value until the next apply**. Nothing in the simulator
interpolates between applies. The graph therefore draws **steps, never ramps**:
- `paths: uPlot.paths.stepped({align: 1})`: a horizontal line from each point to the next
  x, then a vertical step;
- a **continuous** generator is drawn as a fine staircase, one step per message, because
  that is what the stored value did. The coolant ramp and `ice_scenario.yaml`'s sine on
  `engine.engine_load` (`src/ecu_simulator/profiles/ice_scenario.yaml:103-104`) are
  examples. A linear path would draw a ramp between points and claim values the simulator
  never stored;
- **the last value holds to the latest `as_of`.** At draw time the page adds one point,
  (latest `as_of`, last value), to the data given to uPlot. It is not stored. So a value
  that has not changed for 30 s still reaches the right edge, and every series has at
  least two points, as uPlot's docs ask.

### 6.3 The ring buffer

One ring per graphed signal, preallocated when the graph is built:

| Field | Type | Size |
|---|---|---|
| `t` | `Float64Array(4096)` | 32 KiB |
| `v` | `Float64Array(4096)` | 32 KiB |
| `start`, `count` | integers | — |

- **A point is stored only when the value changes**, or after a gap (§6.7). A message that
  moves only `as_of` updates one number per run, `lastAsOf`, which drives the right edge
  (§6.2). The stepped demo stores at most one point per second per signal; the worst case
  is a signal that changes on every message.
- **A gap** is stored as a point whose value is `NaN`. At draw time it becomes `null` for
  uPlot, which breaks the line (`spanGaps: false`).
- **Equal `as_of`.** A message whose `as_of` equals the last stored `t` replaces the last
  value instead of adding a point, so x stays unique. The reconnect `GET /vehicle` and the
  `state` after `hello` can carry the same `as_of`.
- **Caps, both enforced on every append:**
  - **time**: points older than the latest `as_of` minus 600 s (the longest window) are
    dropped, except the newest of them, which is kept so the line enters from the left
    edge;
  - **count**: 4096 points. At the maximum rate of 4 messages a second, 10 min is 2,400
    points, so the time cap normally applies first. The count cap is a hard bound if the
    rate assumption is ever wrong. If it ever trims points still inside the window, the
    readout says "history starts at t = N s".
- **Bytes:** 64 KiB per signal, **320 KiB for five**, allocated once. The worst-case live
  data, 2,400 points × 5 signals × 2 arrays × 8 bytes, is 192,000 B (188 KiB), inside that.
- **History beyond 10 min is discarded.** The page keeps no more; the server keeps none
  (§3.1). This is said in the panel's fine print.

### 6.4 The window

- **Choices: 30 s, 2 min, 10 min. Default: 2 min**, which shows the stepped demo's whole
  90 s cycle with room on both sides.
- A three-button group in the panel header, `role="group"`, `aria-pressed` on each, as the
  log's buttons are styled (`app.css:228-235`). It applies to all five graphs.
- **The x range is always the full window**, `[latest as_of − W, latest as_of]`. The scale
  does not change while history is short: a page that connected 40 s ago shows 40 s of line
  and an empty left part, with "history starts at t = N s (when this page connected)".
- **Saved in `localStorage`**, key `ecu-simulator.graphs.window`, value `30`, `120` or
  `600`. Every read and write is in `try/catch`; any failure or unknown value means the
  default. It is a view preference only: nothing is sent to the simulator.

### 6.5 Missing signals and missing time

| Case | How the page knows | What it shows |
|---|---|---|
| **The path is in `unavailable`** (no source in this profile) | `vehicle.unavailable` (0010 §5, ninth revision) | A labelled card with no plot: "Speed — unavailable, no source", the same words as the table (`app.js:496`). Not reachable for these five paths with the shipped profiles or the demo, where only `vehicle.odometer` is unavailable (0010:148-150); the profile schema can set all five |
| **The path is not on this vehicle kind** (a BEV has no `engine.*`, `vehicle/state.py:104-110`) | The path is absent from `signals` | No card. One line in the panel: "Not on this vehicle (bev): engine.rpm, engine.throttle, engine.engine_load, engine.coolant_temp." |
| **The value is not a finite number** (`typeof v !== "number"` or `!Number.isFinite(v)`) | Checked on every message | A gap in the line (a `NaN` point), and "not a number in the last message" in that card's readout. Never an exception |
| **`as_of` is `null` and `GET /status` says `scenario.enabled` is false** | `S.status.scenario.enabled` (`snapshots.py:96`) | No plots. "No scenario: the values are constant, as configured. Graphs follow scenario time." |
| **`as_of` is `null` and a scenario is enabled** (before the first tick, §3.1) | The same two fields | No plots yet. "Waiting for the first scenario tick." The first message with an `as_of` starts the graphs |

**DEV-26 and non-finite values.** DEV-26 (a `modernization` defect, not fixed on `gui`)
lets a profile put `NaN` or `±inf` into a scenario signal. Facts, from Python's and
JavaScript's standard JSON behaviour, not measured in the page:
- `json.dumps` writes `NaN` and `Infinity` by default. `snapshots.state_message` uses it
  without `allow_nan=False` (`snapshots.py:76`), and `web.json_response` uses it too
  (`server.py:284`);
- `JSON.parse` rejects those tokens. So **such a message never reaches the graphs**: the
  page drops the whole frame (`app.js:260`), and a `GET /vehicle` would fail to parse.

The graphs' `Number.isFinite` check is therefore defensive: it covers a value that
arrives as a string, a boolean or `null`, and any future encoder. It keeps the rule "a gap,
never a crash". The silent drop at `app.js:260` is M3a behaviour. Making it visible (a
count of unparseable messages in the status bar) is **open question 6**, not part of M3b
unless the owner asks. Changing the server's JSON encoding is out of scope: it would change
the API.

### 6.6 Scenario loop boundaries

- **Facts.** `stepped` returns `values[floor(t / interval) mod len(values)]`
  (`scenario/generators.py:96-103`). `t` is elapsed time since the runtime started, and it
  never wraps (`scenario/sync.py:30-35`). So at the demo's 90 s boundary **`as_of` keeps
  increasing** (89.5, 90.0, 90.5 …). Only the index into `values` wraps.
- **The demo wraps idle to idle.** At `t = 89` and `t = 90` the four stepped signals have
  the same values: speed 0 and 0, rpm 800 and 800, throttle 0 and 0, load 20 and 20
  (`docs/examples/ice_drive_cycle_stepped.yaml:62`, `:54`; `:76`, `:68`; `:90`, `:82`;
  `:104`, `:96`). The values do not change, so no point is stored (§6.3), and the line
  runs flat through 90 s. The next steps come at 96 s (speed 5, rpm 880, throttle 45,
  load 75), as they did at 6 s.
- **Coolant** is a ramp of absolute time, from 20 to 90 °C over 240 s, then held
  (`ice_drive_cycle_stepped.yaml:107-111`). It ignores the loop; the graph shows one
  staircase rising to 240 s, then a flat line.
- **No cycle markers.** The page cannot know the cycle length: neither `GET /status`
  (`snapshots.py:93-99`) nor `state` carries the scenario's configuration. The design
  does not guess one. With the axis in scenario seconds, 90, 180 and 270 s are readable
  tick values. Exposing the cycle length would be an API change for a later decision
  (open question 8).

### 6.7 Disconnect and reconnect

- **While down**, the graphs keep their data and are labelled stale, like every other panel:
  the `.stale-tag` in the panel head, the hatched top edge, the faded body
  (`app.css:116-125`), and "Stale, as of HH:MM:SS UTC". **The right edge stops at the last
  `as_of` received**; the graphs do not scroll on their own while stale.
- **The line breaks, and nothing is interpolated across the outage.** When `fail()` runs
  on a page that was live, each ring records a pending break. When the first message of
  the next connection arrives:
  1. the last value is closed off at the last `as_of` received before the drop, as an
     explicit point, so the hold is drawn only as far as the page actually knows;
  2. a `NaN` point is added between that and the new `as_of`;
  3. the new value is added.
  The gap is as wide as the scenario time that passed.
- **Resume.** On reconnect the page already fetches `GET /status` and, for the first data
  of a run, `GET /vehicle` (`app.js:147-170`); then `hello` and a `state` arrive (0010
  §4.5). Whichever comes first resumes the series.
- **No backfill. The API keeps no history of state**, only the latest one (§3.1). What
  happened to the signals during the outage is not known to the page, and the gap says so:
  "No data from t = A to t = B s (disconnected)" in each affected readout. Reconstructing
  values from logged OBD responses would put protocol decoding in the browser, which 0010
  §5 and §7 forbid.

### 6.8 Simulator restart

- **Detection:** the existing `started_at` check in `connect()` (`app.js:149-155`). As a
  second guard, an `as_of` lower than the ring's last `t` is treated the same way: within
  one run it cannot happen (`runner.py:136-146`).
- **Behaviour: the graphs are cleared**, the rings reset and the pending break dropped. A
  note replaces the empty left part: "Simulator restarted at HH:MM:SS UTC. Graphs start
  again from scenario t = 0; the previous run's graphs were cleared." The log keeps its
  own "Simulator restarted." marker, as today.
- **Why clear, not segment.** The new run's scenario time starts at 0 again, so the two
  runs cannot share one ascending axis without inventing an offset. Two runs are never
  joined into one line.
- Keeping the previous run on screen, as a separate segment with a marker, is **open
  question 2**.

### 6.9 Pause, clear and filters

- **The log's pause, clear and filters do not touch the graphs.** They are labelled as the
  log's own controls (`index.html:67`), and exchange filters mean nothing for signals.
- **The graphs get their own "Pause graphs" toggle** in their panel header,
  `aria-pressed`, the same style as "Pause view".
  - While paused, drawing and readouts freeze, and the panel says "Paused at t = N s".
  - Incoming data is still stored, within the same caps. Resuming jumps to live.
  - If a resize happens while paused, the frozen window is redrawn from the rings; points
    already trimmed by the 10-min cap are gone, and the readout says so.
- **No "clear graphs" button.** A restart clears them (§6.8); otherwise there is nothing to
  clear for.
- Everything is view-only; the page still sends nothing on the socket
  (`app.js:3-8`).
- Whether a graph pause is wanted at all is open question 4.

## 7. Proposal: narrow screens (390 px)

- **Layout.** One card per row. At 390 px the layout's side padding is 0.571 rem and the
  panel body's 0.714 rem (`app.css:353-355`), so each plot is about 350 px wide and 6 rem
  high. The five cards take about 35 rem of page height, below the side panels and above
  the log.
- **Sizing.** A `ResizeObserver` on the graphs grid (the page already uses one,
  `app.js:791-797`) calls `u.setSize({width: floor(card content width), height})` for
  each graph, coalesced into one animation frame. Because `.uplot` is `min-content` wide
  (§4.1) and is always given its container's width, a chart can never be wider than its
  card.
- **Axes on narrow cards.** The y-axis is 3 rem wide at most, and uPlot's tick spacing is
  kept at 50 px or more on x, so a 350 px plot gets about five x labels. Axis text uses
  the page's font (`--sans`) at 0.786 rem, read from computed style when the charts are
  built and on resize.
- **Window buttons** wrap under the panel title as the log bar's buttons do
  (`app.css:358-359`).
- **The overflow check** in `scripts/gui_demo_capture.py:213-233` measures `#vehicle`,
  `html` and `body`. M3b adds:
  - `#graphs` (the panel body), whose `scrollWidth` must not exceed its `clientWidth`;
  - every `.uplot` element, whose width must not exceed its card's `clientWidth`.
  It runs at 1440, 390 and 2000, as now (`:47-49`, `:402-417`, `:592-605`).

## 8. Proposal: accessibility

- **Text alternatives.** Each card is a `<figure>`. Its `<figcaption>` holds the name and
  unit. Below the plot, a text line gives the current value, and the minimum and maximum
  in the window: "Now 80 km/h · min 0, max 80 in the last 2 min · as of t = 45.5 s". The
  plot container is `aria-hidden="true"`: the text carries the content.
- **Not colour alone.**
  - Each graph has one line in the page's ink colour, so no colour tells lines apart.
  - Stale state uses the hatch, the "Stale, as of …" tag and the banner, as elsewhere.
  - A gap is a break in the line **and** words in the readout.
  - A restart is a written note.
  - "Unavailable" is a dash and words.
- **No chatter.** Readouts are not `aria-live`; they change up to four times a second. A
  screen reader user reads them on demand.
- **Keyboard.** The window buttons and "Pause graphs" are ordinary buttons. uPlot's cursor,
  legend and selection are off (`cursor: {show: false}`, `legend: {show: false}`), so the
  plot has no pointer-only features that the text lacks.
- **Reduced motion.** Graphs move only when data arrives; nothing is animated.

## 9. Constraints kept

| Constraint | How |
|---|---|
| API state rate | Unchanged: `STATE_MIN_INTERVAL_S` and `run_state` are not touched (`limits.py:16`, `publisher.py:265-272`) |
| Diagnostic path | Unchanged: no file under `observe/`, `ecu/`, `transport/` or `scenario/` changes. The only server change is three entries in `FRONTEND` |
| Endpoints | None added. The three new routes are static files in the fixed list (0010 §6) |
| Build step, npm, CDN | None. The IIFE file is vendored as published and loaded with `<script src="uPlot.iife.min.js" defer>` before `app.js` (`defer` keeps the order) |
| CSP | **Unchanged**, `default-src 'self'` and the rest of `server.py:51-59` (§4.1) |
| Offline | Every file is package data (`pyproject.toml:63`) |
| M3a without uPlot | If `typeof uPlot !== "function"`, the graphs panel says "Graphs unavailable: the chart library did not load", and the rest of the page works (0010:563-564) |

**Files and routes** (proposal):

| Route | File under `static/` | Content type |
|---|---|---|
| `/uPlot.iife.min.js` | `uPlot.iife.min.js`, byte-identical to the tarball | `text/javascript` |
| `/uPlot.min.css` | `uPlot.min.css`, byte-identical | `text/css` |
| `/uPlot-LICENSE.txt` | `uPlot-LICENSE.txt`, the tarball's `LICENSE`, byte-identical | `text/plain` |

- The licence is **served**, not only shipped. The "static directory holds exactly the
  served files" rule (`test_frontend_files.py:60-63`) then needs no exception, and the
  page's footer can link to it: "Graphs drawn with uPlot 1.6.32 (MIT licence)".
- `index.html` links `uPlot.min.css` **before** `app.css`, so the page's rules win.
- `FRONTEND` in `server.py:44-48` and the test's copy (`test_frontend_files.py:14-18`)
  both gain the three rows. `pyproject.toml` does not change (§3.3).

## 10. Estimated cost in the browser

All of this is in the browser. **None of it touches the server**: the server gains three
static routes that are read once at construction and served on page load. The M2 latency
`STOP` concerns the simulator's loop under the M4 harness clients, which are not browsers.
It is unaffected, and stays open.

### 10.1 Memory

| Item | Size | Basis |
|---|---|---|
| Rings, 5 × 4096 × 2 × 8 B | **320 KiB**, fixed | §6.3 |
| Worst-case live data inside them (10 min at 4 Hz) | 188 KiB | 2,400 × 5 × 2 × 8 B |
| Arrays handed to uPlot per draw | about **190–380 KB** of short-lived garbage per full redraw at the worst case (**estimate**) | ≤ 2,403 points × 2 arrays × 5 graphs. Arrays holding `null` are not packed doubles in V8; 8–16 B per element is assumed |
| Canvas backing stores: width × height × dpr² × 4 B each, five charts | 1440 × 900 at dpr 1: 190 × 84 px → 5 × 62 KiB ≈ **0.3 MiB**. At dpr 2: ≈ 1.2 MiB. 390 px phone at dpr 3: 350 × 108 px → 1050 × 324 × 4 ≈ 1.3 MiB each, ≈ **6.5 MiB** | Arithmetic from the §5.2 and §7 sizes |
| uPlot code and instances | 51 KB of source; instance overhead small (**estimate**). For scale, the README's 166,650-point bench ends at a **3 MB** heap | README, "Performance" |

**Total, estimate:** under 2 MiB on a desktop at dpr 1, and under about 8 MiB on a dpr-3
phone. The canvases dominate. It does not grow over time: rings are fixed, and canvases
change only with size.

### 10.2 CPU

- **Per state message** (≤ 4 per second): the JSON is already parsed; the graphs add five
  compare-and-append steps and set a "dirty" flag. Negligible.
- **Drawing is coalesced with `requestAnimationFrame`**: at most one redraw per frame, and
  in practice one per message, ≤ 4 per second. Each redraw copies the window out of the
  rings, then calls `setData(data, false)` and `setScale("x", {min, max})` on each chart.
- **Per redraw, worst case**: 5 × about 2,400 points. At uPlot's published ~31,000
  points/ms, path building is about **0.4 ms**. With axes, text and canvas clears, the
  **estimate** is 1–3 ms for all five on a laptop like the README's. At 4 per second that is
  at most about 12 ms/s, **about 1 % of one core**. The stepped demo stores far fewer
  points, so its cost is lower.
- **Background tab**: `requestAnimationFrame` does not run, so nothing is drawn. Messages
  are still stored; one redraw happens on return.
- **Measured, not just estimated, at implementation**: the capture script records
  Chrome's `Performance.getMetrics` `TaskDuration` and `JSHeapUsedSize` at the start and end
  of a 60 s live stretch at 1440 and at 390 (§11.2). The result goes in the M3b live-demo
  record.
- **One honest caveat.** If the browser runs on the same host as the simulator, its
  drawing shares that host's CPUs. The M4 benchmark (0010 §9.2) uses harness clients, not a
  browser, so no M4 condition changes. Whether to measure a browser on the bench host
  during a demo is open question 5.

## 11. Tests

### 11.1 Python (the `.[dev,gui]` job, 0010 §9.3)

In `tests/unit/api/test_frontend_files.py`:
- **The list grows.** The test's `FRONTEND` (`:14-18`) gains the three rows of §9. Every
  existing parametrised test then covers them with no new code: body, content type and
  charset (`:34-41`), security headers (`:44-49`), 405 on other methods (`:137-142`), the
  Host guard (`:145-149`), read once (`:66-83`), and **the static directory holds exactly
  these files** (`:60-63`).
- **New: `test_vendored_uplot_is_the_pinned_release`.** The SHA-256 of each of the three
  files equals the §4.1 value, and the JS starts with
  `/*! https://github.com/leeoniya/uPlot (v1.6.32) */`. Changing the version means changing
  this test on purpose.
- **New: `test_the_uplot_licence_is_shipped_and_linked`.** `uPlot-LICENSE.txt` starts with
  "The MIT License (MIT)" and contains "Copyright (c) 2022 Leon Sorokin"; `index.html`
  links `uPlot-LICENSE.txt` by a relative URL.
- **New: `test_the_page_loads_uplot_before_app_js`.** In `index.html`, `uPlot.min.css`
  comes before `app.css`, and `uPlot.iife.min.js` comes before `app.js`, both relative and
  `defer`.
- **New: `test_vendored_uplot_makes_no_network_request`.** The JS contains no `fetch(`,
  `XMLHttpRequest`, `WebSocket`, `http://` or `//cdn`, and its only `https://` is in the
  banner.
- **Unchanged, and deliberately so:** `test_the_page_names_only_its_own_files_and_relative_urls`
  (`:152-157`) keeps checking `index.html`, `app.js` and `app.css` only. The vendored JS
  has a URL in its banner, and its content is pinned by hash instead. That test also bans
  the word "sample" in `app.js`, so the graph code must use "point" or "value".

**No other Python test changes.** No API, `observe` or scenario behaviour changes, so
`test_server_http.py`, `test_server_ws.py`, the `observe` unit tests and the ordering and
ledger tests are untouched. `server.py` changes only in its `FRONTEND` table.

### 11.2 Automated browser checks (the capture script, a vcan host only)

No JavaScript test framework (0010 §7). `scripts/gui_demo_capture.py` already drives
Chrome over the DevTools protocol, and in `moving_session` it runs the stepped demo
(`:560-626`). M3b adds:
- **Overflow**: `#graphs` and each `.uplot` in the `OVERFLOW` expression (`:213-220`), at
  1440, 390 and 2000 (§7). A failure makes `main()` exit 1, as now (`:666-667`).
- **Structure**: exactly one `canvas` per available graph signal (five for the demo), each
  with a non-zero width and height; no card for an absent path.
- **Agreement**: each graph readout's "Now" value equals the signal table's cell for that
  path, read in one evaluation.
- **CSP**: `Page.addScriptToEvaluateOnNewDocument` installs a `securitypolicyviolation`
  listener that collects events. After the run the list must be empty.
- **Loop boundary**: a screenshot at scenario t ≈ 97 s with the 2-min window, showing the
  flat idle line through 90 s and the steps from 96 s.
- **Stale and restart**: after the existing SIGTERM (`:615-622`), a screenshot of the stale
  graphs; then a new simulator is started, and the check waits for the restart note and
  asserts every ring is empty except for the new run's points.
- **Window**: click "30 s", reload the page, and assert "30 s" is still pressed
  (`localStorage`).
- **Cost**: `Performance.getMetrics` at the start and end of a 60 s stretch (§10.2).

### 11.3 By hand, at implementation

- `python -m build --wheel` and `unzip -l` on the wheel: the three new files are under
  `ecu_simulator/api/static/`. CI installs editable (`ci.yml:88`), so CI does not see the
  wheel.

## 12. The owner's manual checklist (the M3b exit, 0010:747)

On a vcan host, `--api 127.0.0.1:8080`, the stepped demo profile, with the traffic script
running unless stated. Chrome, then Firefox.

- [ ] Five graphs appear: speed km/h, engine speed rpm, throttle %, engine load %, coolant
      °C. The VIN is still text in the vehicle panel header, and not graphed.
- [ ] Speed and rpm are drawn as **steps**, not ramps: flat, then vertical.
- [ ] The coolant line is a fine staircase that rises to about 240 s, then stays flat.
- [ ] Across the **90 s loop boundary**, the idle lines run flat through 90 s, and the
      next rise starts at 96 s. The time axis keeps increasing.
- [ ] **30 s, 2 min, 10 min** each change all five graphs. The choice survives a reload.
      With `localStorage` blocked (a private window with site data blocked), the page still
      works at 2 min.
- [ ] Each readout's "Now" matches the signal table. Min and max are right for the window.
- [ ] **Stop the simulator** (SIGTERM): the graphs stay, hatched and tagged "Stale, as of
      …", and stop at the last scenario time.
- [ ] **Restart it within about 15 s**: the graphs clear and show the restart note. No line
      joins the two runs.
- [ ] **Disconnect without a restart**: set Chrome DevTools, Network, to "Offline" for
      about 20 s, then back. The line has a gap, with nothing drawn across it, and the
      readout names the gap's scenario times. (Not verified: whether Chrome closes an open
      WebSocket when set offline. `kill -STOP` does not work, because a stopped process
      keeps its socket open and the page has no heartbeat. If neither works, accept this
      item from review.)
- [ ] **No scenario** (`ice_default.yaml`): the panel says "No scenario: the values are
      constant, as configured", with no plots.
- [ ] **A BEV or HEV profile**, if one is at hand: absent engine paths are listed as "Not on
      this vehicle", with no empty cards. If none is at hand, accept from review.
- [ ] **390 px** (device toolbar): one graph per row, no horizontal scroll, readable axis
      labels, the window buttons wrap.
- [ ] **2000 px**: one row of five, and the log keeps most of the height.
- [ ] **Pause graphs**: graphs freeze with "Paused at t = …"; the log keeps running; resume
      jumps to live. "Pause view" on the log does not pause the graphs.
- [ ] **Firefox**: no CSP errors in the console, and the graphs draw.
- [ ] The footer's uPlot licence link opens the MIT text.
- [ ] The page's DevTools Performance monitor shows CPU and heap in line with §10 during a
      minute of live drawing.

## 13. Open questions for the owner

1. **Throttle and load:** two graphs (proposed), or one graph with two lines told apart by
   dash and a text legend?
2. **Restart:** clear the graphs (proposed), or keep the previous run as a separate
   segment to the left, with a marker?
3. **Windows:** are 30 s, 2 min (default) and 10 min right?
4. **Pause graphs:** wanted, or should the graphs never pause?
5. **Cost on the bench host:** should the M3b live demo also measure the simulator host's
   CPU with the page open during traffic? It is not an M4 condition, and M4 is unchanged.
6. **Unparseable messages** (DEV-26's `NaN` in JSON): add a visible count of dropped,
   unparseable frames to the status bar in M3b, or leave it for later?
7. **The rpm unit label:** "rpm" (proposed) or "1/min"?
8. **Cycle markers:** leave them out (proposed, the API has no cycle length), or record a
   future API field for a later decision?
9. **Placement:** above the log (proposed), or collapsible, to give the log more height at
   1440 × 900?

## 14. The 0010 amendment needed (a proposal, applied only if the owner approves)

A **tenth revision**, wording only. It changes no threshold of P1–P9, no API, and no V1.0
or Phase 8b gate. The M2 `STOP` stays open.
- **§7 (`:563-564`).** Replace "for per-signal sparklines" with "for per-signal graphs of
  speed, engine speed, throttle, engine load and coolant temperature, drawn against
  scenario time (`as_of`) from the `state` messages the page receives, with a selectable
  window and a bounded browser-side history. There is no server-side state history". Name
  the pin: **uPlot 1.6.32**, served as `uPlot.iife.min.js`, `uPlot.min.css` and
  `uPlot-LICENSE.txt`. State that the CSP is unchanged.
- **§9.3 (`:728`).** Add: "vendored files are pinned by SHA-256".
- **§10 (`:747`).** M3b's exit becomes "Frontend file tests green in CI; owner runs the
  manual view checklist for the graphs", matching M3a's row (`:746`).
- **Status (`:10-13`).** It still says "M3 has not started". M3a is built on `gui`. That
  line is out of date whatever M3b does; the owner may want it corrected in the same
  revision.

## 15. Implementation outline (only after approval)

Each task is committed on `gui`, and stops for review where the owner asks.

| # | Task | Files |
|---|---|---|
| 1 | Record the 0010 tenth revision (§14), as approved | `docs/decisions/0010-gui-observer-api.md` |
| 2 | Vendor uPlot 1.6.32 byte-identical from the npm tarball; record the tarball's `shasum`. Add the three `FRONTEND` rows. Tests first (§11.1): list, hashes, licence, load order, no network use | `src/ecu_simulator/api/static/uPlot.iife.min.js`, `uPlot.min.css`, `uPlot-LICENSE.txt`; `src/ecu_simulator/api/server.py`; `tests/unit/api/test_frontend_files.py` |
| 3 | Markup and layout: the graphs panel above the log, the main column as a flex column, the card grid, the narrow rules, the stylesheet and script links, the licence link | `index.html`, `app.css` |
| 4 | Data: the rings, ingest in `applyState`, the break in `fail()`, the reset on restart in `connect()`, `as_of` null handling, missing and non-finite values; the `engine.rpm` unit | `app.js` |
| 5 | Drawing: uPlot instances with stepped paths, the scales, the right-edge hold, the window buttons and `localStorage`, readouts, pause, the ResizeObserver, the fallback when uPlot is missing | `app.js`, `app.css` |
| 6 | Capture-script checks (§11.2) and a moving-session run | `scripts/gui_demo_capture.py` |
| 7 | Live demo record, with screenshots, the cost figures, the overflow results, and the §12 checklist for the owner | `docs/validation/gui-m3b-live-demo.md`, with its captures |

**Still open after M3b:** the M2 latency `STOP`, and the hosted `CAN_ISOTP` gap. M3b
closes neither.
