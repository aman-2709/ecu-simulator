"""The GUI's car workflow: the catalog view, the working-copy draft, the check and the log measurement.

Nothing here opens a socket: ``check`` is given its ``LinkInfo`` or reads the interface through a patched ``read_link``.
"""

import hashlib
import io
import json
from collections import Counter
from typing import Any

import pytest
from ruamel.yaml import YAML

from ecu_simulator.car.busload import LinkInfo
from ecu_simulator.car.catalog import load_catalog
from ecu_simulator.car.rules import RuleStore
from ecu_simulator.cli import default_profile_path
from ecu_simulator.config import ConfigError, load_profile_text, read_yaml
from ecu_simulator.dbc import DbcStore, ImportedDb
from ecu_simulator.gui import car_workflow
from ecu_simulator.gui.car_workflow import car_draft, catalog_view, check, log_periods, measure_log
from ecu_simulator.gui.dbc_workflow import changed_draft
from tests.unit.test_car_run import many_messages, synthetic_dbc

VCAN = LinkInfo("vcan0", True, None, None)
CAN0 = LinkInfo("can0", False, 500000, None)


def profile_text(name: str) -> str:
    return default_profile_path().with_name(f"{name}.yaml").read_text()


def stored_upload(tmp_path, monkeypatch, messages, **car: Any) -> tuple[str, str]:
    """A working copy for an uploaded DBC kept in a private store, and the DBC's digest."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    raw = synthetic_dbc(messages)
    digest = hashlib.sha256(raw).hexdigest()
    DbcStore().import_file("bench.dbc", raw)
    text = car_draft(profile_text("ice_default"), {"sha256": digest, "filename": "bench.dbc", **car})
    return text, digest


# --- catalog_view -----------------------------------------------------------------------------------------------------


def test_catalog_view_groups_by_brand():
    view = catalog_view()
    catalog = load_catalog()
    assert set(view) == {"revision", "brands", "skipped_count"}
    assert view["revision"] == catalog.revision and view["skipped_count"] == len(catalog.skipped)
    assert list(view["brands"]) == sorted(view["brands"]) == ["honda", "hyundai", "toyota"]
    for brand, platforms in view["brands"].items():
        ids = [p["id"] for p in platforms]
        assert ids == sorted(ids)
        assert all(catalog.platforms[p["id"]].brand == brand for p in platforms)
    assert sum(len(platforms) for platforms in view["brands"].values()) == len(catalog.platforms) == 121
    corolla = next(p for p in view["brands"]["toyota"] if p["id"] == "TOYOTA_COROLLA")
    assert corolla == {"id": "TOYOTA_COROLLA", "names": ["Toyota Corolla 2017-19"], "buses": ["pt", "radar"]}
    assert len(json.dumps(view)) < 20_000  # the platform list only, no ECUs or firmware data


# --- car_draft --------------------------------------------------------------------------------------------------------


def test_car_draft_sets_car_and_removes_dbc_and_restbus():
    restbus = profile_text("wheel_speed_demo")
    assert load_profile_text(restbus).restbus is not None
    drafted = load_profile_text(car_draft(restbus, {"platform": "TOYOTA_COROLLA", "bus": "pt"}))
    assert (drafted.restbus, drafted.dbc) == (None, None)
    assert (drafted.car.platform, drafted.car.bus) == ("TOYOTA_COROLLA", "pt")

    manual = changed_draft(
        profile_text("ice_default"), {"filename": "powertrain.dbc", "sha256": "a" * 64, "messages": []}
    )
    assert load_profile_text(manual).dbc is not None
    drafted = load_profile_text(car_draft(manual, {"sha256": "b" * 64, "filename": "bench.dbc"}))
    assert drafted.dbc is None
    assert (drafted.car.sha256, drafted.car.filename, drafted.car.platform) == ("b" * 64, "bench.dbc", None)


def test_car_draft_adds_drive_scenario_once():
    first = car_draft(profile_text("ice_default"), {"platform": "TOYOTA_COROLLA"})
    paths = [signal.path for signal in load_profile_text(first).scenario.signals]
    assert paths == ["vehicle.speed", "engine.rpm"]
    again = car_draft(first, {"platform": "HONDA_CIVIC", "rule": "honda"})
    assert load_profile_text(again).scenario == load_profile_text(first).scenario
    assert again.count("path: vehicle.speed") == 1


def test_the_drive_scenario_is_the_one_the_demo_profile_uses():
    demo = load_profile_text(profile_text("car_toyota_demo"))
    drafted = load_profile_text(car_draft(profile_text("ice_default"), {"platform": "TOYOTA_COROLLA"}))
    assert drafted.scenario == demo.scenario


def test_car_draft_keeps_the_scenario_signals_the_working_copy_already_drives():
    own = profile_text("ice_default") + "scenario:\n  signals:\n    - {path: vehicle.speed, type: constant, value: 55}\n"
    drafted = load_profile_text(car_draft(own, {"platform": "TOYOTA_COROLLA"}))
    assert [(s.path, s.type) for s in drafted.scenario.signals] == [
        ("vehicle.speed", "constant"), ("engine.rpm", "stepped"),
    ]


def test_car_draft_does_not_drive_an_engine_the_vehicle_does_not_have():
    data = read_yaml(profile_text("ice_default"), "profile")
    del data["vehicle"]["engine"]
    data["vehicle"].update(type="bev", battery={"soc": 80})
    out = io.StringIO()
    YAML(typ="safe").dump(data, out)
    drafted = load_profile_text(car_draft(out.getvalue(), {"platform": "TOYOTA_COROLLA"}))
    assert [s.path for s in drafted.scenario.signals] == ["vehicle.speed"]


def test_car_draft_keeps_comments_and_key_order():
    text = profile_text("car_toyota_demo").replace("car:\n", "# keep this note\ncar:\n  # the bus\n", 1)
    text += "\n# personal working copy\n"
    result = car_draft(text, {"platform": "HONDA_CIVIC", "periods": {"WHEEL_SPEEDS": 20}})
    assert "# keep this note" in result and "# the bus" in result and "# personal working copy" in result
    assert "A whole 2017-19 Toyota Corolla" in result
    original_keys = list(read_yaml(text, "profile"))
    assert list(read_yaml(result, "profile")) == original_keys
    assert load_profile_text(result).ecus == load_profile_text(text).ecus


def test_car_draft_changing_the_source_drops_the_old_source():
    uploaded = car_draft(profile_text("ice_default"), {"sha256": "c" * 64, "filename": "bench.dbc"})
    catalog = load_profile_text(car_draft(uploaded, {"platform": "TOYOTA_COROLLA", "bus": "radar"})).car
    assert (catalog.platform, catalog.bus, catalog.sha256, catalog.filename) == ("TOYOTA_COROLLA", "radar", None, None)
    again = load_profile_text(car_draft(uploaded, {"sha256": "d" * 64, "filename": "other.dbc"})).car
    assert (again.platform, again.sha256, again.filename) == (None, "d" * 64, "other.dbc")
    radar = car_draft(uploaded, {"platform": "TOYOTA_COROLLA", "bus": "radar"})
    switched = load_profile_text(car_draft(radar, {"platform": "HONDA_CIVIC"})).car
    assert switched.bus == "pt"  # the old platform's bus does not follow the car to a platform that may not have it


def test_car_draft_periods_replace_the_whole_mapping_and_an_empty_one_removes_it():
    first = car_draft(profile_text("ice_default"), {"platform": "TOYOTA_COROLLA", "periods": {"A": 20, "B": 30}})
    assert load_profile_text(first).car.periods == {"A": 20, "B": 30}
    second = car_draft(first, {"periods": {"C": 40}, "log_periods": {"D": 50}})
    car = load_profile_text(second).car
    assert (car.periods, car.log_periods, car.platform) == ({"C": 40}, {"D": 50}, "TOYOTA_COROLLA")
    third = car_draft(second, {"periods": {}})
    assert "periods" not in read_yaml(third, "working copy")["car"]
    car = load_profile_text(third).car
    assert (car.periods, car.log_periods) == ({}, {"D": 50})
    assert load_profile_text(car_draft(third, {"log_periods": {}})).car.log_periods == {}


def test_car_draft_none_removes_a_rule():
    ruled = car_draft(profile_text("ice_default"), {"platform": "TOYOTA_COROLLA", "rule": "honda", "rule_file": "e" * 64})
    car = load_profile_text(ruled).car
    assert (car.rule, car.rule_file) == ("honda", "e" * 64)
    car = load_profile_text(car_draft(ruled, {"rule": None, "rule_file": None})).car
    assert (car.rule, car.rule_file) == (None, None)


@pytest.mark.parametrize(("selection", "message"), [
    ({"platform": "TOYOTA_COROLLA", "colour": "red"}, "colour"),
    ({"restbus": {}}, "restbus"),
    ({"platform": "NO_SUCH_CAR"}, "unknown platform"),
    ({"platform": "TOYOTA_COROLLA", "bus": "chassis"}, "no bus"),
    ({"sha256": "a" * 64}, "filename"),
    ({"platform": "TOYOTA_COROLLA", "periods": {"A": 5}}, "periods"),
    ({"platform": "TOYOTA_COROLLA", "periods": ["A"]}, "periods"),
    ({"sha256": "a" * 64, "filename": "../x"}, "path"),
])
def test_car_draft_refuses_what_the_schema_or_the_selection_refuses(selection, message):
    with pytest.raises(ValueError, match=message):
        car_draft(profile_text("ice_default"), selection)


def test_car_draft_without_a_source_is_a_config_error():
    with pytest.raises(ConfigError, match="platform"):
        car_draft(profile_text("ice_default"), {"rule": "toyota"})


# --- check ------------------------------------------------------------------------------------------------------------


def test_check_counts_match_message_states(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    digest = RuleStore().import_text(
        "version: 1\nrules:\n  - messages: [RULED]\n    checksum: {signal: CHECKSUM, algorithm: xor}\n"
    )
    text, _ = stored_upload(tmp_path, monkeypatch, [
        ("PLAIN", 0x100, 8, False, 20, False),
        ("TOYOTA", 0x101, 8, False, 20, True),
        ("RULED", 0x102, 8, False, 20, True),
        ("WIDE", 0x103, 16, False, 20, False),
    ], rule="toyota", rule_file=digest)
    result = check(text, VCAN)
    states = {m["name"]: (m["integrity"], m["skipped"]) for m in result["messages"]}
    assert states == {
        "PLAIN": ("none", None), "TOYOTA": ("valid", None), "RULED": ("user_rule", None),
        "WIDE": ("unsupported", "CAN FD"),
    }
    assert result["counts"] == {"valid": 1, "counter_only": 0, "user_rule": 1, "none": 1, "skipped": 1}
    assert sum(result["counts"].values()) == len(result["messages"])
    assert result["rule"] == "toyota" and result["platform"] is None
    assert result["dbc"]["filename"] == "bench.dbc"
    assert result["warnings"] == []


def test_check_counts_a_counter_without_a_rule_as_counter_only(tmp_path, monkeypatch):
    text, _ = stored_upload(tmp_path, monkeypatch, [("A", 0x100, 8, False, 20, True)])
    assert check(text, VCAN)["counts"] == {"valid": 0, "counter_only": 1, "user_rule": 0, "none": 0, "skipped": 0}


def test_check_lists_what_the_table_needs_and_nothing_bulky():
    result = check(profile_text("car_toyota_demo"), VCAN)
    assert set(result) == {"platform", "bus", "dbc", "revision", "rule", "messages", "counts", "bus_load", "warnings"}
    assert result["platform"] == "TOYOTA_COROLLA" and result["bus"] == "pt" and result["rule"] == "toyota"
    assert set(result["messages"][0]) == {
        "name", "id", "extended", "dlc", "period_ms", "period_source", "clamped", "integrity", "detail",
        "counters", "checksums", "skipped",
    }
    assert len(result["messages"]) == sum(result["counts"].values())
    assert len(json.dumps(result)) < 30_000
    assert result["bus_load"] == {
        "interface": "vcan0", "estimate": result["bus_load"]["estimate"], "bitrate": 500000, "assumed": True,
        "worst_case": True, "refused": None, "over": [], "unavailable": None,
    }
    assert 0 < result["bus_load"]["estimate"] < 1


def test_check_on_a_physical_link_reports_the_load_against_its_bitrate():
    result = check(profile_text("car_toyota_demo"), CAN0)["bus_load"]
    assert (result["bitrate"], result["assumed"], result["refused"], result["unavailable"]) == (500000, False, None, None)
    assert 0 < result["estimate"] < 0.7 and result["interface"] == "can0"


def test_check_returns_a_refused_bus_load_as_data_with_the_messages(tmp_path, monkeypatch):
    text, _ = stored_upload(tmp_path, monkeypatch, many_messages(), rule="toyota")
    result = check(text, CAN0)
    refusal = result["bus_load"]
    assert "above the 70% ceiling" in refusal["refused"] and "MSG_" in refusal["refused"]
    assert len(refusal["over"]) == 3 and set(refusal["over"][0]) == {"name", "share"}
    assert refusal["estimate"] > 0.7 and refusal["bitrate"] == 500000 and refusal["unavailable"] is None
    assert len(result["messages"]) == 150 and result["counts"]["valid"] == 50  # still listed for Step 2
    down = check(text, LinkInfo("can0", False, None, None))["bus_load"]
    assert "no bitrate" in down["refused"] and down["estimate"] is None and down["over"] == []


def test_check_for_an_interface_that_does_not_exist_says_why_and_still_lists_messages(monkeypatch):
    def unreadable(interface: str) -> LinkInfo:
        raise ValueError(f"Cannot read {interface}: Device \"{interface}\" does not exist.")

    monkeypatch.setattr(car_workflow, "read_link", unreadable)
    text = profile_text("car_toyota_demo").replace("vcan0", "nosuch0")
    result = check(text)
    assert result["bus_load"]["unavailable"] == 'Cannot read nosuch0: Device "nosuch0" does not exist.'
    assert result["bus_load"]["estimate"] is None and result["bus_load"]["refused"] is None
    assert result["messages"] and sum(result["counts"].values()) == len(result["messages"])


def test_check_reads_the_interface_when_no_link_is_given(monkeypatch):
    asked: list[str] = []

    def read(interface: str) -> LinkInfo:
        asked.append(interface)
        return VCAN

    monkeypatch.setattr(car_workflow, "read_link", read)
    assert check(profile_text("car_toyota_demo"))["bus_load"]["assumed"] is True
    assert asked == ["vcan0"]


def test_check_warns_about_a_misspelled_period_name_and_ignores_log_periods(tmp_path, monkeypatch):
    text, _ = stored_upload(tmp_path, monkeypatch, [("SPEED", 0x100, 8, False, 20, False)],
                            periods={"SPEEED": 40, "SPEED": 30}, log_periods={"FROM_ANOTHER_LOG": 50})
    result = check(text, VCAN)
    assert result["warnings"] == ["periods: 'SPEEED' is not a message of this DBC, so its override is ignored"]
    speed = result["messages"][0]
    assert (speed["period_ms"], speed["period_source"]) == (30, "override")


def test_check_caps_the_warning_list(tmp_path, monkeypatch):
    names = {f"BAD_{index:03d}": 20 for index in range(60)}
    text, _ = stored_upload(tmp_path, monkeypatch, [("A", 0x100, 8, False, 20, False)], periods=names)
    warnings = check(text, VCAN)["warnings"]
    assert len(warnings) == 21 and warnings[-1] == "periods: 40 more names are not messages of this DBC"


def test_check_of_a_profile_without_a_car_is_a_config_error():
    with pytest.raises(ConfigError, match="car"):
        check(profile_text("ice_default"), VCAN)


def test_check_reports_an_unknown_dbc_by_its_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    text = car_draft(profile_text("ice_default"), {"sha256": "ab" * 32, "filename": "bench.dbc"})
    with pytest.raises(ConfigError, match="ab" * 32):
        check(text, VCAN)


# --- the transmission review's messages -------------------------------------------------------------------------------


def test_transmittable_lists_one_stamped_sample_of_each_sent_message(tmp_path, monkeypatch):
    text, _ = stored_upload(tmp_path, monkeypatch, [
        ("COUNTED", 0x100, 8, False, 20, True), ("WIDE", 0x103, 16, False, 20, False),
        ("EXT", 0x1ABCDE, 8, True, 50, False),
    ], rule="toyota")
    listed = car_workflow.transmittable(text)
    assert [(m["name"], m["id"], m["extended"], m["dlc"], m["period_ms"]) for m in listed] == [
        ("COUNTED", 0x100, False, 8, 20), ("EXT", 0x1ABCDE, True, 8, 50),
    ]
    counted = listed[0]
    assert len(counted["raw"].split()) == 8 and counted["raw"] == counted["raw"].upper()
    again = car_workflow.transmittable(text)
    assert again == listed  # a fresh sample each time: the first frame, not the next one


def test_transmittable_refuses_a_car_that_sends_nothing(tmp_path, monkeypatch):
    text, _ = stored_upload(tmp_path, monkeypatch, [("WIDE", 0x103, 16, False, 20, False)])
    with pytest.raises(ConfigError, match="no message can be transmitted"):
        car_workflow.transmittable(text)


# --- the run status ---------------------------------------------------------------------------------------------------


def test_run_status_keeps_a_summary_and_drops_the_message_list():
    messages = check(profile_text("car_toyota_demo"), VCAN)["messages"]
    car = {"platform": "TOYOTA_COROLLA", "bus": "radar", "dbc": {"filename": "x.dbc", "sha256": "f" * 64, "name": "x"},
           "revision": "r", "rule": "toyota", "bus_load": {"estimate": 0.1}, "messages": messages}
    status = {"state": "Running", "id": "1", "car": car}
    summary = car_workflow.run_status(status)
    assert status["car"] is car  # the Manager's own copy keeps the whole list
    assert summary["car"] == {
        "platform": "TOYOTA_COROLLA", "bus": "radar", "dbc": {"filename": "x.dbc", "sha256": "f" * 64},
        "revision": "r", "rule": "toyota", "bus_load": {"estimate": 0.1}, "counts": car_workflow.integrity_counts(messages),
    }
    assert summary["state"] == "Running" and "messages" not in json.dumps(summary["car"])
    assert car_workflow.run_status({"state": "Stopped", "car": None})["car"] is None


def test_integrity_counts_name_every_state_even_at_zero():
    assert car_workflow.integrity_counts([]) == {"valid": 0, "counter_only": 0, "user_rule": 0, "none": 0, "skipped": 0}
    counts = car_workflow.integrity_counts([
        {"integrity": "valid", "skipped": None}, {"integrity": "valid", "skipped": None},
        {"integrity": "unsupported", "skipped": "CAN FD"},
    ])
    assert counts == {"valid": 2, "counter_only": 0, "user_rule": 0, "none": 0, "skipped": 1}
    assert Counter(counts).total() == 3


# --- log_periods ------------------------------------------------------------------------------------------------------


def log_line(ms: int, ident: str, interface: str = "can0") -> bytes:
    seconds, rest = divmod(ms, 1000)
    return f"({1000 + seconds}.{rest:03d}000) {interface} {ident}#00\n".encode()


def synthetic_log() -> bytes:
    lines = [log_line(20 * n, "100") for n in range(6)]            # MSG_A, standard, every 20 ms
    lines += [log_line(50 * n, "001ABCDE") for n in range(4)]      # MSG_EXT, extended, every 50 ms
    lines += [log_line(2000, "101")]                               # MSG_ONE: one frame, no measurement
    lines += [log_line(10 * n, "7FF") for n in range(5)]           # an id the DBC does not have
    lines += [log_line(5 * n, "00000100") for n in range(5)]       # id 0x100 extended: another message
    lines += [log_line(1 * n, "200") for n in range(5)]            # 1 ms: below the 10 ms the profile accepts
    return b"".join(sorted(lines, key=lambda line: float(line.split()[0][1:-1])))


LOG_DBC = synthetic_dbc([
    ("MSG_A", 0x100, 8, False, None, False), ("MSG_EXT", 0x1ABCDE, 8, True, None, False),
    ("MSG_ONE", 0x101, 8, False, None, False), ("MSG_FAST", 0x200, 8, False, None, False),
])


def synthetic_db() -> ImportedDb:
    return ImportedDb(LOG_DBC, hashlib.sha256(LOG_DBC).hexdigest())


def test_log_periods_maps_ids_to_message_names():
    assert log_periods(synthetic_db(), synthetic_log()) == {"MSG_A": 20, "MSG_EXT": 50, "MSG_FAST": 10}


def test_measure_log_reports_ids_the_dbc_does_not_have_and_what_was_moved_into_range():
    measured = measure_log(synthetic_db(), synthetic_log())
    assert measured.periods == {"MSG_A": 20, "MSG_EXT": 50, "MSG_FAST": 10}
    assert measured.clamped == ("MSG_FAST",)
    # ids measured in the log that no message of the DBC has, standard ids first
    assert measured.unmatched == ["0x7FF", "0x00000100"]


def test_log_periods_refuses_what_the_replay_path_refuses():
    db = synthetic_db()
    with pytest.raises(ValueError, match="Line 1: expected a timestamped Classical CAN candump frame"):
        log_periods(db, b"not a candump line\n")
    with pytest.raises(ValueError, match="no frames"):
        log_periods(db, b"")
