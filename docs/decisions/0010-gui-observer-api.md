# 0010 — A browser GUI over an opt-in, read-only observer API

Status: **Proposed.** On 2026-09-26 the owner approved M0 **in direction**, subject to two
edits: bound the publisher's work per loop turn, and correct the P5 accounting. Both are
applied in this revision. The direction covers approach A (§3) and a read-only browser
MVP. **No production code has been written.** M1 starts only after the owner has reviewed
its implementation plan.

This work lives on branch `gui`, which starts from `modernization` at `a57b98f`. It is
**not merged into `modernization` until V1.0 is tagged.** A GUI remains a V1.0 non-goal
([modernization-plan.md §8](../modernization-plan.md)). Nothing here changes V1.0 scope,
the Phase 8b gate, or any conformance status.

Revised 2026-09-26 after owner review. The revision:

- corrected the claim about when the publisher runs relative to the reply (§4.2);
- defined exactly which exchanges the wrapper observes, from a trace of the routing path
  (§4.4);
- added byte limits and a sequence watermark (§4.3, §4.5);
- fixed the outcome definition (§5);
- made the M4 criteria explicit (§9.2).

A second revision, after M0 was approved in direction:

- bounded the publisher's work per loop turn (§4.2, O4, P9);
- corrected P5's accounting for queued messages and disconnects with a per-connection
  ledger (§5.1);
- linked the duplicate-reply defect, DEV-25 (§4.4).

The evidence for the routing and ordering claims is in §12.

## 1. Purpose and scope

**Purpose: watch first, control later.** The owner wants to see, live and in a browser,
what the running simulator is doing: its vehicle values, its trouble-code store, and every
diagnostic exchange it serves. This is for demonstrations and for debugging a scan tool
against the simulator. Changing values comes later, as a separately designed step (§8).

**GUI v1 is read-only, and the simulator is started from the CLI** exactly as today. The
GUI observes a simulator that is already running. It never starts, stops or configures
one.

| In v1 | Not in v1, and where it goes |
|---|---|
| Status: interface, profile, uptime, scenario time, API client and drop counts | Starting or stopping the simulator from the browser: the `ControlPort` milestone (§8) |
| Vehicle signals, live | Choosing a profile or scenario from the browser: `ControlPort` |
| Trouble-code store per ECU (pending, confirmed, indicator) and MIL | Setting, raising or clearing trouble codes from the browser: `ControlPort`, coordinated with Phase 10 |
| Decoded request/response log, with filters | Raw CAN frame panel, including other nodes' frames: a later optional milestone (§10) |
| Served on loopback only | Remote access, multiple users, authentication: not planned |

Users: one person, on the Linux host that runs the simulator. Remote viewing, if wanted,
is an SSH port-forward to the loopback port.

## 2. Constraints from the existing design

1. **`ScenarioRunner` is the only writer** of vehicle state
   ([0006 §5](0006-phase-7-scenario-and-testerpresent.md)). v1 writes nothing. Snapshots
   **never call `sync()`**, because that would make the observer a trigger of scenario
   application. They report state as last applied, together with the scenario time it was
   applied at. The existing periodic tick (`scenario.tick`, default 1 s) keeps that current.
2. **`Runtime` is the composition root** (`app.py`). The observer attaches there and in
   `run()`, and nowhere else. Protocol, ECU and vehicle code gain no imports, no sockets
   and no awareness of observation.
3. **The dispatch call sits on the wire path.** `IsoTpTransport._on_readable` calls the
   handler synchronously. It passes the reply to `_send` only after the handler returns
   (§12, E1). Anything done inside the dispatch call is added directly to request-to-reply
   latency.
4. **One asyncio loop, one thread.** The API shares the loop with the transport. That
   means no locks, but also that CPU spent in the API delays socket callbacks: the next
   request's read callback, and the writable callback that drains queued replies. The
   API's work must be bounded and must be measured (§9.2).
5. **Phase 10 (fault injection, V1.1)** owns faults, and v1 must not implement any of it
   by the back door.

## 3. Approaches considered

| | Approach | Verdict |
|---|---|---|
| **A** | Opt-in observer API inside the simulator process, on the same loop; the browser is a client | **Chosen.** It sees internal state that never reaches the wire, uses one process, and provides the one place a future writer can be added cleanly |
| B | Separate process that sniffs CAN and tails the log; no simulator changes | Rejected for v1: no internal state, and control would later need A anyway. Its raw-frame view survives as a later optional panel (§10) |
| C | Separate GUI process with a Unix-socket API into the simulator | Rejected: the most isolation, but two processes and two protocols. A's off-by-default design with a measured hot path addresses the same risk more cheaply |

## 4. Architecture

```
            ┌────────────────── simulator process, one asyncio loop ──────────────────┐
 CAN ⇄ IsoTpTransport ─► ObservedDispatcher ─► Dispatcher ─► ECUs ─► VehicleState / DtcStore
   _on_readable: dispatch,    │ hot path: one bounded append,                ▲ read-only,
   then _send (kernel or      │ no formatting, never awaits                  │ off the hot path
   pending queue)             ▼                                              │
                        HandOff (bounded) ──► Publisher task ── summarise, JSON-encode once ──┐
                                                    │                                         │
                                                    ├─► history ring (GET /exchanges)          │
                                                    └─► per-client queues (bounded) ─► WebSocket clients (max 4)
                                                                                      ▲
                                             ApiServer (aiohttp, [gui] extra) ─ HTTP snapshots
```

### 4.1 Components

| Unit | Lives in | Depends on | Purpose |
|---|---|---|---|
| `ObservedDispatcher` | `ecu_simulator.observe` (core) | `Dispatcher`, `HandOff` | Wraps the dispatcher: times the call and appends one raw record |
| `HandOff` | `observe` | stdlib only | The single bounded buffer between the hot path and everything else, with a drop counter |
| `Publisher` | `observe` | `HandOff`, `Runtime` (read-only) | An asyncio task that drains `HandOff`, builds summaries, JSON-encodes each event once and fans it out |
| Snapshot functions | `observe` | `Runtime` (read-only) | `Runtime` → plain JSON-ready dicts for status, vehicle, DTCs and ECUs |
| `ApiServer` | `ecu_simulator.api`, **`[gui]` extra** | aiohttp, `Publisher`, snapshots | HTTP routes, WebSocket, static files, security checks |
| Static frontend | `ecu_simulator/api/static/` | none at runtime | HTML, CSS, plain JavaScript modules |

`observe` has **no third-party dependencies**. Only `api` imports aiohttp, and only when
`--api` is given.

### 4.2 The hot path, and when the publisher runs

This is what `ObservedDispatcher.__call__` does, and nothing more:

1. Take the next sequence number: an integer increment, owned by the wrapper.
2. Read `time.monotonic_ns()`.
3. Call the wrapped dispatcher. If it raises, append an `error` record and re-raise. The
   transport already logs the exception and sends nothing (§12, E1).
4. Read `time.monotonic_ns()` again.
5. `HandOff.append(record)`. The record is one tuple:
   `(seq, t0_ns, elapsed_ns, request, response_or_None, exception_type_or_None)`.
   `request` is the `DiagnosticRequest` itself, so its endpoint is available from its
   `context`. Both payloads are references to bytes that already exist, with no copying
   and no formatting.
6. Wake the publisher by calling `asyncio.Event.set()`.

**When the publisher runs (corrected).** An earlier draft said the publisher runs "after
the reply has been sent". That was wrong. What holds is this:

- `Event.set()` does not run the publisher. It resolves the waiting future, whose
  callbacks are scheduled with `loop.call_soon` (§12, E4). The publisher therefore resumes
  on a **later loop iteration**, after the read callback that dispatched the request has
  returned.
- By the time that callback returns, `_send` has done one of three things (§12, E2):
  1. **handed the reply to the kernel**: `socket.send` returned `True`. That means the
     kernel accepted it, not that it is on the wire. A multi-frame reply is still waiting
     for the tester's flow control;
  2. **queued it** in `endpoint.pending`, to be sent by a later writable callback, because
     the socket was busy or earlier replies were queued;
  3. **dropped it** after a `TransportIOError`, which is logged.
- So the publisher never runs *inside* the dispatch-to-`_send` sequence. It **can** run
  before a queued reply is transmitted, and it competes for the loop with that writable
  callback and with the next request's read callback. That is what §9.2 measures.

**Verified by** these tests, which run in ordinary CI and need no CAN (§9.3):

- **O1.** A real `IsoTpTransport` built with a fake socket factory (the existing
  `socket_factory` seam, §12, E2) and `_check_environment` off. An ordered event log shows
  that for an immediately accepted reply the fake socket's `send` happens before the
  publisher's first line runs for that exchange.
- **O2.** The same setup with a fake `send` that returns `False` (busy). The log shows the
  publisher **may** run before the queued payload is sent. The exchange is still reported
  as `responded` (§5). The test pins the corrected claim so the wrong one cannot come back.
- **O3.** The hot path never awaits and never calls into the publisher: a test replaces the
  `Publisher` with one that raises if called synchronously from `__call__`.

**Fan-out is not O(1).** Publication costs one queue operation per connected client. The
hot path's cost is independent of the number of clients. The publisher's cost is
proportional to clients × events, and it runs on the same loop.

**The publisher's work per loop turn is bounded.** A full `HandOff` holds 4096 records.
Draining it in one go would hold the loop for the whole backlog, delaying every read and
writable callback behind it. So, each time it is scheduled, the publisher processes
records only while **both** of these hold:

- it has handled **at most 64 records** in this turn;
- it has spent **at most 1 ms** of `time.monotonic()` in this turn, checked after each
  record.

Processing a record covers summarising it, JSON-encoding it once, appending it to the
history ring and offering it to every client queue. As soon as either limit is reached,
the publisher yields with `await asyncio.sleep(0)`. The loop then runs the I/O callbacks
that are ready before the publisher continues, so draining a full `HandOff` takes at
least 64 turns.

Sending to each WebSocket runs in that client's own writer task, which awaits
`send_str`, so no client's network I/O runs inside a publisher turn. The longest a
publisher turn can hold the loop is therefore about 1 ms plus one record's processing.
M1 and M4 measure it (§9.2, P9).

**Verified by** test **O4**. It fills `HandOff` to 4096 records, then from inside the
drain schedules a marker callback with `call_soon`. The ordered log must show the marker
running before the drain finishes, and the publisher yielding at least ⌈4096 / 64⌉ = 64
times. A second case uses a stub encoder that takes 0.3 ms per record, and asserts that
no turn exceeds 1 ms plus one record's processing time.

### 4.3 Limits, byte budgets and overflow behavior

Payload sizes are bounded by ISO-TP: at most 4095 bytes each way on Classical CAN. Every
retaining structure has both a count limit and a byte limit. Whichever is reached first
applies.

| Structure | Count limit | Byte limit | Overflow behavior |
|---|---|---|---|
| `HandOff` (hot path → publisher) | 4096 records | 1 MiB of payload bytes (`len(request)` plus `len(response)`, summed with an O(1) counter, no copying) | **The new record is dropped**, and `handoff_dropped` increments by one. Its sequence number is not reused, so the gap is visible to clients (§4.5). The dispatcher is unaffected |
| Retained payload per event, after publication | — | **512 bytes per payload.** The history ring, `GET /exchanges` and WebSocket messages carry at most the first 512 bytes of each payload, as hex | The event carries `request_len` / `response_len` (full lengths) and `request_truncated` / `response_truncated` flags. Nothing is truncated on the hot path |
| History ring (`GET /exchanges`, initial WebSocket history) | 500 events | 2 MiB of encoded JSON | The oldest event is evicted, by design; `oldest_seq` advances (§4.5). This is not counted as a drop |
| Per-client queue | 1024 messages | 4 MiB of encoded JSON | **The new `exchange` message is dropped** and that client's `client_dropped` increments. `state` and `dropped` messages are never queued behind others: each client has a one-slot latest `state` and a one-slot latest `dropped` notice, which are **replaced** rather than queued, and sent before the next queued exchange. After 5 s of continuous overflow the client is disconnected with close code 1013 and the reason `"client too slow"`, and `forced_disconnects` increments |
| WebSocket clients | 4 | — | A 5th connection is refused with HTTP 503 before the upgrade, and `refused_clients` increments |
| Single encoded `state` message | — | 256 KiB | Checked once when the API starts. A profile whose state encodes larger makes `--api` refuse to start (exit 2), rather than truncating state at runtime |
| Publisher turn | 64 records | 1 ms of loop time | The publisher yields (`await asyncio.sleep(0)`) and continues on a later turn. Nothing is dropped. A backlog that keeps growing ends in `HandOff`'s own overflow above |
| `state` push rate | at most 4 Hz | — | Coalesced into the one-slot latest `state`, which is not a drop |
| Incoming HTTP body | — | 1 KiB | 413. v1 routes are GET-only, so a body is never needed |
| Incoming WebSocket data message (text or binary) | — | — | Any data message closes the socket with 1008: v1 accepts nothing from clients. Control frames (ping/pong/close) are handled normally |

**Bounded memory.** The worst case for the observer's retained data is 1 MiB (`HandOff`) +
2 MiB (history) + 4 × 4 MiB (client queues) + small one-slot buffers, **about 19 MiB**.
§9.2 checks RSS against that.

All counters are exposed in `GET /status` and in `dropped` messages. **A drop is always
reported, never hidden.** Values are constants in `observe`, chosen for a single user.
They are not configuration in v1.

### 4.4 What the wrapper observes, traced

The wrapper sees **one exchange per call to the dispatcher**, and nothing else. §12 gives
the evidence for each row.

| Situation | Reaches the dispatcher? | What the wrapper records |
|---|---|---|
| A request on any receiving endpoint (physical or functional), after the kernel has reassembled it | Yes, once per receiving socket that got it | One exchange |
| The dispatcher returns a response | Yes | `responded`, with the response payload. **At most one** response per call: fan-out to several ECUs is refused, at construction and again per request (E3) |
| A route exists and the ECU returns no response (unsupported, suppressed, Mode 07) | Yes | `no_response` |
| No route for the request's address and kind | Yes: the dispatcher logs *"no ECU for … request; dropped"* and returns `None` (E3) | `unrouted`, decided by the publisher (§5). **Not reachable in v1 as built**: `check_routes` refuses to start unless every receiving endpoint has a route of the same kind (E3), and nothing changes the router at runtime. It is kept so that a future router change is reported rather than misclassified |
| The dispatcher raises | Yes | `error`, with the exception type. The transport logs it and sends nothing (E1) |
| Frames the kernel handles alone: flow control, consecutive frames, frames on IDs no socket receives, anything a transport-only (`receive=False`) socket sees | **No** | Nothing. This is the raw-frame panel's job (§10) |
| `recv` fails with `TransportIOError` (for example a flow-control timeout) | **No**: the transport logs and returns before dispatch (E1) | Nothing |
| The response is not transmitted (send error, or it is still queued) | The dispatch already happened | Still `responded`. **Transmission is not observed in v1**: `responded` means "the dispatcher returned a response", not "it reached the wire" |
| **One CAN request received by two sockets** | Yes, **twice** | **Two** exchanges with the same request payload and adjacent sequence numbers. They are not merged, because the wrapper cannot tell they came from one frame |

The last row is reachable today. The schema requires physical receive IDs to be unique
**across** ECUs, but only `(rx, tx)` pairs to be unique **within** one ECU (E5). One ECU
may therefore declare two physical endpoints on `rx 0x7E0` with different `tx`, and
`check_routes` accepts it (E5). Measured on vcan: two ISO-TP sockets bound to the same rx
ID **both** receive the same single-frame and multi-frame request (E6). So such a profile
answers one request twice, on two IDs. **That is a configuration-validation gap in the
simulator, not a GUI matter.** It is recorded as the existing defect **DEV-25** in
`docs/known-deviations.md` on `modernization`, and is **not fixed as part of any GUI
milestone**.
The GUI reports what happens and does not paper over it.

### 4.5 Sequence numbers and the history/live watermark

- Every exchange gets a sequence number `seq` on the hot path. It starts at 1 and
  increases by 1 per dispatch call, including calls whose record `HandOff` then drops.
  **A gap in `seq` at the client therefore always means a drop or an eviction,** and the
  counters say which.
- `last_published_seq` is the highest `seq` the publisher has put into the history ring.
  `oldest_seq` is the lowest `seq` still in the ring.
- **Joining a live stream without gaps or duplicates.** When a WebSocket client connects,
  the server does the following **in one synchronous step, with no `await` between the
  steps**, so the single-threaded loop guarantees nothing is published in between:
  1. read `W = last_published_seq`;
  2. copy the history ring;
  3. register the client's queue.

  Then it sends, in order:

  ```json
  {"type": "hello", "api": 1, "watermark": W, "oldest_seq": 812}
  {"type": "state", "...": "..."}
  {"type": "exchange", "seq": 812, "...": "..."}
  ```

  The history events all have `seq ≤ W`, oldest first. They are followed by live
  `exchange` messages, all with `seq > W`. Every event is therefore delivered at most
  once, and in `seq` order.
- **Resuming.** Both `WS /events?after=S` and `GET /exchanges?after=S` return only events
  with `seq > S`. If `S < oldest_seq − 1`, the response carries `"gap": true` and starts
  at `oldest_seq`, so a reconnecting client knows it missed events that were evicted.
- Clients detect every other gap from `seq` itself: the next `seq` is not the last + 1.
  The browser shows a gap marker together with the counters that explain it.

## 5. API v1

Everything is under `/api/v1`. Responses are JSON, and bytes are lowercase hex strings.
**Only `GET` and the WebSocket upgrade exist; every other method returns 405.** The
version prefix means a future write API cannot silently change v1.

| Endpoint | Returns |
|---|---|
| `GET /status` | `version`, `interface`, `profile`, `started_at`, `uptime_s`, `scenario` {`enabled`, `t_last_applied`, `pending_events`}, `api` {`clients`, `issued_seq`, `published`, `last_published_seq`, `oldest_seq`, `handoff_dropped`, `refused_clients`, `forced_disconnects`, `connections` [one **ledger** per open connection, §5.1], `closed_connections` [the ledgers of the last 64 closed connections, final values]} |
| `GET /vehicle` | `kind`, `vin`, `signals` {dotted path → value}, `as_of` (the scenario time of the last application, equal to `t_last_applied`; `null` without a scenario) |
| `GET /dtcs` | per ECU: `[{code, pending, confirmed, indicator_requested}]`, and `mil` |
| `GET /ecus` | per ECU: endpoints {`name`, `rx_id`, `tx_id`, `functional`, `receive`, `reply_via`, `padding`} and protocols {`name`, `sids`} |
| `GET /exchanges?limit=N&after=S` | up to N (default and maximum 500) exchange events with `seq > S` (default: the most recent N), oldest first, together with `watermark`, `oldest_seq` and `gap` (§4.5) |
| `WS /events?after=S` | server → client only (§4.5 for ordering) |

WebSocket messages, server → client:

```json
{"type": "hello", "api": 1, "watermark": 1041, "oldest_seq": 542}
{"type": "exchange", "seq": 1042, "t": "2026-09-26T10:15:02.118Z", "ecu": "engine",
 "endpoint": "engine.obd_functional", "rx_id": "0x7df", "tx_id": "0x7e8", "functional": true,
 "request": "010c", "request_len": 2, "request_truncated": false,
 "response": "410c0c80", "response_len": 4, "response_truncated": false,
 "outcome": "responded", "error": null, "dispatch_us": 41,
 "summary": "OBD 01 0C — engine speed"}
{"type": "state", "vehicle": {"...": "as GET /vehicle"}, "dtcs": {"...": "as GET /dtcs"}}
{"type": "dropped", "handoff_dropped": 0, "client_dropped": 12, "forced_disconnects": 0}
```

- **`outcome`** takes exactly one of four values:

  | Value | Meaning |
  |---|---|
  | `responded` | The dispatcher returned a response. **Not** a claim that it was transmitted (§4.4) |
  | `no_response` | The dispatcher returned `None`, and the request's address had a route |
  | `unrouted` | The dispatcher returned `None`, and the request's address had **no** route. Decided by the publisher with a read-only `router.resolve` off the hot path. This is exact in v1 because the router is never changed at runtime. If that changes, the decision has to move into the record. Not reachable in v1 as built (§4.4) |
  | `error` | The dispatcher raised. `error` carries the exception type name, and `response` is `null` |

- `tx_id` is the ID the reply leaves on: the `reply_via` endpoint's tx when one is set,
  which for `engine.obd_functional` is `0x7E8`.
- `ecu` comes from the route, and is `null` for `unrouted`.
- `summary` is built by the publisher from the existing service and PID tables. The
  browser holds **no protocol logic**. An unknown request is summarised by its service
  byte alone.
- `dispatch_us` is **dispatcher time only**. It is not wire latency. Wire timing belongs
  to candump and, later, to the raw-frame panel.
- `client_dropped` in a `dropped` message is that client's own count. `GET /status` has
  every connection's ledger (§5.1).

### 5.1 The per-connection ledger

Every WebSocket connection gets its own ledger, keyed by a connection `id`. A reconnect is
a new connection with a new ledger. The ledger counts **live `exchange` messages only**.
The history sent on connect is counted separately, and `state` and `dropped` messages,
which are replaced rather than queued (§4.3), are not counted.

| Field | Meaning |
|---|---|
| `id`, `connected_at`, `closed_at`, `close_code` | Identity and lifetime. `close_code` is 1013 for a forced disconnect |
| `watermark` | `W` at registration (§4.5) |
| `published_at_open`, `published_at_close` | The global `published` counter when the connection was registered, and when it closed (or now, while it is open) |
| `history_sent` | History events sent on connect, all with `seq ≤ W` |
| `offered` | Live events published while this connection was registered, all with `seq > W` |
| `client_dropped` | Offered events refused because the queue was full |
| `enqueued` | Offered events accepted into the queue |
| `sent` | Events the writer task has passed to `send_str` successfully |
| `queued` | Events in the queue now (0 once closed) |
| `discarded_on_close` | Events still in the queue when the connection closed, forced or not. They are discarded, not sent |

The server keeps these identities **exactly**, at every instant:
`offered = published_at_close − published_at_open`, `offered = enqueued + client_dropped`,
and `enqueued = sent + queued + discarded_on_close`.
The ledger of a closed connection is final and stays in `closed_connections`.

## 6. Security

- `--api HOST:PORT` accepts only a loopback host: `127.0.0.1`, `::1` or `localhost`. Any
  other host is refused at startup with exit status 2 and a message. The API is off by
  default.
- **`Host` header allowlist:** the request's `Host` must be the bound loopback address or
  `localhost` with the bound port, otherwise 421. This defends against DNS rebinding.
- **WebSocket `Origin`** must equal the server's own origin, otherwise 403 before
  `ws.prepare()`. No CORS headers are ever sent.
- **No authentication in v1,** by the owner's decision on 2026-09-26. Any local user of the
  machine can read the page. The data is simulator state, and the VIN is a test value.
  This is revisited when §8 adds writes.
- The static files are package data, served from a fixed directory, with no directory
  listing and no path parameters.

## 7. Frontend

- **No build step, no npm, and no CDN at runtime,** because a bench host may be offline.
  The page is static HTML, CSS and plain JavaScript modules, served by `ApiServer`.
- Views:
  - a status bar showing connection, interface, uptime, scenario time and drops;
  - a vehicle signals table;
  - a DTC panel;
  - an exchange log, filterable by ECU, service and outcome (`no_response`, `unrouted`,
    `error`), with pause and clear. It shows a gap marker wherever `seq` jumps (§4.5).
- Milestone 3b vendors **uPlot** (MIT) for per-signal sparklines, with its licence file
  and pinned version. The MVP (3a) works without it.
- Decoding stays in Python (§5), so the JavaScript only renders. The frontend is checked
  by a manual acceptance list at M4 and by the API tests that produce what it renders.
  There is no JavaScript test framework in v1.

## 8. The future control boundary (designed, not built)

- `/api/v1/control/*` is reserved and returns 405 in v1.
- Controls will enter through **one** `ControlPort` interface. It writes a **manual-override
  layer owned by `ScenarioRunner`** and applied inside `sync()`, the same way scenario
  values are. The runner therefore stays the only writer, and idempotence under the
  request path and the tick is unchanged.
- Start and stop, profile and scenario selection, and DTC injection all belong here. They
  need their **own decision record**, which must settle how DTC injection relates to
  Phase 10, whether writes require authentication, and how an override is shown in the GUI
  and in logs.

## 9. Validation

### 9.1 API off is unchanged

- With `--api` absent, `run()` hands the transport the unwrapped `Dispatcher`, and aiohttp
  is never imported. This is pinned by tests.
- The full suite passes, and the project's **differential comparison** of every reply over
  tens of thousands of request and address combinations shows no difference between the
  API-off build and the `modernization` baseline.
- A test runs the CLI in a subprocess and asserts that aiohttp is **not imported**. In the
  `.[dev]` CI jobs aiohttp is not installed at all, so those jobs prove the stronger
  claim: the simulator runs without it. In the `.[dev,gui]` job, the "not installed" form
  of the test skips with that reason, and only the "not imported" form runs.

### 9.2 Performance

**Early checks, at the end of M1 and again at M2.** These catch regressions while they are
cheap to fix. They are not acceptance.

- **M1**, in-process, no network. 20,000 calls through a bare `Dispatcher` against the
  same calls through `ObservedDispatcher`, with the publisher draining. It also drains a full 4096-record `HandOff` and reports the longest publisher turn.
  **If median overhead exceeds 10 µs, p99 overhead exceeds 50 µs, or any publisher turn
  exceeds 2 ms, stop and report before M2.**
- **M2**, on vcan in a namespace. M4 conditions 1, 2 and 4 at 5,000 requests each. **If
  any M4 latency criterion below is already missed, stop and report before M3.**

**Full benchmark at M4. The criteria below are fixed now, not at M4.**

*Setup.* vcan in a private namespace on the development host, with no other deliberate
load. The report records the kernel, Python, commit and CPU frequency governor. The tester
is a Python ISO-TP client on the functional and physical IDs. The request mix is that of
the 2026-09-25 LX run: `01 0D` and `01 10` polled alternately, with the supported-PID
chain (`01 00`, `01 20`, `01 40`) and the multi-frame VIN (`09 02`) interleaved once every
100 requests. No request in the mix is expected to be silent. The tester sends each
request after the previous reply, or after a 1 s timeout.

| # | Condition |
|---|---|
| 1 | API off |
| 2 | API on, 0 clients |
| 3 | API on, 1 client reading normally |
| 4 | API on, 4 clients: 3 reading normally and 1 stalled (connected, never reading). The §4.3 rule disconnects the stalled client after 5 s of overflow, and the harness reconnects it at once, so a stalled client is present throughout. Each forced disconnect is counted |
| 5 | Condition 4, with the tester sending at the maximum rate it sustains |

*Runs.* Conditions 1–4: 20,000 requests each, repeated in **3 rounds**, with the order of
conditions rotated in each round. Condition 5: 60 s per round, 3 rounds.

*Measurement.* Wire latency is the time from the request's first frame to the reply's
first frame, taken from a `candump -t a` capture by the method of
`docs/validation/phase-8-lx-bluetooth-2026-09-25/analyze.py`. Dispatch latency comes from
the events' `dispatch_us`.

**Pass criteria:**

| # | Criterion | How it is judged |
|---|---|---|
| P1 | Median wire latency, conditions 2, 3 and 4 | ≤ condition 1's median **+ 0.10 ms**, on pooled samples, in every round |
| P2 | p99 wire latency, conditions 2, 3 and 4 | ≤ condition 1's p99 **+ 0.50 ms**, on pooled samples, in every round |
| P3 | Lost replies, every condition | **Exactly 0.** A lost reply is a request frame with no reply frame before the next request, or within 1 s |
| P4 | Throughput, condition 5 | Requests answered per second ≥ **90 %** of the same tester's maximum rate with the API off (measured the same way in each round) |
| P5 | Drop and delivery accounting (reconciliation) | Checked after the run has **quiesced**: tester stopped, `HandOff` drained, every open connection's `queued` = 0. All of the following must hold **exactly**: (a) `issued_seq = published + handoff_dropped`. (b) For every connection, open or closed, the §5.1 identities hold. (c) Summed over every connection that was open for the whole run, `offered` equals the growth of `published` over the run, measured by the harness from `GET /status` before and after. (d) On the harness side, the `exchange` messages a connection received have strictly increasing `seq` and no duplicates. For an **open** connection, received live events = `sent`. For a **closed** connection, received live events ≤ `sent`, and the difference (sent but still in transit when the socket closed) is reported per connection. (e) Closes with code 1013 seen by the harness = `forced_disconnects`, and HTTP 503 refusals seen = `refused_clients`. **Any unexplained difference fails** |
| P6 | Drops where none should occur | In conditions 2 and 3, and for the 3 reading clients in condition 4: `handoff_dropped` = 0, `client_dropped` = 0 and `discarded_on_close` = 0. Drops, discards and in-transit losses are allowed only on the stalled client's connections, and only where P5 accounts for them |
| P7 | Memory (RSS trend) | A 10-minute soak under condition 4 load, sampling the simulator's RSS every 5 s. Samples in the first 60 s are discarded as warm-up. **Pass if** the least-squares slope of RSS against time over the remaining samples is **≤ 0.1 MiB per minute**, **and** the final sample exceeds the first post-warm-up sample by **≤ 2 MiB**. Separately, the peak RSS increase over condition 1 must stay within the §4.3 bound of about 19 MiB plus 10 MiB for code and libraries |
| P8 | Noise guard | If condition 1's own p99 differs by more than 0.50 ms between rounds, the benchmark is **inconclusive**. It is reported as such and does not pass |
| P9 | Loop hold time | The publisher records the length of every turn. The maximum over the whole of conditions 2–5 is **≤ 2 ms**. The M1 early check reports the same maximum for a full 4096-record `HandOff` |

Results, the raw captures and the scripts that produced them are committed under
`docs/validation/`. A failed criterion is reported with its numbers, never rounded into a
pass.

### 9.3 Tests, and where they run

| Test | Needs | Runs in ordinary CI? |
|---|---|---|
| `observe` unit tests: `HandOff` count and byte bounds and drop counting; `seq` gaps on drop; `ObservedDispatcher` timing, error re-raise and one record per call; `Publisher` fan-out, per-client count and byte limits, one-slot `state` / `dropped`, forced disconnect; the client limit; the watermark handshake (no gap and no duplicate across history/live, `after=` and `gap`); every §5 `outcome` value, including `unrouted` by giving the wrapped dispatcher a router with no route; snapshots serialise and **never mutate** (state, store and runner compared before and after) | nothing | **Yes**, every job |
| Ordering tests O1–O3 and the turn bound O4 (§4.2), through a real `IsoTpTransport` with a fake socket factory | nothing | **Yes**, every job |
| Ledger tests (§5.1): the identities hold after enqueue, send, overflow, and voluntary and forced close; closed ledgers are retained and final | nothing | **Yes**, every job |
| Observation of the two-socket case (§4.4): one request delivered to two fake sockets gives two exchanges | nothing | **Yes**, every job |
| API-off proofs (§9.1) | nothing | **Yes**. In the `.[dev]` jobs, without aiohttp installed. In the `.[dev,gui]` job, the "not imported" form only |
| `api` tests with aiohttp's test client over loopback: every route, 405 on every other method, `Host` 421, `Origin` 403, 503 for a 5th client, 1008 on an incoming data message, the WebSocket stream and its ordering, static files | `.[dev,gui]` and loopback | **Yes**, in a CI job that installs `.[dev,gui]`. Loopback exists on hosted runners |
| vcan integration: a real ISO-TP request produces the matching WebSocket `exchange` event | `.[dev,gui]` and a kernel with `CAN_ISOTP` | **No.** It skips on hosted runners (`linux-azure` has no `can_isotp`). It is **pending a compatible runner** ([0009](0009-self-hosted-vcan-runner.md)), runs locally, and is **never counted as validated from a CI run that skipped it** |
| Performance (§9.2) | vcan and `CAN_ISOTP` | No. Local, recorded evidence |

The one CI change is a job installing `.[dev,gui]` for the `api` tests. Integration and
performance results are reported as local results, with their commands.

## 10. Milestones

| # | Deliverable | Production code | Exit |
|---|---|---|---|
| **M0** | This record, approved. **After approval**, a roadmap note in `modernization-plan.md` on branch `gui`: the GUI is a separate track, and the V1.0 non-goal and the 8b gate are unchanged | no | Owner approves this record |
| **M1** | `observe`: `HandOff`, `ObservedDispatcher`, `Publisher`, snapshots, sequence and watermark; the §9.1 proofs, including the differential comparison; the ordering tests; the M1 early check | yes, core, no dependencies | Tests green in CI; differential comparison clean; M1 early check reported |
| **M2** | `ApiServer`, `--api`, the `[gui]` extra, §6 security, §4.3 limits, API tests, the CI job; the M2 early check | yes | API tests green in CI; M2 early check reported |
| **M3a** | Frontend MVP: status, vehicle, DTCs, exchange log with gap markers | yes | Owner runs the manual view checklist |
| **M3b** | Sparklines with vendored uPlot | yes | Owner runs the manual view checklist for sparklines |
| **M4** | Full benchmark (§9.2) and MVP acceptance report | benchmark scripts only | P1–P9 met, or failures reported; owner accepts |
| Later | Raw CAN frame panel (optional, read-only, a raw CAN socket in the API process) | — | Separate approval |
| Later | `ControlPort` controls (§8) | — | Own decision record first |

Each milestone stops for owner review before the next starts. Each is committed on `gui`
and pushed only when the owner asks.

## 11. Risks

| Risk | Mitigation |
|---|---|
| API work on the shared loop delays replies, including queued ones | Single-append hot path (§4.2); count and byte limits (§4.3); early checks at M1 and M2; P1–P4 under client load at M4 |
| The observer mutates state, or triggers scenario application | Snapshots never call `sync()`; "never mutates" unit tests; v1 has no write routes, and every other method returns 405 |
| Memory growth under a slow client | Byte and count limits on every retaining structure; forced disconnect; P7 |
| A drop goes unnoticed | `seq` gaps are visible to clients; P5 reconciliation must balance exactly |
| `responded` read as "transmitted" | §4.4 and §5 define it as dispatcher output only; test O2 pins the queued case |
| A local web page abused by other sites | Loopback-only bind, `Host` allowlist, `Origin` check, no CORS |
| GUI code leaks into V1.0 | Separate branch, merged only after V1.0 is tagged; the API is off by default, and its dependency is an optional extra |
| CI green mistaken for vcan-validated | §9.3 names the skipped test and its reason; reports keep local and CI results separate |

## 12. Evidence for the routing and ordering claims

All references are to `a57b98f`, the base of this branch.

- **E1: the read path** (`src/ecu_simulator/transport/socketcan/transport.py`,
  `_on_readable`, lines 163–191):
  - `recv` failure → log and return, before dispatch (164–169);
  - the handler is called synchronously inside `try` (181–185), and an exception is
    logged with nothing sent;
  - `None` → return (186–187);
  - otherwise `_send(via, payload)` runs, with `via` the `reply_via` endpoint if one is
    set (190–191).
- **E2: the send path** (same file, `_send` 193–202, `_on_writable` 209–219;
  `transport/socketcan/isotp.py` `send` 151–165):
  - `send` returning `True` means the kernel accepted the payload;
  - `False` (`EAGAIN`) means the payload is appended to `endpoint.pending`, and a writable
    callback sends it later;
  - a `TransportIOError` is logged and the reply is dropped;
  - `start` accepts a `socket_factory` seam (116–119), which is what the ordering tests use.
- **E3: routing** (`src/ecu_simulator/ecu/dispatcher.py` 77–99; `ecu/router.py`
  `resolve` 129–136; `app.py` `check_routes` 277–296):
  - `sync()` runs first (78–79);
  - an empty resolve logs *"no ECU for … dropped"* and returns `None` (80–87);
  - more than one route raises `NotImplementedError`, both at construction (59–60) and per
    request (89, 94–99), so one call yields at most one response;
  - `check_routes` refuses to start unless every receiving endpoint has a route of the
    same kind, and every route has an endpoint.
- **E4: the wake-up** (CPython 3.12.12, `asyncio/locks.py` `Event.set` 181–191;
  `asyncio/futures.py` `__schedule_callbacks` 161–173): `set()` resolves the waiters'
  futures, and their callbacks are scheduled with `loop.call_soon`, so nothing runs
  inside `set()`. That is the pure-Python source. Test O1 pins the behavior for whichever
  `Future` implementation is in use.
- **E5: duplicate receive IDs within one ECU** (`src/ecu_simulator/config/schema.py`
  233–236: only `(rx, tx)` pairs must be unique within an ECU; 306–318: physical `rx`
  must be unique across ECUs). Checked 2026-09-26 by loading `ice_default` with an extra
  physical endpoint `rx 0x7E0 / tx 0x7EF` on the same ECU: schema, `build_runtime` and
  `check_routes` **accept** it. A second ECU with its own functional endpoint on `0x7DF`
  is **refused** at build time (`NotImplementedError`, fan-out).
- **E6: kernel delivery to two sockets.** Measured 2026-09-26 on kernel 6.8.0-138-generic,
  vcan in a private namespace, can-isotp 2.x. Two ISO-TP sockets bound to `rx 0x7E0` (tx
  `0x7E8` and `0x7EF`), and a tester sending to `0x7E0`. Both sockets received the
  single-frame `01 0C` and a 20-byte multi-frame payload.
