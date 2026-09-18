"""The protocol contract: what an ECU hands a protocol and the shared response framing."""

import pytest

from ecu_simulator.protocols import base


def test_service_request_exposes_sid_and_addressing_kind():
    request = base.ServiceRequest(b"\x22\xf1\x90")
    assert request.sid == 0x22
    assert request.functional is False
    assert base.ServiceRequest(b"\x01\x00", functional=True).functional is True


def test_service_request_rejects_empty_payload():
    with pytest.raises(ValueError, match="empty"):
        base.ServiceRequest(b"")


def test_service_request_payload_is_immutable_bytes():
    request = base.ServiceRequest(bytearray(b"\x10\x01"))
    assert type(request.payload) is bytes
    with pytest.raises(AttributeError):
        request.payload = b"\x10\x02"  # type: ignore[misc]


def test_negative_response_framing():
    assert base.negative_response(0x22, base.NRC_SERVICE_NOT_SUPPORTED) == b"\x7f\x22\x11"


@pytest.mark.parametrize("sid, nrc", [(0x100, 0x11), (-1, 0x11), (0x22, 0x100)])
def test_negative_response_rejects_out_of_range_bytes(sid, nrc):
    with pytest.raises(ValueError):
        base.negative_response(sid, nrc)


def test_positive_response_sid_adds_0x40():
    assert base.positive_response_sid(0x10) == 0x50


class Echo:
    name = "echo"
    service_ids = frozenset({0x3E})

    def handle(self, request: base.ServiceRequest) -> bytes | None:
        return request.payload


def test_structural_protocol_check():
    assert isinstance(Echo(), base.DiagnosticProtocol)
    assert not isinstance(object(), base.DiagnosticProtocol)
