"""The legacy UDS service layer wrapped as a registered protocol: identical bytes, explicit SIDs.

The OBD half of this file went with the legacy OBD package in Phase 5; ObdProtocol is
covered by tests/unit/test_obd_protocol.py and the characterization suite.
"""

import pytest

from ecu_simulator.ecu import Ecu, Route
from ecu_simulator.protocols.base import DiagnosticProtocol, ServiceRequest
from ecu_simulator.protocols.uds import LegacyUdsProtocol
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse


def test_the_wrapper_satisfies_the_protocol_contract():
    assert isinstance(LegacyUdsProtocol(), DiagnosticProtocol)
    assert LegacyUdsProtocol().name == "uds"


def test_uds_wrapper_claims_exactly_the_legacy_service_table():
    assert LegacyUdsProtocol().service_ids == frozenset({0x10, 0x11, 0x19})


def test_uds_wrapper_reproduces_legacy_bytes():
    uds = LegacyUdsProtocol()
    assert uds.handle(ServiceRequest(b"\x10\x01")) == b"\x50\x01\x00\x1e\x0b\xb8"
    assert uds.handle(ServiceRequest(b"\x19\x02\xff")) == b"\x7f\x19\x13"  # DEV-05 preserved
    assert uds.handle(ServiceRequest(b"\x10\x83")) == b"\x7f\x10\x12"  # DEV-07 preserved


@pytest.mark.parametrize("payload", [b"", b"\x22\xf1\x90", b"\x3e\x00"])
def test_uds_wrapper_sends_nothing_for_empty_or_unsupported(payload):
    if not payload:
        with pytest.raises(ValueError):
            ServiceRequest(payload)
        return
    assert LegacyUdsProtocol().handle(ServiceRequest(payload)) is None


def test_the_wrapper_registers_on_an_ecu_and_answers():
    engine = Ecu("engine")
    engine.register(LegacyUdsProtocol())
    direct = Route("engine", frozenset({"uds"}))
    assert engine.handle(DiagnosticRequest(b"\x11\x01", 0x7E1), direct) == DiagnosticResponse(b"\x51\x01")
