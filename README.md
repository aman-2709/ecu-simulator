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
| 0x10       | DiagnosticSessionControl | **session types** <br> <br> 0x01 default <br> 0x02 programming <br> 0x03 extended <br> 0x04 safety | |
| 0x11       | ECUReset                 | **reset types** <br> <br> 0x01 hardReset <br> 0x02 keyOffOnReset <br> 0x03 softReset <br> 0x04 enableRapidPowerShutDown <br> 0x05 disableRapidPowerShutDown | 0x0F powerDownTime |
| 0x14       | ClearDiagnosticInformation | groupOfDTC `FFFFFF` only; any other group gets NRC 0x31 | |
| 0x19       | ReadDTCInformation       | **report types** <br> <br> 0x02 reportDTCByStatusMask, mask required | <br> 0x8C DTCStatusAvailabilityMask <br> statusOfDTC derived from the store: 0x04 pending, 0x08 confirmed, 0x80 indicator requested |
| 0x3E       | TesterPresent            | 0x00 zeroSubFunction; anything else gets NRC 0x12 | `7E 00`. Stateless: no S3 timer, no session state, no ISO 14229 session-compliance claim |

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



