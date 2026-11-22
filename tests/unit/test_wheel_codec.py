"""Independent fixtures use the physical wheel order and 0.01 km/h per bit."""

import pytest

from ecu_simulator.restbus import WheelCodec


@pytest.mark.parametrize(("speed", "word"), [(0, "1a6f"), (40, "2a0f"), (260, "7fff")])
def test_four_healthy_wheels(speed, word):
    codec = WheelCodec()
    assert codec.encode(speed) == bytes.fromhex(word * 4)


def test_decode_asymmetric_words_and_ignore_fault_bit():
    codec = WheelCodec()
    # Front right fault bit set, then FR=0, FL=1, RR=40 and RL=100.
    assert codec.decode(bytes.fromhex("9a6f 1ad3 2a0f 417f")) == pytest.approx({"FR": 0, "FL": 1, "RR": 40, "RL": 100})


@pytest.mark.parametrize("length", [0, 7, 9])
def test_wrong_dlc(length):
    with pytest.raises(ValueError, match="8 bytes"):
        WheelCodec().decode(bytes(length))


def test_metadata_identifies_database_and_frame():
    assert WheelCodec().metadata() == {
        "revision": "79ddcc7bb72d557fc5b20d038c0fad9c9d423446",
        "sha256": "29ee5c92017b46b8db2fe7566e6f765fe52efcddfb3b426dff929c879d70e066",
        "message": "WHEEL_SPEEDS",
        "can_id": 0xAA,
        "period_ms": 20,
        "dbc": "toyota_nodsu_pt_generated.dbc",
    }


@pytest.mark.parametrize("speed", [-100, 260.01, float("nan"), float("inf")])
def test_reject_unrepresentable_speed(speed):
    with pytest.raises(ValueError):
        WheelCodec().encode(speed)


def test_altered_database_blocks_startup(monkeypatch, tmp_path):
    (tmp_path / "toyota_nodsu_pt_generated.dbc").write_bytes(b"wrong database")
    monkeypatch.setattr("ecu_simulator.restbus.files", lambda package: tmp_path)
    with pytest.raises(ValueError, match="unexpected SHA-256"):
        WheelCodec()
