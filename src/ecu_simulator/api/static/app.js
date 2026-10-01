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
  var EPISODE_ATTEMPTS = 3;          // a recovery episode's budget (M3b §8.5): attempts after...
  var EPISODE_FIRST_MS = 1000;       // ...1 s, then 2 s, then 4 s; never reset except by a recovery
  var MAX_ROWS = 2000;              // exchange rows this page keeps; older ones leave the view
  var RENDER_MIN_MS = 200;           // the log re-renders at most this often
  var HEX_PREVIEW_BYTES = 6;
  var USER_SCROLL_MS = 1000;         // a scroll this soon after the reader's own input is theirs
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
    "engine.runtime": "s", "engine.fuel_level": "%", "engine.fuel_type": "code", "engine.rpm": "rpm"
  };

  // ---------- state ----------
  // Health is two variables (M3b §8.4). S.conn is the connection: polling and reconnecting
  // depend on it alone. S.data is "current" when no recovery requirement is pending and
  // "last-known" while one is (S.req); only applying the state the requirement asks for
  // clears it. Time without messages changes neither.
  var S = {
    conn: "loading",         // loading | live | down | refused
    // The pending recovery requirement, or null. Kinds: connect (this page's data has not yet
    // come from socket `sock`), malformed (a socket after `faultSock` must deliver it), encoding
    // (a socket after `okSock`, the last socket before /status read ok true, must deliver it).
    req: { connect: true, sock: null },
    dataAt: null,            // ms: when the page last applied a valid state
    ep: null,                // a recovery episode: { state: active | exhausted, attempts, inFlight, last }
    sock: null, sockSeq: 0,  // the current socket's record { id, gen, run, spoiled }; sockets opened so far
    malformed: 0, malformedAt: null, polls: 0, pollGen: null,
    reason: null,            // why the latest attempt failed
    cause: null,             // why the page stopped being live
    lastLive: null,          // ms: when data last arrived from a live connection
    downAt: null,            // ms: when the page stopped being live
    attempt: 0, retryAt: null, retryTimer: null, pollTimer: null,
    ws: null, wsOpenedAt: null, hello: null, lastClose: null,
    gen: 0, connecting: false,       // the current attempt's token; an attempt is under way
    runStartedAt: null,
    status: null, statusAt: null, vehicle: null, dtcs: null, ecus: null,
    dropped: null,           // this connection's latest `dropped` message
    lastSeq: null,           // highest seq received, over every event, filtered or not
    entries: [], nextId: 1, exCount: 0, trimmed: 0, trimmedGaps: 0, trimmedNotes: 0, duplicates: 0,
    counts: { ecu: {}, service: {}, outcome: {} }
  };
  var view = {
    ecu: "all", service: "all", outcomes: OUTCOMES.slice(),
    paused: false, pauseAfter: 0, clearedAfter: 0, expanded: {},
    // The log follows the newest row until the reader scrolls away from it. Only the reader's
    // own input (wheel, touch, keys, pointer on the log) stops following; a layout change never does.
    follow: true, shownRows: [], shownIds: [], userInputAt: 0, followFrame: 0,
    layoutTop: null,                 // a scroll position the layout forced, not the reader
    // The log draws a window of LOG_WINDOW matching exchanges. endId null: following, the window
    // ends at the newest one. An id: pinned there, so rows never move under a reader; newer
    // exchanges are counted, not drawn. lastId and counts are the latest render's.
    endId: null, lastId: null, counts: null,
    pinFirst: null,                  // a pinned window's oldest drawn exchange at the last render
    leftCap: false                   // the rows a pinned reader was viewing left the page's cap
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
  function isLive() { return S.conn === "live"; }
  function isStale() { return S.conn === "down" || S.conn === "refused"; }
  function isObject(v) { return v != null && typeof v === "object" && !Array.isArray(v); }
  function hasFault() { return !!S.req && !!(S.req.malformed || S.req.encoding); }
  function reasonOf() {
    var r = S.req;
    if (!r) return "";
    if (!r.malformed && !r.encoding) return "connecting";
    return [r.malformed ? "malformed" : null, r.encoding ? "encoding" : null].filter(Boolean).join(" ");
  }
  function episodeActive() { return !!S.ep && S.ep.state === "active"; }

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
  // One attempt at a time. S.gen changes whenever an attempt starts or the page goes down, so a
  // fetch, socket or poll left over from an earlier attempt sees a stale token and does nothing.
  // A recovery episode's resync closes the socket while S.conn stays as it was, so the guard is
  // on the socket, not on the connection state.
  function connect() {
    if (S.connecting || S.ws) return;          // one attempt at a time, and none while a socket is open
    S.connecting = true;
    var gen = ++S.gen;
    clearRetry();
    if (episodeActive()) { S.ep.attempts += 1; S.ep.inFlight = true; }
    renderLink();
    getJSON("/status").then(function (status) {
      if (gen !== S.gen) return null;
      var restarted = S.runStartedAt != null && status.started_at !== S.runStartedAt;
      if (restarted) {
        addMark("link", "Simulator restarted.", "A new run started at " + utc(status.started_at * 1000) +
          ". Rows above are from the previous run; seq starts again at 1.");
        S.lastSeq = null; S.dropped = null;
        // A restart ends any recovery episode: the new run starts with a connect requirement.
        S.ep = null; S.req = { connect: true, sock: null };
      }
      S.runStartedAt = status.started_at;
      if (restarted) graphsRestart(status.started_at);
      if (!readEncoding(status)) {
        // An encoding episode's attempt ends when /status still says the state cannot be built.
        if (episodeActive()) { fail("the simulator still reports that its full state cannot be encoded (state_encoding.ok false)", false); return null; }
      }
      var first = S.vehicle == null || restarted;
      // While stale, the views keep their data as of the drop; a fresh status waits for the hello,
      // unless this is the first data, or the first of a new run.
      if (first || !isStale()) { S.status = status; S.statusAt = Date.now(); }
      return (first ? Promise.all([getJSON("/vehicle"), getJSON("/dtcs"), getJSON("/ecus")]) : Promise.resolve(null))
        .then(function (initial) {
          if (gen !== S.gen) return;
          if (initial) {
            // Every view now holds data fetched at this moment: the stale marking says so.
            S.lastLive = Date.now();
            if (isStale()) S.downAt = S.lastLive;
            S.ecus = initial[2];
            applyState(initial[0], initial[1], true);
            renderAll();
          }
          openSocket(status.api.refused_clients, gen);
        });
    }).catch(function (err) {
      if (gen !== S.gen) return;
      fail(fetchReason(err, "status request failed"), err instanceof HttpError && (err.status === 421 || err.status === 403));
    });
  }

  // Reads state_encoding from a /status answer, at connect or in a poll (M3b §8.4). Returns
  // false when the simulator says its latest full state failed to build or encode.
  function readEncoding(status) {
    var enc = status.api && status.api.state_encoding;
    if (enc && enc.ok === false) {
      // Not yet pending: the fault starts it. Already pending: the requirement is neither started
      // nor restarted, but an earlier ok true no longer counts, so no socket qualifies until the next.
      if (!S.req || !S.req.encoding) fault("encoding");
      else S.req.okSock = null;
      return false;
    }
    // ok true: only sockets opened after this read can carry the state that clears encoding.
    if (S.req && S.req.encoding) S.req.okSock = S.sockSeq;
    return true;
  }

  function closeSocket(code) {
    if (!S.ws) return;
    var ws = S.ws;
    S.ws = null;                        // its handlers now see S.ws !== ws and ignore it
    try { ws.close(code); } catch (e) { /* already closing */ }
  }

  function openSocket(refusedBefore, gen) {
    closeSocket();                      // never leave an earlier socket holding a client slot
    var scheme = location.protocol === "https:" ? "wss:" : "ws:";
    var url = scheme + "//" + location.host + API + "/events" + (S.lastSeq != null ? "?after=" + S.lastSeq : "");
    var ws = new WebSocket(url);
    var sock = { id: ++S.sockSeq, gen: gen, run: S.runStartedAt, spoiled: false };
    S.ws = ws; S.sock = sock; S.wsOpenedAt = null; S.hello = null; S.dropped = null;
    // A handshake that never completes would hold the attempt forever: give it up.
    setTimeout(function () {
      if (S.ws === ws && S.wsOpenedAt == null) fail("the WebSocket handshake did not finish within " + FETCH_TIMEOUT_MS / 1000 + " s", false);
    }, FETCH_TIMEOUT_MS);
    ws.onopen = function () { if (S.ws === ws) S.wsOpenedAt = Date.now(); };
    ws.onmessage = function (ev) { if (S.ws === ws) onMessage(ev.data, sock); };
    ws.onclose = function (ev) {
      if (S.ws !== ws) return;
      S.ws = null;
      if (S.wsOpenedAt == null) { diagnoseRefusal(refusedBefore, gen); return; }
      S.lastClose = ev.code;
      fail(CLOSE_REASON[ev.code] || "the socket closed (" + ev.code + (ev.reason ? " " + ev.reason : "") + ")", false);
    };
  }

  // The browser does not expose the HTTP status of a refused upgrade, so ask /status whether
  // the simulator counted this attempt as a refused client (503 too many clients).
  function diagnoseRefusal(refusedBefore, gen) {
    getJSON("/status").then(function (status) {
      if (gen !== S.gen) return;
      if (status.api.refused_clients > refusedBefore) {
        fail("refused: too many clients (503). The simulator's client limit is reached; " +
          status.api.clients + " are connected now. Close another observer to free a place.", true);
      } else {
        fail("the WebSocket upgrade was refused before it opened, and the simulator did not count it as too " +
          "many clients. The likely cause is the Origin check (403): the page's origin must match the Host " +
          "it was loaded from.", true);
      }
    }).catch(function (err) { if (gen === S.gen) fail(fetchReason(err, "status request failed"), false); });
  }

  // The page's single retry timer, shared by M3a's reconnects and recovery attempts (M3b §8.5).
  // Arming it always clears the previous one; connect() clears it when an attempt starts.
  function armRetry(delay) {
    clearTimeout(S.retryTimer);
    S.retryAt = Date.now() + delay;
    S.retryTimer = setTimeout(function () { S.retryTimer = null; S.retryAt = null; connect(); }, delay);
  }
  function clearRetry() { clearTimeout(S.retryTimer); S.retryTimer = null; S.retryAt = null; }

  function fail(reason, refused) {
    var wasStale = isStale();
    S.gen += 1;                         // ends this attempt and its poll loop
    S.connecting = false;
    // A connection that stayed open STABLE_MS resets the backoff, however it ended.
    if (S.wsOpenedAt != null && Date.now() - S.wsOpenedAt >= STABLE_MS) { S.attempt = 0; S.lastClose = null; }
    S.wsOpenedAt = null;
    closeSocket();
    clearTimeout(S.pollTimer); S.pollTimer = null;
    if (!wasStale) { S.downAt = S.lastLive; S.cause = reason; }
    S.conn = refused ? "refused" : "down";
    S.reason = reason;
    if (!wasStale) healthBreak("down");
    if (S.ep) {
      // During an episode a failure consumes the attempt and arms the episode's next wait;
      // once exhausted, nothing is armed: only "Retry now" starts again.
      if (episodeActive()) episodeAttemptEnded(reason, false);
    } else {
      var delay = refused ? BACKOFF_CAP_MS : Math.min(BACKOFF_START_MS * Math.pow(2, S.attempt), BACKOFF_CAP_MS);
      S.attempt += 1;
      armRetry(delay);
    }
    renderAll();
  }

  // ---------- recovery episodes (M3b §8.5) ----------
  // The resync is a reconnect: close the socket (normal closure) and run connect() after the
  // episode's wait. S.conn is left as it is; the gen change ends the poll loop and any fetch.
  function resyncClose() {
    S.gen += 1;
    S.connecting = false;
    closeSocket(1000);
    S.wsOpenedAt = null;
    clearTimeout(S.pollTimer); S.pollTimer = null;
    // Nothing arrives until the next socket's state: the graphs break here too (a break already
    // pending is kept, so a resync right after a fault adds none).
    healthBreak("resync");
  }
  function startEpisode() {
    S.ep = { state: "active", attempts: 0, inFlight: false, last: null };
    resyncClose();
    armRetry(EPISODE_FIRST_MS);
  }
  // An attempt ended without its state: count it, then wait 2 s, then 4 s, or stop. `open` is
  // true when the attempt's socket is still open (a malformed frame, a state that did not
  // qualify): it is closed for the next attempt, or left open on exhaustion.
  function episodeAttemptEnded(reason, open) {
    S.ep.inFlight = false;
    S.connecting = false;               // the attempt is over, even if its socket stays open
    S.ep.last = reason;
    if (S.ep.attempts >= EPISODE_ATTEMPTS) {
      S.ep.state = "exhausted"; clearRetry();
      // A socket left open keeps the page live: keep polling it, even if its hello never came.
      if (S.ws && isLive() && S.pollGen !== S.gen) poll(0, S.gen);
      return;
    }
    if (open) resyncClose();
    armRetry(EPISODE_FIRST_MS * Math.pow(2, S.ep.attempts));
  }
  // "Retry now" after exhaustion: a new episode with a fresh budget. With no socket the first
  // attempt starts at once; an open socket is closed first and the usual first wait applies.
  function retryNow() {
    if (S.connecting) return;
    var open = !!S.ws;
    S.ep = { state: "active", attempts: 0, inFlight: false, last: null };
    if (open) { resyncClose(); armRetry(EPISODE_FIRST_MS); } else connect();
    renderLink();
  }

  // ---------- health (M3b §8.4) ----------
  // The graphs' rings register here: called with "malformed" (an unreadable frame), "incomplete"
  // (a state without vehicle or dtcs objects), "encoding" or "down" when S.data enters last-known
  // for a fault or S.conn goes down, with "malformed" or "incomplete" for such a message while an
  // encoding fault is pending, and with "resync" when a resync closes the socket, so each ring sets
  // a pending break (§6.7).
  var breakListeners = [];
  function healthBreak(cause) { breakListeners.forEach(function (fn) { fn(cause); }); }

  // A fault replaces a pending connect requirement; malformed and encoding can both be pending,
  // and then both rules must be met. `cause` names the break for the graphs (default: the kind).
  function fault(kind, cause) {
    var entering = !hasFault();
    if (entering) S.req = { malformed: false, encoding: false, faultSock: 0, okSock: null, unreadable: false, incomplete: false };
    S.req[kind] = true;
    if (kind === "malformed") S.req.faultSock = S.sock ? S.sock.id : S.sockSeq;
    else S.req.okSock = null;
    if (entering) healthBreak(cause || kind);
  }

  // A malformed message: a frame that fails JSON.parse or is not an object ("unreadable"), or a
  // recognised state whose vehicle or dtcs is not an object ("incomplete", Task 41). Both are one
  // requirement kind, malformed, with one count and one episode; the wording tells them apart.
  // Counted, never swallowed.
  function onMalformed(sock, what) {
    S.malformed += 1; S.malformedAt = Date.now();
    var encodingOnly = hasFault() && S.req.encoding && !S.req.malformed;
    var cause = what === "incomplete" ? "incomplete" : "malformed";
    sock.spoiled = true;                // a state on this socket never clears a requirement now
    fault("malformed", cause);
    S.req[what] = true;
    if (S.ep) {
      if (episodeActive() && S.ep.inFlight) {
        episodeAttemptEnded(what === "incomplete" ? "the simulator sent an incomplete state message" :
          "the simulator sent a message this page could not read", true);
      }
    } else if (!encodingOnly) {
      startEpisode();
    } else {
      // The socket stays open until the encoding resync; its data is no longer trusted from here.
      healthBreak(cause);
    }
    // With an encoding requirement pending, the resync waits for a poll that reads ok true;
    // that socket is after this fault too, so it meets both rules.
    renderAll();
  }

  // Valid: type state, vehicle and dtcs objects, on the current socket, in the current run.
  function onState(m, sock) {
    if (sock.gen !== S.gen || sock.run !== S.runStartedAt) return;
    // A recognised state that cannot be applied is a data fault, never ignored (Task 41): the
    // page goes last known at once and resyncs, as for an unreadable frame. An attempt that
    // receives it has ended without its state.
    if (!isObject(m.vehicle) || !isObject(m.dtcs)) { onMalformed(sock, "incomplete"); return; }
    applyState(m.vehicle, m.dtcs);
    S.dataAt = Date.now();
    var r = S.req;
    if (r) {
      var qualifies = hasFault() ?
        !sock.spoiled && (!r.malformed || sock.id > r.faultSock) && (!r.encoding || (r.okSock != null && sock.id > r.okSock)) :
        r.sock === sock.id;
      if (qualifies) {
        S.req = null;
        if (r.malformed || r.encoding) {
          addMark("link", r.malformed ? "Resynchronised after " + malformedWhat(r) + "." : "Resynchronised after the simulator's state recovered.",
            "A complete state arrived on a new connection at " + utc(S.dataAt) + "; the views are current again." +
            (S.ep ? " Recovery took " + S.ep.attempts + (S.ep.attempts === 1 ? " attempt." : " attempts.") : ""));
        }
        S.ep = null;                    // a recovery ends the episode and resets the budget
      } else if (episodeActive() && S.ep.inFlight) {
        episodeAttemptEnded("the state it received did not meet the recovery requirement", true);
      }
    }
    renderLink();
  }

  // One status loop per live connection, tied to the generation it started in.
  function poll(delay, gen) {
    clearTimeout(S.pollTimer);
    S.pollGen = gen;                    // the generation whose loop is running
    S.pollTimer = setTimeout(function () {
      if (gen !== S.gen) return;
      getJSON("/status").then(function (status) {
        if (gen !== S.gen || !isLive()) return;
        S.polls += 1;
        S.status = status; S.statusAt = Date.now(); S.lastLive = Date.now();
        poll(STATUS_POLL_MS, gen);
        var pending = S.req && S.req.encoding;
        // ok true with encoding pending starts the resync; it clears nothing by itself. No
        // automatic resync once an episode is exhausted, and none while one is under way.
        if (readEncoding(status) && pending && !S.ep) startEpisode();
        renderStatus(); renderLink();
      }).catch(function (err) {
        if (gen === S.gen && isLive()) fail(fetchReason(err, "status request failed"), false);
      });
    }, delay);
  }

  function onMessage(text, sock) {
    var m;
    try { m = JSON.parse(text); } catch (e) { onMalformed(sock, "unreadable"); return; }
    if (!isObject(m)) { onMalformed(sock, "unreadable"); return; }
    S.lastLive = Date.now();
    if (m.type === "hello") onHello(m, sock);
    else if (m.type === "state") onState(m, sock);
    else if (m.type === "exchange") addExchange(m);
    else if (m.type === "dropped") { S.dropped = m; renderStatus(); }
  }

  function onHello(h, sock) {
    if (h.api !== 1) { fail("this page speaks API v1; the simulator offers v" + h.api, true); return; }
    S.hello = h;
    var resumed = S.lastSeq != null;
    if (!resumed) {
      // An empty history means nothing has been published (watermark 0): resume from there, so
      // exchanges evicted while this page is away later show as a gap, not as a fresh start.
      if (h.oldest_seq == null) S.lastSeq = h.watermark;
      if (h.oldest_seq != null && h.oldest_seq > 1) {
        addMark("note", "History starts at seq " + h.oldest_seq + ".",
          "Seq 1–" + (h.oldest_seq - 1) + " left the simulator's history before this page connected.");
      }
    } else {
      // A resync is not a lost connection: its own line follows when the state is applied.
      if (!S.ep) {
        addMark("link", "Connection lost, then resumed.", "Last live " + (S.downAt ? utc(S.downAt) : "unknown") +
          ", resumed " + utc(Date.now()) + " after seq " + S.lastSeq + ".");
      }
      if (h.watermark < S.lastSeq) {
        addGap(1, h.watermark, "The simulator's seq went back to " + h.watermark + ": a new run began. Its earlier exchanges were not received.");
        S.lastSeq = h.watermark;
      } else if (h.gap && h.oldest_seq != null) {
        addGap(S.lastSeq + 1, h.oldest_seq - 1, "Evicted from the simulator's history while this page was disconnected.");
        S.lastSeq = h.oldest_seq - 1;
      }
    }
    S.connecting = false;
    S.conn = "live"; S.reason = null; S.cause = null; S.downAt = null;
    // Every new socket starts a connect requirement, unless a fault's requirement is pending.
    if (!hasFault()) S.req = { connect: true, sock: sock.id };
    if (ESCALATING_CLOSES.indexOf(S.lastClose) < 0) S.attempt = 0;
    poll(0, S.gen);
    renderAll();
  }

  // ---------- data ----------
  // `rest` marks the first data of a run, from GET /vehicle and /dtcs. The graphs skip it: it is
  // read live, so it can be newer than the published state that follows hello, and that state's
  // as_of would then go back and read as a new run (§6.8). The graphs start from the first state.
  function applyState(vehicle, dtcs, rest) {
    S.vehicle = vehicle; S.dtcs = dtcs;
    renderVehicle(); renderDtcs();
    if (!rest) graphsApply(vehicle);
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
      if (old.kind === "gap") S.trimmedGaps += 1;
      else if (old.kind !== "ex") S.trimmedNotes += 1;
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

  // ---------- signal graphs (M3b §5.3, §6, §7, §8.4, §9) ----------
  // One ring per graphed signal, in scenario time (as_of). A point is stored only when the value
  // changes, or around a gap; a gap is a NaN point, drawn as a break. Drawing is coalesced into
  // one animation frame and only reads the rings; uPlot is told only the drawn window.
  var GRAPHS = [
    { path: "vehicle.speed", name: "Speed", y: "max", floor: 20 },
    { path: "engine.rpm", name: "Engine speed", y: "max", floor: 1000 },
    { path: "engine.throttle", name: "Throttle", y: "pct" },
    { path: "engine.engine_load", name: "Engine load", y: "pct" },
    { path: "engine.coolant_temp", name: "Coolant", y: "temp" }
  ];
  var RING_CAP = 4096;               // points per signal; 10 min at 4 Hz is 2,400
  var HORIZON_S = 600;               // scenario seconds kept, plus the one older point that holds into it
  var WINDOWS = [30, 120, 600];
  var WINDOW_KEY = "ecu-simulator.graphs.window";
  var BREAK_WHY = { down: "disconnected", malformed: "an unreadable message", incomplete: "an incomplete state message", encoding: "the simulator could not encode its state",
    resync: "reconnecting to resynchronise" };
  var G = {
    ok: typeof uPlot === "function",
    open: true,                      // the section is open on every load; not saved
    paused: false, pausedAt: null,   // the frozen right edge while paused
    win: 120,
    asOf: null,                      // the latest as_of received in this run
    firstT: null,                    // as_of of the first point this page stored in this run
    run: null,                       // started_at of the run the rings belong to
    note: null,                      // the restart note
    restarted: false,                // the rings start at a restart, not at this page's connect
    // A pending break (§6.7): set on a fault or a drop, taken by the next state with a later as_of.
    pending: null,                   // { why } or null
    frame: 0, resizeFrame: 0, built: false, rem: null,
    list: []                         // per graph: { def, ring, fig, value, line2, plot, u, state, ... }
  };

  function readWindow() {
    try {
      var v = Number(window.localStorage.getItem(WINDOW_KEY));
      return WINDOWS.indexOf(v) >= 0 ? v : 120;
    } catch (e) { return 120; }
  }
  function saveWindow(v) {
    try { window.localStorage.setItem(WINDOW_KEY, String(v)); } catch (e) { /* not saved; the choice still applies */ }
  }
  function windowLabel(s) { return s < 60 ? s + " s" : s / 60 + " min"; }
  function round1(x) { return String(Math.round(x * 10) / 10); }
  function tText(t) { return (Math.round(t * 10) / 10).toFixed(1); }

  // `breaks` holds the gaps still within the horizon: { a, b, why } for a drop or fault, { a, b, invalid }
  // for an invalid value; `invalidFrom` is the start of an invalid run still going on.
  function newRing() { return { t: new Float64Array(RING_CAP), v: new Float64Array(RING_CAP), start: 0, count: 0, lastValidT: null, breaks: [], invalidFrom: null, trimmedPaused: false }; }
  function rt(r, i) { return r.t[(r.start + i) % RING_CAP]; }
  function rv(r, i) { return r.v[(r.start + i) % RING_CAP]; }
  // The first logical index whose t is at or after `start`: the count of points before it.
  function firstAtOrAfter(r, start) {
    var lo = 0, hi = r.count;
    while (lo < hi) { var mid = (lo + hi) >> 1; if (rt(r, mid) < start) lo = mid + 1; else hi = mid; }
    return lo;
  }
  function setText(node, text) { if (node.textContent !== text) node.textContent = text; }
  function same(a, b) { return a === b || (a !== a && b !== b); }   // NaN equals NaN here
  function dropOldest(r) {
    r.start = (r.start + 1) % RING_CAP; r.count -= 1;
    if (G.paused) r.trimmedPaused = true;
  }
  // Equal t replaces the last point, so x stays unique.
  function ringPush(r, t, v) {
    if (r.count && rt(r, r.count - 1) === t) { r.v[(r.start + r.count - 1) % RING_CAP] = v; return; }
    if (r.count === RING_CAP) dropOldest(r);
    var i = (r.start + r.count) % RING_CAP;
    r.t[i] = t; r.v[i] = v; r.count += 1;
  }
  // Older than the horizon: dropped, except the newest of them, which holds into the window.
  function ringTrim(r, asOf) {
    var horizon = asOf - HORIZON_S;
    while (r.count >= 2 && rt(r, 1) < horizon) dropOldest(r);
    while (r.breaks.length && r.breaks[0].b < horizon) r.breaks.shift();
  }
  // One message for one ring. `v` is the value, or NaN when it is invalid.
  function ringApply(r, t, v, brk) {
    var n = r.count, lastT = n ? rt(r, n - 1) : null, lastV = n ? rv(r, n - 1) : null;
    if (brk && n && t > G.asOf) {
      // The hold stops where knowledge stops: the last value at the last as_of known, a gap, then the new value.
      if (lastV === lastV) ringPush(r, G.asOf, lastV);
      ringPush(r, (G.asOf + t) / 2, NaN);
      r.breaks.push({ a: G.asOf, b: t, why: brk });
      lastV = NaN;
    }
    if (v !== v) {
      // Invalid: keep the last valid value up to the last as_of it was known valid, then the gap.
      if (lastV !== null && lastV === lastV && r.lastValidT != null && r.lastValidT > lastT) ringPush(r, r.lastValidT, lastV);
      if (lastV === null || !same(lastV, v)) ringPush(r, t, NaN);
      if (r.invalidFrom == null) r.invalidFrom = r.lastValidT != null ? r.lastValidT : t;
    } else {
      if (lastV === null || !same(lastV, v)) ringPush(r, t, v);
      if (r.invalidFrom != null) { r.breaks.push({ a: r.invalidFrom, b: t, invalid: true }); r.invalidFrom = null; }
      r.lastValidT = t;
    }
  }

  function graphsBuild() {
    var grid = $("graphs-grid");
    G.win = readWindow();
    if (!G.ok) {
      $("graphs-controls").hidden = true;
      $("graphs-meta").hidden = true;
      $("btn-graphs-toggle").hidden = true;    // nothing to hide: the section is only this sentence
      var note = $("graphs-note");
      note.hidden = false;
      note.textContent = "Graphs unavailable: the chart library did not load. The rest of the page works without it.";
      return;
    }
    GRAPHS.forEach(function (def) {
      var value = el("b", { cls: "graph__value" }), line2 = el("p", { cls: "graph__line2" });
      var plot = el("div", { cls: "graph__plot", "aria-hidden": "true" });
      var unit = UNITS[def.path];
      var fig = el("figure", { cls: "graph", "data-path": def.path, "data-cap": String(RING_CAP), hidden: true }, [
        el("figcaption", { cls: "graph__line1", title: def.path }, [el("span", { cls: "graph__name", text: def.name + (unit ? " · " + unit : "") }), value]),
        plot, line2
      ]);
      grid.appendChild(fig);
      G.list.push({ def: def, ring: newRing(), fig: fig, value: value, line2: line2, plot: plot, u: null,
        state: "waiting", xr: [0, 1], yr: [0, 1], drawnTo: null, left: null, segments: 0, gaps: 0, shown: null });
    });
    G.built = true;
    $("graphs-panel").addEventListener("click", function (ev) {
      var t = ev.target;
      if (!(t instanceof HTMLElement)) return;
      if (t.dataset.window) setWindow(Number(t.dataset.window));
      else if (t.id === "btn-graphs-pause") setGraphsPaused(!G.paused);
      else if (t.id === "btn-graphs-toggle") setGraphsOpen(!G.open);
    });
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(scheduleResize);
      ro.observe(grid);
      G.list.forEach(function (g) { ro.observe(g.plot); });
    }
    breakListeners.push(function (cause) {
      // Taken by the next state with a later as_of, on whichever socket: a drop changes S.gen and a
      // malformed frame closes the socket at once, so no old-socket state follows those; during an
      // encoding fault the first good state can come on the same socket (§8.3), and it must take the
      // break. With no data yet, nothing breaks.
      if (!G.pending && G.asOf != null) G.pending = { why: BREAK_WHY[cause] || cause };
    });
    syncGraphControls();
  }

  // A new run, or an as_of that went back: the graphs are cleared, never joined across runs.
  // `back` is [from, to] when scenario time went back without a new started_at (the note gives
  // only the time it happened, in the owner's short wording).
  function graphsRestart(startedAt, back) {
    if (!G.built) return;
    G.list.forEach(function (g) { g.ring = newRing(); g.drawnTo = null; g.left = null; });
    G.asOf = null; G.firstT = null; G.pending = null; G.restarted = true;
    G.pausedAt = null;                  // while paused, the first as_of of the new run freezes the view
    G.run = startedAt;
    // The owner's wording (Task 37): short, so it shares one line with a gap note at 1440.
    G.note = back ? "Scenario time went back at " + utc(Date.now()) + "; previous graph history cleared." :
      "Simulator restarted at " + utc((startedAt != null ? startedAt * 1000 : Date.now())) + "; previous graph history cleared.";
    renderGraphsNote();
    scheduleDraw(true);
  }

  // Every applied vehicle snapshot reaches here. Values become numbers or NaN; the text matches
  // the signal table.
  function graphsApply(v) {
    if (!G.built) return;
    var nonfinite = Array.isArray(v.nonfinite) ? v.nonfinite : [];
    var missing = Array.isArray(v.unavailable) ? v.unavailable : [];
    var signals = isObject(v.signals) ? v.signals : {};
    var t = typeof v.as_of === "number" && isFinite(v.as_of) ? v.as_of : null;
    var sc = S.status && S.status.scenario;
    if (G.run == null) G.run = S.runStartedAt;
    if (t != null && G.asOf != null && t < G.asOf) graphsRestart(S.runStartedAt, [G.asOf, t]);   // cannot happen within a run
    var brk = null;
    if (t != null && G.pending && G.asOf != null && t > G.asOf) {
      brk = G.pending.why; G.pending = null;
    }
    G.list.forEach(function (g) {
      var p = g.def.path, raw = signals[p];
      if (missing.indexOf(p) >= 0) g.state = "unavailable";
      else if (!(p in signals)) g.state = "absent";
      else if (t == null) g.state = sc && sc.enabled ? "waiting" : sc ? "no-scenario" : "waiting";
      else g.state = nonfinite.indexOf(p) >= 0 || !(typeof raw === "number" && isFinite(raw)) ? "invalid" : "ok";
      if (t != null && (g.state === "ok" || g.state === "invalid")) {
        ringApply(g.ring, t, g.state === "ok" ? raw : NaN, brk);
      }
      if (t != null) ringTrim(g.ring, t);
    });
    var freeze = false;
    if (t != null) {
      G.asOf = t;
      if (G.firstT == null) G.firstT = t;
      // Paused before any data, or across a restart: the first as_of is where the view freezes.
      if (G.paused && G.pausedAt == null) { G.pausedAt = t; freeze = true; }
    }
    renderGraphsNote();
    if (!G.paused || freeze) G.list.forEach(function (g) { g.shown = signalText(g.def.path, v); renderLine1(g); });
    G.list.forEach(writeGraphAttrs);
    scheduleDraw(freeze);
  }

  // The words of the signal table's value cell, for the table and the graph's line 1 alike (§8.4).
  function signalText(p, v) {
    if ((Array.isArray(v.unavailable) ? v.unavailable : []).indexOf(p) >= 0) return "—";
    var raw = v.signals ? v.signals[p] : undefined;
    if (raw === undefined) return "";
    if (isInvalid(p, raw, v)) return "invalid value";
    return fmtValue(raw);
  }
  function isInvalid(p, raw, v) {
    var nonfinite = Array.isArray(v.nonfinite) ? v.nonfinite : [];
    return nonfinite.indexOf(p) >= 0 || raw === null || (typeof raw === "number" && !isFinite(raw));
  }

  function renderLine1(g) {
    var text = g.shown == null ? "" : g.shown;
    if (g.value.textContent !== text) g.value.textContent = text;
    var cls = "graph__value" + (g.state === "invalid" || g.state === "unavailable" ? " graph__value--na" : "");
    if (g.value.className !== cls) g.value.className = cls;
  }

  // The section's own lines: signals not on this vehicle, and the missing time. The restart note
  // is on the shared notice line (breaksText), beside any gap.
  function renderGraphsNote() {
    var lines = [];
    // The restart note stays while the window still reaches back to the new run's start.
    if (G.note && G.firstT != null && G.asOf != null && G.asOf - G.firstT >= G.win) G.note = null;
    var absent = G.list.filter(function (g) { return g.state === "absent"; }).map(function (g) { return g.def.path; });
    if (absent.length) lines.push("Not on this vehicle" + (S.vehicle && S.vehicle.kind ? " (" + S.vehicle.kind + ")" : "") + ": " + absent.join(", ") + ".");
    var states = G.list.map(function (g) { return g.state; });
    var noTime = states.indexOf("no-scenario") >= 0 ? "No scenario: the values are constant, as configured. Graphs follow scenario time." :
      states.indexOf("waiting") >= 0 && S.vehicle ? "Waiting for the first scenario tick." : null;
    if (noTime) lines.push(noTime);
    var note = $("graphs-note");
    if (note.hidden !== !lines.length) note.hidden = !lines.length;
    setText(note, lines.join(" "));
    G.list.forEach(function (g) {
      var hide = g.state === "absent" || g.state === "waiting" || g.state === "no-scenario";
      if (g.fig.hidden !== hide) { g.fig.hidden = hide; scheduleResize(); }
      var na = g.state === "unavailable";
      if (g.fig.classList.contains("graph--na") !== na) g.fig.classList.toggle("graph--na", na);
      if (na) setText(g.line2, "unavailable, no source");
    });
    renderGraphsMeta();
  }

  function renderGraphsMeta() {
    if (!G.ok) return;
    var text = "";
    if (G.paused) text = "Paused at t = " + (G.pausedAt != null ? tText(G.pausedAt) : "—") + " s";
    else if (G.firstT != null && G.asOf != null && G.asOf - G.firstT < G.win) {
      text = "history starts at t = " + tText(G.firstT) + " s (" + (G.restarted ? "when this run started" : "when this page connected") + ")";
    }
    setText($("graphs-status"), text);
    setText($("graphs-fine"), (text ? " · " : "") + "x: scenario t, s · newest " + HORIZON_S / 60 + " min kept");
    var meta = $("graphs-meta"), title = "The horizontal axis is scenario time (as_of), in seconds. The page keeps the newest " +
      HORIZON_S / 60 + " min of each signal; older history is discarded.";
    if (meta.title !== title) meta.title = title;
  }

  function setWindow(w) {
    if (WINDOWS.indexOf(w) < 0) return;
    G.win = w; saveWindow(w);
    syncGraphControls(); renderGraphsMeta();
    scheduleDraw(true);
  }
  function setGraphsPaused(on) {
    G.paused = on;
    G.pausedAt = on ? G.asOf : null;
    if (!on) {
      // Resume jumps to the latest data, not where the pause began.
      G.list.forEach(function (g) {
        g.ring.trimmedPaused = false;
        if (S.vehicle) g.shown = signalText(g.def.path, S.vehicle);
        renderLine1(g);
      });
    }
    syncGraphControls(); renderGraphsMeta();
    G.list.forEach(writeGraphAttrs);
    scheduleDraw(true);
  }
  function setGraphsOpen(on) {
    G.open = on;
    $("graphs").hidden = !on;
    $("graphs-controls").hidden = !on;
    $("graphs-meta").hidden = !on;
    $("graphs-closed").hidden = on;
    syncGraphControls();
    if (on) scheduleDraw(true);
  }
  function syncGraphControls() {
    Array.prototype.forEach.call(document.querySelectorAll("#graphs-panel [data-window]"), function (b) {
      b.setAttribute("aria-pressed", String(Number(b.dataset.window) === G.win));
    });
    var pause = $("btn-graphs-pause");
    pause.setAttribute("aria-pressed", String(G.paused));
    pause.textContent = G.paused ? "Resume graphs" : "Pause graphs";
    var toggle = $("btn-graphs-toggle");
    toggle.setAttribute("aria-expanded", String(G.open));
    toggle.textContent = G.open ? "Hide graphs" : "Show graphs";
  }

  // Drawing: at most once per animation frame. Hidden draws nothing; paused redraws only the frozen
  // window (a resize, a window change), from the rings.
  function scheduleDraw(force) {
    if (!G.built || !G.open || (G.paused && !force) || G.frame) return;
    G.frame = requestAnimationFrame(function () { G.frame = 0; drawAll(); });
  }
  function scheduleResize() {
    if (!G.built || G.resizeFrame) return;
    G.resizeFrame = requestAnimationFrame(function () { G.resizeFrame = 0; resizeAll(); });
  }
  function resizeAll() {
    if (!G.open) return;
    // The root size follows the viewport; the axes take their font and widths from it, so a new
    // size rebuilds the plots (drawAll makes them again, measured).
    var rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
    if (G.rem != null && rem !== G.rem) {
      G.list.forEach(function (g) { if (g.u) { g.u.destroy(); g.u = null; } });
      G.rem = null;
      drawAll();
      return;
    }
    G.list.forEach(function (g) {
      if (!g.u) return;
      var r = g.plot.getBoundingClientRect(), w = Math.floor(r.width), h = Math.floor(r.height);
      if (w > 0 && h > 0 && (w !== g.u.width || h !== g.u.height)) g.u.setSize({ width: w, height: h });
    });
    if (G.paused) drawAll();
  }

  function drawAll() {
    if (!G.open) return;
    var end = G.paused ? G.pausedAt : G.asOf;
    G.list.forEach(function (g) {
      if (g.fig.hidden || g.state === "unavailable") { writeGraphAttrs(g); return; }
      drawGraph(g, end);
      writeGraphAttrs(g);
    });
    var text = breaksText(end), line = $("graphs-breaks");
    setText(line, text);
    if (line.hidden !== !text) line.hidden = !text;
  }

  // One shared notice line under the head (not in each card, so every card keeps its height): the
  // restart note while it applies, then the latest ended gap inside the drawn window, then every
  // invalid run still open. A drop or fault is the same for every graph; an invalid value names its
  // signal. Each notice is complete; the line wraps rather than cut one. Empty, and hidden, when
  // there is none.
  function breaksText(end) {
    var notices = G.note ? [G.note] : [];
    if (end == null) return notices.join(" · ");
    var start = end - G.win, best = null, who = null, open = [], trimmed = false;
    G.list.forEach(function (g) {
      if (g.fig.hidden || g.state === "unavailable") return;
      var r = g.ring;
      r.breaks.forEach(function (b) {
        if (!(b.b > start && b.a < end)) return;
        if (!best || b.b > best.b || (b.b === best.b && best.invalid && !b.invalid)) { best = b; who = g; }
      });
      // An open run ends "now", so it would always be the latest: it is listed beside, never instead.
      if (r.invalidFrom != null && r.invalidFrom < end) open.push(g);
      if (G.paused && r.trimmedPaused) trimmed = true;
    });
    var parts = [];
    if (best && best.invalid) parts.push(who.def.name + ": invalid value from t = " + tText(best.a) + " to t = " + tText(best.b) + " s");
    else if (best) parts.push("No data from t = " + tText(best.a) + " to t = " + tText(best.b) + " s (" + best.why + ")");
    open.forEach(function (g) {
      parts.push(g.def.name + ": invalid value from t = " + tText(g.ring.invalidFrom) + " to t = " + tText(end) + " s (still invalid)");
    });
    if (trimmed) parts.push("history trimmed while paused");
    return notices.concat(parts).join(" · ");
  }

  // The drawn window [end − W, end]: the value held at its left edge, the points inside it, and
  // the hold to the right edge. Neither added point is stored.
  function drawGraph(g, end) {
    var r = g.ring, xs = [], ys = [];
    var W = G.win;
    if (end != null && r.count) {
      var start = end - W, lo = firstAtOrAfter(r, start);
      var atStart = lo < r.count && rt(r, lo) === start;
      g.left = atStart ? rv(r, lo) : lo > 0 ? rv(r, lo - 1) : null;
      if (lo > 0 && !atStart) { xs.push(start); ys.push(g.left === g.left ? g.left : null); }
      var last = g.left;
      for (var i = lo; i < r.count; i++) {
        var t = rt(r, i);
        if (t > end) break;
        last = rv(r, i);
        xs.push(t); ys.push(last === last ? last : null);
      }
      if (last != null && xs.length && xs[xs.length - 1] < end) { xs.push(end); ys.push(last === last ? last : null); }
    } else {
      g.left = null;
    }
    var min = Infinity, max = -Infinity, segs = 0, gaps = 0;
    for (var k = 0; k < ys.length; k++) {
      var y = ys[k];
      if (y === null) { if (k === 0 || ys[k - 1] !== null) gaps += 1; continue; }
      if (k === 0 || ys[k - 1] === null) segs += 1;
      if (y < min) min = y;
      if (y > max) max = y;
    }
    // Fewer than two points draw nothing: the counts say what is drawn.
    if (xs.length < 2) { xs = []; ys = []; segs = 0; gaps = 0; }
    g.segments = segs; g.gaps = gaps; g.drawnTo = end;
    g.xr = end != null ? [end - W, end] : [0, W];
    g.yr = yRange(g.def, min, max);
    if (!g.u) makePlot(g, [xs, ys]); else g.u.setData([xs, ys]);
    setText(g.line2, line2Text(g, min, max, end));
  }

  function line2Text(g, min, max, end) {
    // Rounded to one decimal so the line fits a card; line 1 carries the value as the table shows it.
    // Only min and max: gaps and trimming are told once, in the shared line (breaksText).
    return isFinite(min) ? "min " + round1(min) + " · max " + round1(max) + " in " + windowLabel(G.win) : "no valid value in " + windowLabel(G.win);
  }

  // §5.3: 0 to a value above the window's maximum (with a floor); a fixed 0–100; or the window's
  // range padded by 2 °C, at least 10 °C wide.
  function niceAbove(x) {
    var p = Math.pow(10, Math.floor(Math.log10(x))), steps = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
    for (var i = 0; i < steps.length; i++) if (steps[i] * p >= x) return steps[i] * p;
    return 10 * p;
  }
  function yRange(def, min, max) {
    if (def.y === "pct") return [0, 100];
    if (def.y === "max") return [0, isFinite(max) && max * 1.1 > def.floor ? niceAbove(max * 1.1) : def.floor];
    if (!isFinite(min)) return [0, 10];
    var lo = min - 2, hi = max + 2;
    if (hi - lo < 10) { var c = (lo + hi) / 2; lo = c - 5; hi = c + 5; }
    return [Math.floor(lo), Math.ceil(hi)];
  }

  function makePlot(g, data) {
    var root = getComputedStyle(document.documentElement), body = getComputedStyle(document.body);
    var rem = parseFloat(root.fontSize) || 16;
    G.rem = rem;
    var font = Math.round(rem * 0.786 * 10) / 10 + "px " + body.fontFamily;
    var ink = root.getPropertyValue("--ink").trim(), ink2 = root.getPropertyValue("--ink-2").trim(), rule = root.getPropertyValue("--rule").trim();
    var box = g.plot.getBoundingClientRect();
    var axis = { stroke: ink2, font: font, gap: 2, ticks: { size: 3, stroke: rule, width: 1 }, grid: { stroke: rule, width: 1 } };
    g.u = new uPlot({
      width: Math.max(1, Math.floor(box.width)), height: Math.max(1, Math.floor(box.height)),
      padding: [Math.ceil(rem * 0.4), Math.ceil(rem * 0.6), 0, 0],
      cursor: { show: false, drag: { x: false, y: false, setScale: false }, points: { show: false } },
      legend: { show: false },
      select: { show: false, left: 0, top: 0, width: 0, height: 0 },
      scales: {
        x: { time: false, range: function () { return g.xr; } },
        y: { range: function () { return g.yr; } }
      },
      axes: [
        Object.assign({ space: 50, size: Math.ceil(rem * 1.45) }, axis),
        Object.assign({ space: Math.ceil(rem * 1.3), size: Math.floor(rem * 3) }, axis)
      ],
      series: [
        {},
        // Step-hold (§6.2); a null is a gap, clipped from the last valid point to the next one (§8.4).
        { stroke: ink, width: 1.5, spanGaps: false, points: { show: false },
          paths: uPlot.paths.stepped({ align: 1, alignGaps: 0 }) }
      ]
    }, data, g.plot);
  }

  // Read-only diagnostics for the browser checks (M3b §12.2): written after each ring update and
  // draw, never read by the page.
  // Each attribute is written only when its value changes.
  function writeGraphAttrs(g) {
    var d = g.fig.dataset, r = g.ring, n = r.count;
    function put(k, v) { if (d[k] !== v) d[k] = v; }
    put("path", g.def.path);
    put("state", g.state);
    put("points", String(n));
    put("cap", String(RING_CAP));
    put("oldestT", n ? String(rt(r, 0)) : "");
    put("newestT", n ? String(rt(r, n - 1)) : "");
    put("asOf", G.asOf != null ? String(G.asOf) : "");
    put("drawnTo", g.drawnTo != null ? String(g.drawnTo) : "");
    put("windowS", String(G.win));
    put("leftValue", g.left != null && g.left === g.left ? String(g.left) : "");
    put("pointsBeforeWindow", String(G.asOf != null ? firstAtOrAfter(r, G.asOf - G.win) : 0));
    put("segments", String(g.segments));
    put("gaps", String(g.gaps));
    put("run", G.run != null ? String(G.run) : "");
    put("paused", String(G.paused));
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
    var root = $("status-polled");
    root.textContent = "";
    var s = S.status;
    $("polled").hidden = !s;
    if (!s) return;
    $("polled-text").textContent = "Polled every " + STATUS_POLL_MS / 1000 + " s from /status" +
      (S.statusAt ? ", last at " + utc(S.statusAt).replace(" UTC", "") : "") + "; may trail the live log";
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
      ]),
      // Shown once the simulator has failed to build its full state at least once (M3b §8.4).
      a.state_encode_failed > 0 ? pairs("State", "drops", [
        ["encode failed", a.state_encode_failed, "state_encode_failed: periodic full-state snapshots that failed to build or encode, since start" +
          (a.state_encoding && a.state_encoding.last_failed_at ? "; latest " + utc(a.state_encoding.last_failed_at * 1000) : ""), true]
      ]) : null
    ].forEach(function (n) { if (n) root.appendChild(n); });
  }

  // "Live" is S.conn live and S.data current (M3b §8.6). Stale (the connection is not live)
  // takes precedence over last known (live, with a recovery requirement pending).
  function renderLink() {
    var conn = $("conn"), text = $("conn-text"), box = $("linkstate");
    var stale = isStale(), reason = reasonOf(), known = !stale && S.req != null && isLive();
    var exhausted = !!S.ep && S.ep.state === "exhausted";
    document.body.classList.toggle("is-stale", stale);
    document.body.classList.toggle("is-known", known);
    var tag = S.lastLive ? "Stale, as of " + utc(S.downAt || S.lastLive) : "No data received";
    Array.prototype.forEach.call(document.querySelectorAll(".stale-tag"), function (t) {
      t.hidden = !stale; t.textContent = tag;
    });
    var knownTag = "Last known" + (S.dataAt ? ", " + utc(S.dataAt) : "");
    Array.prototype.forEach.call(document.querySelectorAll(".known-tag"), function (t) {
      t.hidden = !known; t.textContent = knownTag;
    });
    renderMalformed();
    writeDiagnostics(reason);
    var retry = S.retryAt ? Math.max(0, Math.ceil((S.retryAt - Date.now()) / 1000)) : null;
    if (S.conn === "live" && !S.req) {
      conn.className = "conn conn--live";
      text.textContent = "Live";
      conn.title = "WebSocket open since " + utc(S.wsOpenedAt || Date.now());
    } else if (S.conn === "live") {
      conn.className = "conn conn--known";
      text.textContent = reason === "connecting" ? "Connected, waiting for state" : "Connected, last known data";
      conn.title = reason === "connecting" ? "The connection is open; its first state has not arrived yet." :
        "The views show the last state applied" + (S.dataAt ? ", at " + utc(S.dataAt) : "") + ".";
    } else if (S.conn === "loading") {
      conn.className = "conn conn--loading";
      text.textContent = "Connecting";
      conn.title = "";
    } else {
      conn.className = "conn " + (S.conn === "refused" ? "conn--refused" : "conn--down");
      text.textContent = (S.conn === "refused" ? "Refused" : "Disconnected") +
        (exhausted ? ", not retrying" : retry == null ? ", reconnecting" : ", retry in " + retry + " s");
      conn.title = S.reason || "";
    }
    // The banner: stale, a recovery's cause, or exhaustion. "connecting" lasts one frame: no banner.
    var faultText = faultSentence(exhausted);
    if (!stale && !exhausted && !faultText) { box.hidden = true; return; }
    box.hidden = false;
    box.className = "linkstate" + (S.conn === "refused" && !exhausted ? " linkstate--refused" : !stale ? " linkstate--known" : "");
    // The text is rebuilt every second; the button is not, so a click is never lost to a re-render.
    var p = $("linkstate-text"), button = $("btn-retry");
    if (!p) {
      p = el("p", { id: "linkstate-text" });
      button = el("button", { type: "button", id: "btn-retry" });
      box.append(p, button);
    }
    var episode = episodeActive() ? " Recovery attempt " + S.ep.attempts + " of " + EPISODE_ATTEMPTS +
      (retry == null ? (S.ep.inFlight ? " is under way." : ".") : (S.ep.attempts ? " failed; the next" : "; the first") + " starts in " + retry + " s.") : "";
    if (exhausted) {
      p.replaceChildren(el("b", { text: "Could not recover:" }), " " + (faultText || "the page could not get a complete state.") +
        " The page made " + EPISODE_ATTEMPTS + " attempts to get a complete state and does not try again by itself." +
        (S.ep.last ? " Last attempt: " + S.ep.last + "." : ""));
    } else if (stale) {
      var lead = S.conn === "refused" ? "Connection refused." : "Disconnected.";
      var since = S.downAt ? " Last live " + utc(S.downAt) + " (" + ago(S.downAt) + "). The views below show data as of then." :
        " No data has been received yet.";
      var why = S.cause || S.reason || "unknown";
      if (S.reason && S.cause && S.reason !== S.cause) why = S.cause + (/\.$/.test(S.cause) ? "" : ".") + " Latest retry: " + S.reason;
      p.replaceChildren(el("b", { text: lead }), since + " Reason: " + why + (/\.$/.test(why) ? " " : ". ") +
        (episode ? episode.slice(1) : retry == null ? "Reconnecting now." : "Retrying in " + retry + " s (" + S.attempt + (S.attempt === 1 ? " failed attempt" : " failed attempts") +
          "; the wait doubles from " + BACKOFF_START_MS / 1000 + " s to at most " + BACKOFF_CAP_MS / 1000 + " s)."));
    } else {
      p.replaceChildren(el("b", { text: "Last known data." }), " " + faultText + episode);
    }
    // The button is M3a's "Retry now" while stale, and the only way on after exhaustion.
    // While an episode is active its own timer starts the next attempt: no early attempt by hand.
    button.hidden = (!stale && !exhausted) || episodeActive();
    button.disabled = S.connecting;
    button.textContent = S.connecting ? "Retrying" : "Retry now";
  }

  // What made a malformed requirement: an unreadable message, an incomplete state message, or both.
  function malformedWhat(r) {
    return r.incomplete && r.unreadable ? "an unreadable message and an incomplete state message" :
      r.incomplete ? "an incomplete state message" : "an unreadable message";
  }

  // The banner names the cause of a malformed or encoding requirement; null for none.
  function faultSentence(exhausted) {
    var r = S.req;
    if (!hasFault()) return null;
    var parts = [];
    if (r.malformed) {
      parts.push("the simulator sent " + (r.incomplete && !r.unreadable ? "an incomplete state message" :
        r.incomplete ? "a message this page could not read and an incomplete state message" : "a message this page could not read") +
        " (" + S.malformed + " malformed so far)" +
        (exhausted ? "." : ", so the page reconnects to get a complete state."));
    }
    if (r.encoding) {
      var n = S.status && S.status.api ? S.status.api.state_encode_failed : null;
      parts.push("the simulator could not build its full state (state_encode_failed " + (n == null ? "—" : n) + ")." +
        (exhausted ? "" : r.okSock != null ? " GET /status reports state_encoding ok again; the page reconnects to get the recovered state." :
          " The page keeps polling and reconnects once GET /status reports state_encoding ok."));
    }
    var text = parts.join(" Also, ");
    return text.charAt(0).toUpperCase() + text.slice(1) + " The views show the last state applied" +
      (S.dataAt ? ", at " + utc(S.dataAt) : "") + ".";
  }

  function renderMalformed() {
    var box = $("malformed");
    box.hidden = S.malformed === 0;
    if (!S.malformed) return;
    $("malformed-text").replaceChildren(el("b", { text: String(S.malformed) }), ", last " + utc(S.malformedAt));
  }

  // Read-only diagnostics for the browser checks (M3b §12.2): written here, never read by the page.
  function writeDiagnostics(reason) {
    var d = document.body.dataset;
    d.conn = S.conn;
    d.data = S.req ? "last-known" : "current";
    d.reason = reason;
    d.health = S.conn !== "live" ? "stale" : S.req ? "last-known" : "live";
    d.episode = S.ep ? S.ep.state : "none";
    d.attempts = String(S.ep ? S.ep.attempts : 0);
    d.timers = String(S.retryTimer != null ? 1 : 0);   // the single S.retryTimer slot; only armRetry() schedules it
    d.polls = String(S.polls);
    d.malformedTotal = String(S.malformed);
  }

  // ---------- rendering: vehicle ----------
  function renderVehicle() {
    var v = S.vehicle, body = $("vehicle");
    if (!v) return;
    $("vehicle-meta").textContent = v.as_of == null ? "no scenario: values as configured" : "as of scenario t = " + v.as_of.toFixed(2) + " s";
    // The VIN is shown once, in full, in the panel header (kind, VIN) above the table, so its
    // signal row is left out. Presentation only: the API still carries vehicle.vin in signals.
    var paths = Object.keys(v.signals || {}).filter(function (p) { return p !== "vehicle.vin"; });
    // 0010 §5, ninth revision: paths with no source in the profile. Their stored value is a
    // default, not a measurement, so it is never shown; the list is fixed for a run.
    var missing = {};
    (Array.isArray(v.unavailable) ? v.unavailable : []).forEach(function (p) { missing[p] = true; });
    var key = v.kind + "|" + v.vin + "|" + paths.join(",") + "|" + Object.keys(missing).join(",");
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
          var name = el("th", { scope: "row", cls: "mono sig__name", title: p }, [p.slice(g.length ? g.length + 1 : 0) || p]);
          if (missing[p]) {
            // Text, not colour alone: "—" and the words, live and stale alike. The unit is hidden.
            // The words sit under the name, the widest column, so the narrow sidebar does not scroll.
            vehicleCells[p] = { td: null, raw: undefined };
            name.appendChild(el("span", { cls: "sig__na", text: "unavailable, no source" }));
            tb.appendChild(el("tr", { cls: "sig--na", title: p + ": no source in this profile. No profile field or scenario generator sets it, so the stored default is not shown." }, [
              name, el("td", { cls: "num sig__na-value", text: "—" }), el("td", { cls: "sig__unit" })
            ]));
            return;
          }
          var td = el("td", { cls: "num" });
          vehicleCells[p] = { td: td, raw: undefined };
          tb.appendChild(el("tr", null, [name, td, el("td", { cls: "sig__unit", text: UNITS[p] || "" })]));
        });
        table.appendChild(tb);
      });
      body.appendChild(table);
      body.appendChild(el("p", { cls: "fineprint", text: "Units are a display map from the bundled profiles' comments, not API data. Values are rounded to two decimals; hover for the raw value." +
        (Object.keys(missing).length ? " \u201cunavailable, no source\u201d: nothing in this profile sets the signal, so its stored default is not a reading." : "") }));
    }
    paths.forEach(function (p) {
      var cell = vehicleCells[p], raw = v.signals[p];
      if (!cell.td || cell.raw === raw) return;
      var first = cell.raw === undefined;
      cell.raw = raw;
      // Not a finite number (sent as null, listed in nonfinite): "invalid value", as in the graph (M3b §8.4).
      var invalid = isInvalid(p, raw, v), shown = signalText(p, v);
      cell.td.textContent = shown;
      cell.td.className = "num" + (typeof raw === "string" ? " mono" : "") + (invalid ? " sig__invalid" : "");
      if (invalid) cell.td.title = "The simulator's value is not a finite number.";
      else if (shown !== String(raw)) cell.td.title = "raw " + raw; else cell.td.removeAttribute("title");
      if (!first) { void cell.td.offsetWidth; cell.td.classList.add("changed"); }
    });
  }

  // ---------- rendering: DTCs ----------
  function flag(on, label) {
    // A mark only (filled: yes, hollow: no), so the table fits the sidebar; the words are
    // there for screen readers and in the tooltip.
    return el("td", { cls: "flag " + (on ? "flag--on" : "flag--off"), title: label + ": " + (on ? "yes" : "no") }, [
      el("span", { cls: "flag__mark", "aria-hidden": "true" }), el("span", { cls: "vh", text: (on ? "yes" : "no") + ", " + label })
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
          // Short headers, full text in the title, so the table fits the sidebar at every width.
          el("th", { scope: "col", cls: "flag-h" }, [el("abbr", { title: "pending: the code is pending" }, ["Pend."])]),
          el("th", { scope: "col", cls: "flag-h" }, [el("abbr", { title: "confirmed: the code is confirmed" }, ["Conf."])]),
          el("th", { scope: "col", cls: "flag-h" }, [el("abbr", { title: "indicator_requested: the code asks for the MIL" }, ["Lamp"])])
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

  // ---- log selection (pure, no DOM) ----
  // Which rows the exchange log draws: a window of at most `size` exchanges the filters show,
  // chosen after the filters apply to every retained exchange. Everything is passed in, so
  // scripts/gui_log_check.js runs this block alone. Arguments shared by every function here:
  //   entries  exchanges { kind: "ex", e, id } and markers { kind: "gap" | "note" | …, id }, in id order;
  //   matches  the filter predicate, given an exchange's event `e`;
  //   opts     { clearedAfter, pauseAfter (null when not paused), endId, size }: rows up to
  //            clearedAfter are out of view, exchanges after pauseAfter are held; endId null
  //            means following (the window ends at the newest matching exchange), an id pins
  //            the window's end there.
  var LOG_WINDOW = 200;              // matching exchanges the log draws at most
  var LOG_STEP = 100;                // matching exchanges one Older / Newer press moves the window

  // The matching exchanges in view, as entry indices, and where a window ending at
  // opts.endId ends among them: `end` is one past its newest exchange, `upper` the highest id
  // it may hold (Infinity while following). A pin is honoured as given: one older than every
  // matching exchange (its rows left the page's cap, or were cleared) gives an empty window
  // with them all newer; logAnchorEnd moves such a pin to the oldest of them.
  function logPlace(entries, matches, opts) {
    var m = [], end = 0, en;
    for (var i = 0; i < entries.length; i += 1) {
      en = entries[i];
      if (en.kind !== "ex" || en.id <= opts.clearedAfter) continue;
      if (opts.pauseAfter != null && en.id > opts.pauseAfter) break;
      if (!matches(en.e)) continue;
      m.push(i);
      if (opts.endId == null || en.id <= opts.endId) end = m.length;
    }
    return { m: m, end: end, upper: opts.endId == null ? Infinity : opts.endId };
  }

  // The rows to draw and the counts beside them. Returns { items, firstId, lastId, pinBeforeView, counts }:
  // items, in entry order, are { kind: "entry", entry } and { kind: "hidden", n } (a run of
  // exchanges the filters hide, only ever between two drawn rows); firstId / lastId are the
  // oldest / newest drawn exchange (null when none). Markers between them are drawn in place;
  // where no matching exchange lies beyond the window on a side, the window reaches the end
  // of the view on that side and takes its markers too. Every other marker or hidden exchange
  // is counted as older or newer. A pinned window holds only rows up to endId, so rows
  // appended later never change it. counts: inView (exchanges between the clear and pause
  // boundaries), matching, shown, olderMatching, newerMatching, hiddenTotal,
  // hiddenOutside { older, newer }, markersOlder / markersNewer { gaps, notes }, held.
  // pinBeforeView is true when the window is empty only because its pin is stale: a pin is
  // set, exchanges match, and none of them lies at or before it (its rows left the page's
  // cap, Clear view came after it, or a filter's matches all lie after it). The caller then
  // re-pins with logAnchorEnd; an empty window for any other reason leaves it false.
  function selectLog(entries, matches, opts) {
    var p = logPlace(entries, matches, opts), m = p.m;
    var start = Math.max(0, p.end - opts.size);
    var lower = start > 0 ? entries[m[start]].id : -Infinity;
    var c = {
      inView: 0, matching: m.length, shown: p.end - start, olderMatching: start, newerMatching: m.length - p.end,
      hiddenTotal: 0, hiddenOutside: { older: 0, newer: 0 },
      markersOlder: { gaps: 0, notes: 0 }, markersNewer: { gaps: 0, notes: 0 }, held: 0
    };
    var items = [], run = 0, rows = 0, en;
    for (var i = 0; i < entries.length; i += 1) {
      en = entries[i];
      if (en.id <= opts.clearedAfter) continue;
      if (opts.pauseAfter != null && en.id > opts.pauseAfter) { if (en.kind === "ex") c.held += 1; continue; }
      var isEx = en.kind === "ex";
      if (isEx) c.inView += 1;
      var shows = !isEx || matches(en.e);
      if (!shows) c.hiddenTotal += 1;
      if (en.id < lower || en.id > p.upper) {
        if (!shows) c.hiddenOutside[en.id < lower ? "older" : "newer"] += 1;
        else if (!isEx) c[en.id < lower ? "markersOlder" : "markersNewer"][en.kind === "gap" ? "gaps" : "notes"] += 1;
        continue;
      }
      if (!shows) { run += 1; continue; }
      if (run) {
        if (rows) items.push({ kind: "hidden", n: run }); else c.hiddenOutside.older += run;
        run = 0;
      }
      items.push({ kind: "entry", entry: en });
      rows += 1;
    }
    // A run after the last drawn row is newer; with no drawn row at all, every run is older.
    c.hiddenOutside[rows ? "newer" : "older"] += run;
    return {
      items: items,
      firstId: c.shown ? entries[m[start]].id : null,
      lastId: c.shown ? entries[m[p.end - 1]].id : null,
      pinBeforeView: opts.endId != null && p.end === 0 && m.length > 0,
      counts: c
    };
  }

  // Positions in m (matching exchanges, as entry indices): the last whose id is at or before
  // `id` (-1 if none), and the first whose id is at or after it (m.length if none).
  function logAtOrBefore(entries, m, id) {
    var lo = 0, hi = m.length;
    while (lo < hi) { var mid = (lo + hi) >> 1; if (entries[m[mid]].id <= id) lo = mid + 1; else hi = mid; }
    return lo - 1;
  }
  function logAtOrAfter(entries, m, id) {
    var lo = 0, hi = m.length;
    while (lo < hi) { var mid = (lo + hi) >> 1; if (entries[m[mid]].id < id) lo = mid + 1; else hi = mid; }
    return lo;
  }

  // The window end after "Older": `step` matching exchanges back, but never past the oldest
  // full window. Returns opts.endId unchanged (null stays following) when nothing older matches.
  // seen (optional) { topId, bottomId }: the topmost and bottom-most exchanges the reader has in
  // view. Older keeps the top one (the anchor) in the window: the step shrinks if it would drop
  // it. When the window's far end (its newest exchange) is already in view, the step is 0 and
  // opts.endId comes back unchanged: the caller then shows the window's other end instead.
  function logOlderEnd(entries, matches, opts, step, seen) {
    var p = logPlace(entries, matches, opts), k = p.end - 1;
    if (p.end - opts.size <= 0) return opts.endId;
    if (seen && seen.bottomId != null && seen.bottomId >= entries[p.m[k]].id) return opts.endId;
    var to = Math.max(k - step, Math.min(opts.size, p.m.length) - 1);
    if (seen && seen.topId != null) to = Math.max(to, logAtOrBefore(entries, p.m, seen.topId));
    return to < k ? entries[p.m[to]].id : opts.endId;
  }

  // The window end after "Newer": `step` matching exchanges on; null (following again) when
  // that reaches or passes the newest matching exchange, or when already following. seen: as for
  // Older, mirrored. Newer keeps the bottom-most exchange in view in the window (the window still
  // starts at or before it); the step is 0 (opts.endId unchanged) when the window's oldest
  // exchange is already in view.
  function logNewerEnd(entries, matches, opts, step, seen) {
    if (opts.endId == null) return null;
    var p = logPlace(entries, matches, opts), to = p.end - 1 + step;
    var start = Math.max(0, p.end - opts.size);
    if (seen && seen.topId != null && p.end > 0 && seen.topId <= entries[p.m[start]].id) return opts.endId;
    if (seen && seen.bottomId != null) to = Math.min(to, Math.max(p.end - 1, logAtOrAfter(entries, p.m, seen.bottomId) + opts.size - 1));
    if (to <= p.end - 1) return opts.endId;
    return to >= p.m.length - 1 ? null : entries[p.m[to]].id;
  }

  // The window end after a filter change, from the previous pin opts.endId: the newest
  // exchange the new `matches` shows at or before it, else the oldest after it; null
  // (following) when nothing matches or the window was already following.
  function logAnchorEnd(entries, matches, opts) {
    if (opts.endId == null) return null;
    var p = logPlace(entries, matches, opts);
    if (!p.m.length) return null;
    return entries[p.m[Math.max(0, p.end - 1)]].id;
  }

  // A re-pin that leaves a full window: the logAnchorEnd anchor (nearest matching exchange at
  // or before the pin, else the oldest after it), extended forward until the window holds
  // `size` exchanges, or all of them if fewer match. Used when the pin went stale (its rows
  // left the page's cap) and after a filter change; null when following or nothing matches.
  function logFullEnd(entries, matches, opts) {
    if (opts.endId == null) return null;
    var p = logPlace(entries, matches, opts);
    if (!p.m.length) return null;
    return entries[p.m[Math.max(p.end - 1, Math.min(opts.size, p.m.length) - 1)]].id;
  }
  // ---- end log selection ----

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

  function fmtN(n) { return n.toLocaleString("en"); }
  function plural(n, one, many) { return fmtN(n) + " " + (n === 1 ? one : many); }
  function andList(parts) { return parts.length > 1 ? parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1] : parts[0]; }
  function logOpts(endId) {
    return { clearedAfter: view.clearedAfter, pauseAfter: view.paused ? view.pauseAfter : null,
      endId: endId === undefined ? view.endId : endId, size: LOG_WINDOW };
  }

  // Rows beyond the window, worded by kind and never confused: matching exchanges outside this
  // window (Older / Newer reach them), gap markers and connection notes there, and exchanges
  // the filters hide there. `side` is "older" or "newer".
  function beyondParts(c, side) {
    var m = side === "older" ? c.markersOlder : c.markersNewer, hidden = c.hiddenOutside[side], parts = [], marks = [];
    if (m.gaps) marks.push(plural(m.gaps, "gap marker", "gap markers"));
    if (m.notes) marks.push(plural(m.notes, "connection note", "connection notes"));
    if (marks.length) parts.push(andList(marks) + " in " + side + " rows");
    if (hidden) parts.push(plural(hidden, side + " exchange", side + " exchanges") + " hidden by filters (not a gap)");
    return parts;
  }
  // The window's edges, built once and updated in place (stable nodes, text set only when it
  // changes), in their own table bodies around #log-body: the note on rows that left the cap
  // and the Older row above, the Newer row below a pinned window. A focused button there
  // keeps the focus through every routine render. Text, not colour alone.
  var edges = null;
  function edgeNodes() {
    if (edges) return edges;
    function nav(side, buttons) {
      var text = el("p", { cls: "lognav__text" });
      var tr = el("tr", { id: "lognav-" + side, cls: "lognav lognav--" + side, hidden: true }, [el("td", { colspan: "9" }, [
        el("div", { cls: "lognav__in" }, [text, el("div", { cls: "lognav__buttons" }, buttons)])])]);
      return { tr: tr, text: text };
    }
    var older = el("button", { type: "button", cls: "compact", "data-lognav": "older" }, ["Show older exchanges"]);
    var newer = el("button", { type: "button", cls: "compact", "data-lognav": "newer" }, ["Show newer exchanges"]);
    var newest = el("button", { type: "button", cls: "compact", "data-lognav": "newest",
      "aria-label": "Jump to newest exchange and follow new ones" }, ["Jump to newest"]);
    var title = el("span", { cls: "mark__title" }), note = el("span");
    edges = {
      trimmed: el("tr", { id: "log-trimmed", cls: "mark mark--note", hidden: true }, [el("td", { colspan: "9" }, [title, note])]),
      trimmedTitle: title, trimmedText: note,
      older: nav("older", [older]), newer: nav("newer", [newer, newest]), newerButton: newer
    };
    $("log-head").append(edges.trimmed, edges.older.tr);
    $("log-tail").append(edges.newer.tr);
    return edges;
  }
  function showEdge(node, on) { if (node.hidden === on) node.hidden = !on; }
  // What lies beyond the window on one side: the count of matching exchanges there, then the
  // gap / note / hidden counts.
  function edgeText(side, c) {
    var n = side === "older" ? c.olderMatching : c.newerMatching;
    var lead = n ? plural(n, side + " exchange matches", side + " exchanges match") + " your filters"
      : "No " + side + " exchange matches your filters";
    return [lead].concat(beyondParts(c, side)).join("; ") + ".";
  }
  function renderEdges(c) {
    var x = edgeNodes(), trimmed = S.trimmed > 0 && !view.clearedAfter;
    showEdge(x.trimmed, trimmed);
    if (trimmed) {
      var left = [plural(S.trimmed, "older row", "older rows")];
      if (S.trimmedGaps) left.push(plural(S.trimmedGaps, "gap marker", "gap markers"));
      if (S.trimmedNotes) left.push(plural(S.trimmedNotes, "connection note", "connection notes"));
      setText(x.trimmedTitle, andList(left) + " left this view.");
      setText(x.trimmedText, "The page keeps the newest " + fmtN(MAX_ROWS) + " exchanges it received. The rows were received, so their removal is not a gap." +
        (view.leftCap && view.endId != null ? " Rows this view was showing left too, so it moved to the oldest exchanges kept." : ""));
    }
    showEdge(x.older.tr, c.olderMatching > 0);
    if (c.olderMatching) setText(x.older.text, edgeText("older", c));
    var newer = view.endId != null && c.newerMatching + c.markersNewer.gaps + c.markersNewer.notes + c.hiddenOutside.newer > 0;
    showEdge(x.newer.tr, newer);
    if (newer) { setText(x.newer.text, edgeText("newer", c)); showEdge(x.newerButton, c.newerMatching > 0); }
  }
  // Puts `nodes` in `parent` in order, touching only what differs: a row that stays is never
  // removed and re-inserted, so it keeps the focus (and screen readers are not moved).
  function syncChildren(parent, nodes) {
    var want = new Set(nodes), c = parent.firstChild, next;
    while (c) { next = c.nextSibling; if (!want.has(c)) parent.removeChild(c); c = next; }
    c = parent.firstChild;
    nodes.forEach(function (n) { if (n === c) c = c.nextSibling; else parent.insertBefore(n, c); });
  }
  function hiddenRow(n) {
    return el("tr", { cls: "hiddenrow" }, [el("td", { colspan: "9", text: plural(n, "exchange", "exchanges") + " hidden by filters (not a gap)" })]);
  }

  // The log draws a window of at most LOG_WINDOW exchanges the filters show (the selection
  // block above), following the newest or pinned at view.endId. A pinned window keeps the
  // reader's rows where they are on screen. When rows of a pinned window leave the page's cap
  // (its oldest rows, or all of them: a stale pin), the window re-pins to the oldest full
  // window kept (logFullEnd), so it never shrinks to a few rows.
  function renderLog() {
    lastRender = Date.now();
    var wrap = $("logwrap"), body = $("log-body");
    var topBefore = wrap.scrollTop;
    var anchors = view.follow ? [] : visibleAnchors(wrap);
    var focused = logFocused();
    var sel = selectLog(S.entries, passes, logOpts()), c = sel.counts;
    var shrunk = view.endId != null && view.pinFirst != null && sel.firstId != null && sel.firstId > view.pinFirst &&
      c.olderMatching === 0 && c.shown < LOG_WINDOW && c.newerMatching > 0;
    if (sel.pinBeforeView || shrunk) {
      view.endId = logFullEnd(S.entries, passes, logOpts());
      view.leftCap = true;
      sel = selectLog(S.entries, passes, logOpts());
      c = sel.counts;
    }
    view.pinFirst = view.endId != null ? sel.firstId : null;
    view.lastId = sel.lastId;
    view.counts = c;
    var nodes = [], rows = [], ids = [];
    renderEdges(c);
    if (!c.olderMatching && c.hiddenOutside.older && c.shown) nodes.push(hiddenRow(c.hiddenOutside.older));
    sel.items.forEach(function (item) {
      if (item.kind === "hidden") { nodes.push(hiddenRow(item.n)); return; }
      var tr = rowFor(item.entry);
      nodes.push(tr);
      if (item.entry.kind === "ex") { rows.push(tr); ids.push(item.entry.id); }
    });
    if (view.endId == null && c.hiddenOutside.newer && c.shown) nodes.push(hiddenRow(c.hiddenOutside.newer));
    syncChildren(body, nodes);
    wrap.classList.toggle("is-empty", c.shown === 0);
    view.shownRows = rows;
    view.shownIds = ids;
    renderLogState(c.shown, c.inView, c.held);
    renderFilterCounts();
    // Last, after everything that can change the log's height: the state lines and the controls,
    // which re-wrap as the filter counts widen.
    if (view.follow) toBottom();
    else {
      if (!keepAnchor(wrap, anchors)) showPin(wrap);
      noteLayoutScroll(topBefore);
    }
    keepFocus(focused);
    renderFollow();
    var h = S.hello, mine = c.matching === c.inView, of = mine ? c.inView : c.matching, extra = [];
    var counted = c.shown === c.inView ? plural(c.inView, "exchange", "exchanges") :
      fmtN(c.shown) + " of " + fmtN(of) + (mine ? " shown" : " matching shown");
    if (c.shown < c.inView && rows.length) {
      var s0 = sel.items.filter(function (it) { return it.kind === "entry" && it.entry.kind === "ex"; });
      var a0 = s0[0].entry.e.seq, a1 = s0[s0.length - 1].entry.e.seq;
      extra.push(a0 <= a1 ? "seq " + fmtN(a0) + "–" + fmtN(a1) : "seq " + fmtN(a0) + " … " + fmtN(a1) + " across a restart");
    }
    if (S.exCount !== of) extra.push(fmtN(S.exCount) + " retained");
    if (extra.length) counted += " (" + extra.join("; ") + ")";
    $("log-count").textContent = !h && S.lastSeq == null ? "" : counted +
      (S.lastSeq != null ? ", last seq " + S.lastSeq + (isLive() ? " (live)" : "") : "") + (S.duplicates ? ", " + S.duplicates + " duplicates ignored" : "");
  }
  function atBottom(wrap) { return wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 4; }
  function toBottom() { var wrap = $("logwrap"); wrap.scrollTop = wrap.scrollHeight; }
  // The exchange rows in the log box now, oldest first, each with its offset from the box's
  // top: the reader's anchors. Rows are in order, so a binary search finds the first one.
  function visibleAnchors(wrap) {
    var rows = view.shownRows, out = [];
    if (!rows.length) return out;
    var box = wrap.getBoundingClientRect(), lo = 0, hi = rows.length;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (rows[mid].getBoundingClientRect().bottom > box.top) hi = mid; else lo = mid + 1;
    }
    for (var i = lo; i < rows.length; i += 1) {
      var top = rows[i].getBoundingClientRect().top;
      if (top >= box.bottom) break;
      out.push({ id: view.shownIds[i], offset: top - box.top });
    }
    return out;
  }
  // After a render of a pinned window, the first anchor still drawn goes back to its offset,
  // so a window shift (Older, Newer, a filter, rows leaving the cap) never moves what the
  // reader is looking at. False when none of them is drawn any more.
  function keepAnchor(wrap, anchors) {
    var box = wrap.getBoundingClientRect();
    for (var i = 0; i < anchors.length; i += 1) {
      var tr = rowCache.get(anchors[i].id);
      if (!tr || !tr.isConnected) continue;
      var d = tr.getBoundingClientRect().top - box.top - anchors[i].offset;
      if (Math.abs(d) >= 1) wrap.scrollTop += d;
      return true;
    }
    return false;
  }
  // No anchor left (a filter hid every row the reader saw): show the window's end, the
  // exchange the window is pinned at, at the bottom of the box.
  function showPin(wrap) {
    var tr = view.endId != null ? rowCache.get(view.endId) : null;
    if (!tr || !tr.isConnected) return;
    var d = tr.getBoundingClientRect().bottom - wrap.getBoundingClientRect().bottom;
    if (Math.abs(d) >= 1) wrap.scrollTop += d;
  }
  // The focus is moved only when a render removed or hid the focused control in the log (a
  // window shift took its row): then it goes to the log box itself, not to the page's top.
  // A routine render leaves it alone.
  function logFocused() {
    var a = document.activeElement;
    return a instanceof HTMLElement && a !== $("logwrap") && $("log").contains(a) ? a : null;
  }
  function keepFocus(a) {
    if (a && (!a.isConnected || !a.getClientRects().length)) $("logwrap").focus({ preventScroll: true });
  }
  // Shown exchange rows wholly below the visible part of the log: those the current filters
  // show, however they got there. Rows are in order, so a binary search finds the first one.
  function rowsBelow() {
    var rows = view.shownRows, wrap = $("logwrap");
    if (!rows.length) return 0;
    var bottom = wrap.getBoundingClientRect().bottom - 1;
    var lo = 0, hi = rows.length;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (rows[mid].getBoundingClientRect().top >= bottom) hi = mid; else lo = mid + 1;
    }
    return rows.length - lo;
  }
  // The control lives in the log header and always keeps its place (visibility, not display),
  // so showing it never moves a row, and it never lies over one. Its count is the shown rows
  // below the visible part of the log, plus the matching exchanges beyond a pinned window.
  function renderFollow() {
    var b = $("btn-follow");
    var n = view.follow ? 0 : rowsBelow();
    var beyond = !view.follow && view.counts ? view.counts.newerMatching : 0;
    b.classList.toggle("is-off", view.follow);
    b.disabled = view.follow;
    b.setAttribute("aria-hidden", String(view.follow));
    var parts = n > 0 ? plural(n, "row below", "rows below") + (beyond ? " + " + fmtN(beyond) + " beyond this window" : "") :
      beyond ? plural(beyond, "row", "rows") + " beyond this window" : "";
    b.textContent = parts ? parts + ", jump to newest" : "Jump to newest";
  }
  // A shorter list or a smaller box makes the browser move the scroll position itself (it
  // clamps to the new end). That move is the layout's, not the reader's: remember where it
  // landed, so the scroll event it fires does not change following.
  function noteLayoutScroll(topBefore) {
    var top = $("logwrap").scrollTop;         // reading it applies any clamp now
    view.layoutTop = top !== topBefore ? top : null;
  }
  function scheduleFollow() {
    if (view.followFrame) return;
    view.followFrame = requestAnimationFrame(function () { view.followFrame = 0; renderFollow(); });
  }
  // Leaving follow pins the window at the newest exchange shown at that moment; following
  // again lets the window slide to the newest.
  function setFollow(on) {
    if (view.follow !== on) {
      view.follow = on;
      view.endId = on ? null : view.lastId;
      view.pinFirst = null; view.leftCap = false;
      if (on) { toBottom(); scheduleRender(); }
    }
    renderFollow();
  }
  // The reader's own scroll: away from the end pins the window; back to the end follows again,
  // unless matching exchanges wait beyond a pinned window. Those come in only by Newer or
  // Jump to newest, so a scroll never changes the rows under the reader.
  function readerScroll(wrap) {
    var bottom = atBottom(wrap);
    if (bottom && !view.follow && view.counts && view.counts.newerMatching > 0) return;
    setFollow(bottom);
  }
  // Older / Newer move a pinned window by LOG_STEP matching exchanges (Older first pins a
  // following window where it is); Newer reaching the newest, and Jump to newest, follow again.
  // The exchange rows in the log box stay in the window: the step shrinks if it would drop them.
  // The reader's anchor stays in the window: Older keeps the topmost exchange row in view,
  // Newer the bottom-most, and the step shrinks only if it would drop it. When the step is 0
  // (the window's far end is already in view), the press scrolls the log box to the window's
  // other end, where the rows it would bring lie, so it never does nothing.
  function moveWindow(kind) {
    var wrap = $("logwrap"), opts = logOpts(view.endId != null ? view.endId : view.lastId);
    var seen = visibleAnchors(wrap), end, still = false;
    var inView = seen.length ? { topId: seen[0].id, bottomId: seen[seen.length - 1].id } : null;
    view.pinFirst = null; view.leftCap = false;
    if (kind === "older") {
      if (opts.endId == null) return;
      end = logOlderEnd(S.entries, passes, opts, LOG_STEP, inView);
      still = end === opts.endId;
      view.endId = end;
      view.follow = false;
    } else if (kind === "newer" && opts.endId != null) {
      end = logNewerEnd(S.entries, passes, opts, LOG_STEP, inView);
      still = end === opts.endId;
      view.endId = end;
      view.follow = end == null;
    } else {
      view.endId = null;
      view.follow = true;
    }
    renderLog();
    if (still) {
      var top = wrap.scrollTop;
      wrap.scrollTop = kind === "older" ? 0 : wrap.scrollHeight;
      noteLayoutScroll(top);
    }
  }
  // A filter change keeps following if following; a pinned window moves to the nearest
  // exchange the new filters show at or before its pin (or after it, if none is), extended
  // forward to a full window, and follows again when nothing matches.
  function filtersChanged() {
    view.pinFirst = null; view.leftCap = false;
    if (view.endId != null) {
      view.endId = logFullEnd(S.entries, passes, logOpts());
      if (view.endId == null) view.follow = true;
    }
    renderLog();
  }
  function watchLogScroll() {
    var wrap = $("logwrap");
    function input() { view.userInputAt = Date.now(); }
    ["wheel", "touchstart", "touchmove", "pointerdown", "keydown"].forEach(function (t) {
      wrap.addEventListener(t, input, { passive: true });
    });
    wrap.addEventListener("scroll", function () {
      if (view.layoutTop !== null && wrap.scrollTop === view.layoutTop) {
        view.layoutTop = null;          // the clamp noted above: count again, following unchanged
        scheduleFollow();
        return;
      }
      view.layoutTop = null;
      // Only a scroll that follows the reader's own input changes following, either way. Any
      // other scroll is the layout moving under them (a shorter filtered list clamps the
      // position to the end, a banner resizes the box): following is unchanged.
      if (Date.now() - view.userInputAt < USER_SCROLL_MS) readerScroll(wrap);
      scheduleFollow();                 // the count of rows below changes as the reader scrolls
    }, { passive: true });
    // The log's box changes size with the banner, the pause line and wrapping controls.
    if (window.ResizeObserver) {
      var lastTop = wrap.scrollTop;
      wrap.addEventListener("scroll", function () { lastTop = wrap.scrollTop; }, { passive: true });
      new ResizeObserver(function () {
        if (view.follow) toBottom(); else noteLayoutScroll(lastTop);
        scheduleFollow();
      }).observe(wrap);
    }
    $("btn-follow").addEventListener("click", function () {
      var focused = document.activeElement === this;
      moveWindow("newest");
      if (focused) wrap.focus({ preventScroll: true });   // the control hides while following
    });
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
    $("f-ecu").addEventListener("change", function (ev) { view.ecu = ev.target.value; filtersChanged(); });
    $("f-service").addEventListener("change", function (ev) { view.service = ev.target.value; filtersChanged(); });
    fs.addEventListener("change", function () {
      view.outcomes = OUTCOMES.filter(function (o) { return $("o-" + o).checked; });
      filtersChanged();
    });
    $("btn-pause").addEventListener("click", function () {
      view.paused = !view.paused;
      if (view.paused) view.pauseAfter = S.nextId - 1;
      syncControls(); renderLog();
    });
    // Clear view leaves no row to stay pinned at, so the log follows again.
    $("btn-clear").addEventListener("click", function () {
      view.clearedAfter = S.nextId - 1; view.pauseAfter = view.clearedAfter;
      view.endId = null; view.follow = true; view.pinFirst = null; view.leftCap = false;
      renderLog();
    });
    $("log-panel").addEventListener("click", function (ev) {
      var t = ev.target;
      if (!(t instanceof HTMLElement)) return;
      if (t.id === "btn-restore") { view.clearedAfter = 0; renderLog(); $("btn-clear").focus(); }
      else if (t.id === "btn-reset") {
        view.ecu = "all"; view.service = "all"; view.outcomes = OUTCOMES.slice();
        syncControls(); filtersChanged(); $("f-ecu").focus();
      } else if (t.dataset.lognav) {
        moveWindow(t.dataset.lognav);
        $("logwrap").focus({ preventScroll: true });     // never an off-screen button (WCAG 2.4.11)
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
      if (!(ev.target instanceof HTMLElement) || ev.target.id !== "btn-retry") return;
      if (S.ep && S.ep.state === "exhausted") retryNow();
      else if (isStale() && !episodeActive()) connect();
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

  // Until the first data arrives the panels say whether it is still coming or will not come.
  function renderPlaceholders() {
    var text = isStale() ? "Not loaded: the page could not reach the simulator. It retries on its own." : null;
    [["vehicle", S.vehicle, "Loading vehicle signals"], ["dtcs", S.dtcs, "Loading trouble codes"]].forEach(function (p) {
      if (p[1] != null) return;
      $(p[0]).replaceChildren(el("p", { cls: "placeholder", text: text || p[2] }));
    });
  }

  function renderAll() { renderLink(); renderPlaceholders(); renderStatus(); renderLog(); }

  buildControls();
  graphsBuild();
  watchLogScroll();
  renderAll();
  setInterval(function () { if (isStale() || S.ep) renderLink(); }, 1000);
  connect();
})();
