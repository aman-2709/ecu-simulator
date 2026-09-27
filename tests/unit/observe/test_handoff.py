from ecu_simulator.observe.handoff import ExchangeRecord, HandOff
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse


def record(seq: int, request: bytes = b"\x01\x0c", response: bytes | None = b"\x41\x0c\x0c\x80") -> ExchangeRecord:
    return ExchangeRecord(
        seq, 0, 0, DiagnosticRequest(request, 0x7DF, functional=True),
        DiagnosticResponse(response) if response is not None else None, None,
    )


def test_records_come_out_in_order():
    handoff = HandOff()
    for seq in (1, 2, 3):
        assert handoff.append(record(seq))
    assert [handoff.popleft().seq for _ in range(3)] == [1, 2, 3]
    assert len(handoff) == 0 and handoff.bytes == 0


def test_count_limit_drops_the_new_record_and_counts_it():
    handoff = HandOff(max_records=2, max_bytes=10_000)
    assert handoff.append(record(1)) and handoff.append(record(2))
    assert handoff.append(record(3)) is False
    assert handoff.dropped == 1
    assert [handoff.popleft().seq for _ in range(2)] == [1, 2]  # the new one was dropped, not the oldest


def test_byte_limit_counts_request_and_response_lengths():
    handoff = HandOff(max_records=100, max_bytes=12)
    assert handoff.append(record(1))                   # 2 + 4 = 6 bytes
    assert handoff.append(record(2))                   # 12 bytes: exactly at the limit
    assert handoff.append(record(3)) is False          # 18 > 12
    assert handoff.bytes == 12 and handoff.dropped == 1
    handoff.popleft()
    assert handoff.bytes == 6


def test_a_silent_record_counts_only_its_request():
    handoff = HandOff(max_records=10, max_bytes=2)
    assert handoff.append(record(1, response=None))
    assert handoff.bytes == 2
