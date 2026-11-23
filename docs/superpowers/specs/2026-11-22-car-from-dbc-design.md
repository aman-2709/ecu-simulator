# Run a whole car from a DBC (traffic first)

Status: draft for approval. Part 1 of a larger plan (see "Where this sits"). Written 2026-11-22.

## Intent

Two groups use this: engineers running it on their own vehicle networks (own DBC, own checksum rules), and people who send data so the cloud pipeline can be tested. Test engineers here simulate vehicles for internal testing. They want to pick a car (or upload a DBC), press Play, and get believable, repeating bus traffic on `vcan0` or a bench `can0`, with no per-message setup. Receivers that validate counters and checksums must accept the traffic, so a looped car does not trip them.

Today the DBC workflow is manual and per message: the operator adds messages one at a time with fixed values. A message containing a counter or checksum signal is refused for transmission (`dbc.py` `_INTEGRITY`, "Counter/checksum generation is unsupported"). Only one car, a hand-pinned Toyota wheel-speed publisher, runs end to end. This design adds a whole-car run beside that panel, which stays as it is.

The owner's wording: "we upload dbc file and then run play simulator, it should run the demo", for Toyota, Honda and Hyundai first. Traffic comes first; ECUs answering diagnostic requests as that car comes second.

## Where this sits

The larger plan has five parts, brainstormed one at a time: (1) this design, (2) DTC upload (catalog and fault state), (3) a J1939 ECU side (DM1, address claim, multi-frame transport), (4) the site shell (landing, Learn, Docs, UI polish), (5) Learn content. Part 1b, ECUs answering as the chosen car, follows this design and is not designed here. Pushing data to the edatx cloud pipeline is parked; the output stays plain SocketCAN so it can be added later.

## Goals

1. One action from a car or a DBC to running traffic: every transmittable message in the chosen DBC goes out at a stated period, with repeatable values, looping forever.
2. Valid counters and checksums for Toyota, Honda and Hyundai (classic CAN) platforms, byte-identical to opendbc's reference packer.
3. Every message reports where its period came from and whether its integrity fields are valid, counter-only, or unsupported. Nothing is presented as better than it is.
4. A recorded log, when one exists, can be played as recorded (the existing replay path) or can supply measured periods to a generated run.
5. A dev-only extractor exports opendbc's car catalog in one pass: platform to DBC map, ECU addresses, firmware strings and firmware queries. Part 1b consumes the last three; this design does not.
6. The run does not depend on opendbc, `pycapnp`, `numpy` or `pycryptodome` at runtime.
7. The same run works on virtual CAN (`vcan0`) and on a physical SocketCAN interface (`can0`), and the physical case is an acceptance gate, not an afterthought (see "Virtual and real CAN").

## Non-goals

- ECU answers (1b), DTC upload, J1939 ECUs, the site shell, Learn content.
- CAN FD (the publisher is Classical CAN). That excludes Hyundai CAN FD platforms (`hyundai_canfd_generated`) and `honda_common_canfd`.
- More than one bus per run. A car such as the Toyota Corolla uses a powertrain DBC plus a radar/ADAS DBC; a run picks one bus.
- Brands other than the three in the catalog. Their DBCs can be uploaded and played counter-only, or with a rule file.
- AUTOSAR E2E profiles beyond 1 and 2, and executing uploaded code.
- SecOC authenticators (Toyota `toyota_secoc_pt_generated`): they need per-car keys, so those messages are "unsupported".
- A browser-only engine or public hosting. The Python engine runs the simulator.

## Anti-goals

- A message labelled "valid" whose bytes differ from opendbc's packer for the same inputs.
- Silent downgrades. Counter-only messages and assumed periods are always labelled, in the UI and in the trace metadata.
- Breaking existing profiles. `ice_default` and the characterization tests keep their bytes; the pinned wheel-speed profile keeps its SHA-256 guard in `restbus.py`.
- Calling generated traffic a recording of a real car. This project already separates evidence labels and provenance; generated drives are labelled as generated.

## Constraints

- Python 3.12 or newer, Classical CAN only, one SocketCAN interface per run, DBC at most 5 MiB (existing limits).
- Scenario signals are pure functions of elapsed time, with no randomness (existing rule).
- `restbus` and `dbc` are mutually exclusive in a profile today; a car run joins that choice.
- Physical bench starts keep the existing transmission review.
- opendbc is MIT licensed; ported code and data keep the notice and the upstream revision.
- opendbc DBCs carry no message periods. The source and generator DBCs I sampled (Toyota, Honda, Hyundai) contain zero `BA_` attributes, so no `GenMsgCycleTime` exists.

## Approaches considered

- **A. Extract once, ship data, port the checksums (chosen).** A one-time extractor writes a catalog and generated DBCs; runtime uses our own packer with ported checksum functions, tested against opendbc's packer.
- **B. opendbc as an optional runtime extra.** Every checksum works on day one, but it limits Python to 3.11 or 3.12, pulls `pycapnp`, and `can/dbc.py` imports every brand module at load, so an upstream change could break the simulator. Rejected for bench-machine friction and upstream coupling.
- **C. Keep the current adapter (no counters or checksums).** Not chosen: looped traffic fails on any receiver that validates integrity fields.

The owner picked A and the brand order Toyota, Honda, Hyundai.

## Design

### Extractor (dev-only)

A script, run on a maintainer's machine against a pinned opendbc checkout, writes:

- `cars.json` in `src/ecu_simulator/_opendbc/`: per brand, per platform: id, display names, bus to DBC map (for example powertrain `toyota_new_mc_pt_generated`, radar `toyota_adas`), and the integrity rule name for each DBC. Also per platform the ECU list (role, request address, sub-address if any, firmware strings) and the firmware queries from `fw_query_definitions.py`. The last two are stored for 1b and unused here.
- The generated DBC files for the three brands. opendbc builds `*_generated` DBCs in memory from `dbc/generator`, so copying `opendbc/dbc/` is not enough.
- The upstream revision, recorded in the catalog and the ported modules: opendbc `master` at `af54130d51111197d34384e7dad9ace229b724e4`. The revision the wheel-speed adapter uses (`79ddcc7…`) has no classic-CAN Hyundai rule, so it cannot be the source. That adapter, its DBC and its SHA-256 guard in `restbus.py` stay exactly as they are.

The extractor needs opendbc installed, so it lives outside the runtime package and is the only code that imports it.

### Integrity engine

A new module holds a counter state per message and a registry of checksum functions: Toyota, Honda and Hyundai classic CAN, ported from `toyotacan.py`, `hondacan.py` and `hyundaican.py`. opendbc selects a rule by DBC file name prefix (`get_checksum_state`: `toyota_`/`lexus_`, `honda_`/`acura_`, `hyundai_can_generated`) and finds the fields by signal name (`CHECKSUM`, `COUNTER`, plus Hyundai's named signals and one counter split across two signals on `0x386`). Our catalog records the rule per DBC hash, so an exact-hash match needs no name guessing.

Per message, the state is one of:

- **valid**: rule known; counter advances every transmission and wraps at its maximum; checksum computed after all other signals are set.
- **counter-only**: no rule for this DBC; counter fields advance, checksum fields stay zero. Labelled "checksum not computed" and included in a run by default, since dashboards and loggers do not validate.
- **unsupported**: layout the packer cannot produce (CAN FD, SecOC). Not transmitted; listed with the reason.

Hyundai classic CAN is the weakest of the three. opendbc's packer computes the rule only for the signals `hyundai_setup_signal` names (a 4-bit `Checksum`, `CheckSum_TCS3`, `CR_VSM_ChkSum`, the `WHL_SPD_*` pair, and the matching alive counters), with address special cases for `0x386` (counter and checksum each split across two signals) and `0x394`. opendbc's safety code lists counters and checksums on `0x260`, `0x386`, `0x394`, `0x421` and `0x4F1`, and notes that older Hyundai models carry none on `0x386` and `0x394`. opendbc's own parser sets `ignore_checksum` and `ignore_counter` for this DBC. So for Hyundai, "valid" means byte-identical to opendbc's packer for those named signals. It is not checked by opendbc's parser or against a real car, and a given platform may not use the fields at all. The UI says this in the integrity detail. Any other Hyundai message with checksum-like fields is counter-only.

For an uploaded DBC whose hash is not in the catalog, the operator may pick one of the three rules. Nothing is guessed from the file name. Without a rule, only opendbc's convention names are treated as integrity fields: `COUNTER` (advanced) and `CHECKSUM` (left at zero, labelled). Other names matching the existing `_INTEGRITY` pattern are flagged "possible integrity field, held at its initial value" and are not generated.

### Bring-your-own integrity rule

Networks that are not Toyota, Honda or Hyundai need their own counters and checksums. An uploaded DBC can carry a rule file (YAML, stored beside the DBC by hash, validated on upload) that names, per message or per signal-name pattern, a counter field, a checksum field, and an algorithm from a fixed set:

- `xor`, `sum8` and `nibble-sum` over a stated byte range;
- `crc8` and `crc16` with polynomial, initial value, final XOR and reflection;
- an optional constant mixed in (a data ID) and a counter wrap;
- AUTOSAR E2E profiles 1 and 2 as named presets, since OEMs refer to them by name (the builder works from the published AUTOSAR E2E specification and confirms its access terms).

It is declarative only. Uploaded code is never executed: the GUI server is reachable by anyone on the bench network, and OEM algorithms are their intellectual property. A Python hook loaded from a path the operator names on the server machine is a possible follow-on, not part of this part.

Such messages show integrity `valid (user rule)`. That means computed as the supplied rule says, not checked against a car. Each algorithm is tested against published CRC catalogue check values (for example CRC-8/SAE-J1850 and CRC-8/AUTOSAR over the ASCII string `123456789`) before any message uses it.

### Encoding

Signal values are encoded by the existing cantools path (`ImportedDb.encode`), which already handles signed, float and multiplexed signals. Counter and checksum fields are then written into the final bytes by the integrity engine, using helpers ported from opendbc's `set_value` and `get_raw_value`. The checksum is therefore always valid for the frame actually sent, and the GUI preview shows the wire bytes. "Byte-identical to opendbc" means: for the same raw field values, our counter and checksum bytes equal opendbc's packer's. Physical-to-raw rounding is outside that claim: cantools and opendbc round exact half-LSB ties differently (opendbc uses `floor(x + 0.5)`), so the oracle compares at raw-value level.

### Whole-car run

A car run selects a catalog platform and bus (or an uploaded DBC), a source (generated drive or recorded log) and an interface. It expands to the existing cyclic-publisher schedule for every transmittable message.

Periods, in order of priority, each shown with its source:

1. An operator override.
2. Measured from a recorded log, when one is attached.
3. Declared in the DBC (`BA_ "GenMsgCycleTime"`). opendbc DBCs have none, but customer DBCs often do, and `ImportedDb` already exposes it.
4. Frequencies in opendbc's per-brand `carstate.py` parser tables, if the extractor can read them reliably.
5. A 100 ms default, labelled "assumed".

The existing profile schema floors a period at 10 ms. A faster source is clamped to 10 ms and labelled "clamped".

A message that cannot be transmitted is skipped with its reason and listed; the run continues. This covers CAN FD, SecOC, a layout error, a collision with the simulated ECU's own diagnostic IDs, and an encode failure at a signal's limits. The run fails only if no message is transmittable or the interface cannot be opened.

The existing manual DBC messages panel is unchanged: it keeps refusing counter and checksum messages. Unifying the two paths is a follow-on.

### Virtual and real CAN

One code path serves both; the interface name is the only difference. What changes on a physical bus:

- **Bus load.** A Classical CAN frame with 8 data bytes is about 110 to 130 bits. 1,000 frames per second is therefore roughly 22 to 26% of a 500 kbit/s bus. The run reads the interface's configured bitrate (the simulator never sets it; `scripts/setup_can.sh` does) and shows the estimated load before Play. On a physical interface it refuses to start above a stated ceiling (the builder proposes it, around 70%) and reports which messages push it over. On `vcan0` the estimate is informational.
- **Acknowledgement.** A lone transmitter on a real bus gets no ACK and goes error-passive, then bus-off. The run needs a second active node, and the existing physical-bench transmission review stays. Bus-off and `ENOBUFS` on send are surfaced as run errors, not ignored.
- **Timing.** Measured jitter so far is one 20 ms message on `vcan0` (median 20.224 ms, p95 20.501 ms, max 21.427 ms). That says nothing about 100-plus messages on a physical adapter. If userspace scheduling cannot hold the periods there, the fallback is the kernel's SocketCAN broadcast manager (`CAN_BCM`) for cyclic sends; the builder decides after measuring.
- **Never a live vehicle.** Transmission onto a bus that carries a real vehicle is outside this tool's purpose; the existing review wording stays.

### Generated drive

Deterministic, built on the existing generators (`ramp`, `sine`, `stepped`, `sequence`, `timeline`). Signals recognised by name and unit (vehicle speed, wheel speeds, RPM, steering angle, pedals, gear, brake) follow a repeating drive pattern, for example 0, 40, 0 km/h in 10-second steps like the wheel-speed demo. Speed and RPM are taken from the existing `vehicle.speed` and `engine.rpm`, so when the ICE ECU is running, OBD `01 0D` and `01 0C` agree with the bus, as the wheel-speed demo already does. Steering, pedals, gear and brake have no vehicle-state signal today; they are time functions only, and `ice_default` and the characterization bytes stay unchanged. Every other signal holds the DBC initial value if present, otherwise zero, clamped to its range. Counters are packer state, so they stay continuous across the loop and there is no counter seam. Cumulative signals (odometer, trip) repeat with the loop; that is a known limit.

### Recorded log

The existing replay path (`gui/replay.py`), selected as the run's source. It sends recorded frames verbatim, so the integrity column reads "as recorded" and the existing limits stay: `vcan0` only, single-interface Classical CAN, under 16 MiB and 300,000 frames. A replayed loop restarts counters at the seam; a receiver that validates them will flag that. Re-stamping counters and checksums on replay is a follow-on.

Separately, a log can be attached to a generated run only to measure each message's period. No Toyota, Honda or Hyundai recording is bundled (only Kenworth J1939 samples), so this path is tested with a synthetic log until real ones are supplied.

### GUI

The owner chose **guided steps with a live data panel underneath**, from three wireframes (a one-page run console, guided steps, a library sidebar with a live view). Type, colour and component polish belong to the site shell (part 4); this fixes structure and behaviour. The panel sits on the Simulator tab next to the DBC messages panel, which is unchanged.

```
[ 1 Choose ✓ ]  [ 2 Check ● ]  [ 3 Run ]
  Toyota Corolla 2017-19      29 valid, 3 counter only,   Interface [can0 v]
  powertrain bus              2 skipped                   Bus load 24% of 500 kbit/s
  Source: generated drive     Rule [Toyota (catalog) v]   [Play]
                              [Upload rule file]
  Message          ID   Period            Integrity
  WHEEL_SPEEDS     0AA  20 ms  from log    valid
  GAS_PEDAL        2C1  100 ms assumed      counter only: checksum not computed
  ACC_CONTROL      343  -                   skipped: SecOC authenticator
-------------------------------------------------------------------------
Live data   bus load 24% | frames sent 48,212 | errors 0 | bus-off 0
  Message        Sending now         Period set/measured  Counter  Checksum
  WHEEL_SPEEDS   FL 40.0 km/h ...    20 / 20.2 ms            7     0x3C valid
```

**Step 1, Choose.** Brand and platform from the catalog, or upload a DBC; the bus when a car has more than one; the source, either a generated drive (with "attach log for timing") or a recorded log played as recorded. Once done it collapses to a one-line summary with a tick.

**Step 2, Check.** Summary chips (valid, counter only, skipped); the integrity rule, either the catalog's or an uploaded rule file; the message table with each message's ID, period with its source and an edit control, and integrity state with its reason. A long table shows the first rows and expands.

**Step 3, Run.** The interface, the estimated bus load against its configured bitrate (a physical interface above the ceiling refuses to start), and Play. While running the first two steps collapse to ticks and the step shows "Running on <interface>" with elapsed time, loop count and Stop. Start and Stop reuse the existing Start ECU and Live CAN flow.

**Live data panel.** Always below the steps. Greyed with "Starts when you press Play" until a run begins. While running it shows measured bus load, frames sent, error, bus-off and queue-full counts, and a table of each message's signal values as sent, period set versus measured, counter, and checksum with its integrity state, with pause and a filter. Measured periods come from the bus capture, not from the sender's own timer, so the column reveals real scheduling jitter; the builder decides how the panel reads the capture and the publisher state, and labels whichever it uses.

**Errors.** A banner states the cause and the next step: a send failure with no buffer space, bus-off, an interface that is down or missing. A send failure stops the run. Errors are never hidden.

The run shows the DBC SHA-256, the upstream revision and each message's integrity state.

## Verification

1. Oracle test (dev-only, skipped without opendbc): for every integrity-bearing message in the bundled DBCs, pack frames with opendbc's `CANPacker` and with ours across a full counter wrap, and compare bytes. Commit the generated golden vectors so the same check runs without opendbc.
2. Isolated `vcan` run, as in the wheel-speed spec: start a Toyota car from the GUI, capture independently, check IDs, DLCs, median period within 10% of the stated period, and counter continuity across the loop. With the ICE ECU running, send `01 0D` at a speed plateau and confirm it matches the bus wheel speed.
3. Independent receiver for Toyota and Honda: feed the capture to opendbc's `CANParser` (dev-only), which verifies their counters and checksums, and confirm it reports valid. It cannot check Hyundai classic, because opendbc's parser ignores that DBC's checksums; Hyundai rests on check 1 alone.
4. Regression: existing profile bytes and the characterization suite are unchanged.
5. Physical acceptance is a gate for this part, on the dedicated terminated bench (candleLight adapter at `can0`, 500 kbit/s, a second active node). Run a full Toyota car, then capture with a separate receiver and record: frames received per ID, period median, p95 and maximum for the busiest message, the adapter's error counters before and after, and `bus-off` count (expected zero). Repeat with the opendbc `CANParser` on the receiving side for Toyota and Honda. A local transmit echo does not establish external reception.
6. Bus-load guard: an over-ceiling car on a physical interface refuses to start and names the offending messages; the same car on `vcan0` starts.
7. Browser acceptance, added to the existing suite: on `vcan0`, walk the three steps for a Toyota car, then compare the live panel's counter and checksum cells and measured periods with an independent capture. Force a send failure (bring the interface down mid-run) and confirm the run stops and the banner names the cause.

## Left to the builder

- Profile shape for a car run (a new section versus expansion into `dbc.messages`), and the schema change.
- How to read frequency tables from `carstate.py` reliably, or to drop priority 2.
- Signal recognition rules for the drive pattern.
- The rule file schema and its E2E presets.
- Whether the publisher's per-message schedule on one asyncio loop sustains 100-plus messages, and what to do if it does not.
- Package-data size for the generated DBCs.

## Calls I made (correct any)

- One bus per run; the second DBC is a later addition.
- Hyundai in this part means classic CAN only. If most customer Hyundais are CAN FD, Hyundai coverage here is thin until a CAN FD publisher exists. Even on classic CAN its rule is checked against opendbc's packer only.
- Extractor pinned to opendbc master `af54130…`; the wheel-speed adapter stays on `79ddcc7…`.
- Cantools encodes signals; integrity is a post-pass over the final bytes. The oracle compares raw values, not physical ones.
- A recorded log is a source played as recorded; it can also supply periods to a generated run.
- Period priority: override, log, DBC `GenMsgCycleTime`, opendbc table, 100 ms "assumed"; faster than 10 ms is clamped.
- Non-transmittable messages are skipped with a reason; the run goes on.
- Counter-only messages are included by default, labelled, rather than skipped as they are today in the manual panel.
- The manual DBC messages panel is unchanged.
- Layout (the owner's pick): guided steps (Choose, Check, Run) with a live data panel underneath. The one-page console and the library sidebar were the alternatives.
- Speed and RPM follow `vehicle.speed` and `engine.rpm`, so OBD matches the bus; other signals are time functions.
- Own-network users get a declarative rule file (checksum and CRC building blocks plus E2E profiles 1 and 2), never uploaded code.
- Bundled DBCs: the existing two plus the three brands. Other brands upload.
- No runtime opendbc; the extractor and the oracle test are the only users.

## Changes during implementation

Where the built system differs from, or refines, what is written above. The text above is unchanged.

1. **Period source 4.** The frequencies come from opendbc's safety receive tables (`opendbc/safety/modes/*.h`), because the per-brand `carstate.py` tables no longer exist at the pinned revision. The values are approximate and ambiguous addresses are left out, so 16 messages get one (Toyota 3, Honda 5, Hyundai 8). The UI labels the source "opendbc safety table, approximate".
2. **Shipped DBCs.** The catalog DBCs are under `src/ecu_simulator/_opendbc/cars/`. 13 of the 21 needed normalising (malformed comment lines dropped, ids above 0x7FF flagged extended). Each `cars.json` entry records the source and shipped SHA-256 and what changed, and the extractor checks message and signal equality against opendbc's own parse.
3. **More than one checksum.** Hyundai 0x386 has two checksum signals (`WHL_SPD_Checksum_LSB` and `_MSB`) computed in sequence, so integrity plans and the oracle fixture hold a list of checksums. The fixture has four payload variants per layout (740 vectors) so that each checksum signal takes more than one value.
4. **Generated drive.** It excludes names with SET, REL, TGT, TARGET, TRGT, REQ, LIMIT, OFFSET, DECIMAL or CRUISE as whole words, and multiplexed members. The brake pedal is not animated, and gear and brake keep their DBC defaults.
5. **Encode failures.** Each driven signal is trial-encoded at its limits when the run is prepared, and a failing message is skipped. At run time an encode error stops the run with its error; nothing is resent silently.
6. **AUTOSAR E2E.** The facts differ from the assumptions above. Profile 1 hashes the Data ID first, its effective CRC start and final XOR are 0x00, its counter wraps at 14, and `nibble` mode feeds the low byte and then a zero byte. Profile 2's first protected frame carries counter 1 in AUTOSAR's reference, while this stamper starts at 0. The presets follow AUTOSAR PRS_E2EProtocol R22-11. Its disclaimer permits reproduction "for informational purposes only" and requires a licence for commercial exploitation. Whether that covers the presets is undecided and is for the owner.
7. **GUI routes.** The status poll (`GET /run`) carries only a car summary, and the full message list is `GET /car/run`. Rule and log uploads are parsed off the event loop, one rule parse at a time, with caps of 256 KiB for a rule file and 16 MiB or 300,000 frames for a log. A car run on a physical interface goes through the transmission review, which needed its own gate because the existing one looked only at `dbc` messages.
8. **Measured period.** The live panel shows the median of the last 100 intervals between a message's frames, taken from the capture's kernel timestamps, with the shortest and longest. The first build showed a mean, which equals the set period whatever the jitter.
9. **Bus-load check.** It treats only an interface in state STOPPED as down. BUS-OFF is not refused at start; the run surfaces it.
10. **AUTOSAR E2E presets removed.** The `e2e_p01` and `e2e_p02` presets (commit `17614d4`, described in item 6) were removed by the owner's decision of 2026-11-23, because the AUTOSAR licence question is not settled. Rule files offer only the generic algorithms (`xor`, `sum8`, `nibble_sum`, `crc8`, `crc16`), and a file that names a preset is refused like any unknown algorithm. This replaces the E2E profiles 1 and 2 in the sections above and item 6.
11. **aiohttp import guard.** `gui/car_routes.py` imports aiohttp, so the test that lists the modules allowed to import it (`tests/unit/test_gui_extra_is_optional.py::test_only_the_optional_server_modules_import_aiohttp`) was widened to include it, and a new test (`test_only_the_gui_server_imports_the_car_routes`) pins `gui/server.py` as the only module that references `car_routes`. The core run path still imports no GUI module, which is what decision 0010 section 4.1 protects. Accepted by the owner on 2026-11-23.
12. **Transmit burst.** The design left first-tick behaviour on a physical bus to measurement. The car run now spreads the first deadlines of each period's messages evenly across that period (an opt-in of the publisher that only the car run uses; restbus, plain and dbc profiles keep their schedule), by the owner's decision of 2026-11-23, because a whole car's first tick could overflow an adapter's 10-frame transmit queue. Raising `txqueuelen` remains the documented remedy if a queue still overflows.
13. **Item 4, made precise.** The SET, REL, TGT, TARGET, TRGT, REQ, LIMIT, OFFSET, DECIMAL and CRUISE exclusion applies to speed and engine-speed signals only; steering and pedal signals are not checked for those words.
14. **Run metadata and working copy.** The car metadata, the status summary and `GET /car/run` name the bus (null for an uploaded DBC). The Car panel's working-copy routes (draft, check, review) refuse a working copy above 256 KiB.

Verification item 5 (physical acceptance) has not been run. [docs/validation/car-from-dbc.md](../../validation/car-from-dbc.md) holds the evidence from `vcan0` and the procedure with empty result fields.
