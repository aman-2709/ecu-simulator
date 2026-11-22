import asyncio
import socket
from unittest.mock import Mock

import pytest

from ecu_simulator import app, publisher
from ecu_simulator.cli import default_profile_path
from ecu_simulator.config import ConfigError, load_yaml, parse_profile
from tests.unit.test_cli import isolated_logging  # noqa: F401

HASH = "abc0" * 16


def profile(messages, *, keep_wheels=False):
    data = load_yaml(default_profile_path().with_name("wheel_speed_demo.yaml"))
    if not keep_wheels:
        del data["restbus"]
    data["dbc"] = {"filename": "example.dbc", "sha256": HASH, "messages": messages}
    return app.RuntimeConfig.build(parse_profile(data))


def selection(name="Speed", **kwargs):
    return {"name": name, "period_ms": 20, "values": {"Speed": "vehicle.speed"}, **kwargs}


class Database:
    def __init__(self, frame_id=0x421, maximum=255, extended=False):
        self.frame_id = frame_id
        self.maximum = maximum
        self.extended = extended

    def message(self, name):
        return {
            "name": name,
            "id": self.frame_id,
            "extended": self.extended,
            "dlc": 1,
            "tx_issue": None,
            "signals": [{"name": "Speed", "multiplexer": False}],
        }

    def encode(self, name, values):
        if not 0 <= values["Speed"] <= self.maximum:
            raise ValueError("Speed exceeds limits")
        return bytes([int(values["Speed"])])


@pytest.mark.parametrize(
    "messages,keep_wheels,error",
    [
        ([selection(), selection()], False, "duplicate"),
        ([selection(period_ms=0)], False, "period_ms"),
        ([selection(values={"Speed": "battery.soc"})], False, "battery.soc"),
        ([selection(values={"Speed": True})], False, "values"),
        ([], True, "restbus"),
    ],
)
def test_reject_invalid_profile(messages, keep_wheels, error):
    with pytest.raises(ConfigError, match=error):
        profile(messages, keep_wheels=keep_wheels)


def test_reject_future_scenario_value_before_start():
    with pytest.raises(ConfigError, match="Speed exceeds limits"):
        app.prepare_dbc(profile([selection()]), database=Database(maximum=10))


@pytest.mark.parametrize("frame_id", [0x7DF, 0x7E0, 0x7E8])
def test_collision_uses_identifier_and_format(frame_id):
    config = profile([selection()])
    with pytest.raises(ConfigError, match="conflict"):
        app.prepare_dbc(config, database=Database(frame_id))
    selected = app.prepare_dbc(config, database=Database(frame_id, extended=True))
    assert selected.metadata["messages"][0]["extended"] is True


def test_empty_selection_is_decode_only():
    prepared = app.prepare_dbc(profile([]), database=Database())
    assert not prepared.frames
    assert prepared.metadata == {"filename": "example.dbc", "sha256": HASH, "messages": []}


@pytest.mark.asyncio
async def test_multiple_messages_follow_independent_periods_and_formats(monkeypatch):
    sent = []
    stop = asyncio.Event()

    def send(frame):
        sent.append(publisher.FRAME.unpack(frame))
        if len(sent) == 3:
            stop.set()
        return len(frame)

    sock = Mock()
    sock.send.side_effect = send
    monkeypatch.setattr(publisher, "open_broadcast", lambda _: sock)
    frames = [
        publisher.CyclicFrame("Speed", 0x421, 0.01, lambda: b"\x13"),
        publisher.CyclicFrame("Power", socket.CAN_EFF_FLAG | 0x421, 10, lambda: b"AB"),
    ]
    ready = Mock()
    await asyncio.wait_for(publisher.publish_frames("vcan0", frames, None, ready, stop), 1)
    assert [(cid, data[:dlc]) for cid, dlc, data in sent] == [
        (0x421, b"\x13"),
        (socket.CAN_EFF_FLAG | 0x421, b"AB"),
        (0x421, b"\x13"),
    ]
    ready.assert_called_once()
    sock.close.assert_called_once()


def test_missing_decode_only_artifact_is_config_error(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with pytest.raises(ConfigError, match="Missing DBC artifact"):
        app.prepare_dbc(profile([]))


def test_require_fixed_multiplexer_selection_for_dynamic_source():
    db = Database()
    message = db.message("Speed")
    message["signals"][0]["is_mux"] = True
    db.message = lambda _: message
    with pytest.raises(ConfigError, match="selector"):
        app.prepare_dbc(profile([selection()]), database=db)


def test_integer_constant_preserves_full_signal_precision():
    value = (1 << 63) + 3
    config = profile([selection(values={"CounterFreeValue": value})])
    assert config.profile.dbc.messages[0].values["CounterFreeValue"] == value


def test_cli_validation_resolves_dbc_before_reporting_success(monkeypatch, tmp_path, isolated_logging):  # noqa: F811
    from ecu_simulator.cli import main

    path = tmp_path / "tester.yaml"
    path.write_text(profile([]).profile.model_dump_json(by_alias=True))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["validate-config", "--profile", str(path)]) == 2


@pytest.mark.asyncio
async def test_decode_only_run_reports_selected_dbc_without_raw_socket(monkeypatch):
    from unittest.mock import AsyncMock

    from ecu_simulator.dbc import DbcStore

    monkeypatch.setattr(DbcStore, "load", lambda *_: Database())
    raw = Mock(side_effect=AssertionError("decode-only opened a publisher"))
    monkeypatch.setattr(publisher, "open_broadcast", raw)
    stop = asyncio.Event()
    ready = Mock()
    await app.run(
        profile([]),
        install_signal_handlers=False,
        transport_factory=lambda *_: AsyncMock(),
        ready=stop.set,
        stop=stop,
        dbc_ready=ready,
    )
    ready.assert_called_once_with({"filename": "example.dbc", "sha256": HASH, "messages": []})
    raw.assert_not_called()


def test_preflight_checks_each_scenario_plateau_for_finite_values():
    config = profile([selection()])
    config.profile.scenario.signals[0].values = [0, float("nan"), 2]
    with pytest.raises(ConfigError, match="finite"):
        app.prepare_dbc(config, database=Database())


def test_validate_first_scenario_sample_instead_of_unused_base_value():
    config = profile([selection()])
    config.profile.scenario.signals[0].values = [40, 50]
    db = Database()
    checked = []
    encode = db.encode

    def encode_in_range(name, values):
        assert 20 <= values["Speed"] <= 80
        checked.append(values["Speed"])
        return encode(name, values)

    db.encode = encode_in_range
    app.prepare_dbc(config, database=db)
    assert checked == [40, 40, 50]
