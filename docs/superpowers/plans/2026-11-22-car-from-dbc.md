# Run a whole car from a DBC: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pick a Toyota, Honda or Hyundai car (or upload any DBC), press Play, and get repeating bus traffic with valid counters and checksums on `vcan0` or a physical `can0`, with a guided-steps GUI and a live data panel.

**Architecture:** A dev-only extractor exports opendbc's car catalog and generated DBCs as committed data. A new `ecu_simulator.car` package holds the integrity engine (ported checksum rules, counters, declarative user rule files), period resolution, a generated drive, a bus-load guard and `prepare_car`, which expands a car into the existing cyclic publisher. A `car:` profile section selects it; the GUI gets a Car panel with three steps and a live panel built on the existing `/capture` stream.

**Tech Stack:** Python 3.12, cantools 42 (existing encode path), pydantic 2, aiohttp (GUI), vanilla JS, pytest, Playwright (existing browser suite). opendbc is used only by dev scripts and dev-only tests.

**Spec:** `docs/superpowers/specs/2026-11-22-car-from-dbc-design.md`

**Branch:** work on a new branch `car-from-dbc` created from `socketcan-control`. Never push, merge or rewrite history.

## Global Constraints

- Python `>=3.12`; Classical CAN only; one SocketCAN interface per run; DBC at most 5 MiB.
- No runtime dependency on opendbc, `pycapnp`, `numpy` or `pycryptodome`; only the extractor, the vector generator and dev-only tests import opendbc.
- Extractor source: opendbc `master` at `af54130d51111197d34384e7dad9ace229b724e4`. The wheel-speed adapter (`79ddcc7bb72d557fc5b20d038c0fad9c9d423446`), its DBC and its SHA-256 guard in `restbus.py` stay untouched.
- Scenario signals are pure functions of elapsed time; no randomness.
- A period floor of 10 ms (existing schema); a faster source is clamped and labelled `clamped`. Default period 100 ms labelled `assumed`.
- Period priority: operator override, then measured from a log, then DBC `GenMsgCycleTime`, then opendbc table, then 100 ms assumed.
- Bus-load ceiling on a physical interface: 70%, worst-case estimate; `vcan` never refuses.
- Rule files are declarative YAML only; uploaded code is never executed.
- The manual DBC messages panel (`dbc.js`, `/dbc/*`) keeps its behaviour, including refusing counter and checksum messages.
- Recorded-log replay stays as is (`vcan0` only, Classical CAN, under 16 MiB and 300,000 frames).
- No `shortcut:` or `ponytail:` comments in code. Commit subjects are `area: subject`, no AI trailer. Ruff line length 120; `ecu_simulator.car` and `ecu_simulator.car.*` join the strict mypy overrides.
- Run tests with `.venv/bin/python -m pytest`; lint with `.venv/bin/ruff check src tests scripts`; types with `.venv/bin/mypy`.

## Review Focus

- A counter split across two signals, or a checksum in a non-byte-aligned field, on a message shorter than 8 bytes: checksum covers the actual length. Pinned in Task 6.
- A hostile or malformed rule file (unknown algorithm, polynomial wider than the field, `covers` index past the message, a glob that matches nothing, a file over 256 KiB): refused with the path of the problem, never executed or partially applied. Pinned in Task 10.
- An interface with no bitrate (`vcan`), one that is down, or one that does not exist: the estimate is informational on `vcan` and a clear refusal on physical. Pinned in Task 9.
- A log with one frame per ID, remote frames, an extended ID colliding with a standard ID, or equal timestamps: no period measured rather than a wrong one. Pinned in Task 7.
- A 150-message car at 10 ms: refused on physical by the load guard, and the publisher still keeps the median period within 10% on `vcan`. Pinned in Task 13.

---

### Task 1: Extractor and committed catalog data

**Files:**
- Create: `scripts/extract_opendbc.py` (dev-only; never imported by the package)
- Create (generated, committed): `src/ecu_simulator/_opendbc/cars.json` and `src/ecu_simulator/_opendbc/cars/<dbc name>.dbc` for every DBC the three brands' classic-CAN platforms reference. The extractor writes only `cars.json` and files under `cars/`: `_opendbc/` already holds the wheel-speed adapter's pinned `toyota_nodsu_pt_generated.dbc` and `bmw_e9x_e8x.dbc`, which must never be overwritten (master's file of the same name may differ and would break the SHA-256 guard in `restbus.py`).
- Test: `tests/unit/test_car_catalog_data.py`

**Interfaces:**
- Produces: `cars.json` with keys `revision: str`, `dbcs: {name: {file, sha256, rule: "toyota"|"honda"|"hyundai_can"|null}}` (`file` is relative to `_opendbc/`, for example `cars/toyota_new_mc_pt_generated.dbc`), `platforms: {ID: {brand, names: [str], buses: {bus: dbc name}, ecus: [{role, address, subaddress|null, fw: [hex]}]}}`, `fw_queries: {brand: [{request: [hex], response: [hex], whitelist: [role], rx_offset, bus}]}`, `rates_hz: {brand: {MESSAGE: hz}}`, `skipped: {ID: reason}`. `ecus` and `fw_queries` are stored for the follow-on part and read by nothing here.

- [ ] **Step 1: Write the failing data test.** In `test_car_catalog_data.py`: `test_revision_is_pinned` (`revision == "af54130d51111197d34384e7dad9ace229b724e4"`); `test_every_dbc_file_matches_its_sha256`; `test_every_platform_bus_names_a_listed_dbc`; `test_only_three_brands`; `test_rules_assigned_by_name_prefix` (`toyota_`/`lexus_` to `toyota`, `honda_`/`acura_` to `honda`, `hyundai_can_generated` to `hyundai_can`, everything else `null`); `test_canfd_platforms_are_skipped_not_listed` (`HYUNDAI_*` platforms using `hyundai_canfd_generated` appear in `skipped` with reason `"CAN FD"` and not in `platforms`); `test_no_shipped_dbc_has_a_message_over_8_bytes`; `test_toyota_has_wheel_speeds_rate` (`rates_hz["toyota"]["WHEEL_SPEEDS"] > 0`); `test_total_data_under_5_mib`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_car_catalog_data.py -q`. Expected: FAIL (no `cars.json`).
- [ ] **Step 3: Implement `scripts/extract_opendbc.py`** with `main(argv) -> int` taking `--opendbc <checkout>` and `--out <dir>`. It imports opendbc from the checkout, iterates each brand's `CAR` platforms (read `opendbc/car/__init__.py` for the `Platforms` API), reads each platform's `dbc_dict`, obtains DBC text via `opendbc.get_generated_dbcs()` for `*_generated` names and `opendbc/dbc/<name>.dbc` otherwise, parses every DBC with `ecu_simulator.dbc.ImportedDb` to detect messages over 8 bytes, and writes the outputs above. `rates_hz` is a best-effort regex for `("MESSAGE", <number>)` pairs in `opendbc/car/<brand>/carstate.py`. Scratch setup (outside the repo): clone opendbc in full (a shallow clone cannot check out the pinned commit), `git checkout af54130d51111197d34384e7dad9ace229b724e4`, `uv venv --python 3.12 <scratch>/venv`, `uv pip install --python <scratch>/venv/bin/python -e <checkout> -e <this repo>`; run the script with that interpreter. A CAN FD bus (a DBC with a message over 8 bytes, or a `*canfd*` DBC name) is dropped from its platform's `buses`; a platform left with no bus goes to `skipped` with reason `"CAN FD"`; a dropped bus's DBC is not shipped unless a kept bus uses it. If the output exceeds 5 MiB, stop and report the sizes instead of committing.
- [ ] **Step 4: Run the extractor, then the test.** Expected: PASS. Spot-check `TOYOTA_COROLLA` has buses `pt` and `radar`.
- [ ] **Step 5: Commit** `git add scripts/extract_opendbc.py src/ecu_simulator/_opendbc tests/unit/test_car_catalog_data.py && git commit -m "car: extract opendbc catalog and generated DBCs"`.

### Task 2: Catalog API and bundled DBC lookup

**Files:**
- Create: `src/ecu_simulator/car/__init__.py`, `src/ecu_simulator/car/catalog.py`
- Modify: `src/ecu_simulator/dbc.py` (`DbcStore.load` fallback), `pyproject.toml` (strict mypy overrides)
- Test: `tests/unit/test_car_catalog.py`

**Interfaces:**
- Produces: `DbcEntry(name, file, sha256, rule)`, `Platform(id, brand, names: tuple[str, ...], buses: Mapping[str, str])`, `Catalog(revision, dbcs, platforms, skipped, rates_hz)`, `load_catalog() -> Catalog` (cached), `bundled_dbc_path(sha256: str) -> Path | None`, `rule_for_sha(sha256: str) -> str | None`.

- [ ] **Step 1: Failing tests.** `test_catalog_has_corolla_with_two_buses`; `test_dbcstore_loads_bundled_car_dbc_without_import` (`DbcStore(tmp_path).load(entry.sha256)` returns an `ImportedDb`); `test_unknown_sha_has_no_rule_and_no_path`; `test_existing_bmw_bundle_still_loads`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_car_catalog.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** the dataclasses and loader in `catalog.py` reading `cars.json` through `importlib.resources`; make `DbcStore.load` ask `bundled_dbc_path` before raising "Missing DBC artifact". Add the two `ecu_simulator.car` module patterns to the strict mypy override list.
- [ ] **Step 4: Run** the new tests, then `tests/unit/test_dbc_import.py`. Expected: PASS.
- [ ] **Step 5: Commit** `car: catalog API and bundled DBC lookup`.

### Task 3: Oracle vectors (dev-only)

**Files:**
- Create: `scripts/gen_integrity_vectors.py` (dev-only), `tests/fixtures/car/integrity_vectors.json` (generated, committed)
- Test: `tests/unit/test_car_vectors_fixture.py`

**Interfaces:**
- Produces: a JSON list of vectors `{rule, dbc, address, extended, message, size, base_hex, counters: [{signal, start, size, little_endian, shift, max}], checksums: [{signal, start, size, little_endian}], frames_hex: [str]}` where `checksums` is empty when the message has none and lists every checksum signal in DBC signal order (Hyundai `0x386` has two, `WHL_SPD_Checksum_LSB` and `WHL_SPD_Checksum_MSB`), `base_hex` has every counter and checksum field zero, and `frames_hex[k]` is opendbc's `CANPacker.pack` output for the k-th call (counter value `k mod (max + 1)`), with `max + 3` frames per vector (3 when the message has no counter).

- [ ] **Step 1: Failing test.** `test_fixture_covers_each_rule` (all of `toyota`, `honda`, `hyundai_can` present); `test_hyundai_split_counter_present` (a vector for address `0x386` with two counters with shifts 0 and 2 and two checksums); `test_each_vector_wraps_the_counter` (`len(frames_hex) == (counters[0].max if counters else 0) + 3`); `test_vectors_deduplicated_by_layout`.
- [ ] **Step 2: Run** the test. Expected: FAIL (no fixture).
- [ ] **Step 3: Implement the script** (`main(argv)`, `--opendbc`, `--out`): for every integrity-bearing message of every shipped DBC, use opendbc's `CANPacker(dbc_name)`; fill each non-integrity signal with a fixed-seed raw value (`random.Random(address)`), converted to a physical value as `raw * factor + offset` so rounding never hits a tie; pack `max + 3` times; deduplicate vectors with the same rule, address and field layout. The script documents the same scratch-venv recipe as Task 1 in its `--help`.
- [ ] **Step 4: Generate the fixture and run the test.** Expected: PASS.
- [ ] **Step 5: Commit** `car: oracle vectors from opendbc packer`.

### Task 4: Bit fields and ported checksum rules

**Files:**
- Create: `src/ecu_simulator/car/bits.py`, `src/ecu_simulator/car/checksums.py`
- Test: `tests/unit/test_car_checksums.py`

**Interfaces:**
- Consumes: `ecu_simulator._opendbc.Signal` and `set_value`.
- Produces: `BitField(start: int, size: int, little_endian: bool)` (frozen dataclass) with `read(data: bytes | bytearray) -> int` and `write(data: bytearray, value: int) -> None` (masks to `size`; DBC bit numbering, results identical to opendbc's `Signal.get_raw_value` and `set_value`); `ChecksumFn = Callable[[int, bytearray, BitField, str], int]` (arbitration ID, frame with the checksum field zero, checksum field, signal name; returns the field value); `toyota_checksum`, `honda_checksum`, `hyundai_can_checksum`; `RULES: Mapping[str, ChecksumFn]` keyed `toyota`, `honda`, `hyundai_can`; `is_hyundai_checksum(name: str, size: int) -> bool` (true for `Checksum` of 4 bits, `CheckSum_TCS3`, `CR_VSM_ChkSum`, or a name starting `WHL_SPD_Checksum_`), `HYUNDAI_COUNTER_SIGNALS` (`AliveCounter`, `AliveCounterTCS`, `CR_VSM_Alive`, `CF_Clu_AliveCnt1`), `HYUNDAI_SPLIT_COUNTERS: Mapping[str, int]` (`WHL_SPD_AliveCounter_LSB` to shift 0, `WHL_SPD_AliveCounter_MSB` to shift 2).

- [ ] **Step 1: Failing tests.** `test_checksum_matches_oracle` parametrized over the fixture: zero every checksum field in `frames_hex[k]`, then for each entry of the vector's `checksums` in order call `RULES[rule](address, data, field, signal)` and write the result into `data` before the next call (opendbc's packer does the same), and assert the final frame equals `frames_hex[k]`. `test_bitfield_round_trip_big_and_little_endian`; `test_bitfield_matches_opendbc_signal` (compare with `_opendbc.Signal` built the way opendbc's DBC parser does); `test_hyundai_0x386_uses_signal_name_suffix`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_car_checksums.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** by porting the three functions exactly: `toyotacan.py` `toyota_checksum`, `hondacan.py` `honda_checksum`, `hyundaican.py` `hyundai_can_checksum` at the pinned revision; the Hyundai `0x386` and `0x394` special cases stay. Module docstrings name the upstream file, the revision and the MIT notice in `_opendbc/LICENSE`. `BitField` builds the msb/lsb pair the way opendbc's `DBC._parse_lines` does for big-endian signals.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `car: bit fields and ported checksum rules`.

### Task 5: ImportedDb exposes layout and integrity fields

**Files:**
- Modify: `src/ecu_simulator/dbc.py` (`_signal_info`, message info in `ImportedDb.__init__`, `ImportedDb.encode`)
- Test: `tests/unit/test_dbc_import.py` (add cases)

**Interfaces:**
- Produces: each signal dict gains `start: int`, `length: int`, `little_endian: bool`; each message dict gains `integrity_fields: list[str]` (the names the existing `_INTEGRITY` pattern matches) and `layout_issue: str | None` (the existing issues except the counter/checksum one); `ImportedDb.encode(name, values, *, integrity_ok: bool = False) -> bytes`. With `integrity_ok=True`, a message whose only issue is the counter/checksum one encodes (the fields stay at their defaults); every other issue still raises. `tx_issue` and the default `encode` behave exactly as before.

- [ ] **Step 1: Failing tests.** `test_signal_layout_is_exposed` (a Toyota catalog message's `COUNTER` has the DBC's start, length and endianness); `test_manual_path_still_refuses_counter_checksum_messages` (`tx_issue` set, `encode` raises); `test_car_encode_allows_integrity_only_messages` (`integrity_ok=True` returns bytes); `test_integrity_ok_does_not_bypass_layout_errors`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_dbc_import.py -q`. Expected: FAIL on the new cases.
- [ ] **Step 3: Implement** the additive keys and the keyword-only `integrity_ok`; do not change `issues` or `tx_issue`.
- [ ] **Step 4: Run** `tests/unit/test_dbc_import.py tests/unit/test_dbc_workflow.py tests/unit/test_dbc_runtime.py`. Expected: PASS.
- [ ] **Step 5: Commit** `dbc: expose signal layout and car-run encoding`.

### Task 6: Integrity engine

**Files:**
- Create: `src/ecu_simulator/car/integrity.py`
- Test: `tests/unit/test_car_integrity.py`

**Interfaces:**
- Consumes: `BitField`, `RULES`, Hyundai name sets (Task 4); message and signal dicts from `ImportedDb.message(name)` (Task 5).
- Produces: `IntegrityState` (`StrEnum`: `none`, `valid`, `counter_only`, `user_rule`, `unsupported`); `CounterField(signal: str, field: BitField, shift: int, max_value: int)`; `ChecksumField(signal: str, field: BitField, fn: ChecksumFn)`; `MessagePlan(address, extended, size, counters, checksums: tuple[ChecksumField, ...], state, detail | None, possible_fields: tuple[str, ...])`; `plan_message(message: Mapping[str, Any], *, rule: str | None) -> MessagePlan`; `Stamper(plan)` with `stamp(data: bytes) -> bytes` (copy; write each counter as `(counter >> shift)` masked to its field; zero every checksum field; then in DBC signal order call `fn(address, data, field, signal)` and write each result before the next call, as opendbc's packer does; then advance `counter = (counter + 1) % (counters[0].max_value + 1)`, when the message has counters).
- Rule semantics: checksum fields are the signals named `CHECKSUM` (rule `toyota`/`honda`) or those for which `is_hyundai_checksum(name, size)` is true (rule `hyundai_can`; `0x386` has two); counters are signals named `COUNTER`, or `HYUNDAI_COUNTER_SIGNALS` plus the split pair (max 15 for the pair, field maximum otherwise). With a rule: `valid` when a checksum field exists, `counter_only` when integrity-named fields exist without a rule function, `none` when the message has no integrity fields. Without a rule: only `COUNTER` is advanced and `CHECKSUM` stays zero, state `counter_only`, detail `"checksum not computed"`. Other names matching the existing `_INTEGRITY` pattern go to `possible_fields`, are held at their DBC default, and add `"possible integrity field held at its initial value"` to `detail`.

- [ ] **Step 1: Failing tests.** `test_stamper_matches_oracle_for_every_vector` (start from `base_hex`, call `stamp` `len(frames_hex)` times, assert each equals `frames_hex[k]`; the counter wraps); `test_hyundai_split_counter_0x386` (two counters and two checksums); `test_message_without_integrity_fields_is_identity` (state `none`); `test_no_rule_leaves_checksum_zero_and_is_labelled_counter_only`; `test_possible_integrity_names_are_reported_not_generated`; `test_short_dlc_message_checksum_covers_actual_length` (a 5-byte vector, if the fixture has none use a synthetic DBC message of 5 bytes with a `CHECKSUM` and a `COUNTER` and check the Toyota function receives a 5-byte frame); `test_nibble_aligned_checksum_does_not_disturb_neighbouring_bits`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_car_integrity.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `plan_message` and `Stamper` per the semantics above.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `car: integrity engine with counters and checksums`.

### Task 7: Periods

**Files:**
- Create: `src/ecu_simulator/car/periods.py`
- Test: `tests/unit/test_car_periods.py`

**Interfaces:**
- Consumes: `Recording` from `ecu_simulator.gui.replay` (`frames: list[tuple[int, bytes]]`, each frame a packed `=IB3x8s` record with a nanosecond offset).
- Produces: `PeriodSource` (`StrEnum`: `override`, `log`, `dbc`, `table`, `assumed`); `Period(ms: int, source: PeriodSource, clamped: bool)`; `DEFAULT_PERIOD_MS = 100`; `MIN_PERIOD_MS = 10`; `measure_log_periods(recording: Recording) -> dict[tuple[int, bool], float]` (median interval in ms per `(arbitration id, extended)`, only IDs with at least 3 frames, remote frames ignored, zero or negative intervals ignored); `resolve_period(*, override: int | None, measured_ms: float | None, dbc_ms: int | None, table_hz: float | None) -> Period` (priority per Global Constraints; round to the nearest ms; below 10 ms becomes 10 with `clamped=True`).

- [ ] **Step 1: Failing tests.** `test_priority_order` (each source beats the next); `test_table_hz_converts_to_ms`; `test_below_floor_is_clamped_and_labelled`; `test_assumed_default`; `test_measure_median_of_irregular_intervals`; `test_single_frame_id_has_no_measurement`; `test_remote_frames_ignored`; `test_extended_and_standard_ids_with_same_number_are_separate`; `test_equal_timestamps_ignored`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_car_periods.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** both functions; build recordings in tests from `parse_recording` of small candump text.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `car: period sources and log measurement`.

### Task 8: Generated drive

**Files:**
- Create: `src/ecu_simulator/car/drive.py`
- Test: `tests/unit/test_car_drive.py`

**Interfaces:**
- Consumes: signal dicts from `ImportedDb.message` (`name`, `unit`, `min`, `max`, `default`, `is_mux`); `VehicleState.get(path)`; generator classes in `ecu_simulator.scenario.generators` (`value_at(t)`).
- Produces: `DriveRole` (`StrEnum`: `speed`, `rpm`, `steering`, `pedal`); `classify_signal(info: Mapping[str, Any]) -> DriveRole | None`; `drive_sampler(message: Mapping[str, Any], vehicle: VehicleState, elapsed: Callable[[], float]) -> Callable[[], dict[str, float]]` returning physical values for the recognised signals only (the encoder fills the rest from DBC defaults).
- Rules: `speed`: unit `km/h` or `kph`, or unit `m/s` or `mph` with a name containing `SPEED`; value is `vehicle.speed` converted to the signal's unit. `rpm`: unit `rpm` and the vehicle has `engine.rpm`; value is `engine.rpm`. `steering`: unit `deg` and name matching `STEER.*ANGLE`; a `SineSignal` (centre 0, amplitude 30, period 20 s), clamped to the signal's min and max. `pedal`: unit `%` and name containing `PEDAL`; a `SteppedSignal` over `[0, 12, 0]` with interval 10 s. Gear and brake signals are not animated and keep their DBC defaults. Every value is clamped to the signal's `min` and `max`.

- [ ] **Step 1: Failing tests.** `test_classify_speed_rpm_steering_pedal_and_nothing_else`; `test_wheel_speeds_follow_vehicle_speed` (a stub vehicle whose `speed` changes between calls); `test_m_per_s_and_mph_conversions`; `test_rpm_follows_engine_rpm_only_when_vehicle_has_it`; `test_steering_is_repeatable_and_clamped` (same `elapsed` gives the same value twice; value within the signal's min and max); `test_unrecognised_signals_are_absent_from_the_sample`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_car_drive.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `classify_signal` and `drive_sampler`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `car: generated drive from recognised signals`.

### Task 9: Bus load and link information

**Files:**
- Create: `src/ecu_simulator/car/busload.py`
- Test: `tests/unit/test_car_busload.py`

**Interfaces:**
- Produces: `BUS_LOAD_CEILING = 0.70`; `LinkInfo(interface: str, virtual: bool, bitrate: int | None, state: str | None)`; `read_link(interface: str) -> LinkInfo` (parses `ip -j -d link show dev <interface>`: `linkinfo.info_kind` in `{vcan, vxcan}` means virtual; `linkinfo.info_data.bittiming.bitrate` and `linkinfo.info_data.state` for CAN; a missing interface raises `ValueError` naming it); `read_link_stats(interface: str) -> dict[str, int | str | None]` with keys `state`, `bitrate`, `bus_off`, `bus_errors`, `tx_dropped` (best effort from `ip -j -d -s link show`; missing values are `None`); `frame_bits(dlc: int, extended: bool) -> int` (worst case with stuffing: `g + 8*dlc + 13 + (g + 8*dlc - 1) // 4`, `g = 34` standard, `54` extended); `estimate_load(frames: Iterable[tuple[str, int, bool, float]], bitrate: int) -> float` (frames are `(name, dlc, extended, period_s)`; returns bits per second divided by bitrate); `BusLoadError(ValueError)` carrying `.over: list[tuple[str, float]]` (the three largest contributors); `check_bus_load(frames, link: LinkInfo) -> float` (on a physical link with no bitrate raises `BusLoadError` telling the operator to configure the interface with `scripts/setup_can.sh`; on a physical link whose `state` is `STOPPED` raises `BusLoadError("interface is down")`; above the ceiling raises; on a virtual link assumes 500000 and never raises).

- [ ] **Step 1: Failing tests.** `test_frame_bits_standard_8_bytes_is_135`; `test_one_message_20ms_on_500k` (about 1.35%); `test_virtual_never_refuses_even_over_100_percent`; `test_physical_over_ceiling_names_the_three_largest_messages`; `test_physical_without_bitrate_refuses_with_setup_hint`; `test_physical_link_down_refuses`; `test_read_link_parses_can_and_vcan_json` (monkeypatch `subprocess.run` with iproute2-shaped JSON); `test_read_link_missing_interface_names_it`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_car_busload.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** with `subprocess.run([... "ip", "-j", ...], capture_output=True, timeout=2, check=False)`, reusing the call style of `gui/dbc_workflow.virtual_can`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `car: bus-load estimate and link information`.

### Task 10: Declarative rule files

**Files:**
- Create: `src/ecu_simulator/car/algorithms.py`, `src/ecu_simulator/car/rules.py`
- Modify: `src/ecu_simulator/car/integrity.py` (`plan_message` gains `user: UserRule | None = None`)
- Test: `tests/unit/test_car_algorithms.py`, `tests/unit/test_car_rules.py`

**Interfaces:**
- Produces in `algorithms.py`: `build_checksum(spec: ChecksumSpec, field_size: int) -> ChecksumFn`. Algorithms: `xor` (XOR of covered bytes, then `^ init`, then `^ xorout`); `sum8` (`(init + sum(bytes)) & 0xFF`, then `^ xorout`); `nibble_sum` (`(init - sum of both nibbles of every covered byte) & 0xF`, then `^ xorout`); `crc8` and `crc16` (Rocksoft model: `poly`, `init`, `xorout`, `reflect` for both input and output). `covers` is a list of byte indexes (default: every byte not overlapping the checksum field); `data_id` (0..255) appends one byte after the covered bytes. The result is masked to `field_size`.
- Produces in `rules.py`: pydantic models with `extra="forbid"`: `RuleFile(version: Literal[1], rules: list[Rule])`, `Rule(messages: list[str] | None, glob: str | None, counter: CounterSpec | None, checksum: ChecksumSpec | None)` (exactly one of `messages` and `glob`; `glob` uses `fnmatch`, at most 64 characters; no regular expressions), `CounterSpec(signal: str, max: int | None)`, `ChecksumSpec(signal, algorithm, poly, init, xorout, reflect, covers, data_id)`; `parse_rule_text(text: str) -> RuleFile` (at most 256 KiB, at most 1000 rules; errors carry the dotted path); `UserRule` (a resolved counter and checksum for one message); `RuleFile.rule_for(message: Mapping[str, Any]) -> UserRule | None` (validates signal names, field widths, `covers` indexes against the message); `RuleStore(directory: Path | None = None)` with `import_text(text: str) -> str` (SHA-256, stored as `<sha>.yaml` under `$XDG_CONFIG_HOME/ecu-simulator/rules`, mode 0o700 directory, after validation) and `load(sha256: str) -> RuleFile`.
- `rules.py` imports `CounterField` and `ChecksumField` from `integrity.py`, so `integrity.py` imports `UserRule` only under `typing.TYPE_CHECKING` (no import cycle).
- `plan_message(..., user=...)`: a matched `UserRule` produces state `user_rule`, detail `"computed as the supplied rule says"`, and wins over the named rule for that message.

- [ ] **Step 1: Failing tests.** `test_algorithms.py`: `test_crc_check_values_over_123456789` for CRC-8/SAE-J1850 (`poly 0x1D init 0xFF xorout 0xFF`, 0x4B), CRC-8/AUTOSAR (`0x2F`, `init 0xFF`, `xorout 0xFF`, 0xDF), CRC-16/XMODEM (`0x1021`, init 0, 0x31C3), CRC-16/ARC (`0x8005`, init 0, reflect, 0xBB3D); `test_xor_sum8_nibble_sum_known_values`; `test_data_id_is_appended_before_the_crc`; `test_default_covers_exclude_the_checksum_field`. `test_rules.py`: `test_valid_rule_file_plans_user_rule_state`; `test_rejects` parametrized over: unknown algorithm, `crc8` with `poly: 0x1FF`, `covers` index past the message, glob matching no message in the DBC at plan time (reported, not silent), both `messages` and `glob`, a glob over 64 characters, an unknown key, a 300 KiB file; each message names the dotted path (`rules.0.checksum.poly`); `test_rule_store_round_trip_by_hash`; `test_rule_file_never_executes_anything` (a YAML tag such as `!!python/object` is refused by the existing safe loader).
- [ ] **Step 2: Run** both test files. Expected: FAIL.
- [ ] **Step 3: Implement** the three modules; load YAML through `ecu_simulator.config.loader.read_yaml` (safe, duplicate keys rejected).
- [ ] **Step 4: Run** both files plus `tests/unit/test_car_integrity.py`. Expected: PASS.
- [ ] **Step 5: Commit** `car: declarative rule files and generic algorithms`.

### Task 11: AUTOSAR E2E presets

**Files:**
- Create: `src/ecu_simulator/car/e2e.py`
- Modify: `src/ecu_simulator/car/rules.py` (`algorithm` accepts `e2e_p01`, `e2e_p02` with their parameters), `src/ecu_simulator/car/algorithms.py` (dispatch)
- Test: `tests/unit/test_car_e2e.py`

**Interfaces:**
- Produces: `e2e_p01(...) -> ChecksumFn` (CRC-8 with polynomial `0x1D`, init `0xFF`, final XOR `0xFF`, over the data bytes then the data ID per `data_id_mode` in `both`, `low`, `alt`, `nibble`; the 4-bit counter is the counter field) and `e2e_p02(...) -> ChecksumFn` (CRC-8H2F: polynomial `0x2F`, init `0xFF`, final XOR `0xFF`, over the data bytes then one byte of a 16-entry `data_id_list` selected by the counter). `ChecksumSpec` gains optional `data_id_mode` and `data_id_list` (16 values). The implementer works from AUTOSAR's published E2E Protocol Specification. If its access terms or layout details cannot be confirmed, stop, ship nothing for E2E, remove the two names from the schema and report that to the owner instead of guessing.

- [ ] **Step 1: Failing tests.** `test_p01_uses_crc8_j1850_parameters` (the underlying CRC equals the catalogue check value from Task 10); `test_p01_data_id_modes_change_the_crc_in_the_documented_way`; `test_p02_selects_data_id_by_counter`; `test_p01_requires_data_id_and_p02_requires_sixteen_entries`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_car_e2e.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `e2e.py` and the schema additions.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `car: AUTOSAR E2E profile 1 and 2 presets`.

### Task 12: Profile section and demo profile

**Files:**
- Modify: `src/ecu_simulator/config/schema.py` (`CarConfig`, `Profile.car`, validation)
- Create: `src/ecu_simulator/profiles/car_toyota_demo.yaml`
- Test: `tests/unit/test_car_config.py`

**Interfaces:**
- Produces: `CarConfig(Base)`: `platform: str | None`, `bus: str = "pt"`, `filename: str | None`, `sha256: str | None` (64 lowercase hex), `rule: Literal["toyota", "honda", "hyundai_can"] | None`, `rule_file: str | None` (64 lowercase hex), `periods: dict[str, int]` (operator overrides, 10 to 60000 ms), `log_periods: dict[str, int]` (measured from a log at draft time, 10 to 60000 ms). `car/__init__.py` stays empty so the schema can import `car.catalog` without importing the rest of the package (no import cycle with `app`). Exactly one of `platform` or `sha256` plus `filename` must be given; an unknown platform error lists the known ones; the bus must exist for the platform. `Profile.car: CarConfig | None`; the existing check becomes `choose either restbus, dbc or car`. `car_toyota_demo.yaml` is the `wheel_speed_demo.yaml` shape with `scenario` signals `vehicle.speed` stepped `[0, 40, 0]` interval 10 and `engine.rpm` stepped `[800, 2000, 800]` interval 10, and `car: {platform: TOYOTA_COROLLA, bus: pt}`.

- [ ] **Step 1: Failing tests.** `test_car_needs_platform_or_upload`; `test_car_excludes_restbus_and_dbc`; `test_unknown_platform_lists_known_ones`; `test_unknown_bus_for_platform`; `test_period_floor_and_ceiling`; `test_existing_profiles_still_parse` (every shipped profile except the new one has `car is None`); `test_demo_profile_parses_and_names_corolla`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_car_config.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `CarConfig` and the validators (catalog lookups through `car.catalog.load_catalog()`); add the demo profile.
- [ ] **Step 4: Run** `tests/unit/test_car_config.py tests/unit/test_config_schema.py tests/characterization -q`. Expected: PASS.
- [ ] **Step 5: Commit** `config: car profile section and Toyota demo profile`.

### Task 13: Car run and runtime wiring

**Files:**
- Create: `src/ecu_simulator/car/run.py`
- Modify: `src/ecu_simulator/app.py` (`run`), `src/ecu_simulator/gui/child.py`, `src/ecu_simulator/gui/manager.py`
- Test: `tests/unit/test_car_run.py`, `tests/unit/test_gui_manager.py` (add), `tests/integration/test_car_vcan.py`

**Interfaces:**
- Consumes: Tasks 2 to 10 and 12; `CyclicFrame` and `publish_frames` from `ecu_simulator.publisher`; `DbcStore`, `RuntimeConfig`, `VehicleState`, `Clock`.
- Produces: `CarMessage` and `PreparedCar(metadata: dict[str, Any], frames: list[CyclicFrame])`; `prepare_car(config: RuntimeConfig, vehicle: VehicleState, clock: Clock, *, sync: Callable[[], None] | None = None, link: LinkInfo | None = None, database: ImportedDb | None = None) -> PreparedCar | None` (returns `None` when the profile has no `car`; `link` defaults to `read_link(config.interface)`). It resolves the DBC (catalog bus or uploaded hash), the rule (rule file first for the messages it matches, then `car.rule`, then the catalog's rule for that hash, then none), each message's `MessagePlan` and `Period` (`override` from `car.periods[name]`, `measured_ms` from `car.log_periods[name]`, `dbc_ms` from the message's `period_ms`, `table_hz` from `catalog.rates_hz[platform.brand][name]`), then expands every transmittable message into a `CyclicFrame` whose sample is: `sync()`, drive values, `database.encode(name, values, integrity_ok=True)`, `Stamper.stamp`. A message is skipped with a reason, and the run continues, for: `"CAN FD"`; `"SecOC authenticator"` (any signal whose name contains `SECOC` or `AUTHENTICATOR`, case-insensitive; confirm the real naming in the shipped `toyota_secoc_pt_generated` DBC and adjust the pattern, with a test on that file); `"layout error: <text>"`; `"collides with <ecu>.<endpoint> on 0x<ID>"`; `"encode failed: <text>"`. It raises `ConfigError` if nothing is transmittable and calls `check_bus_load` before returning. `metadata` has `platform`, `dbc: {filename, sha256, name}`, `revision`, `rule`, `bus_load: {estimate, bitrate, assumed, worst_case: true}` and `messages: [{name, id, extended, dlc, period_ms, period_source, clamped, integrity, detail, counters: [str], checksums: [str], skipped: str | None}]`; a skipped message has `integrity: "unsupported"` and a non-null `skipped`, and no other message has either.
- `app.run(..., car_ready: Callable[[dict[str, Any]], None] | None = None)` publishes `car.frames` with `publish_frames` when a car is configured and calls `car_ready(metadata)` from `announce_ready`. `child.py` adds `"car": metadata` and `"dbc": {"filename", "sha256", "messages": []}` to the ready event so the existing `/capture` decoder selects the car's DBC. `Manager` stores `car` from the ready event and returns it in `status()` while Running.

- [ ] **Step 1: Failing tests.** `test_car_run.py` (with `SimulatedClock`): `test_toyota_corolla_lists_valid_messages_and_skip_reasons`; `test_frames_carry_checksum_that_recomputes` (decode the sampled bytes, recompute with `RULES["toyota"]`, equal); `test_counter_is_continuous_across_the_loop` (more samples than `max + 1`, counter sequence `0..max, 0`); `test_period_sources_are_reported_in_metadata`; `test_message_colliding_with_ecu_ids_is_skipped`; `test_nothing_transmittable_raises_config_error`; `test_physical_over_ceiling_is_refused_and_vcan_is_not` (a synthetic DBC of 150 messages at 10 ms generated in the test; `LinkInfo("can0", False, 500000, None)` raises `BusLoadError`; `LinkInfo("vcan0", True, None, None)` returns); `test_secoc_messages_are_skipped_with_reason`; `test_user_rule_file_overrides_named_rule_for_matched_messages`. `test_gui_manager.py`: `test_status_includes_car_metadata_while_running`. `tests/integration/test_car_vcan.py` (marker `vcan`): `test_corolla_run_on_vcan_matches_independent_capture_and_holds_period` (start with `car_toyota_demo.yaml`, capture with a raw socket for 5 s, assert every received frame ID is in the metadata, DLCs match, the median period of the fastest message is within 10% of its stated period, and counters run `0..max` without a gap); `test_150_message_car_keeps_period_on_vcan`; `test_obd_speed_matches_wheel_speed_plateau` (`01 0D` returns the same speed the wheel-speed message encodes at a plateau).
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_car_run.py tests/unit/test_gui_manager.py -q` and `bash scripts/run_integration_tests.sh -k car_vcan -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `prepare_car`, the `app.run` branch and the child and manager changes. A 150-message car must not need a schedule rewrite; if the median period misses the 10% bound, stop and report the measured numbers before changing the publisher.
- [ ] **Step 4: Run** the unit tests, the integration tests, then the whole `tests/unit` and `tests/characterization` suites. Expected: PASS.
- [ ] **Step 5: Commit** `car: prepare and publish a whole car from the catalog or a DBC`.

### Task 14: GUI backend

**Files:**
- Create: `src/ecu_simulator/gui/car_workflow.py`, `src/ecu_simulator/gui/car_routes.py`
- Modify: `src/ecu_simulator/gui/server.py` (install routes, `/run` review rule, asset route for `/car.js`)
- Test: `tests/unit/test_car_workflow.py`, `tests/unit/test_car_routes.py`

**Interfaces:**
- Produces in `car_workflow.py`: `catalog_view() -> dict` (`{"revision", "brands": {brand: [{"id", "names", "buses": [str]}]}, "skipped_count"}`); `car_draft(text: str, selection: Mapping[str, Any]) -> str` (sets the `car:` section from `platform`/`bus` or `sha256`/`filename`, `rule`, `rule_file`, `periods`, `log_periods`; removes `restbus` and `dbc`; adds the demo `vehicle.speed` and `engine.rpm` scenario signals when the working copy has none; validates with `load_profile_text`; returns the updated text); `check(text: str, link: LinkInfo | None = None) -> dict` (`{"messages": [...], "counts": {"valid", "counter_only", "user_rule", "none", "skipped"}, "bus_load": {...}}` from `prepare_car` with a throwaway `SimulatedClock`, opening no socket); `log_periods(db: ImportedDb, raw: bytes) -> dict[str, int]` (message name to measured ms from `parse_recording` and `measure_log_periods`).
- Routes (`car_routes.install_car_routes(routes, store, rules, reviews)`): `GET /car/catalog`; `POST /car/draft` (`{text, ...selection}` to `{text}`); `POST /car/check` (`{text}`); `POST /car/rule?filename=` (raw YAML to `{sha256, rules}`); `POST /car/log?sha256=` (raw candump to `{periods, matched, unmatched_ids}`); `GET /car/link?interface=` (`read_link_stats` plus `LinkInfo`); `POST /car/review` (`{text}`, same response shape as `/dbc/review`, listing the car's transmittable messages with preview bytes, issuing a `ReviewTokens` token and observing the interface for 500 ms). `POST /run` consumes the review token when the profile has `car`, the interface is not virtual, and the car has transmittable messages.

- [ ] **Step 1: Failing tests.** `test_car_workflow.py`: `test_catalog_view_groups_by_brand`; `test_car_draft_sets_car_and_removes_dbc_and_restbus`; `test_car_draft_adds_drive_scenario_once`; `test_check_counts_match_message_states`; `test_log_periods_maps_ids_to_message_names`. `test_car_routes.py` (aiohttp `TestClient` over `server.application(tmp_path)`, POSTs sent with the `Origin` header the middleware requires): `test_catalog_route`; `test_draft_then_check_round_trip`; `test_rule_upload_rejects_hostile_file_with_400`; `test_run_on_physical_requires_review_token` (monkeypatch `dbc_workflow.virtual_can` to return `False`); `test_run_on_vcan_needs_no_token`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_car_workflow.py tests/unit/test_car_routes.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** both modules and the `server.py` edits (one import, one call, the review rule in `start`, and `("/car.js", "car.js")` in the asset list).
- [ ] **Step 4: Run** the two files plus `tests/unit/test_dbc_workflow.py`. Expected: PASS.
- [ ] **Step 5: Commit** `gui: car workflow and routes`.

### Task 15: GUI front end

**Files:**
- Create: `src/ecu_simulator/gui/static/car.js`
- Modify: `src/ecu_simulator/gui/static/control.html` (a `<section id="car-panel">` above the DBC messages section and the script tag), `src/ecu_simulator/gui/static/control.css`
- Test: `tests/integration/test_car_gui.py` (the existing Playwright fixtures from `tests/integration/test_gui_control_vcan.py`)

**Interfaces:**
- Element IDs the browser test and code rely on: `#car-panel`, `#car-brand`, `#car-platform` (option values are platform IDs), `#car-bus`, `#car-upload` (file input for a DBC), `#car-source-generated`, `#car-source-recorded`, `#car-log-file`, `#car-rule` (select), `#car-rule-file`, `#car-step-1`, `#car-step-2`, `#car-step-3` (each carries `data-state` of `todo`, `active` or `done`), `#car-chips`, `#car-table` (rows have `data-message`), `#car-interface` (kept in sync with the run bar's `#interface`), `#car-bus-load`, `#car-play`, `#car-stop`, `#car-live` (greyed class `idle` before a run), `#car-live-rows` (rows have `data-message`; cells `.counter`, `.checksum`, `.set-period`, `.measured-period`), `#car-live-stats`, `#car-live-pause`, `#car-live-filter` (text input matching message name or hex ID), `#car-identity` (a `<details>` showing the DBC SHA-256 and the catalog revision), `#car-banner` (`role="alert"`).
- Behaviour: Step 1 builds the draft through `/car/draft`; Step 2 calls `/car/check`, fills chips and table (period source and integrity state as chips; an `edit` control per period sending `periods` back through `/car/draft`); Step 3 shows the interface and bus load (a refusal appears in `#car-banner`) and Play starts the run through the existing `/run` flow, including the physical review dialog through `/car/review`. The recorded source starts the existing replay (`/replay`) and disables the integrity column. While running, the first two steps collapse to ticks and `#car-live` opens its own `/capture` stream on the run's interface, reading `frame.dbc.signals` and `frame.dbc.period_ms` for the message rows, counters and checksums named in the run's `car` status; it polls `/car/link` every 2 s for the error, bus-off and queue-full counts. A failed or stopped run puts the cause and next step in `#car-banner` (send failure with no buffer space, bus-off, interface down or missing).

- [ ] **Step 1: Failing browser tests.** `test_walk_three_steps_on_vcan` (choose `TOYOTA_COROLLA`; `#car-chips` shows a valid count above zero; Play; `#car-live-rows` shows a row for a message that has both a `COUNTER` and a `CHECKSUM`, found from the catalog data in the test; its counter and checksum cells equal those decoded from an independent raw-socket capture of the same frame); `test_period_edit_marks_override`; `test_banner_names_cause_when_interface_goes_down` (bring the run's interface down mid-run with the same namespace helper the other GUI tests use; `#car-banner` names the send failure and the run state is not Running); `test_recorded_source_disables_integrity_column`; `test_manual_dbc_panel_still_present`.
- [ ] **Step 2: Run** `bash scripts/run_integration_tests.sh -k car_gui -qs`. Expected: FAIL.
- [ ] **Step 3: Implement** `car.js` and the markup and styles. Follow the layout in the spec's GUI section; polish belongs to the site shell, not here. Keep `car.js` free of shared mutable state with `live.js` and `dbc.js`.
- [ ] **Step 4: Run** the browser tests and `tests/integration/test_dbc_import_gui.py`. Expected: PASS.
- [ ] **Step 5: Commit** `gui: car panel with guided steps and live data`.

### Task 16: Acceptance, docs and the physical gate

**Files:**
- Create: `docs/validation/car-from-dbc.md`, `tests/integration/test_car_oracle_receiver.py`
- Modify: `README.md` (a "Run a whole car" section; the manual DBC section stays), `docs/superpowers/specs/2026-11-22-car-from-dbc-design.md` only if a call changed during the work
- Test: the receiver test above

**Interfaces:**
- Consumes: `car_toyota_demo.yaml`, opendbc's `CANParser` (dev-only).

- [ ] **Step 1: Failing test.** `test_opendbc_parser_accepts_toyota_and_honda_run` (skipped when opendbc is not importable): run a Toyota car and a Honda car on `vcan0`, capture, feed the frames to opendbc's `CANParser` for the DBC and confirm it reports valid counters and checksums for every message with a rule; Hyundai is excluded because opendbc's parser ignores that DBC's checksums.
- [ ] **Step 2: Run** it on `vcan0` with the scratch interpreter from Task 1 (`PYTHON=<scratch>/venv/bin/python bash scripts/run_integration_tests.sh -k oracle_receiver -q`). Expected: PASS, since the run exists after Task 13; a failure is a real finding about the checksums and goes in the report, not in a test change.
- [ ] **Step 3: Write `docs/validation/car-from-dbc.md`** as the physical acceptance record: bench (candleLight adapter at `can0`, 500 kbit/s, a second active node), the procedure from the spec's Verification item 5 (a full Toyota car, a separate receiver, frames received per ID, median, p95 and maximum for the busiest message, the adapter's error counters before and after, bus-off count expected zero), and empty result fields. Leave the results blank: only a run on the bench fills them.
- [ ] **Step 4: Update `README.md`** with the car workflow on `vcan0` and `can0`, the rule-file format with one worked example, the integrity labels (including that Hyundai is checked against opendbc's packer only), and the opendbc revision and licence.
- [ ] **Step 5: Run everything** (`.venv/bin/python -m pytest -q`, `bash scripts/run_integration_tests.sh -q`, `.venv/bin/ruff check src tests scripts`, `.venv/bin/mypy`). Expected: PASS. Commit `docs: car validation record and README section`. Report the physical gate as not yet run.
