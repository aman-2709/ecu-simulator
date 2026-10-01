#!/usr/bin/env python3
"""Where the observer page's main thread spends its time, from a Chrome DevTools trace.

It reads a trace written by scripts/gui_demo_capture.py (``Tracing.start`` with
``ReturnAsStream``, gzip) and, for the renderer's main thread (``CrRendererMain``), prints:

- the busy total: the summed duration of the top-level events, which are the scheduler's
  tasks (``ThreadControllerImpl::RunTask``) when the trace has the ``toplevel`` category.
  A trace without it (Task 34's run 9) has no task events; its top level is then the
  outermost timeline events, which misses any time a task spends between them;
- a breakdown of that total by self time: every complete (``X``) event on the thread is
  placed in one tree by containment, and each event is charged only the time none of its
  children covers. So nothing is counted twice, and the buckets (JavaScript including GC,
  style, layout, paint, composite, other) add up to the busy total exactly. "other" is the
  remainder, named by its largest event names (mostly the task's own self time: scheduling,
  IPC and work no timeline event describes);
- the page's own callbacks, found by their ``app.js`` line: M3a's log render (the
  ``renderLog`` timer), the graphs' redraw, the WebSocket handler and the follow-control
  frame. For the log render: its count, the summed inclusive time of each timer task, and that
  time split into the same buckets (the forced style and layout inside the call included).

The ``app.js`` lines are read from the working tree, or with ``--rev`` from a commit (the
traces of run 9 were taken at f8a8e5f, where the log timer was line 1431).

    .venv/bin/python scripts/gui_trace_breakdown.py TRACE.json.gz [...] [--rev REV] [--seconds S]
        [--json OUT] [--extract-main DIR]

Busy % is the busy total over the main thread's own span (its first top-level event's start to
its last one's end). ``--extract-main`` writes a copy of each trace with only the page's main
thread and the metadata events, which is all this script reads; a copy's summary equals the
original's.
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
APP_JS = "src/ecu_simulator/api/static/app.js"

TASK_NAMES = {"ThreadControllerImpl::RunTask", "RunTask"}
BUCKETS = ("js", "style", "layout", "paint", "composite", "other")
JS_NAMES = {"FunctionCall", "TimerFire", "FireAnimationFrame", "EventDispatch", "RunMicrotasks", "EvaluateScript",
            "FireIdleCallback", "XHRReadyStateChange", "XHRLoad", "MajorGC", "MinorGC", "ParseScriptOnBackground"}
STYLE_NAMES = {"UpdateLayoutTree", "RecalculateStyles", "ParseAuthorStyleSheet"}
LAYOUT_NAMES = {"Layout"}
PAINT_NAMES = {"Paint", "PrePaint", "PaintImage", "Decode Image", "Rasterize"}
COMPOSITE_NAMES = {"Layerize", "CompositeLayers", "UpdateLayer", "UpdateLayerTree", "Commit", "ScrollLayer"}

# The page's callbacks, by the text of the line their trace FunctionCall reports (1-based).
CALLBACKS = {
    "renderLog": "renderTimer = setTimeout(",
    "graph_draw": "G.frame = requestAnimationFrame(",
    "ws_onmessage": "ws.onmessage = function",
    "follow_frame": "view.followFrame = requestAnimationFrame(",
}


def bucket(name: str) -> str:
    if name in JS_NAMES or name.startswith(("v8.", "V8.", "BlinkGC.")):
        return "js"
    if name in STYLE_NAMES:
        return "style"
    if name in LAYOUT_NAMES:
        return "layout"
    if name in PAINT_NAMES:
        return "paint"
    if name in COMPOSITE_NAMES:
        return "composite"
    return "other"


def app_lines(rev: str | None) -> dict[str, int]:
    if rev:
        text = subprocess.run(["git", "show", f"{rev}:{APP_JS}"], cwd=ROOT, check=True, capture_output=True,
                              text=True).stdout
    else:
        text = (ROOT / APP_JS).read_text()
    lines = text.splitlines()
    return {key: next(i + 1 for i, line in enumerate(lines) if needle in line) for key, needle in CALLBACKS.items()}


class Node:
    __slots__ = ("bucket", "children", "dur", "event", "name", "parent", "self_us", "ts")

    def __init__(self, event: dict[str, Any], ts: float, dur: float) -> None:
        self.event = event
        self.name: str = event["name"]
        self.bucket = bucket(self.name)
        self.ts, self.dur = ts, dur
        self.self_us = dur
        self.parent: Node | None = None
        self.children: list[Node] = []

    @property
    def end(self) -> float:
        return self.ts + self.dur


def build_tree(events: list[dict[str, Any]]) -> list[Node]:
    """Nest one thread's complete events by containment. A child that runs past its parent's
    end (rounding, or a misnested pair) is clipped to it, so each microsecond has one owner."""
    xs = sorted((e for e in events if e.get("ph") == "X" and e.get("dur")), key=lambda e: (e["ts"], -e["dur"]))
    roots: list[Node] = []
    stack: list[Node] = []
    for e in xs:
        ts, dur = float(e["ts"]), float(e["dur"])
        while stack and ts >= stack[-1].end:
            stack.pop()
        if stack:
            parent = stack[-1]
            dur = min(dur, parent.end - ts)
            node = Node(e, ts, dur)
            node.parent = parent
            parent.children.append(node)
            parent.self_us -= dur
        else:
            node = Node(e, ts, dur)
            roots.append(node)
        stack.append(node)
    return roots


def walk(node: Node) -> list[Node]:
    out, todo = [], [node]
    while todo:
        n = todo.pop()
        out.append(n)
        todo.extend(n.children)
    return out


def split(nodes: list[Node]) -> dict[str, float]:
    parts = dict.fromkeys(BUCKETS, 0.0)
    for n in nodes:
        parts[n.bucket] += n.self_us
    return parts


def ms(us: float) -> float:
    return round(us / 1000, 1)


def main_thread(events: list[dict[str, Any]]) -> tuple[int, int]:
    """The CrRendererMain with the most complete events: the page's (an about:blank one has few)."""
    mains = {(e["pid"], e["tid"]) for e in events if e.get("ph") == "M" and e.get("name") == "thread_name"
             and e.get("args", {}).get("name") == "CrRendererMain"}
    counts = Counter((e.get("pid"), e.get("tid")) for e in events if e.get("ph") == "X")
    return max(mains, key=lambda t: counts[t])


def summarise(path: Path, lines: dict[str, int], seconds: float | None = None) -> dict[str, Any]:
    with gzip.open(path, "rt") as fh:
        data = json.load(fh)
    events: list[dict[str, Any]] = data["traceEvents"] if isinstance(data, dict) else data
    thread = main_thread(events)
    mine = [e for e in events if (e.get("pid"), e.get("tid")) == thread]
    roots = build_tree(mine)
    # The window is the main thread's own span, from its first top-level event's start to its
    # last one's end, so a task cut by the trace's start or end cannot push busy past 100 %.
    span_us = max(r.end for r in roots) - roots[0].ts
    window_s = span_us / 1e6
    has_tasks = any(r.name in TASK_NAMES for r in roots)
    busy = sum(r.dur for r in roots)
    nodes = [n for r in roots for n in walk(r)]
    parts = split(nodes)
    other_names = Counter[str]()
    for n in nodes:
        if n.bucket == "other":
            other_names[n.name] += n.self_us
    root_names = Counter[str]()
    for r in roots:
        root_names[r.name] += r.dur

    # The page's callbacks: each FunctionCall at a known app.js line, with the forced style and
    # layout inside it, charged as its enclosing v8.callFunction and TimerFire while those hold
    # no other FunctionCall: the graphs' redraw then includes uPlot's own commit callbacks, which
    # run inside the same v8.callFunction after the app.js call returns (run 9's figure of
    # 0.03 s was the FunctionCall alone). One FireAnimationFrame can run several callbacks, so it
    # is never charged to one.
    by_line: dict[int, str] = {v: k for k, v in lines.items()}
    calls: dict[str, list[Node]] = defaultdict(list)
    for n in nodes:
        if n.name != "FunctionCall":
            continue
        d = n.event.get("args", {}).get("data", {})
        if str(d.get("url", "")).endswith("/app.js") and d.get("lineNumber") in by_line:
            top = n
            while top.parent is not None and top.parent.name in ("TimerFire", "v8.callFunction") and sum(
                    c.name == "FunctionCall" for c in top.parent.children) <= 1:
                top = top.parent
            calls[by_line[d["lineNumber"]]].append(top)
    callbacks: dict[str, Any] = {}
    for key in lines:
        tops = list({id(t): t for t in calls.get(key, [])}.values())
        durs = [t.dur for t in tops]
        callbacks[key] = {
            "app_js_line": lines[key], "count": len(tops), "sum_ms": ms(sum(durs)),
            "mean_ms": round(statistics.fmean(durs) / 1000, 2) if durs else None,
            "median_ms": round(statistics.median(durs) / 1000, 2) if durs else None,
            "max_ms": round(max(durs) / 1000, 2) if durs else None,
            "split_ms": {k: ms(v) for k, v in split([n for t in tops for n in walk(t)]).items()},
        }
    frames = sum(1 for e in mine if e.get("name") == "AnimationFrame" and e.get("ph") == "b")
    paints = sum(1 for n in nodes if n.name == "Paint")
    result: dict[str, Any] = {
        "trace": path.name, "thread": list(thread), "window_s": round(window_s, 2),
        "measured_s": seconds, "top_level": "RunTask" if has_tasks else "outermost timeline event",
        "top_level_count": len(roots), "busy_ms": ms(busy), "busy_percent": round(busy / 1e4 / window_s, 1),
        "split_ms": {k: ms(v) for k, v in parts.items()},
        "split_sum_ms": ms(sum(parts.values())),
        "gc_ms": ms(sum(n.self_us for n in nodes if n.name.startswith(("V8.GC", "MajorGC", "MinorGC", "BlinkGC")))),
        "prepaint_ms": ms(sum(n.self_us for n in nodes if n.name == "PrePaint")),
        "other_top_names_ms": {k: ms(v) for k, v in other_names.most_common(6)},
        "top_level_names_ms": {k: ms(v) for k, v in root_names.most_common(4)},
        "frames": frames, "paint_events": paints, "callbacks": callbacks,
    }
    return result


def extract_main(path: Path, outdir: Path) -> Path:
    """A copy of the trace with only the page's main thread and the trace's metadata events
    (process and thread names): the events this script reads, at a fraction of the size."""
    with gzip.open(path, "rt") as fh:
        data = json.load(fh)
    events: list[dict[str, Any]] = data["traceEvents"] if isinstance(data, dict) else data
    thread = main_thread(events)
    kept = [e for e in events if e.get("ph") == "M" or (e.get("pid"), e.get("tid")) == thread]
    out = outdir / path.name
    with gzip.open(out, "wt", compresslevel=9) as fh:
        json.dump({"traceEvents": kept}, fh, separators=(",", ":"))
    return out


def table(results: list[dict[str, Any]]) -> str:
    head = ("trace", "busy s", "busy %", "js", "style", "layout", "paint", "composite", "other",
            "renderLog n", "renderLog s", "frames")
    rows = [head]
    for r in results:
        s = r["split_ms"]
        rl = r["callbacks"]["renderLog"]
        rows.append((r["trace"], f"{r['busy_ms'] / 1000:.2f}", f"{r['busy_percent']:.1f}",
                     *(f"{s[k] / 1000:.2f}" for k in BUCKETS), str(rl["count"]), f"{rl['sum_ms'] / 1000:.2f}",
                     str(r["frames"])))
    widths = [max(len(row[i]) for row in rows) for i in range(len(head))]
    return "\n".join("  ".join(c.rjust(w) if i else c.ljust(w)
                               for i, (c, w) in enumerate(zip(row, widths, strict=True))) for row in rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("traces", type=Path, nargs="+", help="gzip DevTools traces (.json.gz)")
    parser.add_argument("--rev", help="read the app.js callback lines at this commit, not the working tree")
    parser.add_argument("--seconds", type=float, help="the capture's own measured window, recorded beside the span")
    parser.add_argument("--json", type=Path, help="also write the full summaries here")
    parser.add_argument("--extract-main", type=Path, metavar="DIR",
                        help="also write each trace's main thread alone (with the metadata events) into DIR")
    args = parser.parse_args(argv)
    lines = app_lines(args.rev)
    results = [summarise(p, lines, args.seconds) for p in args.traces]
    print(table(results))
    if args.json:
        args.json.write_text(json.dumps(results, indent=1))
    if args.extract_main:
        args.extract_main.mkdir(parents=True, exist_ok=True)
        for p in args.traces:
            extract_main(p, args.extract_main)
    return 0


if __name__ == "__main__":
    sys.exit(main())
