/* M3a dashboard mockup. Renders window.ECU_SAMPLE (sample-data.js); nothing is fetched,
 * nothing is sent. A classic script, not a module, so it runs from file:// in every
 * browser. The only protocol knowledge here is reading a request's first byte for the
 * service filter; every description comes from the API's own `summary` (0010 §5, §7).
 *
 * View presets from the URL hash, all view-only:
 *   #ecu=engine  #service=01  #outcome=no_response,error  #paused  #cleared
 *   #tail (scroll the log to the newest row); combine with &, e.g. #outcome=error&paused
 */
(function () {
  "use strict";

  var D = window.ECU_SAMPLE;
  var OUTCOMES = ["responded", "no_response", "unrouted", "error"];
  var OUTCOME_LABEL = { responded: "responded", no_response: "no response", unrouted: "unrouted", error: "error" };

  // Presentation only. Units are the ones ice_default.yaml's comments state; the API sends
  // values without units, and a path missing here is shown without one.
  var UNITS = {
    "vehicle.speed": "km/h",
    "vehicle.ambient_temp": "°C",
    "vehicle.battery_voltage": "V",
    "vehicle.obd_standard": "code",
    "engine.coolant_temp": "°C",
    "engine.intake_temp": "°C",
    "engine.engine_load": "%",
    "engine.throttle": "%",
    "engine.maf": "g/s",
    "engine.map": "kPa",
    "engine.timing_advance": "° BTDC",
    "engine.short_fuel_trim": "%",
    "engine.long_fuel_trim": "%",
    "engine.runtime": "s",
    "engine.fuel_level": "%",
    "engine.fuel_type": "code"
  };

  var HEX_PREVIEW_BYTES = 6;

  var view = {
    ecu: "all",
    service: "all",
    outcomes: OUTCOMES.slice(),
    paused: false,
    clearedAfter: null, // seq; events at or below it are hidden by "Clear view"
    expanded: {}
  };

  // ---------- helpers ----------
  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    if (attrs) {
      for (var k in attrs) {
        if (!Object.prototype.hasOwnProperty.call(attrs, k) || attrs[k] == null || attrs[k] === false) continue;
        if (k === "text") n.textContent = attrs[k];
        else if (k === "cls") n.className = attrs[k];
        else n.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
      }
    }
    (children || []).forEach(function (c) {
      if (c == null) return;
      n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return n;
  }
  function isSynthetic(x) { return x && x._provenance === "synthetic"; }
  function synTag(note) {
    return el("span", { cls: "syn-tag", title: note || "Hand-made for this mockup; not simulator output." }, ["synthetic"]);
  }
  function fmtSeconds(s) {
    if (s < 600) return s.toFixed(1) + " s";
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
    return (h ? h + " h " : "") + m + " min " + sec + " s";
  }
  function fmtValue(v) {
    if (typeof v === "number" && !Number.isInteger(v)) {
      var r = Math.round(v * 100) / 100;
      return String(r);
    }
    return String(v);
  }
  function hexBytes(h) { return h ? h.match(/../g) : []; }
  function canId(s) { return s ? s.replace(/^0x/, "").toUpperCase() : "—"; }
  function serviceOf(e) { return e.request ? e.request.slice(0, 2).toLowerCase() : null; }
  function ecuKey(e) { return e.ecu === undefined ? "?" : e.ecu === null ? "none" : e.ecu; }

  // ---------- status bar ----------
  function readout(label, value, opts) {
    opts = opts || {};
    var dd = el("dd", { cls: opts.cls, title: opts.title }, [value]);
    if (opts.syn) dd.appendChild(synTag(opts.syn));
    return el("div", { cls: "readout" + (opts.group ? " readout--" + opts.group : "") }, [el("dt", { text: label }), dd]);
  }
  function pairs(label, cls, items) {
    return el("div", { cls: "readout " + cls }, [
      el("dt", { text: label }),
      el("dd", { cls: "pairs" }, items.map(function (it) {
        var n = el("span", { cls: "pair", title: it[2] }, [it[0] + " ", el("b", { text: String(it[1]) })]);
        if (it[3]) n.appendChild(synTag(it[3]));
        return n;
      }))
    ]);
  }
  function drops(drop) {
    return pairs("Drops", "readout--drops", [
      ["handoff", drop.handoff_dropped, "handoff_dropped: records the hot path could not hand to the publisher"],
      ["this client", drop.client_dropped, "client_dropped: events this client's queue refused, from the dropped message", drop._synthetic_note],
      ["forced", drop.forced_disconnects, "forced_disconnects"]
    ]);
  }
  function renderStatus() {
    var s = D.status, a = s.api, drop = D.dropped;
    var root = document.getElementById("status");
    document.getElementById("status-sub").textContent = "observer " + s.version;
    var profile = s.profile.split("/").pop();
    var conn = el("span", { cls: "conn" }, [el("span", { cls: "conn__ring", "aria-hidden": "true" }), "SAMPLE DATA — not connected"]);
    [
      readout("Connection", conn, { cls: "readout__conn", title: "This page is a static mockup. It has no WebSocket and no HTTP connection." }),
      readout("Interface", s.interface, { cls: "mono" }),
      readout("Profile", profile, { cls: "mono", title: s.profile }),
      readout("Uptime", fmtSeconds(s.uptime_s), { cls: "num", title: s.uptime_s.toFixed(3) + " s since started_at", group: "time" }),
      readout("Scenario t", s.scenario.enabled ? s.scenario.t_last_applied.toFixed(2) + " s" : "no scenario",
        { cls: "num", title: "t_last_applied; pending_events " + s.scenario.pending_events }),
      pairs("Seq", "readout--seq", [
        ["issued", a.issued_seq, "issued_seq; published " + a.published + ", last_published_seq " + a.last_published_seq],
        ["oldest", a.oldest_seq, "oldest_seq: the lowest seq still in the history ring"]
      ]),
      readout("Clients", String(a.clients), { cls: "num" }),
      drops(drop)
    ].forEach(function (n) { root.appendChild(n); });
  }

  // ---------- vehicle ----------
  function renderVehicle() {
    var v = D.vehicle;
    var groups = {};
    Object.keys(v.signals).forEach(function (path) {
      var dot = path.indexOf(".");
      var g = dot < 0 ? "" : path.slice(0, dot);
      (groups[g] = groups[g] || []).push(path);
    });
    var order = Object.keys(groups).sort(function (x, y) {
      var rank = { vehicle: 0, engine: 1 };
      return ((x in rank) ? rank[x] : 9) - ((y in rank) ? rank[y] : 9) || x.localeCompare(y);
    });
    var table = el("table", { cls: "sig" });
    table.appendChild(el("thead", null, [el("tr", null, [
      el("th", { scope: "col", text: "Signal" }),
      el("th", { scope: "col", cls: "num", text: "Value" }),
      el("th", { scope: "col", text: "Unit" })
    ])]));
    order.forEach(function (g) {
      var tb = el("tbody");
      tb.appendChild(el("tr", { cls: "sig__group" }, [el("th", { scope: "rowgroup", colspan: "3", text: g ? g + ".*" : "(top level)" })]));
      groups[g].forEach(function (path) {
        var raw = v.signals[path];
        var shown = fmtValue(raw);
        tb.appendChild(el("tr", null, [
          el("th", { scope: "row", cls: "mono sig__name", title: path }, [path.slice(g.length + 1) || path]),
          el("td", { cls: "num" + (typeof raw === "string" ? " mono" : ""), title: shown !== String(raw) ? "raw " + raw : null }, [shown]),
          el("td", { cls: "sig__unit", text: UNITS[path] || "" })
        ]));
      });
      table.appendChild(tb);
    });
    var body = document.getElementById("vehicle");
    body.appendChild(el("p", { cls: "kv" }, [
      el("span", null, ["kind ", el("b", { cls: "mono", text: v.kind })]),
      el("span", null, ["VIN ", el("b", { cls: "mono", text: v.vin })])
    ]));
    body.appendChild(table);
    body.appendChild(el("p", { cls: "fineprint" }, ["Units are a display map from the profile's comments, not API data. Values with more than two decimals are rounded for display; hover for the raw value."]));
    body.appendChild(el("div", { cls: "slot-m3b", role: "note" }, ["Per-signal sparklines arrive in M3b. Not part of this mockup."]));
    document.getElementById("vehicle-meta").textContent =
      v.as_of == null ? "no scenario" : "as of t = " + v.as_of.toFixed(2) + " s";
  }

  // ---------- DTCs ----------
  function flag(on, label, apiName) {
    return el("td", { cls: "flag " + (on ? "flag--on" : "flag--off"), title: apiName + ": " + on }, [
      el("span", { cls: "flag__mark", "aria-hidden": "true" }),
      el("span", { cls: "flag__text", text: on ? "yes" : "no" }),
      el("span", { cls: "vh", text: " " + label })
    ]);
  }
  function codeState(c) {
    if (c.confirmed) return "Confirmed";
    if (c.pending) return "Pending";
    return "Not set";
  }
  function renderDtcs() {
    var root = document.getElementById("dtcs");
    Object.keys(D.dtcs).forEach(function (ecu) {
      var d = D.dtcs[ecu];
      var codes = d.codes || [];
      var active = codes.filter(function (c) { return c.pending || c.confirmed || c.indicator_requested; }).length;
      var mil = el("div", { cls: "mil " + (d.mil ? "mil--on" : "mil--off"), role: "status", "aria-label": "MIL " + (d.mil ? "on" : "off") }, [
        el("span", { cls: "mil__lamp", "aria-hidden": "true" }),
        el("span", null, [el("b", { text: "MIL " + (d.mil ? "on" : "off") })])
      ]);
      var head = el("div", { cls: "ecu__head" }, [
        el("h3", { cls: "mono", text: ecu }),
        el("span", { cls: "ecu__count", text: active + " of " + codes.length + " codes set" }),
        mil
      ]);
      var table = el("table", { cls: "dtc" });
      table.appendChild(el("thead", null, [el("tr", null, [
        el("th", { scope: "col", text: "Code" }),
        el("th", { scope: "col", text: "State" }),
        el("th", { scope: "col", text: "Pending" }),
        el("th", { scope: "col", text: "Confirmed" }),
        el("th", { scope: "col" }, [el("abbr", { title: "indicator_requested: the code asks for the MIL" }, ["Lamp"])])
      ])]));
      var tb = el("tbody");
      codes.forEach(function (c) {
        var st = codeState(c);
        tb.appendChild(el("tr", { cls: "dtc__row dtc__row--" + st.toLowerCase().replace(" ", "-") }, [
          el("th", { scope: "row", cls: "mono dtc__code", text: c.code }),
          el("td", { cls: "dtc__state", text: st }),
          flag(c.pending, "pending", "pending"),
          flag(c.confirmed, "confirmed", "confirmed"),
          flag(c.indicator_requested, "lamp requested", "indicator_requested")
        ]));
      });
      table.appendChild(tb);

      var ecuInfo = D.ecus[ecu];
      var details = null;
      if (ecuInfo) {
        var list = el("ul", { cls: "eplist" });
        ecuInfo.endpoints.forEach(function (ep) {
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
        ecuInfo.protocols.forEach(function (p) {
          protos.appendChild(el("li", null, [
            el("span", { cls: "mono", text: p.name }),
            el("span", { cls: "mono sids", text: p.sids.map(function (x) { return ("0" + x.toString(16)).slice(-2).toUpperCase(); }).join(" ") })
          ]));
        });
        details = el("details", { cls: "ecu__routes" }, [
          el("summary", { text: ecuInfo.endpoints.length + " endpoints, " + ecuInfo.protocols.length + " protocols" }),
          list, el("p", { cls: "fineprint", text: "Service IDs served, hex:" }), protos
        ]);
      }
      root.appendChild(el("article", { cls: "ecu", "aria-label": "ECU " + ecu }, [head, table, details]));
    });
  }

  // ---------- exchange log ----------
  function events() { return D.exchanges.events.slice().sort(function (a, b) { return a.seq - b.seq; }); }

  function passes(e) {
    if (view.ecu !== "all" && ecuKey(e) !== view.ecu) return false;
    if (view.service !== "all" && (serviceOf(e) || "?") !== view.service) return false;
    if (view.outcomes.indexOf(e.outcome) < 0) return false;
    return true;
  }

  function hexCell(e, which) {
    var h = e[which], len = e[which + "_len"], trunc = e[which + "_truncated"];
    var td = el("td", { cls: "c-hex" });
    if (h == null) {
      td.appendChild(el("span", { cls: "none", text: e.error === "encode_failed" ? "not encoded" : "none" }));
      return td;
    }
    var bytes = hexBytes(h);
    var key = e.seq + ":" + which;
    var open = !!view.expanded[key];
    var shown = open ? bytes : bytes.slice(0, HEX_PREVIEW_BYTES);
    td.appendChild(el("code", { cls: "hex" + (open ? " hex--open" : "") }, [shown.join(" ")]));
    var meta = el("span", { cls: "hexmeta" }, [len + " B"]);
    if (trunc) meta.appendChild(el("span", { cls: "trunc", title: "The API retained the first " + bytes.length + " of " + len + " bytes" }, [bytes.length + " of " + len + " B retained"]));
    if (bytes.length > HEX_PREVIEW_BYTES) {
      meta.appendChild(el("button", {
        type: "button", cls: "linkbtn", "data-expand": key, "aria-expanded": String(open)
      }, [open ? "show less" : "show all " + bytes.length]));
    }
    td.appendChild(meta);
    return td;
  }

  function row(e) {
    var syn = isSynthetic(e);
    var fallback = e.error === "encode_failed";
    var tr = el("tr", { cls: "ex ex--" + e.outcome + (syn ? " ex--syn" : "") });
    var seqCell = el("td", { cls: "c-seq mono" }, [String(e.seq)]);
    if (syn) seqCell.appendChild(synTag(e._synthetic_note));
    tr.appendChild(seqCell);
    tr.appendChild(el("td", { cls: "c-time mono", "data-label": "Time" }, [e.t ? e.t.slice(11, 23) : "—"]));
    tr.appendChild(el("td", { cls: "c-ecu", "data-label": "ECU" }, [
      e.ecu === undefined ? el("span", { cls: "none", text: "—" }) : e.ecu === null ? el("span", { cls: "none", text: "no route" }) : e.ecu
    ]));
    var ep = el("td", { cls: "c-ep", "data-label": "Endpoint" });
    if (e.endpoint) {
      ep.appendChild(el("span", { cls: "mono ep__name", title: e.endpoint }, [e.endpoint.replace(/^[^.]+\./, "")]));
    } else if (e.endpoint === null) {
      ep.appendChild(el("span", { cls: "none ep__name", text: "no endpoint" }));
    }
    if (e.rx_id !== undefined) {
      ep.appendChild(el("span", { cls: "mono ids" }, [canId(e.rx_id) + " → " + canId(e.tx_id)]));
      if (e.functional) ep.lastChild.appendChild(el("span", { cls: "func", title: "functional: true (a broadcast request)" }, ["func"]));
    }
    tr.appendChild(ep);
    var sum = el("td", { cls: "c-sum" }, [e.summary || ""]);
    if (fallback) sum.appendChild(el("span", { cls: "fallback" }, ["encode_failed fallback: only seq, outcome and error were kept"]));
    if (e.error && !fallback) sum.appendChild(el("span", { cls: "errname mono", title: "Exception type the dispatcher raised" }, [e.error]));
    tr.appendChild(sum);
    tr.appendChild(hexCell(e, "request"));
    tr.appendChild(hexCell(e, "response"));
    tr.appendChild(el("td", { cls: "c-out" }, [el("span", { cls: "badge badge--" + e.outcome, title: "outcome: " + e.outcome }, [OUTCOME_LABEL[e.outcome] || e.outcome])]));
    tr.appendChild(el("td", { cls: "c-us num mono" }, [e.dispatch_us == null ? "—" : String(e.dispatch_us)]));
    return tr;
  }

  function gapRow(from, to) {
    var n = to - from + 1;
    var drop = D.dropped;
    var explain = [];
    if (drop && drop._after_seq === from - 1) {
      explain.push(el("span", null, ["Explained by the dropped message: this client's client_dropped ",
        el("b", { text: String(drop.client_dropped) }), ", handoff_dropped ", el("b", { text: String(drop.handoff_dropped) }),
        ", forced_disconnects ", el("b", { text: String(drop.forced_disconnects) }), "."]));
      if (isSynthetic(drop)) explain.push(synTag(drop._synthetic_note));
    } else {
      explain.push("No dropped message arrived; see the drop counters in the status bar.");
    }
    return el("tr", { cls: "gap" }, [el("td", { colspan: "9" }, [
      el("span", { cls: "gap__rule", "aria-hidden": "true" }),
      el("span", { cls: "gap__text" }, [
        el("b", { cls: "gap__title", text: n === 1 ? "Gap: seq " + from + " not received" : "Gap: seq " + from + "–" + to + " not received (" + n + ")" }),
        " "
      ].concat(explain))
    ])]);
  }

  function boundaryRow(seq) {
    return el("tr", { cls: "boundary" }, [el("td", { colspan: "9" }, [
      "End of the real capture at seq " + seq + ". Every row below is hand-made to show a state the capture did not produce."
    ])]);
  }

  function renderLog() {
    var body = document.getElementById("log-body");
    var all = events();
    var visibleSource = view.clearedAfter == null ? all : all.filter(function (e) { return e.seq > view.clearedAfter; });
    var shownCount = 0;
    body.textContent = "";

    var prev = view.clearedAfter == null ? (D.exchanges.oldest_seq - 1) : view.clearedAfter;
    if (view.clearedAfter == null && D.exchanges.gap) {
      body.appendChild(el("tr", { cls: "gap" }, [el("td", { colspan: "9", text: "History starts at seq " + D.exchanges.oldest_seq + "; older events were evicted." })]));
    }
    var hidden = 0;
    function flushHidden() {
      if (!hidden) return;
      body.appendChild(el("tr", { cls: "hiddenrow" }, [el("td", { colspan: "9", text: hidden + (hidden === 1 ? " exchange" : " exchanges") + " hidden by filters (not a gap)" })]));
      hidden = 0;
    }
    visibleSource.forEach(function (e) {
      var gap = e.seq > prev + 1, boundary = prev === D.captured_last_seq && e.seq > prev;
      if (gap || boundary) flushHidden();
      if (gap) body.appendChild(gapRow(prev + 1, e.seq - 1));
      if (boundary) body.appendChild(boundaryRow(prev));
      prev = e.seq;
      if (!passes(e)) { hidden++; return; }
      flushHidden();
      body.appendChild(row(e));
      shownCount++;
    });
    if (shownCount) flushHidden(); else hidden = 0;

    var state = document.getElementById("log-state");
    state.textContent = "";
    state.hidden = true;
    state.className = "log-state";
    if (view.clearedAfter != null && visibleSource.length === 0) {
      state.hidden = false;
      state.classList.add("log-state--empty");
      state.appendChild(el("p", null, [el("b", { text: "No exchanges in this view." }),
        " You cleared it after seq " + view.clearedAfter + ". The simulator's history is untouched; new exchanges would appear here as testers send requests. In this mockup nothing arrives."]));
      state.appendChild(el("button", { type: "button", id: "btn-restore" }, ["Show the sample again"]));
    } else if (shownCount === 0) {
      state.hidden = false;
      state.classList.add("log-state--empty");
      state.appendChild(el("p", null, [el("b", { text: "No exchanges match these filters." }), " " + visibleSource.length + " exchanges are hidden by the ECU, service or outcome filter."]));
      state.appendChild(el("button", { type: "button", id: "btn-reset" }, ["Reset filters"]));
    }
    if (view.paused) {
      var p = el("div", { cls: "log-state log-state--paused", role: "status" }, [
        el("p", null, [el("b", { text: "View paused." }), " New exchanges are held and counted, not shown. Held: 0 (this mockup receives none)."])
      ]);
      if (state.hidden) { state.hidden = false; state.className = "log-state log-state--paused"; state.appendChild(p.firstChild); }
      else state.insertBefore(p, state.firstChild);
    }
    document.getElementById("logwrap").classList.toggle("is-empty", shownCount === 0);
    document.getElementById("log-panel").classList.toggle("is-paused", view.paused);
    var total = visibleSource.length;
    document.getElementById("log-count").textContent =
      (shownCount === total ? total + " exchanges" : shownCount + " of " + total + " shown") +
      ", watermark " + D.exchanges.watermark;
  }

  function renderFilters() {
    var all = events();
    var ecuSel = document.getElementById("f-ecu");
    var svcSel = document.getElementById("f-service");
    var ecus = {}, svcs = {};
    all.forEach(function (e) {
      ecus[ecuKey(e)] = (ecus[ecuKey(e)] || 0) + 1;
      var s = serviceOf(e) || "?";
      svcs[s] = (svcs[s] || 0) + 1;
    });
    ecuSel.appendChild(el("option", { value: "all", text: "All ECUs (" + all.length + ")" }));
    Object.keys(ecus).sort().forEach(function (k) {
      var label = k === "none" ? "no route" : k === "?" ? "not in event" : k;
      ecuSel.appendChild(el("option", { value: k, text: label + " (" + ecus[k] + ")" }));
    });
    svcSel.appendChild(el("option", { value: "all", text: "All services" }));
    Object.keys(svcs).sort().forEach(function (k) {
      svcSel.appendChild(el("option", { value: k, text: (k === "?" ? "no request" : "0x" + k.toUpperCase()) + " (" + svcs[k] + ")" }));
    });
    ecuSel.value = view.ecu;
    svcSel.value = view.service;

    var fs = document.getElementById("f-outcome");
    var counts = {};
    all.forEach(function (e) { counts[e.outcome] = (counts[e.outcome] || 0) + 1; });
    OUTCOMES.forEach(function (o) {
      var id = "o-" + o;
      var input = el("input", { type: "checkbox", id: id, value: o, checked: view.outcomes.indexOf(o) >= 0 });
      fs.appendChild(el("label", { cls: "chip chip--" + o, "for": id }, [input, el("span", { text: OUTCOME_LABEL[o] }), el("span", { cls: "chip__n", text: String(counts[o] || 0) })]));
    });

    ecuSel.addEventListener("change", function () { view.ecu = ecuSel.value; renderLog(); });
    svcSel.addEventListener("change", function () { view.service = svcSel.value; renderLog(); });
    fs.addEventListener("change", function () {
      view.outcomes = OUTCOMES.filter(function (o) { return document.getElementById("o-" + o).checked; });
      renderLog();
    });
  }

  function syncControls() {
    var pause = document.getElementById("btn-pause");
    pause.setAttribute("aria-pressed", String(view.paused));
    pause.textContent = view.paused ? "Resume view" : "Pause view";
    document.getElementById("f-ecu").value = view.ecu;
    document.getElementById("f-service").value = view.service;
    OUTCOMES.forEach(function (o) { document.getElementById("o-" + o).checked = view.outcomes.indexOf(o) >= 0; });
  }

  function wireControls() {
    document.getElementById("btn-pause").addEventListener("click", function () {
      view.paused = !view.paused; syncControls(); renderLog();
    });
    document.getElementById("btn-clear").addEventListener("click", function () {
      var all = events();
      view.clearedAfter = all.length ? all[all.length - 1].seq : 0;
      renderLog();
    });
    document.getElementById("log-panel").addEventListener("click", function (ev) {
      var t = ev.target;
      if (!(t instanceof HTMLElement)) return;
      if (t.id === "btn-restore") { view.clearedAfter = null; renderLog(); document.getElementById("btn-clear").focus(); }
      else if (t.id === "btn-reset") {
        view.ecu = "all"; view.service = "all"; view.outcomes = OUTCOMES.slice(); syncControls(); renderLog();
        document.getElementById("f-ecu").focus();
      } else if (t.dataset.expand) {
        var key = t.dataset.expand;
        view.expanded[key] = !view.expanded[key];
        renderLog();
        var again = document.querySelector('[data-expand="' + key + '"]');
        if (again) again.focus();
      }
    });
  }

  function readHash() {
    var h = (location.hash || "").replace(/^#/, "");
    h.split("&").forEach(function (part) {
      if (!part) return;
      var kv = part.split("="), k = decodeURIComponent(kv[0]), v = kv.length > 1 ? decodeURIComponent(kv[1]) : "";
      if (k === "ecu") view.ecu = v;
      else if (k === "service") view.service = v.toLowerCase().replace(/^0x/, "");
      else if (k === "outcome") view.outcomes = v.split(",").filter(function (o) { return OUTCOMES.indexOf(o) >= 0; });
      else if (k === "paused") view.paused = true;
      else if (k === "cleared") view.clearedAfter = events().slice(-1)[0].seq;
      else if (k === "tail") view.tail = true;
    });
  }

  function renderBanner() {
    var m = D.meta;
    var detail = document.getElementById("banner-detail");
    detail.textContent = "";
    detail.appendChild(document.createTextNode(
      "M3a mockup. Real simulator output from commit " + m.commit.slice(0, 7) + ", profile " + m.profile.split("/").pop() +
      ", captured " + m.captured_at.slice(0, 10) + ", replayed statically. Rows tagged "));
    detail.appendChild(synTag());
    detail.appendChild(document.createTextNode(" are hand-made."));
  }

  if (!D) {
    document.body.insertBefore(el("p", { cls: "fatal", text: "sample-data.js did not load; nothing to show." }), document.body.firstChild);
    return;
  }
  readHash();
  renderBanner();
  renderStatus();
  renderVehicle();
  renderDtcs();
  renderFilters();
  wireControls();
  syncControls();
  renderLog();
  if (view.tail) { var w = document.getElementById("logwrap"); w.scrollTop = w.scrollHeight; }
})();
