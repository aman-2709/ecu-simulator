"""OBD and UDS flows over the real kernel ISO-TP path on vcan.

Expected bytes are the legacy responses pinned in tests/characterization; this file
proves they travel the new transport unchanged. Strict xfails mark the two deviations
Phase 2 corrects deliberately (DEV-01 physical addressing, DEV-08 TX padding).
"""

import pytest

from tests.integration.conftest import FunctionalTester, RawCapture, Simulator, open_tester_socket

# DEV-02 corrected: the third byte is the number of data items, one VIN.
VIN_RESPONSE = b"\x49\x02\x01TESTVIN0123456789"


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
    assert functional.recv() == bytes.fromhex("41001e3f8013")


def test_functional_fuel_level(functional):
    functional.send(b"\x01\x2f")
    assert functional.recv() == b"\x41\x2f\x7f"


def test_functional_multi_parameter_request(functional):
    # DEV-18 corrected in Phase 5.1: four parameters, one response, over the real ISO-TP
    # path. Before, this answered 41 05 82 alone.
    functional.send(bytes.fromhex("01050c2f51"))
    assert functional.recv() == bytes.fromhex("410582" "0c0c80" "2f7f" "5101")


def test_functional_six_parameter_response_is_multi_frame(vcan, functional):
    # Six parameters make the response longer than one CAN frame, so the kernel segments
    # it and the tester's flow control carries the rest.
    capture = RawCapture(vcan)
    functional.send(bytes.fromhex("01" "04050b0c0d0e"))
    assert functional.recv() == bytes.fromhex("4104" "38" "05" "82" "0b" "21" "0c" "0c80" "0d" "00" "0e" "94")
    frames = capture.collect(0.2)
    capture.close()
    pcis = [f.data[0] >> 4 for f in frames if f.can_id == 0x7E8]
    assert pcis[:2] == [1, 2], (
        f"expected a first frame then a consecutive frame from 0x7E8, saw "
        f"{[hex(f.can_id) + ':' + f.data.hex() for f in frames]}"
    )


def test_functional_vin_is_multi_frame(vcan, functional):
    capture = RawCapture(vcan)
    functional.send(b"\x09\x02")
    assert functional.recv() == VIN_RESPONSE
    frames = capture.collect(0.2)
    capture.close()
    pcis = [f.data[0] >> 4 for f in frames if f.can_id == 0x7E8]
    assert pcis[:3] == [1, 2, 2], (
        f"expected FF + 2 CF from 0x7E8, saw {[hex(f.can_id) + ':' + f.data.hex() for f in frames]}"
    )
    assert any(f.can_id == 0x7E0 and f.data[0] >> 4 == 3 for f in frames), "tester flow control on 0x7E0 missing"


def test_supported_pid_chain_terminates_on_the_wire(functional):
    # DEV-04 corrected: walk the chain as a tester would. It ends after the last populated
    # range, and the range beyond it is not answered at all.
    functional.send(b"\x01\x00")
    assert functional.recv() == bytes.fromhex("41001e3f8013")
    functional.send(b"\x01\x20")
    assert functional.recv() == bytes.fromhex("412000020001")
    functional.send(b"\x01\x40")
    last = functional.recv()
    assert last == bytes.fromhex("414044008000")
    assert last[-1] & 0x01 == 0, "the last populated range must not claim a successor"
    functional.send(b"\x01\x60")
    with pytest.raises(TimeoutError):
        functional.recv()


def test_unsupported_pid_gets_no_response(functional):
    # PID 0x01 monitor status is deferred (Phase 6 DTC store), so nothing answers it.
    functional.send(b"\x01\x01")
    with pytest.raises(TimeoutError):
        functional.recv()


def test_engine_rpm_is_answered_on_the_wire(functional):
    # DEV-12 corrected in Phase 5: the shipped profile idles at 800 rpm -> 800 * 4.
    functional.send(b"\x01\x0c")
    assert functional.recv() == bytes.fromhex("410c0c80")


def test_uds_session_control_and_negative_response(uds):
    uds.send(b"\x10\x01")
    assert uds.recv() == bytes.fromhex("5001001e0bb8")
    uds.send(b"\x19\x01")
    assert uds.recv() == bytes.fromhex("7f1912")


def test_uds_read_dtc_by_status_mask_on_the_wire(uds):
    # DEV-05 corrected in Phase 6: 19 02 FF was answered 7F 19 13 and is now answered
    # with the matching records; the two-byte form is now the malformed one.
    uds.send(b"\x19\x02\xff")
    assert uds.recv() == bytes.fromhex("5902ff" + "9477012f" + "0001012f")
    uds.send(b"\x19\x02")
    assert uds.recv() == bytes.fromhex("7f1913")


def test_uds_read_dtc_with_a_mask_matching_nothing_returns_the_header_alone(uds):
    uds.send(b"\x19\x02\x40")
    assert uds.recv() == bytes.fromhex("5902ff")


def test_physical_request_is_answered(physical):
    # DEV-01 corrected: a request on 0x7E0 is answered on 0x7E8.
    physical.send(b"\x01\x2f")
    assert physical.recv() == b"\x41\x2f\x7f"


def test_physical_vin_is_multi_frame(physical):
    physical.send(b"\x09\x02")
    assert physical.recv() == VIN_RESPONSE


def test_functional_and_physical_sockets_coexist(functional):
    # One tester channel, alternating ATSH 7DF / ATSH 7E0 style requests with distinct answers.
    for _ in range(3):
        functional.send(b"\x01\x51")
        assert functional.recv() == b"\x41\x51\x01"
        functional.send_physical(b"\x01\x2f")
        assert functional.recv() == b"\x41\x2f\x7f"


def test_obd_response_frames_are_padded_to_dlc_8(vcan, functional):
    # DEV-08 corrected: every OBD response frame on the wire is DLC 8, padded with 0x00.
    capture = RawCapture(vcan)
    functional.send(b"\x01\x2f")
    assert functional.recv() == b"\x41\x2f\x7f"
    functional.send_physical(b"\x09\x02")
    assert functional.recv() == VIN_RESPONSE
    frames = capture.collect(0.2)
    capture.close()
    response_frames = [f for f in frames if f.can_id == 0x7E8]
    assert len(response_frames) >= 4, (
        f"expected SF + FF + 2 CF from 0x7E8, saw {[f.data.hex() for f in response_frames]}"
    )
    assert {f.dlc for f in response_frames} == {8}, [f.dlc for f in response_frames]
    single = response_frames[0]
    assert single.data == b"\x03\x41\x2f\x7f\x00\x00\x00\x00"


def test_uds_response_frames_are_not_padded(vcan, uds):
    # Unchanged in Phase 2: UDS padding waits for per-ECU configuration.
    capture = RawCapture(vcan)
    uds.send(b"\x11\x01")
    assert uds.recv() == b"\x51\x01"
    frames = capture.collect(0.2)
    capture.close()
    (response,) = [f for f in frames if f.can_id == 0x7E9]
    assert response.dlc == 3 and response.data == b"\x02\x51\x01"
