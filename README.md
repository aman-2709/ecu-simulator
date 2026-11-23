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

Open **Live CAN → Replay a CAN recording**, choose the default **2015 Kenworth T660** driving excerpt and click **Play on vcan0**. If Play starts capture, either stop button ends both replay and capture. Capture also stops after the last frame, leaving the trace available for review. If you started capture yourself, it continues after replay ends. The Kenworth sample opens **By PGN and source**, with one row per J1939 parameter group and sender, its measured interarrival time, and the latest payload. Search for `rpm`, `EEC1`, `61444`, or `0CF00400` to follow engine speed (SPN 190). **Trace** retains chronological frame order. Select **Interpret extended CAN IDs as J1939** to use this view with a local recording or live capture. **Repeat recording** repeats a pass until you click **Stop replay**. No ECU profile is required. Other senders on the same bus also appear in the capture.

EEC2 shows accelerator pedal position (SPN 91, byte 2) and percent engine load at current speed (SPN 92, byte 3). Hover over the value to see the byte mapping. Request messages show the requested PGN. The monitor distinguishes unavailable readings from zero and identifies groups without a signal decoder. **Last interval** measures the elapsed time between observations in that group. **Replay loop** marks an interval spanning the start of another pass on vcan0.

**Signal plot** graphs engine speed (rpm), accelerator pedal (%) and engine load (%) for one J1939 source address on a shared elapsed-time axis. Choose the source and signals above the plot. Unavailable readings leave gaps. **Pause display** freezes both the table and plot while capture continues. A new capture or replay pass starts a fresh timeline. The rolling plot retains up to 30 seconds or 8,192 signal-bearing frames.

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

### Run a whole car

Pick a car from the catalog (or upload a DBC) and press **Play**. The simulator then sends every message of that car's DBC that it can transmit, each at its own period, with signal values from a generated drive and with counters and checksums computed for the bytes actually sent, until you press **Stop**. It is for test engineers who need believable, repeating bus traffic from a whole car on `vcan0` or on a bench `can0`, for example to exercise a logger, a gateway or a receiver that validates counters and checksums, and for engineers who run it on their own network with their own DBC and their own checksum rules. The traffic is generated from the DBC; it is not a recording of a real car and is labelled as generated. The simulated ECU still answers OBD and UDS as the profile's own vehicle, not as the chosen car, and the manual **DBC messages** panel above is unchanged (it still refuses messages that have counter or checksum fields).

The catalog has 121 platforms (Toyota 39, Honda 30, Hyundai 52, classic CAN only) from 21 DBCs. Everything below was checked on `vcan0` or by unit tests. The physical-bus acceptance has not been run; see [docs/validation/car-from-dbc.md](docs/validation/car-from-dbc.md) for what was measured and for the procedure that remains.

#### In the browser

```sh
ecu-simulator-gui --port 8766
```

Open the printed loopback address. The **Car** panel is on the **Simulator** tab, above **DBC messages**.

1. **Choose.** Pick a brand and platform, and the bus if the car has more than one (the Corolla has `pt` and `radar`; one bus runs at a time), or upload a DBC. Then pick the source: a **generated drive**, optionally with a candump log attached only to measure each message's period, or a **recorded log** played as recorded (`vcan0` only, once through).
2. **Check.** Chips count the messages in each integrity state. The table lists every message with its ID, its period and where the period came from (with an **Edit** button), and its integrity state with the reason. Here you also pick the integrity rule: the catalog's, Toyota, Honda, Hyundai (classic CAN), or an uploaded rule file. Nothing is sent yet.
3. **Run.** Pick the interface and read the estimated bus load against the interface's bitrate, then press **Play**. The live data panel below the steps shows, for each message, the signal values as sent, the period set and the period measured on the bus, the counter, and the checksum with its integrity state. It also shows the bus load seen, and on a physical interface the adapter's bus-error and bus-off counters and the kernel's tx-dropped counter.

Selecting the shipped `car_toyota_demo` profile and clicking **Start ECU** runs the same Toyota car. The run shows the DBC's SHA-256, the opendbc revision and each message's integrity state.

#### From the command line

With `vcan0` set up as under [Usage](#usage):

```sh
ecu-simulator validate-config --profile src/ecu_simulator/profiles/car_toyota_demo.yaml
ecu-simulator --profile src/ecu_simulator/profiles/car_toyota_demo.yaml --interface vcan0
candump -L vcan0
```

`car_toyota_demo` is a 2017-19 Toyota Corolla on its powertrain bus. It sends 57 of the bus's 58 messages (`PCS_HUD` is skipped: its DBC layout has overlapping signals). Vehicle speed steps 0, 40 and 0 km/h every 10 seconds, and the car's speed signals follow it, so an OBD request `01 0D` agrees with the wheel speeds on the bus.

#### The `car:` profile section

```yaml
car:
  platform: TOYOTA_COROLLA
  bus: pt
  periods:
    WHEEL_SPEEDS: 20
```

| Key | Meaning |
|---|---|
| `platform` | A catalog platform ID such as `TOYOTA_COROLLA` or `HONDA_CIVIC_BOSCH`. Give `platform`, or `sha256` with `filename`, not both. |
| `bus` | A bus of that platform, `pt` by default. An uploaded DBC has one bus. |
| `sha256`, `filename` | An uploaded DBC: the digest the importer shows, and a display name. The name is never used as a path. |
| `rule` | `toyota`, `honda` or `hyundai_can`. Overrides the catalog's rule for the DBC. |
| `rule_file` | The SHA-256 of an uploaded rule file (see below). |
| `periods` | Your overrides in milliseconds, 10 to 60000, by DBC message name. |
| `log_periods` | Periods measured from an attached log. The panel writes this; you rarely edit it. |

`car` cannot be combined with `restbus` or `dbc`. A name in `periods` that is not a message of the DBC is ignored by the run, and the Check step lists it as a warning. The importer keeps uploaded DBCs and rule files by digest under `$XDG_CONFIG_HOME/ecu-simulator` (normally `~/.config/ecu-simulator`), so a profile that names a digest runs only on a machine that has imported the same files.

#### On a physical bus

- Never point this at a vehicle network that carries a real car. It is for a dedicated, terminated test bus.
- A lone transmitter gets no acknowledgement and ends up bus-off, so the bus needs a second active node.
- The simulator never sets the bitrate. Configure the interface first with `scripts/setup_can.sh`, as under [Usage](#usage).
- On a physical interface the run refuses to start when its estimated load is above 70% of the interface's configured bitrate, and names the three largest messages. It also refuses when the interface has no bitrate configured or is down (state `STOPPED`). A link in `BUS-OFF` is not refused at start; the run reports bus-off while it runs. The estimate counts worst-case stuff bits. On `vcan0` it is informational and never refuses.
- Starting a car on a physical interface asks for the same bench transmission review as the manual DBC messages: a dialog lists every message with one stamped sample frame and any identifier already seen on the bus during a 500 ms observation. A silent window can miss a slower or intermittent sender.
- Any failed send stops the run with its cause (tested by taking the interface down mid-run), and so does a run-time encode error. Nothing is resent silently.

#### What the integrity labels mean

| Label | Meaning |
|---|---|
| `valid` | A rule for this DBC is known and the message has fields it recognises. The counter advances on every transmission and wraps at its maximum, and the checksum is computed over the bytes sent. For Toyota and Honda the bytes equal opendbc's packer's for the same field values (740 stored vectors), and opendbc's own `CANParser` accepts the frames of a Corolla and a Honda Civic run. For **Hyundai classic CAN**, `valid` means only that the bytes equal opendbc's packer's for the signals it names: opendbc's own parser ignores that DBC's checksums and counters, nothing here was checked against a real Hyundai, and a given model may not use those fields. |
| `counter only: checksum not computed` | There is no rule for this DBC. A signal named `COUNTER` advances; a signal named `CHECKSUM` stays at its DBC default (normally zero). |
| `valid (user rule)` | Computed as the supplied rule file says. It has not been checked against a car. |
| `no counter or checksum` | There is nothing to generate. A signal whose name only looks like a counter or checksum is held at its DBC initial value and the detail says so. |
| `skipped: <reason>` | Not transmitted: `CAN FD`, `layout error: ...`, `SecOC authenticator`, `collides with <ecu>.<endpoint> on 0x...` (the simulated ECU's own diagnostic IDs), or `encode failed: ...`. The run goes on with the rest. It fails only when nothing can be transmitted or the interface cannot be used. |

Rules are chosen by the DBC's SHA-256, never by guessing from a file name. An upload whose bytes equal a catalog DBC's, shipped or original, gets that DBC's rule. Any other uploaded DBC has no rule until you pick one in the Check step or upload a rule file.

#### Where the periods come from

Each message's period comes from the first of these that exists:

1. **set by you**: the Edit button, or `car.periods`.
2. **from log**: measured from a candump log you attached (needs at least three frames of that message).
3. **from DBC**: the message's `GenMsgCycleTime`. opendbc's DBCs have none.
4. **opendbc safety table, approximate**: the expected frequency in opendbc's safety receive tables. Only 16 messages have one (Toyota 3, Honda 5, Hyundai 8), and ambiguous addresses are left out.
5. **assumed**: 100 ms. Most messages end up here (55 of the Corolla's 58).

A period below 10 ms or above 60000 ms is clamped and labelled. The **period measured on the bus** column in the live panel is the median of the last 100 intervals between that message's frames, with the shortest and longest of them, taken from the capture's kernel receive timestamps rather than the sender's own timer. A mean would hide scheduling jitter, so none is shown. The column reads "measuring..." until five intervals exist.

#### Rule files

A DBC that the catalog does not cover can have its counters and checksums generated by a rule file: YAML, data only, uploaded in the Check step. For a catalog car it replaces the catalog's rule for the messages it matches. Nothing in it is executed, and YAML anchors, aliases and tags are refused. Each rule picks its messages exactly (`messages: [Gearbox]`) or by `glob: "Gear*"` (case-sensitive), and gives a `counter`, a `checksum`, or both. The first rule in the file that matches a message wins. A rule that matches no message of the DBC, names a missing signal or puts a field outside the frame is refused with its dotted path, for example `rules.0.checksum.signal`. A file is at most 256 KiB and 1000 rules.

Checksum algorithms: `xor`, `sum8` and `nibble_sum` over the bytes in `covers` (by default every byte that does not overlap the checksum field), and `crc8` and `crc16` with `poly`, `init`, `xorout` and `reflect`. An optional `data_id` is one more byte after the covered bytes. The counter is written before the checksum is computed, so a checksum whose `covers` include the counter's byte covers the counter.

This DBC fragment has two messages (`XXX` is the node name opendbc DBCs use):

```dbc
BO_ 512 Gearbox: 8 XXX
 SG_ GEAR : 0|4@1+ (1,0) [0|15] "" XXX
 SG_ OUTPUT_RPM : 8|16@1+ (1,0) [0|65535] "rpm" XXX
 SG_ ALIVE : 48|4@1+ (1,0) [0|15] "" XXX
 SG_ CRC : 56|8@1+ (1,0) [0|255] "" XXX

BO_ 291 Brake: 4 XXX
 SG_ CRC : 0|8@1+ (1,0) [0|255] "" XXX
 SG_ ALIVE : 8|4@1+ (1,0) [0|15] "" XXX
 SG_ PRESSURE : 16|16@1+ (1,0) [0|65535] "" XXX
```

and this rule file covers both:

```yaml
version: 1
rules:
  - messages: [Gearbox]
    counter: {signal: ALIVE, max: 9}      # counts 0 to 9, then 0 again
    checksum:
      signal: CRC
      algorithm: crc8
      poly: 0x1D
      init: 0xFF
      xorout: 0xFF                        # reflect is false unless you set it
      covers: [0, 1, 2, 3, 4, 5, 6]
      data_id: 0x5A                       # appended after byte 6
  - messages: [Brake]
    counter: {signal: ALIVE}              # no max: counts 0 to 15, the field's 4 bits
    checksum:
      signal: CRC                         # byte 0
      algorithm: xor                      # no covers: bytes 1 to 3, every byte CRC does not overlap
```

With `GEAR` 3 and `OUTPUT_RPM` 2500 and every other signal 0, the `Gearbox` rule produces (byte 6 is `ALIVE`, byte 7 is `CRC`):

```text
frame  bytes
0      03 C4 09 00 00 00 00 5E
1      03 C4 09 00 00 00 01 12
2      03 C4 09 00 00 00 02 C6
9      03 C4 09 00 00 00 09 48
10     03 C4 09 00 00 00 00 5E
```

`Brake` counts 0 to 15 and then wraps, and its `CRC` is the XOR of bytes 1 to 3. A unit test reads the three blocks above from this README, parses the rule file, binds it to the DBC, stamps the frames and compares them with a separate bitwise CRC (`tests/unit/test_car_readme_example.py`). Parsing is pure Python and slows the GUI server while it runs: a dense 256 KiB file took 7.7 to 10.5 seconds on the development machine and a realistic 1000-rule file about 3 seconds.

#### Catalog source and licence

The catalog and the DBCs come from comma.ai's [opendbc](https://github.com/commaai/opendbc) (MIT licence, the text is in `src/ecu_simulator/_opendbc/LICENSE`) at revision `af54130d51111197d34384e7dad9ace229b724e4`. The Toyota, Honda and Hyundai counter and checksum rules are ported from opendbc's `toyotacan.py`, `hondacan.py` and `hyundaican.py`. The simulator does not import opendbc at run time; a dev-only extractor wrote the catalog (`scripts/extract_opendbc.py`). Thirteen of the 21 shipped DBCs are not byte-identical to opendbc's: malformed comment lines were dropped and identifiers above `0x7FF` were flagged as extended, so that the DBC parser used here can read them. `cars.json` records, for each DBC, opendbc's SHA-256, the shipped SHA-256 and what changed, and the extractor checks that every message and signal reads the same as in opendbc's own parse. The wheel-speed demo's pinned DBC (opendbc `79ddcc7bb72d557fc5b20d038c0fad9c9d423446`) is separate and unchanged.

#### Known limits

- The physical-bus acceptance has not been run. The evidence is from `vcan0` and unit tests.
- Classical CAN only. 27 platforms are skipped as CAN FD (Hyundai CAN FD and Honda CAN FD DBCs). The `honda_common_canfd` name rule also skips `ACURA_MDX_4G`, which is a classic CAN car that shares that DBC.
- One bus per run. A car with a powertrain DBC and a radar DBC runs one of them.
- Only 16 messages carry a catalog rate; every other message is 100 ms, labelled assumed, unless the DBC has a cycle time, an attached log supplies one, or you set it.
- The generated drive animates vehicle speed, engine speed, steering angle and one pedal. Gear and brake keep their DBC defaults. Hyundai gets no steering and Honda and Hyundai get no pedals (their units are missing or differ). A few signals that are not the vehicle's speed or engine speed still follow them: `SPDVAL1`, `HUD_SPEED_KPH`, `HUD_SPEED_MPH`, `PCM_SPEED`, `R_NEngIdlTgC`, `CR_Opi_Spd_Rpm`, `N_TC` and `N_TC_RAW`. Signals with SET, REL, TGT, TARGET, TRGT, REQ, LIMIT, OFFSET, DECIMAL or CRUISE as a whole word in their names are deliberately left at their defaults.
- About 38 messages across the 21 DBCs are skipped, with the reason, because the DBC importer rejects their layout. 34 of them carry counter and checksum fields: three on each of the 11 Honda and Acura powertrain DBCs (`KINEMATICS`, `KINEMATICS_ALT`, and `BRAKE_HOLD` or `BRAKE_PRESSURE`) and a fourth, `STEER_STATUS`, on the Odyssey (Taiwan) DBC. The other 4 are Toyota `PCS_HUD`, which has no counter or checksum. For 33 of the Honda messages the importer says `Invalid physical range for <signal>` (`LAT_ACCEL`, `BRAKE_PRESSURE1` or `XMISSION_SPEED`): the DBC's declared minimum and maximum do not fit that signal's width, scale and offset, or its minimum is above its maximum. The other 5 (`STEER_STATUS` and the four `PCS_HUD`) have overlapping signals. This is a limit of the importer's validation, not of the car or of the checksum code. A receiver that expects these messages will not get them from this simulator: `HONDA_CIVIC_BOSCH` sends 47 of its 50 messages and not `KINEMATICS`, `KINEMATICS_ALT` or `BRAKE_HOLD`. The 9 SecOC authenticator messages in the Toyota SecOC DBC are skipped.
- Hyundai classic CAN is checked against opendbc's packer only.
- Rule files: the first matching rule wins, uploaded files are data only, and a worst-case 256 KiB file can take 7 to 10 seconds to parse.
- A replayed recorded log sends its own counters, so they restart where a repeated recording loops. Replay is `vcan0` only.
- "tx dropped (kernel)" is the interface's tx-dropped counter. It may not count a frame refused because the queue was full.
- The simulator never sets the bitrate and must never be pointed at a live vehicle network.

#### After upgrading

A `ecu-simulator-gui` that was started before an upgrade keeps serving the old code until you restart it. It reads its page HTML fresh from disk but not its routes, so the new page loads and the Car panel stays empty. Stop the old process and start it again. If `ecu-simulator-gui --port 8766` ends with `address already in use`, an older GUI still holds the port.

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



