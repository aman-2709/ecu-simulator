/* ecu-simulator observer page (decisions/0010 §7).
 *
 * Uses only the running simulator's read-only API, on the page's own origin: GET
 * /api/v1/status, /vehicle, /dtcs, /ecus, and the WebSocket /api/v1/events. It sends GETs
 * and the WebSocket upgrade, and never sends a message on the socket. Every description
 * comes from the API (`summary`); the only protocol knowledge here is reading a request's
 * first byte for the service filter. The DOM is built with createElement and textContent.
 */
(function () {
  "use strict";

  // ---------- fixed values, stated in the page ----------
  var API = "/api/v1";
  var STATUS_POLL_MS = 2000;         // GET /status while live: the socket carries no status message
  var BACKOFF_START_MS = 1000;       // reconnect delay doubles from here...
  var BACKOFF_CAP_MS = 15000;        // ...to this cap; a refused connection waits the cap
  var STABLE_MS = 30000;             // after a 1008/1011/1013 close, only a connection open this long resets the backoff
  var ESCALATING_CLOSES = [1008, 1011, 1013];   // the simulator closed this page: keep backing off
  var FETCH_TIMEOUT_MS = 5000;
  var MAX_ROWS = 2000;               // exchange rows this page keeps; older ones leave the view
  var RENDER_MIN_MS = 200;           // the log re-renders at most this often
  var HEX_PREVIEW_BYTES = 6;
  var OUTCOMES = ["responded", "no_response", "unrouted", "error"];
  var OUTCOME_LABEL = { responded: "responded", no_response: "no response", unrouted: "unrouted", error: "error" };
  var CLOSE_REASON = {
    1001: "the simulator is shutting down (1001)",
    1006: "the connection was lost without a close frame (1006)",
    1008: "the simulator closed it for a policy violation (1008)",
    1011: "the simulator hit an internal error on this connection (1011)",
    1013: "the simulator disconnected this page for reading too slowly (1013 client too slow)"
  };
  var HTTP_REASON = {
    403: "refused by the Origin check (403)",
    421: "this Host is not allowed (421)",
    503: "the simulator is unavailable (503)"
  };

  // Presentation only: units from the bundled profiles' comments. The API sends values
  // without units; a path missing here is shown without one.
  var UNITS = {
    "vehicle.speed": "km/h", "vehicle.ambient_temp": "°C", "vehicle.battery_voltage": "V",
    "vehicle.obd_standard": "code", "engine.coolant_temp": "°C", "engine.intake_temp": "°C",
    "engine.engine_load": "%", "engine.throttle": "%", "engine.maf": "g/s", "engine.map": "kPa",
    "engine.timing_advance": "° BTDC", "engine.short_fuel_trim": "%", "engine.long_fuel_trim": "%",
    "engine.runtime": "s", "engine.fuel_level": "%", "engine.fuel_type": "code"
  };

  // ---------- state ----------
  var S = {
    phase: "loading",        // loading | live | down | refused
    reason: null,            // why the latest attempt failed
    cause: null,             // why the page stopped being live
    lastLive: null,          // ms: when data last arrived from a live connection
    downAt: null,            // ms: when the page stopped being live
    attempt: 0, retryAt: null, retryTimer: null, pollTimer: null,
    ws: null, wsOpenedAt: null, hello: null, lastClose: null,
    runStartedAt: null,
    status: null, vehicle: null, dtcs: null, ecus: null,
    dropped: null,           // this connection's latest `dropped` message
    lastSeq: null,           // highest seq received, over every event, filtered or not
    entries: [], nextId: 1, exCount: 0, trimmed: 0, duplicates: 0,
    counts: { ecu: {}, service: {}, outcome: {} }
  };
  var view = {
    ecu: "all", service: "all", outcomes: OUTCOMES.slice(),
    paused: false, pauseAfter: 0, clearedAfter: 0, expanded: {}
  };
  var rowCache = new Map();
  var renderTimer = null, lastRender = 0;
  var vehicleCells = null, vehicleKey = null, dtcKey = null;

  // ---------- helpers ----------
  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v == null || v === false) return;
        if (k === "text") n.textContent = v;
        else if (k === "cls") n.className = v;
        else n.setAttribute(k, v === true ? "" : v);
      });
    }
    (children || []).forEach(function (c) {
      if (c == null) return;
      n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return n;
  }
  function utc(ms) { return new Date(ms).toISOString().slice(11, 19) + " UTC"; }
  function ago(ms) {
    var s = Math.max(0, Math.round((Date.now() - ms) / 1000));
    if (s < 90) return s + " s ago";
    if (s < 5400) return Math.round(s / 60) + " min ago";
    return Math.round(s / 3600) + " h ago";
  }
  function fmtSeconds(s) {
    if (s < 600) return s.toFixed(1) + " s";
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
    return (h ? h + " h " : "") + m + " min " + sec + " s";
  }
  function fmtValue(v) {
    if (typeof v === "number" && !Number.isInteger(v)) return String(Math.round(v * 100) / 100);
    return String(v);
  }
  function hexBytes(h) { return h ? h.match(/../g) : []; }
  function canId(s) { return s ? s.replace(/^0x/, "").toUpperCase() : "—"; }
  function serviceOf(e) { return typeof e.request === "string" && e.request.length >= 2 ? e.request.slice(0, 2).toLowerCase() : "?"; }
  function ecuKey(e) { return e.ecu === undefined ? "?" : e.ecu === null ? "none" : String(e.ecu); }
  function isLive() { return S.phase === "live"; }
  function isStale() { return S.phase === "down" || S.phase === "refused"; }

  function HttpError(status, text) { this.status = status; this.text = text; }
  function getJSON(path) {
    var ctl = new AbortController();
    var timer = setTimeout(function () { ctl.abort(); }, FETCH_TIMEOUT_MS);
    return fetch(API + path, { method: "GET", cache: "no-store", signal: ctl.signal, credentials: "omit" })
      .then(function (r) {
        if (!r.ok) return r.text().then(function (t) { throw new HttpError(r.status, t); });
        return r.json();
      })
      .finally(function () { clearTimeout(timer); });
  }
  function fetchReason(err, what) {
    if (err instanceof HttpError) return what + ": " + (HTTP_REASON[err.status] || "HTTP " + err.status);
    if (err && err.name === "AbortError") return what + ": no answer within " + FETCH_TIMEOUT_MS / 1000 + " s";
    return what + ": no answer from the simulator (it may have stopped)";
  }

  // ---------- connection ----------
  function connect() {
    clearTimeout(S.retryTimer);
    S.retryTimer = null; S.retryAt = null;
    renderLink();
    getJSON("/status").then(function (status) {
      var restarted = S.runStartedAt != null && status.started_at !== S.runStartedAt;
      if (restarted) {
        addMark("link", "Simulator restarted.", "A new run started at " + utc(status.started_at * 1000) +
          ". Rows above are from the previous run; seq starts again at 1.");
        S.lastSeq = null; S.dropped = null;
      }
      S.runStartedAt = status.started_at;
      var first = S.vehicle == null || restarted;
      // While stale, the views keep their data as of the drop; a fresh status waits for the hello.
      if (first || !isStale()) S.status = status;
      return (first ? Promise.all([getJSON("/vehicle"), getJSON("/dtcs"), getJSON("/ecus")]) : Promise.resolve(null))
        .then(function (initial) {
          if (initial) {
            if (!isStale()) S.lastLive = Date.now();
            S.ecus = initial[2];
            applyState(initial[0], initial[1]);
          }
          openSocket(status.api.refused_clients);
        });
    }).catch(function (err) {
      fail(fetchReason(err, "status request failed"), err instanceof HttpError && (err.status === 421 || err.status === 403));
    });
  }

  function openSocket(refusedBefore) {
    var scheme = location.protocol === "https:" ? "wss:" : "ws:";
    var url = scheme + "//" + location.host + API + "/events" + (S.lastSeq != null ? "?after=" + S.lastSeq : "");
    var ws = new WebSocket(url);
    S.ws = ws; S.wsOpenedAt = null; S.hello = null; S.dropped = null;
    ws.onopen = function () { if (S.ws === ws) S.wsOpenedAt = Date.now(); };
    ws.onmessage = function (ev) { if (S.ws === ws) onMessage(ev.data); };
    ws.onclose = function (ev) {
      if (S.ws !== ws) return;
      S.ws = null;
      if (S.wsOpenedAt == null) { diagnoseRefusal(refusedBefore); return; }
      S.lastClose = ev.code;
      if (Date.now() - S.wsOpenedAt >= STABLE_MS) S.attempt = 0;
      fail(CLOSE_REASON[ev.code] || "the socket closed (" + ev.code + (ev.reason ? " " + ev.reason : "") + ")", false);
    };
  }

  // The browser does not expose the HTTP status of a refused upgrade, so ask /status whether
  // the simulator counted this attempt as a refused client (503 too many clients).
  function diagnoseRefusal(refusedBefore) {
    getJSON("/status").then(function (status) {
      if (status.api.refused_clients > refusedBefore) {
        fail("refused: too many clients (503). The simulator serves at most 4 live pages or tools at once; " +
          status.api.clients + " are connected now. Close another observer to free a place.", true);
      } else {
        fail("the WebSocket upgrade was refused before it opened, and the simulator did not count it as too " +
          "many clients. The likely cause is the Origin check (403): the page's origin must match the Host " +
          "it was loaded from.", true);
      }
    }).catch(function (err) { fail(fetchReason(err, "status request failed"), false); });
  }

  function fail(reason, refused) {
    if (S.ws) { var ws = S.ws; S.ws = null; try { ws.close(); } catch (e) { /* already closing */ } }
    clearTimeout(S.pollTimer); S.pollTimer = null;
    if (!isStale()) { S.downAt = S.lastLive; S.cause = reason; }
    S.phase = refused ? "refused" : "down";
    S.reason = reason;
    var delay = refused ? BACKOFF_CAP_MS : Math.min(BACKOFF_START_MS * Math.pow(2, S.attempt), BACKOFF_CAP_MS);
    S.attempt += 1;
    S.retryAt = Date.now() + delay;
    clearTimeout(S.retryTimer);
    S.retryTimer = setTimeout(connect, delay);
    renderAll();
  }

  function poll(delay) {
    S.pollTimer = setTimeout(function () {
      getJSON("/status").then(function (status) {
        if (!isLive()) return;
        S.status = status; S.lastLive = Date.now();
        renderStatus();
        poll(STATUS_POLL_MS);
      }).catch(function (err) {
        if (isLive()) fail(fetchReason(err, "status request failed"), false);
      });
    }, delay);
  }

  function onMessage(text) {
    var m;
    try { m = JSON.parse(text); } catch (e) { return; }
    if (!m || typeof m !== "object") return;
    S.lastLive = Date.now();
    if (m.type === "hello") onHello(m);
    else if (m.type === "state") applyState(m.vehicle, m.dtcs);
    else if (m.type === "exchange") addExchange(m);
    else if (m.type === "dropped") { S.dropped = m; renderStatus(); }
  }

  function onHello(h) {
    if (h.api !== 1) { fail("this page speaks API v1; the simulator offers v" + h.api, true); return; }
    S.hello = h;
    var resumed = S.lastSeq != null;
    if (!resumed) {
      if (h.oldest_seq != null && h.oldest_seq > 1) {
        addMark("note", "History starts at seq " + h.oldest_seq + ".",
          "Seq 1–" + (h.oldest_seq - 1) + " left the simulator's history before this page connected.");
      }
    } else {
      addMark("link", "Connection lost, then resumed.", "Last live " + (S.downAt ? utc(S.downAt) : "unknown") +
        ", resumed " + utc(Date.now()) + " after seq " + S.lastSeq + ".");
      if (h.watermark < S.lastSeq) {
        addGap(1, h.watermark, "The simulator's seq went back to " + h.watermark + ": a new run began. Its earlier exchanges were not received.");
        S.lastSeq = h.watermark;
      } else if (h.gap && h.oldest_seq != null) {
        addGap(S.lastSeq + 1, h.oldest_seq - 1, "Evicted from the simulator's history while this page was disconnected.");
        S.lastSeq = h.oldest_seq - 1;
      }
    }
    S.phase = "live"; S.reason = null; S.cause = null; S.downAt = null;
    if (ESCALATING_CLOSES.indexOf(S.lastClose) < 0) S.attempt = 0;
    clearTimeout(S.pollTimer);
    poll(0);
    renderAll();
  }

  // ---------- data ----------
  function applyState(vehicle, dtcs) {
    S.vehicle = vehicle; S.dtcs = dtcs;
    renderVehicle(); renderDtcs();
    document.body.classList.remove("is-loading");
  }

  function addEntry(entry) {
    entry.id = S.nextId++;
    S.entries.push(entry);
    scheduleRender();
  }
  function addMark(kind, title, text) { addEntry({ kind: kind, title: title, text: text }); }
  function addGap(from, to, why) {
    if (to < from) return;
    var d = dropCounters();
    addEntry({ kind: "gap", from: from, to: to, why: why, drops: d });
  }
  function count(bucket, key, delta) { bucket[key] = (bucket[key] || 0) + delta; }

  // The gap check runs over every received event; a filter hides rows, never makes a gap.
  function addExchange(e) {
    if (typeof e.seq !== "number") return;
    if (S.lastSeq != null) {
      if (e.seq <= S.lastSeq) { S.duplicates += 1; return; }
      if (e.seq > S.lastSeq + 1) addGap(S.lastSeq + 1, e.seq - 1, null);
    }
    S.lastSeq = e.seq;
    count(S.counts.ecu, ecuKey(e), 1);
    count(S.counts.service, serviceOf(e), 1);
    count(S.counts.outcome, e.outcome, 1);
    S.exCount += 1;
    addEntry({ kind: "ex", e: e });
    trim();
  }

  // Keep the newest MAX_ROWS exchanges. Rows that leave are counted and said so; not a gap.
  function trim() {
    while (S.exCount > MAX_ROWS || (S.entries.length && S.entries[0].kind !== "ex" && S.trimmed > 0)) {
      var old = S.entries.shift();
      rowCache.delete(old.id);
      if (old.kind === "ex") {
        S.exCount -= 1; S.trimmed += 1;
        count(S.counts.ecu, ecuKey(old.e), -1);
        count(S.counts.service, serviceOf(old.e), -1);
        count(S.counts.outcome, old.e.outcome, -1);
      }
    }
  }

  function dropCounters() {
    var a = S.status ? S.status.api : {};
    var d = S.dropped || {};
    return {
      handoff: d.handoff_dropped != null ? d.handoff_dropped : a.handoff_dropped,
      client: d.client_dropped,
      forced: d.forced_disconnects != null ? d.forced_disconnects : a.forced_disconnects
    };
  }

  // ---------- rendering: status and link ----------
  function readout(label, value, opts) {
    opts = opts || {};
    return el("div", { cls: "readout" + (opts.group ? " readout--" + opts.group : "") }, [
      el("dt", { text: label }), el("dd", { cls: opts.cls, title: opts.title }, [value])
    ]);
  }
  function pairs(label, group, items) {
    return el("div", { cls: "readout readout--" + group }, [
      el("dt", { text: label }),
      el("dd", { cls: "pairs" }, items.map(function (it) {
        var warn = typeof it[1] === "number" && it[1] > 0 && it[3];
        return el("span", { cls: "pair" + (warn ? " pair--warn" : ""), title: it[2] }, [it[0] + " ", el("b", { text: it[1] == null ? "—" : String(it[1]) })]);
      }))
    ]);
  }

  function renderStatus() {
    var root = $("status");
    var connBox = root.firstElementChild;
    while (connBox.nextSibling) root.removeChild(connBox.nextSibling);
    var s = S.status;
    if (!s) return;
    $("status-sub").textContent = "read-only observer " + s.version;
    var a = s.api, d = dropCounters();
    var sc = s.scenario;
    [
      readout("Interface", s.interface, { cls: "mono" }),
      readout("Profile", String(s.profile).split("/").pop(), { cls: "mono", title: s.profile }),
      readout("Uptime", fmtSeconds(s.uptime_s), { cls: "num", group: "time", title: "since " + utc(s.started_at * 1000) + "; status refreshes every " + STATUS_POLL_MS / 1000 + " s" }),
      readout("Scenario t", sc.enabled && sc.t_last_applied != null ? sc.t_last_applied.toFixed(2) + " s" : sc.enabled ? "not applied yet" : "no scenario",
        { cls: "num", title: sc.enabled ? "t_last_applied; pending_events " + sc.pending_events : "This profile runs no scenario." }),
      pairs("Seq", "seq", [
        ["issued", a.issued_seq, "issued_seq: dispatch calls so far; published " + a.published + ", last_published_seq " + a.last_published_seq],
        ["oldest", a.oldest_seq, "oldest_seq: the lowest seq still in the simulator's history"]
      ]),
      readout("Clients", String(a.clients), { cls: "num", title: "Live WebSocket clients, this page included; refused so far " + a.refused_clients }),
      pairs("Drops", "drops", [
        ["handoff", d.handoff, "handoff_dropped: records the dispatcher could not hand to the publisher", true],
        ["this page", d.client, d.client == null ? "client_dropped: no dropped message on this connection yet" : "client_dropped: events this page's queue refused, on this connection", true],
        ["forced", d.forced, "forced_disconnects: clients closed for reading too slowly (1013)", true]
      ])
    ].forEach(function (n) { root.appendChild(n); });
  }

  function renderLink() {
    var conn = $("conn"), text = $("conn-text"), box = $("linkstate");
    var stale = isStale();
    document.body.classList.toggle("is-stale", stale);
    var tag = S.lastLive ? "Stale, as of " + utc(S.downAt || S.lastLive) : "No data received";
    Array.prototype.forEach.call(document.querySelectorAll(".stale-tag"), function (t) {
      t.hidden = !stale; t.textContent = tag;
    });
    var retry = S.retryAt ? Math.max(0, Math.ceil((S.retryAt - Date.now()) / 1000)) : null;
    if (S.phase === "live") {
      conn.className = "conn conn--live";
      text.textContent = "Live";
      conn.title = "WebSocket open since " + utc(S.wsOpenedAt || Date.now());
    } else if (S.phase === "loading") {
      conn.className = "conn conn--loading";
      text.textContent = "Connecting";
      conn.title = "";
    } else {
      conn.className = "conn " + (S.phase === "refused" ? "conn--refused" : "conn--down");
      text.textContent = (S.phase === "refused" ? "Refused" : "Disconnected") +
        (retry == null ? ", reconnecting" : ", retry in " + retry + " s");
      conn.title = S.reason || "";
    }
    if (!stale) { box.hidden = true; box.textContent = ""; return; }
    box.hidden = false;
    box.className = "linkstate" + (S.phase === "refused" ? " linkstate--refused" : "");
    box.textContent = "";
    var lead = S.phase === "refused" ? "Connection refused." : "Disconnected.";
    var since = S.downAt ? " Last live " + utc(S.downAt) + " (" + ago(S.downAt) + "). The views below show data as of then." :
      " No data has been received yet.";
    var reason = S.cause || S.reason || "unknown";
    if (S.reason && S.cause && S.reason !== S.cause) reason = S.cause + (/\.$/.test(S.cause) ? "" : ".") + " Latest retry: " + S.reason;
    box.appendChild(el("p", null, [el("b", { text: lead }), since + " Reason: " + reason + (/\.$/.test(reason) ? " " : ". ") +
      (retry == null ? "Reconnecting now." : "Retrying in " + retry + " s (" + S.attempt + (S.attempt === 1 ? " failed attempt" : " failed attempts") +
        "; the wait doubles from " + BACKOFF_START_MS / 1000 + " s to at most " + BACKOFF_CAP_MS / 1000 + " s).")]));
    box.appendChild(el("button", { type: "button", id: "btn-retry" }, ["Retry now"]));
  }

  // ---------- rendering: vehicle ----------
  function renderVehicle() {
    var v = S.vehicle, body = $("vehicle");
    if (!v) return;
    $("vehicle-meta").textContent = v.as_of == null ? "no scenario: values as configured" : "as of scenario t = " + v.as_of.toFixed(2) + " s";
    var paths = Object.keys(v.signals || {});
    var key = v.kind + "|" + v.vin + "|" + paths.join(",");
    if (key !== vehicleKey) {
      vehicleKey = key; vehicleCells = {};
      body.textContent = "";
      body.appendChild(el("p", { cls: "kv" }, [
        el("span", null, ["kind ", el("b", { cls: "mono", text: v.kind })]),
        el("span", null, ["VIN ", el("b", { cls: "mono", text: v.vin })])
      ]));
      if (!paths.length) { body.appendChild(el("p", { cls: "empty", text: "This profile defines no vehicle signals." })); return; }
      var groups = {};
      paths.forEach(function (p) {
        var dot = p.indexOf("."), g = dot < 0 ? "" : p.slice(0, dot);
        (groups[g] = groups[g] || []).push(p);
      });
      var rank = { vehicle: 0, engine: 1 };
      var order = Object.keys(groups).sort(function (x, y) {
        return ((x in rank) ? rank[x] : 9) - ((y in rank) ? rank[y] : 9) || x.localeCompare(y);
      });
      var table = el("table", { cls: "sig" });
      table.appendChild(el("thead", null, [el("tr", null, [
        el("th", { scope: "col", text: "Signal" }), el("th", { scope: "col", cls: "num", text: "Value" }), el("th", { scope: "col", text: "Unit" })
      ])]));
      order.forEach(function (g) {
        var tb = el("tbody");
        tb.appendChild(el("tr", { cls: "sig__group" }, [el("th", { scope: "rowgroup", colspan: "3", text: g ? g + ".*" : "(top level)" })]));
        groups[g].forEach(function (p) {
          var td = el("td", { cls: "num" });
          vehicleCells[p] = { td: td, raw: undefined };
          tb.appendChild(el("tr", null, [
            el("th", { scope: "row", cls: "mono sig__name", title: p }, [p.slice(g.length ? g.length + 1 : 0) || p]),
            td, el("td", { cls: "sig__unit", text: UNITS[p] || "" })
          ]));
        });
        table.appendChild(tb);
      });
      body.appendChild(table);
      body.appendChild(el("p", { cls: "fineprint", text: "Units are a display map from the bundled profiles' comments, not API data. Values are rounded to two decimals; hover for the raw value." }));
    }
    paths.forEach(function (p) {
      var cell = vehicleCells[p], raw = v.signals[p];
      if (cell.raw === raw) return;
      var first = cell.raw === undefined;
      cell.raw = raw;
      var shown = fmtValue(raw);
      cell.td.textContent = shown;
      cell.td.className = "num" + (typeof raw === "string" ? " mono" : "");
      if (shown !== String(raw)) cell.td.title = "raw " + raw; else cell.td.removeAttribute("title");
      if (!first) { void cell.td.offsetWidth; cell.td.classList.add("changed"); }
    });
  }

  // ---------- rendering: DTCs ----------
  function flag(on, label) {
    return el("td", { cls: "flag " + (on ? "flag--on" : "flag--off"), title: label + ": " + on }, [
      el("span", { cls: "flag__mark", "aria-hidden": "true" }), el("span", { text: on ? "yes" : "no" }), el("span", { cls: "vh", text: " " + label })
    ]);
  }
  function codeState(c) { return c.confirmed ? "Confirmed" : c.pending ? "Pending" : "Not set"; }

  function renderDtcs() {
    var dtcs = S.dtcs, root = $("dtcs");
    if (!dtcs) return;
    var key = JSON.stringify(dtcs) + "|" + (S.ecus ? "e" : "");
    if (key === dtcKey) return;
    dtcKey = key;
    root.textContent = "";
    var names = Object.keys(dtcs);
    if (!names.length) { root.appendChild(el("p", { cls: "empty", text: "This profile has no ECUs." })); return; }
    names.forEach(function (ecu) {
      var d = dtcs[ecu], codes = d.codes || [];
      var set = codes.filter(function (c) { return c.pending || c.confirmed || c.indicator_requested; }).length;
      var head = el("div", { cls: "ecu__head" }, [
        el("h3", { cls: "mono", text: ecu }),
        el("span", { cls: "ecu__count", text: codes.length ? set + " of " + codes.length + " codes set" : "no codes stored" }),
        el("div", { cls: "mil " + (d.mil ? "mil--on" : "mil--off"), title: "MIL: the malfunction indicator lamp" }, [
          el("span", { cls: "mil__lamp", "aria-hidden": "true" }), el("b", { text: "MIL " + (d.mil ? "on" : "off") })
        ])
      ]);
      var article = el("article", { cls: "ecu", "aria-label": "ECU " + ecu }, [head]);
      if (!codes.length) {
        article.appendChild(el("p", { cls: "empty", text: "No trouble codes are stored for this ECU." }));
      } else {
        var table = el("table", { cls: "dtc" });
        table.appendChild(el("thead", null, [el("tr", null, [
          el("th", { scope: "col", text: "Code" }), el("th", { scope: "col", text: "State" }),
          el("th", { scope: "col", text: "Pending" }), el("th", { scope: "col", text: "Confirmed" }),
          el("th", { scope: "col" }, [el("abbr", { title: "indicator_requested: the code asks for the MIL" }, ["Lamp"])])
        ])]));
        var tb = el("tbody");
        codes.forEach(function (c) {
          var st = codeState(c);
          tb.appendChild(el("tr", { cls: "dtc__row dtc__row--" + st.toLowerCase().replace(" ", "-") }, [
            el("th", { scope: "row", cls: "mono dtc__code", text: c.code }),
            el("td", { cls: "dtc__state", text: st }),
            flag(c.pending, "pending"), flag(c.confirmed, "confirmed"), flag(c.indicator_requested, "indicator_requested")
          ]));
        });
        table.appendChild(tb);
        article.appendChild(table);
      }
      var info = S.ecus && S.ecus[ecu];
      if (info) article.appendChild(ecuDetails(ecu, info));
      root.appendChild(article);
    });
  }

  function ecuDetails(ecu, info) {
    var list = el("ul", { cls: "eplist" });
    info.endpoints.forEach(function (ep) {
      list.appendChild(el("li", null, [
        el("span", { cls: "mono", text: ep.name.replace(ecu + ".", "") }),
        el("span", { cls: "mono ids", text: canId(ep.rx_id) + " → " + canId(ep.tx_id) }),
        el("span", { cls: "ep__notes", text: [
          ep.functional ? "functional" : "physical",
          ep.reply_via ? "replies via " + ep.reply_via.replace(ecu + ".", "") : null,
          ep.padding ? "padded" : "unpadded"
        ].filter(Boolean).join(", ") })
      ]));
    });
    var protos = el("ul", { cls: "eplist" });
    info.protocols.forEach(function (p) {
      protos.appendChild(el("li", null, [
        el("span", { cls: "mono", text: p.name }),
        el("span", { cls: "mono sids", text: p.sids.map(function (x) { return ("0" + x.toString(16)).slice(-2).toUpperCase(); }).join(" ") })
      ]));
    });
    return el("details", { cls: "ecu__routes" }, [
      el("summary", { text: info.endpoints.length + " endpoints, " + info.protocols.length + " protocols" }),
      list, el("p", { cls: "fineprint", text: "Service IDs served, hex:" }), protos
    ]);
  }

  // ---------- rendering: exchange log ----------
  function passes(e) {
    if (view.ecu !== "all" && ecuKey(e) !== view.ecu) return false;
    if (view.service !== "all" && serviceOf(e) !== view.service) return false;
    return view.outcomes.indexOf(e.outcome) >= 0;
  }

  function hexCell(e, which, id) {
    var h = e[which], len = e[which + "_len"], trunc = e[which + "_truncated"];
    var td = el("td", { cls: "c-hex " + (which === "request" ? "c-req" : "c-res") });
    if (h == null) {
      td.appendChild(el("span", { cls: "none", text: e.error === "encode_failed" ? "not encoded" : "none" }));
      return td;
    }
    var bytes = hexBytes(h), key = id + ":" + which, open = !!view.expanded[key];
    td.appendChild(el("code", { cls: "hex" + (open ? " hex--open" : "") }, [(open ? bytes : bytes.slice(0, HEX_PREVIEW_BYTES)).join(" ")]));
    var meta = el("span", { cls: "hexmeta" }, [(len != null ? len : bytes.length) + " B"]);
    if (trunc) meta.appendChild(el("span", { cls: "trunc", title: "The API kept the first " + bytes.length + " of " + len + " bytes" }, [bytes.length + " of " + len + " B kept"]));
    if (bytes.length > HEX_PREVIEW_BYTES) {
      meta.appendChild(el("button", { type: "button", cls: "linkbtn", "data-expand": key, "data-id": String(id), "aria-expanded": String(open) },
        [open ? "show less" : "show all " + bytes.length]));
    }
    td.appendChild(meta);
    return td;
  }

  function exRow(entry) {
    var e = entry.e, fallback = e.error === "encode_failed";
    var tr = el("tr", { cls: "ex ex--" + e.outcome });
    tr.appendChild(el("td", { cls: "c-seq mono", text: String(e.seq) }));
    tr.appendChild(el("td", { cls: "c-time mono", title: e.t || "" , text: e.t ? e.t.slice(11, 23) : "—" }));
    tr.appendChild(el("td", { cls: "c-ecu" }, [
      e.ecu === undefined ? el("span", { cls: "none", text: "—" }) : e.ecu === null ? el("span", { cls: "none", text: "no route" }) : String(e.ecu)
    ]));
    var ep = el("td", { cls: "c-ep" });
    if (e.endpoint) ep.appendChild(el("span", { cls: "mono ep__name", title: e.endpoint, text: e.endpoint.replace(/^[^.]+\./, "") }));
    else if (e.endpoint === null) ep.appendChild(el("span", { cls: "none ep__name", text: "no endpoint" }));
    if (e.rx_id !== undefined) {
      var ids = el("span", { cls: "mono ids", text: canId(e.rx_id) + " → " + canId(e.tx_id) });
      if (e.functional) ids.appendChild(el("span", { cls: "func", title: "functional: a broadcast request", text: "func" }));
      ep.appendChild(ids);
    }
    tr.appendChild(ep);
    var sum = el("td", { cls: "c-sum", text: e.summary || "" });
    if (fallback) sum.appendChild(el("span", { cls: "fallback", text: "encode_failed: the simulator kept only seq, outcome and error for this exchange" }));
    else if (e.error) sum.appendChild(el("span", { cls: "errname mono", title: "Exception type the dispatcher raised", text: e.error }));
    tr.appendChild(sum);
    tr.appendChild(hexCell(e, "request", entry.id));
    tr.appendChild(hexCell(e, "response", entry.id));
    tr.appendChild(el("td", { cls: "c-out" }, [el("span", { cls: "badge badge--" + e.outcome, title: "outcome: " + e.outcome, text: OUTCOME_LABEL[e.outcome] || String(e.outcome) })]));
    tr.appendChild(el("td", { cls: "c-us num mono", title: "dispatcher time, not wire latency", text: e.dispatch_us == null ? "—" : String(e.dispatch_us) }));
    return tr;
  }

  function markRow(entry) {
    if (entry.kind === "gap") {
      var n = entry.to - entry.from + 1;
      var d = entry.drops;
      var title = n === 1 ? "Gap: seq " + entry.from + " not received." : "Gap: seq " + entry.from + "–" + entry.to + " not received (" + n + ").";
      var text = entry.why || ("Counters when it was seen: handoff_dropped " + (d.handoff == null ? "—" : d.handoff) +
        ", this page's client_dropped " + (d.client == null ? "—" : d.client) + ", forced_disconnects " + (d.forced == null ? "—" : d.forced) + ".");
      return el("tr", { cls: "mark mark--gap" }, [el("td", { colspan: "9" }, [el("span", { cls: "mark__title", text: title }), text])]);
    }
    return el("tr", { cls: "mark mark--" + entry.kind }, [el("td", { colspan: "9" }, [el("span", { cls: "mark__title", text: entry.title }), entry.text])]);
  }

  function scheduleRender() {
    if (renderTimer) return;
    renderTimer = setTimeout(function () { renderTimer = null; renderLog(); }, Math.max(0, RENDER_MIN_MS - (Date.now() - lastRender)));
  }

  function renderLog() {
    lastRender = Date.now();
    var wrap = $("logwrap"), body = $("log-body");
    var stick = wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 40;
    var frag = document.createDocumentFragment();
    var shown = 0, hidden = 0, inView = 0, held = 0;
    function flushHidden() {
      if (!hidden) return;
      frag.appendChild(el("tr", { cls: "hiddenrow" }, [el("td", { colspan: "9", text: hidden + (hidden === 1 ? " exchange" : " exchanges") + " hidden by filters (not a gap)" })]));
      hidden = 0;
    }
    if (S.trimmed && !view.clearedAfter) {
      frag.appendChild(markRow({ kind: "note", title: S.trimmed + " older rows left this view.",
        text: "The page keeps the newest " + MAX_ROWS.toLocaleString("en") + " exchanges it received. They were received, so this is not a gap." }));
    }
    S.entries.forEach(function (entry) {
      if (entry.id <= view.clearedAfter) return;
      if (view.paused && entry.id > view.pauseAfter) { if (entry.kind === "ex") held += 1; return; }
      if (entry.kind !== "ex") { flushHidden(); frag.appendChild(rowFor(entry)); return; }
      inView += 1;
      if (!passes(entry.e)) { hidden += 1; return; }
      flushHidden();
      frag.appendChild(rowFor(entry));
      shown += 1;
    });
    if (shown) flushHidden(); else hidden = 0;
    body.replaceChildren(frag);
    wrap.classList.toggle("is-empty", shown === 0);
    if (stick && !view.paused) wrap.scrollTop = wrap.scrollHeight;
    renderLogState(shown, inView, held);
    renderFilterCounts();
    var h = S.hello;
    $("log-count").textContent = !h && S.lastSeq == null ? "" :
      (shown === inView ? inView + " exchanges" : shown + " of " + inView + " shown") +
      (S.lastSeq != null ? ", last seq " + S.lastSeq : "") + (S.duplicates ? ", " + S.duplicates + " duplicates ignored" : "");
  }
  function rowFor(entry) {
    var tr = rowCache.get(entry.id);
    if (!tr) { tr = entry.kind === "ex" ? exRow(entry) : markRow(entry); rowCache.set(entry.id, tr); }
    return tr;
  }

  function renderLogState(shown, inView, held) {
    var box = $("log-state");
    box.textContent = "";
    function line(cls, parts, button) {
      var p = el("div", { cls: "log-state__line" + (cls ? " log-state__line--" + cls : "") }, [el("p", null, parts)]);
      if (button) p.appendChild(button);
      box.appendChild(p);
    }
    if (view.paused) {
      line("paused", [el("b", { text: "View paused." }), " " + held + (held === 1 ? " new exchange is" : " new exchanges are") + " held; they appear when you resume. The simulator keeps running."]);
    }
    if (!S.hello && S.lastSeq == null) {
      line(null, isStale() ? [el("b", { text: "No exchanges received." }), " The live stream has not connected; exchanges appear once it does."] :
        [el("b", { text: "Loading the exchange history." })]);
    } else if (shown === 0) {
      if (S.exCount === 0 && !view.clearedAfter) {
        var w = S.hello ? S.hello.watermark : 0;
        line(null, [el("b", { text: "No exchanges yet." }), w ? " The simulator has published " + w + " but none are in its history." :
          " The simulator has not handled a diagnostic request since it started. Send one and it appears here."]);
      } else if (inView === 0 && view.clearedAfter) {
        line(null, [el("b", { text: "No exchanges since you cleared the view." }), " The simulator's history is untouched; new exchanges appear here."],
          el("button", { type: "button", id: "btn-restore", text: "Show cleared rows" }));
      } else if (inView > 0) {
        line(null, [el("b", { text: "No exchanges match these filters." }), " " + inView + " are hidden by the ECU, service or outcome filter."],
          el("button", { type: "button", id: "btn-reset", text: "Reset filters" }));
      }
    }
    box.hidden = !box.firstChild;
  }

  // ---------- filters and view controls (view only; nothing is sent) ----------
  function syncOptions(select, counts, label) {
    var have = {};
    Array.prototype.forEach.call(select.options, function (o) { have[o.value] = o; });
    var total = S.exCount;
    have.all.textContent = select.id === "f-ecu" ? "All ECUs (" + total + ")" : "All services (" + total + ")";
    Object.keys(counts).sort().forEach(function (k) {
      if (!have[k]) { have[k] = el("option", { value: k }); select.appendChild(have[k]); }
      have[k].textContent = label(k) + " (" + counts[k] + ")";
    });
  }
  function renderFilterCounts() {
    syncOptions($("f-ecu"), S.counts.ecu, function (k) { return k === "none" ? "no route" : k === "?" ? "not in event" : k; });
    syncOptions($("f-service"), S.counts.service, function (k) { return k === "?" ? "no request" : "0x" + k.toUpperCase(); });
    OUTCOMES.forEach(function (o) { $("n-" + o).textContent = String(S.counts.outcome[o] || 0); });
  }

  function buildControls() {
    var fs = $("f-outcome");
    OUTCOMES.forEach(function (o) {
      var id = "o-" + o;
      fs.appendChild(el("label", { cls: "chip chip--" + o, "for": id }, [
        el("input", { type: "checkbox", id: id, value: o, checked: true }),
        el("span", { text: OUTCOME_LABEL[o] }), el("span", { cls: "chip__n", id: "n-" + o, text: "0" })
      ]));
    });
    $("f-ecu").addEventListener("change", function (ev) { view.ecu = ev.target.value; renderLog(); });
    $("f-service").addEventListener("change", function (ev) { view.service = ev.target.value; renderLog(); });
    fs.addEventListener("change", function () {
      view.outcomes = OUTCOMES.filter(function (o) { return $("o-" + o).checked; });
      renderLog();
    });
    $("btn-pause").addEventListener("click", function () {
      view.paused = !view.paused;
      if (view.paused) view.pauseAfter = S.nextId - 1;
      syncControls(); renderLog();
    });
    $("btn-clear").addEventListener("click", function () { view.clearedAfter = S.nextId - 1; view.pauseAfter = view.clearedAfter; renderLog(); });
    $("log-panel").addEventListener("click", function (ev) {
      var t = ev.target;
      if (!(t instanceof HTMLElement)) return;
      if (t.id === "btn-restore") { view.clearedAfter = 0; renderLog(); $("btn-clear").focus(); }
      else if (t.id === "btn-reset") {
        view.ecu = "all"; view.service = "all"; view.outcomes = OUTCOMES.slice();
        syncControls(); renderLog(); $("f-ecu").focus();
      } else if (t.dataset.expand) {
        var key = t.dataset.expand;
        view.expanded[key] = !view.expanded[key];
        rowCache.delete(Number(t.dataset.id));
        renderLog();
        var again = document.querySelector('[data-expand="' + key + '"]');
        if (again) again.focus();
      }
    });
    $("linkstate").addEventListener("click", function (ev) {
      if (ev.target instanceof HTMLElement && ev.target.id === "btn-retry") connect();
    });
    $("foot-limits").textContent = "Filters, pause and clear change this view only; the page sends nothing to the simulator. " +
      "Status refreshes every " + STATUS_POLL_MS / 1000 + " s; signals, trouble codes and exchanges arrive on the live stream. " +
      "The page keeps the newest " + MAX_ROWS.toLocaleString("en") + " exchanges.";
  }
  function syncControls() {
    var pause = $("btn-pause");
    pause.setAttribute("aria-pressed", String(view.paused));
    pause.textContent = view.paused ? "Resume view" : "Pause view";
    $("f-ecu").value = view.ecu;
    $("f-service").value = view.service;
    OUTCOMES.forEach(function (o) { $("o-" + o).checked = view.outcomes.indexOf(o) >= 0; });
  }

  function renderAll() { renderLink(); renderStatus(); renderLog(); }

  buildControls();
  renderAll();
  setInterval(function () { if (isStale()) renderLink(); }, 1000);
  connect();
})();
