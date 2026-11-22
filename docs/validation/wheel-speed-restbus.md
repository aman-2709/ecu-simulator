# Wheel-speed restbus acceptance

Tested the shipped wheel_speed_demo profile on 2026-11-20 with a Chromium browser, a GUI-managed simulator child and an independent Linux CAN_RAW socket. The run used Classical CAN in an isolated vcan0 namespace. A second namespace with a virtual interface named can0 exercised GUI interface selection using the identical profile. Actual physical reception remains pending.

| Check | Observed result |
| --- | --- |
| Standard CAN ID and size | 0x0AA, eight bytes |
| Zero-speed plateau | 1A 6F repeated four times |
| 40 km/h plateau | 2A 0F repeated four times, four decoded wheel values of 40.00 km/h |
| Independent OBD read during the plateau | 01 0D returned 41 0D 28 |
| Publisher timing on vcan0 | Across 100 consecutive kernel receive intervals the median was 20.224 ms, p95 was 20.501 ms and maximum was 21.427 ms |
| Pause and resume | The monitor went stale, the independent capture observed silence, TesterPresent received 7E 00, and resumed frames followed the current scenario |
| Shutdown | The independent receiver observed silence after stop |
| Upstream parity | Independently ran the pinned opendbc CANPacker for integer speeds 0 through 255 km/h. The packaged adapter produced identical bytes for all 256 values. |
| DBC identity | toyota_nodsu_pt_generated.dbc, opendbc revision 79ddcc7bb72d557fc5b20d038c0fad9c9d423446, SHA-256 29ee5c92017b46b8db2fe7566e6f765fe52efcddfb3b426dff929c879d70e066 |

Reproduce the browser and wire checks with `bash scripts/run_integration_tests.sh -k wheel_speed_demo -qs`. The recorded jitter describes this host and run. For physical acceptance use the dedicated terminated bus, the bench’s configured bitrate and a second receiver. Record its observed ID, DLC, data and timestamps.
