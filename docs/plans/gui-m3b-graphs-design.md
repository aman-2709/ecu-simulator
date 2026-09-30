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
- The first version of this document is `9a69d2e`; the second is `0b12379`.
- **Third revision (owner, 2026-09-30, §2.1):**
  - the silence rule is removed, so page health rests only on connection or poll
    failures, malformed messages, and reported state-encoding failures;
  - "last known" is distinct from "stale";
  - `/status` reports current encoding health;
  - the first good snapshot after a failure is always published;
  - a bounded resynchronisation;
  - fault injection in a test harness, independent of DEV-26;
  - explicit SIGSTOP/SIGCONT assertions;
  - two implementation checkpoints, with overhead measurements.

  Contradictions found, with their resolutions, are in §14.1.

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

**Proposed at the owner's request, since `9a69d2e`:**
- one additive field, `nonfinite`, on `GET /vehicle` and the WS `state` message;
- on `GET /status`: the counters `state_encode_failed` and `vehicle_encode_failed`, and the
  health object `state_encoding`;
- containment of state-encoding failures in the server;
- one change to the state push rule: the first good snapshot after a failure is always
  published (§8).

These are API and server changes. They keep the state rate limit (at most one push per
0.25 s) and do not touch the diagnostic path.

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

### 2.1 Third round (owner, 2026-09-30)

Mandatory, from the previous review:
1. **No silence rule.** Delivery is change-only, so silence proves nothing. Page health
   rests only on connection or poll failures, malformed messages and reported
   state-encoding failures. Recovery is explicit. A healthy, unchanging session with no
   scenario stays "Live" for 60 s or more (§8.4, §12.2).
2. **"Last known" after an encoding failure**, distinct from "stale" after a disconnect,
   with its marking and how it clears (§8.4).
3. **Non-finite testing independent of DEV-26:** controlled fault injection. The Python
   tests set the value in the runtime directly; the browser checks use a test server that
   injects it in-process, with no profile file. The shipped page stays live-data-only
   (§12.3).
4. **SIGSTOP:** assert that the status-poll timeout really puts the page into its
   disconnected state, and that after SIGCONT there is a gap and a recovery **without a
   restart**: `started_at` unchanged, no restart marker (§12.2).
5. **Two checkpoints,** with the safeguards' overhead measured; optionally one rotated M2
   early check as a regression comparison, not acceptance. The M2 `STOP` stays unresolved
   unless its criteria pass (§17).

This round's eight points:
1. `/status` reports **current encoding health**, `state_encoding: {ok, last_ok_at,
   last_failed_at}`, next to the cumulative counter. `ok` is set explicitly on each success
   and failure; the timestamps are for reporting only and are never compared with a clock
   to decide `ok` (§8.3).
2. Health is defined for the **complete** WS state snapshot, vehicle and DTCs. A successful
   `GET /vehicle` never clears a failed full-state encode (§8.3).
3. **The first good snapshot after a failure is always published**, even if its text
   matches the last successful one, within the existing rate limit (§8.3).
4. The browser clears "Not current" only after **applying a valid replacement state for the
   current connection and run**. A healthy `/status` alone is not enough (§8.4).
5. **Bounded resynchronisation** after malformed messages, including a session with no
   scenario and no changes. One path is chosen and justified, and checked against point 2
   (§8.5).
6. **Regression cases:** same-value recovery, and a malformed state followed by unchanged
   data (§12).
7. Keep the harness fault injection, the SIGSTOP/SIGCONT assertions and the two
   checkpoints.
8. **0010:** §4.3 and §5 updated (specified, not implemented), and an eleventh-revision
   note (§15).

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
- **Every new connection gets the current state right after `hello`, changed or not.**
  - `Publisher.connect` gives the new connection the last pushed state text: "a new client
    gets the current state, changed or not" (`publisher.py:177-178`).
  - The writer sends `hello`, then that state, then the history (`observe/writer.py:22-31`;
    the contract is at `observe/connection.py:10`).
  - `tests/unit/api/test_server_ws.py:59-63` pins it: the second frame after connecting is
    the `state`.
  - The state sent is `_last_state`, which is the **last successfully pushed** text
    (`publisher.py:254-255`, `:269-270`).

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

## 8. Proposal: the non-finite rule, containment, health and resynchronisation

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

### 8.3 The server: containment, encoding health, and the first-good publish

The mechanism is **sanitise, then encode strictly, then contain**:
1. **Sanitise before `json.dumps`:** `snapshots.vehicle` builds `signals` with non-finite
   floats replaced by `None`, and builds `nonfinite` (§8.2).
2. **Encode strictly:** `state_message` calls `json.dumps(..., allow_nan=False)`, and
   `GET /vehicle` uses `web.json_response` with `dumps` bound to the same strict call. After
   step 1 a non-finite value cannot reach the encoder. The strict flag turns any value that
   still does into an exception instead of invalid JSON.
3. **Contain any residual failure**, as below.

**Health is defined for the complete WS state snapshot**: `state_message`, which is vehicle
**and** DTCs (`snapshots.py:56-58`). It is not defined for `GET /vehicle`, which is only
part of it.

**`GET /status` `api` gains** (additive, specified, not implemented):

| Field | Meaning |
|---|---|
| `state_encode_failed` | Periodic full-state snapshots that failed to build or encode. Cumulative, never reset |
| `vehicle_encode_failed` | `GET /vehicle` answers that failed to encode (500). Cumulative. **Separate from the above, and never touches `state_encoding`** |
| `state_encoding.ok` | `true` if the **latest** periodic full-state attempt succeeded, `false` if it failed. Set **explicitly** on every attempt, success or failure. `true` at startup, because a failed initial encode refuses to start |
| `state_encoding.last_ok_at` | Wall time (`time.time()`) of the latest successful attempt. **Reporting only** |
| `state_encoding.last_failed_at` | Wall time of the latest failed attempt, or `null`. **Reporting only** |

- **The timestamps never decide `ok`.** No code, on the server or the page, compares them
  with a clock or with each other to decide health. `ok` is only ever the stored result of
  the latest attempt.
- **A successful `GET /vehicle` changes nothing in `state_encoding`.** It can succeed while
  the full snapshot fails, for example on a DTC field, and it must not report the full
  state as healthy.

**The periodic state task, precisely.** Today it is (`publisher.py:265-272`): build the
text; push it if it differs from the last pushed text; push `dropped`; sleep. M3b makes it:

```
loop:
    try:
        text = snapshot()                        # the full state: vehicle and DTCs
    except Exception as error:                   # CancelledError is not an Exception: shutdown unchanged
        state_encode_failed += 1
        state_encoding.ok = False
        state_encoding.last_failed_at = time.time()
        log_once("state encode", error)          # the same _log_once as encode_failed
        must_publish = True                      # the next good snapshot is published regardless
    else:
        state_encoding.ok = True
        state_encoding.last_ok_at = time.time()
        if must_publish or text != last_state:
            push_state(text)                     # sets last_state, and every connection's one-slot state
            must_publish = False
    push_dropped()
    await sleep(interval_s)
```

- **The first-good publish rule.** After one or more failed attempts, the **first**
  successful attempt is pushed **even if its text equals the last successful text**.
  - Why: a page that marked its data "last known" needs a replacement to apply (§8.4), and
    with no scenario and no changes, nothing else would ever arrive.
  - After that push the only-when-changed rule applies again.
- **The rate limit is unchanged:** one attempt per `interval_s` (0.25 s), and at most one
  push per attempt. The forced push replaces the one-slot state of each connection, as any
  push does (`connection.py:114-117`). It is never queued, so it cannot add a backlog.
- **A failed attempt pushes nothing.** Every connection's pending state and `_last_state`
  keep the last good text. So **a client that connects during a failure receives the last
  good state after `hello`** (§4.1). That state is last known, not current. The page learns
  that from `state_encoding.ok` (§8.4).
- **`GET /vehicle`:** a residual failure answers **HTTP 500**, "vehicle state could not be
  encoded (decisions/0010 §4.3)", increments `vehicle_encode_failed`, and is logged once
  per type. It never touches `state_encoding`.
- **Startup** (`check_state_size` and the initial `push_state`, `api/server.py:165-176`):
  non-finite values are sanitised. Any other failure becomes `ApiStartupError`, exit 2,
  naming the exception type. So `ok` starts `true`, with `last_ok_at` the startup time.
- **Why skip rather than send a fallback:** exchanges need a fallback event to keep `seq`
  contiguous (0010 §5). `state` has no sequence and is replaced, not queued, so keeping the
  last good state and reporting `ok: false` is the honest answer.

### 8.4 The page: invalid values, health, and its marks

**Invalid values.**
- A path in `nonfinite`, or any graphed or tabled value that is not a finite number and not
  in `unavailable`, reads **"invalid value"** in the table and in the graph's line 1.
- **The graph leaves a gap, never a line to or from it.** When an invalid value arrives at
  `as_of = t`, the ring stores the last valid value at the last `as_of` it was known valid,
  then a `NaN` point at `t`. A valid value later starts a new segment. If uPlot's default
  would draw the hold past the last valid point, the implementation sets the series' gap
  alignment so it does not. The `data-*` attributes and the manual check verify it
  (§12, §13).

**Health rests on exactly three signals. Silence is never one of them.** State is delivered
only on change (§4.1), and with no scenario a healthy connection receives no `state` after
its first. So the page never infers anything from time without messages. It has **no
"no valid state for T seconds" rule, in any form**.

| Signal | Detected by | Page condition | Words | Clears when |
|---|---|---|---|---|
| **Connection or poll failure** | the socket closes, or a `GET /status` poll or connect-time fetch fails (5 s timeout, `app.js:19`, `:243-256`); unchanged M3a behaviour | **Stale** (`down` or `refused`) | "Disconnected" / "Refused"; tag "Stale, as of HH:MM:SS UTC" | The existing reconnect: `hello` on a new connection (`app.js:269-297`) |
| **Malformed message**: a WS frame that fails `JSON.parse`, or parses to a non-object | `onMessage` | **Last known (malformed)** | "Connected, last known data"; tag "Last known, HH:MM:SS UTC"; banner "A message from the simulator could not be read …" | The resync of §8.5 applies a valid `state` |
| **Reported encoding failure**: `state_encoding.ok` is `false` in any `GET /status` the page reads (the connect-time fetch or the 2 s poll) | the status handlers | **Last known (encoding)** | "Connected, last known data"; tag as above; banner "The simulator could not encode its state (`state_encode_failed` N) …" | See "Clearing" below |

- **"Last known" is not "stale".** Stale means the page lost the simulator: nothing
  arrives. Last known means the page is connected and exchanges still arrive, but the
  vehicle and DTC data cannot be trusted to be current. They are marked differently:
  - **stale** keeps the M3a marking on every panel: the hatched top edge, the faded body,
    and "Stale, as of …" (`app.css:116-125`);
  - **last known** marks only the vehicle, DTC and graphs panels, with a dotted top edge
    (not the hatch) and the words "Last known, HH:MM:SS UTC". The time is when the page
    last applied a valid `state`. Values are not faded, so they stay readable. The
    exchange log is not marked, because its events still arrive and are valid.
  - The lamp is not green in either condition. Stale: "Disconnected, retry in N s", as
    today. Last known: "Connected, last known data", with an amber outline lamp.
  - Stale takes precedence: while disconnected, the page is stale whatever else holds.
- **Graphs:** entering either condition sets each ring's pending break (§6.7), so no line
  joins the data before and after.
- **Counts stay visible.** A status-bar readout "Malformed messages **N**, last HH:MM:SS
  UTC" appears when N > 0. The polled readouts show `state_encode_failed` when it is
  above 0. Neither is hidden again.

**Clearing "last known": only an applied replacement.** In both cases the page clears the
condition **only after it receives and applies a valid `state` for the current connection
and run**:
- **current connection:** the `state` arrived on the socket of the page's current attempt
  (the existing `S.gen` token, `app.js:137-146`);
- **current run:** that attempt's `GET /status` had the same `started_at` as the page's
  run (`app.js:149-155`). A restart clears the graphs and starts a new run anyway (§6.8).

A healthy `/status` alone never clears it. The two causes differ in **what counts as a
replacement**:
- **Malformed:** the first valid `state` applied **after the resync began** (§8.5) clears
  it.
- **Encoding:** a valid `state` clears it only if it is applied **after** the page has read
  `state_encoding.ok == true` in a `GET /status` for this connection and run. A `state`
  applied while the page's latest reading of `ok` is `false` does not clear it. That
  includes the last-good state a reconnect delivers during a failure (§8.3).
- **The race, and how it is closed:**
  - When the server recovers, the forced first-good push (§8.3) may reach the page
    **before** the poll that reports `ok: true`, and then no further `state` may come
    (no scenario, no changes).
  - So when the page reads `ok` change from `false` to `true`, it starts the resync of
    §8.5. That delivers the current state after `hello`.
  - `ok` and `_last_state` are updated in the same synchronous step on the server's single
    loop (§8.3). So any connection accepted after a `/status` reply that said `ok: true`
    is given the post-recovery state.
- If both causes are active, both must clear. The marking stays until the last one does.

**Recovery, summarised:**
- stale → live on `hello`, as in M3a;
- last known (malformed) → live when the resync's `state` is applied;
- last known (encoding) → live when `ok` has been read `true` and a `state` is applied
  after that.

### 8.5 Bounded resynchronisation: a reconnect, not a REST re-fetch

**Chosen: the page resynchronises by reconnecting the WebSocket**, with its existing
`after=lastSeq` resume, and applies the `state` that follows `hello`.
- **No server change is needed for this.** Every new connection already gets the current
  state right after `hello` (§4.1). The owner's brief said a new connection gets no
  `state`, which the code contradicts. §14.1, C1, records this.
- **Why a reconnect, not `GET /vehicle` and `/dtcs`:**
  - The `state` after `hello` is the **complete** snapshot, vehicle and DTCs, produced by
    the same encoder as the pushes, which health is defined on (§8.3). A REST re-fetch is
    two separate encodes, and `GET /vehicle` can succeed while the full state fails. It
    could therefore never prove encoding recovery (§2.1, point 2). This is the tension the
    owner anticipated, §14.1, C2.
  - The exchange stream resumes by `after=lastSeq`, with no gap or duplicate beyond what
    the server's history reports (0010 §4.5). An exchange whose frame was the malformed
    one is re-delivered from history, because `lastSeq` did not advance past it.
  - It reuses the page's existing reconnect path, markers and backoff.
- **How it runs:**
  1. **Trigger:** a malformed frame, or `ok` read changing from `false` to `true`.
  2. The page closes its socket itself (normal closure). The `S.gen` token makes the old
     socket's late events harmless, as today (`app.js:137-146`).
  3. It calls `connect()`, which fetches `GET /status` first, so `started_at` and
     `state_encoding.ok` are read before the socket opens.
  4. It opens `/events?after=lastSeq`, receives `hello` and the `state`, and applies them.
     The log gets the marker "Resynchronised after an unreadable message", not "Connection
     lost".
- **Bounded:**
  - one resync per trigger, and at most **3 automatic resyncs in a row** that each end in
    another malformed frame before a valid `state`;
  - the waits between them are 1, 2 and 4 s, the existing backoff's first steps
    (`app.js:15-16`);
  - after the third, the page stops resyncing and shows "Could not resynchronise: messages
    from the simulator are unreadable. Retry now". It stays "last known", never "Live".
  - A valid `state` resets the count.
  - While last known (encoding), the page does **not** reconnect in a loop. It waits for
    the 2 s poll to read `ok: true`, then resyncs once.
- **No scenario, no changes:** the resync still ends with a valid `state`, the one after
  `hello`, so recovery never depends on a spontaneous update. §12.2 tests exactly this.
- The page never sends anything on the socket; closing it is not a message.
- **Cost:** one reconnect per episode, each a small `GET /status` plus a WebSocket upgrade
  and the history. It is bounded as above, and uses one of the 4 client slots, as the page
  does today.

### 8.6 What "Live" means after M3b

"Live" means all three of these hold:
- the socket is open and the latest poll succeeded;
- no malformed frame is unresolved;
- the latest `state_encoding.ok` the page read is `true` and has been followed by an
  applied `state`.

Time without messages never changes it.

## 9. Proposal: accessibility

- Each card is a `<figure>`: `<figcaption>` has the name and unit; line 1 has the current
  value; line 2 has the min and max in the window. The plot container is
  `aria-hidden="true"`; the text carries the content.
- **Not colour alone:** one line per graph in the ink colour. Stale uses the hatch, the
  tag and the banner; last known uses a dotted edge, its own tag and the banner. A gap is a break **and** words. A restart is a
  note. "unavailable" and "invalid value" are words.
- Readouts are not `aria-live`, because they change up to four times a second.
- Buttons are ordinary buttons. uPlot's cursor, legend and selection are off, so the plot
  has no pointer-only content.
- Nothing is animated.

## 10. Constraints kept, and files

| Constraint | How |
|---|---|
| API state rate | The limit is unchanged: one attempt per `STATE_MIN_INTERVAL_S`, at most one push per attempt. The push rule gains one case, the first-good publish after a failure (§8.3) |
| Diagnostic path | Unchanged: nothing in `ecu/`, `transport/`, `protocols/` or `scenario/`, and nothing on the hot path, changes |
| Endpoints | None added. `nonfinite`, `state_encode_failed`, `vehicle_encode_failed` and `state_encoding` are additive fields. The `state` after `hello` already exists (§4.1) |
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
- **Server side (estimate):** §8.2's `isfinite` pass and §8.3's `try` and flag, a few
  microseconds per snapshot at ≤ 4 Hz. The forced push after a recovery is one ordinary
  push. Checkpoint 1 measures it (§17), before and after.

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

### 12.1 Python, at checkpoint 1 (`observe` in every job; `api` in the `.[dev,gui]` job)

**Fault injection is controlled and in-process.** Every test below puts the bad value or
the failure into the running objects directly:
- it sets a float signal with `VehicleState.set` (`vehicle/state.py:171`) to `nan`, `inf`
  or `-inf`; or
- it replaces the snapshot callable with one that raises, for a counted number of calls.

No profile file carries a non-finite value, so none of these tests depends on DEV-26 or
its fix.

**The non-finite rule** (`tests/unit/observe/`):
- `snapshots.vehicle` after `set(path, nan | inf | -inf)`:
  - each value is `None`, and `nonfinite` lists exactly those paths, sorted;
  - finite floats, ints, bools and strings are unchanged;
  - `nonfinite == []` when there are none.
- **Precedence:** a path forced into both `unavailable` and a non-finite value appears only
  in `unavailable`, with `null`.
- `state_message` parses under `json.loads` with a `parse_constant` hook that raises, so no
  `NaN` or `Infinity` token can hide in it. With an unserialisable value it raises, which
  pins `allow_nan=False` and strictness.

**The state task, encoding health and the first-good publish** (`tests/unit/observe/`):
- A snapshot that raises once, then succeeds with a new text:
  - the task is still running, and `state_encode_failed == 1`;
  - `state_encoding.ok` was `false` after the failure and `true` after the success;
  - `last_failed_at` and `last_ok_at` were set from an injected clock;
  - the previous state stayed current during the failure;
  - the new text was pushed, and `push_dropped` ran in the failed turn.
- **Same-value recovery:** the snapshot raises once, then succeeds with **exactly the last
  pushed text**. The text is **pushed again**: each connection's one-slot state is set, and
  a registered connection's `next_message()` returns it. The attempt after that, with the
  same text, pushes nothing.
- `ok` is set on every attempt: success, failure, success gives `true`, `false`, `true`,
  with no clock comparison. A test with a frozen clock and a test with a clock going
  backwards give the same `ok` sequence.
- A connection registered during a failure receives the last good text after `hello`
  (pins §8.3's statement that it is last known, not current).
- Logging: two failures of one exception type log once (`caplog`); a second type logs
  again.
- `CancelledError` still ends the task.

**The API** (`tests/unit/api/`):
- `GET /vehicle` with a non-finite signal set in the runtime: 200, `null`, `nonfinite` set.
- The WS `state` after `hello`, and a later pushed `state`, carry `null` and `nonfinite`.
- **Changed test:** `test_server_ws.py:63` asserts the exact `vehicle` key set; it gains
  `nonfinite`.
- **Health is for the full state:** make the DTC part of the snapshot raise while the
  vehicle part is fine:
  - `/status` shows `state_encoding.ok == false`, and `state_encode_failed` rising;
  - `GET /vehicle` is still 200, and **`ok` stays `false` after it**.
- A residual `GET /vehicle` failure: 500 with the stated text; `vehicle_encode_failed`
  rises; `state_encoding` is unchanged; `GET /status` is still 200.
- `GET /status` carries `state_encode_failed`, `vehicle_encode_failed` and `state_encoding`.
- **Startup:** a non-finite value set in the runtime before `ApiServer` is built starts
  normally, sanitised, with `ok == true`. A residual failure in `check_state_size` raises
  `ApiStartupError` naming the exception type.
- **Same-value recovery over the wire:** a WS client with no scenario:
  1. it receives `hello` and `state`;
  2. the snapshot raises for three attempts, then recovers to identical text;
  3. the client receives a **second `state` with identical text**, and no further `state`
     after it.

**Unchanged:** the API-off proofs, the differential comparison, the ordering, ledger and
publisher-turn tests. The state task is not a publisher turn, and nothing on the hot path
changes.

### 12.2 Browser acceptance checks, at checkpoint 2

**A canvas count alone proves nothing.** A canvas can exist and be blank, stale, joined
across a gap or unbounded. So every check reads only these:
- the **visible readouts**, as text: line 1's current value, and line 2's min and max;
- the connection text, the banner and the panel tags;
- a small set of **read-only diagnostic `data-*` attributes** on each graph container.
  The page writes them after each ring update and draw, and nothing in the page reads
  them. They describe the page's own rings and drawing, never uPlot internals.

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

The page's own condition is exposed the same way on `<body>`:
- `data-health`: `live`, `stale`, `last-known`;
- `data-last-known`: empty, `malformed`, `encoding`, or both;
- `data-malformed-total`;
- `data-resyncs`: resyncs in the current episode.

Two servers are used:
- the **real simulator** (`ecu-simulator --api`) for everything except fault injection;
- a **fault-injection test server** (§12.3) for the non-finite and encoding cases.

| Case | How the check exercises it | Passes if |
|---|---|---|
| **No false invalidation** (§2.1, point 1) | Real simulator, `ice_default.yaml` (no scenario), **no traffic**, the page left alone for **65 s**. That is longer than every timer in the page: the 5 s fetch timeout, the 15 s backoff cap and the 30 s stability window (`app.js:15-19`) | At every 5 s sample: conn text "Live"; `body[data-health]` = `live`; no "Last known" or "Stale" tag; no banner; `data-malformed-total` = 0. The page received exactly one `state` in the whole period (counted by the WebSocket wrapper below) |
| **Held value at the left edge of a trimmed window** | Stepped demo, 30 s window, read at t ≈ 55 s: speed has been 80 since t = 21 | `data-left-value` = 80; line 2 reads "min 80 · max 80 in 30 s"; `data-segments` = 1; `data-oldest-t` < `data-as-of` − 30 |
| **Bounded history** | `--long` (about 11 min) on `ice_scenario.yaml`, whose sine on `engine.engine_load` changes on every message, with traffic | At every 10 s sample, for every graph: `data-points` ≤ `data-cap`. After 600 s: `data-oldest-t` ≥ `data-as-of` − 601 and `data-left-value` is set |
| **Disconnect without restart** (SIGSTOP) | Stepped demo. Stop the traffic. Read `started_at` from `GET /status`. `SIGSTOP` the simulator, and wait for the page | **Within 8 s** (2 s poll + 5 s timeout + 1 s): conn text starts "Disconnected"; `body[data-health]` = `stale`; the banner is shown; the panels carry "Stale, as of". This asserts the poll timeout really takes the page down, not merely that the process paused |
| … then SIGCONT | `SIGCONT`, and wait for "Live" (within the backoff, ≤ 16 s) | `GET /status` `started_at` **equals** the value read before; the log has **no** "Simulator restarted." marker, and has "Connection lost, then resumed."; `data-run` unchanged; `data-gaps` rose by 1 and `data-segments` by 1; line 2 names the gap's scenario times; seq continuity in the log (no duplicate seq) |
| **Restart reset** | The existing SIGTERM (`gui_demo_capture.py:615-622`), then a new simulator | `data-run` changed; `data-oldest-t` ≥ 0 and `data-points` counts only the new run; the restart note and marker are visible; `data-segments` = 1 after the first new point |
| **Window persists** | Click "30 s", reload | `data-window-s` = 30 everywhere; "30 s" has `aria-pressed="true"` |
| **Pause while buffering** | "Pause graphs" for 10 s, then resume | While paused: `data-paused` = true; `data-drawn-to` and line 1 unchanged; `data-as-of` rising; `data-points` not falling except by the caps. After: `data-drawn-to` = `data-as-of`. The log kept running |
| **Non-finite values** | Fault-injection server, scenario profile, **no profile file with a non-finite value**: the harness sets `engine.coolant_temp` to `nan` for scenario t ∈ [10, 20) s, in-process (§12.3). No traffic | While invalid: `data-state` = `invalid`; line 1 and the table read "invalid value"; conn text "Live"; `data-malformed-total` = 0. After: `data-gaps` ≥ 1, `data-segments` ≥ 2, and no point is drawn between the last valid and the next valid `t` |
| **Malformed state, then unchanged data** (regression) | Real simulator, `ice_default.yaml`, no traffic, so no `state` will come on its own. The WebSocket wrapper dispatches one `MessageEvent` with `{bad` on the page's socket | Immediately: `data-health` = `last-known`, `data-last-known` = `malformed`, the tag "Last known", `data-malformed-total` = 1. Within 3 s: the resync reconnected; `data-health` = `live`; the tag is gone; the log shows "Resynchronised after an unreadable message"; `started_at` unchanged; no restart marker; seq continuity |
| **Malformed, bounded** | As above, but the wrapper answers every new socket's first frame with `{bad` | Exactly 3 automatic resyncs (`data-resyncs` = 3), 1, 2 and 4 s apart; then "Could not resynchronise …" with "Retry now"; the page never shows "Live" meanwhile. Removing the wrapper's fault and pressing "Retry now" recovers |
| **Encoding failure, then recovery to changed data** | Fault-injection server, stepped scenario: the harness makes the full snapshot raise for 5 s | Within one poll (≤ 3 s): `data-last-known` = `encoding`, the banner names `state_encode_failed`. The graphs get a break. After the fault ends: `live` again, and only after a `state` was applied following a `/status` with `ok: true` |
| **Same-value recovery** (regression) | Fault-injection server, **no scenario**: the snapshot raises for 5 s, then recovers to **identical** text | `data-last-known` = `encoding` during the fault; after it, `live` within one poll plus one resync (≤ 5 s); the wrapper saw a `state` after the recovery (the forced push, the post-`hello` state, or both). A page loaded **during** the fault starts as "last known", not "Live", although it received a `state` after `hello` |
| **REST is not proof** | Fault-injection server: the DTC part of the snapshot fails and the vehicle part succeeds | `GET /vehicle` answers 200 throughout; the page stays "last known" until the fault ends, and then clears as above |
| **Overflow** | `#graphs` and each `.uplot` in `OVERFLOW` (`gui_demo_capture.py:213-220`), at 1440, 390 and 2000 | No `scrollWidth` above its `clientWidth` |
| **Log rows at 1440 × 900** | Section open, log filled | At least 5 full log rows inside `#logwrap` |
| **Agreement** | Every live screenshot | Each line-1 value equals the signal table's cell, read in one evaluation |
| **Cost** | §11.3 | Recorded, not judged |

**The WebSocket wrapper** is test instrumentation, installed with
`Page.addScriptToEvaluateOnNewDocument` before the page's scripts run. It wraps
`window.WebSocket` so the script can count received `state` frames and dispatch synthetic
`MessageEvent`s. The shipped page is unchanged and contains no test code.

**Chrome only.** The capture script drives Chrome over the DevTools protocol. Firefox is a
snap here, cannot run in the namespace automation, and no Playwright browser is
downloaded. Nothing in §12.2 covers Firefox (§13).

### 12.3 The fault-injection test server

- **What it is:** `scripts/gui_fault_server.py`, a test harness. It is not package data and
  not served by the shipped simulator.
- **How it runs:** in the capture run's namespace, on vcan. It builds the runtime from a
  **shipped** profile (`ice_default.yaml`, `ice_scenario.yaml`) or the stepped demo, and
  runs the real `app.run(..., api=...)` in-process, so the page talks to the real
  `ApiServer`.
- **Faults are scheduled in-process, by scenario time or wall time, from command-line
  options:**
  - **`--nonfinite PATH:START:END`**: after each scenario apply, the harness sets `PATH` to
    `nan` while `t` is in `[START, END)`, through `VehicleState.set`. It wraps the runner's
    `apply` in the harness only; nothing in `src/` changes. With no scenario, it sets the
    value once on a timer.
  - **`--state-fault START:END[:vehicle|dtcs]`**: the snapshot callable the server passes
    to `run_state` is wrapped so that it raises while the window is open. Optionally only
    the DTC or vehicle part raises. The wrapper returns the real snapshot otherwise, so
    recovery text is genuinely identical when nothing changed.
- **Independent of DEV-26:** no profile file with a non-finite value exists. When DEV-26's
  load-time rejection lands, nothing here changes.
- **Traffic:** none by default. A `01 05` request during a non-finite window would reach the
  OBD encoder, which DEV-26 records as crashing on non-finite input. That is a diagnostic
  path defect, outside this design.
- **The shipped page stays live-data-only:** it has no fault switch, no test mode, and no
  sample data.

### 12.4 By hand, at implementation

`python -m build --wheel` and `unzip -l` show the three uPlot files under
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
- [ ] Stop the simulator: the graphs stay, marked stale. Start it again: they clear with
      the restart note; no line joins the runs.
- [ ] `kill -STOP` for 12 s: within about 8 s the page says "Disconnected". `kill -CONT`:
      it comes back "Live" with a gap in the graphs, the log says "Connection lost, then
      resumed", and there is no restart marker.
- [ ] `ice_default.yaml` (no scenario), left alone for a few minutes: "No scenario: the
      values are constant, as configured", no plots, and the page stays "Live".
- [ ] The fault-injection server with `--nonfinite`: "invalid value" in the table and the
      coolant graph, a gap, and the page still "Live".
- [ ] The fault-injection server with `--state-fault`: "Last known" on the vehicle, DTC and
      graphs panels, distinct from "Stale", and the exchange log still running. After the
      fault, "Live" again.
- [ ] 390 px: one graph per row, the head on two rows, no horizontal scroll.
- [ ] 2000 px: one row of five.
- [ ] The footer's uPlot licence link opens the MIT text.
- [ ] The measured cost in the live-demo record (§11.3), and the server overhead from
      checkpoint 1 (§17), are in line with the estimates, or the difference is explained.

## 14. Open questions and contradictions

### 14.1 Contradictions found, and the resolution chosen

- **C1. The brief said a new WS connection gets `hello` and history but no `state`
  (citing `publisher.py:166-186`). The code contradicts it.**
  - `connect()` gives the new connection the current state "changed or not"
    (`publisher.py:177-178`).
  - The writer sends `hello`, then that state, then the history (`writer.py:22-31`).
  - `test_server_ws.py:59-63` pins it.
  - **Resolution:** the design relies on the existing behaviour. The resync is a bounded
    reconnect, with **no server change** for it (§8.5). 0010 already says the state
    follows `hello` (§4.5, the fourth revision). The eleventh revision records that no new
    "state after `hello`" rule is added, because it exists.
- **C2. A REST re-fetch against "health covers the complete snapshot".**
  - `GET /vehicle` can succeed while the full state (vehicle and DTCs) fails, so a REST
    snapshot cannot prove recovery.
  - **Resolution:** REST is not used for resync at all. Both conditions clear only on a
    `state` applied from the WebSocket. For an encoding failure, that `state` must also
    come after a `/status` that reports `ok: true`. `GET /vehicle` never touches
    `state_encoding` (§8.3, §8.4).
- **C3. "Clear only on an applied replacement" against "no replacement ever comes"** (no
  scenario, unchanged data).
  - **Resolution:** the first-good publish (§8.3), plus the resync after the page reads
    `ok` change from `false` to `true` (§8.4). Together they always deliver a replacement.
- **C4. The post-`hello` state during a failure against "clear on an applied state".**
  - A page that reconnects while the server is failing receives the last good state, which
    is valid JSON but not current.
  - **Resolution:** for the encoding condition, a `state` counts only after `ok: true` has
    been read (§8.4). A page loaded during a failure starts "last known", because its
    connect-time `/status` says `ok: false`.
- **C5. The second revision's "10 s without a valid state" rules, and "3 malformed frames
  in a row", against point 1.**
  - **Resolution:** all removed. The first malformed frame marks "last known" at once and
    starts the resync. Only the resync's retries are counted and bounded (§8.5).
- **C6. "Keep the existing rate limit" against "publish even if unchanged".**
  - Not a real conflict: the forced push uses the one attempt per interval that already
    exists, and replaces a one-slot state (§8.3). It is noted because it changes the push
    rule's text.

### 14.2 Still open

1. **Restart:** clear the graphs (proposed), or keep the previous run as a separate segment
   with a marker?
2. **Cost on the bench host:** measure the simulator host's CPU with the page open during
   traffic in the M3b live demo? It is not an M4 condition.
3. **Cycle markers:** leave them out (proposed), or record a future API field for a later
   decision?
4. **Resync bound:** three automatic resyncs, then a manual "Retry now". Is that right?

Answered earlier: separate throttle and load graphs, the windows, the pause, reporting
unparseable messages, "rpm", and the placement.

## 15. Decision 0010: the tenth and eleventh revisions

Made in the same commits as this document, documentation only.
- **The tenth revision** (`0b12379`):
  - the status line;
  - the M3b wording in §7, §9.3 and §10;
  - `nonfinite` and state-encoding containment in §4.3 and §5, marked specified, not
    implemented.
- **The eleventh revision** (this commit, 2026-09-30):
  - records the owner's third-round decisions (§2.1), and that the approval covers
    documentation only;
  - §4.3 gains encoding health, the first-good publish rule, and the skipped push's
    effect on new connections;
  - §5 gains `state_encoding` and `vehicle_encode_failed`;
  - §9.3 gains the fault-injection harness and the regression cases;
  - §10's M3b row names the two checkpoints.

  All of it is specified, not implemented. The M2 latency `STOP`, the hosted `CAN_ISOTP`
  gap, the Phase 8b gate and the V1.0 branch rule stay open.

## 16. Concerns for the owner

- **M3b changes server code at checkpoint 1**: `observe/snapshots.py`,
  `observe/publisher.py` and `api/server.py`. The changes are off the diagnostic path. But
  the state task shares the loop, the M2 latency `STOP` is open, and P9 measures publisher
  turns, not the state task. Checkpoint 1 therefore measures the overhead (§17).
- **The fault-injection harness wraps the runner's `apply` and the state snapshot from
  outside `src/`.** It must keep to public seams, the `ApiServer` construction and the
  runtime objects. If a needed seam is private, checkpoint 1 stops and reports rather than
  widening an API for the harness.
- **Resync uses a client slot briefly.** With 4 clients connected, a page's resync can be
  refused (503). The page then shows the existing "refused" state and backs off, which is
  correct but noisy.

## 17. Implementation outline: two checkpoints (only after approval)

Each checkpoint stops for the owner's review. Commits are on `gui`, and are pushed only
when the owner asks.

### Checkpoint 1: observer JSON and health safeguards, with their tests

| # | Task | Files |
|---|---|---|
| 1.1 | Tests first (§12.1): non-finite, precedence, strict encoding, state task survival, `state_encoding`, first-good and same-value push, full-state health against `GET /vehicle`, startup | `tests/unit/observe/`, `tests/unit/api/` (incl. `test_server_ws.py:63`) |
| 1.2 | `nonfinite` and sanitising in `snapshots.vehicle`; strict `state_message` | `src/ecu_simulator/observe/snapshots.py` |
| 1.3 | `run_state` as in §8.3: containment, `ok`, timestamps, the first-good publish; the counters in `stats()` | `src/ecu_simulator/observe/publisher.py` |
| 1.4 | Strict `GET /vehicle` with 500 and `vehicle_encode_failed`; the startup refusal; `state_encoding` in `/status` | `src/ecu_simulator/api/server.py`, `observe/snapshots.py` |
| 1.5 | **Overhead measurement**, reported with the checkpoint | a script under `scripts/`, results under `docs/validation/` |

**The overhead measurement (1.5):**
- **State build and encode, before and after:** a microbenchmark that calls
  `snapshots.state_message` 10,000 times on the `ice_scenario.yaml` runtime. It runs once
  at the commit before checkpoint 1 and once after, on the same host, in the same Python.
  It reports the median, p99 and max per call, and the difference.
- **The publisher's longest turn:** the M1 early check's full-`HandOff` drain
  (`scripts/gui_m1_early_check.py`), before and after, reporting `longest_turn_s`. The
  state task is not a publisher turn; this shows that nothing else moved.
- **Optional:** one rotated M2 early check (`scripts/gui_m2_early_check.py`, three rounds),
  as a **regression comparison only, not an acceptance run**. **The M2 latency `STOP`
  stays unresolved unless its acceptance criteria actually pass**, and one early-check run
  is not the M4 benchmark.

### Checkpoint 2: graph rendering and the browser checks

| # | Task | Files |
|---|---|---|
| 2.1 | Vendor uPlot 1.6.32 byte-identical; the three `FRONTEND` rows; the file tests | `src/ecu_simulator/api/static/uPlot.iife.min.js`, `uPlot.min.css`, `uPlot-LICENSE.txt`; `api/server.py`; `tests/unit/api/test_frontend_files.py` |
| 2.2 | Markup and layout: the collapsible section, the flex main column, the height cap, 390 px, the links | `index.html`, `app.css` |
| 2.3 | Page data and health: rings, ingest, breaks, restart reset, `as_of` null, invalid values, the rpm unit, malformed counting, "last known", the resync, the clear rules, the `data-*` attributes | `app.js`, `app.css` |
| 2.4 | Drawing: uPlot instances, stepped paths and gaps, scales, windows and `localStorage`, readouts, pause, hide, resize, the fallback | `app.js`, `app.css` |
| 2.5 | The fault-injection server and the capture-run checks (§12.2, §12.3), with the cost measurement (§11.3) | `scripts/gui_fault_server.py`, `scripts/gui_demo_capture.py` |
| 2.6 | Live-demo record: screenshots, measurements, and the §13 checklist | `docs/validation/gui-m3b-live-demo.md` |

**Still open after M3b:** the M2 latency `STOP` and the hosted `CAN_ISOTP` gap. M3b closes
neither, and does not change the Phase 8b gate or the V1.0 branch rule.
