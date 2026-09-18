"""OBD and UDS flows over the real kernel ISO-TP path on vcan.

Expected bytes are the legacy responses pinned in tests/characterization; this file
proves they travel the new transport unchanged. Strict xfails mark the two deviations
Phase 2 corrects deliberately (DEV-01 physical addressing, DEV-08 TX padding).
"""

import pytest

from tests.integration.conftest import FunctionalTester, RawCapture, Simulator, open_tester_socket

VIN_RESPONSE = b"\x49\x02\x00TESTVIN0123456789"


@pytest.fixture
def functional(vcan, simulator: Simulator):
    tester = FunctionalTester(vcan, functional_id=0x7DF, response_id=0x7E8, physical_id=0x7E0)
    try:
        yield tester
    finally:
        tester.close()


def _physical_fixture(rx_id: int, tx_id: int):
    @pytest.fixture
    def fixture(vcan, simulator: Simulator):
        sock = open_tester_socket(vcan, rx_id=rx_id, tx_id=tx_id)
        try:
            yield sock
        finally:
            sock.close()

    return fixture


physical = _physical_fixture(rx_id=0x7E8, tx_id=0x7E0)
uds = _physical_fixture(rx_id=0x7E9, tx_id=0x7E1)


def test_functional_supported_pids(functional):
    functional.send(b"\x01\x00")
    assert functional.recv() == bytes.fromhex("410008080001")


def test_functional_fuel_level(functional):
    functional.send(b"\x01\x2f")
    assert functional.recv() == b"\x41\x2f\x7f"


def test_functional_vin_is_multi_frame(vcan, functional):
    capture = RawCapture(vcan)
    functional.send(b"\x09\x02")
    assert functional.recv() == VIN_RESPONSE
    frames = capture.collect(0.2)
    capture.close()
    pcis = [f.data[0] >> 4 for f in frames if f.can_id == 0x7E8]
    assert pcis[:3] == [1, 2, 2], f"expected FF + 2 CF from 0x7E8, saw {[hex(f.can_id) + ':' + f.data.hex() for f in frames]}"
    assert any(f.can_id == 0x7E0 and f.data[0] >> 4 == 3 for f in frames), "tester flow control on 0x7E0 missing"


def test_unsupported_pid_gets_no_response(functional):
    functional.send(b"\x01\x0c")
    with pytest.raises(TimeoutError):
        functional.recv()


def test_uds_session_control_and_negative_response(uds):
    uds.send(b"\x10\x01")
    assert uds.recv() == bytes.fromhex("5001001e0bb8")
    uds.send(b"\x19\x02\xff")
    assert uds.recv() == bytes.fromhex("7f1913")


@pytest.mark.xfail(strict=True, reason="DEV-01: physically addressed OBD requests are not served yet")
def test_physical_request_is_answered(physical):
    physical.send(b"\x01\x2f")
    assert physical.recv() == b"\x41\x2f\x7f"


def test_physical_request_is_dropped_today(physical):
    # Characterization of DEV-01 on the wire: nothing comes back on 0x7E8.
    physical.send(b"\x01\x2f")
    with pytest.raises(TimeoutError):
        physical.recv()


@pytest.mark.xfail(strict=True, reason="DEV-08: OBD responses are not padded to DLC 8 yet")
def test_obd_response_frames_are_padded_to_dlc_8(vcan, functional):
    capture = RawCapture(vcan)
    functional.send(b"\x01\x2f")
    assert functional.recv() == b"\x41\x2f\x7f"
    frames = capture.collect(0.2)
    capture.close()
    response_frames = [f for f in frames if f.can_id == 0x7E8]
    assert response_frames, "no response frame captured"
    assert {f.dlc for f in response_frames} == {8}, [f.dlc for f in response_frames]


def test_obd_response_frames_are_short_today(vcan, functional):
    capture = RawCapture(vcan)
    functional.send(b"\x01\x2f")
    assert functional.recv() == b"\x41\x2f\x7f"
    frames = capture.collect(0.2)
    capture.close()
    (response,) = [f for f in frames if f.can_id == 0x7E8]
    assert response.dlc == 4 and response.data == b"\x03\x41\x2f\x7f"
