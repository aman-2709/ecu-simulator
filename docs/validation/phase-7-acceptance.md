# Phase 7 — Verification and Acceptance Report

**Result: accepted.** Phase 7, the deterministic scenario engine and minimal UDS 0x3E,
passed independent manual acceptance on **2026-09-22** and every automated gate in
[modernization-plan.md section 10](../modernization-plan.md).

| | |
|---|---|
| Phase | 7 — Deterministic scenario engine and minimal 0x3E (V1.0) |
| Branch | `modernization` |
| Revision under test | **`08698d299a1d9c1a41743c2ccac84cf6bca15d69`** (`08698d2`) |
| Functional code delivered by | `cd82837` … `c625585` (seven commits); `08698d2` adds documentation only |
| Manual acceptance date | 2026-09-22 |
| Manual UDS result | **10 / 10 passed** |
| Manual scenario result | **21 / 21 passed** |
| Automated result | **837 passed, 2 expected failures**; 53 integration; 97% coverage |
| CI | run **35796655595**, conclusion **success** |
| `standards validated` after this phase | **no**, on every row. Unchanged and deliberate |
| `hardware validated` after this phase | **no**, on every row. Phase 8 |

## Provenance, stated before anything else

This report draws on **three separate sources of evidence**, and they are never merged:

| Source | Who produced it | How it is marked |
|---|---|---|
| **Manual acceptance** | The reviewer, by hand, on 2026-09-22 | "reported by the reviewer". Their values, their run |
| **Scripted re-run** | Written and executed while producing this report, 2026-09-22 | "scripted re-run". A second, independent run |
| **Automated suites** | `pytest` at `08698d2` | Section 6 |

Two limits follow, and both matter more than the convenience of a tidier document:

- **The reviewer's own acceptance script was not retained and was not available here.**
  `scripts/acceptance/phase7_scenario_acceptance.py` is therefore **not** that script. It
  was written for this report, checks the same behaviors at the same checkpoints, and was
  run; its results are reported as its own. Nothing in this document reconstructs the
  reviewer's script or presents anything as its output.
- **Every CAN trace in section 5 is from the scripted re-run**, captured with `candump`
  during runs performed while writing this report. No trace from the manual session was
  available, and none has been invented.

Where the two runs agree, section 4 says so explicitly. That agreement is itself the
useful finding: two independently constructed runs of the same scenario produced the same
bytes.

## 1. Objective, scope, environment and revision

### 1.1 Objective

Establish that the behavior Phase 7 introduced is the behavior that reaches a diagnostic
tester on a real ISO-TP socket — not merely that it passes tests written alongside it.

### 1.2 In scope

- UDS `0x3E` TesterPresent, stateless (DEV-23).
- `suppressPosRspMsgIndicationBit` handled generically for every sub-function service
  (DEV-07), including ReadDTCInformation (DEV-24).
- The deterministic scenario engine: six generators, `ScenarioRunner`, request-side
  synchronisation, the periodic tick, load-time validation.
- That the **default profile is unchanged**, which is the condition on which every
  pre-existing golden byte still stands.

### 1.3 Out of scope

Everything the plan defers: S3 and session timing (Phase 11), fault injection (Phase 10),
multi-ECU fan-out and 29-bit addressing (Phase 9), CAN FD (Phase 13), and **physical CAN
and ELM327 acceptance (Phase 8)**. See sections 8 and 9.

### 1.4 Environment

| Item | Value |
|---|---|
| Host OS | Ubuntu 22.04.5 LTS |
| Kernel | `6.8.0-138-generic` |
| CAN transport | Linux **`vcan`** with the in-tree **kernel ISO-TP** module. **Not physical CAN** |
| Python | 3.12.12 (`uv`-created virtual environment) |
| `can-isotp` | 2.0.7 |
| `pydantic` | 2.13.5 |
| `pytest` / `ruff` / `mypy` | 9.1.1 / 0.16.8 / 2.3.1 |
| `can-utils` | 2020.11.0-1 (`candump`) |
| Tester | ISO-TP sockets with TX padding to DLC 8. **No ELM327 and no OBD dongle was used** |

### 1.5 Revision

`08698d299a1d9c1a41743c2ccac84cf6bca15d69`, verified as both the local `HEAD` and
`origin/modernization` at the time of writing, with a clean working tree.

## 2. Setup and reproducible commands

Neither the scenario run nor the integration suite needs root, and neither touches the
host's CAN interfaces: both create a private user and network namespace, put a `vcan0`
inside it, and let it disappear on exit. That also makes them immune to a simulator
already running on the host.

```bash
# Automated: unit, characterization, coverage
.venv/bin/pytest tests/unit tests/characterization -q --cov=ecu_simulator --cov-report=term

# Automated: integration, in a private namespace
scripts/run_integration_tests.sh

# Static analysis
.venv/bin/ruff check src tests scripts
.venv/bin/mypy src

# Scenario acceptance, in a private namespace; ~2 minutes of real time
scripts/acceptance/run_phase7_acceptance.sh --trace /tmp/phase7_trace.log --json /tmp/phase7.json

# The demonstration profile, by hand
scripts/setup_vcan.sh                                     # once, as root
ecu-simulator --profile src/ecu_simulator/profiles/ice_scenario.yaml --interface vcan0
```

`scripts/acceptance/phase7_scenario_acceptance.py` exits 0 only when every check passes
**and** the simulator exits cleanly.

## 3. Manual UDS verification — 10 / 10 reported

Default ICE profile (`ice_default.yaml`) on `vcan0`. The reviewer reported **10 of 10
passing**, described in seven groups.

The **Expected** column was measured at `08698d2` while writing this report, through the
dispatcher and again on the wire; it is not recalled. The **Reviewer** column states only
what was reported. Where a group was reported by description rather than by exact bytes,
the table says so rather than attributing specific bytes to the reviewer.

| # | Request | Expected at `08698d2` | Reviewer | Scripted re-run |
|---|---|---|---|---|
| 1 | `3E 00` | `7E 00` | reported: produces `7E 00` | `7E 00` — confirmed |
| 2 | `3E 80` | **no response** | reported: produces no response | no frame transmitted — confirmed |
| 3 | `3E 01` | `7F 3E 12` | within "unsupported subfunctions … return the expected NRCs"; exact bytes not itemised in the report received | `7F 3E 12` — confirmed |
| 4 | `3E` | `7F 3E 13` | within "malformed requests return the expected NRCs"; exact bytes not itemised | `7F 3E 13` — confirmed |
| 5 | `10 03` | `50 03 00 1E 0B B8` | baseline for group 6 | `50 03 00 1E 0B B8` — confirmed |
| 6 | `10 83` | **no response** | reported: suppression works for `0x10` | no frame transmitted — confirmed |
| 7 | `10 85` | `7F 10 12` | reported: without hiding negative responses | `7F 10 12` — confirmed |
| 8 | `19 02 FF` | `59 02 8C 94 77 01 0C 00 01 01 0C` | reported: returns the expected DTCs | confirmed, **multi-frame** (section 5.1) |
| 9 | `19 82 FF` | **no response** | reported: suppresses its positive response | no frame transmitted — confirmed |
| 10 | `19 82` | `7F 19 13` | reported: returns `7F 19 13` | `7F 19 13` — confirmed |

The reviewer additionally reported that **the multi-frame UDS response completed
successfully through ISO-TP flow control**; row 8 is that response, and section 5.1 shows
the flow-control exchange frame by frame.

Rows 6, 7 and 10 together are the load-bearing evidence for DEV-07 and DEV-24, and the
reason is worth stating. `10 83` being silent could be produced by a wrong implementation
that drops any response whose request had bit 7 set. `10 85` returning `7F 10 12` rules
that out: bit 7 is masked off and the sub-function then matched on its value, so an
unsupported one still refuses. `19 82` returning `7F 19 13` rather than `7F 19 12` rules
it out a second time, because a length error can only arise if `0x82` was masked to `0x02`
and dispatched as report type `0x02`.

## 4. Manual scenario acceptance — 21 / 21 reported

Profile `ice_scenario.yaml`. The reviewer reported **21 of 21 passing**.

### 4.1 The corrected expectation: timelines are stepwise, not interpolated

The reviewer records that **a first version of the manual script incorrectly assumed the
timeline generator interpolates between points**, and that the corrected script verifies
the actual stepwise behavior. **This report documents only the corrected expectations.**

The implementation is unambiguous and the corrected reading matches it. A `timeline`
yields *"the value of the latest point whose `at` has arrived"*, held until the next point
— `TimelineSignal.value_at` in `src/ecu_simulator/scenario/generators.py` walks the points
and keeps the last one reached. It performs no interpolation, and
[decisions/0006](../decisions/0006-phase-7-scenario-and-testerpresent.md) §5.1 defines it
that way.

The clearest instance is road speed at **t = 25 s**, between the `t=20 → 30 km/h` and
`t=30 → 55 km/h` points:

| Reading | Value | Byte | Status |
|---|---|---|---|
| Stepwise — the t=20 point held | 30 km/h | `41 0D 1E` | **correct, and observed** |
| Interpolated — what the first script assumed | 42.5 km/h | `41 0D 2A` | wrong; not the implemented behavior |

**Ramps are a different generator and do interpolate**, which is why coolant is 50 °C at
t=25 rather than holding its starting value. The corrected expectations reflect both:
timelines step, ramps slope. That the two behave differently is by design, and the
reviewer's corrected results are consistent with it at every checkpoint.

### 4.2 Checkpoints

**Expected** derives from `ice_scenario.yaml` and was confirmed against the implementation
before the scripted run. **Reviewer** is what was reported. **Scripted re-run** is the
second run's observation.

| t | Behavior | Expected | Reviewer | Scripted re-run |
|---|---|---|---|---|
| 1 s | Speed zero | `41 0D 00` | speed zero | `41 0D 00` |
| 1 s | No DTCs | `43 00` / `59 02 8C` | no DTCs | `43 00` / `59 02 8C` |
| 25 s | Speed 30 km/h **(stepwise)** | `41 0D 1E` | 30 km/h | `41 0D 1E` |
| 25 s | Engine speed 1600 rpm | `41 0C 19 00` | 1600 rpm | `41 0C 19 00` |
| 25 s | Coolant 50 °C **(ramp)** | `41 05 5A` | 50 °C | `41 05 5A` |
| 25 s | Repeated speed read unchanged | `41 0D 1E` again | unchanged | identical |
| 35 s | No DTCs in OBD or UDS | `43 00` / `59 02 8C` | none | `43 00` / `59 02 8C` |
| 45 s | P0128 **pending** in UDS | `59 02 8C 01 28 01 04` | pending | `59 02 8C 01 28 01 04` |
| 45 s | Not yet confirmed in OBD | `43 00` | not confirmed | `43 00` |
| 78 s | P0128 **confirmed** in OBD | `43 01 01 28` | confirmed | `43 01 01 28` |
| 78 s | Status `0x8C` in UDS | `59 02 8C 01 28 01 8C` | status `0x8C` | `59 02 8C 01 28 01 8C` |
| 78 s | Coolant 92 °C | `41 05 84` | — | `41 05 84` |
| 82 s | Mode 04 clears P0128 | `44` | clears | `44` |
| 82 s | Cleared in OBD **and** UDS | `43 00` / `59 02 8C` | both | `43 00` / `59 02 8C` |
| 82 s | Repeated read remains clear | `43 00` | remains clear | `43 00` |
| 125 s | Fault has not returned | `43 00` / `59 02 8C` | not returned | `43 00` / `59 02 8C` |
| 125 s | Vehicle stationary | `41 0D 00` | stationary | `41 0D 00` |
| 125 s | Engine speed 800 rpm | `41 0C 0C 80` | 800 rpm | `41 0C 0C 80` |
| 125 s | Coolant 92 °C | `41 05 84` | 92 °C | `41 05 84` |
| exit | Clean shutdown | status 0 | exit code 0 | status 0 |

Status byte `0x8C` at 78 s decomposes as bit 2 `pendingDTC` `0x04` (set at t=40), bit 3
`confirmedDTC` `0x08` and bit 7 `warningIndicatorRequested` `0x80` (both set at t=75),
which is also exactly the `DTCStatusAvailabilityMask` the server advertises. The two
values coinciding is a property of this profile, not a coincidence worth relying on.

### 4.3 Scripted re-run result

```
23/23 checks passed
simulator exit status 0 (clean)
```

Command: `scripts/acceptance/run_phase7_acceptance.sh`. It uses 23 checks rather than 21
because two of the reviewer's checkpoints are expressed here as two assertions each. **No
disagreement with the reviewer's reported values was found at any checkpoint.**

## 5. CAN / ISO-TP traces

Captured with `candump -L` during the runs described above, in a private namespace.
Timestamps are relative to the first frame; `vcan0` omitted for width.

### 5.1 UDS on the default profile — suppression and flow control

```
 0.000  7E1#023E000000000000     3E 00        TesterPresent
 0.001  7E9#027E00               7E 00        positive response

 0.401  7E1#023E800000000000     3E 80        TesterPresent, suppress bit set
   ...                                        NO RESPONSE FRAME. The 1.9 s gap that
                                              follows is the tester's own timeout

 2.303  7E1#023E010000000000     3E 01        unsupported sub-function
 2.303  7E9#037F3E12             7F 3E 12     subFunctionNotSupported

 2.703  7E1#013E000000000000     3E           one byte, malformed
 2.704  7E9#037F3E13             7F 3E 13     incorrectMessageLengthOrInvalidFormat

 3.104  7E1#031902FF00000000     19 02 FF     ReadDTCInformation, status mask FF
 3.104  7E9#100B59028C947701     FIRST FRAME, 11 data bytes
 3.104  7E1#3000000000000000     FLOW CONTROL from the tester: clear to send
 3.104  7E9#210C0001010C         CONSECUTIVE FRAME, sequence 1
                                 reassembled: 59 02 8C 947701 0C 000101 0C

 3.504  7E1#031982FF00000000     19 82 FF     the same read, suppress bit set
   ...                                        NO RESPONSE FRAME

 5.406  7E1#0219820000000000     19 82        suppressed, but no status mask
 5.406  7E9#037F1913             7F 19 13     length error, and NOT suppressed

 5.806  7E1#0210030000000000     10 03        DiagnosticSessionControl
 5.807  7E9#065003001E0BB8       50 03 00 1E 0B B8

 6.207  7E1#0210830000000000     10 83        the same session, suppress bit set
   ...                                        NO RESPONSE FRAME

 8.108  7E1#0210850000000000     10 85        unsupported session, suppress bit set
 8.108  7E9#037F1012             7F 10 12     refused, and NOT suppressed
```

Three things this trace shows that a functional check cannot. Suppression is the **absence
of a frame**, not a shortened or empty one — nothing is transmitted at all. The multi-frame
response is a genuine ISO-TP segmented transfer with the tester supplying flow control
(`30 00 00`), not a long single frame. And `7F 19 13` at 5.406 s proves the masking: the
service could only reach its length rule by having been dispatched as report type `0x02`.

### 5.2 Scenario — pending, confirmed, cleared

From the scripted re-run against `ice_scenario.yaml`. Timestamps are seconds since the
simulator became ready.

**Before the first event — no trouble codes (t = 35 s)**

```
  35.00  7E0#0103000000000000    03            request stored DTCs
  35.00  7E8#0243000000000000    43 00         none
  35.00  7E1#031902FF00000000    19 02 FF
  35.00  7E9#0359028C            59 02 8C      header only: nothing matches the mask
```

**PENDING — after `raise_pending` at t = 40 s (observed t = 45 s)**

```
  45.00  7E1#031902FF00000000    19 02 FF
  45.00  7E9#0759028C01280104    59 02 8C | 01 28 01 | 04
                                 P0128, frozen third byte 0x01, status 0x04 = pending

  45.00  7E0#0103000000000000    03
  45.00  7E8#0243000000000000    43 00         service 03 reports confirmed codes only,
                                               so a pending code is correctly absent
```

**CONFIRMED — after `raise_confirmed` and `request_indicator` at t = 75 s (observed t = 78 s)**

```
  78.00  7E0#0103000000000000    03
  78.00  7E8#0443010128000000    43 01 01 28   one code, P0128

  78.00  7E1#031902FF00000000    19 02 FF
  78.00  7E9#0759028C0128018C    59 02 8C | 01 28 01 | 8C
                                 status 0x8C = pending + confirmed + indicator requested
```

**CLEARED — OBD Mode 04, and the UDS view agrees (t = 82 s)**

```
  82.00  7E0#0104000000000000    04            clear DTCs
  82.00  7E8#0144000000000000    44            acknowledged

  82.00  7E0#0103000000000000    03
  82.00  7E8#0243000000000000    43 00         cleared

  82.00  7E1#031902FF00000000    19 02 FF
  82.00  7E9#0359028C            59 02 8C      cleared in the UDS view too: one store
```

**NOT REPLAYED — 43 seconds later, with the periodic tick running throughout (t = 125 s)**

```
 125.00  7E0#0103000000000000    03
 125.00  7E8#0243000000000000    43 00         the consumed event did not fire again
 125.00  7E1#031902FF00000000    19 02 FF
 125.00  7E9#0359028C            59 02 8C

 125.00  7E0#02010D0000000000    01 0D
 125.00  7E8#03410D0000000000    41 0D 00      stationary: the t=120 timeline point
 125.00  7E0#02010C0000000000    01 0C
 125.00  7E8#04410C0C80000000    41 0C 0C 80   3200/4 = 800 rpm
 125.00  7E0#0201050000000000    01 05
 125.00  7E8#0341058400000000    41 05 84      132-40 = 92 C, the ramp held
```

### 5.3 Stepwise timeline, on the wire (t = 25 s)

```
  25.00  7E0#02010D0000000000    01 0D
  25.00  7E8#03410D1E00000000    41 0D 1E      0x1E = 30 km/h, the t=20 point HELD
                                               interpolation would give 42.5 -> 0x2A
  25.00  7E0#02010C0000000000    01 0C
  25.00  7E8#04410C1900000000    41 0C 19 00   0x1900 = 6400, /4 = 1600 rpm
  25.00  7E0#0201050000000000    01 05
  25.00  7E8#0341055A00000000    41 05 5A      0x5A = 90, -40 = 50 C, the ramp sloping
  25.00  7E0#02010D0000000000    01 0D         asked again, same instant
  25.00  7E8#03410D1E00000000    41 0D 1E      identical: a read observes, never advances
```

## 6. Automated verification

All at `08698d2`, re-run while writing this report.

### 6.1 Unit, characterization and coverage

```
837 passed, 2 xfailed in 5.81s
TOTAL   1968 statements   51 missed   97%
```

| Module | Statements | Missed | Coverage |
|---|---|---|---|
| `scenario/__init__.py` | 9 | 0 | **100%** |
| `scenario/events.py` | 18 | 0 | **100%** |
| `scenario/generators.py` | 81 | 0 | **100%** |
| `scenario/runner.py` | 73 | 0 | **100%** |
| `scenario/sync.py` | 15 | 0 | **100%** |
| `protocols/uds/protocol.py` | 107 | 0 | **100%** |
| Whole package | 1968 | 51 | **97%** |

### 6.2 Expected failures

Two, both `xfail(strict=True)`, both **evidence-blocked and unrelated to Phase 7**:

| Test | DEV | Why it remains |
|---|---|---|
| `test_mode09_pid0a_ecu_name_corrected_framing` | **DEV-03** | Mode 09 PID 0A byte layout needs J1979-DA text or two independent captures |
| `test_mode07_pending_dtcs_is_answered` | **DEV-11 Mode 07** | `47` + count framing has no public worked example |

**No unexpected XPASS.** `xfail_strict = true` is set in `pyproject.toml`, so a deviation
that quietly started passing would fail the run.

### 6.3 Integration

```
scripts/run_integration_tests.sh
53 passed in 28.92s
```

Up from 33 before Phase 7. The 20 added cover 0x3E on the wire, suppression as silence,
negative responses surviving suppression, the 0x19 transition, scenario values changing
between reads, a timed code arriving with no request in flight, a clear staying cleared
with the tick running, and clean SIGINT/SIGTERM shutdown with the tick live.

### 6.4 Static analysis

```
.venv/bin/ruff check src tests scripts   ->  All checks passed!
.venv/bin/mypy src                       ->  Success: no issues found in 45 source files
```

### 6.5 Regression and the unchanged default profile

The full characterization suite passes unchanged, and every deliberate wire change
transitioned a pin in the commit that made it.

Beyond that, a **differential comparison** was run between `01eeca9` (before Phase 7) and
the end of the phase: every response the shipped profile gives, over **56,064
request/address combinations** — all three addresses, every first byte, lengths one to
five, eighteen second-byte values. **172 differ, and every one is an intended change.** The
informative part is what did not: no OBD response moved, nothing on the broadcast address
`0x7DF` changed, `10 80` / `11 86` / `19 81` kept their `0x12`, and `14 FF FF FF` is
untouched. Recorded in
[decisions/0006](../decisions/0006-phase-7-scenario-and-testerpresent.md).

### 6.6 Continuous integration

| | |
|---|---|
| Run | **35796655595** |
| Commit | `08698d2` |
| Conclusion | **success** |
| Jobs | vcan probe, Python 3.12, Python 3.13, lint and type check — all success |
| Per-version result | **837 passed, 53 skipped, 2 xfailed**, coverage 97%, on both 3.12 and 3.13 |

**Skipped in CI, with the reason:** all **53** integration tests, because the
GitHub-hosted runner's kernel `6.17.0-1022-azure` is built without `CONFIG_CAN_ISOTP`, so
no ISO-TP socket can be created. The CI job reports this as an explicit skip reason rather
than a pass. Those 53 tests **do** run and pass locally in a private namespace (§6.3).

The earlier run on `61e7f56`, the commit carrying every functional change, was also
`success`; the commits between it and `08698d2` are documentation only.

## 7. Manual runtime verification versus automated event idempotence

These two kinds of evidence are often conflated. They are not interchangeable, and Phase 7
needed both.

### 7.1 What the manual run can establish

A wall-clock run confirms that an event fires **around** its configured time, that a
clear works, and that the fault is absent much later. The reviewer's 82 s → 125 s span,
with the periodic tick running throughout, is strong evidence that nothing replayed the
event in 43 seconds of real operation.

### 7.2 What it cannot

It cannot establish **exact-timestamp idempotence**, and no amount of manual testing can.
The property is:

> `apply(t)` called twice with the *same* `t` must apply a timed event at most once — and
> must still not apply it after a diagnostic clear has undone its effect.

Two requests on a real bus never share a timestamp. A monotonic clock advances between
them, so the adversarial case simply cannot be produced by hand. Nor can the interleaving
of the periodic tick and a request landing on one instant, or a clock going backwards.

### 7.3 What covers it

A `SimulatedClock`, which moves only when a test moves it, in `tests/unit/test_scenario_runner.py`
and `tests/unit/test_scenario_wiring.py`:

| Property | Test |
|---|---|
| Same `t` twice applies an event once | `test_applying_the_same_time_twice_does_not_apply_an_event_twice` |
| Fifty applications at one instant | `test_applying_the_same_time_many_times_applies_each_event_once` |
| Not replayed after a diagnostic clear | `test_an_event_is_not_replayed_after_a_diagnostic_clear` |
| A clock that does not advance produces no second application | `test_a_clock_that_does_not_advance_produces_no_second_application` |
| Request then tick, one instant | `test_a_request_side_apply_then_a_tick_at_the_same_time_applies_an_event_once` |
| Tick then request, one instant | `test_a_tick_then_a_request_side_apply_at_the_same_time_applies_an_event_once` |
| Both interleaved on one event loop | `test_interleaving_a_tick_and_a_request_at_the_same_time_applies_an_event_once` |
| Backward time refused, not reinterpreted | `test_a_backward_time_is_refused_rather_than_reinterpreted` |
| Backward time does not release a consumed event | `test_a_backward_time_does_not_release_a_consumed_event` |
| Two requests at one instant (through the dispatcher) | `test_an_event_is_not_replayed_by_two_requests_at_the_same_instant` |
| A request never moves the clock | `test_a_request_does_not_move_the_clock` |
| `apply` is synchronous, so the tick and a request never interleave mid-call | `test_apply_is_synchronous`, `test_no_await_can_be_hidden_inside_apply` |

**The division of labour.** The automated tests prove the property holds at instants that
cannot be reached by hand. The manual run proves the same code behaves correctly when
driven by a real clock, through a real kernel socket, for two minutes. Neither substitutes
for the other, and this report does not present either as if it did.

## 8. Known deviations and deferred behavior

### 8.1 Closed by Phase 7

| DEV | What closed |
|---|---|
| **DEV-23** | `0x3E` TesterPresent implemented, stateless. `3E 00` → `7E 00`; unsupported sub-function → `7F 3E 12`; wrong length → `7F 3E 13` |
| **DEV-07** | `suppressPosRspMsgIndicationBit` honoured once at the dispatch layer for every service explicitly declared to have a sub-function |
| **DEV-24** | The `0x19` consequence of that rule, in its own commit. `19 82 FF` → silence; `19 82` → `7F 19 13`. A deliberate correction to behavior Phase 6 established |

### 8.2 Corrected metadata, no behavior change

**DEV-09** and **DEV-10** had their fix-phase column corrected from 7 to 5, the phase the
fix actually landed in.

### 8.3 Still open

| DEV | State | What would unblock it |
|---|---|---|
| **DEV-03** | Open, strict xfail | J1979-DA text, or two independent captures of the Mode 09 PID 0A layout |
| **DEV-11 Mode 07** | Deferred, strict xfail | The J1979 text, or a published capture of a Mode 07 CAN response |
| **DEV-15** | Open | J1979 text on malformed-request handling |
| **DEV-12**, **DEV-16** (remaining halves), **DEV-17** | Open | Assigned to later phases |

DEV-03, DEV-11 Mode 07 and DEV-15 are **evidence-blocked, not effort-blocked**, and none
was pulled into Phase 7. A physical bench does not unblock them either: a capture of this
simulator is evidence about this simulator, not about what a standard requires.

### 8.4 Deferred by design

S3, TesterPresent timing, session state, Concurrent TesterPresent and configurable P2/P2*
(Phase 11); fault injection of every kind (Phase 10); multi-ECU fan-out and 29-bit
addressing (Phase 9); CAN FD (Phase 13). Scenario conditions, branching, triggers and
randomness are in **no** phase: the scenario schema forbids them with `extra="forbid"`,
and a test asserts each of the four is refused at load.

## 9. Limitations

Stated plainly, because a report that omits them overstates what was achieved.

1. **This was `vcan`, not physical CAN.** Every result used Linux virtual CAN with the
   in-tree kernel ISO-TP module. No physical CAN controller, transceiver, wiring or bus
   termination was involved, and no bus error, arbitration loss or bus-off condition can
   occur on `vcan`. Physical CAN is **Phase 8**, reviewed in
   [decisions/0007](../decisions/0007-phase-8-hardware-validation.md).
2. **No ELM327, and no OBD dongle of any kind.** The tester side was ISO-TP sockets. No
   claim is made that any commercial scan tool interoperates with this simulator. ELM327
   USB and Bluetooth acceptance is Phase 8.
3. **Nothing is `hardware validated`.** All 61 rows in
   [conformance.md](../conformance.md) read `no`, and nothing in this report changes that.
   That column requires physical CAN with named hardware and firmware.
4. **Nothing is `standards validated`, and no SAE or ISO conformance is claimed.**
   ISO 14229-1:2026, SAE J1979, J1979-DA and J2012 are licensed and have not been read by
   this project. Phase 7's UDS behavior rests on **corroborating public material** — the
   AUTOSAR Dcm specification (R25-11), and independently on what `udsoncan` builds and
   `scapy` parses. That is interoperability evidence and documentary evidence. It is not
   specification text, and agreement with either does not make a behavior conformant.
5. **The suppress-bit rule has no interoperability evidence at all.** Both client
   libraries set the bit and expect nothing back, so neither has anything to parse. It
   rests on AUTOSAR `[SWS_Dcm_00200]`, `[SWS_Dcm_00201]`, `[SWS_Dcm_00204]` and
   `[ECUC_Dcm_00737]` alone. Recorded as a difference from the other rows, not glossed.
6. **Float determinism on `sine` is a recorded risk, not a tested property.** `math.sin`
   is libm and its last bit could differ between platforms. Every byte-exact assertion
   here uses `constant`, `timeline`, `stepped` or `ramp`; `sine` drives engine load, which
   nothing asserts a byte of.
7. **Single-ECU only.** Functional fan-out to several ECUs is rejected by construction
   until Phase 9.
8. **The reviewer's original script was not retained**, so this report cross-checks their
   reported values against an independently written re-run rather than re-executing their
   exact procedure. See the provenance note.
9. **Timing tolerance.** In the scripted re-run, values on a continuous ramp are checked
   with a stated tolerance, because arriving at a wall-clock checkpoint to the millisecond
   is not possible. Values held by a timeline segment, and all DTC state, are checked
   exactly. The tolerance used is recorded in the script beside each check.

## 10. Acceptance summary

| Area | Result |
|---|---|
| Manual UDS verification | **10 / 10 passed**, as reported by the reviewer |
| Manual scenario acceptance | **21 / 21 passed**, as reported by the reviewer |
| Scripted re-run of the scenario | **23 / 23 passed**, clean exit; no disagreement with the reviewer at any checkpoint |
| Unit and characterization | **837 passed**, 2 expected failures |
| Integration (`vcan`, namespaced) | **53 passed** |
| Coverage | **97%** overall; **100%** on all five scenario modules and on the UDS protocol |
| Unexpected XPASS | **none** |
| `ruff` | clean |
| `mypy` | clean, 45 source files |
| Differential comparison vs `01eeca9` | 56,064 combinations; **172 differ, all intended** |
| CI | run **35796655595** on `08698d2`, **success**; 53 integration tests skipped with a recorded reason |
| Deviations closed | DEV-23, DEV-07, DEV-24 |
| `hardware validated` | **no** — unchanged, Phase 8 |
| `standards validated` | **no** — unchanged, deliberate |

**Phase 7 is accepted.** Every acceptance criterion in
[decisions/0006](../decisions/0006-phase-7-scenario-and-testerpresent.md) §11 maps to an
automated test, a manual check or a named command, and none is marked complete on the
strength of the implementation existing.

Phase 8 has been **reviewed but not started**
([decisions/0007](../decisions/0007-phase-8-hardware-validation.md)), and six questions
are open for the user — the first being whether any hardware is available, which decides
whether Phase 8 can close the V1.0 gate or only prepare for it.

---

*Revision under test `08698d299a1d9c1a41743c2ccac84cf6bca15d69`. Manual acceptance
2026-09-22 by the reviewer; automated results and traces re-produced the same day while
writing this report.*
