# Hardware testbench

The bench this project's physical validation runs on, recorded so that someone else can
rebuild it and get the same bytes. The field list is
[0007 §4.4](decisions/0007-phase-8-hardware-validation.md); the rules about what may be
claimed from it are [0007 §4.3](decisions/0007-phase-8-hardware-validation.md) and
[§9.1](decisions/0007-phase-8-hardware-validation.md).

**Status: partially recorded. Phase 8b acceptance is not complete.**

A real bench exists and has been exercised once, on 2026-09-23, as a **preliminary smoke
test** — not as acceptance. What that run established, and the captures behind it, are in
[validation/phase-8-smoke-test/](validation/phase-8-smoke-test/README.md). This document
records the equipment; it does not repeat the evidence and does not restate the results.

**The mandatory USB ELM327 acceptance has not been performed.** The tester used so far is
a Bluetooth adapter. Under [0008 §3](decisions/0008-phase-8-question-resolutions.md) USB
acceptance is required for V1.0 and Bluetooth is optional and separately reported, so
nothing below substitutes for it. Acceptance criterion 8 remains `not verified`, all 61
rows of [conformance.md](conformance.md) still read `hardware validated: no`, and V1.0 is
not tagged.

---

## 1. Bench topology

```
   ┌──────────────┐  USB   ┌──────────────┐  CAN-H (pin 6)   ┌───────────────┐
   │  Linux host  │◄──────►│   CANable    │◄────────────────►│ OBD-II        │
   │  simulator   │        │  (gs_usb)    │  CAN-L (pin 14)  │ breakout      │
   └──────────────┘        │  120 Ω ON    │◄────────────────►│               │
                           └──────┬───────┘                  │   ┌────────┐  │
                                  │ GND ◄────────────────────┼──►│OBDLink │  │
                                  │                          │   │   LX   │  │
                           (USB ground)                      │   └───┬────┘  │
                                                             └───────┼───────┘
                                                              12 V ──┘  ) )) Bluetooth
                                                                            │
                                                                     Android host
```

Two nodes, which is the minimum: a lone transmitter gets no acknowledgement. The ELM327 is
both the tester and the second node.

## 2. Equipment

### 2.1 Host

| Field | Value |
|---|---|
| Make and model | not recorded — a developer workstation, not bench-specific |
| Distribution | **Ubuntu 22.04.5 LTS** |
| Kernel | **6.8.0-138-generic** |
| Python | **3.12.12** |
| `iproute2` | **5.15.0** |
| can-utils | **2020.11.0-1** |
| `CONFIG_CAN_ISOTP` | present; `AF_CAN`/`SOCK_DGRAM`/`CAN_ISOTP` sockets open |

### 2.2 CAN adapter

| Field | Value |
|---|---|
| Make and model | **CANable**, original revision — identified by two jumpers (`Boot`, `Term`) and a 48 MHz clock; the CANable 2.0 is CAN-FD capable at 170 MHz |
| Interface and connection | USB, enumerates as `can0` |
| USB identity | `1d50:606f` — OpenMoko, Inc. / Geschwister Schneider CAN adapter |
| USB strings | manufacturer `canable.io`, product `canable gs_usb` |
| Driver / kernel module | **`gs_usb`** (in-tree) |
| Controller clock | 48 MHz |
| Firmware | not recorded — candleLight; the running version is not reported through SocketCAN |
| Serial number | **withheld — see §5** |
| Termination | **onboard 120 Ω enabled**, `Term` jumper toward the screw terminals |
| Switchable termination via netlink | **not supported.** `ip -details link show can0` reports no `termination` attribute, so `CAN_TERMINATION` in `setup_can.sh` correctly skips on this adapter |
| Link configuration used | bitrate **500000**, sample-point 0.875, `restart-ms 0`, `ECHO` flag set |
| `ip -details link show` | recorded in [`can0-stats-before.txt`](validation/phase-8-smoke-test/can0-stats-before.txt) and [`can0-stats-final.txt`](validation/phase-8-smoke-test/can0-stats-final.txt) |

### 2.3 Diagnostic tester

| Field | Value |
|---|---|
| Make and model | **OBDLink LX Bluetooth** |
| Vendor | OBD Solutions LLC |
| Interface and connection | Bluetooth (classic), driven from an Android host |
| Product ID as the device reports it | **`ELM327 v1.4b`** |
| Firmware ID as the device reports it | **`STN1155 v5.6.19`** |
| Hardware revision as the device reports it | **`OBDLink LX BT r1.2`** |
| Serial number | **withheld — see §5** |
| Protocol negotiated | `ISO 15765-4 CAN (11 bit ID, 500 Kbaud)` — protocol 6 |
| Adapter error count after the run | **0** |
| Driving software | Android **OBDLink app 7.4.0.115** |
| Screenshots | [`adapter-info/`](validation/phase-8-smoke-test/adapter-info/) |
| **`AT I` raw output** | **not recorded** — see §4 |
| **`AT @1` raw output** | **not recorded** — see §4 |
| **`AT DP` / `AT DPN` raw output** | **not recorded** — see §4 |

This device reports an ELM327 compatibility string **and** an STN firmware ID at the same
time. Per [0007 §4.3](decisions/0007-phase-8-hardware-validation.md) both are recorded as
reported and neither "genuine" nor "clone" is applied: it is an STN-based adapter that
implements the ELM327 command set. Any finding from it is an interoperability finding
naming *this* adapter, never a general statement about ELM327 devices.

### 2.4 USB ELM327 — required, absent

| Field | Value |
|---|---|
| Make and model | **not recorded — no USB adapter is present** |
| Everything else | **not recorded** |

This row is the one that gates V1.0. It is unfilled.

### 2.5 Run provenance

The remaining [§4.4](decisions/0007-phase-8-hardware-validation.md) fields. There has been
one run; a Phase 8b acceptance run will add its own row rather than replace this one.

| Run | Date tested | Simulator commit | What it was |
|---|---|---|---|
| Preliminary smoke test | **2026-09-23** | **`f607d73`**, working tree clean | investigation, not acceptance — [report](validation/phase-8-smoke-test/README.md) |
| Phase 8b acceptance | **not performed** | — | — |

### 2.6 Wiring

| Item | Value |
|---|---|
| CAN-H | OBD breakout **pin 6** → CANable `CANH` |
| CAN-L | OBD breakout **pin 14** → CANable `CANL` |
| Ground | OBD breakout ground → CANable `GND` |
| Tester power | separate 12 V supply into the OBD breakout |
| CANable 5 V terminal | **nothing connected** — it is an output only |

Pins 6 and 14 are the only OBD connector assignments this project has sourced
(ELM327DSJ, "CAN Input Frequency Matching", p. 62). The ELM327 reference circuit takes
power from OBD pins 16 and 5, or pin 4 where a vehicle has no pin 5 (ELM327DSJ p. 77).
**Take the supply arrangement for any other dongle from that device's own documentation.**
The pin numbers throughout ELM327DSJ are the integrated circuit's, not the OBD connector's,
and confusing the two produces confident, wrong wiring.

Termination must be two 120 Ω across the differential pair **in total**. An adapter and a
dongle that each carry a built-in resistor are already correct, and adding a third is a
fault. `setup_can.sh` will not touch termination unless `CAN_TERMINATION` is set, and skips
with a warning on a controller that does not support it.

## 3. Setting the bench up

```bash
sudo scripts/setup_can.sh can0 500000     # 500000 and 250000 are the OBD bitrates
python -m ecu_simulator --interface can0  # unprivileged
```

`setup_can.sh` arms automatic bus-off recovery at 100 ms by default. **While
troubleshooting, disable it** so a bus-off stays visible instead of the interface quietly
recovering underneath you:

```bash
sudo CAN_RESTART_MS=0 scripts/setup_can.sh can0 500000
```

Capture independently of the simulator's own log:

```bash
candump -e -t a 'can0,0:0,#FFFFFFFF' > capture.log     # all ids, all error frames
```

Record `ip -details -statistics link show can0` **before and after** every run. `ip link
set up` succeeds on an adapter attached to nothing, so configuration alone cannot tell
"the link is up" from "the link is up on a working bus" — the controller state and the
`re-started` / `bus-errors` / `arbit-lost` / `error-warn` / `error-pass` / `bus-off`
counters can.

Check `ECHO` appears in the interface flags and record it. The kernel documentation says
loopback of sent frames is performed "right after a **successful** transmission" and that a
driver signals it handles this itself with `IFF_ECHO`. For such a driver a frame your own
tooling sees looped back was successfully transmitted, which on a real bus means another
node acknowledged it. Without `ECHO` that inference does not hold.

## 4. What is not recorded, and why

| Item | Why |
|---|---|
| `AT I`, `AT @1`, `AT DP`/`AT DPN` raw text | The smoke test was driven from the Android app, which reports these as parsed fields rather than raw responses. No PC-side serial session has been established, so the verbatim strings §4.4 asks for do not exist yet. The Python harness built in Phase 8a Tasks 8–9 will produce them on the first run against the adapter |
| Termination measured as resistance | No multimeter was available. The CANable's onboard 120 Ω is confirmed enabled by jumper position; whether the LX also terminates is **unknown**, so the bus is either ~120 Ω or ~60 Ω. Clean traffic shows the bus *works*; it is not evidence that it is correctly terminated |
| CANable firmware version | Not reported through SocketCAN, and not read out of band |
| USB ELM327, all fields | No such device is present (§2.4) |
| Bluetooth RFCOMM path from the host | The host's classic BR/EDR inquiry returns nothing; see [smoke-test §6](validation/phase-8-smoke-test/README.md). `/dev/rfcomm*` has never been bound, so the Phase 8a harness has not yet run against this adapter |
| 250 kbit/s operation | Not attempted |

## 5. Serial numbers are withheld

The CANable's USB serial and the LX's device serial are withheld here and blacked out in
the screenshots. This repository is public and neither value is needed to reproduce the
bench: [0007 §4.4](decisions/0007-phase-8-hardware-validation.md) asks for make and model,
interface, driver or chipset, and firmware or hardware revision as the device reports it —
not for a unit serial, and nobody rebuilding this bench can use these particular units.
Every field §4.4 does ask for is present and unredacted. Both serials were recorded during
the run and are available to the maintainer from the original screenshots, which are not
committed.

## 6. Troubleshooting

**Not yet written.** It is Phase 8a Task 11 and will cover the six cases in
[0007 §8](decisions/0007-phase-8-hardware-validation.md) plus `listen-only`, which
[0008 §2](decisions/0008-phase-8-question-resolutions.md) moved out of `setup_can.sh` and
into documentation.

## 7. Status of Phase 8b

| Acceptance criterion (0007 §9) | Status |
|---|---|
| 8 — every test in 0007 §6.2 passes against a real ELM327 over physical CAN | **not verified** |
| 9 — Bluetooth acceptance | **not verified**; optional for V1.0 |
| 10 — 250 kbaud | **not attempted** |
| 11 — `candump` confirms the bytes independently | partially: captures exist from the smoke test, but not for the §6.2 test list |
| 12 — conformance rows gain `hardware validated: yes` | **none have** |
| 13 — no row gained `standards validated` | holds; nothing is `standards validated` |

**The 2026-09-23 smoke test is not acceptance.** It was a preliminary investigation, it
used a Bluetooth adapter rather than the required USB one, it exercised the simulator
rather than this project's Python harness, and it ran before Phase 8a had begun. It is
recorded and preserved because it is real evidence about physical CAN — and it is kept
separate from this document for exactly that reason.
