# 0006 — Phase 7 evidence and design review: deterministic scenario engine and minimal 0x3E

Status: proposed 2026-09-19, before any Phase 7 code.

Produced under the documentation and standards verification gate
([modernization-plan.md section 11](../modernization-plan.md)). It covers only what Phase 7
touches. No OBD service, DoIP, J1979-2, J1979-3, CAN FD or later-roadmap standard was
researched. [0004](0004-phase-6-dtc-evidence.md) is the shape this record follows.

Phase 7 has two unrelated halves, and they are reviewed separately throughout: a
**scenario engine**, which is domain and library work with no new external standard, and
**UDS 0x3E**, which is protocol behavior governed by a specification this project cannot
read.

## 1. Phase 7 scope, taken from the current plan

From [modernization-plan.md section 4](../modernization-plan.md), verbatim and complete:

> ### Phase 7 — Deterministic scenario engine and minimal 0x3E (V1.0)
>
> Generators constant, ramp, sine, stepped, sequence, timeline as pure functions of `t`;
> lazy evaluation on read plus a periodic tick for timed DTC events; `SimulatedClock` in
> tests. Replaces the speed counter and random coolant. Stateless UDS 0x3E as scoped in
> section 3. Risk L.

Section 9 assigns three commits:

> 42. `feat(clock): Clock protocol with monotonic and simulated implementations`
> 43. `feat(scenario): deterministic generators replace speed counter and random coolant`
> 44. `feat(uds): stateless 0x3E TesterPresent`

Section 3 scopes 0x3E, verbatim:

> UDS 0x3E in V1.0 validates the request, supports the zeroSubFunction sub-function,
> honours `suppressPosRspMsgIndicationBit`, and returns the positive response. It does not
> implement S3, does not touch any timer, creates no session state, and claims no
> ISO 14229 session compliance. Real session timing is V1.1.

### 1.1 Two parts of that sentence are already done

Recorded so the phase is not credited with work it is not doing:

- **Commit 42 is already delivered.** `src/ecu_simulator/clock.py` has held `Clock`,
  `MonotonicClock` and `SimulatedClock` since Phase 4, which states in its own docstring
  that it "only establishes" the seam. What Phase 7 adds is the first production *use* of
  it: nothing in `src/` constructs a clock today.
- **"Replaces the speed counter and random coolant" happened in Phase 5.** DEV-09 and
  DEV-10 are both recorded as fixed there: Mode 01 parameters read `VehicleState` through
  signal paths, a read never mutates it, and `grep` over `src/` finds no `random` and no
  `sleep`. What is missing is not determinism but *variation*: every value is static until
  something drives it. That is Phase 7's actual job.

### 1.2 Scope, itemised

| Item | In Phase 7 | Note |
|---|---|---|
| Generators `constant`, `ramp`, `sine`, `stepped`, `sequence`, `timeline` | yes | pure functions of `t`; the plan names exactly these six |
| Lazy evaluation on read | yes | see section 5 for where "on read" is resolved |
| Periodic tick for timed DTC events | yes | |
| `SimulatedClock` in tests | yes | class exists; Phase 7 is its first use |
| First production use of `Clock` | yes | commit 42's remaining half |
| Stateless UDS 0x3E | yes | section 7 |
| Scenario configuration and its validation | yes, implied | a generator is configuration; the plan validates configuration before runtime |

### 1.3 Deviations assigned to Phase 7

**DEV-23, the 0x3E half only.** The register lists its fix phase as "6 (0x14), 7 (0x3E)";
the 0x14 half landed in Phase 6.

No other DEV identifier names Phase 7 as its fix phase. **DEV-09 and DEV-10 still list
fix phase 7 in the register but are already marked fixed in Phase 5**; Phase 7 completes
their intent (values that vary over time) without reopening them.

One deviation *may* belong here at the user's discretion: **DEV-07**. Its fix phase reads
"6/7 or 11 (deferred from 3)", and the Phase 3 note says the
`suppressPosRspMsgIndicationBit` fix "moves to the UDS behavior work (Phase 6/7 with 0x3E,
or Phase 11 with session handling)". Phase 6 deferred it. Section 7.6 below sets out why
0x3E forces a decision on it and recommends one; it is **not** taken as assigned.

### 1.4 Deferred beyond Phase 7

| Behavior | Where the plan puts it |
|---|---|
| S3 timeout, TesterPresent timing, session state machine, configurable P2/P2* | Phase 11 (V1.1) |
| 0x22, 0x19 sub-functions 0x01 and 0x0A | Phase 11 |
| Fault injection: drop, delay, NRC override, response pending, truncate, corrupt, ECU offline | Phase 10 (V1.1) |
| Multi-ECU profile, functional fan-out, 29-bit addressing | Phase 9 (V1.1) |
| Physical CAN, ELM327 acceptance, `hardware validated` | Phase 8 |
| 0x2E, mock 0x27 | Phase 12 |
| DEV-03, DEV-11 Mode 07, DEV-15, DEV-16 remaining halves, DEV-17, DEV-18 (done in 5.1) | unchanged, section 9 |

**Fault injection is explicitly Phase 10 and must not leak into the scenario engine.** A
scenario that drops a response or forces an NRC is a `FaultPolicy`, not a generator.

## 2. Applicable documentation

Split by half, because the two halves need different kinds of document.

### 2.1 UDS 0x3E — external specification

| Item | Version/revision | Source type | Full text available? | Relevant to this phase |
|---|---|---|---|---|
| ISO 14229-1 | **ISO 14229-1:2026, Edition 4, published 2026-06-05** | Reused from the section 7.1 inventory, verified in Phase 6 from the ISO catalogue record and an official national-body distributor listing | **No** — licensed | Defines service 0x3E, the zeroSubFunction sub-function, and in clause 6.5 "Server response implementation rules" the `suppressPosRspMsgIndicationBit` |
| AUTOSAR CP R24-11, SWS Diagnostic Communication Manager | R24-11, document ID 18 | Public specification of another standards organisation | **Yes** | `[SWS_Dcm_00251]` scopes 0x3E; section 7.3.4.2 gives the suppress-bit rules; section 7.2.4.3 defines Concurrent TesterPresent |
| ISO 14229-2 | not checked, not researched | — | **No** | **Out of scope.** It defines the S3 timer that TesterPresent exists to reset. Phase 7 implements no timer, so this document is named only to say what would be needed for Phase 11 |

The ISO 14229-1 revision is **reused from the inventory, not re-researched**, as instructed:
Phase 6 established it four commits ago and nothing has changed.

### 2.2 Scenario engine — libraries and runtime

No external protocol or standard is involved. Under
[section 11.5](../modernization-plan.md) the review is of the exact major versions in use,
with behavior confirmed experimentally where it matters.

| Item | Version in use | Source | Relevant to this phase |
|---|---|---|---|
| Pydantic | 2.13.5, pinned `>=2.13,<3` | Official project documentation (`docs/concepts/unions.md`, `docs/concepts/fields.md`, `docs/errors/usage_errors.md`), read at the version in use | Validating a list of heterogeneous generator configurations |
| Python | 3.12.12 in the venv; 3.12 and 3.13 supported | stdlib; `asyncio` task cancellation confirmed experimentally | The periodic tick and its shutdown behavior |
| `ecu_simulator.clock` | in-repo, Phase 4 | project source | The time seam the generators read |

**Finding, Pydantic 2.13.** `Field(discriminator='<literal field>')` over a union of models
is the documented way to validate a tagged list, and it reports errors against the matched
member only. **A `before`, `wrap` or `plain` validator on the discriminator field is
disallowed** and raises a usage error. That rules out the Phase 6 trick of accepting a bare
shorthand string via a `mode="before"` validator *on the tag itself*: a scenario entry must
carry its `type:` explicitly, or the union must be declared without a discriminator and
lose the good error messages. **Decision: require `type:` explicitly.** Configuration that
drives wire values should be unambiguous in the file anyway.

**Finding, asyncio.** A periodic task created with `create_task`, cancelled in the
`finally` of `run()` and awaited once, leaves no pending tasks and the loop closes
cleanly. Confirmed experimentally on 3.12.12: five ticks at 10 ms, then `cancelled`,
`task.cancelled() is True`, `asyncio.all_tasks()` empty afterwards. This matters because
the Phase 2 lifecycle tests require SIGINT/SIGTERM shutdown inside one second with no
leaked resources.

## 3. Normative-text availability

| Item | Current revision | Full normative text available | Official metadata available | Behavior in this phase that depends on it |
|---|---|---|---|---|
| ISO 14229-1 | **ISO 14229-1:2026 (Ed. 4)** | **No** | Yes | 0x3E request and response form, sub-function values, suppress-bit semantics, negative-response selection |
| ISO 14229-2 | not checked | **No** | not checked | Nothing. Named only as the home of S3, which is out of scope |

**The normative text is unavailable.** Nothing in Phase 7 will be marked
`standards validated`, no suppress-positive-response or session-timing semantics are
guessed at, and no requirement is reconstructed from memory. The scenario engine has no
normative-text question at all: it is project behavior.

Interoperability evidence is kept distinct from normative evidence throughout section 4.
An open-source implementation showing that a tester expects `7E 00` demonstrates that the
simulator will interoperate with that tester; it demonstrates nothing about ISO
conformance.

## 4. Evidence matrix for every wire-visible change

Current behavior below was measured through the dispatcher at HEAD `14b2dd4`, not recalled:

```
0x7E1  3E 00  -> 7F 3E 11      (the route's unsupported-service policy; no protocol claims 0x3E)
0x7E1  3E 80  -> 7F 3E 11
0x7E0  01 0D  -> 41 0D 00      (static; the profile sets vehicle.speed = 0)
0x7E0  03     -> 43 02 94 77 00 01
```

Note `7F 3E 11` **is not pinned by any test**; `grep` for it over `tests/`, `src/` and
`docs/` finds nothing. Phase 7 must pin it before changing it, as Phase 6 did for
`19 82 FF`.

### W1 — UDS 0x3E, sub-function 0x00

| Field | Content |
|---|---|
| Feature / DEV | **DEV-23**, 0x3E half |
| Existing behavior | No protocol claims SID 0x3E, so a physical route answers `7F 3E 11`. Measured above |
| Proposed behavior | `3E 00` is answered `7E 00`: the positive response service identifier and the sub-function echoed back |
| Applicable specification | ISO 14229-1:2026, service 0x3E TesterPresent |
| Normative text available | **No** |
| Official public material | AUTOSAR Dcm `[SWS_Dcm_00251]`: "The Dcm module shall implement the Tester Present (service 0x3E, diagnostic communication and security) of the Unified Diagnostic Services for the subfunction values 0x00 and 0x80." |
| Independent secondary references | None sought; the primary-ish and implementation evidence is unambiguous and consistent |
| Open-source interoperability | **Scapy** `UDS_TPPR` is service `0x7e` followed by a `zeroSubFunction` byte, and `UDS_TP` is `0x3e` plus a `subFunction` byte. **udsoncan** builds the request with `subfunction=0` and its response parser requires at least one data byte, documented as "Requests subfunction echoed back by the server. This value should always be 0". Two independent implementations agree on both directions |
| Capture evidence | None sought. The response carries no variable data, so a capture would add nothing beyond what two independent parsers already fix |
| Conflicting evidence | None found |
| Recommendation | **Implement, not standards validated.** Corroborated by an official public specification of another body plus two independent implementations, agreeing byte for byte. The existing DEV-23 strict xfail already asserts exactly `7E 00` |
| Conformance status after | implemented yes, unit tested yes, integration tested yes, hardware validated no, standards validated **no**. Interoperability evidence: the request and response shapes udsoncan and Scapy build and parse |

### W2 — UDS 0x3E, sub-function 0x80 (suppressPosRspMsgIndicationBit)

| Field | Content |
|---|---|
| Feature / DEV | **DEV-23** for the service; the bit itself is the subject of **DEV-07**, see section 7.6 |
| Existing behavior | `3E 80` is answered `7F 3E 11`, like any unclaimed service. Measured above |
| Proposed behavior | `3E 80` performs the service and sends **nothing** |
| Applicable specification | ISO 14229-1:2026 clause 6.5, "Server response implementation rules" |
| Normative text available | **No.** The clause number comes from AUTOSAR's citation of it, not from reading it |
| Official public material | AUTOSAR Dcm section 7.3.4.2: "The 'suppressPosRspMsgIndicationBit' is part of the subfunction parameter structure (Bit 7 based on second byte of the diagnostic message, see ISO14229-1 Section 6.5: Server response implementation rules)." `[SWS_Dcm_00200]`: "If the 'suppressPosRspMsgIndicationBit' is TRUE, the DSD submodule shall NOT send a positive response message." `[SWS_Dcm_00201]`: "The DSD submodule shall remove the 'suppressPosRspMsgIndicationBit' (by masking the Bit) from the diagnostic message." `[SWS_Dcm_00204]`: the handling applies only to a service that **has** a sub-function. `[SWS_Dcm_00251]` lists 0x80 as one of 0x3E's two supported sub-function values |
| Independent secondary references | None sought |
| Open-source interoperability | Neither udsoncan nor Scapy exercises the bit for 0x3E: both are clients and a client that sets it expects no answer, so there is nothing for them to parse. **This row therefore has no interoperability evidence, only documentary evidence.** Recorded as a difference from W1, not glossed over |
| Capture evidence | None |
| Conflicting evidence | None found. AUTOSAR additionally defines "Concurrent TesterPresent" as `3E 80` received with **functional** addressing (section 7.2.4.3, and the glossary entry naming the bytes `3E 80`), which keeps a non-default session alive. That behavior depends on session state and is Phase 11; it is **not** proposed here, and in the shipped profile UDS is not enabled on the functional route at all, so a functional `3E 80` cannot reach this protocol |
| Recommendation | **Implement for 0x3E, not standards validated.** Three named requirements of an official public specification state the rule, the masking and its scope, and name 0x80 as one of 0x3E's two values. Plan section 3 already commits V1.0's 0x3E to honouring the bit. See section 7.6: **where the rule is implemented is a decision the user must take**, because implementing it correctly reaches 0x10 and 0x11 as well |
| Conformance status after | implemented yes, unit tested yes, integration tested yes, hardware validated no, standards validated **no**. **No interoperability evidence** |

### W3 — UDS 0x3E, unsupported sub-function

| Field | Content |
|---|---|
| Feature / DEV | **DEV-23** |
| Existing behavior | `7F 3E 11` for every 0x3E request |
| Proposed behavior | Any sub-function other than 0x00 and 0x80 is answered `7F 3E 12` (subFunctionNotSupported) |
| Normative text available | **No** |
| Official public material | AUTOSAR `[SWS_Dcm_00251]` supports 0x00 and 0x80 only, so nothing else is a supported sub-function |
| Open-source interoperability | **udsoncan** lists exactly two negative responses for TesterPresent: `SubFunctionNotSupported` (0x12) and `IncorrectMessageLengthOrInvalidFormat` (0x13) |
| Conflicting evidence | None |
| Recommendation | **Implement, not standards validated.** It is also the same ordering rule Phase 6 chose for 0x19: sub-function before the length that sub-function requires |

### W4 — UDS 0x3E, malformed length

| Field | Content |
|---|---|
| Feature / DEV | **DEV-23** |
| Existing behavior | `7F 3E 11` |
| Proposed behavior | `3E` alone, and any request longer than two bytes, are answered `7F 3E 13` |
| Normative text available | **No** |
| Official public material | 0x3E's request is the service identifier and a sub-function, so two bytes; AUTOSAR describes no further parameter |
| Open-source interoperability | **udsoncan** lists `IncorrectMessageLengthOrInvalidFormat` among the two NRCs it expects |
| Conflicting evidence | None |
| Recommendation | **Implement, not standards validated** |

### W5 — vehicle signal values varying over time

| Field | Content |
|---|---|
| Feature / DEV | no DEV; completes the intent of DEV-09 and DEV-10, both already fixed |
| Existing behavior | Every signal is whatever the profile set. `01 0D` answers `41 0D 00` on the shipped profile, for the life of the process |
| Proposed behavior | **Unchanged unless a scenario is configured.** With a scenario, a signal's value at time `t` is its generator's value at `t`, encoded by the existing unchanged encoders |
| Applicable specification | None. The *encoding* of every parameter is unchanged and was reviewed in Phase 5; what changes is the physical value fed to it, which is simulation, not protocol |
| Normative text available | n/a |
| Recommendation | **Implement, and ship no scenario in `ice_default.yaml`.** Every golden and characterization byte, and the V1.0 acceptance bench, depends on the shipped profile's static values. A second profile demonstrates the feature. This keeps Phase 7's wire-visible surface at zero for the default configuration, which is the only reason this row is not a wire change at all |
| Conformance status after | a new runtime row, `standards validated` not applicable; the parameter rows are untouched |

### W6 — DTC state changing over time

| Field | Content |
|---|---|
| Feature / DEV | no DEV; uses the Phase 6 store |
| Existing behavior | The store holds what the profile configured until a Mode 04 or 0x14 clears it |
| Proposed behavior | **Unchanged unless a scenario is configured.** A scenario may raise, clear or update a configured code at a scheduled time, only through `DtcStore.update()` and `DtcStore.clear()` |
| Applicable specification | None for the transition itself. What the resulting bytes look like was reviewed in [0004](0004-phase-6-dtc-evidence.md) and does not change: services 03, 04, 0x14 and 0x19/02 encode whatever the store says |
| Normative text available | n/a |
| Conflicting evidence | None. One constraint carried from 0004: a scenario must not invent a trouble code. `DtcStore.update()` raises `KeyError` for an unconfigured code by design, so a scenario naming one is rejected at load rather than at runtime |
| Recommendation | **Implement.** No new encoding, no new status bit, no change to the availability mask |

## 5. Proposed scenario-engine architecture

The smallest thing that satisfies the plan. Deliberately **not** a workflow engine: no
conditions, no branching, no triggers on diagnostic requests, no fault injection.

```
profile.scenario            (optional; absent means today's behavior exactly)
  signals:   [ {path, type, ...} ]        vehicle-wide
  ecus.<name>.dtc_events: [ {at, code, action} ]   per ECU, beside the existing dtcs:
                  │
                  │  validated by Pydantic at load: unknown signal path, unknown
                  │  trouble code, bad generator parameters all rejected before a
                  │  socket opens
                  ▼
        Scenario            pure, no state, no clock
          value_at(path, t) -> float | int
          events_in(t0, t1) -> [DtcEvent]
                  │
                  ▼
        ScenarioRunner.apply(t)           the only writer
          for each signal:  VehicleState.set(path, generator(t))
          for each due event: DtcStore.update(...) | DtcStore.clear()
                  ▲
                  │ called with clock.now() - t0 by
                  ├── the Dispatcher, once before handling a request
                  └── a periodic tick task in run(), cancelled on shutdown
```

Rules this preserves:

1. **Generators are pure functions of `t`.** No generator holds state, reads a clock, or
   knows what a signal means. `value(t)` called twice with the same `t` returns the same
   number.
2. **`ScenarioRunner` is the only writer**, and it writes only through `VehicleState.set`
   and `DtcStore.update` / `DtcStore.clear` — the domain APIs that already exist. It
   produces no bytes and imports no protocol module.
3. **Protocol layers stay encoders and readers.** `ObdProtocol` and `UdsProtocol` are not
   touched by this half of the phase. They continue to read current state and encode it.
4. **No wall-clock sleep anywhere in a handler.** The only `sleep` is inside the tick
   task, which is runtime code, not protocol code.
5. **No randomness at all.** Not seeded randomness: none. Every generator is
   deterministic by construction, so "no randomness unless explicitly seeded" is satisfied
   by there being nothing to seed. If a future phase wants noise, it brings a seed with it.

### 5.1 The six generators

Each a pure function of elapsed seconds `t`. Parameters are physical quantities.

| `type` | Parameters | Value at `t` |
|---|---|---|
| `constant` | `value` | `value` |
| `ramp` | `from`, `to`, `over` | linear from `from` to `to` across `[0, over]`; holds `to` afterwards |
| `sine` | `centre`, `amplitude`, `period`, `phase` (default 0) | `centre + amplitude * sin(2*pi*t/period + phase)` |
| `stepped` | `values` (list), `interval` | `values[floor(t/interval) mod len(values)]` — a repeating staircase |
| `sequence` | `steps` (list of `{value, for}`) | each value held for its own duration, in order, once; holds the last value afterwards |
| `timeline` | `points` (list of `{at, value}`) | the value of the latest point whose `at <= t`; the first point's value before that |

`stepped` repeats, `sequence` runs once, `timeline` is absolute. They are distinct enough
to earn separate names, and between them they cover the plan's list without a seventh.

**Float determinism caveat, recorded honestly.** `sine` uses `math.sin`, which is libm;
the last bit could in principle differ between platforms, and the encoders truncate, so a
value sitting exactly on a truncation boundary could encode to a different byte on a
different machine. Mitigation: scenario tests assert on values chosen away from those
boundaries, and the byte-exact assertions use `constant`, `timeline` and `stepped`, which
involve no transcendental function. Recorded as a known risk rather than discovered later
on a CI matrix.

### 5.2 DTC events

`{at: <seconds>, code: <configured code>, action: <action>}` with actions limited to what
the store already supports: `raise_pending`, `raise_confirmed`, `request_indicator`,
`clear_code`, `clear_all`. Each maps to one `DtcStore.update()` or `DtcStore.clear()`
call. No action can create a code, change an encoding, or set a status bit the store does
not model.

Events fire once, when the elapsed time passes their `at`. `ScenarioRunner` keeps the last
applied time so `events_in(previous, now)` is exact and an event is never applied twice —
that is the runner's only piece of state, and a `SimulatedClock` makes it fully
inspectable.

### 5.3 Validation before runtime

Rejected at load, with the path to the problem, as the Phase 4 schema already does for
everything else: an unknown signal path; a signal path the configured vehicle does not
have; a trouble code not configured on that ECU; a negative or zero `period`, `over` or
`interval`; an empty `values`, `steps` or `points`; `points` not in ascending `at` order;
a missing or unknown `type`.

## 6. Proposed clock and timing model

| Concern | Decision |
|---|---|
| Time source | The existing `Clock` protocol. `MonotonicClock` in production, constructed in `run()`; `SimulatedClock` in tests |
| Scenario origin | `t0` captured once when the runtime starts. Scenario time is `clock.now() - t0`, so a scenario always begins at 0 and a restart restores the configured initial state — which the independent acceptance run already confirmed for the DTC store |
| Who advances time | Nothing in this project advances time. The clock reports it; the runner reads it |
| Refresh on read | The `Dispatcher` calls `runner.apply(elapsed)` once before dispatching a request |
| Periodic tick | One task in `run()`, period from configuration with a documented default, cancelled and awaited in the existing `finally` |
| Injectability | `Dispatcher` and `run()` take an optional clock; tests pass `SimulatedClock` and call `advance()` |

**The one design point worth the user's attention.** The plan says "lazy evaluation on
read". Resolving it in the `Dispatcher`, not inside a protocol or inside
`VehicleState.get()`, is what keeps two properties that matter:

- `VehicleState` stays a plain state holder and never learns about generators, so
  `protocols/` continues to read a number rather than ask a scenario for one;
- a read still observes rather than advances. `apply(t)` is idempotent for a fixed `t`:
  two requests at the same clock instant leave identical state and return identical bytes,
  which is exactly what `test_handling_a_request_does_not_mutate_the_vehicle` asserts
  today. With a `SimulatedClock` that test remains true literally; with a real clock it is
  true of the simulation, which is advanced by time passing and not by anyone asking.

The alternative — refresh only on the periodic tick — would make a value's granularity the
tick period and would contradict the plan's wording. The other alternative — compute
inside `VehicleState.get()` — would put scenario knowledge under the protocol layer. Both
are rejected. If the user reads "reads observe state" more strictly than the reasoning
above, the fallback is tick-only refresh, and that is a one-line change to where `apply`
is called.

## 7. Proposed UDS 0x3E behavior

Evaluated entirely separately from the scenario engine, and implemented in its own commit.

| Aspect | Proposal |
|---|---|
| Current behavior | `3E 00` and `3E 80` both answered `7F 3E 11` by the route's unsupported-service policy; no protocol claims the SID. Measured, not recalled |
| Minimum Phase 7 behavior | Claim SID 0x3E in `UdsProtocol`; validate the request; answer `7E 00`; honour the suppress bit. No state of any kind |
| Request forms supported | Exactly two bytes: `3E 00` (zeroSubFunction) and `3E 80` (zeroSubFunction with the suppress bit) |
| Positive response | `7E 00` — the positive-response service identifier and the sub-function echoed, which is what Scapy parses and udsoncan expects |
| Malformed request | `3E` alone, or three bytes or more: `7F 3E 13` |
| Unsupported sub-function | Anything but 0x00 and 0x80: `7F 3E 12`. Checked before the length that sub-function requires, matching the ordering Phase 6 chose for 0x19 |
| Suppress bit | `3E 80`: the service is performed and **nothing is sent**. See 7.6 for where the rule lives |
| Session timeout / S3 | **Not in this phase.** No timer is started, read or reset; no session state is created or consulted; nothing claims ISO 14229 session compliance. Plan section 3 says so and plan section 4 puts S3, TesterPresent timing and the session state machine in Phase 11 |
| Concurrent TesterPresent | **Not in this phase.** AUTOSAR defines it as functionally addressed `3E 80` keeping a non-default session alive; it is meaningless without session state. In the shipped profile UDS is not enabled on the functional route, so it is unreachable regardless |

### 7.6 Where the suppress-bit rule lives — a decision for the user

This is the one thing in Phase 7 that cannot be settled without a ruling.

The evidence in W2 is about the bit, not about 0x3E: AUTOSAR puts the handling in the DSD
submodule and applies it to **any** service that has a sub-function
(`[SWS_Dcm_00204]`), masking the bit off before the service sees the value
(`[SWS_Dcm_00201]`). That is precisely the behavior DEV-07 records as missing for 0x10 and
0x11, where `10 83` returns `7F 10 12` today.

| Option | What it means | Consequence |
|---|---|---|
| **A** — handle the bit inside the 0x3E handler only | 0x10 and 0x11 unchanged, DEV-07 stays open with both xfails in place | Literal compliance with "do not fix DEV-07". But the same bit then has two behaviors in one ECU, the rule is implemented in the wrong layer, and Phase 11 has to move it and un-pick this |
| **B** — handle the bit once for every sub-function service in `UdsProtocol` | 0x3E works, and `10 83` / `11 81` start performing the service silently. **DEV-07 is fixed**, in one of the two phases the roadmap nominates for it ("Phase 6/7 with 0x3E, or Phase 11") | Two extra wire changes, in their own commit, with DEV-07's two strict xfails flipping. Needs the user's explicit approval |
| **C** — implement 0x3E without the suppress bit | `3E 80` would get `7F 3E 12` | Contradicts plan section 3, which commits V1.0's 0x3E to honouring the bit. Not recommended |

**Recommendation: B, subject to your approval**, taken as a separate commit after W1/W3/W4
land, with its own characterization transitions. The reason is architectural rather than
enthusiasm for closing a deviation: the suppress bit is a framing rule about sub-functions,
not a property of TesterPresent, and A would duplicate it in the wrong place while leaving
the simulator self-inconsistent. If you prefer A, it is a smaller change and Phase 7 still
satisfies its own scope; DEV-07 then stays open and pinned exactly as it is now.

Either way **no session state and no timer is introduced**, and nothing about P2, P2* or S3
changes.

## 8. DEV identifiers Phase 7 will change

| DEV | Change | xfail |
|---|---|---|
| **DEV-23** | 0x3E half fixed; the entry becomes fully fixed, both halves | `test_0x3e_tester_present_corrected` flips |
| **DEV-09, DEV-10** | No behavior change. Their register rows say "Fixed in Phase 5" and the fix-phase column says 7; the column is corrected to `5` with a note that Phase 7 supplies the variation their "corrected behavior" anticipated | none; both already plain-tested |
| **DEV-07** | **Only under option B**, and only with your approval | `test_0x10_suppress_positive_response_bit_corrected` and `test_0x11_suppress_positive_response_bit_corrected` both flip |

## 9. DEV identifiers explicitly preserved

Each is pinned today and each pin must still pass unchanged after Phase 7.

| DEV | Behavior preserved | Pinned by |
|---|---|---|
| **DEV-03** | Mode 09 PID 0A: no count byte, name left-padded, 22 bytes | `tests/characterization/test_mode09_pid0a_frozen.py`, plus strict xfail `test_mode09_pid0a_ecu_name_corrected_framing` |
| **DEV-07** | `10 81` → `7F 10 12`, `11 81` → `7F 11 12` — **under option A only**; option B changes them deliberately | strict xfails `test_0x10_/test_0x11_suppress_positive_response_bit_corrected`, plus plain pins in `test_uds_protocol.py` |
| **DEV-11 Mode 07** | `07` answers with silence | strict xfail `test_mode07_pending_dtcs_is_answered`, plus `test_mode07_is_a_valid_sid_but_gets_no_response` |
| **DEV-15** | `03 00` → `43 00 02 …`, and Mode 04 deliberately does not echo | `test_mode03_with_trailing_byte_echoes_it_into_the_response`, `test_a_trailing_request_byte_is_echoed_after_the_service_id`, `test_mode04_with_a_trailing_byte_is_acknowledged_without_echoing_it` |
| **DEV-16, third byte** | The DTC number's third byte stays `0x01` | `test_the_frozen_third_byte_is_a_named_constant_so_the_freeze_is_visible`, `test_uds_encoding_appends_the_frozen_third_byte_and_the_derived_status` |
| **DEV-16, 0x19/01 and /0A** | Both answered `7F 19 12` | `test_0x19_negative_responses`, `test_0x19_an_unsupported_subfunction_is_rejected_as_one_at_any_length` |
| **DEV-17** | `10 0x` → `50 0x 00 1E 0B B8`, no session state | `test_0x10_positive_response_with_fixed_session_parameter_record`, `test_0x10_answers_with_the_fixed_session_parameter_record` |
| **DEV-18** | Up to six Mode 01 parameters, fixed in Phase 5.1 | the datasheet-capture tests in `test_obd_protocol.py` |
| Every Phase 6 behavior | Mode 03/04, 0x14, 0x19/02, availability mask `0x8C`, derived status | the Phase 6 unit, characterization and integration suites |

Additionally, **the shipped profile gains no scenario**, so every existing golden byte
stays reachable by exactly the request that produces it today.

## 10. Behaviors to defer

| Behavior | Reason |
|---|---|
| S3 timer, TesterPresent timing, session state | Phase 11; plan section 3 excludes them from V1.0's 0x3E explicitly |
| Concurrent TesterPresent (functional `3E 80` keep-alive) | Needs session state; unreachable in the shipped profile anyway |
| Configurable P2 / P2* (DEV-17) | Phase 11 |
| Fault injection of any kind: drop, delay, NRC override, response pending, truncate, corrupt, ECU offline | Phase 10. A scenario changes physical state; it does not interfere with responses |
| Scenario conditions, branching, or triggers on diagnostic requests | Not in the plan. The plan asks for pure functions of `t` and timed events, and nothing more |
| Randomness, seeded or otherwise | Not needed by any Phase 7 requirement |
| A scenario in `ice_default.yaml` | Would move the V1.0 acceptance bench and every golden byte |
| Mode 01 PID 0x01, Mode 07, DEV-03, DEV-15, DEV-16's third byte | Unchanged from their existing deferrals |

## 11. Planned acceptance criteria and how each is verified

Every criterion maps to an automated test, a static check, or a named command, as
[section 10](../modernization-plan.md) requires.

| Criterion | Verification |
|---|---|
| All six generators are pure functions of `t` | Unit tests per generator, including `value(t) == value(t)` on repeat calls and known values at chosen `t` |
| Determinism end to end | A scenario run twice against a `SimulatedClock` with the same advance sequence produces identical bytes |
| Test-controlled clock; no wall-clock dependence in scenario tests | The scenario test module constructs `SimulatedClock` only; a test asserts no scenario test imports `time` |
| No `sleep` in any protocol handler | A test greps the protocol packages for `sleep`, alongside the existing transport-isolation test pattern |
| Scenarios mutate domain state only | A test asserts `scenario/` imports nothing from `protocols/`, mirroring `test_transport_isolation.py` |
| Signal values reach the wire | Integration: a scenario profile on vcan, advance, read `01 0D`, observe the changed byte |
| DTC events reach the wire through supported APIs only | Unit: a timed `raise_confirmed` appears in Mode 03 and in `0x19/02`; a `clear_all` empties both views |
| A scenario cannot invent a trouble code | Schema test: a `dtc_events` entry naming an unconfigured code is rejected at load with its path |
| Bad generator parameters rejected before runtime | Schema tests for each rule in 5.3 |
| No scenario means today's behavior | The entire existing suite passes unchanged; a test asserts the shipped profile has no scenario |
| Clean startup and shutdown with the tick running | The Phase 2 lifecycle integration tests, unchanged: SIGINT and SIGTERM exit 0 within a second; plus a test that the tick task is cancelled and awaited |
| `3E 00` → `7E 00` | Characterization transition plus unit and integration tests |
| `3E 80` → nothing | Unit and integration tests |
| `3E 01` → `7F 3E 12`; `3E`, `3E 00 00` → `7F 3E 13` | Unit tests |
| `7F 3E 11` pinned before it changes | A characterization test added in its own commit first, as Phase 6 did for `19 82 FF` |
| DEV-07 unchanged (option A) or changed deliberately (option B) | Its two strict xfails, either left in place or flipped in their own commit |
| Every preserved deviation still preserved | The full suite, with the section 9 pins named in the phase report |
| Regression | Full unit, characterization and integration suites; `ruff`; `mypy`; CI on 3.12 and 3.13 |

## 12. Outcome

| Item | Decision |
|---|---|
| Scenario engine | Implement as section 5: six pure generators, one `ScenarioRunner` writer, Pydantic-validated configuration, `type:` required explicitly |
| Clock model | Section 6: existing `Clock`, `t0` at startup, refresh in the `Dispatcher`, one cancellable tick task |
| Shipped profile | **No scenario.** A separate demonstration profile |
| 0x3E W1, W3, W4 | Implement; not standards validated |
| 0x3E W2, the suppress bit | Implement; **where it lives needs your ruling, options A/B/C in 7.6, recommendation B** |
| S3, session state, Concurrent TesterPresent | Defer to Phase 11 |
| Fault injection | Defer to Phase 10; explicitly not a scenario feature |
| DEV-23 | Closed by this phase |
| DEV-07 | Option B closes it with your approval; option A leaves it open and pinned |
| Everything in section 9 | Preserved, each with a named pin |
