import copy
import json
from pathlib import Path

import pytest

from ecu_simulator import app
from ecu_simulator.config import load_profile
from ecu_simulator.observe import snapshots
from ecu_simulator.observe.handoff import HandOff
from ecu_simulator.observe.publisher import Publisher

PROFILES = Path(app.__file__).parent / "profiles"


def runtime(name="ice_default.yaml"):
    return app.build_runtime(app.RuntimeConfig.build(load_profile(PROFILES / name), "vcan0"))


@pytest.mark.parametrize("name", ["ice_default.yaml", "ice_scenario.yaml"])
def test_snapshots_serialise_and_fit(name):
    rt = runtime(name)
    for snap in (snapshots.vehicle(rt), snapshots.dtcs(rt), snapshots.ecus(rt)):
        json.dumps(snap)
    snapshots.check_state_size(rt)


def test_snapshots_never_mutate_and_never_call_sync():
    rt = runtime("ice_scenario.yaml")
    calls = []
    before = (copy.deepcopy(rt.vehicle.signals), [(s.code, s.pending, s.confirmed, s.indicator_requested) for e in rt.ecus for s in e.dtc_store], rt.runner.last_applied, rt.runner.pending_events)
    original_apply = rt.runner.apply
    rt.runner.apply = lambda t: calls.append(t)
    for _ in range(3):
        snapshots.vehicle(rt)
        snapshots.dtcs(rt)
        snapshots.ecus(rt)
        snapshots.state_message(rt)
    rt.runner.apply = original_apply
    after = (copy.deepcopy(rt.vehicle.signals), [(s.code, s.pending, s.confirmed, s.indicator_requested) for e in rt.ecus for s in e.dtc_store], rt.runner.last_applied, rt.runner.pending_events)
    assert before == after and calls == []


def test_vehicle_reports_as_of_the_last_application():
    rt = runtime("ice_scenario.yaml")
    assert snapshots.vehicle(rt)["as_of"] is None      # nothing applied yet
    rt.sync()
    assert snapshots.vehicle(rt)["as_of"] == rt.runner.last_applied


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
        snapshots.check_state_size(rt)


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
