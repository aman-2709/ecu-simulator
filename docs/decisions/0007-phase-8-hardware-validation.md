# 0007 — Phase 8 documentation, hardware-readiness and test-plan review

Status: proposed 2026-09-22, before any Phase 8 code. **Review only. No production change
has been made, and nothing in this project is `hardware validated`.**

**Amended 2026-09-23.** The six questions in §10.2 were answered by the project owner and
Phase 8 was split into 8a and 8b; the rulings live in
[0008](0008-phase-8-question-resolutions.md). This document is otherwise left as the
review it was on 2026-09-22 — it records what was established at that checkpoint, the same
treatment Phase 6's record received when Phase 7 changed two of its bytes. Three places
carry a dated amendment because a later reading changed the conclusion rather than merely
adding to it: **§6.3** (the frequency-matching mechanism is narrower than stated), **§6.4
and criterion 9** (Bluetooth is optional for V1.0), and **§10.2** (all six resolved).

Produced under the documentation and standards verification gate
([modernization-plan.md section 11](../modernization-plan.md)). It covers only what Phase 8
touches. [0006](0006-phase-7-scenario-and-testerpresent.md) is the shape it follows.

Phase 8 is unlike every phase before it. Its deliverable is not behavior but **evidence**:
it is the phase that can put the first `yes` in the `hardware validated` column of
[conformance.md](../conformance.md), where all 61 rows currently read `no`. That column is
defined in [section 6](../modernization-plan.md) as "exercised on physical CAN with named
hardware and firmware", and no amount of vcan work, review or reasoning can fill it. This
review therefore spends most of its length on what must be *recorded* from a bench that
does not exist yet, and deliberately does not predict the values.

## 1. Phase 8 scope, taken from the current plan

From [modernization-plan.md section 4](../modernization-plan.md), verbatim and complete:

> ### Phase 8 — Physical CAN and ELM327 validation (V1.0 gate)
>
> `setup_can.sh` bitrate handling, `--bitrate` guidance, `tests/hardware` opt-in suite,
> `docs/hardware-testbench.md` with exact hardware and firmware, troubleshooting. Risk M.

Section 3 places it in V1.0 and names its acceptance content:

> | V1.0 | ... Classical CAN on vcan0 and can0 ... unit + vcan tests, CI, **ELM327
> USB/Bluetooth acceptance** | 0 to 8 |

and adds the one release rule that touches this phase:

> Multi-ECU on physical addressing may fall out of the router in V1.0. If it does, its
> status is recorded accurately in the conformance table; **it never gates the V1.0 bench.**

**Phase 8 is the V1.0 gate.** Nothing after it is V1.0, so the release cannot be declared
until this phase produces its evidence.

### 1.1 What already exists, so the phase is not credited with it

| Item | State today |
|---|---|
| `scripts/setup_can.sh` | Exists since Phase 2. Takes `<interface> [bitrate]`, default 500000, validates the interface name and the bitrate, sets bitrate, brings the link up, prints `ip -details link show`. Section 5 reviews what it does not do |
| `pytest` marker `hardware` | **Already declared** in `pyproject.toml`: "needs physical CAN hardware and an ELM327 adapter; opt-in only, never run in CI". Reserved in an earlier phase and still unused |
| `scripts/probe_can_capabilities.py` | Read-only capability probe, used by CI to record why the vcan suite skips. Never loads modules or configures interfaces |
| ELM327 datasheet as a source | Already this project's most productive primary source: it settled DEV-02, DEV-04, four Mode 01 encodings, the Mode 03 CAN framing, the Mode 04 response byte, and the two multi-PID captures Phase 5.1 reproduces byte for byte |
| `tests/integration`, 53 tests | Runs against vcan, in a private namespace. The hardware suite is a sibling, not a replacement |

`tests/hardware/` and `docs/hardware-testbench.md` **do not exist**.

### 1.2 Scope, itemised

| Item | In Phase 8 | Note |
|---|---|---|
| `setup_can.sh` bitrate handling reviewed and extended | yes | section 5 |
| `--bitrate` guidance | yes | the plan's words; section 5.3 reads them as CLI-facing guidance, not a new simulator option |
| `tests/hardware` opt-in suite | yes | section 7 |
| `docs/hardware-testbench.md` with exact hardware and firmware | yes | section 4; the values come from the bench, not from this review |
| Troubleshooting documentation | yes | section 8 |
| ELM327 USB acceptance | yes | section 6 |
| ELM327 Bluetooth acceptance | yes, conditionally | section 6.4: same AT sequence over a different serial transport. If no Bluetooth device is available it is reported **not verified**, never assumed |
| First `hardware validated` entries in conformance | yes, and only for rows actually exercised | section 9 |

### 1.3 Deviations in scope

**None.** No DEV identifier names Phase 8 as its fix phase. This phase changes no wire
behavior at all, and that is a property worth stating up front: **if a physical-CAN run
produces different bytes from the vcan run, that is a finding to investigate and record,
not a licence to change the protocol layer.** Any change it does motivate gets its own
DEV identifier, its own evidence and its own commit, under the same rules as every phase
before it.

DEV-03, DEV-11 Mode 07 and DEV-15 remain open and evidence-blocked. A physical bench does
**not** unblock them: they need specification text or independent published captures, and
a capture of this simulator talking to a tester is a capture of this simulator's own
behavior, which is not evidence about what the standard requires.

### 1.4 Deferred beyond Phase 8

| Behavior | Where the plan puts it |
|---|---|
| Multi-ECU profile, functional fan-out, 29-bit addressing | Phase 9. **Explicitly does not gate the V1.0 bench** |
| Fault injection of every kind | Phase 10 |
| Session state, S3, TesterPresent timing, 0x22, 0x19/01 and /0A | Phase 11 |
| CAN FD, `fd`, `dbitrate`, `tx_dl`, MTU probing | Phase 13. `setup_can.sh` says so already |
| DoIP | Phase 14 |
| Real-vehicle testing | **Not in any phase.** See section 10 |

## 2. Applicable documentation

Per [section 11.6](../modernization-plan.md). Phase 8 touches no new protocol
specification — it re-exercises protocols already implemented — so the table is dominated
by operating-system and device documentation.

| Item | Version/revision | Source type | Full text available? | Relevant to this phase |
|---|---|---|---|---|
| ELM327 datasheet | **ELM327DSJ**, 94 pages | Manufacturer documentation, public | **Yes** | The tester side of every acceptance test: protocol numbers, the AT command set, the initiation and frequency-matching behavior |
| Linux SocketCAN documentation | `Documentation/networking/can.rst`, read at `docs.kernel.org/networking/can.html` on 2026-09-22 | Official Linux kernel documentation (source priority 3) | **Yes** | `ip link set … type can` parameters, termination control, bus statistics |
| ISO 15765-4 | not established; not researched beyond what the inventory already says | Licensed | **No** | Defines the OBD-on-CAN physical layer: the bitrates, the identifiers and the timing. The project implements the public conventions and labels them |
| ISO 11898-1 / ISO 11898-2 | not checked | Licensed | **No** | The CAN data link and physical layers: acknowledgement, error confinement, bus-off, termination values. **Named here to say what would be needed**, not reconstructed |
| can-utils | version to be recorded from the bench host | Open-source tool | **Yes** | `candump`, `isotpsend`, `isotprecv` for independent capture |
| SocketCAN adapter and driver | **to be recorded from the bench** | — | — | Section 4 |
| ELM327 device and firmware | **to be recorded from the bench** | — | — | Section 4 |

### 2.1 Revision check performed for this review

- **ELM327DSJ is current.** Downloaded on 2026-09-22 from the manufacturer's canonical
  URL (`elmelectronics.com/wp-content/uploads/2016/07/ELM327DS.pdf`, HTTP 200, 463,576
  bytes) and the running footer reads `ELM327DSJ` on every page. This is the same revision
  [section 7.1](../modernization-plan.md) already records, so **no inventory row changes.**
  The manufacturer's product pages additionally list an `ELM327L` low-voltage variant and
  an `ELM327 v1.3a` part, neither of which this project has used.
- **The kernel documentation was read, not recalled**, at the URL above.
- **ISO 14229-1, SAE J1979, J1979-DA and J2012 are untouched by this phase** and their
  rows are reused from the inventory without re-research, as section 11.1 requires.

## 3. Normative-text availability

| Item | Full normative text available | Behavior in this phase that depends on it |
|---|---|---|
| ISO 15765-4 | **No** | The bench bitrate (500 kbit/s) and the 11-bit identifiers. Already implemented and labelled; Phase 8 exercises them, it does not newly derive them |
| ISO 11898-1 / -2 | **No** | Why a lone transmitter cannot get its frames acknowledged, and what the termination value must be. Phase 8 must *design around* these; it does not implement them, the controller does |

**Nothing in Phase 8 will be marked `standards validated`, and passing every hardware test
will not change that.** [Section 11.8](../modernization-plan.md) is explicit: "Hardware
interoperability alone is never standards validation." A real ELM327 agreeing with this
simulator demonstrates that the two interoperate. It demonstrates nothing about ISO
conformance, because neither party's conformance has been checked against the text.

## 4. Required hardware, and what must be recorded

The plan asks for "exact hardware and firmware". **This review deliberately names no
specific adapter**, because a bench that does not exist has no exact anything, and
inventing plausible part numbers is precisely the failure mode
[section 11](../modernization-plan.md) exists to prevent. What follows is the shopping
list by capability, and the fields `docs/hardware-testbench.md` must carry once the bench
is real.

### 4.1 Minimum bench

| # | Item | Requirement | Why |
|---|---|---|---|
| 1 | Linux host | SocketCAN, `CONFIG_CAN_ISOTP`, Python 3.12+. The existing development host qualifies | Runs the simulator |
| 2 | SocketCAN CAN adapter | Classical CAN, in-tree driver, appears as `canX`. USB is simplest | Gives the simulator a physical bus |
| 3 | ELM327 adapter, USB | Genuine ELM327 strongly preferred; see 4.3 | The tester under acceptance |
| 4 | ELM327 adapter, Bluetooth | Optional; section 3 of the plan names USB/Bluetooth acceptance | The second transport |
| 5 | Wiring between them | CAN-H and CAN-L, plus a common ground | The bus |
| 6 | Bus termination | 120 Ω at each end of the pair | 5.2 |
| 7 | Power for the ELM327 dongle | Whatever the chosen device requires | 4.2 |
| 8 | can-utils on the host | `candump` at minimum | Independent capture, not the simulator's own log |

### 4.2 The two wiring facts this review can source, and the one it will not guess

- **CAN is on pins 6 and 14 of the OBD connector.** ELM327DSJ, "CAN Input Frequency
  Matching": "Most modern vehicles have a CAN network connected to pins 6 and 14 of the
  OBD connector."
- **`AT RV` reads the input voltage.** ELM327DSJ command table, "RV — Read the input
  Voltage". This is the cheapest possible bench check that a dongle is powered and alive
  before any protocol question is asked.
- **How the chosen dongle is powered is not guessed here.** Consumer OBD dongles are built
  to draw power from the vehicle's connector, and a bench has no vehicle. The supply
  arrangement, its voltage, and which pins carry it **must be taken from the specific
  device's own documentation and recorded**, not assumed from this or any other datasheet.
  The ELM327DSJ pin numbers that appear throughout that document are the **integrated
  circuit's** pins, not the OBD connector's, and confusing the two would produce confident
  and wrong wiring instructions.

### 4.3 Genuine versus clone ELM327, recorded as a risk before it bites

The ELM327 is the most cloned part in this domain. Clones commonly report `ELM327 v2.1` or
`v1.5` to `AT I` while implementing a subset, and some misreport entirely. This matters to
Phase 8 in a specific way: **a clone that fails an acceptance test may be evidence about
the clone, not about this simulator.**

The rule this phase adopts: `docs/hardware-testbench.md` records the device's `AT I`
identification string, `AT @1` device description and `AT DP`/`AT DPN` protocol report
verbatim, alongside how the device was obtained. A failure is investigated against the
datasheet's documented behavior. A test that only fails on one adapter is recorded as an
**interoperability finding naming that adapter**, and never silently generalised into a
defect of this simulator or quietly dropped.

### 4.4 Fields `docs/hardware-testbench.md` must carry

Per item, so the bench is reproducible by someone else: make and model; interface and
connection; driver or chipset with kernel module name; firmware or hardware revision as
the device reports it; `AT I` / `AT @1` output verbatim for the ELM327; `ip -details link
show` output for the CAN interface; kernel version and distribution; can-utils version;
the date tested and the simulator commit tested.

## 5. `setup_can.sh` review

Read at `c81d3e8`. It is sound for what it does — it validates the interface name and
bitrate, refuses a missing interface with a useful message, needs no changes to run
unprivileged afterwards — and the gaps below are all of the same kind: **things a bench
needs that a vcan interface never does.** Each is evidence-backed from the kernel
documentation read for this review.

### 5.1 Gaps, in the order they will bite

| # | Gap | Consequence on a bench | Kernel facility |
|---|---|---|---|
| 1 | **No `restart-ms`** | After a bus-off the interface stays down until someone notices. This is the single most likely bench failure, and it presents as "the simulator stopped responding" rather than as a bus fault | `restart-ms TIME-MS`, and `ip link set canX type can restart` to recover by hand |
| 2 | **No statistics after setup** | The script prints `ip -details link show`, which shows configuration but not the error counters | `ip -details -statistics link show canX` reports `bus-errors`, `error-warn`, `error-pass`, `bus-off` and `re-started` |
| 3 | **No termination control** | On a controller with switchable termination the bus may be unterminated with no warning | `ip link set dev canX type can termination 120`; available values appear in `ip -details link show` as `termination 120 [ 0, 120 ]` |
| 4 | **No `listen-only`** | No passive way to watch a bus without a node joining it | `listen-only { on \| off }` |
| 5 | **No bitrate sanity warning** | A bitrate the OBD conventions do not use is accepted silently, and the ELM327 will then refuse to transmit for a reason that looks like a dead adapter (6.3) | The script already parses the value; only a warning is needed |
| 6 | **No `sample-point`** | The default is usually right, but a long or marginal bench harness may need it, and there is currently no way to set it without bypassing the script | `bitrate BITRATE [ sample-point SAMPLE-POINT ]` |

`loopback`, `one-shot`, `triple-sampling`, `berr-reporting`, `presume-ack` and
`cc-len8-dlc` also exist. **None is proposed.** They change error semantics in ways this
project has no evidence it needs, and a bench that needs `presume-ack` to pass is a bench
that is not acknowledging frames — a problem to find, not to mask.

### 5.2 Termination and the second node: designed for, not asserted

Two properties of a physical CAN bus shape the bench, and this review is careful about how
far it can claim them:

- **A CAN bus needs termination across the differential pair.** The kernel documentation
  states it directly: "CAN bus requires a specific impedance across the differential pair,
  typically provided by two 120 Ohm resistors on the farthest nodes of the bus."
- **A transmitter needs another node to acknowledge its frames.** The precise mechanism —
  the acknowledgement slot, error counters, the transition to error-passive and bus-off —
  is ISO 11898-1, which this project has **not read**. What can be shown without it: the
  kernel exposes `presume-ack` as a control mode and reports `bus-off` and `error-pass`
  counters, both of which exist because a real bus can reach those states.

The consequence for Phase 8 is the same either way, so the design does not depend on the
unread text: **the bench is a two-node bus — the SocketCAN adapter and the ELM327 — with
120 Ω at each end**, and the first diagnostic for any failure is the error counters from
gap 2.

### 5.3 What "`--bitrate` guidance" is read as

The plan's phrase is ambiguous and this review resolves it, for the user to confirm.
**`--bitrate` is taken as guidance about `setup_can.sh`'s existing bitrate argument and
the documentation around it, not as a new `ecu-simulator --bitrate` option.** The
simulator does not configure the interface and must not start doing so: it runs
unprivileged, bitrate is a privileged link property, and `setup_can.sh` exists precisely
to keep that separation. The guidance belongs in the README, the testbench document and
the script's own usage text.

## 6. ELM327 acceptance test plan

The tester side. Every item below is sourced from ELM327DSJ; nothing is recalled.

### 6.1 The protocol to select

| Protocol | Meaning | Use |
|---|---|---|
| **6** | ISO 15765-4 CAN, 11-bit ID, 500 kbaud | **The bench protocol.** It matches the shipped profile's `0x7DF` / `0x7E0` / `0x7E8` and the 500000 default in `setup_can.sh` |
| 7 | ISO 15765-4 CAN, 29-bit ID, 500 kbaud | Phase 9 |
| 8 | ISO 15765-4 CAN, 11-bit ID, 250 kbaud | Optional second bitrate, section 6.5 |
| 9 | ISO 15765-4 CAN, 29-bit ID, 250 kbaud | Phase 9 |

`AT SP 6` selects it and saves it; `AT TP 6` tries it without saving. Automatic search
(`AT SP 0`) should **not** be the first thing tried on the bench: a search transmits
requests in other protocols, and a deterministic starting point makes a failure legible.
It belongs in its own test (6.2, item 2) once the deterministic path passes.

### 6.2 Test list

Numbered so each can be reported individually against section 10 of the plan. Each maps to
behavior already covered on vcan, so a failure is a *transport* finding, not a new protocol
question.

**Bring-up**

1. `AT Z` resets; `AT I` and `AT @1` identify the device. Recorded verbatim (4.3).
2. `AT RV` returns a plausible supply voltage.
3. `AT SP 6`, then a request, then `AT DP` / `AT DPN` reports protocol 6.
4. `AT SP 0` auto-search settles on protocol 6 against this simulator.

**OBD, functional addressing on `0x7DF`**

5. `01 00` returns the supported-PID bitmap; the chain `01 00` → `01 20` → … terminates
   where Phase 5 made it terminate.
6. `01 0D`, `01 05`, `01 0C`, `01 2F`, `01 1C` return the shipped profile's values.
7. A multi-parameter request (`01 0D 0C 05`) returns every parameter in request order —
   the DEV-18 behavior, over a real ELM327 rather than a raw socket.
8. A six-parameter request produces a **multi-frame** response, exercising flow control
   with the ELM327 as the flow-control sender rather than a test harness.
9. `09 02` returns the VIN across several frames with item count `0x01` (DEV-02).
10. `03` returns the shipped profile's two trouble codes.
11. `04` clears them; `03` then returns none.

**UDS, physical addressing**

12. `AT SH 7E0` then `10 01`, `11 01`, `19 02 FF`, `14 FF FF FF`, `3E 00`.
13. `3E 80` produces **no response**, and the ELM327 reports its own no-data condition
    rather than the simulator answering. The most interesting test in the list, because
    Phase 7's suppression is invisible to a raw socket test except as a timeout.
14. `10 05` returns `7F 10 12`; a negative response reaches the tester unaltered.
15. An unclaimed service returns `7F <SID> 11` (DEV-06).

**Cross-protocol and lifecycle**

16. A clear over OBD Mode 04 is visible to UDS `19 02 FF`, and the reverse — the Phase 6
    shared store, on real wire.
17. The demonstration scenario profile drives values a tester can watch change, and the
    timed fault appears, over a real bus (Phase 7).
18. SIGINT stops the simulator cleanly while the tester is connected; restarting restores
    the configured initial state.

**Independent capture**

19. `candump` on the host records the frames for a representative subset, so the bytes are
    confirmed by something that is neither the simulator's log nor the ELM327's report.

### 6.3 The finding most likely to waste bench time

ELM327DSJ, "CAN Input Frequency Matching": from **firmware 2.1** the device "actually
measures the input frequency and requires that it matches that of the selected CAN
protocol before any test message can be sent", and "if the user is trying a non-standard
OBD frequency, but a standard frequency is received, a send will not be allowed."

So **a bitrate mismatch does not produce an error — it produces silence**, and a silent
ELM327 looks exactly like a dead adapter, bad wiring or a broken simulator. The datasheet
also supplies the lever: `AT BI` bypasses the initiation process, and "this frequency
matching test will also bypassed". That belongs in the troubleshooting section as a
*diagnostic*, not as a normal step — needing `AT BI` means the bench is misconfigured.

This is also the argument for gap 5 in section 5.1: warning at setup time is cheaper than
discovering it through silence.

**Amended 2026-09-23 — the mechanism is narrower than this section states, and silence is
not the only symptom.** Read in full, page 62 says the frequency check "is only used while
searching for a valid protocol", so a bench that selects protocol 6 with `AT SP 6` rather
than searching is not in this path; and a send is allowed "if the input signal frequency
matches the CAN setting (250 or 500 kbps), **or if there appears to be no signal**". A
bitrate mismatch can also surface as an error: page 87 gives `CAN ERROR` for "a baud rate
that does not match the actual data rate", and `NO DATA` when the `AT ST` timer expires.
The ruling on gap 5 is unaffected — the warning is still cheap and the diagnosis still
expensive — but **the troubleshooting section must list three symptoms, not one**: silence,
`NO DATA` and `CAN ERROR`. See [0008 §6](0008-phase-8-question-resolutions.md).

### 6.4 Bluetooth

The same AT sequence over a different serial transport. It tests the pairing and the
serial binding (`rfcomm`, or a `/dev/rfcomm*` device), not the protocol, so it reruns a
subset — bring-up plus a handful from each group — rather than all nineteen.

**If no Bluetooth adapter is available, the row is reported `not verified` with the
reason**, per the acceptance-criterion traceability rule in section 10 of the plan. It is
never inferred from the USB result.

**Amended 2026-09-23 — Bluetooth is optional for the initial V1.0 release.** This section
and [section 5 of the plan](../modernization-plan.md) disagreed: section 5 listed a
Bluetooth adapter's `ATI` output as a required bench record, so a missing dongle blocked
the V1.0 tag however honestly this section reported it. Neither document acknowledged the
other. The ruling is that the V1.0 bench requires a SocketCAN adapter, a physical bus with
a second node, and a real **USB** ELM327; Bluetooth is a separately reported, optional
acceptance test, marked **not verified** when untested and never inferred from the USB
result. Section 5 of the plan is amended to match. See
[0008 §3](0008-phase-8-question-resolutions.md).

### 6.5 250 kbaud

`setup_can.sh` at 250000 and `AT SP 8` exercise the other OBD bitrate. Worth doing because
it is nearly free once the bench exists and it tests that nothing hard-codes 500 kbit/s.
**Optional**, and reported as not attempted if it is not attempted.

## 7. The `tests/hardware` suite

### 7.1 Rules it must obey

1. **Never runs in CI, and never runs by accident.** The `hardware` marker already says
   so. Default `pytest` collects `tests/unit`, `tests/characterization` and
   `tests/integration`; the hardware suite is opt-in by marker *and* by an explicit
   environment variable naming the interface and the serial device, so a developer who
   happens to have a `can0` does not start transmitting on it.
2. **Skips with a reason, never fails, when the bench is absent** — the pattern
   `tests/integration/conftest.py` already establishes for `CAN_ISOTP`.
3. **Never asserts a value the vcan suite does not already assert.** Its job is to show
   the same bytes survive real wire. A new expectation belongs in the vcan suite first.
4. **Tests are individually runnable and individually reportable**, because a bench run is
   partly manual and a partial result must be recordable.
5. **The ELM327 side is driven as a serial device**; the response text is parsed, and both
   the raw text and the decoded bytes are recorded.

### 7.2 A dependency question the user should decide

Driving a serial device needs either `pyserial` or hand-rolled `termios` code.
`pyserial` would be the project's **first new dependency since Phase 4**, and it would be
used by nothing but an opt-in suite that CI never runs.

| Option | For | Against |
|---|---|---|
| **A** — `pyserial` as a `[hardware]` extra | Standard, well-maintained, handles timeouts and framing properly | A new dependency, even if optional |
| **B** — stdlib `termios` / `os.read` | No new dependency | More code to own, for a suite that runs rarely, and serial timeout handling is exactly where hand-rolled code goes wrong |
| **C** — no automation; a documented manual procedure | Nothing to maintain; matches how a bench is actually used | Nothing is repeatable, and the plan asks for a `tests/hardware` **suite** |

**Recommendation: A**, as an optional extra so the default install is unchanged. **B** is
defensible. **C** contradicts the plan's wording and is not recommended, though parts of
section 6 will remain manual under any option.

## 8. Troubleshooting documentation

The plan asks for it explicitly. From this review, it must at minimum cover: no frames at
all (termination, wiring, ground, `ip -details -statistics` counters); the interface going
down and staying down (bus-off, `restart-ms`, `ip link set canX type can restart`); a
silent ELM327 (6.3, and `AT BI` as a diagnostic); the ELM327 reporting the wrong protocol
(`AT DPN`, `AT SP 6`); a dongle that identifies as an ELM327 but behaves differently
(4.3); and the simulator's own guard messages, which already name their cause.

## 9. Acceptance criteria, and how each is verified

Per the traceability rule in [section 10](../modernization-plan.md). **Every criterion
whose verification is "a bench run" is unverifiable until the hardware exists, and each
will be reported `not verified` with its reason until it is actually performed.**

| # | Criterion | Verification |
|---|---|---|
| 1 | `setup_can.sh` handles bitrate, and the gaps in 5.1 that are accepted are implemented | Script review; a shell-level test where one is meaningful; the vcan suite proves no regression |
| 2 | `setup_can.sh` warns on a bitrate the OBD conventions do not use | Direct invocation and its output |
| 3 | `--bitrate` guidance present and consistent across README, script usage and testbench document | Documentation review |
| 4 | `tests/hardware` exists, is opt-in, and is skipped with a reason when the bench is absent | `pytest` with and without the marker and the environment variable, on a host with no bench |
| 5 | `tests/hardware` is never collected by a default run | `pytest --collect-only`, count unchanged |
| 6 | `docs/hardware-testbench.md` exists and carries every field in 4.4 | Documentation review |
| 7 | Troubleshooting section covers the six cases in section 8 | Documentation review |
| 8 | **Every test in 6.2 passes against a real ELM327 over physical CAN** | **A bench run. Not verifiable otherwise** |
| 9 | Bluetooth acceptance (6.4). **Optional for V1.0 as of 2026-09-23**, separately reported | **A bench run**, or `not verified` with the reason. It never blocks the V1.0 tag and is never inferred from the USB result |
| 10 | 250 kbaud (6.5) | **A bench run**, or `not attempted` |
| 11 | `candump` capture confirms the bytes independently for a representative subset | **A bench run**; captures archived |
| 12 | Conformance rows exercised on hardware gain `hardware validated: yes`, **and no others do** | Row-by-row review against the bench log |
| 13 | No row gained `standards validated` | Review; section 3 |
| 14 | No wire behavior changed | Full suite unchanged; the differential comparison if any protocol file is touched |
| 15 | Full regression: unit, characterization, vcan integration, ruff, mypy, CI green | The standing section 10 gate |

### 9.1 What `hardware validated: yes` will mean, and what it will not

It will mean: this behavior was exercised on physical CAN, against the adapter and
firmware named in `docs/hardware-testbench.md`, on a recorded date and a recorded commit.

It will **not** mean the behavior is correct per any specification; that is
`standards validated`, which stays `no`. It will not extend to rows that were not
exercised — a phase that tests twelve behaviors marks twelve rows, not sixty-one. It will
not extend to other adapters. And it will not be marked from a run that was partly manual
unless the manual part is recorded with the same detail as an automated one.

## 10. Risks and open questions

### 10.1 Risks

| Risk | Mitigation |
|---|---|
| **No hardware is available**, so the V1.0 gate cannot close | Everything in criteria 1 to 7 is doable without a bench and delivers real value. The bench-dependent criteria are reported `not verified` and the phase is reported **partially complete**, never complete. This must not be papered over: Phase 8 is the V1.0 gate |
| A clone ELM327 fails a test | 4.3: record the identification, investigate against the datasheet, report as an interoperability finding naming the adapter |
| Bitrate mismatch presents as silence | 6.3, and criterion 2 |
| Bus-off leaves the interface down mid-run | 5.1 gap 1 |
| Physical CAN reveals a genuine defect | Good: it is what the phase is for. It gets its own DEV identifier, evidence and commit, and does not get fixed inside a "hardware validation" commit |
| Scope creep into Phase 9 | 29-bit and multi-ECU are Phase 9 and the plan says they do not gate this bench |

### 10.2 Questions for the user

**All six were resolved on 2026-09-23, together with a seventh conflict this section did
not ask about (Bluetooth versus the plan's section 5). The rulings and their reasoning are
in [0008](0008-phase-8-question-resolutions.md); the questions are left below as asked.**

| # | Ruling, in one line |
|---|---|
| 1 | No connected bench. Proceed on that assumption; buy nothing; claim no hardware validation. Unplugged equipment is a separate inventory exercise |
| 2 | **A** — `pyserial` as an optional `[hardware]` extra; configurable port and baud; read-until-prompt; independently tested parser |
| 3 | Yes. No runtime `--bitrate`; bitrate stays with `setup_can.sh` and its documentation |
| 4 | Gaps **1+2 together**, **3 guarded**, **5 as a non-rejecting warning**. Gap 4 becomes troubleshooting documentation. Gap 6 deferred |
| 5 | Split into **8a** (deliverable now) and **8b** (stays open). V1.0 is not tagged without 8b |
| 6 | Confirmed out of scope: the simulator never transmits on a live vehicle bus. Passive listen-only capture may be considered separately, with its own safety review |

1. **Is hardware available, and which?** This determines whether Phase 8 can close the
   V1.0 gate or only prepare for it. If a specific adapter and ELM327 are already to hand,
   naming them now lets the testbench document be written against the real thing rather
   than restructured later.
2. **Serial driving: A, B or C** (section 7.2)? Recommendation **A**, `pyserial` as an
   optional extra.
3. **Is `--bitrate` read correctly** as guidance about `setup_can.sh`, not a new
   `ecu-simulator` option (section 5.3)?
4. **Which of the six `setup_can.sh` gaps are in scope?** The recommendation is 1, 2 and 5
   certainly; 3 and 4 if cheap; 6 only if a bench turns out to need it.
5. **If no bench materialises, should Phase 8 land its documentation-and-script half** and
   stay open for the hardware half, or wait entirely?
6. **Real-vehicle testing is in no phase.** Confirmed as out of scope? It carries safety
   and legal considerations this project has never discussed, and a simulator that also
   drives a real bus is a different kind of tool.

## 11. Outcome

**This document is a review. No code, script, test or profile has been changed, and no
status column has moved.** Phase 8 implementation starts only on the user's approval, and
`hardware validated` stays `no` on all 61 conformance rows until a bench run has actually
been performed and recorded.
