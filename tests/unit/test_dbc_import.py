import hashlib
from importlib.resources import files

import pytest

from ecu_simulator.dbc import DbcStore

SIMPLE = b'''BO_ 257 Climate: 4 XXX
 SG_ Mode M : 0|4@1+ (1,0) [0|15] "" XXX
 SG_ Indoor m1 : 15|16@0- (0.25,-5) [-40|80] "degC" XXX
 SG_ Outdoor m2 : 8|16@1- (0.5,6) [0|0] "degC" XXX
 SG_ Voltage : 24|8@1+ (0.05,6) [0|0] "V" XXX
'''


def test_store_catalog_and_codecs(tmp_path):
    store = DbcStore(tmp_path)
    catalog = store.import_file("climate.dbc", SIMPLE)
    digest = hashlib.sha256(SIMPLE).hexdigest()
    db = DbcStore(tmp_path).load(digest)
    assert db.catalog("climate.dbc") == catalog
    message = db.message("Climate")
    assert message["id"] == 257
    assert message["period_ms"] is None
    assert message["tx_issue"] is None
    signals = {s["name"]: s for s in message["signals"]}
    assert signals["Mode"]["default"] == 1
    assert signals["Voltage"]["min"] == signals["Voltage"]["default"] == 6
    assert signals["Outdoor"]["mux_ids"] == [2]
    assert db.encode("Climate", {"Indoor": -6}) == bytes.fromhex("01 ff fc 00")
    data = db.encode("Climate", {"Mode": 2, "Outdoor": 5})
    assert data == bytes.fromhex("02 fe ff 00")
    decoded = db.decode(257, False, data)
    assert decoded["signals"]["Outdoor"] == {"value": 5, "unit": "degC"}
    assert "Indoor" not in decoded["signals"]
    assert db.decode(257, True, data) is None
    assert db.decode(257, False, b"")["error"]
    for values in ({"Indoor": 100}, {"Outdoor": 3}, {"Missing": 1}, {"Mode": 3}, {"Mode": 1.5}, {"Indoor": float("nan")}):
        with pytest.raises(ValueError):
            db.encode("Climate", values)


@pytest.mark.parametrize("extra", [SIMPLE, SIMPLE.replace(b"257", b"258"), SIMPLE.replace(b"Climate", b"Climate2"), b'CM_ "IMPORT _more.dbc";'])
def test_reject_invalid_sources(tmp_path, extra):
    with pytest.raises(ValueError):
        DbcStore(tmp_path).import_file("bad.dbc", SIMPLE + extra)
    assert not list(tmp_path.glob("*.dbc"))


def test_layout_and_integrity_issues(tmp_path):
    store = DbcStore(tmp_path)
    raw = SIMPLE + b'''BO_ 1 Mistake: 1 XXX
 SG_ A : 0|8@1+ (1,0) [0|0] "" XXX
 SG_ B : 0|8@1+ (1,0) [0|0] "" XXX
BO_ 2 Long: 12 XXX
 SG_ A : 0|8@1+ (1,0) [0|0] "" XXX
BO_ 3 Integrity: 1 XXX
 SG_ Seq_No_Cnt : 0|8@1+ (1,0) [0|0] "" XXX
'''
    db = store.load(store.import_file("mixed.dbc", raw)["sha256"])
    for name in ("Mistake", "Long", "Integrity"):
        assert db.message(name)["tx_issue"]
        with pytest.raises(ValueError):
            db.encode(name, {})
    assert db.decode(1, False, b"\0")["error"]
    assert db.decode(3, False, b"\x01")["signals"]["Seq_No_Cnt"]["value"] == 1


def test_real_generated_toyota(tmp_path):
    raw = files("ecu_simulator._opendbc").joinpath("toyota_nodsu_pt_generated.dbc").read_bytes()
    store = DbcStore(tmp_path)
    db = store.load(store.import_file("toyota.dbc", raw)["sha256"])
    assert db.message("PCS_HUD")["tx_issue"]
    assert db.message("WHEEL_SPEEDS")["tx_issue"] is None
    payload = db.encode("WHEEL_SPEEDS", {"WHEEL_SPEED_FL": 40})
    assert db.decode(0xaa, False, payload)["signals"]["WHEEL_SPEED_FL"]["value"] == pytest.approx(40)


def test_tampered_or_missing_artifact(tmp_path):
    store = DbcStore(tmp_path)
    digest = store.import_file("sample.dbc", SIMPLE)["sha256"]
    (tmp_path / f"{digest}.dbc").write_bytes(b"altered")
    for invalid in (digest, "../bad", "0" * 64):
        with pytest.raises(ValueError):
            store.load(invalid)


def test_declared_cycle_and_extended_identity(tmp_path):
    raw = SIMPLE + b'''BO_ 2147483905 Extended: 1 XXX
 SG_ Status : 0|8@1+ (1,0) [0|255] "" XXX
BA_DEF_ BO_ "GenMsgCycleTime" INT 0 60000;
BA_ "GenMsgCycleTime" BO_ 2147483905 25;
'''
    store = DbcStore(tmp_path)
    db = store.load(store.import_file("extended.dbc", raw)["sha256"])
    assert db.message("Extended")["period_ms"] == 25
    assert db.decode(257, True, b"\x05")["name"] == "Extended"


@pytest.mark.parametrize("bad", [
    b'BO_ 55 Bad: 65 XXX\n',
    b'BO_ 55 Bad: 2 XXX\n SG_ S : 512|2@1+ (1,0) [0|0] "" XXX\n',
])
def test_invalid_dimensions_rejected_before_compiling(tmp_path, bad):
    with pytest.raises(ValueError, match="CAN FD dimensions"):
        DbcStore(tmp_path).import_file("invalid.dbc", bad)
