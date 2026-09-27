import json

from ecu_simulator.ecu.router import AddressRouter
from ecu_simulator.observe.events import classify, encode_exchange, summarise
from ecu_simulator.observe.handoff import ExchangeRecord
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse
from ecu_simulator.transport.socketcan import EndpointConfig, IsoTpAddress

FUNC = EndpointConfig("engine.obd_functional", IsoTpAddress(0x7DF, 0x7E8), functional=True, reply_via="engine.obd_physical")
PHYS = EndpointConfig("engine.obd_physical", IsoTpAddress(0x7E0, 0x7E8))
ENDPOINTS = {e.name: e for e in (FUNC, PHYS)}
ORIGIN = (1_790_000_000.0, 0)


def router() -> AddressRouter:
    r = AddressRouter()
    r.add_functional(0x7DF, "engine", {"obd"})
    r.add_physical(0x7E0, "engine", {"obd", "uds"})
    return r


def rec(payload, response=b"\x41\x0c\x0c\x80", error=None, address=0x7DF, functional=True, context=FUNC):
    request = DiagnosticRequest(payload, address, functional=functional, context=context)
    return ExchangeRecord(7, 1_500_000, 41_000, request, DiagnosticResponse(response) if response else None, error)


def test_the_four_outcomes():
    assert classify(rec(b"\x01\x0c"), router()) == ("responded", "engine")
    assert classify(rec(b"\x07", response=None), router()) == ("no_response", "engine")
    assert classify(rec(b"\x01\x0c", response=None, address=0x7AA, functional=False, context=None), router()) == ("unrouted", None)
    assert classify(rec(b"\x01\x0c", response=None, error="ValueError"), router()) == ("error", "engine")


def test_summaries():
    assert summarise(b"\x01\x0c") == "OBD 01 0C — Engine speed"
    assert summarise(b"\x01\x0c\x0d") == "OBD 01 0C 0D — 2 parameters"
    assert summarise(b"\x09\x02") == "OBD 09 02 — vehicle information"
    assert summarise(b"\x19\x02\xff") == "UDS 19 — ReadDTCInformation"
    assert summarise(b"\x22\xf1\x90") == "service 0x22"


def test_encoded_exchange_carries_the_reply_id_of_reply_via():
    event = json.loads(encode_exchange(rec(b"\x01\x0c"), router(), ENDPOINTS, ORIGIN))
    assert event["type"] == "exchange" and event["seq"] == 7
    assert (event["endpoint"], event["rx_id"], event["tx_id"], event["functional"]) == ("engine.obd_functional", "0x7df", "0x7e8", True)
    assert (event["request"], event["response"], event["outcome"], event["dispatch_us"]) == ("010c", "410c0c80", "responded", 41)
    assert event["t"].endswith("Z")


def test_event_without_endpoint_context():  # Review Focus 2
    event = json.loads(encode_exchange(rec(b"\x01\x0c", context=None), router(), ENDPOINTS, ORIGIN))
    assert event["endpoint"] is None and event["tx_id"] is None and event["rx_id"] == "0x7df"


def test_truncation_boundary():  # Review Focus 3
    at = json.loads(encode_exchange(rec(b"\x01" + bytes(511)), router(), ENDPOINTS, ORIGIN))
    over = json.loads(encode_exchange(rec(b"\x01" + bytes(512)), router(), ENDPOINTS, ORIGIN))
    assert (at["request_len"], at["request_truncated"], len(at["request"])) == (512, False, 1024)
    assert (over["request_len"], over["request_truncated"], len(over["request"])) == (513, True, 1024)
