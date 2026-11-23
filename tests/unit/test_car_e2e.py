"""The AUTOSAR E2E Profile 1 and Profile 2 presets, checked against the published AUTOSAR text.

Sources (both published on autosar.org, read for this test and not copied beyond the values below):

- "E2E Protocol Specification", AUTOSAR_PRS_E2EProtocol, document ID 849, AUTOSAR FO R22-11. Section 6.5 (Profile 1)
  and 6.6 (Profile 2); the worked examples are Tables 6.20 to 6.27 (section 6.5.5) and Table 6.36 (section 6.6.5).
- "Specification of CRC Routines", AUTOSAR_SWS_CRCLibrary, document ID 16, AUTOSAR CP R22-11. Section 7.2.1: the
  parameters and check values of Crc_CalculateCRC8 (Table 7.1, results in Table 7.2) and Crc_CalculateCRC8H2F (Table
  7.3, results in Table 7.4).

The oracle in this file is ``_bitwise_crc8``, a plain MSB-first CRC written here from those tables; it shares no code
with ``ecu_simulator.car.crc``.
"""

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from ecu_simulator.car import e2e
from ecu_simulator.car.algorithms import build_checksum
from ecu_simulator.car.bits import BitField
from ecu_simulator.car.crc import crc
from ecu_simulator.car.integrity import IntegrityState, Stamper, plan_message
from ecu_simulator.car.rules import ChecksumSpec, parse_rule_text
from ecu_simulator.dbc import DbcStore

CHECK_STRING = b"123456789"
CRC_FIELD = BitField(0, 8, True)
COUNTER_FIELD = BitField(8, 4, True)


def _bitwise_crc8(data: Sequence[int], poly: int, init: int, xorout: int) -> int:
    """An 8-bit CRC, input and result not reflected (SWS_CRCLibrary Tables 7.1 and 7.3), one bit at a time."""
    register = init
    for byte in data:
        register ^= byte
        for _ in range(8):
            register = ((register << 1) ^ poly) & 0xFF if register & 0x80 else (register << 1) & 0xFF
    return register ^ xorout


def _crc_calculate_crc8(data: Sequence[int], start: int, first_call: bool) -> int:
    """AUTOSAR ``Crc_CalculateCRC8`` as SWS_Crc_00014 and SWS_Crc_00041 describe it: initial value 0xFF on a first
    call, otherwise the previous result XORed with the XOR value 0xFF; the result is XORed with 0xFF."""
    initial = 0xFF if first_call else start ^ 0xFF
    return _bitwise_crc8(data, 0x1D, initial, 0xFF)


def _p01_reference(frame: bytes, data_id: int, mode: str) -> int:
    """The Profile 1 CRC as PRS_E2EProtocol R22-11 Figures 6.55 and 6.56 compute it with CRCOffset 0: the Data ID
    bytes through ``getDataIDCRC`` (each ``Crc_CalculateCRC8(.., 0xFF, FALSE)`` first call), then the bytes after the
    CRC byte, then ``CRC ^ 0xFF``."""
    low, high, counter = data_id & 0xFF, data_id >> 8, frame[1] & 0x0F
    if mode == "both":
        value = _crc_calculate_crc8([high], _crc_calculate_crc8([low], 0xFF, False), False)
    elif mode == "low":
        value = _crc_calculate_crc8([low], 0xFF, False)
    elif mode == "alt":
        value = _crc_calculate_crc8([low if counter % 2 == 0 else high], 0xFF, False)
    else:
        value = _crc_calculate_crc8([0x00], _crc_calculate_crc8([low], 0xFF, False), False)
    value = _crc_calculate_crc8(list(frame[1:]), value, False)
    return value ^ 0xFF


def _p02_reference(frame: bytes, data_id_list: Sequence[int]) -> int:
    """The Profile 2 CRC as PRS_E2EProtocol R22-11 Figure 6.65 computes it: CRC8H2F with start value 0xFF over
    Data[1] to Data[N-1], then the Data ID that the counter selects."""
    return _bitwise_crc8([*frame[1:], data_id_list[frame[1] & 0x0F]], 0x2F, 0xFF, 0xFF)


# --- The CRC primitives the presets run on ---------------------------------------------------------------------------

# SWS_CRCLibrary R22-11 Table 7.2 (Crc_CalculateCRC8) and Table 7.4 (Crc_CalculateCRC8H2F).
CRC_LIBRARY_RESULTS = [
    ([0x00, 0x00, 0x00, 0x00], 0x59, 0x12),
    ([0xF2, 0x01, 0x83], 0x37, 0xC2),
    ([0x0F, 0xAA, 0x00, 0x55], 0x79, 0xC6),
    ([0x00, 0xFF, 0x55, 0x11], 0xB8, 0x77),
    ([0x33, 0x22, 0x55, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF], 0xCB, 0x11),
    ([0x92, 0x6B, 0x55], 0x8C, 0x33),
    ([0xFF, 0xFF, 0xFF, 0xFF], 0x74, 0x6C),
]


def test_the_crc_primitives_match_the_autosar_crc_library() -> None:
    j1850, h2f = e2e.CRC8_SAE_J1850, e2e.CRC8H2F
    # Table 7.1 and 7.3: polynomial, initial value and XOR value; neither is reflected.
    assert (j1850.poly, j1850.init, j1850.xorout) == (0x1D, 0xFF, 0xFF)
    assert (h2f.poly, h2f.init, h2f.xorout) == (0x2F, 0xFF, 0xFF)
    # The catalogue check values over "123456789", through the CRC function the presets call.
    assert e2e.crc8(j1850, CHECK_STRING) == 0x4B
    assert e2e.crc8(h2f, CHECK_STRING) == 0xDF
    for data, expected_j1850, expected_h2f in CRC_LIBRARY_RESULTS:
        assert e2e.crc8(j1850, data) == expected_j1850, data
        assert e2e.crc8(h2f, data) == expected_h2f, data
        # The test's own oracle agrees with the library tables too.
        assert _bitwise_crc8(data, 0x1D, 0xFF, 0xFF) == expected_j1850
        assert _bitwise_crc8(data, 0x2F, 0xFF, 0xFF) == expected_h2f


def test_p01_uses_crc8_j1850_parameters() -> None:
    # PRS_E2E_00070: the J1850 polynomial, with start value and XOR value 0x00. Figure 6.55 gets there by calling the
    # 0xFF/0xFF library function chained with start 0xFF and XORing the end result with 0xFF.
    assert e2e.P01_CRC == e2e.Crc8(poly=e2e.CRC8_SAE_J1850.poly, init=0x00, xorout=0x00)
    for data, _, _ in CRC_LIBRARY_RESULTS:
        chained = _crc_calculate_crc8(data, 0xFF, False) ^ 0xFF
        assert e2e.crc8(e2e.P01_CRC, data) == chained, data
    assert e2e.P02_CRC == e2e.CRC8H2F


# --- What each preset feeds the CRC ----------------------------------------------------------------------------------


@pytest.fixture
def fed(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[list[int], tuple[int, ...]]]]:
    """Every call the presets make to the CRC primitive: the bytes and the (width, poly, init, xorout, reflect)."""
    calls: list[tuple[list[int], tuple[int, ...]]] = []

    def recording(data: Sequence[int], width: int, poly: int, init: int, xorout: int, reflect: bool) -> int:
        calls.append((list(data), (width, poly, init, xorout, reflect)))
        return crc(data, width, poly, init, xorout, reflect)

    monkeypatch.setattr(e2e, "crc", recording)
    yield calls


def _p01(data_id: int, mode: str) -> Callable[[bytearray], int]:
    spec = ChecksumSpec.model_validate(
        {"signal": "CRC", "algorithm": "e2e_p01", "data_id": data_id, "data_id_mode": mode}
    )
    fn = build_checksum(spec, 8)
    return lambda frame: fn(0x123, frame, CRC_FIELD, "CRC")


def _p02(data_id_list: Sequence[int]) -> Callable[[bytearray], int]:
    spec = ChecksumSpec.model_validate({"signal": "CRC", "algorithm": "e2e_p02", "data_id_list": list(data_id_list)})
    fn = build_checksum(spec, 8)
    return lambda frame: fn(0x123, frame, CRC_FIELD, "CRC")


P01_PARAMS = (8, 0x1D, 0x00, 0x00, False)
P02_PARAMS = (8, 0x2F, 0xFF, 0xFF, False)


def test_p01_data_id_modes_change_the_crc_in_the_documented_way(fed: list[tuple[list[int], tuple[int, ...]]]) -> None:
    # Data ID 0x0A5C: low byte 0x5C, high byte 0x0A. Byte 1 carries 0x6 in its high nibble and the counter below it.
    for counter in (4, 7):
        frame = bytearray([0x00, 0x60 | counter, 0x11, 0x22])
        tail = [0x60 | counter, 0x11, 0x22]
        # PRS_E2E_00082 and PRS_E2E_00163: the Data ID first, low byte then high byte, then every byte after the CRC.
        _p01(0x0A5C, "both")(frame.copy())
        _p01(0x005C, "low")(frame.copy())
        _p01(0x0A5C, "alt")(frame.copy())
        expected = [
            ([0x5C, 0x0A, *tail], P01_PARAMS),
            ([0x5C, *tail], P01_PARAMS),
            # ALT: the low byte on an even counter, the high byte on an odd one.
            ([0x5C if counter % 2 == 0 else 0x0A, *tail], P01_PARAMS),
        ]
        # NIBBLE (PRS_E2E_00640): the low byte, a zero byte, then the data, whose byte 1 now carries the nibble 0xA.
        nibble_frame = frame.copy()
        _p01(0x0A5C, "nibble")(nibble_frame)
        expected.append(([0x5C, 0x00, 0xA0 | counter, 0x11, 0x22], P01_PARAMS))
        assert fed == expected, counter
        fed.clear()


def test_p01_nibble_mode_writes_the_data_id_nibble_into_the_high_nibble_of_byte_1() -> None:
    # PRS_E2E_00195 step 2 and Figure 6.54 with DataIDNibbleOffset 12 (variant 1C): bits 12 to 15 carry DataID >> 8.
    frame = bytearray([0x00, 0xF3, 0x00, 0x00])
    _p01(0x0B21, "nibble")(frame)
    assert frame[1] == 0xB3
    assert list(frame[2:]) == [0, 0]
    # The other modes leave byte 1 as the encoder wrote it.
    frame = bytearray([0x00, 0xF3, 0x00, 0x00])
    _p01(0x0B21, "both")(frame)
    assert frame == bytearray([0x00, 0xF3, 0x00, 0x00])


def test_p02_selects_data_id_by_counter(fed: list[tuple[list[int], tuple[int, ...]]]) -> None:
    data_id_list = [0x10 + 7 * k for k in range(16)]
    fn = _p02(data_id_list)
    for counter in range(16):
        frame = bytearray([0x00, 0x50 | counter, 0xAB, 0xCD])
        fn(frame)
        # PRS_E2E_00120 and PRS_E2E_00132: Data[1] to Data[N-1], then DataIDList[Counter]; CRC8H2F, start 0xFF.
        assert fed == [([0x50 | counter, 0xAB, 0xCD, data_id_list[counter]], P02_PARAMS)], counter
        fed.clear()


# --- The worked examples of the specification ------------------------------------------------------------------------

# PRS_E2EProtocol R22-11 section 6.5.5: DataID 0x123, CRC in byte 0, counter in the low nibble of byte 1, DataID nibble
# (mode NIBBLE) in the high nibble of byte 1, DataLength 64 bits, data otherwise all zero. (mode, counter, frame).
P01_EXAMPLES = [
    pytest.param("both", 0, "cc 00 00 00 00 00 00 00", id="table-6.20-both-counter-0"),
    pytest.param("both", 1, "91 01 00 00 00 00 00 00", id="table-6.21-both-counter-1"),
    pytest.param("alt", 0, "ce 00 00 00 00 00 00 00", id="table-6.22-alt-counter-0"),
    pytest.param("alt", 1, "02 01 00 00 00 00 00 00", id="table-6.23-alt-counter-1"),
    pytest.param("low", 0, "ce 00 00 00 00 00 00 00", id="table-6.24-low-counter-0"),
    pytest.param("low", 1, "93 01 00 00 00 00 00 00", id="table-6.25-low-counter-1"),
    pytest.param("nibble", 0, "2a 10 00 00 00 00 00 00", id="table-6.26-nibble-counter-0"),
    pytest.param("nibble", 1, "77 11 00 00 00 00 00 00", id="table-6.27-nibble-counter-1"),
]


@pytest.mark.parametrize(("mode", "counter", "expected"), P01_EXAMPLES)
def test_p01_reproduces_the_specification_examples(mode: str, counter: int, expected: str) -> None:
    # Table 6.24 is the LOW example of DataID 0x123 itself; the LOW preset needs a high byte of 0x00
    # (PRS_E2E_00169), and the low byte is the only one the CRC sees, so 0x23 gives the same frames.
    data_id = 0x23 if mode == "low" else 0x123
    frame = bytearray(8)
    frame[1] = counter
    frame[0] = _p01(data_id, mode)(frame)
    assert frame == bytearray.fromhex(expected)
    assert _p01_reference(bytes(frame), data_id, mode) == frame[0]


# PRS_E2EProtocol R22-11 section 6.6.5, Table 6.36: DataIDList 0x01 to 0x10, DataLength 64 bits, data all zero.
# Each row: the counter and the CRC in byte 0.
P02_EXAMPLE_LIST = list(range(0x01, 0x11))
P02_EXAMPLES = [
    (1, 0x1B),
    (2, 0x98),
    (3, 0x31),
    (4, 0x0D),
    (5, 0x18),
    (6, 0x9B),
    (7, 0x65),
    (8, 0x08),
    (9, 0x1D),
    (10, 0x9E),
    (11, 0x37),
    (12, 0x0B),
    (13, 0x1E),
    (14, 0x9D),
    (15, 0xCD),
    (0, 0x0E),
]


def test_p02_reproduces_the_specification_examples() -> None:
    fn = _p02(P02_EXAMPLE_LIST)
    for counter, expected in P02_EXAMPLES:
        frame = bytearray(8)
        frame[1] = counter
        assert fn(frame) == expected, counter
        assert _p02_reference(bytes(frame), P02_EXAMPLE_LIST) == expected, counter


# --- Through a rule file, the integrity plan and the stamper ---------------------------------------------------------

BRAKE_DBC = b"""BO_ 291 Brake: 4 XXX
 SG_ CRC : 0|8@1+ (1,0) [0|255] "" XXX
 SG_ ALIVE : 8|4@1+ (1,0) [0|15] "" XXX
 SG_ PRESSURE : 16|16@1+ (1,0) [0|65535] "" XXX
"""


def _brake(tmp_path: Path) -> dict[str, Any]:
    store = DbcStore(tmp_path)
    message: dict[str, Any] = store.load(store.import_file("brake.dbc", BRAKE_DBC)["sha256"]).message("Brake")
    return message


def _stamp_frames(tmp_path: Path, checksum: str, count: int) -> list[bytes]:
    text = (
        "version: 1\nrules:\n  - messages: [Brake]\n    counter: {signal: ALIVE}\n"
        f"    checksum: {{signal: CRC, algorithm: {checksum}}}\n"
    )
    message = _brake(tmp_path)
    user = parse_rule_text(text).resolve([message])["Brake"]
    plan = plan_message(message, rule=None, user=user)
    assert plan.state == IntegrityState.USER_RULE
    stamper = Stamper(plan)
    frames = []
    for k in range(count):
        payload = bytes([0x00, 0x00, (0x40 + 3 * k) & 0xFF, 0x12])
        frames.append(stamper.stamp(payload))
    return frames


@pytest.mark.parametrize("mode", ["both", "low", "alt", "nibble"])
def test_p01_stamps_successive_frames_through_a_rule_file(tmp_path: Path, mode: str) -> None:
    data_id = {"both": 0xBEEF, "alt": 0xBEEF, "low": 0x00EF, "nibble": 0x0EEF}[mode]
    frames = _stamp_frames(tmp_path, f"e2e_p01, data_id: {data_id}, data_id_mode: {mode}", 40)
    for k, frame in enumerate(frames):
        # PRS_E2E_00075: the counter runs 0 to 14 and skips 15.
        assert COUNTER_FIELD.read(frame) == k % 15, k
        assert frame[1] >> 4 == (0xE if mode == "nibble" else 0x0), k
        assert frame[2:] == bytes([(0x40 + 3 * k) & 0xFF, 0x12]), k
        assert frame[0] == _p01_reference(frame, data_id, mode), k


def test_p01_alt_alternates_with_the_counter_parity_across_the_wrap(
    tmp_path: Path, fed: list[tuple[list[int], tuple[int, ...]]]
) -> None:
    _stamp_frames(tmp_path, "e2e_p01, data_id: 0xBEEF, data_id_mode: alt", 32)
    counters = [k % 15 for k in range(32)]
    assert [data[0] for data, _ in fed] == [0xEF if c % 2 == 0 else 0xBE for c in counters]
    # Counter 14 is followed by 0: both even, so the low byte is fed twice in a row at the wrap.
    assert [data[0] for data, _ in fed][14:16] == [0xEF, 0xEF]


def test_p02_stamps_successive_frames_through_a_rule_file(tmp_path: Path) -> None:
    data_id_list = [0x3C, 0x91, 0x07, 0xD2, 0x5E, 0x48, 0xA1, 0x16, 0xFB, 0x60, 0x2D, 0x83, 0xC7, 0x39, 0x74, 0xEA]
    frames = _stamp_frames(tmp_path, f"e2e_p02, data_id_list: {data_id_list}", 40)
    for k, frame in enumerate(frames):
        # PRS_E2E_00128 and PRS_E2E_00129: the counter runs 0 to 15.
        assert COUNTER_FIELD.read(frame) == k % 16, k
        assert frame[0] == _p02_reference(frame, data_id_list), k
    assert {COUNTER_FIELD.read(frame) for frame in frames} == set(range(16))
