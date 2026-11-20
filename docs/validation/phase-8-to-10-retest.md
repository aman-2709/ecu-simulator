# Phase 8 through 10 retest, November 19, 2026

Each suite ran from its recorded branch revision, in a separate checkout and Python 3.12 environment with the dev and hardware extras. The full default pytest suite ran in a private network namespace with loopback and vcan0 up and real kernel ISO-TP available.

| Phase | Revision | Passed | Expected failures | Kernel integration tests included |
| --- | --- | ---: | ---: | ---: |
| 8a | `982092e` | 1,029 | 3 | 68 |
| 9 | `ad291c6` | 1,148 | 3 | 75 |
| 10 | `5423bce` | 1,202 | 3 | 84 |

No integration tests skipped. The expected failures in all three runs were DEV-26, DEV-11 and DEV-03. Ruff and mypy passed at every revision. Phase 8a exercised the simulated ELM327 harness, Phase 9 exercised both 11-bit and 29-bit multi-ECU routing, and Phase 10 exercised the fault policy on SocketCAN. The Phase 9 CLI also validated and listed both ECUs and all eight endpoints in multi_ecu.yaml. The Phase 10 CLI validated faults_demo.yaml.

## Phase 8b physical attempt

With the GUI ECU stopped and can0 up at 500 kbit/s on the dedicated bench, the Phase 8 revision ran `ECU_SIM_CAN_IFACE=can0 python -m pytest tests/integration -m vcan -x` under an independent candump capture. One test passed. The next test, `test_uds_service_on_the_functional_address_reaches_no_protocol[session]`, failed sending its follow-up probe with errno 70 (ECOMM). The capture included 16 CAN controller error frames with the missing-ACK flag. The controller went from ERROR-ACTIVE to ERROR-PASSIVE, error-warning events increased from 197 to 201, error-passive events from 1,356 to 1,357, and bus-off stayed at zero. Testing stopped at that failure.

The host enumerated the CANable as a SocketCAN adapter and exposed no USB ELM327 serial port. The nineteen-point physical ELM327 suite was not run. The earlier Android/OBDLink LX GUI results remain separate Bluetooth interoperability evidence. Phase 8b is open pending a stable acknowledging bus and the USB ELM327 bench run required by decision 0008. No Phase 12 implementation or release tagging was performed.
