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

Bitrate is a privileged link property and is set here, never by the simulator. **There is
no `ecu-simulator --bitrate` and there will not be**: the simulator runs unprivileged and
`setup_can.sh` exists to keep that separation
([0008 §2](decisions/0008-phase-8-question-resolutions.md), Q3). Any bitrate other than
500000 or 250000 is accepted with a warning, which is correct for non-OBD use.

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

The six cases in [0007 §8](decisions/0007-phase-8-hardware-validation.md), plus
`listen-only`, which [0008 §2](decisions/0008-phase-8-question-resolutions.md) moved out of
`setup_can.sh` and into documentation. Where a case was actually met while building this
bench, that is said rather than left as theory.

### 6.0 Start here: the three faces of "it isn't answering"

These look identical from the driver's seat and have different causes. Telling them apart
first saves most of the hunt.

| What you see | Where it comes from | What it usually means |
|---|---|---|
| **Silence** — nothing at all, the tester waits out its own timeout | the simulator chose not to respond, **or** nothing reached the bus | a suppressed positive response is *supposed* to look like this (`3E 80`, DEV-07). Otherwise: wiring, ground, termination, or the adapter never transmitted |
| **`NO DATA`** | the **ELM327**, after its `AT ST` timer expired with no reply | the request went out and nothing answered. The bus works well enough to transmit; the far end did not reply, or the simulator is not running |
| **`CAN ERROR`** | the **ELM327** (ELM327DSJ p. 87) | "difficulty initializing, sending, or receiving" — including a baud rate that does not match the actual data rate, and wiring faults |

The distinction matters because only the first can be a *correct* result. `NO DATA` and
`CAN ERROR` are always the device telling you something, and
[0008 §6](decisions/0008-phase-8-question-resolutions.md) records why naming only silence
would misdirect an operator two times in three.

**First diagnostic, every time — the interface counters:**

```bash
ip -details -statistics link show can0
```

```
can state ERROR-ACTIVE restart-ms 100
  bitrate 500000 sample-point 0.875
  re-started bus-errors arbit-lost error-warn error-pass bus-off
  0          0          0          0          0          0
RX: bytes packets errors dropped  missed   mcast
TX: bytes packets errors dropped carrier collsns
```

Read it in this order:

1. **Controller state.** `ERROR-ACTIVE` is healthy. `ERROR-WARNING` / `ERROR-PASSIVE` /
   `BUS-OFF` mean the controller is unhappy about the physical bus — go to 6.1 and 6.2.
2. **`TX errors` and `TX dropped`.** Non-zero means frames did not get out. **Zero TX
   errors with a non-zero TX packet count is the strongest single thing this bench can
   tell you**: every frame was acknowledged, so wiring, ground, termination and bitrate all
   carry traffic. The 2026-09-23 run transmitted 51,517 frames with zero TX errors.
3. **`bus-errors`, `error-warn`, `error-pass`, `bus-off`.** Any of these climbing during a
   run points at the physical layer, not at protocol logic.
4. **`re-started`.** Non-zero means automatic recovery fired — a bus-off happened and was
   papered over. See 6.2.

Take it **before and after** every run. `ip link set up` succeeds on an adapter attached to
nothing, so configuration alone cannot distinguish "the link is up" from "the link is up on
a working bus".

### 6.1 No frames at all

Nothing on `candump`, nothing at the tester, TX errors climbing or the controller leaving
`ERROR-ACTIVE`.

```bash
candump -e -t a 'can0,0:0,#FFFFFFFF'      # all ids, all error frames
ip -details -statistics link show can0    # before and after
```

Check, in this order:

1. **Ground.** The commonest fault, and the one this bench actually had. The CANable
   documentation is explicit: *"Connect the CANH, CANL, and GND pins of your CANable to
   your target CAN bus. You must connect ground for the CAN bus to function properly."* A
   tester on its own 12 V supply and an adapter grounded through USB have **no shared
   reference** unless a wire provides one. The failure looks exactly like a termination or
   bitrate fault, which is why it is worth eliminating first — it costs one wire.
2. **Termination.** Two 120 Ω across the differential pair, total. With everything powered
   off and the adapter unplugged, CAN-H to CAN-L should read ~60 Ω for two, ~120 Ω for one,
   open for none. The manufacturer states a completely unterminated bus "will not function
   at all". On this bench the CANable's onboard resistor is enabled by jumper (§2.2) and
   whether the LX also terminates is unknown, so the bus is ~120 Ω or ~60 Ω — **unmeasured**.
3. **CAN-H / CAN-L not swapped**, and on the right pins: 6 and 14 (§2.6).
4. **Bitrate agreement** on both ends — 6.3.
5. **Two nodes.** A lone transmitter gets no acknowledgement and will error out. The tester
   must be powered, not merely wired.

### 6.2 The interface goes down and stays down

Symptom: the simulator "stopped responding", when in fact the controller reached bus-off
and the link is no longer passing traffic.

```bash
ip -details -statistics link show can0 | grep -E "state|bus-off|re-started"
sudo ip link set can0 type can restart          # recover by hand
```

`setup_can.sh` arms automatic recovery at 100 ms by default, so this normally self-heals —
and that is exactly the problem while diagnosing, because a fault that recovers silently
is a fault you cannot see. Turn it off for troubleshooting:

```bash
sudo CAN_RESTART_MS=0 scripts/setup_can.sh can0 500000
```

`CAN_RESTART_MS=0` **explicitly disables** recovery rather than leaving whatever a previous
run armed; `restart-ms` is a persistent link property and nothing else in the script clears
it. Watch `re-started` afterwards: a non-zero count on a bench that "seems fine" means it
is not.

### 6.3 A silent ELM327

Nothing comes back at all — no response, no `NO DATA`, no error.

From ELM327DSJ, "CAN Input Frequency Matching" (p. 62): from firmware 2.1 the device
measures the bus frequency and will not transmit unless it matches the selected protocol.
Read in full, that check is **narrower than it first appears**, and the narrowing matters:

- it applies **only while searching** for a protocol — *"Once a particular protocol is
  considered to be active, no further frequency checks are made"*. A bench that selects
  protocol 6 with `AT SP 6` is not in that path;
- **a quiet bus passes it** — a send is allowed *"if the input signal frequency matches the
  CAN setting (250 or 500 kbps), or if there appears to be no signal"*.

So silence is one presentation of a bitrate mismatch, not the only one — see 6.0. Check:

```bash
ip -details link show can0 | grep bitrate       # what the adapter is set to
```

then confirm the tester's protocol (6.4). `setup_can.sh` warns when the bitrate is neither
500000 nor 250000, which is the cheap half of this trade.

`AT BI` bypasses the initiation sequence, and the datasheet notes *"this frequency matching
test will also bypassed"*. **It is a diagnostic, not a step.** If the bench needs `AT BI`
to talk, the bench is misconfigured and `AT BI` is hiding it.

**If the tester is Bluetooth, eliminate the link first**, before suspecting CAN at all. On
this bench that was the whole problem for a while: an OBDLink LX is **not discoverable by
default** — it advertises only for about two minutes after its `Connect` button is pressed
(ScanTool's own quick-start guide), so scans find nothing and the adapter looks dead. Its
`BT` LED fast-blinks while discoverable, is solid when connected, and the `POWER` LED
flashing every ~3 s means BatterySaver sleep. A lit power LED proves the rail, not the
radio.

### 6.4 The ELM327 reports the wrong protocol

```
AT DPN        → the protocol number, prefixed 'A' if it was reached by automatic search
AT DP         → the same thing in words
AT SP 6       → set ISO 15765-4 CAN, 11-bit, 500 kbaud, and save it
```

Protocol **6** is this bench's protocol; it matches the shipped profile's `0x7DF` / `0x7E0`
/ `0x7E8` and the 500000 default. Protocol 8 is the 250 kbaud variant; 7 and 9 are their
29-bit counterparts and are Phase 9 work.

**Do not start with `AT SP 0`.** An automatic search transmits requests in other protocols
before it reaches CAN, which makes a failure much harder to read. Set the protocol
explicitly, get a deterministic pass, and only then test the search as its own case
(0007 §6.2 item 4). For the record, on this bench the app's automatic search did settle on
protocol 6 unaided once the simulator was running.

### 6.5 A device that says ELM327 but behaves differently

Adapters commonly report an ELM327 version string while implementing something else
underneath — sometimes a subset, sometimes a superset.

```
AT I          → the product identification string
AT @1         → the device description
AT DP / DPN   → the protocol as the device understands it
```

Record all of them verbatim, plus how the device was obtained, and put them in §2.3 of this
document. The rule from [0007 §4.3](decisions/0007-phase-8-hardware-validation.md): a test
that fails on one adapter is an **interoperability finding naming that adapter**, never a
defect of this simulator and never quietly dropped.

This bench is a live example rather than a hypothetical. The OBDLink LX reports Product ID
`ELM327 v1.4b` **and** Firmware ID `STN1155 v5.6.19` at the same time — an STN-based device
implementing the ELM327 command set. It is neither "genuine" nor a "clone", and this
project applies neither label; it records what the device says.

### 6.6 The simulator's own guard messages

These already name their cause and their fix, so read them literally before investigating
further. All exit with status **2**:

| Message | Meaning |
|---|---|
| ``CAN interface 'can0' does not exist. Create it with scripts/setup_vcan.sh (virtual) or scripts/setup_can.sh (hardware), or pass --interface.`` | the adapter is unplugged, the driver did not bind, or the name is wrong |
| ``CAN interface 'can0' exists but is down. Bring it up with: sudo ip link set up can0`` | `setup_can.sh` was not run, or the link was taken down afterwards |
| a kernel without `CAN_ISOTP` | the module is missing; the simulator cannot open an ISO-TP socket |
| an invalid profile | reported by `validate-config` without opening any socket |

`ecu-simulator validate-config --profile <path>` checks a profile with no bus involved at
all, which separates configuration faults from bench faults in one step.

### 6.7 Watching a bus without joining it — `listen-only`

A node in listen-only mode receives but **does not transmit**, and does not even
acknowledge. Useful for watching an established bus without becoming a participant:

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000 listen-only on
sudo ip link set can0 up
candump -e -t a 'can0,0:0,#FFFFFFFF'
```

Restore it before running the simulator:

```bash
sudo ip link set can0 down
sudo ip link set can0 type can listen-only off
sudo scripts/setup_can.sh can0 500000
```

**This is why `setup_can.sh` does not offer it.** An interface left in listen-only is
itself a cause of 6.1 and 6.3: the simulator appears to start normally, transmits nothing,
and the tester sees silence. [0008 §2](decisions/0008-phase-8-question-resolutions.md)
keeps it in documentation for exactly that reason. If a bench is silent and everything else
checks out, confirm `LISTEN-ONLY` is *absent* from the controller mode flags:

```bash
ip -details link show can0 | grep -o "<[A-Z,-]*>"
```

Note also that a listen-only node cannot acknowledge, so it does not count as the second
node a transmitter needs (6.1, item 5).

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
