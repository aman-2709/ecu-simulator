# 0008 — Phase 8 question resolutions and the 8a/8b split

Status: **decided 2026-09-23 by the project owner**, before any Phase 8 code. Resolves the
six open questions in [0007 §10.2](0007-phase-8-hardware-validation.md) and one conflict
that 0007 did not ask about.

**No production code, script, test or profile has been changed by this record, and no
status column has moved.** `hardware validated` stays `no` on all 61 rows of
[conformance.md](../conformance.md).

0007 remains the review it was. Where this record changes a conclusion, 0007 carries a
dated amendment pointing here rather than being silently rewritten — the same treatment
Phase 6's record received when Phase 7 changed two of its bytes.

## 1. Applicable documentation reviewed for this record

Per [modernization-plan.md section 11.6](../modernization-plan.md). This record re-read
material that 0007 cited second-hand and added two items 0007 did not use.

| Item | Version/revision | Source type | Full text available? | Relevant to this record |
|---|---|---|---|---|
| ELM327 datasheet | **ELM327DSJ**, 94 pages. Re-downloaded 2026-09-23 from the manufacturer's canonical URL, HTTP 200, **463,576 bytes** — byte-identical in size to the 2026-09-22 download recorded in [0007 §2.1](0007-phase-8-hardware-validation.md), and the running footer reads `ELM327DSJ` | Manufacturer documentation, public | **Yes** | The serial contract (§4), the error vocabulary (§4.3), and the frequency-matching correction (§6) |
| Linux SocketCAN documentation | `Documentation/networking/can.rst` at `docs.kernel.org/networking/can.html`, read 2026-09-23 | Official Linux kernel documentation (priority 3) | **Yes** | Local loopback semantics (§7), termination, `ip link … type can` parameters, statistics |
| pyserial | **3.5**, current release on PyPI as at 2026-09-23 | Official project distribution | **Yes** | Q2. Confirmed experimentally, §4.1 |
| iproute2 | **5.15.0** on this development host | Tool, installed | n/a | Confirms every gap in 0007 §5.1 is reachable with what is already installed: `ip link help can` lists `restart-ms`, `termination`, `listen-only`, `sample-point` and `presume-ack` |
| can-utils | **2020.11.0** on this development host | Open-source tool | **Yes** | `candump` is present; no installation is needed for criterion 11 |

No **specification** was newly read. ISO 15765-4 and ISO 11898-1/-2 remain unavailable and
nothing here is `standards validated`.

## 2. The rulings

### Q1 — Hardware availability

**Ruled: proceed on the assumption that a complete physical testbench is not yet
available.** There is no physical CAN interface connected to the development machine —
independently confirmed for this record: `ip -brief link show type can` returns nothing,
while `vcan0` is present and `CAN_ISOTP` sockets open.

Two qualifications the owner attached, recorded so a later reader does not over-read the
ruling:

- **This is not a statement that no hardware is owned.** Unplugged equipment may be
  inventoried separately. The absence recorded here is of a *connected, working bench*.
- **No equipment is to be purchased**, and no hardware validation is to be claimed.

Consequence: Phase 8 cannot close the V1.0 gate now. That is the whole reason for the
split in §3.

### Q2 — Serial driving: option A

**Ruled: option A.** `pyserial` as an optional `[hardware]` dependency, with a
configurable serial port and baud rate, read-until-prompt with a timeout, and a parser
tested independently against recorded ELM327 exchanges. **The default installation is
unchanged.**

The four requirements in that sentence are each sourced from ELM327DSJ, not chosen for
convenience:

| Requirement | Why the device makes it necessary |
|---|---|
| Configurable **baud** | "this will be either 9600 baud (if pin 6 = 0V at power up), or 38400 baud (if PP 0C has not been changed)". A fixed baud would fail on half the possible devices |
| Configurable **port** | The device is a virtual serial port whose name is assigned by the host — `/dev/ttyUSB*` for USB, `/dev/rfcomm*` for Bluetooth. **The same code path serves both transports**, which is why this ruling does not affect the Bluetooth decision in §3 |
| **Read-until-prompt**, not read-line | "software should always wait for either the prompt character ('>' or hex 3E) … before beginning to send the next command". Responses are terminated by a single carriage return with an *optional* linefeed, and a multi-line response contains several of them, so a line-based read cannot know when a response has ended |
| An independently tested **parser** | §4 below |

8N1 framing is likewise from the datasheet: "8 data bits, no parity bits, and 1 stop bit".

### Q3 — Bitrate stays outside the simulator process

**Ruled: no runtime `--bitrate` option.** Bitrate configuration remains privileged work
done by `scripts/setup_can.sh` before the simulator starts. 0007 §5.3's reading is
confirmed: `--bitrate` in the Phase 8 sentence means guidance about the script's existing
argument and the documentation around it.

The separation is already load-bearing in the code — `cli.py:39` and
`transport/socketcan/interface.py:29-30` both direct the operator to the setup scripts —
and the simulator runs unprivileged after Phase 2 by design.

The read-only startup warning floated during the review (the simulator reading the
interface bitrate and warning when it is not an OBD value) is **not adopted**. It would be
a production code change inside the phase whose defining rule is that it changes nothing,
and gap 5 in §2 Q4 delivers the same diagnostic for a fraction of the cost.

### Q4 — `setup_can.sh` gaps

**Ruled**, itemised against 0007 §5.1:

| Gap | Ruling |
|---|---|
| 1 `restart-ms` | **In, implemented together with gap 2.** Not separable: automatic restart cycles through a persistent fault and makes a broken bench look healthy. What makes it safe is the `re-started` counter, which only gap 2 surfaces |
| 2 `-statistics` | **In.** Output only. Also distinguishes "the link is up" from "the link is up on a working bus", which the script cannot do today: `ip link set up` succeeds on an adapter attached to nothing |
| 3 termination | **In, optional and guarded.** Applied only when the controller reports support. Guarding is mandatory, not stylistic: `ip link set dev canX type can termination 120` fails on a controller without switchable termination, and under `set -euo pipefail` that aborts the script |
| 4 `listen-only` | **Out of the setup script. Documented as a troubleshooting procedure.** It is a mode a node cannot transmit from, so offering it at setup time invites a bench configured into exactly the silent-failure state this phase exists to diagnose |
| 5 bitrate warning | **In, as a warning that never rejects.** Legitimate custom configurations must still work: `setup_can.sh` is a generic CAN setup script and the kernel accepts `BITRATE := { 1..1000000 }` |
| 6 `sample-point` | **Deferred.** With `CONFIG_CAN_CALC_BITTIMING` the kernel calculates CiA-recommended timing. It is added when a bench produces bus errors that bit timing explains, with that evidence attached |

### Q5 — Phase 8 splits into 8a and 8b

**Ruled: split.** See §3.

### Q6 — Real-vehicle testing

**Ruled: the simulator must not transmit on a live vehicle's CAN bus.** Real-vehicle
testing is outside Phase 8 and outside V1.0.

**Passive, listen-only vehicle captures may be considered separately in the future**, as
evidence for unresolved diagnostic behavior, and each would carry its own safety review.
They are not scheduled and not in any phase.

Why this is recorded rather than left as an omission: it has a consequence that is easy to
lose. [known-deviations.md](../known-deviations.md) says DEV-11 Mode 07 is unblocked by
"the J1979 text, **or one or more independent real captures of a mode 07 CAN response**".
A passive capture of a *different* vehicle's ECU would be such a capture — so this ruling
also closes, for now, the one route to DEV-11 Mode 07 that does not run through a
paywalled document. Recording the ruling means that route is deliberately parked, not
forgotten.

It would remain interoperability evidence under
[section 11.2](../modernization-plan.md), ranking below the applicable specification.
**It would never be `standards validated`**, and it is not evidence about this simulator
at all — it is evidence about what some other ECU does.

Note also that 0007 §1.3's sentence "a physical bench does **not** unblock them" is
correct for the bench this phase builds — a capture of this simulator answering a tester
is a capture of this simulator — and says nothing about captures of third-party ECUs.

## 3. The Bluetooth conflict, and the 8a/8b split

### 3.1 The conflict 0007 did not ask about

[modernization-plan.md section 5](../modernization-plan.md) required the V1.0 physical
bench to record "exact Bluetooth ELM327 adapter and its `ATI` output" as a listed item,
while [0007 §6.4](0007-phase-8-hardware-validation.md) treats a missing Bluetooth device
as a `not verified` row. Read together, **the absence of a Bluetooth dongle blocked the
V1.0 tag** however honestly 0007 reported it. Neither document acknowledged the other.

### 3.2 Ruling

**Bluetooth ELM327 testing is optional for the initial V1.0 release.**

The V1.0 physical bench requires:

1. a SocketCAN adapter;
2. a working physical CAN bus **with a second node**;
3. a real **USB** ELM327 tester, with its actual adapter identity and `AT I` output recorded.

**Bluetooth ELM327 compatibility is a separately reported, optional acceptance test.** An
untested Bluetooth connection is marked **not verified**, explicitly and with its reason.
It is never inferred from a USB result — the two share a code path (§2 Q2) but not a
transport, and a shared code path is not evidence.

Both documents are amended to say this. Section 5 of the plan and §6.4 and criterion 9 of
0007 now agree.

### 3.3 The split

| | Phase 8a | Phase 8b |
|---|---|---|
| Content | Hardware-test documentation, preparation scripts, hardware-specific tests, test infrastructure | Real physical-CAN and ELM327 acceptance |
| Needs a bench? | **No** | **Yes** |
| Can be completed and independently verified now? | **Yes** | No |
| 0007 acceptance criteria | 1–7, 14, 15 | 8–13 |
| Status until done | — | **Open** |

Two standing rules attach to the split:

- **V1.0 is not tagged without the Phase 8b hardware evidence.** Completing 8a does not
  move the gate, and 8a's own completion report must say so.
- **Later architectural work may proceed** — Phases 9 to 11 need no hardware — **provided
  it never misrepresents the hardware-validation status.** No row gains
  `hardware validated: yes` outside 8b.

## 4. Test-infrastructure corrections ruled in scope for 8a

Three corrections, all identified while reading the existing suite for this review, all to
be done **before** a bench is prepared rather than discovered during bench time.

### 4.1 DTC-mutating integration tests must be independently runnable

`tests/integration/test_ecu_dispatch.py::test_clearing_dtcs_is_not_mistaken_for_a_suppressed_request`
sends `14 FF FF FF`, which clears the shared DTC store, **without requesting the
`mutating` fixture** that exists precisely to restart the simulator afterwards. On vcan
this is masked by the module-scoped simulator and by the test's position near the end of
the file.

It stops being masked the moment tests are run individually or out of order — which is
exactly what [0007 §7.1 rule 4](0007-phase-8-hardware-validation.md) requires of a bench
run, because a bench run is partly manual and a partial result must be recordable.

**Measured, not inferred.** Run at `9565e13` on 2026-09-23, with the two tests named in
the order a bench run could easily produce:

```
$ .venv/bin/python -m pytest -p no:cacheprovider \
    "tests/integration/test_ecu_dispatch.py::test_clearing_dtcs_is_not_mistaken_for_a_suppressed_request" \
    "tests/integration/test_ecu_dispatch.py::test_reading_dtcs_can_be_asked_for_silently" -v

>       assert uds_physical.recv() == bytes.fromhex("59028c9477010c0001010c")
E       AssertionError: assert b'Y\x02\x8c' == b'Y\x02\x8c\x...0\x01\x01\x0c'
E         - (b'Y\x02\x8c\x94w\x01\x0c\x00\x01\x01\x0c')
E         + b'Y\x02\x8c'
========================= 1 failed, 1 passed in 0.60s ==========================
```

The store was cleared and never restored, so `19 02 FF` answers the header alone. The
natural file order is the reverse — the reading test is at line 150, the clearing test at
line 169 — which is precisely why a whole-file run has never shown this.

### 4.2 A silence assertion must be followed by a known-good request

Twelve of the 53 integration tests prove *silence* with a bare
`pytest.raises(TimeoutError)` against a 1.0 s tester timeout. (Seventeen parameterisations
assert a timeout in all; three already follow it with a positive exchange and need no
change, and the two `test_shutdown_is_clean_while_the_tick_is_running` parameterisations
are a different concern, below.)

On vcan, silence can only mean the simulator chose not to answer. **On a physical bus it
also matches a dead adapter, a bitrate mismatch, an unterminated bus, a bus-off interface
or an unpowered dongle** — so those tests pass while the bench is broken, which is the
worst failure a test can have.

The rule adopted: **a test asserting silence sends a known-good request on the same
channel afterwards and asserts its answer.** Several tests already do this — 
`test_the_channel_still_works_after_a_suppressed_response` is the pattern. It costs
nothing on vcan and is load-bearing on hardware.

Ordering matters and is part of the rule: `test_reading_dtcs_can_be_asked_for_silently`
proves the channel alive and *then* asserts silence, so a bus that died in between still
passes. The probe goes **after** the silence, not before.

The twelve, counted by parameterisation rather than by function, for the avoidance of a
hand-wave:

| File | Tests |
|---|---|
| `test_ecu_dispatch.py` | `test_uds_service_on_the_functional_address_reaches_no_protocol` (3 parameterisations), `test_unknown_service_on_the_functional_address_gets_no_response`, `test_a_suppressed_positive_response_is_not_transmitted`, `test_a_suppressed_service_that_is_not_tester_present_is_silent_too` (2), `test_reading_dtcs_can_be_asked_for_silently`, `test_tester_present_on_the_obd_broadcast_address_reaches_no_protocol` |
| `test_obd_isotp.py` | `test_supported_pid_chain_terminates_on_the_wire`, `test_unsupported_pid_gets_no_response` |
| `test_scenario_isotp.py` | `test_tester_present_still_works_while_a_scenario_runs` |

`test_shutdown_is_clean_while_the_tick_is_running` is **not** in this list. Its
hardware-sensitive constant is a different one — a 1.0 s bound on process exit, where
ISO-TP socket teardown on a real driver is the plausible cause of a widening. It is left
alone and widened only against a measurement, never pre-emptively.

### 4.3 Hardware-only tests are excluded from ordinary runs and target an isolated bench

**Exclusion.** The `hardware` marker has been declared in `pyproject.toml` since Phase 1,
but **a marker does not prevent collection** — pytest collects first and deselects after,
so importing `pyserial` in a default run would fail on a machine without the extra. And
`testpaths = ["tests"]` means a bare `pytest` will collect `tests/hardware/` the moment
the directory exists. Acceptance criterion 5 therefore needs real machinery, not the
marker alone, and 8a supplies it.

**Isolation.** Physical testing targets a dedicated bench, never an arbitrary active CAN
interface. The suite refuses to run unless explicitly told which interface and which
serial device to use — no defaults — and refuses a `vcan*` name outright, since a hardware
suite on a virtual interface proves nothing.

The strongest guard available is one the project already owns:
`tests/integration/conftest.py` has `_foreign_responder_present`, which asks `01 00`
functionally and fails if anything answers. On a physical bench nothing should answer
before the simulator starts. **On a vehicle bus, a real ECU would answer** — so reusing
that probe as a pre-flight refusal turns an existing guard into the mechanism that
enforces the Q6 ruling.

## 5. Consequences for the roadmap

Reconciled in [modernization-plan.md](../modernization-plan.md) in the same commit as this
record:

| Location | Change |
|---|---|
| §3 release mapping | V1.0 phase range `0 to 8` → `0 to 8b`; its acceptance content now reads "ELM327 USB acceptance (Bluetooth optional, separately reported)" |
| §4 | The Phase 8 entry becomes **Phase 8a** and **Phase 8b**, each with its own scope and Definition of Done |
| §5 V1.0 acceptance gate | The Bluetooth adapter moves out of the required bench list into an optional, separately reported line |
| §7.2 | A `pyserial` row, marked decided and not yet installed |
| §9 commit sequence | Phase 8's three commits expand into the 8a and 8b sequences |

## 6. A correction to 0007 §6.3, found while re-reading the datasheet

0007 §6.3 states that a bitrate mismatch "does not produce an error — it produces
silence". Read in full, ELM327DSJ "CAN Input Frequency Matching" (page 62) is narrower
than that, in two ways that matter on a bench:

- **The frequency check applies only during a protocol search.** "This logic is only used
  while searching for a valid protocol. Once a particular protocol is considered to be
  active, no further frequency checks are made (as it is time consuming)." A bench that
  selects protocol 6 with `AT SP 6` rather than searching is not in the path the review
  describes.
- **A quiet bus passes the check.** "a send is allowed if the input signal frequency
  matches the CAN setting (250 or 500 kbps), **or if there appears to be no signal**."

And a bitrate mismatch can in fact surface as an error. ELM327DSJ "Error Messages and
Alerts" (page 87) gives `CAN ERROR` as covering the case where "you have set the system to
an incorrect protocol, or to a baud rate that does not match the actual data rate";
separately, `NO DATA` is printed when the `AT ST` timer expires with no response.

**The practical conclusion is unchanged and the ruling on gap 5 stands** — a bitrate
mismatch is still cheap to prevent and expensive to diagnose. What changes is the
troubleshooting text, which must list **three** symptoms rather than one: silence,
`NO DATA`, and `CAN ERROR`. A troubleshooting section that describes only silence would
send a bench operator down the wrong path two times in three.

0007 §6.3 carries a dated amendment pointing here.

## 7. A property of physical-bus testing that decides how much a same-host test proves

The kernel documentation states that loopback of sent frames "has to be performed right
after a **successful** transmission", that it is enabled by default, and that a driver
signalling `IFF_ECHO` handles it itself, with the PF_CAN core as a fallback. The kernel's
own `ip -details -statistics` example shows `<NOARP,UP,LOWER_UP,ECHO>` on `can0`.

Stated precisely, and no further: **for a driver that sets `ECHO`, a frame a same-host
tester socket receives has been successfully transmitted, which on a real bus means
another node acknowledged it.** For a driver without `ECHO` the core echoes as a fallback
and the documentation does not state its timing, so the inference does not follow.

Two bench steps follow, and they belong in `docs/hardware-testbench.md` rather than in an
assumption:

1. Confirm `ECHO` appears in the interface flags and record it.
2. Record `ip -details -statistics link show <iface>` **before and after** every run. TX
   `errors` or `dropped` climbing, or the controller leaving `ERROR-ACTIVE`, means frames
   did not cross the wire whatever any test asserted.

This also bounds criterion 11 honestly: `candump` on the simulator's own host sees
loopback frames, so it is an independent *decoder* of the bytes but not independent proof
they crossed the wire. The error counters are. The ELM327's own answer is. A second host
would be.

## 8. What this record does not decide

- **Which adapter and which dongle.** No part number, no price, no "a typical adapter
  such as". The bench does not exist; inventing its contents is the failure mode
  [section 11](../modernization-plan.md) exists to prevent.
- **Whether unplugged equipment exists.** Named as a separate inventory exercise by the
  owner, not folded into this record.
- **The `sample-point` question** (gap 6), deferred with its unblocking condition stated.
- **Whether passive vehicle capture ever happens.** Parked with its own safety review
  attached as a precondition.
- **Anything about Phase 8b's results.** It has not run.

## 9. Outcome

Six questions resolved, one conflict removed, three test-infrastructure corrections
scoped, and Phase 8 split so that the half that needs no bench can be finished and
verified while the half that does stays honestly open.

The implementation plan for the first half is
[plans/phase-8a-implementation.md](../plans/phase-8a-implementation.md). **Phase 8b is not
started, no equipment is purchased, and no production protocol behavior changes.**
