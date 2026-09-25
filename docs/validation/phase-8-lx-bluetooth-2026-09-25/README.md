# Phone-driven Bluetooth hardware test — OBDLink LX against the Task 12 cases

Performed **2026-09-25**, 09:30:48 to 09:37:05 PDT, against simulator commit
**`68859fad742f1cb5e9cf9a5b759dd3dc1e696bb1`** (a fresh clone of `origin/modernization`).

**This is a phone-driven Bluetooth bench check. It is not an execution of
`tests/hardware`, and it is not Phase 8b acceptance.** The OBDLink LX was driven by the
OBDLink Android app, and the PC's Bluetooth was not used. No `tests/hardware` test ran:
that suite needs a serial device on the PC, and none existed. Under
[0008 §3](../../decisions/0008-phase-8-question-resolutions.md), **USB ELM327 acceptance
is mandatory for V1.0 and remains open**. Bluetooth is optional and reported separately.
No conformance status has moved: no row of [conformance.md](../../conformance.md) reads
`hardware validated: yes`. Its 71 status rows read 48 `no` and 23 `n/a`. An earlier
version of this sentence said "all 61 rows", repeating a figure that had been counted by
grepping for `| no |` in any column. See the
[Phase 8a completion report](../phase-8a-completion.md).

Every figure below is reproduced by [`analyze.py`](analyze.py) from the committed capture
and log:

    python3 docs/validation/phase-8-lx-bluetooth-2026-09-25/analyze.py

## 1. Bench

Same bench as the [2026-09-23 smoke test](../phase-8-smoke-test/README.md) §1, and the
caveats recorded there still apply. Termination in particular has still not been
measured.

| Item | Value |
|---|---|
| CAN adapter | Original CANable (`gs_usb`) on `can0`, **not reconfigured**: already UP at 500000 bit/s, sample-point 0.875, `restart-ms 0` |
| Tester | OBDLink LX Bluetooth, driven by the OBDLink Android app by the operator |
| Simulator | `python -m ecu_simulator --interface can0 --log-level DEBUG`, default profile `ice_default.yaml` |
| Capture | `candump -e -t a 'can0,0:0,#FFFFFFFF'`: all IDs and all error frames |
| Host | kernel `6.8.0-138-generic`, Python 3.12.12 |

**Pre-flight.** Before the simulator started, the bus was silent for 3 s, and the
project's own foreign-responder probe (`_foreign_responder_present`, one `01 00` on
`0x7DF`) found nothing answering. The simulator was stopped with SIGINT at 09:37:03.450
and logged `shutdown complete` 26 ms later.

## 2. Results

| Measure | Value |
|---|---|
| Capture duration | 376.6 s |
| Frames captured | 12,093: `0x7DF` 6,050, `0x7E8` 6,041, `0x7E0` 2 |
| **Error frames** | **0** |
| Requests / answered | 6,050 / 6,036 |
| Unanswered | 14: 6 while running (all by design, §4), 8 after SIGINT |
| Reply latency, request to first reply frame | median **0.51 ms**, p99 **0.67 ms**, max **9.61 ms** (n = 6,036) |
| Multi-frame answers | `09 02` (VIN) and `09 0A` (ECU name). The LX sent flow control `30 00 00` on `0x7E0` for both |

**Bus statistics** ([can0-stats.txt](can0-stats.txt)): `ERROR-ACTIVE` before and after,
and every error counter, `bus-errors`, `error-warn`, `error-pass`, `bus-off`,
`re-started` and RX/TX `errors`/`dropped`, stayed at 0. TX rose by 6,042, which is the
6,041 captured replies plus the one pre-flight probe frame sent before the capture
started. RX rose by 12,095. On `gs_usb` the adapter echoes each transmitted frame into the
RX count. The captured 12,093 plus the echoed probe account for 12,094. The remaining
frame falls outside the capture and cannot be identified from it. It is most likely one
more of the tester's `01 00` retries, which were arriving every ~0.34 s after shutdown,
landing between the capture stopping and the counters being read.

## 3. Case-by-case comparison with Task 12

The 16 cases are in `tests/hardware/acceptance_cases.py`. Those cases assert on what the
ELM327 returns to its host. This run can only see the CAN side, plus what the app
displayed.

| # | Case | Verdict | Evidence |
|---|---|---|---|
| 1 | supported-pid-chain | **Confirmed byte-for-byte** | `01 00` → `41 00 1E 3F 80 13` ×9, `01 20` → `41 20 00 02 00 01`, `01 40` → `41 40 44 00 80 00` |
| 2 | individual-parameters | **Confirmed byte-for-byte** | All five: `410C0C80` ×80, `410D00` ×2,935, `410582` ×5, `412F7F` ×1, `410B21` ×64 |
| 3 | vin-multi-frame | **Confirmed byte-for-byte** | `49 02 01 TESTVIN0123456789`, multi-frame, with the LX's own flow control |
| 4 | trouble-codes | **Confirmed byte-for-byte** | `03` → `43 02 94 77 00 01`. The app displayed **B1477** and **P0001** ([screenshot 1](screenshots/01-dtcs-before-clear.jpg)) |
| 5 | clear-over-obd | **Confirmed byte-for-byte** | `03` → `43 02 94 77 00 01`, `04` → `44`, `03` → `43 00`. The app then showed no codes ([screenshot 2](screenshots/02-dtcs-after-clear.jpg)) |
| 6 | protocol-6-selected | Consistent with the capture | Every request is 11-bit ISO-TP at 500 kbit/s on `0x7DF`, which is protocol 6. The `AT DP` answer never reaches CAN |
| 7 | auto-search-finds-protocol-6 | Consistent with the capture | The adapter ended up on protocol 6. Whether it got there by automatic search or by the app's setting is AT-level, not visible on CAN, and was not recorded |
| 8 | identifies-itself | Not exercised (not observable) | `AT I` stays between phone and adapter |
| 9 | supply-voltage | Not exercised (not observable) | `AT RV` stays between phone and adapter |
| 10 | multi-parameter-order | Not exercised | The app sent one PID per request |
| 11 | six-parameters-multi-frame | Not exercised | Same reason |
| 12 | uds-physical-services | Not exercised | No frame on `0x7E1`: the app sends no UDS |
| 13 | suppressed-response-is-no-data | Not exercised | Same reason |
| 14 | negative-response | Not exercised | Same reason |
| 15 | unclaimed-service-nrc-11 | Not exercised | Same reason |
| 16 | clear-over-uds-seen-by-obd | Not exercised | Same reason |

**5 confirmed byte-for-byte, 2 consistent with the capture, 9 not exercised.** A
report given in conversation before this record was written said 6, 2 and 8. That was a
miscount, and the table above is the recount against the capture. Cases 8 to 16 need a
tester that can send arbitrary requests, which is what the USB ELM327 run of
`tests/hardware` in Phase 8b provides.

## 4. Other traffic, all expected

- **`01 10` → `41 10 01 5E` ×2,935** (MAF 3.5 g/s): no acceptance case covers it. It
  matches `tests/unit/test_obd_pids.py`.
- **`09 0A` → `49 0A` + seven NULs + `ECU_SIMULATOR`**: matches the frozen bytes in
  `tests/characterization/test_mode09_pid0a_frozen.py`. This is DEV-03, still open and
  evidence-blocked.
- **No reply to `07` ×2, `0A` ×2 and `02 02 00` ×2.** `conformance.md` documents modes
  outside its tables as accepted but silent, and Mode 07 is DEV-11, deferred. The
  simulator logged `OBD mode 0x.. is not implemented; no response` for each.
- **"MIL status: Not Available"** in the app: PID `01 01` is not implemented
  (`conformance.md`, deferred), so it is absent from the `01 00` bitmap and the app never
  requested it.
- **Engine-running warning before the clear:** the app's own guard, triggered by the
  simulated 800 rpm. The screenshot is not included.

## 5. Not established by this run

- Nothing about the `tests/hardware` suite. When the simulator is running it currently
  refuses the bench, reporting "something already answers OBD requests". That defect was
  found the same day and is unfixed.
- The ELM327's own rendering of the responses (spacing, headers, `NO DATA`, `>` prompt).
  Only the CAN bytes and the app's interpretation were observed.
- USB ELM327 behaviour, which is still required for V1.0.

## 6. Files and privacy review

| File | Contents |
|---|---|
| `candump-full.log.gz` | All 12,093 frames |
| `simulator-debug.log.gz` | Simulator DEBUG log, startup to clean shutdown |
| `analyze.py` | Reproduces every figure above from the two files above |
| `can0-stats.txt` | Interface counters before and after, transcribed from `ip -details -statistics` |
| `run-metadata.txt` | Commit, commands, host |
| `screenshots/` | Two of the four app screenshots |

Before commit, the evidence was reviewed for private information. The logs contain no
paths, usernames, hostnames, MAC addresses or serial numbers. The only CAN IDs present are
`7DF`, `7E0` and `7E8`. No device serial number or Bluetooth address appears anywhere in
this directory. All four screenshots were inspected individually. They show no serial
numbers, addresses or personal data, and none carried EXIF metadata; the colour profile
was Android's generic sRGB. The two kept were re-encoded with all metadata stripped. The
two warning dialogs were left out because they add no evidence.
