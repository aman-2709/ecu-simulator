import pytest

from ecu_simulator.observe.handoff import HandOff
from ecu_simulator.observe.wrapper import ObservedDispatcher
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse

REQ = DiagnosticRequest(b"\x01\x0c", 0x7DF, functional=True)


def wrapped(inner, handoff=None, wakes=None):
    handoff = handoff if handoff is not None else HandOff()
    ticks = iter(range(0, 10_000_000, 1000))       # each clock read advances 1000 ns
    wake = (lambda: wakes.append(1)) if wakes is not None else (lambda: None)
    return ObservedDispatcher(inner, handoff, wake, clock_ns=lambda: next(ticks)), handoff


def test_reply_is_returned_unchanged_and_one_record_is_kept():
    reply = DiagnosticResponse(b"\x41\x0c\x0c\x80")
    wakes: list[int] = []
    observed, handoff = wrapped(lambda request: reply, wakes=wakes)
    assert observed(REQ) is reply
    rec = handoff.popleft()
    assert (rec.seq, rec.elapsed_ns, rec.request, rec.response, rec.error) == (1, 1000, REQ, reply, None)
    assert len(handoff) == 0 and wakes == [1] and observed.issued == 1


def test_sequence_numbers_are_issued_even_when_handoff_drops():
    observed, handoff = wrapped(lambda request: None, handoff=HandOff(max_records=1))
    for _ in range(3):
        observed(REQ)
    assert observed.issued == 3 and handoff.dropped == 2 and handoff.popleft().seq == 1


def test_exceptions_are_recorded_and_reraised():
    def boom(request):
        raise ValueError("bad")
    observed, handoff = wrapped(boom)
    with pytest.raises(ValueError, match="bad"):
        observed(REQ)
    rec = handoff.popleft()
    assert rec.error == "ValueError" and rec.response is None


def test_base_exceptions_are_recorded_and_reraised():  # Review Focus 1
    def interrupted(request):
        raise KeyboardInterrupt
    observed, handoff = wrapped(interrupted)
    with pytest.raises(KeyboardInterrupt):
        observed(REQ)
    assert handoff.popleft().error == "KeyboardInterrupt"


def test_the_hot_path_never_calls_anything_but_wake():  # O3
    calls: list[str] = []
    handoff = HandOff()
    observed = ObservedDispatcher(lambda r: None, handoff, lambda: calls.append("wake"))
    observed(REQ)
    assert calls == ["wake"]
