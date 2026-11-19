"""Phase 10 fault effects through real vcan and kernel CAN_ISOTP sockets."""

from __future__ import annotations

import signal
import socket
import struct
import time
from collections.abc import Iterator

import pytest

from ecu_simulator.cli import default_profile_path
from tests.integration.conftest import RawCapture, Simulator, open_tester_socket


@pytest.fixture
def fault_simulator(vcan: str, tmp_path) -> Iterator[Simulator]:
    profile = default_profile_path().with_name("faults_demo.yaml")
    sim = Simulator(vcan, str(tmp_path), profile=str(profile))
    try:
        sim.wait_ready()
        yield sim
    finally:
        sim.terminate()


@pytest.fixture
def physical(vcan: str, fault_simulator: Simulator):
    tester = open_tester_socket(vcan, rx_id=0x7E8, tx_id=0x7E0, timeout=0.3)
    try:
        yield tester
    finally:
        tester.close()


def test_drop_is_silence_once_then_the_same_channel_recovers(physical):
    physical.send(b"\x01\x0d")
    with pytest.raises(TimeoutError):
        physical.recv()
    physical.send(b"\x01\x0d")
    assert physical.recv() == b"\x41\x0d\x2a"


def test_delay_is_visible_on_the_wire_without_blocking_the_ecu(physical):
    started = time.monotonic()
    physical.send(b"\x3e\x00")
    assert physical.recv() == b"\x7e\x00"
    assert time.monotonic() - started >= 0.12


def test_nrc_override_replaces_the_ordinary_response(physical):
    physical.send(b"\x14\xff\xff\xff")
    assert physical.recv() == b"\x7f\x14\x22"


def test_response_pending_is_followed_by_the_ordinary_final_response(physical):
    started = time.monotonic()
    physical.send(b"\x10\x03")
    assert physical.recv() == b"\x7f\x10\x78"
    assert physical.recv() == bytes.fromhex("5003001e0bb8")
    assert time.monotonic() - started >= 0.08


def test_truncate_removes_tail_bytes_from_the_wire_payload(physical):
    ordinary = b"\x49\x02\x01TESTVIN0123456789"
    physical.send(b"\x09\x02")
    assert physical.recv() == ordinary[:-5]


def test_corrupt_xors_the_configured_response_byte(physical):
    physical.send(b"\x03")
    assert physical.recv() == bytes.fromhex("420294770001")


def test_offline_bypasses_session_reset_and_then_recovers(physical):
    physical.send(b"\x10\x03")
    assert physical.recv() == b"\x7f\x10\x78"
    assert physical.recv() == bytes.fromhex("5003001e0bb8")
    physical.send(b"\x11\x01")
    with pytest.raises(TimeoutError):
        physical.recv()
    physical.send(bytes.fromhex("22f186"))
    assert physical.recv() == bytes.fromhex("62f18603")


def test_ignored_flow_control_is_reported_and_the_transport_recovers(vcan, fault_simulator):
    capture = RawCapture(vcan)
    raw = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    raw.bind((vcan,))
    try:
        data = bytes.fromhex("0209020000000000")
        raw.send(struct.pack("=IB3x8s", 0x7E0, 8, data))
        frames = capture.collect(0.2)
        assert any(frame.can_id == 0x7E8 and frame.data[0] >> 4 == 1 for frame in frames)
        deadline = time.monotonic() + 2.0
        while "no flow control" not in fault_simulator.log and time.monotonic() < deadline:
            time.sleep(0.02)
        assert "no flow control" in fault_simulator.log

        tester = open_tester_socket(vcan, rx_id=0x7E8, tx_id=0x7E0, timeout=0.5)
        try:
            tester.send(bytes.fromhex("22f186"))
            assert tester.recv() == bytes.fromhex("62f18601")
        finally:
            tester.close()
    finally:
        raw.close()
        capture.close()


def test_shutdown_cancels_a_pending_delayed_response(vcan, tmp_path):
    profile = default_profile_path().with_name("faults_demo.yaml")
    sim = Simulator(vcan, str(tmp_path), profile=str(profile))
    tester = open_tester_socket(vcan, rx_id=0x7E8, tx_id=0x7E0, timeout=0.3)
    try:
        sim.wait_ready()
        tester.send(b"\x3e\x00")
        sim.signal(signal.SIGTERM)
        code, log = sim.wait(timeout=3)
        assert code == 0, log
        assert "shutdown complete" in log and "Traceback" not in log
    finally:
        tester.close()
        sim.terminate()
