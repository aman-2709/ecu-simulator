# Run 9 performance traces (M3b §11.3)

These are the two DevTools traces of run 9 of `scripts/run_gui_demo.sh --m3b`, the run of record in
[../../gui-m3b-live-demo.md](../../gui-m3b-live-demo.md). They are copied unchanged.

**Code.** They were taken on `gui` at **`f8a8e5f`**:
- the page is unchanged since `0c564c9`;
- the capture script is at `f8a8e5f`.

**Capture.** The run started at 2026-10-01T05:51:19Z. Each trace covers 60 s.
- Page: headless Chrome 151.0.7922.173, dpr 1, `--disable-gpu`.
- Data: the stepped demo, with the traffic script at 4 Hz and the 2 min window.
- Trace categories: `devtools.timeline` and `v8`.
- Format: `Tracing.start` with `transferMode: ReturnAsStream` and gzip compression.

| File | Viewport | Size | SHA-256 |
|---|---|---|---|
| `m3b-trace-1440x900.json.gz` | 1440 × 900 | 1,504,837 B | `c477e60cbe3b54413cbea28400dc8048a88abeba60b9aaa39863c5cee9874d8c` |
| `m3b-trace-390x844.json.gz` | 390 × 844 | 1,240,608 B | `533fa925b374aca69fde1e9e1a0524edb53a2c254e825aff6d85289e347c9a7d` |

## How to open them

- **Perfetto:** open https://ui.perfetto.dev and use "Open trace file" on the `.json.gz`. It reads gzip directly.
- **Chrome's DevTools:**
  1. Unzip the file first: `gunzip -k m3b-trace-1440x900.json.gz`.
  2. Open the Performance panel and use "Load profile…" on the `.json`.
- **`chrome://tracing`:** "Load" accepts the unzipped `.json`.

The page's main thread is `CrRendererMain`. In the trace:
- **The graphs' redraw** is the `FunctionCall` from `app.js` line 857, the `requestAnimationFrame` callback of `scheduleDraw`.
- **M3a's log rebuild** is the `TimerFire` / `FunctionCall` from `app.js` line 1431, the `renderLog` timer.

The capture script's `trace_summary()` reads the same events.
