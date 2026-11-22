# ECU Simulator

## Project status

This repository is a maintained fork of [lbenthins/ecu-simulator](https://github.com/lbenthins/ecu-simulator). The original upstream project is archived and no longer maintained.

The fork is being actively modernized into a testbench-grade vehicle diagnostic simulator (modern Linux SocketCAN and in-tree ISO-TP, Python 3.12+, packaging, CI, deterministic vehicle state and scenarios, multi-ECU support). Development currently happens on the `modernization` branch.

The project is **not yet production-ready**. Known protocol deviations of the current implementation still exist and are tracked; the sections below describe the legacy behavior until the corresponding phase replaces it.

* Roadmap, architecture and release plan: [docs/modernization-plan.md](docs/modernization-plan.md)
* What is implemented and how far it is verified: [docs/conformance.md](docs/conformance.md)
* Known protocol and implementation deviations: [docs/known-deviations.md](docs/known-deviations.md)

Nothing in this project is standards validated. The applicable SAE and ISO documents are
licensed and have not been reviewed against this implementation.

This Python tool simulates some vehicle diagnostic services. It can be used to test OBD-II dongles or tester tools that support the UDS (ISO 14229) and ISO-TP (ISO 15765-2) protocols. 

This tool does NOT implement the ISO-TP protocol. It just simulates a couple of OBD and UDS services. The simulation consists in receiving a diagnostic request (e.g., Request DTCs (0x03)), and responding to it according to the protocol specifications. The data of some responses (e.g., VIN) must be defined in the `ecu_config.json` file.

I created this project to learn more about the OBD and UDS protocols. I did my best to understand the specifications, however, if you suspect that something is implemented wrongly, please let me know. Any feedback will be very appreciated. 

## Supported Services

### OBD-II

| Service | PID    |          Description                   |
|:-------:|:-----: |:---------------------------------------|
| 0x01    | 0x00, 0x20, 0x40 | Supported parameters, advertised only where populated |
| 0x01    | 0x04   | Calculated engine load |
| 0x01    | 0x05   | Engine coolant temperature |
| 0x01    | 0x06, 0x07 | Short and long term fuel trim, bank 1 |
| 0x01    | 0x0B   | Intake manifold absolute pressure |
| 0x01    | 0x0C   | Engine speed |
| 0x01    | 0x0D   | Vehicle speed |
| 0x01    | 0x0E   | Timing advance |
| 0x01    | 0x0F   | Intake air temperature |
| 0x01    | 0x10   | Mass air flow rate |
| 0x01    | 0x11   | Throttle position |
| 0x01    | 0x1C   | OBD standards conformed to |
| 0x01    | 0x1F   | Run time since engine start |
| 0x01    | 0x2F   | Fuel tank level input |
| 0x01    | 0x42   | Control module voltage |
| 0x01    | 0x46   | Ambient air temperature |
| 0x01    | 0x51   | Fuel type |
| 0x01    | several | Up to six parameters in one request, answered in one response |
| 0x03    | -      | Request stored DTCs (the confirmed codes in the ECU's DTC store) |
| 0x04    | -      | Clear DTCs; the same clear as UDS 0x14 |
| 0x09    | 0x00   | Supported parameters in service 0x09 |
| 0x09    | 0x02   | Vehicle Identification Number (VIN) |
| 0x09    | 0x0A   | ECU name |

Values come from the profile's vehicle section as physical quantities, and the encoders
turn them into wire bytes. A read never changes them, so two identical requests give
identical answers; time-varying behavior arrives with the scenario engine. Which
parameters are supported follows from which signals the configured vehicle has, so a
battery-electric profile advertises no engine parameters.

See [docs/conformance.md](docs/conformance.md) for how far each one is verified and what
evidence its encoding rests on. Nothing is standards validated.

### UDS (ISO 14229)

| Service ID |          Name            | Supported sub-functions | Default parameters (response) |
|:----------:|:-------------------------|:------------------------|:-----------------|
| 0x10       | DiagnosticSessionControl | **session types** <br> <br> 0x01 default <br> 0x02 programming <br> 0x03 extended <br> 0x04 safety <br> (configurable per ECU) | P2 / P2* from the profile, default `00 1E 0B B8`. The ECU starts in 0x01 and returns to it after S3 (5000 ms, configurable) without a UDS request |
| 0x11       | ECUReset                 | **reset types** <br> <br> 0x01 hardReset <br> 0x02 keyOffOnReset <br> 0x03 softReset <br> 0x04 enableRapidPowerShutDown <br> 0x05 disableRapidPowerShutDown | 0x0F powerDownTime. 0x01-0x03 return the ECU to the default session |
| 0x14       | ClearDiagnosticInformation | groupOfDTC `FFFFFF` only; any other group gets NRC 0x31 | |
| 0x19       | ReadDTCInformation       | **report types** <br> <br> 0x01 reportNumberOfDTCByStatusMask <br> 0x02 reportDTCByStatusMask, mask required <br> 0x0A reportSupportedDTCs | <br> 0x8C DTCStatusAvailabilityMask <br> statusOfDTC derived from the store: 0x04 pending, 0x08 confirmed, 0x80 indicator requested |
| 0x22       | ReadDataByIdentifier     | one or more DIDs per request | F186 active session, F190 VIN, plus the profile's `dids:`. Unserved DIDs are omitted; NRC 0x31 only when none is served |
| 0x3E       | TesterPresent            | 0x00 zeroSubFunction; anything else gets NRC 0x12 | `7E 00`. Restarts S3, as every UDS request does. No ISO 14229 session-compliance claim |

The following services use per-ECU bench state. Each simulator launch starts fresh.

| SID | Simulated behavior |
| --- | --- |
| `23`, `3D` | Read and write volatile RAM. Three independent 4 KiB regions start at `0x1000`, `0x100000`, and `0x200000`. An access must fit within one region. |
| `34`, `35`, `36`, `37` | Download to RAM or upload a snapshot. Format `00` selects plain data, up to 64 data bytes per block. Counters begin at one. Retrying the last identical block returns the previous reply. A download becomes visible on successful transfer exit. The optional four-byte exit record is a big-endian CRC32. |
| `28`, `2A` | Subscribe to periodic RPM (01), speed (02), or coolant (03) when the vehicle has that signal. The three rates are 1, 4 and 10 Hz. Reports carry `6A`, the periodic ID and the encoded value on the requesting endpoint's response route. Communication control stores RX/TX modes per traffic group. Disabling normal TX pauses these simulated reports. Diagnostic replies continue. |
| `31` | Start, stop and query the example routine IDs. The result byte is 01 after start and 00 after stop. Actuator routines only change simulator state. Starting FF00 fills the simulated region at 0x1000 with FF. |
| `83` | Read the configured or active timing record, set a four-byte timing record, or restore the profile default. This changes the timing values reported for the active session. |
| `85` | Enable or suspend updates to the ECU's DTC flags. Reads still show stored faults and an explicit clear remains available. |

A change of diagnostic session, S3 expiry, or simulated restart clears subscriptions, communication modes,
routine status, timing overrides and unfinished transfers, and enables DTC updates. Committed RAM
contents survive a simulated ECU reset within the same process. Unsupported formats, ranges,
subfunctions or request sequences produce negative responses. These bench behaviors are simulator
models. Only physical exchanges explicitly listed in the validation reports have hardware evidence.

Every service above that has a sub-function honours the `suppressPosRspMsgIndicationBit`,
bit 7 of the sub-function byte. It is handled once at the dispatch layer, not inside any
handler: the bit is masked off before the service sees the sub-function, the service runs
normally, and only a *positive* response is withheld. `3E 80`, `10 83` and `19 82 FF` send
nothing; `3E 81`, `10 85` and `19 81` still get their negative response, because a tester
that suppressed the answer asked for silence on success, not for its errors to be hidden.
Services without a sub-function, such as 0x14, are unaffected.


## Requirements

* Linux with SocketCAN and the in-tree ISO-TP kernel module (`CONFIG_CAN_ISOTP`, Linux 5.10 or newer; Ubuntu 24.04 ships it and loads `can_isotp` on demand). GitHub-hosted Azure kernels do not build it.
* Python 3.12 or newer.
* [can-isotp](https://pypi.org/project/can-isotp/) 2.x, installed automatically; the only runtime dependency. It drives the kernel `CAN_ISOTP` socket, see [docs/decisions/0001-isotp-binding.md](docs/decisions/0001-isotp-binding.md).
* `iproute2` (`ip`) for the interface setup scripts. [can-utils](https://github.com/linux-can/can-utils) (`candump`, `isotpsend`, `isotprecv`) is optional but useful.

## Installation

```
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -e .            # runtime
python -m pip install -e ".[dev]"     # plus pytest, ruff, mypy
```

With `uv`: `uv venv --python 3.12 .venv && uv pip install -e ".[dev]"`. A venv created by `uv` has no `pip` of its own, so a bare `pip` there resolves to whatever is on your PATH (possibly another Python); use `uv pip`, or bootstrap it with `python -m ensurepip --upgrade`.

## Browser controls

Install the GUI extra and launch the local manager with your project environment active.

```sh
python -m pip install -e ".[gui]"
ecu-simulator-gui
```

Open the printed loopback address (default port 8766). Select a shipped profile, edit the YAML working copy, select an up CAN interface, and click **Start ECU**. The running interface and run ID appear when the child has opened its sockets. **Save as** keeps a named copy for the next launch. A validation or startup error leaves the draft in the editor.

The request builder has examples for 20 UDS service IDs. The simulated ECU handles all 20 on a UDS-enabled endpoint. Session, request data, profile, and prior exchanges determine whether a particular example receives a positive response. Selecting an example previews its bytes. Only **Send request** transmits it.

For request bytes, prerequisites, expected bench responses and protocol sources for each SID, see the [UDS request builder examples](docs/examples/uds-request-builder.md).

With `ice_default`, choose **engine.uds_physical**, select **0x19 · Read DTC information**, then choose **Read DTCs**. Edit the data bytes or use the example as written. Check the complete request (`19 02 FF`) in **Request preview** and press **Send request**. Select **Raw request (full payload)** to enter the SID yourself. **Terminal output** keeps requests made in this tab for the current run, newest first. Expand a request to inspect its complete response bytes and decoded trouble codes. Reloading the page clears the history. **Stop ECU** releases the sockets. The same controls use `vcan0` or a configured physical bench interface.

**Live CAN** captures Classical CAN frames on the selected interface even while the ECU is stopped.
Choose an interface and click **Start capture**. **Trace** retains the latest 512 frames in arrival
order with UTC timestamps. **By CAN ID** retains the 512 most recently seen IDs, counts frames
while each ID is retained, and highlights bytes that changed from its previous frame.
Enter an exact hexadecimal ID to filter either view. **Pause display** freezes the view while
capture continues. You can switch back to **Simulator** to send a UDS request during capture.
**Stop capture** releases the socket and leaves the last results visible until the next capture.

### Replay a recorded drive

Open **Live CAN → Replay a CAN recording**, choose the default **2015 Kenworth T660** driving excerpt and click **Play on vcan0**. Capture starts before the first frame. The Kenworth sample opens **By PGN and source**, with one row per J1939 parameter group and sender, its measured interarrival time, and the latest payload. Search for `rpm`, `EEC1`, `61444`, or `0CF00400` to follow engine speed (SPN 190). **Trace** retains chronological frame order. Select **Interpret extended CAN IDs as J1939** to use this view with a local recording or live capture. **Repeat recording** repeats a pass until you click **Stop replay**. No ECU profile is required. Other senders on the same bus also appear in the capture.

EEC2 shows accelerator pedal position (SPN 91, byte 2) and percent engine load at current speed (SPN 92, byte 3). Hover over the value to see the byte mapping. Request messages show the requested PGN. The monitor distinguishes unavailable readings from zero and identifies groups without a signal decoder. **Last interval** measures the elapsed time between observations in that group. **Replay loop** marks an interval spanning the start of another pass on vcan0.

The T660 excerpt uses the 12 seconds starting at timestamp 1604007304 in Colorado State University’s 2015 Kenworth T660 candump drive archive. It opens with **Vehicle readings only** selected. Clear that filter to inspect every captured group.

The alternate T270 excerpt contains 7,098 frames and 104 identifiers from Colorado State University's [Heavy Vehicle CAN Data](https://systemscyber.engr.colostate.edu/research/HeavyVehicleCANData). It uses the interval from 1594702604 inclusive to 1594702616 exclusive in `CSU090AT.txt` from the 2014 Kenworth T270 Short archive. The original frames and timestamps are preserved.

Choose **Local candump log** for your own recording. Use timestamped single-interface Classical CAN data in compact `candump -L` format or absolute-timestamp `[DLC]` byte-column format. Timestamps must be chronological. The GUI accepts files smaller than 16 MiB with up to 300,000 frames. Source interface names are remapped to virtual `vcan0`. Replay uses recorded time offsets at 1× speed with host scheduling precision. A matching DBC under **DBC decoder** enables signal decoding. The trace also displays unknown identifiers and raw payloads.

### BMW demo drive

Select **bmw_e9x_demo_drive** and **vcan0**, click **Start ECU**, then start capture on **Live CAN**. The bundled BMW E9x/E8x database decodes all four wheels (ID `0CE`) and battery voltage (ID `3B4`). This synthetic drive cycles through 0, 20, 40 and 20 km/h in three-second steps with a constant battery reading of 14. Eight messages repeat every 100 ms. The parking brake releases as the car starts moving, and automatic wiper mode switches on and off. Steering stays centered, indicators and cruise control stay idle, and the ignition stays on. The engine also follows an 800–2000 rpm scenario accessible through OBD request `01 0C`. Use **By CAN ID** to watch each broadcast update in place.

### Import a Classical CAN DBC

In **Simulator → DBC messages**, choose a standalone `.dbc` file or a generated opendbc database. The importer checks message and signal layouts, identifies unsupported transmissions and displays the file's SHA-256. Related opendbc source templates with `IMPORT` directives must first be flattened by their generator. Uploaded files are stored on the machine running the GUI under `$XDG_CONFIG_HOME/ecu-simulator/dbc` (normally `~/.config/ecu-simulator/dbc`). Named profiles reference the original bytes by hash. To move a profile to another bench, re-import the same DBC there.

To try the [pinned Ford Fusion sample](https://raw.githubusercontent.com/commaai/opendbc/79ddcc7bb72d557fc5b20d038c0fad9c9d423446/opendbc/dbc/ford_fusion_2018_pt.dbc), start with the **ice_default** working copy. Select **EPAS_INFO**, enter **12** for **SteMdule_U_Meas** (the spelling in the DBC), set **100 ms**, and select **Add message to working copy**. Start the ECU on **vcan0**. In **Live CAN**, start capture on the same interface. Expect standard ID **082**, DLC **8**, data **80 00 14 00 78 00 00 00** and measured voltage **12 Volts**. **Save as** retains the configuration for another launch. A signal field also accepts an existing physical vehicle signal path such as `vehicle.battery_voltage`. Multiplexor selections are constants.

For passive inspection only, open **Live CAN → DBC decoder**, choose a local database and start capture. A switch of decoder starts a fresh trace. Frames are matched by both ID and frame format. Unknown messages stay visible as raw traffic. Use **Use this DBC for decoding only** in the simulator editor to attach it to a profile without publishing imported messages.

Starting from the GUI on a physical bench reviews message IDs, periods, preview bytes and values. The GUI reports matching IDs seen during a 500 ms observation window. A silent window can miss a slower or intermittent sender. The physical interface's bitrate comes from its existing SocketCAN configuration. CAN FD and generation of recognized OEM checksum/counter fields are outside this Classical CAN publisher's scope. A message passing the layout check still requires the vehicle program's specifications to establish ECU compatibility.

### Wheel-speed demo

Select **wheel_speed_demo**, choose **vcan0**, and click **Start ECU**. Under **Live CAN**, select the same interface and click **Start capture**. The simulator broadcasts the Toyota `WHEEL_SPEEDS` message (standard CAN ID `0AA`) every 20 ms. All four wheels follow `vehicle.speed`. The repeating scenario spends 10 seconds at each step: 0, 40, then 0 km/h. The two zero-speed steps at the cycle boundary run consecutively.

Read the measured period, receive age and raw bytes in the wheel-speed panel. At 40 km/h the bytes are `2A 0F 2A 0F 2A 0F 2A 0F`. Under **Simulator**, send `01 0D` as a raw request to **engine.obd_physical** during that plateau to see `41 0D 28`. **Pause broadcast** keeps the scenario and diagnostic services running. The monitor marks the sample stale after 100 ms without a wheel frame. **Resume broadcast** publishes the current speed.

The same profile runs on **can0** on a dedicated terminated bench at its configured bitrate. The publisher opens one raw SocketCAN socket. Other profiles enable this feature only if their YAML includes a supported `restbus` mapping. The [bench design](docs/superpowers/specs/2026-11-20-wheel-speed-restbus-design.md) identifies the pinned opendbc database and the [bundled license](src/ecu_simulator/_opendbc/LICENSE) covers the upstream assets.

To run browser acceptance locally, install the dev and GUI extras, Playwright, and a Chromium browser. The suite discovers Google Chrome or Chromium on PATH, honors `ECU_GUI_CHROME` for an explicit executable, and otherwise uses Playwright's installed Chromium.

```sh
python -m pip install -e ".[dev,gui,hardware]" playwright
python -m playwright install chromium
bash scripts/run_integration_tests.sh -k "gui_control_vcan or wheel_speed_demo" -q
```

## Usage

Configure the CAN interface once, with privileges, then run the simulator unprivileged.

Virtual CAN (software-only testing, CI-style):

```
sudo scripts/setup_vcan.sh vcan0
ecu-simulator --interface vcan0
```

Physical CAN (ELM327 or other testers on a real bus, Classical CAN):

```
sudo scripts/setup_can.sh can0 500000
ecu-simulator --interface can0
```

Bitrate is a privileged link property, so it is set here and not by the simulator. There is
no `ecu-simulator --bitrate` and there will not be: the simulator runs unprivileged, and
`setup_can.sh` exists to keep that separation. `500000` and `250000` are the OBD bitrates
(ISO 15765-4); any other value is accepted with a warning, which is fine for non-OBD use.

`setup_can.sh` also takes two environment variables:

| Variable | Default | Effect |
|---|---|---|
| `CAN_RESTART_MS` | `100` | automatic bus-off recovery delay in ms. **`0` explicitly disables it** — use that while troubleshooting, so a bus-off stays visible |
| `CAN_TERMINATION` | unset | ohms for a controller's switchable termination, e.g. `120` or `0`. Unset means termination is not touched. Ignored with a warning on controllers that do not support it |

Against an ELM327 a bitrate mismatch does not announce itself: it presents as silence, as
`NO DATA`, or as `CAN ERROR`. See
[docs/hardware-testbench.md](docs/hardware-testbench.md) for the bench procedure and
troubleshooting.

Options: `--profile PATH` (default: the packaged `profiles/ice_default.yaml`), `--interface IFACE` (default: the profile's `transport.interface`), `--log-level {DEBUG,INFO,WARNING,ERROR}`, `--version`, `--help`. Stop with Ctrl-C or SIGTERM; the simulator closes its sockets and exits with status 0. A missing or down interface, a kernel without `CAN_ISOTP`, or an invalid profile is reported with an actionable message and exit status 2.

Check a profile without opening a socket:

```
ecu-simulator validate-config --profile my_profile.yaml
```

### Configuration

Addresses, vehicle data and per-ECU trouble codes come from a YAML profile, validated before anything runs. The shipped default is `src/ecu_simulator/profiles/ice_default.yaml`; copy it and pass `--profile`. Each ECU lists its endpoints, and each endpoint carries its receive and transmit CAN identifiers, whether it is physically or functionally addressed, which protocols it enables, and its padding. The schema is multi-ECU: a second ECU is a second key under `ecus`.

A malformed profile is rejected at load with the dotted path to every problem at once, rather than being silently corrected or failing later (DEV-13, DEV-14). This is project input validation and is not a standards conformance claim.

### Scenarios: state that changes over time

A profile may add a `scenario:` section, and each ECU may add `dtc_events:` beside its
`dtcs:`. Signals are then driven by generators that are pure functions of the seconds
elapsed since the simulator started -- `constant`, `ramp`, `sine`, `stepped`, `sequence`
and `timeline` -- and trouble codes can be raised or cleared at scheduled times:

```yaml
scenario:
  tick: 0.5                 # how often the scenario is applied on an idle bus
  signals:
    - {path: vehicle.speed, type: ramp, from: 0, to: 120, over: 60}
    - {path: engine.coolant_temp, type: timeline, points: [{at: 0, value: 20}, {at: 90, value: 92}]}

ecus:
  engine:
    dtcs:
      - {code: P0128, pending: false, confirmed: false}
    dtc_events:
      - {at: 40, action: raise_pending, code: P0128}
```

Everything is deterministic and reproducible: the same elapsed time always gives the same
answer, there is no randomness of any kind, a read never advances anything, and restarting
the process replays the scenario from the beginning. A timed event fires exactly once --
clear it with OBD Mode 04 or UDS 0x14 and it stays cleared.

A scenario changes what the vehicle *is*. It cannot drop, delay, corrupt or override a
response; fault injection is a later phase. It also cannot invent a trouble code: an event
may only act on a code the profile already declares, and a scenario naming an unknown
signal, an unknown code or a nonsensical parameter is rejected at load with its path.

The shipped `ice_default.yaml` deliberately has **no** scenario, so the default
configuration answers the same bytes it always has. A worked demonstration --- a
two-minute drive with a warm-up ramp and a thermostat fault --- ships beside it:

```bash
ecu-simulator --profile src/ecu_simulator/profiles/ice_scenario.yaml --interface vcan0
```

The simulator no longer configures interfaces, loads kernel modules or needs root; the old `sudo python3 ecu_simulator.py` workflow is gone, and so are `ecu_config.json`, `ecu_config.py` and `addresses.py`.

### Addressing on the wire

All three addresses belong to one simulated ECU, `engine`. Each address enables a set of
protocols, and a protocol that is not enabled on the address a request arrives on never
sees that request.

| Address | Enables | Answers on | Unknown service |
|---|---|---|---|
| `0x7DF` functional | OBD | `0x7E8`, padded to 8-byte frames (pad byte `0x00`) | no response |
| `0x7E0` physical | OBD, UDS | `0x7E8`, padded | `7F <SID> 11` |
| `0x7E1` physical | OBD, UDS | `0x7E9`, unpadded | `7F <SID> 11` |

* A UDS request on `0x7DF` reaches no protocol, so nothing is transmitted. The shipped
  configuration records that the UDS module does not use functional addressing.
* A response an enabled protocol produces is transmitted unchanged, including a negative
  response. Nothing is filtered out after the fact.
* The tester's flow control for multi-frame responses is expected on `0x7E0`, as
  ISO 15765-4 testers and ELM327 adapters send it.
* OBD modes `0x01` to `0x0A` that the legacy OBD layer does not implement still get no
  response (DEV-11).

These addresses come from the profile. The shipped profile keeps the identifiers the project has always used, with the response identifier eight above the request identifier.

## Logging

Application events go to the console and to a rotating `ecu_simulator.log` in the working directory (1.5 MB per file, 5 files), at the level given by `--log-level`. Every line names the ECU and protocol that produced it, `[engine/uds]`, or `[-/-]` outside request handling. For raw CAN or ISO-TP captures use can-utils instead of the removed file loggers:

```
candump -l vcan0                 # raw frames to a candump log file
isotprecv -s 7E0 -d 7E8 -l vcan0 # ISO-TP payloads on the OBD physical channel
```

## Testing

```
pytest                                   # unit + characterization; integration skips without vcan0
scripts/run_integration_tests.sh         # integration tests in a private namespace, no root needed
```

The integration tests use the real kernel ISO-TP path on a `vcan` interface and skip with an explicit reason when the kernel lacks `CAN_ISOTP` or the interface is missing. `docs/known-deviations.md` lists the protocol behaviors that are still known to be wrong and pinned by tests.

## Original upstream test environment

The upstream project was tested on a Raspberry Pi (Raspbian, Linux Kernel 4.19) with PiCAN and [SBC-CAN01](http://www.anleitung.joy-it.net/wp-content/uploads/2018/09/SBC-CAN01-Anschlussplan.pdf) (see pic below) as CAN-Bus board. 

### OBD-II

The OBD-II services were tested using a real OBD-II scanner.

<img src="https://github.com/lbenthins/ecu-simulator/blob/master/img/obd_sbc-can01.jpg" alt="OBD-II test - SBC-CAN01"/>

<img src="https://github.com/lbenthins/ecu-simulator/blob/master/img/obd_detecting.jpg" alt="OBD-II test env" width="407" height="314"/>

<img src="https://github.com/lbenthins/ecu-simulator/blob/master/img/obd_dtc.jpg" alt="OBD-II test env" width="407" height="314"/>

<img src="https://github.com/lbenthins/ecu-simulator/blob/master/img/obd_info.jpg" alt="OBD-II test env" width="407" height="314"/>


### UDS

To test the UDS services, the [Caring Caribou](https://github.com/CaringCaribou/caringcaribou) tool was used.

<img src="https://github.com/lbenthins/ecu-simulator/blob/master/img/caringcaribou_1.png" alt="UDS test env" width="496" height="160" />

<img src="https://github.com/lbenthins/ecu-simulator/blob/master/img/caringcaribou_2.png" alt="UDS test env" width="496" height="160" /> 

## License 

MIT License

Copyright (c) 2020 Luis Alberto Benthin Sanguino

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.



