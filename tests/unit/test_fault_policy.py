"""FaultPolicy: deterministic rules around the ordinary ECU response path."""

import pytest

from ecu_simulator.faults import (
    Corrupt,
    Delay,
    Drop,
    FaultPolicy,
    FaultRule,
    NrcOverride,
    Offline,
    ResponsePending,
    Truncate,
)
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse


def request(sid=0x22, *, functional=False):
    return DiagnosticRequest(bytes([sid, 0xF1, 0x90]), 0x7E1, functional=functional)


def response(payload=b"\x62\xf1\x90DATA"):
    return DiagnosticResponse(payload)


def apply(action, *, ordinary=response):
    return FaultPolicy([FaultRule(action)]).apply(request(), ordinary)


def test_no_rules_is_exactly_the_ordinary_path():
    expected = response()
    assert FaultPolicy().apply(request(), lambda: expected) is expected


def test_drop_processes_the_request_then_discards_the_response():
    calls = []
    assert apply(Drop(), ordinary=lambda: calls.append("handled") or response()) is None
    assert calls == ["handled"]


def test_offline_bypasses_the_ecu_handler_entirely():
    calls = []
    assert apply(Offline(), ordinary=lambda: calls.append("handled") or response()) is None
    assert calls == []


def test_delay_marks_the_ordinary_response_for_async_scheduling():
    observed = apply(Delay(0.025))
    assert observed.payload == response().payload and observed.delay == 0.025


def test_nrc_override_uses_the_request_sid_even_when_the_ordinary_path_is_silent():
    observed = apply(NrcOverride(0x22), ordinary=lambda: None)
    assert observed == DiagnosticResponse(b"\x7f\x22\x22")


def test_response_pending_is_immediate_and_the_ordinary_final_is_delayed():
    final = response()
    observed = apply(ResponsePending(0.05), ordinary=lambda: final)
    assert observed.payload == b"\x7f\x22\x78" and observed.delay == 0
    assert observed.follow_ups == (DiagnosticResponse(final.payload, delay=0.05),)


def test_response_pending_stays_silent_when_there_is_no_final_response():
    assert apply(ResponsePending(0.05), ordinary=lambda: None) is None


def test_truncate_removes_bytes_but_never_makes_an_empty_response():
    assert apply(Truncate(3)).payload == b"\x62\xf1\x90D"
    assert apply(Truncate(99)).payload == b"\x62"


def test_corrupt_xors_a_wrapped_byte_offset():
    observed = apply(Corrupt(offset=8, xor=0xFF))
    assert observed.payload == bytes([0x62, 0x0E, 0x90]) + b"DATA"


def test_occurrence_sid_and_addressing_filters_are_deterministic():
    policy = FaultPolicy(
        [FaultRule(NrcOverride(0x21), sid=0x22, functional=False, occurrences=frozenset({2, 4}))]
    )

    def ordinary():
        return response()

    assert policy.apply(request(0x10), ordinary) == response()
    assert policy.apply(request(functional=True), ordinary) == response()
    assert policy.apply(request(), ordinary) == response()
    assert policy.apply(request(), ordinary).payload == b"\x7f\x22\x21"
    assert policy.apply(request(), ordinary) == response()
    assert policy.apply(request(), ordinary).payload == b"\x7f\x22\x21"


def test_always_triggers_on_every_matching_request():
    policy = FaultPolicy([FaultRule(Drop(), occurrences=None)])
    assert policy.apply(request(), response) is None
    assert policy.apply(request(), response) is None


def test_all_matching_counters_advance_when_the_first_triggered_rule_wins():
    policy = FaultPolicy(
        [
            FaultRule(Drop(), sid=0x22, occurrences=frozenset({1})),
            FaultRule(NrcOverride(0x21), sid=0x22, occurrences=frozenset({2})),
        ]
    )
    assert policy.apply(request(), response) is None
    assert policy.apply(request(), response).payload == b"\x7f\x22\x21"


@pytest.mark.parametrize(
    "rule",
    [
        FaultRule,
        lambda: FaultRule(Delay(-1)),
        lambda: FaultRule(NrcOverride(256)),
        lambda: FaultRule(Truncate(0)),
        lambda: FaultRule(Corrupt(0, 0)),
        lambda: FaultRule(Drop(), sid=256),
        lambda: FaultRule(Drop(), occurrences=frozenset({0})),
    ],
)
def test_invalid_rules_are_rejected(rule):
    with pytest.raises((TypeError, ValueError)):
        rule()
