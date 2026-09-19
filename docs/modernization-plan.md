# ecu-simulator modernization plan

Status: approved 2026-09-17. This is the single authoritative roadmap. The audit that
produced it is summarized in section 1; the wire-level findings it pins are tracked in
[known-deviations.md](known-deviations.md).

"Production-quality" here means reliable, maintainable, testable, deterministic,
CI-friendly testbench software. It does not mean safety-certified ECU software, and
nothing in this project claims SAE or ISO compliance.

## 1. Audit summary

Repository at commit ce46b87: 16 Python files, about 1,000 lines, archived upstream.

Confirmed problems:

- Flat script layout, no package, no `pyproject.toml`, no declared dependencies.
- Depends on an out-of-tree `can-isotp.ko` loaded with `insmod` from a hard-coded Kali 5.3
  path. `CAN_ISOTP` has been in mainline since Linux 5.10 and auto-loads on socket creation.
- Interface setup via `os.system` with string concatenation, `ifconfig`, `dmesg`, run as root.
- Four non-daemon threads in `while True`; SIGINT does not stop the process.
- CAN file logger uses the removed python-can `socketcan_native` backend; ISO-TP file
  logger blocks on four sockets in sequence and cannot work.
- Physically addressed OBD requests on 0x7E0 are received by a socket that is never read.
- pytest collection fails on duplicate test basenames.
- All configuration and pre-encoded responses are module globals loaded at import.
- Vehicle speed increments on every read; coolant temperature is random.
- Protocol coverage: OBD 01 (05, 0D, 2F, 51), 03, 09 (02, 0A); UDS 10, 11, 19/02.

Worth preserving: SAE J2012 DTC encoding, supported-PID bitmask arithmetic, UDS positive
and negative response framing, the functional/physical two-address concept, default CAN
IDs, VIN and ECU-name defaults, and the existing 31 tests as characterization tests.

## 2. Target architecture

```
                    ┌──────────────────────────────────────────────────┐
                    │  Runtime (asyncio loop, lifecycle, signals,      │
                    │  clock, scenario ticks)                          │
                    └───────────────┬──────────────────────────────────┘
                                    │ builds from validated config
        ┌───────────────────────────┼───────────────────────────────┐
        ▼                           ▼                               ▼
  VehicleState               Ecu("engine")                    Ecu("tcm")
  common + powertrain        ├ DtcStore                       ├ DtcStore
                     <───────┤ protocols registered by SID    ├ ...
                             └ handle(request) -> response(s)
                                    ▲
                                    │ DiagnosticRequest (payload + addressing only)
                    ┌───────────────┴──────────────────────────────────┐
                    │  AddressRouter: (transport id, target address)   │
                    │  -> ECU name. A dict, built from config.         │
                    └───────────────┬──────────────────────────────────┘
                                    ▲
        ┌───────────────────────────┴───────────────────────────────┐
        │  DiagnosticTransport                                      │
        │   IsoTpTransport over kernel CAN_ISOTP (Classical CAN)    │  V1.0
        │   CAN FD link-layer options                               │  V2
        │   DoIpTransport (experimental)                            │  V2
        └───────────────────────────────────────────────────────────┘
```

Rules:

1. `transport/` imports nothing from `ecu/`, `protocols/`, `vehicle/`, or `dtc/`. It
   produces `DiagnosticRequest(payload, addressing_mode, source_address, target_address,
   functional, transport_context)` and consumes `DiagnosticResponse(payload, delay)`.
2. The router owns address-to-ECU mapping. Transports never see ECU objects.
3. Protocol and ECU code are synchronous and socket-free; unit tests need no event loop.
4. Encoders read physical values (rpm = 2500) from `VehicleState`; only the protocol
   module produces wire bytes.
5. Protocols register the SIDs they serve. Two protocols claiming one SID on one ECU is a
   configuration error unless the profile orders them explicitly. No first-match.
6. UDS data services delegate to `DidProvider` and `DtcProvider` registries on the ECU.
   These are the extension points a future OBDonUDS module may use; whether OBDonUDS is a
   provider, a sibling protocol, or both is not decided until SAE J1979-2 is available.
7. DTC clearing is one operation on the shared `DtcStore`; OBD Mode 04 and UDS 0x14 both
   call it and neither owns the transition.

Concurrency: asyncio, one loop, `add_reader` per socket, timers for session and scenario
work, `add_signal_handler` for shutdown. No thread per ECU.

Vehicle model: `CommonState` plus one of `IcePowertrain`, `HevPowertrain`,
`BevPowertrain`, composed from `IceState`, `TractionBattery`, `EMotor`, `Charging`.
Signals are addressed by dotted paths (`engine.rpm`, `battery.soc`). Supported-PID masks
are computed from which signals exist on the configured vehicle.

Configuration: YAML profiles validated by Pydantic v2. The schema is multi-ECU from V1.0
(`ecus:` mapping) even though the V1.0 profiles define one ECU. Duplicate names or CAN IDs,
VIN length, DTC format, PID/DID ranges, unknown vehicle or ECU types, unknown signal
paths, and unsupported transport options are rejected before any socket opens.

ISO-TP: the kernel `CAN_ISOTP` socket is the implementation. How it is driven, stdlib
socket with a minimal option wrapper or `isotp` 2.x `isotp.socket`, is decided by the
Phase 2 spike (section 4). The maintained dependency is preferred unless the native route
shows a concrete technical advantage. Both must expose `fileno()` and non-blocking mode
for `asyncio.add_reader`; the loser is deleted.

Functional addressing: if the kernel allows several ISO-TP sockets to share the functional
RX ID with distinct TX IDs, each OBD-capable ECU gets a second socket bound that way and no
raw CAN parsing exists in the project. Otherwise one `CAN_RAW` receiver in
`transport/socketcan/functional.py` parses single frames only. Responses always leave via
the ECU's physical socket.

Directory layout:

```
pyproject.toml
src/ecu_simulator/
  cli.py  app.py  clock.py  logging.py
  config/   vehicle/   dtc/   scenario/   faults/   ecu/
  protocols/base.py  protocols/obd/  protocols/uds/
  transport/messages.py  transport/base.py  transport/socketcan/
  profiles/ice_default.yaml
tests/unit  tests/integration  tests/hardware  tests/characterization
scripts/   docs/   .github/workflows/ci.yml
```

## 3. Release mapping

| Release | Content | Phases |
|---|---|---|
| V1.0 | Python 3.12+, package, lifecycle, kernel ISO-TP, Classical CAN on vcan0 and can0, transport/protocol separation, router, multi-ECU schema with single-ECU profile, configurable CAN IDs, OBD Modes 01 (common PIDs), 03, 04, 07, 09 with VIN, mutable vehicle state, DTC store, deterministic scenarios, UDS 0x10, 0x11, 0x14, 0x19/02, stateless 0x3E, unit + vcan tests, CI, ELM327 USB/Bluetooth acceptance | 0 to 8 |
| V1.1 | Multi-ECU profile and functional fan-out, fault injection, 29-bit addressing, UDS session state machine, S3, TesterPresent timing, 0x22, 0x19/01 and /0A | 9 to 11 |
| V1.2 | 0x2E, mock 0x27, richer ICE/HEV/BEV profiles, additional UDS behavior | 12 |
| V2 | CAN FD, DoIP (experimental only), gateway simulation | 13 to 15 |
| Spec-gated | SAE J1979-2 OBDonUDS, SAE J1979-3 ZEVonUDS | 16 |

Multi-ECU on physical addressing may fall out of the router in V1.0. If it does, its
status is recorded accurately in the conformance table; it never gates the V1.0 bench.

UDS 0x3E in V1.0 validates the request, supports the zeroSubFunction sub-function, honours
`suppressPosRspMsgIndicationBit`, and returns the positive response. It does not implement
S3, does not touch any timer, creates no session state, and claims no ISO 14229 session
compliance. Real session timing is V1.1.

## 4. Phases

Each phase leaves the repository runnable. Behavioral wire changes are one commit each,
preceded by a characterization test that pins the old behavior. From Phase 3 onward every
phase must also pass the phase completion gate in section 10 before it is declared
complete, and any phase depending on an external standard, interface, library or tool must
first pass the documentation and standards verification gate in section 11. Each phase's
Definition of Done below is in addition to those gates.

### Phase 0 — Baseline and characterization (V1.0)

Objective: pin current byte-exact behavior. Fix pytest collection with `__init__.py`
files only. Golden tests for every OBD and UDS response, DTC encoding, config access, and
listener socket wiring. Every known-wrong behavior recorded in `known-deviations.md` with a
strict `xfail` where the corrected behavior is certain. No production code changes.
DoD: `pytest tests` collects and passes; the 31 legacy tests are untouched.

### Phase 1 — Packaging, tooling, CI, test baseline (V1.0)

`pyproject.toml`, `src/` layout, `pip install -e .`, `ecu-simulator` entry pointing at the
existing `main()`, ruff, mypy (lenient), pytest markers `vcan` and `hardware`, GitHub
Actions unit job on 3.12 and 3.13, integration job scaffold that proves whether the runner
can create `vcan0` and load `can_isotp`. Only behavioral change: python-can 4.x interface
name in the CAN logger. Risk L.

### Phase 2 — SocketCAN ISO-TP transport and lifecycle (V1.0)

Deliverable 1: the ISO-TP binding spike, time-boxed to one day, recorded in
`docs/decisions/0001-isotp-binding.md` with the shared-functional-RX-ID kernel result.
Then: `transport/messages.py`, `IsoTpSocket` protocol with the chosen binding, interface
probing and `TransportError`, asyncio runtime with SIGINT/SIGTERM cleanup, CLI
`--interface`, `--profile`, `--log-level`, `scripts/setup_vcan.sh` and `setup_can.sh`
without `insmod`, removal of `os.system` and the `.ko` path, removal of the file loggers.
Wire changes, each own commit after its failing test: physical OBD requests answered,
TX padding enabled. DoD: no thread remains, `transport/` imports no domain module
(enforced by a test), vcan job green or skipping with a reason. Risk M.

### Phase 3 — Transport/protocol separation (V1.0)

`AddressRouter`, `Ecu` with SID registration and conflict detection, `DidProvider` and
`DtcProvider` registries, legacy OBD and UDS wrapped as registered protocols, per-ECU
logger context. Wire change: NRC 0x11 for unsupported SIDs on a physical address.
DoD: `Ecu` never receives a socket; the router never inspects payloads. Risk L.

Decided 2026-09-18: one implicit ECU named `engine` is derived from the legacy JSON so
Phase 4 can make the ECU list explicit without an architecture change. Each address is a
route carrying the protocols enabled there and the policy for a service identifier none
of them claims; a protocol that is not enabled on a route never receives the request, and
no response is ever filtered out after a protocol has produced it. The `suppressPosRspMsgIndicationBit` fix
(DEV-07) is protocol semantics, not routing, and moves to the UDS behavior work
(Phase 6/7 with 0x3E, or Phase 11 with session handling).

### Phase 4 — Vehicle and ECU state model, configuration (V1.0)

`vehicle/`, `clock.py`, Pydantic schema, YAML loader, `validate-config`, per-ECU DIDs and
DTCs in config, `profiles/ice_default.yaml`. Legacy JSON config, `ecu_config.py`, and
`addresses.py` deleted. No wire change. Risk M.

Deviation, Phase 4: the default profile ships inside the package, at
`src/ecu_simulator/profiles/ice_default.yaml` rather than in a top-level `profiles/`
directory, so the installed console script can find it without packaging a data directory
outside the package. `--profile` takes any path.

### Phase 5 — Legacy OBD refactor and PID expansion (V1.0)

`PidDefinition` table reading `VehicleState`; Mode 01 PIDs 00, 01, 04, 05, 06, 07, 0B,
0C, 0D, 0E, 0F, 10, 11, 1C, 1F, 2F, 42, 46, 51; Modes 04, 07, 09 with corrected item
counts; dynamic supported masks. Wire changes, one commit each: continuation bits, Mode 09
item count, ECU-name encoding, multi-PID requests. Formulas from public references,
labelled "not standards-validated"; PID 0x01 layout flagged experimental. Risk M.

### Phase 5.1 — Several Mode 01 parameters in one request (V1.0)

Added 2026-09-19. A follow-up to Phase 5, not a new stage of the roadmap: Phase 5 was
assigned DEV-18 and did not implement it, and the correction belongs to the Mode 01
request parser rather than to the DTC state model Phase 6 builds. It is separated so that
an OBD parser change is not folded into a DTC phase.

Scope, and nothing beyond it: several Mode 01 parameters answered in one response;
byte-exact tests built from the two worked CAN captures in the ELM327 datasheet;
single-parameter behavior preserved; malformed and unsupported-parameter behavior tested
explicitly; the DEV-18 strict xfail transition; conformance and deviation updates. No
unrelated OBD refactoring. Evidence review in
[decisions/0005-phase-5-1-multi-pid-evidence.md](decisions/0005-phase-5-1-multi-pid-evidence.md).
Risk L.

Both standing gates apply to it independently: section 11 before implementation, section
10 before it is declared complete.

### Phase 6 — DTC state management (V1.0)

`DtcStore` (pending, confirmed, stored, MIL) with one `clear()` operation; OBD 03/04/07
and UDS 0x14 and 0x19/02 with the status-mask fix; protocol encoders in
`protocols/*/dtc.py`. Legacy `uds/` deleted. Risk M.

Decided 2026-09-19, from the evidence review in
[decisions/0004-phase-6-dtc-evidence.md](decisions/0004-phase-6-dtc-evidence.md) and
approved before implementation. Two narrowings of the sentence above:

- **Three flags, not four.** `stored` is folded into `confirmed`; service 03 reports the
  confirmed view. The evidence distinguishes no third state, and nothing on the wire could
  tell them apart. This is a modeling decision for the behavior this project serves, not a
  claim that SAE or ISO define the two as equivalent.
- **OBD Mode 07 is deferred**, and DEV-11 is split so Mode 04 can be fixed while Mode 07
  stays open. Its `47` + count framing rests on two open-source implementations with no
  public worked example, which section 11 does not accept for new wire behavior. The
  generic `pending` state it would report is implemented and tested.

Also deferred out of Phase 6, each for a stated reason: Mode 01 PID 0x01, the third byte
of the UDS DTC number, status bits needing an operation-cycle model, `0x19` sub-functions
`0x01` and `0x0A`, `MemorySelection`, `0x3E`, DEV-07, DEV-15 and DEV-17.

### Phase 7 — Deterministic scenario engine and minimal 0x3E (V1.0)

Generators constant, ramp, sine, stepped, sequence, timeline as pure functions of `t`;
lazy evaluation on read plus a periodic tick for timed DTC events; `SimulatedClock` in
tests. Replaces the speed counter and random coolant. Stateless UDS 0x3E as scoped in
section 3. Risk L.

### Phase 8 — Physical CAN and ELM327 validation (V1.0 gate)

`setup_can.sh` bitrate handling, `--bitrate` guidance, `tests/hardware` opt-in suite,
`docs/hardware-testbench.md` with exact hardware and firmware, troubleshooting. Risk M.

### Phase 9 — Multi-ECU (V1.1)

`profiles/multi_ecu.yaml`, functional fan-out integration test, `list-ecus`, 29-bit
normal fixed addressing with its functional test.

### Phase 10 — Fault injection (V1.1)

`FaultPolicy` in the ECU pipeline: drop, delay, NRC override, response pending then final,
truncate, corrupt, ECU offline; transport hooks for ignored flow control and silence.
Off by default, deterministic triggers.

### Phase 11 — UDS session handling (V1.1)

Session state machine, S3 timeout, TesterPresent timing, 0x22 with providers, 0x19/01 and
/0A, configurable P2/P2* in the 0x10 record, 0x11 effects on session.

### Phase 12 — UDS data and security (V1.2)

0x2E for writable DIDs, 0x27 with a documented non-OEM mock strategy, session and security
gating NRCs, richer profiles.

### Phase 13 — CAN FD (V2)

`fd`, `tx_dl`, link-layer options, MTU probing, vcan FD tests.

### Phase 14 — DoIP, experimental (V2)

Vehicle announcement, routing activation, diagnostic message, logical addresses, TCP
lifecycle. Built only from publicly documented layouts; labelled experimental
interoperability in the conformance table and CLI help; never called production-quality
without ISO 13400 review.

### Phase 15 — Gateway simulation (V2)

Generic routing ECU, visibility control. No OEM secure-gateway behavior.

### Phase 16 — OBDonUDS / ZEVonUDS (spec-gated)

Nothing beyond reserved protocol names until SAE J1979-2 and J1979-3 are available.

## 5. V1.0 acceptance gate

V1.0 is tagged only when all of the following pass reproducibly, recorded in
`docs/release-v1.0-evidence.md`.

Software, in CI on two consecutive runs:

- full unit suite with the coverage threshold met
- vcan integration suite, not skipped
- deterministic scenario tests with `SimulatedClock`
- clean startup and SIGINT/SIGTERM shutdown within one second, no leaked sockets
- physical-address OBD flow
- functional-address OBD flow
- ISO-TP multi-frame VIN with flow control
- Mode 01, Mode 03, Mode 04 followed by Mode 03, Mode 07, Mode 09
- malformed-configuration suite covering every validation rule

Physical bench on `can0`, recorded with:

- exact CAN interface hardware
- exact USB ELM327 adapter and its `ATI` output
- exact Bluetooth ELM327 adapter and its `ATI` output
- results for `0100`, `010C`, `010D`, `03`, `04` followed by `03`, `0902`

## 6. Conformance vocabulary

`docs/conformance.md` carries one row per service and PID with five status dimensions,
and these five are the only status vocabulary used anywhere in the project:

| Dimension | Meaning |
|---|---|
| implemented | the behavior exists in this codebase |
| unit tested | pinned by a unit or characterization test |
| integration tested | exercised over the real kernel ISO-TP path on a vcan interface |
| hardware validated | exercised on physical CAN with named hardware and firmware |
| standards validated | checked against the applicable specification revision, with the clause cited |

The last column stays empty until someone with the specification reviews the behavior and
cites the clause. Hardware interoperability never fills it, and neither does agreement
with vehicle captures or open-source implementations.

Interoperability is deliberately not a sixth dimension. Where interoperability evidence is
useful it is recorded as free text on the row it supports, for example
"Interoperability evidence: exercised with can-utils isotpsend and isotprecv" or
"Interoperability evidence: validated with a named ELM327 adapter". This keeps one
authoritative status vocabulary while preserving the evidence.

The README links to this table instead of listing supported services. The table is created
in Phase 4, seeded with the behavior that exists at that point, and extended as each later
phase changes behavior.

## 7. Dependency and standards inventory

The single authoritative inventory of external material this project relies on. Every
phase's source report under section 11 updates or references this section; no phase starts
a competing version table.

### 7.1 Specifications

| Specification | Revision checked | Availability | Consequence | Reviewed |
|---|---|---|---|---|
| ISO 15765-2 | not checked | Implemented by the kernel | None in-repo | — |
| ISO 15765-4 | not checked | Licensed; conventions public | Conventions implemented, labelled | — |
| SAE J1979 | `J1979_202505`, reaffirmed 2025-05-23; last technical revision Feb 2017 | Licensed, **text unavailable** | Listed PIDs implemented, labelled; also the only source for the service 03/04/07 framing and the service 01 PID 01 layout | Phases 5, 6 |
| SAE J1979-DA | `J1979DA_202607`, revised 2026-07-16 (supersedes `J1979DA_202508`) | Licensed, **text unavailable** | Would settle the service 09 field layouts and the service 01 PID 01 monitor bits; DEV-03 and PID 0x01 deferred for want of it | Phases 5, 6 |
| SAE J2012 | `J2012_202509`, revised 2025-09-01 (supersedes `J2012_201612`) | Licensed, **text unavailable** | Existing two-byte encoder retained; the third byte of the UDS DTC number stays frozen at 0x01 for want of the failure-type definition | Phase 6 |
| SAE J2012-DA | Current revision **not established**: the publisher index returns `J2012DA_202403` as the latest catalogued entry and a 2025 edition appears in reseller listings. No `J2012DA_202607` exists; that identifier is J1979-DA's | Licensed, **text unavailable** | None. This project encodes whatever codes a profile declares and depends on no standardized code meaning | Phase 6, found not applicable |
| ISO 14229-1 | **`ISO 14229-1:2026`, Edition 4, published 2026-06-05**; cancels and replaces `ISO 14229-1:2020` and its `Amd 1:2022` | Licensed, **text unavailable** | V1 services implemented, labelled; 0x14, 0x19/02, the DTC status byte and the availability mask rest on corroborating public material only | Phase 6 |
| SAE J1979-2, J1979-3 | not checked | Licensed, not public | Nothing implemented | — |
| ISO 13400-2 | not checked | Licensed; layouts public | Experimental only | — |
| ELM327 datasheet | ELM327DSJ | Public | Tester side; supplied the service 09 and service 01 captures behind the Phase 5 decisions, and the service 03 CAN framing, service 04 response and PID 01 first byte behind the Phase 6 ones | Phases 5, 6 |
| AUTOSAR CP R24-11 SWS Diagnostic Event Manager | R24-11, document ID 19 | **Public** | Corroborates the UDS DTC status byte bit layout and the post-clear state. Not ISO 14229-1 and never a conformance claim | Phase 6 |
| AUTOSAR CP R24-11 SWS Diagnostic Communication Manager | R24-11, document ID 18 | **Public** | Corroborates the 0x14 and 0x19/02 request and response shapes and the status-mask AND rule. Not ISO 14229-1 and never a conformance claim | Phase 6 |

No **specification** in this table has been reviewed against its text by this project, and
for those marked **text unavailable** an attempt was made and the document is paywalled.
Nothing is `standards validated`. Per-phase evidence reviews live in `docs/decisions/`.

The two AUTOSAR rows are the one exception to "text unavailable": those documents are
public and were read. They are official specifications of another standards organisation
that normatively reference ISO 14229-1, so they corroborate strongly, but they are not
ISO 14229-1 and reading them upgrades nothing to `standards validated`. Under
[section 11.2](#112-source-priority) they rank below the applicable specification and
above open-source implementations.

### 7.2 Runtime and development dependencies

Versions recorded when the phase named in the last column reviewed them.

| Item | Version in use | Current release | Source | Reviewed |
|---|---|---|---|---|
| Python | 3.12 and 3.13 supported; 3.12.12 in the venv | n/a | project configuration | Phase 4 |
| can-isotp | 2.0.7, pinned `>=2.0,<3` | 2.0.7 (2025-05-14) | PyPI metadata, project source | Phases 2, 2A |
| pydantic | 2.13.5, pinned `>=2.13,<3` | 2.13.5 (2026-08-28) | PyPI metadata, official docs | Phase 4 |
| ruamel.yaml | 0.19.1, pinned `>=0.19,<0.20` | 0.19.1 (2026-01-02) | PyPI metadata, experiment | Phase 4 |
| pytest | 9.1.1, pinned `>=8.0` | 9.1.1 (2026-06-19) | PyPI metadata | Phase 4 |
| mypy | 2.3.1, pinned `>=1.11` | 2.3.1 (2026-08-15) | PyPI metadata | Phase 4 |
| ruff | 0.16.8, pinned `>=0.6` | 0.16.8 (2026-09-16) | PyPI metadata | Phase 4 |
| Linux CAN_ISOTP | in-tree, kernel 6.8 and 6.17 observed | n/a | kernel source and experiments | Phases 2, 2A |

## 8. Non-goals for V1.0

CAN FD traffic, DoIP, OBDonUDS, ZEVonUDS, gateway routing, extended ISO-TP addressing,
UDS 0x22/0x27/0x28/0x2E/0x31, session timing, any OEM security algorithm, Windows or
macOS, Docker as a requirement, a GUI, J1939, any claim of standards compliance.

## 9. Commit sequence

One commit per line. Wire changes carry their tests. Bisectable.

Phase 0
1. `test: add package markers so pytest collects both test_services modules`
2. `test: characterize current OBD responses`
3. `test: characterize current UDS responses`
4. `test: characterize DTC encoding, config access, and listener socket wiring`
5. `docs: known deviations register and modernization plan`

Phase 1
6. `build: add pyproject.toml, src layout, console entry point`
7. `build: add ruff and mypy configuration`
8. `ci: unit tests, lint, type check, coverage on Python 3.12/3.13`
9. `ci: probe vcan and can_isotp availability on the runner`
10. `fix(logging): use python-can 4.x socketcan interface name`

Phase 2
11. `docs(decisions): ISO-TP binding spike result and kernel shared-rx-id check`
12. `feat(transport): DiagnosticRequest/Response with addressing metadata only`
13. `feat(transport): IsoTpSocket protocol and chosen kernel CAN_ISOTP binding`
14. `feat(transport): interface probing and TransportError`
15. `feat(app): asyncio runtime with signal handling and cleanup`
16. `feat(cli): --interface, --profile, --log-level`
17. `chore(scripts): privileged setup scripts without insmod; remove os.system`
18. `test(integration): physical OBD request is dropped (expected failure)`
19. `fix(obd): answer physically addressed requests`
20. `feat(transport): enable ISO-TP TX padding for OBD sockets`
21. `refactor: remove CAN and ISO-TP file loggers in favour of candump`

Phase 3
22. `feat(ecu): AddressRouter and Ecu with SID registration`
23. `feat(uds): DidProvider and DtcProvider registries`
24. `refactor(obd,uds): wrap legacy services as registered protocols`
25. `fix(uds): return NRC 0x11 for unsupported services`
26. `feat(logging): per-ECU logger context` (DEV-07 `suppressPosRspMsgIndicationBit` deferred to Phase 6/7 or 11)

Phase 4
28. `feat(vehicle): composed VehicleState with signal paths`
29. `feat(config): pydantic schema, YAML loader, validate-config`
30. `refactor: remove ecu_config.json, ecu_config.py, addresses.py`

Phase 5
31. `feat(obd): PID definition table reading VehicleState`
32. `feat(obd): add common Mode 01 PIDs`
33. `fix(obd): next-range bit only when PIDs exist`
34. `fix(obd): Mode 09 item-count byte for VIN and ECU name`
35. `feat(obd): Mode 04 and Mode 07`
36. `feat(obd): multi-PID Mode 01 requests`
37. `refactor: delete legacy obd package`

Phase 5.1
37a. `docs(decisions): Phase 5.1 multi-parameter evidence review`
37b. `test(obd): characterize the first-parameter-only Mode 01 request`
37c. `feat(obd): answer several Mode 01 parameters in one request`

Phase 6
38. `feat(dtc): DtcStore with a single clear operation`
38a. `refactor(obd): read stored DTCs from the shared store`
38b. `feat(obd): Mode 04 clears the shared DTC store`
38c. `refactor(uds): replace the legacy UDS module with a store-backed protocol`
38d. `test(uds): pin the 0x19 length-before-subfunction check`
39a. `fix(uds): validate the 0x19 sub-function before the request length`
39b. `fix(uds): require and apply the status mask for 0x19/0x02 (DEV-05)`
39c. `fix(uds): derive the DTC status byte and availability mask (DEV-16)`
40. `feat(uds): 0x14 ClearDiagnosticInformation via DtcStore.clear (DEV-23)`
41. `docs: Phase 6 conformance, deviations and README`

Commit 41 replaces the planned `refactor: delete legacy uds package`: the deletion
happened in 38c, because the replacement and the removal of what it replaces have to be
one bisectable step for the differential comparison to mean anything.

Phase 7
42. `feat(clock): Clock protocol with monotonic and simulated implementations`
43. `feat(scenario): deterministic generators replace speed counter and random coolant`
44. `feat(uds): stateless 0x3E TesterPresent`

Phase 8
45. `docs: conformance table, hardware testbench, README rewrite`
46. `test(hardware): ELM327 acceptance suite, opt-in`
47. `release: v1.0.0`

V1.1 and later commits follow the phase list above with numbering continued.

## 10. Phase completion gate

Standing requirement, added 2026-09-18. It applies to Phase 3 and every phase after it,
in addition to that phase's own Definition of Done in section 4.

A phase is not complete until the functionality introduced or changed in that phase has
been directly exercised and verified. Code review, a successful import, static analysis,
or an aggregate test count that does not exercise the new behavior are never sufficient
on their own. Targeted verification of the phase's actual behavior is mandatory.

### Required before declaring a phase complete

- Complete the documentation and standards verification gate in section 11 first, where
  the phase depends on an external standard, interface, library or tool.
- Add or update tests for every new or intentionally changed behavior.
- Run targeted tests for the functionality implemented in that phase.
- Run regression tests for adjacent behavior that could reasonably have been affected.
- Run the complete existing test suite.
- Run the local integration tests whenever the phase touches transport, sockets,
  CAN/ISO-TP, timing, lifecycle, routing, configuration loading, or wire behavior.
- Run hardware or manual validation when the phase requires behavior that cannot be
  meaningfully automated.
- Run `ruff`.
- Run `mypy`.
- Verify there are no unexpected XPASS results.
- Verify that existing characterization tests did not change except for deliberate,
  documented behavior corrections.
- Update the relevant DEV, conformance and documentation status for every intentional
  behavior change.
- Push the completed phase to `origin/modernization`.
- Wait for GitHub CI and verify that the required jobs are green.
- Record every CI test that was skipped, and why.

### Acceptance-criterion traceability

Every acceptance criterion listed for the phase maps to at least one of: an automated
unit test, an automated integration test, a characterization or regression test, a
static-analysis check, an explicit command and its result, or a documented manual or
hardware verification.

A criterion is never marked complete merely because the implementation exists. A
criterion that cannot be verified is reported as **not verified**, with the reason. It is
never silently treated as complete.

### Phase completion report

The report at each phase boundary states, explicitly:

- functionality implemented;
- behavior directly tested;
- each acceptance criterion and the verification that covers it;
- the exact test and verification commands executed;
- the exact results and counts;
- deliberate xfail changes with their DEV identifiers;
- regression-suite result;
- integration-suite result, where applicable;
- `ruff` result;
- `mypy` result;
- the GitHub CI run and its result;
- skipped tests and the reason for each;
- anything that could not be tested, and why;
- new defects or unexpected behavior discovered;
- local HEAD SHA, remote HEAD SHA, and the final `git status`.

## 11. Documentation and standards verification gate

Standing requirement, added 2026-09-18. It applies before implementing any phase that
depends on an external protocol, standard, operating-system interface, library or tool,
and it is a precondition of the phase completion gate in section 10: a standards-dependent
phase has no complete acceptance criteria until this review has been done.

Implementation never starts from model knowledge, the existing code, old README content,
blog posts or remembered protocol behavior. The current authoritative documentation for
the material the phase actually touches is identified and reviewed first.

### 11.1 Applicable versions

For every external specification or dependency the phase touches, identify the document or
library name, its current version or revision, its publication or release date where
available, whether a revision this project relies on has been superseded, whether the
specification text is actually accessible, and which source was used. The revision the
original repository used is never assumed to still be current.

### 11.2 Source priority

1. The applicable SAE or ISO specification, when legally available here.
2. Official standards-publisher information for revision and status.
3. Official Linux kernel documentation.
4. Official library or project documentation and source.
5. Well-maintained open-source implementations, as interoperability cross-checks only.
6. Secondary technical references, only where necessary.

Blogs, forum posts, question-and-answer sites, arbitrary repositories and generated
summaries are never treated as normative. An open-source implementation can demonstrate
interoperability; it never demonstrates standards compliance.

### 11.3 Licensed or unavailable standards

Where the applicable specification is not available, the report says so explicitly and
names the exact document and revision that would be needed. Normative behavior is not
reconstructed from memory, and missing requirements are not inferred from unrelated
implementations. Only behavior supported by sufficiently reliable accessible material is
implemented, and it is labelled `implemented`, `unit tested`, `integration tested` or
`experimental`, never `standards validated`. A feature that cannot be implemented
confidently without the specification is stopped rather than guessed; unrelated work that
does not depend on the missing material continues.

### 11.4 Existing behavior against current documentation

Before any wire-level change, record the current behavior, the proposed behavior, the
source supporting the proposal, the applicable revision, the DEV identifier where one
exists, and the tests that will prove the change. Wire behavior is never changed merely
because another open-source implementation differs.

### 11.5 Software and library interfaces

Dependencies are verified against the exact major version in use, not against remembered
API shapes. Where the behavior matters, it is also confirmed experimentally, and the
report keeps observed implementation behavior distinct from normative requirements.

### 11.6 Pre-implementation source report

Each standards-dependent or dependency-dependent phase produces a short report covering
only the items that phase touches, in the form:

| Item | Version/revision | Source type | Full text available? | Relevant to this phase |
|---|---|---|---|---|

The report updates or references the inventory in section 7; it never starts a competing
version table. Standards that appear only on the long-term roadmap are not researched.

### 11.7 Recording decisions

A documentation review that changes an architectural or protocol decision is recorded in
`docs/decisions/`. Implementation status belongs in `docs/conformance.md`.

### 11.8 Status vocabulary

The five status dimensions in section 6 are the only status vocabulary. Interoperability
is not a sixth dimension: it is recorded as evidence text on the row it supports, for
example "Interoperability evidence: exercised with can-utils isotpsend and isotprecv".
`standards validated` is used only where the implementation has actually been checked
against the applicable specification revision with sufficient evidence. Hardware
interoperability alone is never standards validation, and agreement with vehicle captures
or open-source implementations never upgrades it.
