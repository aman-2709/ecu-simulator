# 0010 — A browser GUI over an opt-in, read-only observer API

Status: **Proposed 2026-09-26.** The owner approved the direction in principle: approach A
(§3) and a read-only browser MVP. This record, the written specification, is awaiting
review. **No production code has been written, and none will be until it is approved.**

This work lives on branch `gui`, which starts from `modernization` at `a57b98f`. It is
**not merged into `modernization` until V1.0 is tagged.** A GUI remains a V1.0 non-goal
([modernization-plan.md §8](../modernization-plan.md)). Nothing here changes V1.0 scope,
the Phase 8b gate, or any conformance status.

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
   handler synchronously and sends the reply only after it returns. Anything done inside
   the dispatch call is added directly to request-to-reply latency (§4.2).
4. **One asyncio loop, one thread.** The API shares the loop with the transport. That
   means no locks, but also that CPU spent in the API delays socket callbacks. The API's
   work must be bounded and must be measured (§9.2).
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
          (sends reply        │ hot path: one bounded append,                ▲ read-only,
           after return)      │ no formatting, never awaits                  │ off the hot path
                              ▼                                              │
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

### 4.2 The hot path, and why it is the whole design

This is what `ObservedDispatcher.__call__` does, and nothing more:

1. Read `time.monotonic_ns()`.
2. Call the wrapped dispatcher. If it raises, append an `error` record and re-raise,
   because the transport already logs and handles the exception.
3. Read `time.monotonic_ns()` again.
4. `HandOff.append((seq, t0_ns, elapsed_ns, endpoint_name, request_bytes, response_bytes_or_None))`.
   This is one tuple, holding references to bytes that already exist, with no copying or
   formatting.
5. Wake the publisher: set an `asyncio.Event`, which is a no-op if it is already set.

**Fan-out is not O(1).** Publication costs one queue operation per connected client. So
fan-out happens in the `Publisher` task, **after** the dispatch call has returned and the
reply has been sent, never inside it. The hot path's cost is independent of the number of
clients. The publisher's cost is proportional to clients × events, and it runs on the same
loop. That is why §9.2 measures latency **under client load** and not only with the API
idle.

### 4.3 Limits and drop counting

| Limit | Value | When exceeded |
|---|---|---|
| `HandOff` capacity | 4096 records | The new record is dropped and `handoff_dropped` increments. The dispatcher is unaffected |
| WebSocket clients | **4** | A 5th connection is refused (HTTP 503 before the upgrade) and counted |
| Per-client queue | 1024 encoded messages | That client's message is dropped and its `dropped` counter increments. After 5 s of continuous overflow the client is disconnected with close code 1013 and a reason |
| `state` push rate | at most 4 Hz, and only when the vehicle or DTC snapshot changed | Changes are coalesced; this is not a drop |
| History ring for `GET /exchanges` | 500 summarised events | The oldest is evicted, by design |
| HTTP request body | 1 KiB (only GET is accepted) | 413 |
| WebSocket incoming message | any message | The socket is closed: v1 accepts nothing from clients |

All counters are exposed in `GET /status` and sent to clients as `dropped` messages. **A
drop is always reported, never hidden.** Values are constants in `observe`, chosen for a
single user. They are not configuration in v1.

## 5. API v1

Everything is under `/api/v1`. Responses are JSON, and bytes are lowercase hex strings.
**Only `GET` and the WebSocket upgrade exist; every other method returns 405.** The
version prefix means a future write API cannot silently change v1.

| Endpoint | Returns |
|---|---|
| `GET /status` | `version`, `interface`, `profile`, `started_at`, `uptime_s`, `scenario` {`enabled`, `t_last_applied`, `pending_events`}, `api` {`clients`, `handoff_dropped`, `client_dropped` per client, `refused_clients`} |
| `GET /vehicle` | `kind`, `vin`, `signals` {dotted path → value}, `as_of` (scenario time of the last application, or `null` without a scenario) |
| `GET /dtcs` | per ECU: `[{code, pending, confirmed, indicator_requested}]`, and `mil` |
| `GET /ecus` | per ECU: endpoints {`name`, `rx_id`, `tx_id`, `functional`, `padding`} and protocols {`name`, `sids`} |
| `GET /exchanges?limit=N` | up to N (at most 500) of the most recent exchange events, newest last |
| `WS /events` | server → client only (below) |

WebSocket messages, server → client:

```json
{"type": "exchange", "seq": 1042, "t": "2026-09-26T10:15:02.118Z", "ecu": "engine",
 "endpoint": "engine.obd_functional", "rx_id": "0x7df", "tx_id": "0x7e8", "functional": true,
 "request": "010c", "response": "410c0c80", "outcome": "answered", "dispatch_us": 41,
 "summary": "OBD 01 0C — engine speed"}
{"type": "state", "vehicle": {"...": "as GET /vehicle"}, "dtcs": {"...": "as GET /dtcs"}}
{"type": "dropped", "handoff_dropped": 0, "client_dropped": 12}
```

- `outcome` is `answered`, `silent` (the handler returned no response) or `error`.
  `unrouted` is determined by the publisher with a read-only router lookup, off the hot
  path.
- `summary` is built by the publisher from the existing service and PID tables. The
  browser holds **no protocol logic**. An unknown request is summarised by its service
  byte alone.
- `dispatch_us` is **dispatcher time only**. It is not wire latency. Wire timing belongs
  to candump and, later, to the raw-frame panel.
- On connect, a client first receives one `state` message and the history ring, then live
  events.

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
- Views: a status bar (connection, interface, uptime, scenario time, drops), a vehicle
  signals table, a DTC panel, and an exchange log with filters by ECU, service and
  `silent` / `error`, plus pause and clear.
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
- A test runs the CLI in a subprocess **without aiohttp installed**. Ordinary CI's unit
  jobs install `.[dev]` only, so every CI run re-proves it.

### 9.2 Performance

**Early check, at the end of M1 and again at M2.** The goal is to catch a regression while
it is cheap to fix, not to accept the design:

- M1, in-process, with no network: dispatch latency for 20,000 calls through a bare
  `Dispatcher` against the same calls through `ObservedDispatcher`, with the publisher
  draining.
- M2, on vcan in a namespace: the M4 conditions 1, 2 and 4 below, at reduced length
  (5,000 requests each).

**Full benchmark, at M4,** on vcan, 20,000 requests per condition. The request mix is
that of the 2026-09-25 LX run: constant `01 0D` / `01 10` polling, with the
supported-PID chain and multi-frame VIN interleaved.

| # | Condition |
|---|---|
| 1 | API off |
| 2 | API on, 0 clients |
| 3 | API on, 1 client |
| 4 | API on, 4 clients, one of them stalled (connected but never reading). The §4.3 rule disconnects the stalled client after 5 s of overflow; the harness reconnects it each time, so a stalled client is present throughout, and the number of forced disconnects is recorded |
| 5 | Condition 4 at the maximum request rate the tester sustains |

Measured for each condition: dispatch latency in-process; wire latency from request frame
to first reply frame, from candump, using the method of
`docs/validation/phase-8-lx-bluetooth-2026-09-25/analyze.py`; lost replies; drop
counters; and RSS over a 10-minute soak.

Proposed pass criteria, for the owner to confirm at M4: wire-latency median no more than
**+0.1 ms** and p99 no more than **+0.5 ms** over condition 1; **zero lost replies** in
every condition; every drop reported in the counters; RSS flat (no upward trend) over the
soak. Results are recorded in `docs/validation/`, with the scripts that produced them.

### 9.3 Tests, and where they run

| Test | Needs | Runs in ordinary CI? |
|---|---|---|
| `observe` unit tests: `HandOff` bounds and drop counting, `ObservedDispatcher` timing and error re-raise, `Publisher` fan-out and per-client drops, the client limit, snapshots serialise and **never mutate** (state, store and runner compared before and after) | nothing | **Yes**, every job |
| API-off proofs (§9.1) | nothing | **Yes** |
| `api` tests with aiohttp's test client over loopback: every route, 405 on every other method, `Host` 421, `Origin` 403, 503 for a 5th client, the WebSocket stream and its ordering, static files | `.[dev,gui]` and loopback | **Yes**, in a CI job that installs `.[dev,gui]`. Loopback exists on hosted runners |
| vcan integration: a real ISO-TP request produces the matching WebSocket `exchange` event | `.[dev,gui]` and a kernel with `CAN_ISOTP` | **No.** It skips on hosted runners (`linux-azure` has no `can_isotp`). It is **pending a compatible runner** ([0009](0009-self-hosted-vcan-runner.md)), runs locally, and is **never counted as validated from a CI run that skipped it** |
| Performance (§9.2) | vcan and `CAN_ISOTP` | No. Local, recorded evidence |

The one CI change is a job installing `.[dev,gui]` for the `api` tests. Integration and
performance results are reported as local results, with their commands.

## 10. Milestones

| # | Deliverable | Production code | Exit |
|---|---|---|---|
| **M0** | This record, approved. **After approval**, a roadmap note in `modernization-plan.md` on branch `gui`: the GUI is a separate track, and the V1.0 non-goal and the 8b gate are unchanged | no | Owner approval |
| **M1** | `observe`: `HandOff`, `ObservedDispatcher`, `Publisher`, snapshots; the §9.1 proofs including the differential comparison; the M1 early performance check | yes, core, no dependencies | Proofs green; early check reported |
| **M2** | `ApiServer`, `--api`, the `[gui]` extra, §6 security, §4.3 limits, API tests, the CI job; the M2 early performance check | yes | API tests green in CI; early check reported |
| **M3a** | Frontend MVP: status, vehicle, DTCs, exchange log | yes | Manual view checklist |
| **M3b** | Sparklines with vendored uPlot | yes | Same |
| **M4** | Full benchmark (§9.2) and MVP acceptance report | no | Owner accepts |
| Later | Raw CAN frame panel (optional, read-only, a raw CAN socket in the API process) | — | Separate approval |
| Later | `ControlPort` controls (§8) | — | Own decision record first |

Each milestone stops for owner review before the next starts. Each is committed on `gui`
and pushed only when the owner asks.

## 11. Risks

| Risk | Mitigation |
|---|---|
| API work on the shared loop delays replies | Single-append hot path (§4.2); hard limits (§4.3); early checks at M1 and M2; full benchmark under client load at M4 |
| The observer mutates state, or triggers scenario application | Snapshots never call `sync()`; "never mutates" unit tests; v1 has no write routes, and every other method returns 405 |
| A local web page abused by other sites | Loopback-only bind, `Host` allowlist, `Origin` check, no CORS |
| GUI code leaks into V1.0 | Separate branch, merged only after V1.0 is tagged; the API is off by default, and its dependency is an optional extra |
| CI green mistaken for vcan-validated | §9.3 names the skipped test and its reason; reports keep local and CI results separate |
