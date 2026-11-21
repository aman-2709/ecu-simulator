# Wheel-speed restbus simulation on SocketCAN

The first operator workflow starts a simulated wheel-speed publisher on vcan0, observes decoded values in Live CAN, and reads the same speed through OBD. The same shipped profile and child executable run on a dedicated physical can0 bench by changing only the existing interface selector. vcan0 is the first acceptance gate. Physical results are recorded separately.

## Profile and runtime

Add an opt-in wheel-speed demo profile using the existing YAML validation and start/stop controls. Use the generated toyota_nodsu_pt database from opendbc revision 79ddcc7bb72d557fc5b20d038c0fad9c9d423446. Resolve and validate the database, declared message and signal mapping before reporting Running. Include the upstream license and report the revision and resolved DBC hash with the run. Preserve existing profiles and startup behavior when no broadcast is configured. Arbitrary DBC import and additional publishers are future extensions.

The child owns one Classical CAN raw-frame publisher on the selected SocketCAN interface. Schedule the standard 11-bit ID 0x0AA every 20 ms using a monotonic clock. This period is a bench choice. Skip missed slots to avoid bursts. Update shared vehicle state at sampling time. Map vehicle speed in km/h to FR, FL, RR and RL, with fault bits zero. Use opendbc packing. Keep UDS and OBD on the existing diagnostic transport. Stop and startup failure close all sockets. Surface interface and transmit errors. Use the operator-configured physical interface bitrate.

Use repeatable speed plateaus of 0, 40 and 0 km/h with enough dwell time to issue an OBD request during the middle plateau (10 seconds each, then repeat). Model straight-line motion with equal wheel speeds. A run-scoped GUI pause/resume control affects only the wheel-speed publisher. The scenario clock and diagnostics continue. Resume with the current sample. Reject stale run IDs.

## Live CAN

Use the active profile’s DBC and decode frames captured on the selected interface. Show the four labeled wheel speeds and units alongside the raw bytes. Show measured interarrival period and age since last reception. Use capture timestamps for timing, update age while silent, and mark an observed message stale after five missed periods (100 ms). Before the first frame show waiting. Distinguish malformed frames and retain ordinary raw display for unrelated IDs. Exclude matching extended-ID or remote frames from the standard data-frame decoder. Clear run-specific decoder and timing state when changing runs or interfaces.

## Acceptance

1. In an isolated network namespace, start the profile from the GUI on vcan0 and independently capture CAN frames. Verify ID, DLC and payload on each plateau. For this DBC each wheel occupies a big-endian word with a high fault bit and a 15-bit speed value. With no faults, zero km/h encodes as 1A 6F repeated four times, and 40 km/h as 2A 0F repeated four times. Derive these fixtures from the DBC factor 0.01 and offset -67.67, independently of the production packer. Add an asymmetric decoder fixture to catch swapped wheel labels.
2. Verify the GUI shows the captured bytes and four physical values. On the stable 40 km/h plateau send 01 0D to the physical OBD endpoint and verify 41 0D 28. On an unloaded vcan0 collect 100 consecutive interarrival intervals and require a median between 18 and 22 ms. Report p95 and maximum separately to expose scheduling jitter. These are simulator acceptance limits.
3. Pause publication, verify raw silence for 0x0AA and a stale indication, issue a diagnostic request, resume and verify fresh data. Stop the ECU and verify publication ends. Re-run the existing diagnostic and GUI tests.
4. Repeat on the dedicated, terminated can0 bench with a second active CAN node to acknowledge frames. Verify the fixture payloads with a separate receiving adapter where available. Record interface bitrate, adapter details, received frames and observed timing. A local transmit echo alone cannot establish external reception.

[Upstream wheel-speed definition](https://github.com/commaai/opendbc/blob/79ddcc7bb72d557fc5b20d038c0fad9c9d423446/opendbc/dbc/generator/toyota/_toyota_2017.dbc)
