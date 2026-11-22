# UDS request builder examples

The request and response columns are application payloads. Hexadecimal SIDs and data identifiers follow the public service definitions. The bench supplies the RAM map, routines, seed/key fixture, periodic signal assignments and optional transfer-exit checksum. An OEM diagnostic description is needed to assign those fields on a particular vehicle. [UDS services](https://udsoncan.readthedocs.io/en/latest/udsoncan/services.html), [UDS data identifiers](https://udsoncan.readthedocs.io/en/latest/_modules/udsoncan/common/dids.html), [AUTOSAR DCM](https://www.autosar.org/fileadmin/standards/R24-11/CP/AUTOSAR_CP_SWS_DiagnosticCommunicationManager.pdf)

These are selected examples from all 20 menus. The browser acceptance test uses a fresh default ECU, checks the complete response for each exchange, and runs dependent requests in sequence.

| SID | Request | Bench response | Conditions or interpretation |
| --- | --- | --- | --- |
| 10 | `10 03` | `50 03 00 1E 0B B8` | Enter extended session with the default profile's timing record. |
| 11 | `11 03` | `51 03` | Soft reset. |
| 14 | `14 FF FF FF` | `54` | Clear all DTCs. The three-byte selector matters. |
| 19 | `19 02 FF` | `59 02 8C` | After clear, the availability mask remains and there are no matching records. |
| 22 | `22 F1 86` | `62 F1 86 03` | Read the active diagnostic session after entering extended session. |
| 23 | `23 24 00 10 00 00 00 04` | `63 AA BB CC DD` | Read four bytes from bench address 0x100000 after the 3D write below. |
| 27 | `27 01` | `67 01 12 34 56 78` | Level 1 seed in extended session. The next level 1 key card uses the local mock fixture. |
| 28 | `28 00 01` | `68 00` | Enable normal receive and transmit. |
| 2A | `2A 04` | `6A` | Stop all periodic reports on this endpoint. IDs and signals are ECU-specific. |
| 2E | `2E F1 00 01` | `7F 2E 31` | A one-byte manufacturer data record. In extended session the shipped ECU rejects this DID because it has no writable provider. |
| 2F | `2F D1 00 03 01` | `6F D1 00 03 01` | In extended session on uds_records_demo, switch the simulated lamp on. |
| 31 | `31 01 01 00` | `71 01 01 00` | Start bench routine 0100. Its function and result record are local. |
| 34 | `34 00 44 00 00 10 00 00 00 00 04` | `74 10 42` | Open a plain four-byte download at 0x1000. The advertised block length includes SID and counter. |
| 35 | `35 00 44 00 00 10 00 00 00 00 04` | `75 10 42` | After finishing the download, open a four-byte upload from that address. |
| 36 | `36 01 AA BB CC DD` | `76 01` | First download block. During upload, send `36 01` to receive the four data bytes. |
| 37 | `37 55 B4 01 A7` | `77` | Finish the four-byte download with the bench CRC32. Plain `37` also finishes a completed transfer. |
| 3D | `3D 24 00 10 00 00 00 04 AA BB CC DD` | `7D 24 00 10 00 00 00 04` | Write four bytes to bench RAM. The response echoes format, address and size. |
| 3E | `3E 00` | `7E 00` | Tester Present with a positive reply. |
| 83 | `83 04 00 32 01 F4` | `C3 04` | Set the bench timing record. Subfunction 03 reads the active values. |
| 85 | `85 01` | `C5 01` | Enable DTC updates. |

The **writable-DID test fixture** card uses `2E F1 A2 0A`. With the project’s test provider for F1A2 registered in extended session, it returns `6E F1 A2` and `22 F1 A2` reads back `62 F1 A2 0A`. This provider is exercised by the protocol unit tests and is separate from the shipped profiles.

For comparison, ordinary OBD vehicle speed uses `01 0D`. A Classical CAN single frame can carry that request as `02 01 0D` followed by padding. The first byte is ISO-TP framing. The request builder accepts the application bytes, and Live CAN shows the frame. [OBD vehicle-speed example](https://www.csselectronics.com/pages/obd2-pid-table-on-board-diagnostics-j1979), [Linux ISO-TP documentation](https://docs.kernel.org/networking/iso15765-2.html)

## Diagnostic records and output control

Select **uds_records_demo**, choose **vcan0**, start the ECU and select **engine.uds_physical**.

| Request | Expected response | Meaning in this profile |
| --- | --- | --- |
| `19 04 00 01 01 01` | `59 04 00 01 01 0C 01 01 D1 01 03 20` | P0001, pending and confirmed. Snapshot 01 contains one DID, D101, configured as 800 RPM. |
| `19 06 00 01 01 01` | `59 06 00 01 01 0C 01 01` | P0001 extended record 01 contains the fixture byte 01. |
| `10 03` | `50 03 00 1E 0B B8` | Enter extended session. |
| `2F D1 00 03 01` | `6F D1 00 03 01` | Temporarily turn the simulated lamp on. |
| `22 D1 00` | `62 D1 00 01` | Observe the current lamp state. |
| `2F D1 00 00` | `6F D1 00 00` | Release the override. The simulated ECU drives the lamp off. |

The profile defines synthetic captured bytes under each DTC’s `snapshot` and `extended_record`. This implementation supports record 01 and the all-record selector FF. Clearing fault memory removes the captured records. A later scenario assertion stores the configured fixture bytes again. The observation is repeatable and independent of the live RPM signal. A request for a configured record after clear returns the DTC and its current status without a record body. An unknown DTC or unsupported record number receives NRC 31.

D100 and D101 are local DID assignments. The output supports shortTermAdjustment (03) and returnControlToECU (00). ECU reset, a session change and S3 expiry release the override. The diagnostic message layouts follow AUTOSAR DCM’s descriptions of services 19 and 2F. OEM-specific record meanings and the output’s default value come from the bench profile. [AUTOSAR Diagnostic Communication Manager](https://www.autosar.org/fileadmin/standards/R24-11/CP/AUTOSAR_CP_SWS_DiagnosticCommunicationManager.pdf)
