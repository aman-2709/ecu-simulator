import pytest

from ecu_simulator.transport import AddressingMode, DiagnosticRequest, DiagnosticResponse


def test_request_defaults_and_immutability():
    request = DiagnosticRequest(payload=b"\x01\x0d", target_address=0x7E0)
    assert request.functional is False
    assert request.addressing_mode is AddressingMode.NORMAL_11BIT
    assert request.source_address is None
    assert request.context is None
    with pytest.raises(AttributeError):
        request.payload = b""  # type: ignore[misc]


def test_request_normalises_bytearray_and_rejects_non_bytes():
    assert DiagnosticRequest(payload=bytearray(b"\x03"), target_address=0x7DF).payload == b"\x03"
    with pytest.raises(TypeError):
        DiagnosticRequest(payload="03", target_address=0x7DF)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        DiagnosticRequest(payload=b"\x03", target_address=-1)


def test_request_carries_opaque_context():
    token = object()
    request = DiagnosticRequest(payload=b"\x3e\x00", target_address=0x7E1, context=token)
    assert request.context is token


def test_response_validation():
    assert DiagnosticResponse(payload=b"\x41\x0d\x00").delay == 0.0
    with pytest.raises(ValueError):
        DiagnosticResponse(payload=b"")
    with pytest.raises(ValueError):
        DiagnosticResponse(payload=b"\x7e\x00", delay=-1)
    with pytest.raises(TypeError):
        DiagnosticResponse(payload="7e00")  # type: ignore[arg-type]


def test_addressing_mode_values_are_stable_strings():
    assert AddressingMode("normal_11bit") is AddressingMode.NORMAL_11BIT
    assert AddressingMode("normal_fixed_29bit") is AddressingMode.NORMAL_FIXED_29BIT
