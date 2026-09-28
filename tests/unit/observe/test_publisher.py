import asyncio
import itertools
import json
import logging
import socket
import time

import pytest

from ecu_simulator.observe.handoff import ExchangeRecord, HandOff
from ecu_simulator.observe.limits import TURN_MAX_RECORDS, TURN_MAX_S
from ecu_simulator.observe.publisher import Publisher, TooManyClients
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse


class Router:
    def resolve(self, request):
        class Route:
            ecu = "engine"
        return (Route(),)


def fill(handoff, n, start=1):
    for seq in range(start, start + n):
        handoff.append(ExchangeRecord(seq, 0, 0, DiagnosticRequest(b"\x01\x0c", 0x7DF, functional=True), DiagnosticResponse(b"\x41\x0c\x00\x00"), None))


def publisher(handoff, **kw):
    return Publisher(handoff, Router(), {}, encode=lambda rec, *_: json.dumps({"type": "exchange", "seq": rec.seq}), **kw)


def test_a_turn_stops_at_64_records():
    handoff = HandOff()
    fill(handoff, 200)
    p = publisher(handoff, monotonic=lambda: 0.0)
    assert p.drain_turn() == 64 and len(handoff) == 136 and p.published == 64


def test_a_turn_stops_at_the_time_budget():
    handoff = HandOff()
    fill(handoff, 50)
    clock = {"t": 0.0}
    def slow(rec, *_):
        clock["t"] += 0.0003                      # 0.3 ms per record
        return json.dumps({"seq": rec.seq})
    p = Publisher(handoff, Router(), {}, encode=slow, monotonic=lambda: clock["t"])
    assert p.drain_turn() == 4                   # 4th record takes the turn to 1.2 ms: stop after it
    assert p.longest_turn_s == pytest.approx(0.0012)


@pytest.mark.asyncio
async def test_O4_ready_io_and_call_soon_run_between_turns_while_handoff_is_non_empty():  # (amended)
    handoff = HandOff()
    fill(handoff, 4096)
    reads: list[float] = []                       # every clock read the publisher makes
    def clock() -> float:
        now = time.monotonic()
        reads.append(now)
        return now
    p = publisher(handoff, monotonic=clock)
    loop = asyncio.get_running_loop()
    log: list[tuple[str, int]] = []               # (what ran, len(handoff) when it ran)
    turns: list[tuple[int, list[float]]] = []     # (records processed, clock reads in that turn)
    real_turn = p.drain_turn
    def logged_turn() -> int:
        first = len(reads)
        n = real_turn()
        turns.append((n, reads[first:]))
        log.append(("turn", len(handoff)))
        loop.call_soon(lambda: log.append(("soon", len(handoff))))   # scheduled from inside the drain
        return n
    p.drain_turn = logged_turn
    reader, writer = socket.socketpair()
    reader.setblocking(False)
    writer.setblocking(False)
    def readable() -> None:                       # ready I/O: always one byte waiting
        reader.recv(1)
        log.append(("io", len(handoff)))
        writer.send(b"x")
    loop.add_reader(reader.fileno(), readable)
    writer.send(b"x")
    task = asyncio.create_task(p.run())
    p.wake()
    try:
        while len(handoff):
            await asyncio.sleep(0)
    finally:
        task.cancel()
        loop.remove_reader(reader.fileno())
        reader.close()
        writer.close()

    at = [i for i, (kind, _) in enumerate(log) if kind == "turn"]
    assert len(at) >= 4096 // TURN_MAX_RECORDS
    for i, j in itertools.pairwise(at):
        between = log[i + 1:j]
        assert {"soon", "io"} <= {kind for kind, _ in between}, (i, j, between)
        assert all(left > 0 for _, left in between)          # HandOff still non-empty when they ran
    # Measured against max_turn_s: reads are [start, one per record..., final].
    for (n, turn_reads), left in zip(turns, [left for kind, left in log if kind == "turn"], strict=True):
        assert len(turn_reads) == n + 2
        start, checks = turn_reads[0], turn_reads[1:n + 1]
        assert all(t - start < TURN_MAX_S for t in checks[:-1])   # never continued past the budget
        assert n == TURN_MAX_RECORDS or left == 0 or checks[-1] - start >= TURN_MAX_S   # stopped for a reason
    assert p.longest_turn_s == max(r[-1] - r[0] for _, r in turns)


def test_encode_failure_publishes_a_fallback_and_seq_stays_contiguous(caplog):  # Review Focus 7
    handoff = HandOff()
    fill(handoff, 4)
    def flaky(rec, *_):
        if rec.seq == 2:
            raise ValueError("bad record")
        if rec.seq == 3:
            raise ValueError("bad again")
        return json.dumps({"type": "exchange", "seq": rec.seq})
    p = Publisher(handoff, Router(), {}, encode=flaky, monotonic=lambda: 0.0)
    conn, _, _ = p.connect()
    with caplog.at_level(logging.ERROR, logger="ecu_simulator.observe.publisher"):
        assert p.drain_turn() == 4
    assert [seq for seq, _ in p.history.snapshot()] == [1, 2, 3, 4]
    assert json.loads(p.history.snapshot()[1][1]) == {"type": "exchange", "seq": 2, "outcome": "responded", "error": "encode_failed"}
    assert (p.published, p.encode_failed) == (4, 2)
    assert len([r for r in caplog.records if "encode" in r.getMessage()]) == 1   # once per error type
    assert [json.loads(conn.next_message())["seq"] for _ in range(4)] == [1, 2, 3, 4]
    stats = p.stats(issued=4)
    assert stats["issued_seq"] == stats["published"] + stats["handoff_dropped"] and stats["encode_failed"] == 2


def test_encode_fallback_survives_a_failing_router():
    handoff = HandOff()
    fill(handoff, 1)
    class Broken:
        def resolve(self, request):
            raise RuntimeError("router")
    def boom(*_):
        raise ValueError
    p = Publisher(handoff, Broken(), {}, encode=boom, monotonic=lambda: 0.0)
    assert p.drain_turn() == 1
    assert json.loads(p.history.snapshot()[0][1])["outcome"] == "responded"


def test_fanout_failure_closes_only_that_connection():  # Review Focus 7
    handoff = HandOff()
    p = publisher(handoff, monotonic=lambda: 0.0)
    a, _, _ = p.connect()
    b, _, _ = p.connect()
    fill(handoff, 1)
    p.drain_turn()
    def broken(text):
        raise RuntimeError("offer")
    a.offer = broken
    fill(handoff, 2, start=2)
    assert p.drain_turn() == 2                               # did not raise
    assert a.closed and a.close_code == 1011 and a not in p.connections
    assert p.fanout_failed == 1 and p.published == 3
    assert [json.loads(b.next_message())["seq"] for _ in range(3)] == [1, 2, 3]
    stats = p.stats(issued=3)
    (closed,) = stats["closed_connections"]
    assert (closed["id"], closed["offered"], closed["published_at_close"]) == (a.id, 1, 1)
    for ledger in stats["connections"] + stats["closed_connections"]:
        assert ledger["offered"] == ledger["published_at_close"] - ledger["published_at_open"]
        assert ledger["offered"] == ledger["enqueued"] + ledger["client_dropped"]
    assert stats["fanout_failed"] == 1 and stats["writer_failed"] == 0
    assert stats["closed_totals"]["close_codes"]["1011"] == stats["fanout_failed"] + stats["writer_failed"]


def test_a_connection_closed_elsewhere_is_retired_at_the_next_publication():
    handoff = HandOff()
    p = publisher(handoff)
    c, _, _ = p.connect()
    fill(handoff, 1)
    p.drain_turn()
    c.close(1000, published_now=p.published)                 # e.g. M2's writer task saw the socket fail
    fill(handoff, 1, start=2)
    p.drain_turn()
    assert c not in p.connections and c.offered == 1
    (closed,) = p.stats(issued=2)["closed_connections"]
    assert closed["offered"] == closed["published_at_close"] - closed["published_at_open"] == 1


def test_watermark_handshake_has_no_gap_and_no_duplicate():
    handoff = HandOff()
    fill(handoff, 5)
    p = publisher(handoff)
    p.drain_turn()                                # seq 1..5 published
    conn, hello, history = p.connect()
    assert hello == {"type": "hello", "api": 1, "watermark": 5, "oldest_seq": 1}
    assert [json.loads(t)["seq"] for t in history] == [1, 2, 3, 4, 5]
    fill(handoff, 3, start=6)
    p.drain_turn()
    live = [json.loads(conn.next_message())["seq"] for _ in range(3)]
    assert live == [6, 7, 8]


def test_client_limit_refuses_and_counts():
    p = publisher(HandOff(), max_clients=2)
    p.connect()
    p.connect()
    with pytest.raises(TooManyClients):
        p.connect()
    assert p.refused_clients == 1


def test_ledgers_reconcile_after_quiesce():
    handoff = HandOff()
    p = publisher(handoff)
    a, _, _ = p.connect()
    fill(handoff, 10)
    p.drain_turn()
    b, _, _ = p.connect()
    fill(handoff, 10, start=11)
    p.drain_turn()
    while a.next_message():
        a.mark_sent()
    p.disconnect(b, 1000)
    stats = p.stats(issued=20)
    assert stats["issued_seq"] == stats["published"] + stats["handoff_dropped"] == 20
    for ledger in stats["connections"] + stats["closed_connections"]:
        assert ledger["offered"] == ledger["published_at_close"] - ledger["published_at_open"]
        assert ledger["offered"] == ledger["enqueued"] + ledger["client_dropped"]
        assert ledger["enqueued"] == (ledger["sent"] + ledger["delivery_unknown"] + ledger["queued"]
                                       + ledger["discarded_on_close"])


def test_a_bad_after_registers_nothing():  # (amended)
    p = publisher(HandOff())
    with pytest.raises(ValueError):
        p.connect(after=-1)
    assert p.connections == [] and p.refused_clients == 0


@pytest.mark.asyncio
async def test_state_is_pushed_only_when_it_changes_and_dropped_follows_it():
    p = publisher(HandOff())
    conn, _, _ = p.connect()
    texts = iter(["a", "a", "b"])
    calls: list[str] = []
    def snapshot() -> str:
        calls.append(text := next(texts))
        return text
    pushed: list[str] = []
    real_push = p.push_state
    def spy(text: str) -> None:
        pushed.append(text)
        real_push(text)
    p.push_state = spy
    task = asyncio.create_task(p.run_state(snapshot, interval_s=0))
    while len(calls) < 3:
        await asyncio.sleep(0)
    task.cancel()
    assert pushed == ["a", "b"]                                 # the repeated "a" was not pushed
    assert conn.next_message() == "b"                           # one slot: "b" replaced "a"
    assert json.loads(conn.next_message())["type"] == "dropped"
    assert conn.next_message() is None


def test_closed_connections_are_retired_without_traffic():  # final review
    p = publisher(HandOff(), max_clients=2)
    a, _, _ = p.connect()
    b, _, _ = p.connect()
    a.close(1001, published_now=p.published)       # e.g. the browser tab closed; no exchange follows
    b.close(1001, published_now=p.published)
    stats = p.stats(issued=0)
    assert stats["clients"] == 0 and stats["connections"] == [] and len(stats["closed_connections"]) == 2
    p.connect()                                    # not TooManyClients


@pytest.mark.asyncio
async def test_a_late_client_gets_the_current_state():  # final review
    p = publisher(HandOff())
    calls: list[int] = []
    def snapshot() -> str:
        calls.append(1)
        return "s"
    task = asyncio.create_task(p.run_state(snapshot, interval_s=0))
    while len(calls) < 2:                          # state unchanged since the first push
        await asyncio.sleep(0)
    conn, _, _ = p.connect()
    task.cancel()
    assert conn.next_message() == "s"


def test_a_closed_ledger_reflects_a_send_confirmed_after_close():  # final review
    handoff = HandOff()
    p = publisher(handoff)
    conn, _, _ = p.connect()
    fill(handoff, 1)
    p.drain_turn()
    conn.next_message()
    p.disconnect(conn, 1013)
    conn.mark_sent()
    (closed,) = p.stats(issued=1)["closed_connections"]
    assert (closed["sent"], closed["queued"], closed["discarded_on_close"]) == (1, 0, 0)


def test_abandon_keeps_the_ledger_exact_when_the_clock_also_fails():  # final review
    handoff = HandOff()
    p = publisher(handoff, monotonic=lambda: 0.0)
    a, _, _ = p.connect()
    fill(handoff, 1)
    p.drain_turn()
    def broken_offer(text):
        raise RuntimeError("offer")
    def broken_clock():
        raise RuntimeError("clock")
    a.offer = broken_offer
    a._now = broken_clock                          # close() must still close, without a timestamp
    fill(handoff, 1, start=2)
    assert p.drain_turn() == 1
    assert a.closed and a.close_code == 1011 and a not in p.connections
    stats = p.stats(issued=2)
    (closed,) = stats["closed_connections"]
    assert closed["closed_at"] is None and closed["published_at_close"] == 1
    assert closed["offered"] == closed["published_at_close"] - closed["published_at_open"]
    assert closed["offered"] == closed["enqueued"] + closed["client_dropped"]
    assert stats["writer_failed"] == 0
    assert stats["closed_totals"]["close_codes"]["1011"] == stats["fanout_failed"] + stats["writer_failed"]


def test_totals_reconcile_beyond_the_64_retained_ledgers():
    handoff = HandOff()
    p = Publisher(handoff, Router(), {}, encode=lambda rec, *_: json.dumps({"seq": rec.seq}),
                  connection_options={"max_messages": 2, "overflow_disconnect_s": 0.0})
    seq = 1
    for i in range(200):
        c, _, _ = p.connect()
        fill(handoff, 3, start=seq)
        seq += 3
        p.drain_turn()                                   # 3 offered: 2 enqueued, then a forced 1013 on the 3rd
        if i % 2:
            p.disconnect(c, 1000)                        # already forced: stays 1013, idempotent
    stats = p.stats(issued=seq - 1)
    totals = stats["closed_totals"]
    assert len(stats["closed_connections"]) == 64 and totals["connections"] == 200
    assert stats["connections_opened"] == stats["clients"] + totals["connections"] + stats["closed_unresolved"] == 200
    assert totals["offered"] == totals["published_span"] == totals["enqueued"] + totals["client_dropped"]
    assert totals["enqueued"] == totals["sent"] + totals["delivery_unknown"] + totals["discarded_on_close"]
    assert totals["delivery_unknown"] == 0
    assert totals["close_codes"] == {"1013": 200} and stats["forced_disconnects"] == 200


def test_an_unresolved_close_is_added_to_the_totals_only_when_it_resolves():
    handoff = HandOff()
    p = publisher(handoff)
    c, _, _ = p.connect()
    fill(handoff, 1)
    p.drain_turn()
    c.next_message()                                      # in flight
    p.disconnect(c, 1001)
    stats = p.stats(issued=1)
    assert (stats["closed_unresolved"], stats["closed_totals"]["connections"]) == (1, 0)
    c.mark_sent()
    stats = p.stats(issued=1)
    assert (stats["closed_unresolved"], stats["closed_totals"]["connections"], stats["closed_totals"]["sent"]) == (0, 1, 1)


def test_delivery_unknown_is_carried_into_the_totals():
    handoff = HandOff()
    p = publisher(handoff)
    c, _, _ = p.connect()
    fill(handoff, 1)
    p.drain_turn()
    c.next_message()
    p.disconnect(c, 1001)
    c.mark_unknown()
    totals = p.stats(issued=1)["closed_totals"]
    assert (totals["connections"], totals["sent"], totals["delivery_unknown"]) == (1, 0, 1)
    assert totals["enqueued"] == totals["sent"] + totals["delivery_unknown"] + totals["discarded_on_close"]


def test_connection_options_reach_every_connection():
    p = Publisher(HandOff(), Router(), {}, connection_options={"max_messages": 1})
    c, _, _ = p.connect()
    assert c.offer("a") and c.offer("b") is False


def test_delivery_unknown_is_totalled_per_close_code():  # 0010 P5(h), sixth revision
    handoff = HandOff()
    p = publisher(handoff)
    forced, _, _ = p.connect()
    normal, _, _ = p.connect()
    fill(handoff, 1)
    p.drain_turn()
    forced.next_message()                                 # in flight when closed 1013
    p.disconnect(forced, 1013)
    forced.mark_unknown()
    normal.next_message()
    normal.mark_sent()
    p.disconnect(normal, 1000)
    totals = p.stats(issued=1)["closed_totals"]
    assert totals["close_codes"] == {"1013": 1, "1000": 1}
    assert totals["delivery_unknown_by_close_code"] == {"1013": 1}  # a zero sum adds no code
    totals["delivery_unknown_by_close_code"]["1013"] = 99             # stats() returns a copy
    assert p.stats(issued=1)["closed_totals"]["delivery_unknown_by_close_code"] == {"1013": 1}


def test_a_failed_writer_closes_its_connection_1011_and_is_counted():
    p = publisher(HandOff())
    c, _, _ = p.connect()
    p.fail_writer(c)
    stats = p.stats(issued=0)
    assert c.close_code == 1011 and stats["clients"] == 0
    assert (stats["writer_failed"], stats["fanout_failed"]) == (1, 0)
    assert stats["closed_totals"]["close_codes"] == {"1011": 1}


def closes_with_one_offender(offender: bool):
    """Two early 1013 closes (one with two unknowns if ``offender``), then 70 clean 1000 closes."""
    handoff = HandOff()
    p = publisher(handoff)
    first, _, _ = p.connect()
    clean, _, _ = p.connect()
    fill(handoff, 2)
    p.drain_turn()
    for _ in range(2):
        first.next_message()                             # in flight
        if offender:
            first.mark_unknown()
        else:
            first.mark_sent()
    p.disconnect(clean, 1013)                            # folded first: a running sum check passes 2 <= 2
    p.disconnect(first, 1013)
    for _ in range(70):
        c, _, _ = p.connect()
        p.disconnect(c, 1000)
    return p, first


def test_a_connection_over_its_allowance_is_counted_after_its_ledger_is_evicted():  # 0010 §5.1, P5(h)
    p, offender = closes_with_one_offender(offender=True)
    stats = p.stats(issued=2)
    totals = stats["closed_totals"]
    assert offender.id not in [ledger["id"] for ledger in stats["closed_connections"]]   # evicted
    assert totals["delivery_unknown_over_allowance"] == 1
    # Why the counter exists: the per-code sums alone pass (2 unknowns over 2 closes 1013).
    assert totals["delivery_unknown_by_close_code"] == {"1013": 2} and totals["close_codes"]["1013"] == 2
    assert totals["delivery_unknown_by_close_code"]["1013"] <= totals["close_codes"]["1013"]


def test_no_offender_no_count():
    p, _ = closes_with_one_offender(offender=False)
    assert p.stats(issued=2)["closed_totals"]["delivery_unknown_over_allowance"] == 0
