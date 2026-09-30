import copy
import json
import math
from pathlib import Path

import pytest

from ecu_simulator import app
from ecu_simulator.config import load_profile
from ecu_simulator.observe import availability, snapshots
from ecu_simulator.observe.handoff import HandOff
from ecu_simulator.observe.publisher import Publisher

PROFILES = Path(app.__file__).parent / "profiles"


def runtime(name="ice_default.yaml"):
    return app.build_runtime(app.RuntimeConfig.build(load_profile(PROFILES / name), "vcan0"))


def missing(rt):
    return availability.unavailable(rt.config.profile)


@pytest.mark.parametrize("name", ["ice_default.yaml", "ice_scenario.yaml"])
def test_snapshots_serialise_and_fit(name):
    rt = runtime(name)
    for snap in (snapshots.vehicle(rt, missing(rt)), snapshots.dtcs(rt), snapshots.ecus(rt)):
        json.dumps(snap)
    snapshots.check_state_size(rt, missing(rt))


def test_snapshots_never_mutate_and_never_call_sync():
    rt = runtime("ice_scenario.yaml")
    calls = []
    before = (copy.deepcopy(rt.vehicle.signals), [(s.code, s.pending, s.confirmed, s.indicator_requested) for e in rt.ecus for s in e.dtc_store], rt.runner.last_applied, rt.runner.pending_events)
    original_apply = rt.runner.apply
    rt.runner.apply = lambda t: calls.append(t)
    for _ in range(3):
        snapshots.vehicle(rt, missing(rt))
        snapshots.dtcs(rt)
        snapshots.ecus(rt)
        snapshots.state_message(rt, missing(rt))
    rt.runner.apply = original_apply
    after = (copy.deepcopy(rt.vehicle.signals), [(s.code, s.pending, s.confirmed, s.indicator_requested) for e in rt.ecus for s in e.dtc_store], rt.runner.last_applied, rt.runner.pending_events)
    assert before == after and calls == []


def test_vehicle_reports_as_of_the_last_application():
    rt = runtime("ice_scenario.yaml")
    assert snapshots.vehicle(rt, missing(rt))["as_of"] is None      # nothing applied yet
    rt.sync()
    assert snapshots.vehicle(rt, missing(rt))["as_of"] == rt.runner.last_applied


def test_vehicle_shape_carries_the_unavailable_list_beside_the_stored_value():
    rt = runtime()
    v = snapshots.vehicle(rt, ("vehicle.odometer",))
    assert set(v) == {"kind", "vin", "signals", "as_of", "unavailable", "nonfinite"}  # §8.2
    assert v["unavailable"] == ["vehicle.odometer"]
    assert v["signals"]["vehicle.odometer"] == 0                             # signals still carries it
    assert v["nonfinite"] == []
    message = json.loads(snapshots.state_message(rt, ("vehicle.odometer",)))
    assert set(message) == {"type", "vehicle", "dtcs"} and message["vehicle"] == v


def test_nonfinite_signals_become_null_and_are_listed_sorted():
    rt = runtime()
    rt.vehicle.set("engine.coolant_temp", float("nan"))
    rt.vehicle.set("engine.intake_temp", float("inf"))
    rt.vehicle.set("engine.throttle", float("-inf"))
    v = snapshots.vehicle(rt, missing(rt))
    assert v["signals"]["engine.coolant_temp"] is None
    assert v["signals"]["engine.intake_temp"] is None
    assert v["signals"]["engine.throttle"] is None
    assert v["nonfinite"] == sorted(["engine.coolant_temp", "engine.intake_temp", "engine.throttle"])


class _FakeRuntime:
    """A stand-in carrying only what ``snapshots.vehicle`` reads, to reach a bool signal
    (``charging.active``), which no on-disk profile currently has (only ICE profiles exist).
    """

    def __init__(self, vehicle_state, runner=None):
        self.vehicle = vehicle_state
        self.runner = runner


def test_finite_values_of_every_type_are_unchanged():
    rt = runtime()
    v = snapshots.vehicle(rt, missing(rt))
    assert v["signals"]["engine.coolant_temp"] == 90.0          # finite float
    assert v["signals"]["engine.rpm"] == 800                    # int
    assert v["signals"]["vehicle.vin"] == "TESTVIN0123456789"   # string
    assert isinstance(v["signals"]["engine.coolant_temp"], float)

    from ecu_simulator.vehicle.state import BevPowertrain, CommonState, VehicleState
    bev = VehicleState(CommonState(vin="BEVVIN"), BevPowertrain())
    bev.set("charging.active", True)
    fake = snapshots.vehicle(_FakeRuntime(bev), ())
    assert fake["signals"]["charging.active"] is True            # bool unchanged
    assert fake["nonfinite"] == []


def test_nonfinite_is_empty_list_when_none_are_nonfinite():
    rt = runtime()
    assert snapshots.vehicle(rt, missing(rt))["nonfinite"] == []


def test_unavailable_wins_precedence_over_nonfinite():
    rt = runtime()
    rt.vehicle.set("vehicle.odometer", float("nan"))
    v = snapshots.vehicle(rt, ("vehicle.odometer",))
    assert v["unavailable"] == ["vehicle.odometer"]
    assert v["nonfinite"] == []                    # listed only in unavailable
    assert v["signals"]["vehicle.odometer"] is None  # still sent as null


def _raising_parse_constant(token):
    raise ValueError(f"unexpected constant: {token}")


def test_state_message_parses_with_a_parse_constant_guard():
    rt = runtime()
    rt.vehicle.set("engine.coolant_temp", float("nan"))
    text = snapshots.state_message(rt, missing(rt))
    parsed = json.loads(text, parse_constant=_raising_parse_constant)
    assert parsed["vehicle"]["signals"]["engine.coolant_temp"] is None


def test_nonfinite_values_are_not_mutated_and_a_second_call_agrees():
    rt = runtime()
    rt.vehicle.set("engine.coolant_temp", float("nan"))
    rt.vehicle.set("engine.intake_temp", float("inf"))
    before = copy.deepcopy(rt.vehicle.signals)
    first = snapshots.vehicle(rt, missing(rt))
    snapshots.state_message(rt, missing(rt))
    second = snapshots.vehicle(rt, missing(rt))
    assert math.isnan(rt.vehicle.get("engine.coolant_temp"))
    assert rt.vehicle.get("engine.intake_temp") == float("inf")
    after = rt.vehicle.signals
    assert math.isnan(after.pop("engine.coolant_temp")) and math.isnan(before.pop("engine.coolant_temp"))
    assert after == before                          # every other stored value is unchanged
    assert first == second


def test_guard_catches_a_nonfinite_dtc_field_the_sanitiser_does_not_look_at(monkeypatch):
    rt = runtime()
    bad = {"engine": {"codes": [{"code": "P0001", "pending": True, "confirmed": True,
                                  "indicator_requested": False}],
                       "mil": False, "extra": float("nan")}}
    monkeypatch.setattr(snapshots, "dtcs", lambda runtime: bad)
    payload = {"type": "state", "vehicle": snapshots.vehicle(rt, missing(rt)), "dtcs": bad}
    json.dumps(payload)                              # default json.dumps: no error, proving the point
    with pytest.raises(ValueError, match="Out of range float values are not JSON compliant"):
        snapshots.state_message(rt, missing(rt))


def test_guard_catches_a_nonfinite_as_of_the_sanitiser_does_not_look_at():
    rt = runtime("ice_scenario.yaml")
    rt.runner._last_applied = float("inf")            # the brief permits setting this private field
    payload = {"type": "state", "vehicle": snapshots.vehicle(rt, missing(rt)), "dtcs": snapshots.dtcs(rt)}
    json.dumps(payload)                               # default json.dumps: no error, proving the point
    with pytest.raises(ValueError, match="Out of range float values are not JSON compliant"):
        snapshots.state_message(rt, missing(rt))


def test_dtcs_and_ecus_shape():
    rt = runtime()
    d = snapshots.dtcs(rt)
    assert d["engine"]["mil"] is False and {x["code"] for x in d["engine"]["codes"]} == {"B1477", "P0001"}
    e = snapshots.ecus(rt)["engine"]
    assert {p["name"] for p in e["protocols"]} == {"obd", "uds"}
    assert any(ep["name"] == "engine.obd_functional" and ep["reply_via"] == "engine.obd_physical" for ep in e["endpoints"])


def test_oversized_state_is_refused():
    rt = runtime()
    rt.vehicle.common.vin = "V" * (300 * 1024)   # test-only object, discarded after the test
    with pytest.raises(ValueError, match="256 KiB"):
        snapshots.check_state_size(rt, missing(rt))


def test_status_has_every_section_5_field_including_profile():
    rt = runtime("ice_scenario.yaml")
    publisher = Publisher(HandOff(), rt.router, {})
    path = str(PROFILES / "ice_scenario.yaml")
    status = snapshots.status(rt, publisher, issued=0, started_at=1_790_000_000.0, version="9.9.9", profile=path)
    json.dumps(status)
    assert set(status) == {"version", "interface", "profile", "started_at", "uptime_s", "scenario", "api"}
    assert (status["profile"], status["version"], status["interface"]) == (path, "9.9.9", "vcan0")
    assert status["scenario"] == {"enabled": True, "t_last_applied": None, "pending_events": rt.runner.pending_events}
    assert status["api"]["issued_seq"] == 0 and status["api"]["clients"] == 0
