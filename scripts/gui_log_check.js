#!/usr/bin/env node
// The exchange log's selection block (app.js, "log selection (pure, no DOM)"), checked in node.
//
// Reads app.js as text, takes the marked block out, runs it in an empty vm context (no DOM,
// no page state: a stray reference throws) and checks the window, the absent-row counts and
// the movement helpers against the plan's Task 42 list, plus a seeded randomized comparison
// with a brute-force reference written here. Prints one line per check; "LOGCHECK PASS" and
// exit 0 when all pass, the first failure and exit 1 otherwise. No dependencies.
"use strict";

var fs = require("fs");
var path = require("path");
var vm = require("vm");

var APP_JS = path.join(__dirname, "..", "src", "ecu_simulator", "api", "static", "app.js");
var START = "// ---- log selection (pure, no DOM) ----";
var END = "// ---- end log selection ----";

function fail(msg) {
  console.log("LOGCHECK FAIL: " + msg);
  process.exit(1);
}

var text = fs.readFileSync(APP_JS, "utf8");
var a = text.indexOf(START), b = text.indexOf(END);
if (a < 0 || b < 0 || b < a) fail("the marked block is not in " + APP_JS);
if (text.indexOf(START, a + 1) >= 0 || text.indexOf(END, b + 1) >= 0) fail("the block markers appear more than once");
var block = text.slice(a, b + END.length);

var ctx = vm.createContext({});
vm.runInContext('"use strict";\n' + block, ctx, { filename: "app.js#log-selection" });
var L = ctx;
["selectLog", "logOlderEnd", "logNewerEnd", "logAnchorEnd"].forEach(function (f) {
  if (typeof L[f] !== "function") fail("the block does not define " + f);
});

var passed = 0;
function check(name, fn) {
  try { fn(); } catch (err) { fail(name + ": " + (err && err.message ? err.message : String(err))); }
  passed += 1;
  console.log("ok " + passed + " - " + name);
}
function eq(actual, expected, what) {
  var x = JSON.stringify(actual), y = JSON.stringify(expected);
  if (x !== y) throw new Error((what || "value") + ": expected " + y + ", got " + x);
}
function ok(cond, what) { if (!cond) throw new Error(what); }

// ---- fixtures ----
// A list from a token string: x = exchange the filter shows, h = exchange it hides,
// g = gap marker, n = note (a connection / history note). Ids run from `first` (default 1).
function build(spec, first) {
  var id = first || 1, out = [];
  spec.split(/\s+/).filter(Boolean).forEach(function (t) {
    if (t === "x") out.push({ kind: "ex", e: { ok: true }, id: id });
    else if (t === "h") out.push({ kind: "ex", e: { ok: false }, id: id });
    else if (t === "g") out.push({ kind: "gap", id: id });
    else if (t === "n") out.push({ kind: "note", id: id });
    else throw new Error("bad token " + t);
    id += 1;
  });
  return out;
}
function shows(e) { return e.ok; }
function opts(o) {
  var r = { clearedAfter: 0, pauseAfter: null, endId: null, size: 3 };
  Object.keys(o || {}).forEach(function (k) { r[k] = o[k]; });
  return r;
}
// Items as a compact string: x7 = exchange id 7, g3 / n3 = markers, [h2] = 2 hidden by filters.
function str(sel) {
  return sel.items.map(function (it) {
    if (it.kind === "hidden") return "[h" + it.n + "]";
    var en = it.entry;
    return (en.kind === "ex" ? "x" : en.kind === "gap" ? "g" : "n") + en.id;
  }).join(" ");
}
function sel(spec, o, matches) { return L.selectLog(build(spec), matches || shows, opts(o)); }
function shownExchanges(s) { return s.items.filter(function (it) { return it.kind === "entry" && it.entry.kind === "ex"; }).length; }
function hiddenInside(s) { return s.items.reduce(function (t, it) { return t + (it.kind === "hidden" ? it.n : 0); }, 0); }
function addUp(s) {
  var c = s.counts;
  eq(c.matching, c.olderMatching + shownExchanges(s) + c.newerMatching, "matching = older + shown + newer");
  eq(c.hiddenTotal, hiddenInside(s) + c.hiddenOutside.older + c.hiddenOutside.newer, "hidden = inside + outside");
  eq(c.inView, c.matching + c.hiddenTotal, "inView = matching + hidden");
  eq(c.shown, shownExchanges(s), "shown");
}

// ---- the plan's Task 42 checks ----
check("constants: a window of 200, a step of 100", function () {
  eq(L.LOG_WINDOW, 200, "LOG_WINDOW");
  eq(L.LOG_STEP, 100, "LOG_STEP");
});

check("the block names no DOM or page state", function () {
  var body = block.replace(/\/\/[^\n]*/g, "");
  var banned = /\b(document|window|localStorage|setTimeout|requestAnimationFrame|rowCache|passes|el)\b|\$\(|(^|[^\w$.])(S|view)\./;
  var m = body.match(banned);
  ok(!m, "the block uses " + (m && m[0]));
});

check("window size and order: the newest `size` matching, oldest first", function () {
  var s = sel("x x x x x", { size: 3 });
  eq(str(s), "x3 x4 x5", "items");
  eq([s.firstId, s.lastId], [3, 5], "first / last");
  eq([s.counts.olderMatching, s.counts.newerMatching, s.counts.matching, s.counts.inView], [2, 0, 5, 5], "counts");
  addUp(s);
});

check("filters apply to all retained rows before windowing", function () {
  // The only matching exchanges are old: they still fill the window.
  var s = sel("x x h h h h h h h h", { size: 3 });
  eq(str(s), "x1 x2", "items");
  eq([s.counts.matching, s.counts.hiddenTotal, s.counts.hiddenOutside.newer], [2, 8, 8], "counts");
  // A window of 2 over x h x h x h: the window counts matching exchanges, not rows.
  s = sel("x h x h x h", { size: 2 });
  eq(str(s), "x3 [h1] x5", "items, size 2");
  eq([s.counts.olderMatching, s.counts.hiddenOutside.older, s.counts.hiddenOutside.newer], [1, 1, 1], "outside");
  addUp(s);
});

check("hidden-by-filter runs appear only between shown rows", function () {
  var s = sel("h h x h h x h", { size: 5 });
  eq(str(s), "x3 [h2] x6", "items");
  eq([s.counts.hiddenOutside.older, s.counts.hiddenOutside.newer], [2, 1], "outside");
  // A marker is a shown row: it splits a run, and a run between it and an exchange is shown.
  s = sel("x h g h h x", { size: 5 });
  eq(str(s), "x1 [h1] g3 [h2] x6", "split by a marker");
  addUp(s);
});

check("markers inside the window keep their place; outside ones are only counted", function () {
  var s = sel("x g x n x x g x x", { size: 3 });
  // window: x6 x8 x9 (the three newest matching); the gap at 7 lies inside.
  eq(str(s), "x6 g7 x8 x9", "items");
  eq(s.counts.markersOlder, { gaps: 1, notes: 1 }, "markers older");
  eq(s.counts.markersNewer, { gaps: 0, notes: 0 }, "markers newer");
  // Pinned at x5: the gap at 7 is newer than the window.
  s = sel("x g x n x x g x x", { size: 2, endId: 5 });
  eq(str(s), "x3 n4 x5", "pinned items");
  eq(s.counts.markersOlder, { gaps: 1, notes: 0 }, "pinned markers older");
  eq(s.counts.markersNewer, { gaps: 1, notes: 0 }, "pinned markers newer");
  addUp(s);
});

check("at the list's ends the window takes the markers beyond its outer exchanges", function () {
  // Nothing older matches: leading markers are shown; nothing newer (following): trailing too.
  var s = sel("n g x x n", { size: 5 });
  eq(str(s), "n1 g2 x3 x4 n5", "items");
  eq(s.counts.markersOlder, { gaps: 0, notes: 0 }, "older");
  eq(s.counts.markersNewer, { gaps: 0, notes: 0 }, "newer");
  // A trailing hidden run after the last shown row is outside, not a row.
  s = sel("x n h h", { size: 5 });
  eq(str(s), "x1 n2", "trailing hidden");
  eq(s.counts.hiddenOutside.newer, 2, "trailing hidden counted");
});

check("the clear boundary: cleared rows are neither shown nor counted", function () {
  var s = sel("x g h x x n x", { clearedAfter: 3, size: 5 });
  eq(str(s), "x4 x5 n6 x7", "items");
  eq([s.counts.inView, s.counts.matching, s.counts.hiddenTotal], [3, 3, 0], "counts");
  eq(s.counts.markersOlder, { gaps: 0, notes: 0 }, "cleared markers not counted");
  addUp(s);
});

check("the pause boundary: later rows are held, not shown or counted as newer", function () {
  var s = sel("x x g x h x x n", { pauseAfter: 4, size: 5 });
  eq(str(s), "x1 x2 g3 x4", "items");
  eq([s.counts.held, s.counts.newerMatching, s.counts.inView], [3, 0, 3], "counts");
  eq(s.counts.markersNewer, { gaps: 0, notes: 0 }, "held markers not counted");
  // Clear then pause at the same id (the page's "Clear view"): an empty view, arrivals held.
  s = sel("x x x x", { clearedAfter: 2, pauseAfter: 2, size: 5 });
  eq([str(s), s.counts.held, s.counts.inView], ["", 2, 0], "cleared and paused");
  addUp(s);
});

check("a pinned window does not move as entries are appended", function () {
  var list = build("x h x g x x");
  var o = opts({ endId: 5, size: 2 });
  var before = L.selectLog(list, shows, o);
  var more = list.concat(build("x n x h g x x", 7));
  var after = L.selectLog(more, shows, o);
  eq(str(after), str(before), "items");
  eq([after.firstId, after.lastId], [before.firstId, before.lastId], "first / last");
  eq(after.counts.newerMatching, before.counts.newerMatching + 4, "arrivals counted as newer");
  eq(after.counts.markersNewer, { gaps: 1, notes: 1 }, "arrived markers counted");
  addUp(after);
});

check("a following window slides as entries arrive", function () {
  var list = build("x x x x");
  var s = L.selectLog(list, shows, opts({ size: 3 }));
  eq(str(s), "x2 x3 x4", "before");
  s = L.selectLog(list.concat(build("h x g", 5)), shows, opts({ size: 3 }));
  eq(str(s), "x3 x4 [h1] x6 g7", "after");
  eq(s.counts.olderMatching, 2, "older");
});

check("Older / Newer step by matching exchanges; reaching the newest resumes following", function () {
  var list = build("x x x x x x x x x x"), o = opts({ size: 3 });
  var e = L.logOlderEnd(list, shows, o, 2);
  eq(e, 8, "older from following pins two back");
  eq(str(L.selectLog(list, shows, opts({ size: 3, endId: e }))), "x6 x7 x8", "window");
  eq(L.logOlderEnd(list, shows, opts({ size: 3, endId: 8 }), 2), 6, "older again");
  eq(L.logNewerEnd(list, shows, opts({ size: 3, endId: 6 }), 2), 8, "newer");
  eq(L.logNewerEnd(list, shows, opts({ size: 3, endId: 8 }), 2), null, "newer reaching the newest follows");
  eq(L.logNewerEnd(list, shows, opts({ size: 3, endId: 8 }), 5), null, "newer past the newest follows");
  eq(L.logNewerEnd(list, shows, opts({ size: 3 }), 2), null, "newer while following stays following");
  // Steps count matching exchanges, skipping hidden ones and markers.
  list = build("x h x g x h x x");
  eq(L.logOlderEnd(list, shows, opts({ size: 1, endId: 8 }), 2), 5, "older skips hidden and markers");
  eq(L.logNewerEnd(list, shows, opts({ size: 1, endId: 1 }), 2), 5, "newer skips hidden and markers");
});

check("Older clamps at the oldest full window and does nothing when nothing older matches", function () {
  var list = build("x x x x x x");
  eq(L.logOlderEnd(list, shows, opts({ size: 3, endId: 5 }), 100), 3, "clamped to the oldest full window");
  eq(L.logOlderEnd(list, shows, opts({ size: 3, endId: 3 }), 100), 3, "already oldest: unchanged");
  eq(L.logOlderEnd(build("x x"), shows, opts({ size: 3 }), 100), null, "all in view while following: unchanged");
  eq(L.logOlderEnd([], shows, opts({ size: 3 }), 100), null, "empty");
});

check("the filter-change anchor: nearest matching at or before the pin, else after", function () {
  var list = build("x h h x h h x");
  var odd = function (e) { return !e.ok; };       // the new filter shows the h rows
  eq(L.logAnchorEnd(list, odd, opts({ endId: 4 })), 3, "at or before the pin");
  eq(L.logAnchorEnd(list, shows, opts({ endId: 6 })), 4, "the old filter's rows");
  eq(L.logAnchorEnd(list, odd, opts({ endId: 1 })), 2, "none before: the nearest after");
  eq(L.logAnchorEnd(list, function () { return false; }, opts({ endId: 4 })), null, "nothing matches: following");
  eq(L.logAnchorEnd(list, odd, opts({ endId: null })), null, "following stays following");
  eq(L.logAnchorEnd(list, odd, opts({ endId: 6, clearedAfter: 5, pauseAfter: null })), 6, "the clear boundary applies");
});

check("a pin older than every matching exchange: an empty window, then the anchor re-pins", function () {
  // The pinned rows left the page's cap: the pin is kept as given, every row counted newer,
  // and logAnchorEnd moves the pin to the oldest matching exchange.
  var list = build("h x x x x", 10);
  var s = L.selectLog(list, shows, opts({ size: 2, endId: 4 }));
  eq([str(s), s.firstId, s.lastId], ["", null, null], "empty window");
  eq([s.counts.olderMatching, s.counts.newerMatching, s.counts.hiddenOutside.newer], [0, 4, 1], "counts");
  addUp(s);
  var e = L.logAnchorEnd(list, shows, opts({ endId: 4 }));
  eq(e, 11, "re-pinned at the oldest matching");
  eq(str(L.selectLog(list, shows, opts({ size: 2, endId: e }))), "x11", "re-pinned window");
});

check("empty and tiny lists", function () {
  var s = L.selectLog([], shows, opts());
  eq([str(s), s.firstId, s.lastId], ["", null, null], "empty");
  eq(s.counts, {
    inView: 0, matching: 0, shown: 0, olderMatching: 0, newerMatching: 0, hiddenTotal: 0,
    hiddenOutside: { older: 0, newer: 0 }, markersOlder: { gaps: 0, notes: 0 }, markersNewer: { gaps: 0, notes: 0 }, held: 0
  }, "empty counts");
  eq(str(sel("x")), "x1", "one exchange");
  eq(str(sel("n")), "n1", "one note");
  eq(str(sel("h")), "", "one hidden");
  eq(sel("h").counts.hiddenOutside.older, 1, "a lone hidden run counts as older");
  eq(str(sel("x x", { size: 1 })), "x2", "size 1");
});

check("all hidden: no exchange shown, markers kept, every hidden one counted", function () {
  var s = sel("h h g h n h", { size: 3 });
  eq(str(s), "g3 [h1] n5", "items");
  eq([s.firstId, s.lastId, s.counts.matching, s.counts.hiddenTotal], [null, null, 0, 4], "counts");
  eq([s.counts.hiddenOutside.older, s.counts.hiddenOutside.newer], [2, 1], "outside");
  addUp(s);
});

check("the 2,000-entry case at the real window", function () {
  var spec = [];
  for (var i = 0; i < 2000; i += 1) {
    spec.push(i % 3 === 2 ? "h" : "x");
    if (i % 250 === 125) spec.push(i < 1500 ? "g" : "n");
  }
  var list = build(spec.join(" "));
  var s = L.selectLog(list, shows, opts({ size: L.LOG_WINDOW }));
  eq([s.counts.inView, s.counts.matching, s.counts.shown], [2000, 1334, 200], "counts");
  eq([s.counts.olderMatching, s.counts.newerMatching], [1134, 0], "older / newer");
  eq(s.counts.markersOlder, { gaps: 6, notes: 1 }, "markers older");
  ok(s.items.length < 2000 / 4, "items bounded by the window: " + s.items.length);
  addUp(s);
  var e = L.logOlderEnd(list, shows, opts({ size: L.LOG_WINDOW }), L.LOG_STEP);
  s = L.selectLog(list, shows, opts({ size: L.LOG_WINDOW, endId: e }));
  eq([s.counts.olderMatching, s.counts.newerMatching], [1034, 100], "one step older");
  addUp(s);
  var t0 = Date.now();
  for (var k = 0; k < 200; k += 1) L.selectLog(list, shows, opts({ size: L.LOG_WINDOW }));
  console.log("   (200 selections over 2,000 entries: " + (Date.now() - t0) + " ms)");
});

check("the counts add up (matching = older + shown + newer)", function () {
  ["x h g x x n h x", "h", "", "g g", "x x x x x x x x"].forEach(function (spec) {
    [null, 1, 3, 5].forEach(function (endId) {
      addUp(sel(spec, { size: 2, endId: endId }));
      addUp(sel(spec, { size: 2, endId: endId, pauseAfter: 4, clearedAfter: 1 }));
    });
  });
});

// ---- the brute-force reference ----
// Built from the definitions, by filtering and slicing whole lists (no single pass, no
// running state): the visible rows; the matching exchanges among them; the window's
// exchanges as a slice of those; the id range the window spans (open at either end where
// nothing matching lies beyond it); the shown rows as the matching exchanges and markers in
// that range; a hidden run as the hidden exchanges between two adjacent shown rows; every
// other hidden exchange or marker classed older or newer by id.
function reference(entries, matches, o) {
  var held = 0, vis = entries.filter(function (en) {
    if (en.id <= o.clearedAfter) return false;
    if (o.pauseAfter != null && en.id > o.pauseAfter) { if (en.kind === "ex") held += 1; return false; }
    return true;
  });
  var isEx = function (en) { return en.kind === "ex"; };
  var isMatch = function (en) { return isEx(en) && !!matches(en.e); };
  var M = vis.filter(isMatch);
  var end = o.endId == null ? M.length : M.filter(function (en) { return en.id <= o.endId; }).length;
  var upper = o.endId == null ? Infinity : o.endId;
  var W = M.slice(Math.max(0, end - o.size), end);
  var older = end - W.length;
  var lower = older > 0 ? W[0].id : -Infinity;
  var inRange = function (en) { return en.id >= lower && en.id <= upper; };
  var rows = vis.filter(function (en) { return inRange(en) && (!isEx(en) || isMatch(en)); });
  var items = [];
  rows.forEach(function (r, i) {
    if (i > 0) {
      var prev = rows[i - 1];
      var n = vis.filter(function (en) { return isEx(en) && !isMatch(en) && en.id > prev.id && en.id < r.id; }).length;
      if (n) items.push({ kind: "hidden", n: n });
    }
    items.push({ kind: "entry", entry: r });
  });
  var firstRow = rows.length ? rows[0].id : Infinity;       // no rows: every hidden one in range is older
  var lastRow = rows.length ? rows[rows.length - 1].id : Infinity;
  var hidden = vis.filter(function (en) { return isEx(en) && !isMatch(en); });
  var markers = vis.filter(function (en) { return !isEx(en); });
  function marks(list) {
    return { gaps: list.filter(function (en) { return en.kind === "gap"; }).length,
      notes: list.filter(function (en) { return en.kind !== "gap"; }).length };
  }
  return {
    items: items,
    firstId: W.length ? W[0].id : null,
    lastId: W.length ? W[W.length - 1].id : null,
    counts: {
      inView: vis.filter(isEx).length,
      matching: M.length,
      shown: W.length,
      olderMatching: older,
      newerMatching: M.length - end,
      hiddenTotal: hidden.length,
      hiddenOutside: {
        older: hidden.filter(function (en) { return en.id < lower || (en.id <= upper && en.id < firstRow); }).length,
        newer: hidden.filter(function (en) { return en.id > upper || (en.id >= lower && rows.length && en.id > lastRow); }).length
      },
      markersOlder: marks(markers.filter(function (en) { return en.id < lower; })),
      markersNewer: marks(markers.filter(function (en) { return en.id > upper; })),
      held: held
    }
  };
}

// ---- the randomized comparison (seeded: the same cases every run) ----
function rng(seed) {
  var s = seed >>> 0;
  return function () {           // mulberry32
    s = (s + 0x6D2B79F5) >>> 0;
    var t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
var rand = rng(0x42);
function int(n) { return Math.floor(rand() * n); }
function randomList(n, first) {
  var out = [], id = first;
  for (var i = 0; i < n; i += 1) {
    var r = rand();
    if (r < 0.08) out.push({ kind: "gap", id: id });
    else if (r < 0.14) out.push({ kind: rand() < 0.5 ? "note" : "link", id: id });
    else out.push({ kind: "ex", e: { k: int(4) }, id: id });
    id += 1;
  }
  return out;
}
function normal(s) {             // a selection as plain data, for comparison
  return JSON.stringify({ items: s.items.map(function (it) { return it.kind === "hidden" ? { h: it.n } : { id: it.entry.id }; }),
    firstId: s.firstId, lastId: s.lastId, counts: s.counts });
}

check("randomized: matches the brute-force reference, and the invariants hold (seeded, 4,000 cases)", function () {
  var roundTrips = 0;
  for (var c = 0; c < 4000; c += 1) {
    var first = 1 + int(5), list = randomList(int(40), first);
    var lastId = first + list.length - 1;
    var keep = int(4);
    var matches = function (e) { return e.k !== keep && e.k !== (keep + 1) % 4; };
    if (c % 7 === 0) matches = function () { return false; };
    if (c % 11 === 0) matches = function () { return true; };
    var exIds = list.filter(function (en) { return en.kind === "ex" && matches(en.e); }).map(function (en) { return en.id; });
    var o = {
      clearedAfter: rand() < 0.3 ? first + int(list.length + 1) - 1 : 0,
      pauseAfter: rand() < 0.3 ? first + int(list.length + 1) - 1 : null,
      endId: null,
      size: 1 + int(8)
    };
    var r = rand();
    if (r < 0.3 && list.length) o.endId = first + int(list.length);
    else if (r < 0.6 && exIds.length) o.endId = exIds[int(exIds.length)];
    var tag = "case " + c + " " + JSON.stringify(o);

    var s = L.selectLog(list, matches, o);
    eq(normal(s), normal(reference(list, matches, o)), tag + " vs reference");
    addUp(s);
    // Item order is entry order; hidden runs never lead, trail or touch.
    var prev = -Infinity;
    s.items.forEach(function (it, i) {
      if (it.kind === "hidden") {
        ok(i > 0 && i < s.items.length - 1, tag + ": a hidden run at an edge");
        ok(s.items[i - 1].kind === "entry" && s.items[i + 1].kind === "entry", tag + ": adjacent hidden runs");
        ok(it.n > 0, tag + ": an empty hidden run");
        return;
      }
      ok(it.entry.id > prev, tag + ": items out of order");
      prev = it.entry.id;
    });
    ok(shownExchanges(s) <= o.size, tag + ": more than size exchanges");

    // A pinned window does not change as entries are appended after its end.
    if (o.endId != null) {
      var more = list.concat(randomList(1 + int(10), lastId + 1));
      eq(normal({ items: L.selectLog(more, matches, o).items, firstId: 0, lastId: 0, counts: 0 }),
        normal({ items: s.items, firstId: 0, lastId: 0, counts: 0 }), tag + ": pinned window moved on append");
    }

    // Older then Newer returns to the same window when Older moved by a whole step.
    var step = 1 + int(5);
    if (o.endId == null || exIds.indexOf(o.endId) >= 0) {
      var o1 = L.logOlderEnd(list, matches, o, step);
      var s1 = L.selectLog(list, matches, { clearedAfter: o.clearedAfter, pauseAfter: o.pauseAfter, endId: o1, size: o.size });
      ok(s1.counts.olderMatching <= s.counts.olderMatching, tag + ": Older moved newer");
      if (s1.counts.olderMatching === s.counts.olderMatching - step) {
        var o2 = L.logNewerEnd(list, matches, { clearedAfter: o.clearedAfter, pauseAfter: o.pauseAfter, endId: o1, size: o.size }, step);
        var s2 = L.selectLog(list, matches, { clearedAfter: o.clearedAfter, pauseAfter: o.pauseAfter, endId: o2, size: o.size });
        eq([s2.firstId, s2.lastId], [s.firstId, s.lastId], tag + ": Older then Newer");
        // The same rows too, unless Newer resumed following from a pin at the newest.
        if ((o2 == null) === (o.endId == null)) eq(normal(s2), normal(s), tag + ": Older then Newer rows");
        roundTrips += 1;
      }
    }

    // The filter-change anchor is a matching exchange at or before the pin, else the first after.
    if (o.endId != null) {
      var visM = list.filter(function (en) {
        return en.kind === "ex" && matches(en.e) && en.id > o.clearedAfter && (o.pauseAfter == null || en.id <= o.pauseAfter);
      }).map(function (en) { return en.id; });
      var before = visM.filter(function (id) { return id <= o.endId; });
      var want = before.length ? before[before.length - 1] : visM.length ? visM[0] : null;
      eq(L.logAnchorEnd(list, matches, o), want, tag + ": anchor");
    }
  }
  ok(roundTrips > 500, "too few Older / Newer round trips exercised: " + roundTrips);
});

console.log("LOGCHECK PASS (" + passed + " checks)");
