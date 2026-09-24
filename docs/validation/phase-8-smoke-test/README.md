# Preliminary hardware smoke test — physical CAN and Bluetooth ELM327

Performed **2026-09-23** against simulator commit `f607d73`, working tree clean.

**This is a preliminary smoke test, not Phase 8b acceptance.** It was run before Phase 8a
implementation began, using the simulator exactly as it stands. No production code, script,
test or profile was changed, and **no conformance row has moved** — all 61 rows of
[conformance.md](../../conformance.md) still read `hardware validated: no`.

Read [0008 §3](../../decisions/0008-phase-8-question-resolutions.md) before drawing
conclusions from this: **USB ELM327 acceptance is mandatory for V1.0 and Bluetooth is
optional and separately reported.** Everything below is Bluetooth evidence. It does not
satisfy acceptance criterion 8, and it does not close the V1.0 gate.

## 1. The bench, as actually built

| Item | Value | How established |
|---|---|---|
| Host | Ubuntu, kernel `6.8.0-138-generic`, Python 3.12.12 | `uname -r` |
| CAN adapter | **Original CANable**, USB `1d50:606f`, `canable.io` / `canable gs_usb` (unit serial redacted, see §2.1) | `lsusb`, sysfs |
| Driver | `gs_usb`, in-tree; clock 48 MHz | `/sys/class/net/can0/device/driver` |
| Link | bitrate **500000**, sample-point 0.875, `restart-ms 0`, `ECHO` flag set | `ip -details link show` |
| Termination | CANable onboard 120 Ω **enabled** (`Term` jumper toward the screw terminals) | canable.io Getting Started + physical inspection |
| Tester | **OBDLink LX Bluetooth** | see §2 |
| Wiring | OBD breakout pins 6/14 → CANable CANH/CANL; breakout GND → CANable GND; 12 V supply → dongle | operator-confirmed |
| can-utils | 2020.11.0-1 | `dpkg -l` |
| Bluetooth path | LX → Android **OBDLink app 7.4.0.115** (the PC's own Bluetooth was not used) | §6 |

### 1.1 What is *not* established about the bench

- **Termination was never measured.** No multimeter was available. The CANable's onboard
  120 Ω is confirmed enabled by jumper position; whether the LX also terminates is
  **unknown**, so the bus is either ~120 Ω or ~60 Ω. 51,517 frames transmitted with zero
  errors demonstrates the bus *works*; it does not establish that it is correctly
  terminated, and this run must not be cited as evidence that it is.
- The ground bond between the breakout and the CANable was added during this session, on
  the strength of the manufacturer's instruction ("You must connect ground for the CAN bus
  to function properly"). It was not verified by measurement either.

## 2. Tester identity, recorded verbatim

Per [0007 §4.3 and §4.4](../../decisions/0007-phase-8-hardware-validation.md), from the
app's Information screen (`adapter-info/obdlink-lx-information.jpeg`):

| Field | Value |
|---|---|
| Device Name | `OBDLink LX` |
| **Product ID** | **`ELM327 v1.4b`** |
| **Firmware ID** | **`STN1155 v5.6.19`** |
| Hardware ID | `OBDLink LX BT r1.2` |
| Serial Number | *redacted — see §2.1* |
| Vendor | `OBD Solutions LLC` |
| Protocol negotiated | `ISO 15765-4 CAN (11 bit ID, 500 Kbaud)` — protocol 6 |
| Refresh rate | 17.9 PIDs/second |
| **Adapter Error Count** | **0** |

This device reports an **ELM327 compatibility string and an STN firmware ID at the same
time**. It is neither a genuine ELM327 nor a clone, and this record deliberately applies
neither label — §4.3 requires what the device reports to be recorded, not classified.
Any finding from this adapter is an interoperability finding naming *this* adapter.

The adapter's own error counter reading 0 independently corroborates the zero bus-errors
measured at the CANable end: two counters, opposite ends of the bus, in agreement.

### 2.1 Serial numbers are deliberately redacted

The CANable's USB serial and the LX's device serial are **redacted here and blacked out in
the screenshots**, because this repository is public and neither is needed to reproduce the
bench. [0007 §4.4](../../decisions/0007-phase-8-hardware-validation.md) asks for make and
model, interface, driver or chipset, and firmware or hardware revision as the device
reports it — it does not ask for a unit serial, and nobody reproducing this bench can use
these particular units. Everything §4.4 does ask for is present and unredacted.

Both serials were recorded during the run and are available to the maintainer from the
original screenshots, which are not committed.

**Not captured:** raw `AT I`, `AT @1` and `AT DPN` response text. The app reports these
fields as parsed values, and no serial session was established from the PC. §4.4 asks for
the verbatim strings, so this remains an open item for Phase 8b.

## 3. Result

| Measure | Value |
|---|---|
| Frames captured | **103,050** |
| Error frames | **0** |
| RX packets / errors / dropped | 103,050 / **0** / **0** |
| TX packets / errors / dropped | 51,517 / **0** / **0** |
| `re-started` / `bus-errors` / `arbit-lost` | 0 / 0 / 0 |
| `error-warn` / `error-pass` / `bus-off` | 0 / 0 / 0 |
| Controller state | `ERROR-ACTIVE` throughout |

51,517 frames left the CANable and **every one was acknowledged**, which is what a zero TX
error count on a real controller means. That is the strongest single statement this run
supports about the physical layer.

### 3.1 Responses compared against the vcan suite

Every byte below was produced on physical CAN and compared to the value the existing
integration tests assert on `vcan0`.

| Request | Response on physical CAN | vs vcan golden |
|---|---|---|
| `01 00` | `41 00 1E 3F 80 13` | **match** |
| `01 05` | `41 05 82` (90 °C) | **match** |
| `01 0B` | `41 0B 21` (33 kPa) | **match** |
| `01 0C` | `41 0C 0C 80` (800 rpm) | **match** |
| `01 20` | `41 20 00 02 00 01` | **match** |
| `01 2F` | `41 2F 7F` (49.8 %) | **match** |
| `01 40` | `41 40 44 00 80 00` | **match** |
| `09 02` | `49 02 01 "TESTVIN0123456789"` | **match** |
| `03` | `43 02 94 77 00 01` → B1477, P0001 | **match** |
| `01 0D` | `41 0D 00` | no vcan golden; consistent with profile |
| `01 10` | `41 10 01 5E` | no vcan golden |
| `01 1C` | `41 1C 01` | no vcan golden |
| `09 00` | `49 00 40 40 00 00` | no vcan golden |
| `09 0A` | `49 0A 00…00 "ECU_SIMULATOR"` | **DEV-03**, see §5 |
| `01 07` | *no response* | correct — DEV-11 Mode 07 deferred |
| `01 0A` | *no response* | correct — not implemented |
| `06 00`, `08 00` | *no response* | correct — silence on the broadcast address |

**Zero differences between physical CAN and vcan.** Under
[0007 §1.3](../../decisions/0007-phase-8-hardware-validation.md) a difference would have
required its own DEV identifier; none arose.

### 3.2 Behaviours only real hardware could demonstrate

- **ISO-TP flow control generated by a third-party stack.** The VIN response segmented into
  a First Frame and two Consecutive Frames, with the flow-control frame `30 00 …` on
  `0x7E0` **sent by the LX**. Every vcan test generates its own flow control from a Python
  socket; this is the first time the simulator's multi-frame path has been driven by an
  independent ISO-TP implementation.
- **The supported-PID mask honoured by third-party software.** The app read
  `41 00 1E 3F 80 13`, saw the PID-0x01 bit clear, and never requested `01 01` — reporting
  "MIL status: Not Available" instead. The mask was consumed, not merely transmitted.
- **VIN parsed by third-party software.** The app decoded `TESTVIN0123456789` and derived a
  model year from position 10, presenting it as "vehicle: 2032".
- **Protocol auto-search settling on protocol 6** against this simulator, unprompted.
- **Clean lifecycle on physical hardware.** SIGINT produced `received SIGINT, shutting
  down`, all three ISO-TP sockets closed, `transport on can0 stopped`, `shutdown complete`,
  with no traceback and no destroyed tasks — the Phase 2 guarantee, on real wire.

## 4. What this establishes, and what it does not

### ✅ Established on real physical CAN hardware
ISO-TP single-frame request/response in both directions · multi-frame segmentation with
hardware-generated flow control · DLC-8 TX padding · functional addressing on `0x7DF` with
responses on `0x7E8` · silence for unimplemented modes · the nine byte-compared responses
in §3.1 · clean SIGINT shutdown.

### ✅ Established with a real Bluetooth ELM327-compatible tester
Protocol auto-search settling on protocol 6 · sustained polling at 17.9 PIDs/s over roughly
48 minutes · VIN read and parsed · DTC read presented as B1477 and P0001 · adapter error
count 0.

### ❌ Not established — remaining for Phase 8b
- **USB ELM327 acceptance.** No USB device is present. **Acceptance criterion 8 remains
  `not verified`, and it is the mandatory one for V1.0.**
- Raw `AT I` / `AT @1` / `AT DPN` strings (§2).
- UDS services on `0x7E1`: `10`, `11`, `14`, `19/02`, `3E`, and suppress-bit silence.
- Cross-protocol DTC clear (Mode 04 ↔ `19 02`) — deliberately not exercised, as this run
  was required to be non-destructive.
- The scenario profile (`ice_scenario.yaml`) driving values over real wire.
- 250 kbaud (`AT SP 8`).
- Termination confirmed by measurement (§1.1).
- 11 of the 19 items in [0007 §6.2](../../decisions/0007-phase-8-hardware-validation.md).

### 📋 Previously vcan-only, now also seen on hardware
The nine byte-compared responses, multi-frame VIN, TX padding, functional/physical
addressing, clean shutdown. **These conformance rows still read `hardware validated: no`**
and will not change until Phase 8b is performed and recorded under the phase gate.

## 5. Observations that are not new findings

- **DEV-03 reproduced.** `09 0A` returned `49 0A` followed by eight `00` bytes and
  `"ECU_SIMULATOR"` — no item-count byte, NUL-padded on the left. This is the registered
  deviation under strict xfail. Hardware confirms the *behaviour*; it does not unblock the
  *correction*, which needs SAE J1979-DA. A capture of this simulator is not evidence about
  what the standard requires.
- **DEV-11 Mode 07** behaved as designed: request received, no response.
- The app's pre-simulator connection failures were expected — `can0` was down and nothing
  was listening. No reconnection investigation is warranted.

## 6. A limitation of the environment, not of the bench

The development PC's own Bluetooth (Intel 9460/9560, `8087:0aaa`) never discovered the
adapter. Firmware loaded cleanly and BLE scanning worked, but **classic BR/EDR inquiry
returned no devices in any attempt**, including while the LX was confirmed discoverable a
metre away. The test proceeded via the Android app instead, which is why no PC-side serial
session exists and why §2's raw AT strings are missing.

This matters for Phase 8a: the planned `tests/hardware` suite drives the adapter over
`/dev/rfcomm*` from the PC. **On this machine that path is currently blocked.** Options not
yet tried: `hcitool` inquiry as root (it fails silently without privileges, which may
explain every negative result), restarting `bluetooth.service`, or unblocking WiFi — the
9460/9560 is a combo card and `phy0` remains rfkill-blocked. A USB ELM327, which Phase 8b
requires anyway, would bypass the question entirely.

## 7. Files

| File | Contents |
|---|---|
| `candump-full.log.gz` | All 103,050 frames, `candump -e -t a 'can0,0:0,#FFFFFFFF'` — all IDs, all error frames enabled |
| `key-exchanges.txt` | Curated verbatim excerpts: RPM, VIN with flow control, DTC read, the silent modes, the PID chain |
| `simulator-debug.log.gz` | Full simulator DEBUG log, startup through clean shutdown |
| `can0-stats-before.txt` | Interface statistics before any traffic — all counters zero |
| `can0-stats-after.txt` | Mid-run snapshot |
| `can0-stats-final.txt` | Final statistics after shutdown |
| `run-metadata.txt` | Commit, tree state, kernel, can-utils version |
| `ice_default.yaml` | Copy of the exact profile served |
| `adapter-info/obdlink-lx-information.jpeg` | Device Name, Product ID, Firmware ID, Hardware ID, Protocol, error count. Serial blacked out |
| `adapter-info/obdlink-lx-firmware.jpeg` | Firmware version, "up to date". Serial blacked out |
| `adapter-info/dtc-read-b1477-p0001.jpeg` | The DTC read as the app presented it |

Capture command and interface configuration are recorded so the run is reproducible:

```bash
sudo scripts/setup_can.sh can0 500000
candump -e -t a 'can0,0:0,#FFFFFFFF' > candump.log
python -m ecu_simulator --interface can0 --log-level DEBUG
```

## 8. Status

**Preliminary. Not a phase gate, not an acceptance run, not Phase 8b.** Phase 8a has not
started. V1.0 is not tagged. The mandatory USB ELM327 acceptance is untouched.
