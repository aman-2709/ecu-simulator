"""The legacy service layers wrapped as registered protocols: identical bytes, explicit SIDs."""

import pytest

from ecu_simulator.ecu import Ecu
from ecu_simulator.obd import responses
from ecu_simulator.protocols.base import DiagnosticProtocol, ServiceRequest
from ecu_simulator.protocols.obd import LegacyObdProtocol
from ecu_simulator.protocols.uds import LegacyUdsProtocol
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse


@pytest.fixture
def reset_speed(monkeypatch):
    monkeypatch.setattr(responses, "vehicle_speed", 0)


def test_wrappers_satisfy_the_protocol_contract():
    assert isinstance(LegacyObdProtocol(), DiagnosticProtocol)
    assert isinstance(LegacyUdsProtocol(), DiagnosticProtocol)
    assert (LegacyObdProtocol().name, LegacyUdsProtocol().name) == ("obd", "uds")


def test_obd_wrapper_claims_every_sid_the_legacy_layer_accepts():
    # Legacy obd.services.is_sid_valid accepts 0x01..0x0A, answering unimplemented modes
    # with silence (DEV-11). Claiming the same set keeps that silence in place.
    assert LegacyObdProtocol().service_ids == frozenset(range(0x01, 0x0B))


def test_uds_wrapper_claims_exactly_the_legacy_service_table():
    assert LegacyUdsProtocol().service_ids == frozenset({0x10, 0x11, 0x19})


def test_obd_wrapper_reproduces_legacy_bytes(reset_speed):
    obd = LegacyObdProtocol()
    assert obd.handle(ServiceRequest(b"\x01\x0d", functional=True)) == b"\x41\x0d\x00"
    assert obd.handle(ServiceRequest(b"\x09\x02")) == b"\x49\x02\x00TESTVIN0123456789"
    assert obd.handle(ServiceRequest(b"\x01\x0d\x2f\x51")) == b"\x41\x0d\x01"  # DEV-18 preserved
    assert obd.handle(ServiceRequest(b"\x01\x0c")) is None
    assert obd.handle(ServiceRequest(b"\x04")) is None  # DEV-11 preserved


def test_uds_wrapper_reproduces_legacy_bytes():
    uds = LegacyUdsProtocol()
    assert uds.handle(ServiceRequest(b"\x10\x01")) == b"\x50\x01\x00\x1e\x0b\xb8"
    assert uds.handle(ServiceRequest(b"\x19\x02\xff")) == b"\x7f\x19\x13"  # DEV-05 preserved
    assert uds.handle(ServiceRequest(b"\x10\x83")) == b"\x7f\x10\x12"  # DEV-07 preserved


def test_both_wrappers_register_on_one_ecu_without_conflict(reset_speed):
    engine = Ecu("engine")
    engine.register(LegacyObdProtocol())
    engine.register(LegacyUdsProtocol())
    assert engine.handle(DiagnosticRequest(b"\x01\x2f", 0x7DF, functional=True)) == DiagnosticResponse(b"\x41\x2f\x7f")
    assert engine.handle(DiagnosticRequest(b"\x11\x01", 0x7E1)) == DiagnosticResponse(b"\x51\x01")
