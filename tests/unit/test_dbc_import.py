import hashlib
import json
from importlib.resources import files
from pathlib import Path

import pytest
from cantools.database.can import Database

from ecu_simulator.car.catalog import bundled_dbc_path, load_catalog
from ecu_simulator.dbc import DbcStore

VECTORS = Path(__file__).parents[1] / "fixtures" / "car" / "integrity_vectors.json"

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


def _catalog_db(tmp_path, name):
    return DbcStore(tmp_path).load(load_catalog().dbcs[name].sha256)


def test_signal_layout_is_exposed(tmp_path):
    toyota = _catalog_db(tmp_path, "toyota_nodsu_pt_generated")
    message = toyota.message("STEERING_LKA")
    counter = next(s for s in message["signals"] if s["name"] == "COUNTER")
    assert (counter["start"], counter["length"], counter["little_endian"]) == (6, 6, False)
    assert message["integrity_fields"] == ["COUNTER", "CHECKSUM"]

    store = DbcStore(tmp_path)
    simple = store.load(store.import_file("climate.dbc", SIMPLE)["sha256"]).message("Climate")
    layout = {s["name"]: (s["start"], s["length"], s["little_endian"]) for s in simple["signals"]}
    assert layout == {"Mode": (0, 4, True), "Indoor": (15, 16, False), "Outdoor": (8, 16, True), "Voltage": (24, 8, True)}
    assert simple["integrity_fields"] == [] and simple["layout_issue"] is None
    json.dumps(simple)


def test_manual_path_still_refuses_counter_checksum_messages(tmp_path):
    message = _catalog_db(tmp_path, "toyota_nodsu_pt_generated").message("STEERING_LKA")
    assert message["tx_issue"] == "Counter/checksum generation is unsupported for COUNTER, CHECKSUM"
    assert message["issues"] == [message["tx_issue"]]
    assert message["layout_issue"] is None
    with pytest.raises(ValueError, match="Counter/checksum generation is unsupported"):
        _catalog_db(tmp_path, "toyota_nodsu_pt_generated").encode("STEERING_LKA", {})


def test_car_encode_allows_integrity_only_messages(tmp_path):
    db = _catalog_db(tmp_path, "toyota_nodsu_pt_generated")
    payload = db.encode("STEERING_LKA", {}, integrity_ok=True)
    assert isinstance(payload, bytes) and len(payload) == db.message("STEERING_LKA")["dlc"]
    fields = db.decode(740, False, payload)["signals"]
    defaults = {s["name"]: s["default"] for s in db.message("STEERING_LKA")["signals"]}
    assert fields["COUNTER"]["value"] == defaults["COUNTER"] and fields["CHECKSUM"]["value"] == defaults["CHECKSUM"]
    assert db.encode("STEERING_LKA", {"STEER_REQUEST": 1}, integrity_ok=True) != payload
    with pytest.raises(ValueError, match="Unknown signal"):
        db.encode("STEERING_LKA", {"Missing": 1}, integrity_ok=True)


def test_integrity_ok_does_not_bypass_layout_errors(tmp_path):
    raw = SIMPLE + b'''BO_ 1 Overlap: 2 XXX
 SG_ A : 0|8@1+ (1,0) [0|0] "" XXX
 SG_ B : 0|8@1+ (1,0) [0|0] "" XXX
 SG_ Seq_No_Cnt : 8|4@1+ (1,0) [0|0] "" XXX
BO_ 2 Wide: 12 XXX
 SG_ Seq_No_Cnt : 0|8@1+ (1,0) [0|0] "" XXX
'''
    store = DbcStore(tmp_path)
    db = store.load(store.import_file("mixed.dbc", raw)["sha256"])
    for name, issue in (("Overlap", "overlap"), ("Wide", "CAN FD transmission is unsupported")):
        message = db.message(name)
        assert message["integrity_fields"] == ["Seq_No_Cnt"]
        assert issue in message["layout_issue"]
        assert "Counter/checksum" not in message["layout_issue"] and "Counter/checksum" in message["tx_issue"]
        with pytest.raises(ValueError) as default:
            db.encode(name, {})
        with pytest.raises(ValueError) as car:
            db.encode(name, {}, integrity_ok=True)
        assert str(default.value) == message["tx_issue"]
        assert str(car.value) == message["layout_issue"]  # the car path names what blocks it, not the counter rule


def _layouts(db, message, name, cantools_db):
    """Signal layouts by name: ImportedDb's own, or cantools' for a message ImportedDb refuses to describe."""
    if message["signals"]:
        return {s["name"]: (s["start"], s["length"], s["little_endian"]) for s in message["signals"]}
    assert message["layout_issue"], f"{name} has no signals and no layout issue"
    frame = cantools_db.get_message_by_name(message["name"])
    return {s.name: (s.start, s.length, s.byte_order == "little_endian") for s in frame.signals}


def test_layout_matches_the_oracle_fixture(tmp_path):
    vectors = [v for v in json.loads(VECTORS.read_text()) if v["variant"] == 0]
    assert vectors
    loaded: dict[str, tuple] = {}
    compared = refused = 0
    for vector in vectors:
        name = vector["dbc"]
        if name not in loaded:
            entry = load_catalog().dbcs[name]
            catalog = _catalog_db(tmp_path, name).catalog(name)
            reference = Database(strict=False)
            reference.add_dbc_file(str(bundled_dbc_path(entry.sha256)))
            loaded[name] = ({(m["id"], m["extended"]): m for m in catalog["messages"]}, reference)
        messages, reference = loaded[name]
        message = messages[vector["address"], vector["extended"]]
        assert message["name"] == vector["message"]
        layouts = _layouts(name, message, vector["message"], reference)
        refused += not message["signals"]
        for field in vector["counters"] + vector["checksums"]:
            assert layouts[field["signal"]] == (field["start"], field["size"], field["little_endian"]), (
                name, vector["message"], field["signal"],
            )
            compared += 1
    assert compared and refused < len(vectors)
