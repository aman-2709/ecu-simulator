# Known deviations register

Wire-level and runtime behaviors of the current implementation that are believed to be
wrong, missing, or non-deterministic. Each entry is pinned by a characterization test
under `tests/characterization/` so that any change is deliberate. Entries marked
"strict xfail" also carry a test asserting the corrected behavior with
`pytest.mark.xfail(strict=True)`; when the fix lands that test starts passing and the
marker must be removed in the same commit.

"Corrected behavior" is stated only where public documentation is unambiguous. None of
the corrections below constitute a standards-compliance claim. Confidence levels:

- **High**: consistent across public references, real-vehicle captures, and open-source
  tools; only the exact clause text is unavailable.
- **Medium**: plausible from public references; needs the specification to settle.

| ID | Behavior today | Evidence | Corrected behavior | Confidence | Fix phase | Strict xfail | Status |
|---|---|---|---|---|---|---|---|
| DEV-01 | Physically addressed OBD requests on 0x7E0 are bound to a socket that is never read; only 0x7DF is served | `obd/listener.py:11-21` | Requests on the physical ID are answered on the response ID | High | 2 | yes | **Fixed** in Phase 2: the physical OBD endpoint is read; integration test `test_physical_request_is_answered` |
| DEV-02 | Mode 09 PID 02 response is `49 02 00` + VIN; item-count byte is 0x00 | `obd/responses.py:88` | Item count 0x01 | High | 5 | yes | Open |
| DEV-03 | Mode 09 PID 0A response has no item-count byte and NUL-pads on the left | `obd/responses.py:98-102` | `49 0A 01` + 20 bytes; padding layout per specification | High for count byte; content Medium | 5 | yes, count byte and length only | Open |
| DEV-04 | Supported-PID bit 0 ("next range supported") is set for every range 0x00 to 0xC0 and for Mode 09 PID 00, whether or not PIDs exist in the next range | `obd/services.py:107-110` | Bit set only when at least one PID exists in the next range | High | 5 | yes | Open |
| DEV-05 | UDS 0x19 sub-function 0x02 requires a 2-byte request; the 3-byte form carrying `DTCStatusMask` gets NRC 0x13, and the mask is never applied | `uds/services.py:77` | `19 02 <mask>` is accepted and answered positively; mask filtering | High for framing; filtering Medium | 6 | yes, framing only | Open |
| DEV-06 | Unsupported UDS SID on the physical address produces no response | `uds/services.py:48` | NRC 0x11 serviceNotSupported | High | 3 | yes | **Fixed** in Phase 3 by the ECU's unsupported-service policy: a SID no registered protocol claims gets `7F <SID> 11` on a physical address and nothing on the functional one (no negative response is sent to any functionally addressed request); the legacy module is unchanged. Tests `test_unregistered_sid_on_physical_address_gets_nrc_0x11`, `test_unsupported_sids_on_the_physical_address_get_nrc_0x11`, integration `test_unsupported_sid_on_uds_address_gets_nrc_0x11` |
| DEV-07 | `suppressPosRspMsgIndicationBit` (0x80) in sub-functions of 0x10 and 0x11 is not masked; `10 83` returns NRC 0x12 | `uds/services.py:56,66` | Session change performed, no positive response sent | High | 6/7 or 11 (deferred from 3) | yes | Open |
| DEV-08 | ISO-TP TX padding is not enabled; short responses go out with DLC below 8 | `obd/listener.py:24-27` | 8-byte frames with configurable pad byte for OBD | High | 2 | no unit test possible; integration test in Phase 2 | **Fixed** in Phase 2 for OBD endpoints (pad byte 0x00, configurable); UDS unpadded until per-ECU config. Integration test `test_obd_response_frames_are_padded_to_dlc_8` |
| DEV-09 | Reading vehicle speed increments it; two consecutive `01 0D` reads differ | `obd/responses.py:37-46` | Reads have no side effects; value driven by scenario | n/a, design | 7 | yes | Open |
| DEV-10 | Coolant temperature is `random.randrange(130, 150)` | `obd/responses.py:49-50` | Deterministic value from state or scenario | n/a, design | 7 | yes | Open |
| DEV-11 | Modes 04 and 07 are accepted as valid SIDs but produce no response | `obd/services.py:23-37,69` | `44` for clear; `47` + pending DTCs | High | 5, 6 | yes | Open |
| DEV-12 | Mode 01 PID 0C (RPM) and other common PIDs absent | `obd/services.py:25-30` | PID implemented | n/a, feature | 5 | yes for 0C | Open |
| DEV-13 | Malformed DTC strings in config are silently skipped by the encoder | `dtc_utils.py:17,25` | Rejected at configuration load | n/a, design | 4 | no | Open |
| DEV-14 | Invalid config values: bad hex address calls `exit(1)`; out-of-range fuel level or type is silently replaced by a default; a negative fuel level passes validation and raises `OverflowError` at request time | `ecu_config.py:41-46`, `obd/responses.py:56-74` | Rejected at configuration load with a path-qualified error | n/a, design | 4 | no | Open |
| DEV-15 | A Mode 03 request with a trailing byte (`03 00`) echoes that byte into the response: `43 00 02 ...` | `obd/services.py:48,54-59` | Undetermined; malformed request handling per specification | Medium | 5 | no | Open |
| DEV-16 | UDS DTC records use a fixed third byte 0x01 and fixed status 0x2F; 0x19 sub-functions 0x01 and 0x0A unsupported | `dtc_utils.py:9-11,22-28` | Status derived from DTC state | Medium | 6, 11 | no | Open |
| DEV-17 | 0x10 session parameter record `00 1E 0B B8` (P2 = 30 ms, P2* = 30 s) and session changes have no effect | `uds/services.py:9,54-61` | Configurable, realistic defaults; session state | Medium | 11 | no | Open |
| DEV-18 | Mode 01 requests carrying several PIDs (`01 0D 0C`) answer only the first | `obd/listener.py:30-38` | Up to six PIDs per ISO 15765-4 request | High | 5 | no | Open |
| DEV-19 | CAN file logger uses python-can `socketcan_native`, removed in 4.x; thread crashes at start | `loggers/logger_can.py:7,22` | Logger removed in favour of `candump -l` | n/a | 1, 2 | no | **Fixed** in Phase 2: logger removed; use `candump -l` |
| DEV-20 | ISO-TP file logger blocks on four sockets in sequence and cannot log independently | `loggers/logger_isotp.py:19-23` | Removed | n/a | 2 | no | **Fixed** in Phase 2: logger removed |
| DEV-21 | Four non-daemon `while True` threads; SIGINT does not terminate the process | `ecu_simulator.py:37-50` | asyncio runtime with clean shutdown | n/a | 2 | no | **Fixed** in Phase 2: asyncio runtime, SIGINT/SIGTERM exit 0 (integration test `test_signal_shuts_down_cleanly`) |
| DEV-22 | Interface setup through `os.system` with string concatenation, `ifconfig`, `insmod` of an out-of-tree module | `ecu_simulator.py:24-34`, `setup_*.sh` | Standalone privileged scripts; in-tree `CAN_ISOTP` | n/a | 2 | no | **Fixed** in Phase 2: `scripts/setup_vcan.sh` / `setup_can.sh`, no `os.system`, in-tree `CAN_ISOTP` |
| DEV-23 | UDS 0x3E TesterPresent and 0x14 ClearDiagnosticInformation unsupported | `uds/services.py:34-38` | `7E 00` and `54` | High | 6, 7 | yes | Open |

Entries without a strict xfail are pinned by a plain golden test that asserts today's
bytes; changing them still requires editing that test.
