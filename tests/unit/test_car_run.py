"""The whole-car run: which messages of a car's DBC go on the bus, how often, with which counters and checksums.

``prepare_car`` is driven with a ``SimulatedClock`` and an explicit ``LinkInfo``, so nothing here opens a socket or runs
``ip``. The Corolla tests use the bundled catalog DBC; the rest build small DBCs in the test, loaded through the real
``ImportedDb`` (and, for an upload, the real ``DbcStore`` under a private ``XDG_CONFIG_HOME``).
"""

import builtins
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from ecu_simulator import app
from ecu_simulator.car.bits import BitField
from ecu_simulator.car.busload import BusLoadError, LinkInfo
from ecu_simulator.car.catalog import bundled_dbc_path, load_catalog
from ecu_simulator.car.checksums import RULES
from ecu_simulator.car.periods import PeriodSource
from ecu_simulator.car.rules import RuleStore
from ecu_simulator.car.run import PreparedCar, prepare_car
from ecu_simulator.cli import default_profile_path
from ecu_simulator.clock import SimulatedClock
from ecu_simulator.config import ConfigError, load_yaml, parse_profile
from ecu_simulator.dbc import DbcStore, ImportedDb

VCAN = LinkInfo("vcan0", True, None, None)
CAN0 = LinkInfo("can0", False, 500000, None)
SECOC = re.compile("SECOC|AUTHENTICATOR", re.IGNORECASE)


def demo_data() -> dict[str, Any]:
    return load_yaml(default_profile_path().with_name("car_toyota_demo.yaml"))


def config(car: dict[str, Any] | None = None) -> app.RuntimeConfig:
    data = demo_data()
    if car is None:
        del data["car"]
    else:
        data["car"] = car
    return app.RuntimeConfig.build(parse_profile(data))


def corolla(**car: Any) -> app.RuntimeConfig:
    return config({"platform": "TOYOTA_COROLLA", "bus": "pt", **car})


def prepare(cfg: app.RuntimeConfig, **kwargs: Any) -> PreparedCar:
    kwargs.setdefault("link", VCAN)
    prepared = prepare_car(cfg, app.build_vehicle(cfg), SimulatedClock(), **kwargs)
    assert prepared is not None
    return prepared


def by_name(prepared: PreparedCar) -> dict[str, dict[str, Any]]:
    return {message["name"]: message for message in prepared.metadata["messages"]}


def frame(prepared: PreparedCar, name: str):
    return next(f for f in prepared.frames if f.name == name)


def corolla_db() -> ImportedDb:
    entry = load_catalog().dbcs[load_catalog().platforms["TOYOTA_COROLLA"].buses["pt"]]
    return DbcStore().load(entry.sha256)


def field(db: ImportedDb, message: str, signal: str) -> BitField:
    info = next(s for s in db.message(message)["signals"] if s["name"] == signal)
    return BitField(info["start"], info["length"], info["little_endian"])


# A small DBC written in the test. Each message is (name, id, dlc, extended, cycle_ms, integrity): integrity adds a
# Motorola COUNTER (4 bits, byte 6 high nibble) and a CHECKSUM (byte 7) in Toyota's layout.
def synthetic_dbc(messages: list[tuple[str, int, int, bool, int | None, bool]]) -> bytes:
    lines = ['VERSION ""', "", "NS_ :", "", "BS_:", "", "BU_: XXX", ""]
    cycles = []
    for name, can_id, dlc, extended, cycle_ms, integrity in messages:
        wire = can_id | (0x8000_0000 if extended else 0)
        lines.append(f"BO_ {wire} {name}: {dlc} XXX")
        lines.append(' SG_ VALUE : 7|16@0+ (1,0) [0|65535] "" XXX')
        if integrity:
            lines.append(' SG_ COUNTER : 55|4@0+ (1,0) [0|15] "" XXX')
            lines.append(' SG_ CHECKSUM : 63|8@0+ (1,0) [0|255] "" XXX')
        lines.append("")
        if cycle_ms is not None:
            cycles.append(f'BA_ "GenMsgCycleTime" BO_ {wire} {cycle_ms};')
    lines += ['BA_DEF_ BO_ "GenMsgCycleTime" INT 0 65535;', 'BA_DEF_DEF_ "GenMsgCycleTime" 0;', *cycles, ""]
    return "\n".join(lines).encode()


def many_messages(count: int = 150, period_ms: int = 10) -> list[tuple[str, int, int, bool, int | None, bool]]:
    """``count`` classical messages at ``period_ms``, every third one with a counter and a checksum."""
    return [(f"MSG_{index:03d}", 0x100 + index, 8, False, period_ms, index % 3 == 0) for index in range(count)]


def database(raw: bytes) -> ImportedDb:
    return ImportedDb(raw, hashlib.sha256(raw).hexdigest())


def upload(raw: bytes, **car: Any) -> app.RuntimeConfig:
    return config({"sha256": hashlib.sha256(raw).hexdigest(), "filename": "bench.dbc", **car})


# --- the Corolla from the catalog -------------------------------------------------------------------------------------


def test_toyota_corolla_lists_valid_messages_and_skip_reasons():
    prepared = prepare(corolla())
    metadata = prepared.metadata
    catalog = load_catalog()
    entry = catalog.dbcs["toyota_new_mc_pt_generated"]
    assert metadata["platform"] == "TOYOTA_COROLLA"
    assert metadata["dbc"] == {"filename": Path(entry.file).name, "sha256": entry.sha256, "name": entry.name}
    assert metadata["revision"] == catalog.revision
    assert metadata["rule"] == "toyota"
    assert metadata["bus_load"]["assumed"] is True and metadata["bus_load"]["worst_case"] is True
    assert metadata["bus_load"]["bitrate"] == 500000 and 0 < metadata["bus_load"]["estimate"] < 1

    messages = by_name(prepared)
    assert len(messages) == len(corolla_db().catalog("x")["messages"])
    assert messages["PCS_HUD"]["skipped"].startswith("layout error: The signals PCS_TEMP and SET_ME_X10 are overlapping")
    assert messages["SPEED"] == {
        "name": "SPEED", "id": 0xB4, "extended": False, "dlc": 8, "period_ms": 100, "period_source": "assumed",
        "clamped": False, "integrity": "valid", "detail": None, "counters": [], "checksums": ["CHECKSUM"],
        "skipped": None,
    }
    assert messages["STEERING_LKA"]["counters"] == ["COUNTER"]
    assert messages["WHEEL_SPEEDS"]["integrity"] == "none"
    sent = [message["name"] for message in metadata["messages"] if message["skipped"] is None]
    assert [f.name for f in prepared.frames] == sent
    assert len(sent) == len(messages) - 1


def test_skipped_messages_are_unsupported_and_no_transmitted_message_is():
    prepared = prepare(corolla())
    transmitted = {f.name for f in prepared.frames}
    for message in prepared.metadata["messages"]:
        skipped = message["skipped"] is not None
        assert skipped == (message["integrity"] == "unsupported"), message
        assert skipped == (message["name"] not in transmitted), message


def test_frames_carry_checksum_that_recomputes():
    prepared = prepare(corolla())
    db = corolla_db()
    checked = 0
    for message in prepared.metadata["messages"]:
        if message["skipped"] or not message["checksums"]:
            continue
        data = bytearray(frame(prepared, message["name"]).sample())
        checksum = field(db, message["name"], "CHECKSUM")
        sent = checksum.read(data)
        checksum.write(data, 0)
        assert RULES["toyota"](message["id"], data, checksum, "CHECKSUM") == sent, message["name"]
        checked += 1
    assert checked == 13


def test_counter_is_continuous_across_the_loop():
    clock = SimulatedClock()
    cfg = corolla()
    prepared = prepare_car(cfg, app.build_vehicle(cfg), clock, link=VCAN)
    assert prepared is not None
    lka = frame(prepared, "STEERING_LKA")
    counter = field(corolla_db(), "STEERING_LKA", "COUNTER")
    seen = []
    for _ in range(64 + 2):
        seen.append(counter.read(lka.sample()))
        clock.advance(0.1)
    assert seen == [*range(64), 0, 1]


def test_scenario_drives_the_wheel_speeds_through_sync_and_the_vehicle():
    cfg = corolla()
    vehicle = app.build_vehicle(cfg)
    calls = []
    prepared = prepare_car(cfg, vehicle, SimulatedClock(), sync=lambda: calls.append(1), link=VCAN)
    assert prepared is not None
    wheels = frame(prepared, "WHEEL_SPEEDS")
    assert wheels.can_id == 0xAA and wheels.period == pytest.approx(0.012)
    assert wheels.sample() == bytes.fromhex("1A 6F") * 4
    vehicle.set("vehicle.speed", 40)
    assert wheels.sample() == bytes.fromhex("2A 0F") * 4
    assert len(calls) == 2


def test_period_sources_are_reported_in_metadata():
    prepared = prepare(corolla(periods={"SPEED": 10}, log_periods={"SPEED": 50, "GAS_PEDAL": 33}))
    messages = by_name(prepared)
    assert (messages["SPEED"]["period_ms"], messages["SPEED"]["period_source"]) == (10, "override")
    assert (messages["GAS_PEDAL"]["period_ms"], messages["GAS_PEDAL"]["period_source"]) == (33, "log")
    assert (messages["STEER_TORQUE_SENSOR"]["period_ms"], messages["STEER_TORQUE_SENSOR"]["period_source"]) == (
        20, "table",
    )
    assert (messages["WHEEL_SPEEDS"]["period_ms"], messages["WHEEL_SPEEDS"]["period_source"]) == (12, "table")
    assert (messages["ACC_CONTROL"]["period_ms"], messages["ACC_CONTROL"]["period_source"]) == (100, "assumed")
    assert frame(prepared, "SPEED").period == pytest.approx(0.010)
    assert frame(prepared, "GAS_PEDAL").period == pytest.approx(0.033)

    raw = synthetic_dbc([("FAST", 0x100, 8, False, 5, False), ("SLOW", 0x101, 8, False, 200, False)])
    uploaded = by_name(prepare(upload(raw), database=database(raw)))
    assert {k: uploaded["FAST"][k] for k in ("period_ms", "period_source", "clamped")} == {
        "period_ms": 10, "period_source": "dbc", "clamped": True,
    }
    assert {k: uploaded["SLOW"][k] for k in ("period_ms", "period_source", "clamped")} == {
        "period_ms": 200, "period_source": "dbc", "clamped": False,
    }
    sources = {m["period_source"] for m in [*messages.values(), *uploaded.values()]}
    assert sources == {source.value for source in PeriodSource}


def test_an_override_wins_and_an_absent_one_is_not_zero():
    raw = synthetic_dbc([("A", 0x100, 8, False, 50, False), ("B", 0x101, 8, False, 50, False)])
    prepared = prepare(upload(raw, periods={"A": 10}, log_periods={"A": 20}), database=database(raw))
    messages = by_name(prepared)
    assert (messages["A"]["period_ms"], messages["A"]["period_source"]) == (10, "override")
    assert (messages["B"]["period_ms"], messages["B"]["period_source"], messages["B"]["clamped"]) == (50, "dbc", False)


def test_metadata_is_json_serialisable():
    prepared = prepare(corolla(periods={"SPEED": 10}))
    assert json.loads(json.dumps(prepared.metadata)) == prepared.metadata


# --- skips ------------------------------------------------------------------------------------------------------------


def test_message_colliding_with_ecu_ids_is_skipped():
    raw = synthetic_dbc([
        ("OK", 0x100, 8, False, 20, False),
        ("REQUEST", 0x7E0, 8, False, 20, False),
        ("FUNCTIONAL", 0x7DF, 8, False, 20, False),
        ("SAME_NUMBER_EXTENDED", 0x7E0, 8, True, 20, False),
    ])
    prepared = prepare(upload(raw), database=database(raw))
    messages = by_name(prepared)
    assert messages["REQUEST"]["skipped"] == "collides with engine.obd_physical on 0x7E0"
    assert messages["FUNCTIONAL"]["skipped"] == "collides with engine.obd_functional on 0x7DF"
    assert messages["SAME_NUMBER_EXTENDED"]["skipped"] is None
    assert [f.name for f in prepared.frames] == ["OK", "SAME_NUMBER_EXTENDED"]
    assert frame(prepared, "SAME_NUMBER_EXTENDED").can_id == 0x7E0 | 0x8000_0000
    assert frame(prepared, "OK").can_id == 0x100


def test_can_fd_message_is_skipped_and_the_bus_load_check_still_runs():
    raw = synthetic_dbc([("CLASSIC", 0x100, 8, False, 10, False), ("WIDE", 0x101, 16, False, 10, False)])
    prepared = prepare(upload(raw), database=database(raw))
    messages = by_name(prepared)
    assert messages["WIDE"]["skipped"] == "CAN FD"
    assert messages["WIDE"]["integrity"] == "unsupported"
    assert [f.name for f in prepared.frames] == ["CLASSIC"]
    assert prepared.metadata["bus_load"]["estimate"] > 0


def test_nothing_transmittable_raises_config_error():
    raw = synthetic_dbc([("WIDE", 0x101, 16, False, 10, False), ("REQUEST", 0x7E0, 8, False, 20, False)])
    with pytest.raises(ConfigError, match=r"bench\.dbc.*no message can be transmitted.*CAN FD.*collides"):
        prepare(upload(raw), database=database(raw))


def test_physical_over_ceiling_is_refused_and_vcan_is_not():
    raw = synthetic_dbc(many_messages())
    cfg = upload(raw, rule="toyota")
    with pytest.raises(ConfigError, match=r"above the 70% ceiling\. Largest messages: MSG_\d+") as caught:
        prepare(cfg, database=database(raw), link=CAN0)
    cause = caught.value.__cause__
    assert isinstance(cause, BusLoadError) and len(cause.over) == 3
    prepared = prepare(cfg, database=database(raw), link=VCAN)
    assert len(prepared.frames) == 150
    assert prepared.metadata["bus_load"]["estimate"] > 1


def test_an_unreadable_interface_is_a_config_error(monkeypatch):
    def unreadable(interface: str) -> LinkInfo:
        raise ValueError(f"Cannot read {interface}: Device does not exist")

    monkeypatch.setattr("ecu_simulator.car.run.read_link", unreadable)
    with pytest.raises(ConfigError, match="Cannot read vcan0"):
        prepare(corolla(), link=None)


def test_secoc_messages_are_skipped_with_reason():
    platform = load_catalog().platforms["TOYOTA_RAV4_PRIME"]
    entry = load_catalog().dbcs[platform.buses["pt"]]
    assert entry.name == "toyota_secoc_pt_generated"
    text = bundled_dbc_path(entry.sha256).read_text()
    # The shipped file marks every authenticated message with a 28-bit AUTHENTICATOR; no signal is named SECOC.
    assert len(re.findall(r"^ SG_ AUTHENTICATOR : 35\|28@0\+", text, re.M)) == 9
    assert not re.search(r"^ SG_ \w*SECOC", text, re.M | re.I)
    authenticated = {
        m["name"] for m in DbcStore().load(entry.sha256).catalog("x")["messages"]
        if any(SECOC.search(s["name"]) for s in m["signals"])
    }
    assert len(authenticated) == 9 and "SECOC_SYNCHRONIZATION" in authenticated

    prepared = prepare(config({"platform": "TOYOTA_RAV4_PRIME", "bus": "pt"}))
    skipped = {m["name"] for m in prepared.metadata["messages"] if m["skipped"] == "SecOC authenticator"}
    assert skipped == authenticated
    assert not skipped & {f.name for f in prepared.frames}


def test_secoc_pattern_is_case_insensitive_and_matches_a_secoc_signal():
    raw = (
        b'BO_ 256 PLAIN: 8 XXX\n SG_ VALUE : 7|16@0+ (1,0) [0|65535] "" XXX\n\n'
        b'BO_ 257 LOWER: 8 XXX\n SG_ authenticator_x : 7|16@0+ (1,0) [0|65535] "" XXX\n\n'
        b'BO_ 258 FRESH: 8 XXX\n SG_ SecOC_FRESHNESS : 7|16@0+ (1,0) [0|65535] "" XXX\n'
    )
    messages = by_name(prepare(upload(raw), database=database(raw)))
    assert messages["PLAIN"]["skipped"] is None
    assert messages["LOWER"]["skipped"] == messages["FRESH"]["skipped"] == "SecOC authenticator"


def test_encode_failure_at_prepare_time_is_skipped():
    # A selector default with no branch of its own and a driven value outside a branch member's range would both be
    # caught here; the simplest real case is a speed whose range the vehicle's speed falls outside.
    raw = (
        b'BO_ 256 OK: 8 XXX\n SG_ VALUE : 7|16@0+ (1,0) [0|65535] "" XXX\n\n'
        b'BO_ 257 SPEEDY: 8 XXX\n SG_ VEHICLE_SPEED : 7|8@0+ (1,0) [0|255] "km/h" XXX\n'
    )
    db = database(raw)

    class Refusing:
        def catalog(self, filename: str) -> dict[str, Any]:
            return db.catalog(filename)

        def encode(self, name: str, values: dict[str, float], *, integrity_ok: bool = False) -> bytes:
            if name == "SPEEDY":
                raise ValueError("VEHICLE_SPEED is outside its physical range")
            return db.encode(name, values, integrity_ok=integrity_ok)

    messages = by_name(prepare(upload(raw), database=Refusing()))
    assert messages["SPEEDY"]["skipped"] == "encode failed: VEHICLE_SPEED is outside its physical range"
    assert messages["OK"]["skipped"] is None


def test_run_time_encode_failure_resends_the_last_good_payload_with_a_fresh_counter(caplog):
    raw = synthetic_dbc([("A", 0x100, 8, False, 10, True)])
    db = database(raw)
    calls = {"count": 0}

    class Flaky:
        def catalog(self, filename: str) -> dict[str, Any]:
            return db.catalog(filename)

        def encode(self, name: str, values: dict[str, float], *, integrity_ok: bool = False) -> bytes:
            calls["count"] += 1
            if calls["count"] > 2:  # the prepare-time trial and the first frame succeed
                raise ValueError("VALUE is outside its physical range")
            return db.encode(name, values, integrity_ok=integrity_ok)

    prepared = prepare(upload(raw, rule="toyota"), database=Flaky())
    a = frame(prepared, "A")
    counter, checksum = field(db, "A", "COUNTER"), field(db, "A", "CHECKSUM")
    first, second, third = a.sample(), a.sample(), a.sample()
    assert [counter.read(f) for f in (first, second, third)] == [0, 1, 2]
    for sent in (second, third):
        assert sent[:6] == first[:6]
        data = bytearray(sent)
        value = checksum.read(data)
        checksum.write(data, 0)
        assert RULES["toyota"](0x100, data, checksum, "CHECKSUM") == value
    assert prepared.encode_failures == {"A": 2}
    assert sum("encode failed for A" in record.getMessage() for record in caplog.records) == 1


# --- rules ------------------------------------------------------------------------------------------------------------


RULE_FILE = """\
version: 1
rules:
  - messages: [SPEED]
    checksum: {signal: CHECKSUM, algorithm: xor}
"""


def test_user_rule_file_overrides_named_rule_for_matched_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    digest = RuleStore().import_text(RULE_FILE)
    prepared = prepare(corolla(rule_file=digest))
    messages = by_name(prepared)
    assert messages["SPEED"]["integrity"] == "user_rule"
    assert messages["SPEED"]["detail"] == "computed as the supplied rule says"
    assert messages["GAS_PEDAL"]["integrity"] == "valid"
    data = frame(prepared, "SPEED").sample()
    assert data[7] == data[0] ^ data[1] ^ data[2] ^ data[3] ^ data[4] ^ data[5] ^ data[6]


def test_a_rule_file_that_does_not_fit_the_dbc_is_a_config_error_with_its_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    digest = RuleStore().import_text(RULE_FILE.replace("CHECKSUM,", "NO_SUCH_SIGNAL,"))
    with pytest.raises(ConfigError, match=r"car\.rule_file: rules\.0\.checksum\.signal: message 'SPEED' has no signal"):
        prepare(corolla(rule_file=digest))


def test_a_missing_rule_file_is_a_config_error(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with pytest.raises(ConfigError, match=r"car\.rule_file: Missing rule file"):
        prepare(corolla(rule_file="ab" * 32))


def test_profile_rule_beats_the_catalog_rule():
    prepared = prepare(corolla(rule="honda"))
    assert prepared.metadata["rule"] == "honda"
    data = bytearray(frame(prepared, "SPEED").sample())
    checksum = field(corolla_db(), "SPEED", "CHECKSUM")
    sent = checksum.read(data)
    checksum.write(data, 0)
    assert RULES["honda"](0xB4, data, checksum, "CHECKSUM") == sent
    assert RULES["toyota"](0xB4, data, checksum, "CHECKSUM") != sent


def test_counter_only_message_of_an_upload_without_rule_is_sent_and_labelled():
    raw = synthetic_dbc([("A", 0x100, 8, False, 10, True)])
    prepared = prepare(upload(raw), database=database(raw))
    message = by_name(prepared)["A"]
    assert prepared.metadata["rule"] is None and prepared.metadata["platform"] is None
    assert (message["integrity"], message["counters"], message["checksums"]) == ("counter_only", ["COUNTER"], [])
    assert message["detail"] == "checksum not computed"
    counter = field(database(raw), "A", "COUNTER")
    assert [counter.read(frame(prepared, "A").sample()) for _ in range(3)] == [0, 1, 2]


# --- the DBC source ---------------------------------------------------------------------------------------------------


def test_a_profile_without_car_prepares_nothing():
    cfg = config(None)
    assert prepare_car(cfg, app.build_vehicle(cfg), SimulatedClock(), link=VCAN) is None


def test_an_unknown_dbc_hash_is_a_config_error_naming_it(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    digest = "cd" * 32
    with pytest.raises(ConfigError, match=digest):
        prepare(config({"sha256": digest, "filename": "bench.dbc"}))


def test_an_uploaded_dbc_is_loaded_by_digest_and_its_filename_never_reaches_the_filesystem(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    raw = synthetic_dbc([("A", 0x100, 8, False, 10, False)])
    DbcStore().import_file("whatever.dbc", raw)
    cfg = upload(raw)
    car = cfg.profile.car.model_copy(update={"filename": "../x"})
    cfg = app.RuntimeConfig(cfg.profile.model_copy(update={"car": car}), cfg.interface)

    touched: list[str] = []
    real_open, real_read_bytes, real_os_open = builtins.open, Path.read_bytes, os.open

    def guard(path: object) -> None:
        if isinstance(path, str | os.PathLike) and "../x" in str(os.fspath(path)):
            touched.append(str(path))
            raise AssertionError(f"opened {path}")

    def guarded_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        guard(file)
        return real_open(file, *args, **kwargs)

    def guarded_read_bytes(self: Path) -> bytes:
        guard(self)
        return real_read_bytes(self)

    def guarded_os_open(path: Any, *args: Any, **kwargs: Any) -> int:
        guard(path)
        return real_os_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    monkeypatch.setattr(os, "open", guarded_os_open)
    prepared = prepare(cfg)
    assert touched == []
    assert prepared.metadata["dbc"] == {"filename": "../x", "sha256": hashlib.sha256(raw).hexdigest(), "name": None}
    assert [f.name for f in prepared.frames] == ["A"]
