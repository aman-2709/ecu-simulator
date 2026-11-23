# Whole-car run (car from DBC) acceptance

The whole-car run sends every transmittable message of a catalog car's DBC, with counters and checksums, on a SocketCAN interface. Its design is [2026-11-22-car-from-dbc-design.md](../superpowers/specs/2026-11-22-car-from-dbc-design.md), and its verification items are numbered as in that design.

This record has two parts. **Part A** is evidence collected on `vcan0` and by unit tests. **Part B** is the physical-bus acceptance (verification item 5). It is a procedure with empty result fields.

> **Part B status: NOT RUN: no physical bench result exists yet.** Nothing in Part A is a physical-bus result, and nothing in Part A should be read as one.

## Part A: evidence on vcan and from unit tests

Measured on 2026-11-22 on a Linux 6.8.0-138-generic host (12 CPUs, Python 3.12.12), in an isolated `vcan0` made by `scripts/run_integration_tests.sh`. The source under `src/` is identical at the measured commit `fc76f6d` and at `d2c3d43`, the last commit that changed it. Figures marked "(notes)" come from the notes kept during implementation (a report per task and a running ledger). Those are working files that are not in the repository, so a reader cannot check them; the test named in the same row can be rerun.

| Check | Observed result | Source and where measured |
| --- | --- | --- |
| Counters and checksums equal opendbc's packer (verification 1) | 740 stored vectors (185 message layouts, four payload variants each) from opendbc `CANPacker` at revision `af54130d`. Replayed through the integrity engine, 724 were compared and every frame was byte-identical: 19,035 frames (Toyota 336 vectors and 17,595 frames, Honda 236 and 882, Hyundai classic 24 and 142, no rule 128 and 416). The other 16 vectors (4 distinct messages with layout errors in the fixture, four variants each) are refused as unsupported. Across all 21 shipped DBCs 38 messages have such errors; see the README's known limits. The checksum functions alone reproduce 612 vectors that have a rule: 17,418 Toyota, 942 Honda and 115 Hyundai checksum evaluations. | Unit tests `tests/unit/test_car_checksums.py` and `tests/unit/test_car_integrity.py`; counts from the task notes. No bus involved. A reviewer also compared the ported rules with the upstream functions on 60,000 generated cases with no difference (notes). |
| Independent receiver: opendbc `CANParser`, Toyota (verification 3) | TOYOTA_COROLLA, powertrain bus, 7.0 s on `vcan0` through the GUI's child process, captured by a raw socket with no receive-queue overflow. All 13 messages the integrity engine marks `valid` (12 with a checksum, 1 with a 6-bit counter and a checksum) were accepted frame by frame: 1,424 frames, at least 71 per message. The parser's own count of counter misses stayed 0 after each message's first frame. The parser and the engine named the same counter and checksum signals in every message. | `tests/integration/test_car_oracle_receiver.py`, run on `vcan0`. opendbc revision `af54130d`; its DBC bytes match the catalog's recorded source hash. |
| Independent receiver: opendbc `CANParser`, Honda (verification 3) | HONDA_CIVIC_BOSCH (DBC `honda_civic_hatchback_ex_2017_can_generated`), 7.0 s. All 47 `valid` messages were accepted frame by frame: 4,988 of 4,988 captured frames, at least 71 per message. 46 messages carry a counter and a checksum, and two (LKAS_HUD_A, LKAS_HUD_B) have 29-bit identifiers, which exercises Honda's extended-identifier rule. The car lists 50 messages and transmits 47: KINEMATICS_ALT, KINEMATICS and BRAKE_HOLD are skipped with `layout error: Invalid physical range for ...` (see the README's known limits), so a receiver that expects those three will not see them. This platform was chosen because its powertrain DBC has 46 messages with both a counter and a checksum, which is many, and includes two extended-identifier messages. Six platforms share that DBC (among them HONDA_ACCORD and HONDA_CRV_5G). One other Honda DBC, `honda_bosch_radarless_generated`, has 49 such messages; it was not run. | Same test, same run. |
| That receiver test can fail | One checksum bit of one captured frame changed in memory (frame 10 of Toyota SPEED `0xB4`, of Honda GAS_PEDAL_2 `0x130`): the parser rejected exactly that frame and accepted the rest. A counter frozen after frame 10 (STEERING_LKA `0x2E4`, GAS_PEDAL_2 `0x130`): the parser rejected 56 of 71 frames from frame 15 and `can_valid` turned false. Mutating the production code instead made the acceptance test fail for both cars: the Toyota sum off by one together with Honda's extended-identifier term removed (the Honda failure began at LKAS_HUD_A `0x33DA`, the first extended message), and separately the counter advancing by 2 (STEERING_LKA and GAS_PEDAL_2 from their fifth frame). The code was restored afterwards. | The two control tests in the same file, and mutations run once by hand. |
| Hyundai classic CAN | Not checked by an independent receiver. opendbc's parser sets `ignore_checksum` and `ignore_counter` for `hyundai_can_generated`, so it would accept any bytes. Hyundai "valid" means equal to opendbc's packer for the signals it names, which rests on the first row alone. | opendbc's own `opendbc/can/dbc.py` at revision `af54130d` (not part of this repository). |
| Corolla period on `vcan0` (verification 2) | 7.0 s capture, 5,064 frames, no overflow. Busiest message WHEEL_SPEEDS (12 ms, from the opendbc safety table): 586 frames, median 12.254 ms (2.1% over), p95 12.673 ms, maximum 13.601 ms. A 100 ms message: 71 frames, median 100.010 ms, p95 100.736 ms, maximum 100.976 ms. The design's bound is a median within 10%. Earlier runs gave WHEEL_SPEEDS medians of 12.278 and 12.267 ms (notes). | `tests/integration/test_car_vcan.py::test_corolla_run_on_vcan_matches_independent_capture_and_holds_period`, `vcan0`. |
| Counter continuity (verification 2) | STEERING_LKA's 6-bit counter ran 0 to 63 and back to 0 with each frame one more than the last, in a capture long enough for one wrap. | Same test. |
| 150-message car on `vcan0` | A synthetic DBC of 150 messages at 10 ms, every third with a Toyota counter and checksum, 5.00 s: 75,307 frames (about 75,061 expected), no overflow, 498 to 504 frames per message. Per-message median period 10.000 to 10.079 ms, worst per-message p95 11.475 ms, longest single interval 24.666 ms. Earlier runs gave medians 10.015 to 10.122 ms with a longest interval of 13.066 ms, and 9.976 to 10.054 ms with 17.864 ms (notes). The publisher's schedule held without a change, so the kernel broadcast manager fallback in the design was not needed on `vcan0`. A 10 ms message is sometimes late by more than its period on this host, and the median does not show it. | `tests/integration/test_car_vcan.py::test_150_message_car_keeps_period_on_vcan`, `vcan0`. This says nothing about a physical adapter. |
| OBD agrees with the bus (verification 2) | At the 40 km/h plateau, `01 0D` returned `41 0D 28` and the wheel speed in the next WHEEL_SPEEDS frame was 40.0 km/h. | `tests/integration/test_car_vcan.py::test_obd_speed_matches_wheel_speed_plateau`, `vcan0`. |
| Live panel against an independent capture (verification 7) | In Chromium on `vcan0`, the measured-period cell of the busiest message is the median of its last 100 bus intervals with the shortest and longest. The test requires it to be within 0.5 ms or 5%, whichever is larger, of the median of the last 100 intervals seen by a separate raw socket, and that median to lie between the cell's minimum and maximum. A walk of the final commit read WHEEL_SPEEDS as 12.28 ms with minimum 10.03 and maximum 13.53 ms (notes). The earlier panel showed a mean, and read 12.0 ms in every sample while a raw socket measured median 12.178, mean 12.023, minimum 3.16, maximum 25.19 and p95 12.83 ms over 1,067 intervals (notes). That is why the panel shows a median and a spread. | `tests/integration/test_car_gui.py::test_measured_period_is_the_bus_median_with_its_spread` and `test_measured_period_statistic_is_the_median_not_the_mean`. |
| Live panel counter and checksum cells (verification 7) | The paused panel's counter and checksum for STEERING_LKA matched one of the frames of a raw capture, decoded by a bit reader and a Toyota rule written in the test. | `tests/integration/test_car_gui.py::test_walk_three_steps_on_vcan`, `vcan0`. |
| A send failure stops the run (verification 7) | Taking the interface down mid-run stopped the run: the state read Failed and the banner named "Send failed", "Network is down" and `ip link set up <interface>`. | `tests/integration/test_car_gui.py::test_banner_names_cause_when_interface_goes_down`, a private virtual interface. |
| Bus-load guard (verification 6) | The same 150-message car on a physical-style link (`LinkInfo` for `can0`, 500 kbit/s, not up to a real adapter) is refused: "car: Estimated load 405.0% of can0 at 500000 bit/s is above the 70% ceiling. Largest messages: MSG_000 2.7%, MSG_001 2.7%, MSG_002 2.7%". On a virtual link all 150 messages are prepared. The Corolla demo's estimate is 18.7% and the Honda Civic Bosch car's 18.0% at an assumed 500 kbit/s, both worst case with stuff bits. The browser's refusal path was tested with a stubbed Check response, because a private namespace has no physical interface. The link parser was written against the real output of `ip -j -d -s link show` for a physical adapter, read only (notes). | `tests/unit/test_car_run.py::test_physical_over_ceiling_is_refused_and_vcan_is_not`, `tests/unit/test_car_busload.py`, `tests/integration/test_car_gui.py`. No physical run. |
| Transmission review gate | With the physical-interface rule forced on a virtual interface: a start without a review token is refused (400, "review"); `/car/review` lists each message with a stamped sample and the identifier `0x0AA` it observed during its 500 ms window; the token starts the run (202) and a second use is refused. A start on a virtual interface needs no token. | `tests/unit/test_car_routes.py::test_run_on_physical_requires_review_token` and the tests around it, `tests/integration/test_car_review.py`. No physical run. |
| Rule file example | The README's DBC fragment and rule file parse, bind to each other and stamp the frames the README prints, checked against checksums computed separately in the test (Gearbox: counter 0 to 9, bitwise CRC-8; Brake: counter 0 to 15, XOR of bytes 1 to 3). The same file was run with `ecu-simulator --profile` on `vcan0` with an independent `candump -L` on 2026-11-23: 37 Gearbox and 37 Brake frames with counters and checksums matching (Brake's counter reached 15 and wrapped). | `tests/unit/test_car_readme_example.py`, and one `candump -L` run by hand in a private namespace. |
| Catalog | 121 platforms (Toyota 39, Honda 30, Hyundai 52) from 21 DBCs; 27 platforms skipped as CAN FD; 16 messages with a catalog rate (Toyota 3, Honda 5, Hyundai 8); 13 of the 21 DBCs normalised (malformed comment lines dropped, identifiers above `0x7FF` flagged extended), each recorded in `cars.json` with both hashes. Shipped data: 21 DBC files, 510,592 bytes, and `cars.json`, 204,587 bytes. | `tests/unit/test_car_catalog.py`, `scripts/extract_opendbc.py`, `src/ecu_simulator/_opendbc/cars.json`. |
| Regression (verification 4) | No file under `tests/characterization` and no existing profile changed between the branch's base `1de01d3` and the commit tested; the only profile added is `car_toyota_demo.yaml`. The default `python -m pytest -q` (unit, characterization and integration, run in a private namespace with its own `vcan0`): 3,291 passed, 2 skipped, 3 xfailed (the known deviations DEV-26, DEV-11, DEV-03) in 456 s. `bash scripts/run_integration_tests.sh -q`: 129 passed, 1 skipped in 325 s. `ruff check src tests scripts` and `mypy` (94 source files) are clean. The skips are the opendbc receiver test, which needs opendbc, and the "aiohttp not installed" form of one test. | `python -m pytest -q` and `scripts/run_integration_tests.sh -q`, run at `fc76f6d` plus the README example test added with this record. |

### What Part A does not show

- Anything about a physical adapter: how the adapter's queue and USB path hold 57 or 150 messages, whether the receiver sees every frame, the error counters and bus-off behaviour with a real second node, and how the "tx dropped (kernel)" counter behaves. The panel labels that counter as the interface's tx-dropped counter, and it may not count a frame refused because a queue was full.
- Anything about a real vehicle or a real receiving ECU. The traffic is generated from DBCs.
- Hyundai classic CAN against any receiver, and CAN FD at all.
- That a period is right: most Corolla periods (55 of 58) are an assumed 100 ms.

## Part B: physical acceptance (verification item 5)

> **Status: NOT RUN: no physical bench result exists yet.** Every result field below is empty on purpose. Only a run on the bench fills them. The design gate for this part reads: a full Toyota car on the dedicated terminated bench, captured by a separate receiver, with the figures below recorded, and the same with opendbc's `CANParser` on the receiving side for Toyota and Honda.

The design sets one expected value, a bus-off count of zero, and expects the parser to report valid frames. This record also expects no error frames at the receiver. The design sets no numeric limit for the periods or the error counters, so record the numbers and let the owner judge them. The `vcan0` figures in Part A are for comparison only.

### Bench

- Host with the candleLight adapter (a CANable running candleLight firmware, driver `gs_usb`) at `can0`, as described in [hardware-testbench.md](../hardware-testbench.md), section 2.2. The simulator runs here, unprivileged.
- A second active node that captures: another SocketCAN adapter on the same two-wire bus, on this host or another (called `can1` below), set to the same bitrate and not in listen-only mode. A lone transmitter gets no acknowledgement, and a listen-only node does not acknowledge.
- A bus terminated with two 120 ohm resistors in total. Check how the adapters terminate before adding any resistor, as in hardware-testbench.md.
- 500 kbit/s on both adapters, set with `scripts/setup_can.sh` (the simulator never sets the bitrate).
- No vehicle. Never connect this to a vehicle network.

### Procedure

Run the Toyota car first. Then repeat steps 3 to 10 for the Honda car with its own log files, choosing platform `HONDA_CIVIC_BOSCH` in step 5 (the GUI can stay running).

1. Note the commit and the host. `git rev-parse HEAD` and `git status --short` in the repository, and `uname -r`. The tested tree should be clean.
2. Configure both interfaces: `sudo scripts/setup_can.sh can0 500000`, and `sudo scripts/setup_can.sh can1 500000` on the receiving host. (The command is the one under Usage in the README and is covered by `tests/unit/test_setup_can_script.py`. It was not run for this record because it needs root and would reconfigure a real adapter.)
3. Record `ip -details -statistics link show can0` and the same for `can1`, before. Keep the full text. Confirm the state is `ERROR-ACTIVE`, the bitrate is 500000, and all counters are readable.
4. Start the capture on the receiver, before the car: `candump -L can1,0:0,#FFFFFFFF > receiver-toyota.log` (the filter with the error mask makes `candump` log error frames as well as data frames). Capture on `can1`, not on `can0`: a local transmit echo does not establish external reception. If `can0` is also captured, treat it as the echo.
5. Start the GUI on the simulator host: `ecu-simulator-gui --port 8766`, and open the printed address. Use the **Car** panel: platform `TOYOTA_COROLLA`, bus `pt`, generated drive, interface `can0`. Write down the Check chips and the bus-load estimate against the bitrate, and the message count. On `vcan0` the Corolla lists 58 messages and transmits 57 (`PCS_HUD` is skipped), and `HONDA_CIVIC_BOSCH` lists 50 and transmits 47 (`KINEMATICS`, `KINEMATICS_ALT` and `BRAKE_HOLD` are skipped), so the receiver should not be expected to show the skipped ones.
6. Press **Play**. The transmission review dialog opens. Write down the identifiers it reports as already seen on the bus (none are expected) and confirm. Let the run go for at least 60 seconds, which is about 5,000 frames of the busiest message. Write down the live panel's bus load seen, bus errors, bus-off and tx dropped (kernel) readings, then press **Stop**.
7. Stop the capture with Ctrl-C. Record `ip -details -statistics link show can0` and `can1` again, after.
8. Count the frames received per identifier and the error frames, and compute the busiest message's median, p95 and maximum period and the bus load, with the first script below: `python3 bench_stats.py receiver-toyota.log 500000`.
9. Check the same log with opendbc's `CANParser`, using the second script below in a virtual environment that has opendbc: `python opendbc_receiver.py receiver-toyota.log toyota_new_mc_pt_generated`. For Honda, use the DBC name `honda_civic_hatchback_ex_2017_can_generated`.
10. Compare the bus-load estimate from step 5 with the figures the script computes. The estimate counts worst-case stuff bits. The script prints the load without stuff bits and with worst-case stuff bits, and the real load lies between them over a long enough window.

### Results

All fields are empty until someone runs the procedure.

| Run | |
| --- | --- |
| Date | |
| Person | |
| Host (make, model, distribution) | |
| Kernel (`uname -r`) | |
| Commit tested (`git rev-parse HEAD`), tree clean | |
| Simulator adapter (make, model, USB identity, driver) | |
| Simulator adapter firmware (as reported by the device, or "not recorded") | |
| Simulator interface settings (`ip -details link show can0`: bitrate, sample point, restart-ms, flags including ECHO) | |
| Receiver adapter and interface name | |
| Receiver mode (active, not listen-only) and bitrate | |
| Termination (how verified) | |
| opendbc revision and Python used for the parser | |

| Measurement | Toyota (TOYOTA_COROLLA, pt) | Honda (HONDA_CIVIC_BOSCH, pt) | Expected by the design |
| --- | --- | --- | --- |
| Run length (s) | | | none set (this record asks for at least 60) |
| Messages transmitted (Check step) and bus-load estimate (%) | | | none set |
| Frames received per identifier (attach the script's table) | | | none set; compare with the Check step's message list |
| Busiest message (name, identifier, set period in ms) | | | none set |
| Busiest message period at the receiver: median (ms) | | | none set |
| Busiest message period at the receiver: p95 (ms) | | | none set |
| Busiest message period at the receiver: maximum (ms) | | | none set |
| Simulator adapter counters before: re-started, bus-errors, arbit-lost, error-warn, error-pass, bus-off; TX packets, errors, dropped | | | none set |
| Simulator adapter counters after (same fields) | | | none set |
| Bus-off count (after minus before) | | | 0 |
| Receiver adapter counters before and after | | | none set |
| Live panel at Stop: bus load seen, bus errors, bus-off, tx dropped (kernel) | | | none set |
| Bus load: estimate in the GUI, receiver without stuff bits, receiver with worst-case stuffing (%) | | | none set; the receiver's two figures bracket the real load |
| Frames per identifier on `can0` (echo) against `can1`, if both were captured | | | none set |
| Error frames at the receiver (the script's count) | | | 0 (this record's expectation; the design names only bus-off) |
| opendbc `CANParser`: messages seen, frames accepted, frames rejected | | | valid (no rejected frames) |
| Anomalies (errors, review conflicts, banners, restarts) | | | none set |

| Verdict | |
| --- | --- |
| Result as recorded by the person who ran it | |
| Judged by the owner | |

### Analysis scripts

Save each script below under the name given before it. The two were run for this record on `candump -L` captures of the Toyota and Honda demos on `vcan0` (not bench results). On the 2.56 s Toyota capture the first printed `1853 frames, 57 identifiers` and `busiest 0xAA: median 12.288 ms, p95 12.603 ms, max 13.533 ms`. Use a window of at least 60 s for the load figures: on a short window the frames at its edges weigh too much. The second, with opendbc installed (`uv pip install --python <venv python> -e <opendbc checkout at af54130d51111197d34384e7dad9ace229b724e4>`, which was run in a fresh virtual environment for this record), printed `13 messages with a counter or checksum seen, 0 frames rejected` for the Toyota capture and `47 messages ... 0 frames rejected` for a 7.5 s Honda capture. With one checksum bit flipped in the Toyota log it printed `0xB4 SPEED: 25 of 26 frames accepted` and exited with status 1. The error-mask form of `candump` was checked on a private `vcan0` with `cansend` (three data frames, one remote frame and the error frame `20000004#0000000000000000`). The log held lines such as `(1795421725.006386) vcan0 20000004#0000000000000000`, and the first script printed `3 frames, 2 identifiers, 0.00 s, 1 error frames`. Adding one such line to the Toyota capture changed only the error count.

`bench_stats.py` (standard library only; `candump -L` logs; the argument after the log is the bitrate):

```python
import statistics
import sys
from collections import defaultdict
from itertools import pairwise

log, bitrate = sys.argv[1], int(sys.argv[2])
times: dict[int, list[float]] = defaultdict(list)
extended: dict[int, bool] = {}
errors = 0
unstuffed = worst = 0
first = last = None
for line in open(log):
    parts = line.split()
    if len(parts) != 3 or "#" not in parts[2]:
        continue
    stamp = float(parts[0].strip("()"))
    ident, data = parts[2].split("#")
    number = int(ident, 16)
    if number & 0x20000000 and len(ident) == 8:  # an error frame
        errors += 1
        continue
    if data.startswith("R"):  # a remote frame
        continue
    key, size, wide = number & 0x1FFFFFFF, len(data) // 2, len(ident) == 8
    times[key].append(stamp)
    extended[key] = wide
    base = (54 if wide else 34) + 8 * size
    unstuffed += base + 13
    worst += base + 13 + (base - 1) // 4
    first = stamp if first is None else first
    last = stamp
if not times:
    sys.exit("no frames in the log")
seconds = last - first
print(f"{sum(len(t) for t in times.values())} frames, {len(times)} identifiers, {seconds:.2f} s, {errors} error frames")
for key in sorted(times):
    print(f"  0x{key:X}{' (extended)' if extended[key] else ''}: {len(times[key])} frames")
busiest = max(times, key=lambda key: len(times[key]))
intervals = [(b - a) * 1000 for a, b in pairwise(times[busiest])]
if len(intervals) >= 2:
    print(
        f"busiest 0x{busiest:X}: median {statistics.median(intervals):.3f} ms, "
        f"p95 {statistics.quantiles(intervals, n=100, method='inclusive')[94]:.3f} ms, max {max(intervals):.3f} ms"
    )
else:
    print(f"busiest 0x{busiest:X}: too few frames for a period statistic")
if seconds > 0:
    print(
        f"bus load at {bitrate} bit/s: {100 * unstuffed / seconds / bitrate:.1f}% without stuff bits, "
        f"{100 * worst / seconds / bitrate:.1f}% with worst-case stuffing"
    )
```

`opendbc_receiver.py` (needs opendbc; the arguments are the log and opendbc's DBC name):

```python
import sys
from collections import defaultdict

from opendbc.can.dbc import DBC, SignalType
from opendbc.can.parser import CANParser

log, dbc_name = sys.argv[1], sys.argv[2]
dbc = DBC(dbc_name)
checked = [
    address
    for address, message in dbc.addr_to_msg.items()
    if any(s.calc_checksum or s.type == SignalType.COUNTER for s in message.sigs.values())
]
parser = CANParser(dbc_name, [(address, 0) for address in checked], bus=0)
seen: dict[int, int] = defaultdict(int)
accepted: dict[int, int] = defaultdict(int)
for line in open(log):
    parts = line.split()
    if len(parts) != 3 or "#" not in parts[2] or parts[2].split("#")[1].startswith("R"):
        continue
    ident, data = parts[2].split("#")
    address = int(ident, 16) & 0x1FFFFFFF
    if address not in checked or int(ident, 16) & 0x20000000 and len(ident) == 8:
        continue
    stamp = int(float(parts[0].strip("()")) * 1e9)
    seen[address] += 1
    accepted[address] += address in parser.update([(stamp, [(address, bytes.fromhex(data), 0)])])
bad = 0
for address in sorted(seen):
    name = dbc.addr_to_msg[address].name
    print(f"0x{address:X} {name}: {accepted[address]} of {seen[address]} frames accepted")
    bad += seen[address] - accepted[address]
print(f"{len(seen)} messages with a counter or checksum seen, {bad} frames rejected")
sys.exit(1 if bad else 0)
```

The second script registers every message of the DBC that has a counter or checksum signal and counts the frames of those that appear in the log. The parser starts with a previous counter of 0, so a log that begins mid-run counts one counter miss for each message and still passes. With the error mask from step 4 (`,0:0,#FFFFFFFF`), `candump -L` logs an error frame as a line whose identifier has the `0x20000000` flag and eight hex digits, for example `20000004#0000000000000000`. `bench_stats.py` counts those lines and leaves them out of the per-identifier table and the load figures; this script skips them. Whether an adapter reports error frames to the socket depends on its driver, so a count of 0 means none were reported to the capture. The adapter's error counters from steps 3 and 7 are the other evidence.
