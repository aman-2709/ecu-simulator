# Task 39 comparison traces (the page's main-thread load)

The evidence for [../gui-m3b-main-thread.md](../gui-m3b-main-thread.md): sixteen 60 s DevTools
traces from one run of `scripts/run_gui_demo.sh --m3b-perf`, its results file and its step log.

**Code.** Taken on `gui` at **`f10501d`**:
- the page (`src/ecu_simulator/api/static/`) is unchanged since `de9972c`;
- `f10501d` adds the `--m3b-perf` mode and `scripts/gui_trace_breakdown.py`, and changes nothing
  under `src/`.

**Capture.** The run started at 2026-10-01T08:16:27Z (`capture.log`).
- Page: headless Chrome 151.0.7922.173, dpr 1, `--disable-gpu`, inside a private network namespace.
- No WebSocket wrapper and no MutationObserver were injected (unlike `--m3b`): the page ran alone.
- Data: the stepped demo (`docs/examples/ice_drive_cycle_stepped.yaml`), the 2 min graph window.
  The log was first filled to its cap of 2,000 exchanges (`app.js` `MAX_ROWS`) with the traffic
  script at its 50/s maximum; every run then had the traffic script at `--rate 4` (or none, where
  the condition says so).
- Trace categories: `toplevel`, `devtools.timeline` and `v8`; `Tracing.start` with
  `transferMode: ReturnAsStream` and gzip compression.

**The traces here are the page's main thread only.** The raw traces hold every thread of every
Chrome process (61.5 MB for the sixteen; 22.2 MB here). The files here keep the `CrRendererMain` thread of the
page and the metadata events (process and thread names), written by
`scripts/gui_trace_breakdown.py --extract-main`. That thread is all the breakdown reads, and its
summary of each file here equals the summary of the raw trace taken during the run (checked: no
field differs). The copies drop the raster, compositor and GPU threads. The raw files were kept only in a session scratchpad, which is not durable, so the sizes and SHA-256 below record them but cannot be re-checked later.

| File | Size here | SHA-256 here | Raw size | Raw SHA-256 |
|---|---|---|---|---|
| `perf-1440x900-baseline-a.json.gz` | 1,663,639 B | `475b7057c5d2116c458471fcaf4b1ad318f25b76f86abccfa0cd417a94f199b0` | 5,879,468 B | `59cf25a09f149ac16a3c9237ccb58bee79aac625b553b300af923c07706e20fc` |
| `perf-1440x900-log-paused.json.gz` | 1,647,216 B | `5f9ad3a8eca33b4cf7a01aadbdb6367f7d523ea2760a9be240e6ea909fe10a85` | 5,843,694 B | `ccc047a08e108d81d0b98724d704a73a8a853d0faf0dee1f0cf2de4d3ccf7f27` |
| `perf-1440x900-graphs-hidden.json.gz` | 1,596,410 B | `588ebde9177856c9f2d00b8730641d16fed0b1a2601b837a27f2fb786779ee75` | 4,388,078 B | `a877976c529e3b5fc28e259aa2e9302ee2ed93b0fa3bb1fb2b2e0e711b37f39b` |
| `perf-1440x900-no-traffic.json.gz` | 1,358,463 B | `6a2f618253548abc309063d175b38038d5a70c7d144dcb4c3ca32695867adc47` | 4,738,905 B | `456da9de4689ffe08c428f1fecd07f96bc46eac174a18ca5bd62df29147422ab` |
| `perf-1440x900-no-traffic-reduced-motion.json.gz` | 539,461 B | `7a05ecc14689d472ad228c58a49fb8fa008f850fd05cf252ce7e37b63b4b8bfb` | 1,708,648 B | `bb155db5f293af337aa5106759e9ee89ddfa2c2c57cf99acac7dbb887e1c4d3b` |
| `perf-1440x900-baseline-b.json.gz` | 1,617,408 B | `a74b0755f852aefec1066db51379709010ebbf34dabbfca628f99d2c17c31a29` | 4,507,041 B | `45d5b4a38a00c581c2d3048a5e1e87008d878a3177a1f71f3f8c0f2dc3bb3446` |
| `perf-1440x900-log-cleared.json.gz` | 1,889,301 B | `40b0f3b60d2c9ed2277fad750d20a04d69d11a5842faac859cef5ebc9ec9c756` | 5,742,242 B | `dfa8a9b6c3baf739d094983d69b63bf61c19d9946c3b2b93f6ae77b9972bbc28` |
| `perf-1440x900-log-cleared-reduced-motion.json.gz` | 1,149,892 B | `6bab55ea2f28bb4a9a5a9ac183efe4ced286d19ee77541a8325adf778b0e0b7f` | 3,474,253 B | `21862b4629a42b328804b438374c72bb0094eb7f29204ed39555801a26873499` |
| `perf-390x844-baseline-a.json.gz` | 1,294,601 B | `79a1f0047349b422dce58400f3a510d66d2934e386fc42668ae2e72096d3dfb0` | 3,296,930 B | `399351ada7fe5f75863e3294422d34ea892d90dd7055bfd828e5f47745c140fb` |
| `perf-390x844-log-paused.json.gz` | 1,266,465 B | `66f59e5891401c5b6cc3dd8d8fa888f8a60890abdfaac8d102edb16d9a8f36fc` | 3,348,074 B | `aa281953291a5a2c8c0c325a22b5e583cf82132e4cb32e31fe4b851bcc7e8b41` |
| `perf-390x844-graphs-hidden.json.gz` | 1,250,488 B | `7542a7c15ec6da9cc27d11d0bebf744e26a46d388921d63258bc9e6532cfa369` | 3,150,168 B | `93d68d21f9483755b3e6ee16339e3f13e3c6ef7f27718ecd78561191bb3d1a92` |
| `perf-390x844-no-traffic.json.gz` | 1,498,064 B | `4ede93a1390fd22299e652b4e566974292c3dec9e793eefb8a9fb42038a8ca40` | 3,049,026 B | `0a9f4c7bdfec68c9df6838754163f83e05389edc42a7b7ba6f6a05aa41623d92` |
| `perf-390x844-no-traffic-reduced-motion.json.gz` | 624,384 B | `3a4f1b61b8d14addae5e861f81cacd677b57e36f5a220d80a16dff41c533a500` | 1,442,251 B | `e845c1870e22c7ba2f1b634a3b70de416ebcaa3a9e247a863cbf8d9dbdabe9e4` |
| `perf-390x844-baseline-b.json.gz` | 1,314,818 B | `e38fc52c214191b6387c4d462a7db4a1a304dc48845ad5298d5ad99206938fbd` | 3,405,600 B | `64dee4f86a9effce38fe7abaaf20fa6fdfac1f33c9dfa1031559668fd57e3353` |
| `perf-390x844-log-cleared.json.gz` | 2,185,370 B | `2a205fa562bc6c298ac524344bac806d96b9ee7d154140ba65d400207ca34fa4` | 4,444,569 B | `e02cc94fab93937ee269209f2af3fb9fcf576f9673d11b8d819728da024a4a64` |
| `perf-390x844-log-cleared-reduced-motion.json.gz` | 1,323,471 B | `03d6b89d746c807dc5bab9c4e2669082f3ef81637f7ff11b6fd3bf19e86ac0a5` | 3,061,387 B | `c8cd0b55c1884b99675882665ee94bd7499c8008815e24fc19ce34403690d52d` |

The runs were taken in the order listed, all of 1440 × 900 first.

## The other files

- `m3b-perf-results.json`: per run, the traffic counters, the log's state at the start and end, the
  `Performance.getMetrics` deltas, the raw trace's size and SHA-256, and the breakdown.
- `capture.log`: the run's step log.
- `session1/`: an earlier session the same day, before `f10501d`. Its **1440 × 900** runs are a
  repeat of the baseline, paused, hidden, no-traffic and cleared conditions. **Its 390 × 844 paused,
  hidden and cleared runs are invalid**: the script clicked controls below the fold, the clicks
  missed, and the page state did not match the label (`log_start` in that file shows it). `f10501d`
  scrolls each control into view and checks the page took the click. Its traces are not committed.

## Reading them

```
.venv/bin/python scripts/gui_trace_breakdown.py docs/validation/gui-m3b-perf/traces/*.json.gz
```

They also open in Perfetto (https://ui.perfetto.dev, "Open trace file", gzip is read directly)
or, unzipped, in DevTools' Performance panel. In the trace, M3a's log render is the `TimerFire` whose
`FunctionCall` is `app.js` line 1434 (`scheduleRender`'s timer); the graphs' redraw is line 857.

## After the log fix (Task 46b): one trace per width

Two more files in `traces/`, the first of two 60 s runs per width of
`scripts/run_gui_demo.sh --m3b-perf-log` on the shipped page after the log fix (Tasks 42-45).

**Code.** Taken on `gui` with the harness at **`9c7afd2`**. The page (`src/ecu_simulator/api/static/`)
is unchanged since **`3593be7`**: the served `app.js` SHA-256 was
`4c2419cae4709f962a59d3968fbfacc3e7b9e2a864a759ee498816f7d8959c9e` and the served `app.css`
`ecbea9ec609fc9f8aa2ee90914bf0fd492ff8c683dba6c2cc854b91157056917`, both equal to the files at `3593be7`.

**Capture.** The run started at 2026-10-01T23:21:25Z.
- Headless Chrome 151.0.7922.173, dpr 1, `--disable-gpu` (GPU compositing `disabled_software`, from
  `SystemInfo.getInfo`), inside a private network namespace. No WebSocket wrapper and no
  MutationObserver; the only harness code in the page is an Event Timing `PerformanceObserver`.
- Data and traffic as above: the stepped demo, the log filled to its 2,000-exchange cap at 50/s, then
  the traffic script at `--rate 4` (3.80 requests/s measured in both runs here).
- The log follows, and during each run the harness makes ten real clicks (Pause, Resume, the
  "no response" filter chip off and on, twice each; Older after a wheel scroll up; Jump to newest).
- Same trace categories and the same main-thread-only copy (`--extract-main`); each copy's summary
  equals the raw trace's (checked: no field differs).

| File | Size here | SHA-256 here | Raw size | Raw SHA-256 |
|---|---|---|---|---|
| `perf-1440x900-following-1.json.gz` | 1,044,010 B | `d50feef6caa4e87ef91e145fd0905989d7a60c90b720802181dedc21af55971c` | 4,916,275 B | `cd54e52db97455df01a7163e47cfdfb3c9b11508fec7a8a23b5d6b356547ebc2` |
| `perf-390x844-following-1.json.gz` | 792,492 B | `f775807eb92754ab740cccbb68cef61b11da63db9c386981675295603f857b91` | 2,952,017 B | `97897f83ccedf160400e3b0e2a7a637abfa5f4e68395d3dbc1f721ecb43748d2` |

Their summaries: busy 10.0 % and 6.4 %; `renderLog` 228 calls, 0.74 s and 0.37 s; no task over 50 ms.
In these traces the log render is the `TimerFire` whose `FunctionCall` is `app.js` line 1633, so read
them with `--rev 3593be7`:

```
.venv/bin/python scripts/gui_trace_breakdown.py --rev 3593be7 \
    docs/validation/gui-m3b-perf/traces/perf-1440x900-following-1.json.gz \
    docs/validation/gui-m3b-perf/traces/perf-390x844-following-1.json.gz
```

As for the files above, the raw traces were kept only in a session scratchpad; the second runs, the
traced paused / pinned / graphs-hidden runs and the visible-browser runs are recorded as numbers only.
