import pytest

from ecu_simulator.observe.connection import Connection


def conn(**kw):
    clock = {"t": 0.0}
    c = Connection(1, watermark=10, published_at_open=10, now=lambda: clock["t"], **kw)
    return c, clock


def identities(ledger):
    assert ledger["offered"] == ledger["published_at_close"] - ledger["published_at_open"]
    assert ledger["offered"] == ledger["enqueued"] + ledger["client_dropped"]
    assert ledger["enqueued"] == ledger["sent"] + ledger["queued"] + ledger["discarded_on_close"]


def test_queue_order_and_ledger():
    c, _ = conn()
    assert c.offer("a") and c.offer("b")
    assert c.next_message() == "a"
    c.mark_sent()
    ledger = c.ledger(published_now=12)
    assert (ledger["offered"], ledger["enqueued"], ledger["sent"], ledger["queued"]) == (2, 2, 1, 1)
    identities(ledger)


def test_state_and_dropped_are_one_slot_and_go_first():
    c, _ = conn()
    c.offer("x")
    c.set_state("s1")
    c.set_state("s2")
    c.set_dropped("d1")
    assert [c.next_message(), c.next_message(), c.next_message(), c.next_message()] == ["s2", "d1", "x", None]


def test_count_and_byte_limits_drop_new_messages():
    c, _ = conn(max_messages=2, max_bytes=100)
    assert c.offer("a") and c.offer("b") and c.offer("c") is False
    c2, _ = conn(max_messages=100, max_bytes=3)
    assert c2.offer("ab") and c2.offer("cd") is False
    identities(c.ledger(published_now=13))
    identities(c2.ledger(published_now=12))


def test_five_seconds_of_continuous_overflow_closes_with_1013():
    c, clock = conn(max_messages=1)
    c.offer("a")
    c.offer("b")                          # overflow starts at t=0
    clock["t"] = 4.9
    c.offer("c")
    assert not c.closed
    clock["t"] = 5.0
    c.offer("d")
    assert c.closed and c.close_code == 1013
    ledger = c.ledger(published_now=14)
    assert ledger["discarded_on_close"] == 1 and ledger["queued"] == 0
    identities(ledger)


def test_overflow_timer_resets_when_the_queue_drains():
    c, clock = conn(max_messages=1)
    c.offer("a")
    c.offer("b")                          # overflow at t=0
    clock["t"] = 3.0
    c.next_message()
    c.mark_sent()
    c.offer("c")                          # accepted: the overflow run has ended
    clock["t"] = 6.0
    c.offer("d")                          # overflow starts again at 6.0
    assert not c.closed


def test_close_is_idempotent_and_final():  # Review Focus 5
    c, _ = conn(max_messages=1)
    c.offer("a")
    c.offer("b")
    c.close(1000, published_now=12)
    first = c.ledger(published_now=99)
    c.close(1013, published_now=50)
    assert c.ledger(published_now=200) == first and c.close_code == 1000
    assert c.offer("late") is False and first["published_at_close"] == 12
    identities(first)


def test_offer_changes_nothing_when_it_raises():  # Review Focus 7 (amended)
    clock = {"fail": False}
    def now():
        if clock["fail"]:
            raise RuntimeError("clock")
        return 0.0
    c = Connection(1, watermark=10, published_at_open=10, now=now, max_messages=1)
    c.offer("a")
    before = c.ledger(published_now=11)
    clock["fail"] = True
    with pytest.raises(RuntimeError):
        c.offer("b")                      # overflow path: reads the clock before counting anything
    assert c.ledger(published_now=11) == before and not c.closed
    identities(before)


def test_a_message_in_flight_at_close_resolves_as_sent_or_discarded():  # final review
    c, _ = conn()
    c.offer("x")
    c.offer("y")
    assert c.next_message() == "x"                 # handed to send_str, not yet confirmed
    c.close(1013, published_now=12)
    ledger = c.ledger(published_now=99)
    assert (ledger["sent"], ledger["queued"], ledger["discarded_on_close"]) == (0, 1, 1)   # y discarded, x unresolved
    identities(ledger)
    c.mark_sent()                                  # the frame did go out
    ledger = c.ledger(published_now=99)
    assert (ledger["sent"], ledger["queued"], ledger["discarded_on_close"]) == (1, 0, 1)
    identities(ledger)

    d, _ = conn()
    d.offer("x")
    d.next_message()
    d.close(1013, published_now=11)
    d.mark_failed()                                # the send failed
    ledger = d.ledger(published_now=99)
    assert (ledger["sent"], ledger["queued"], ledger["discarded_on_close"]) == (0, 0, 1)
    identities(ledger)


def test_the_documented_writer_contract():
    # The sequence in connection.py's module docstring, step by step.
    c, _ = conn()
    c.set_state("s")
    c.offer("x")
    c.offer("y")
    assert c.next_message() == "s"
    c.mark_sent()                                  # after a state message: a no-op
    assert c.ledger(published_now=12)["sent"] == 0
    assert c.next_message() == "x"                 # exchange in flight
    c.close(1013, published_now=12)                # forced close while send_str is awaiting
    assert c.next_message() is None                # nothing more to send after close
    ledger = c.ledger(published_now=12)
    assert (ledger["sent"], ledger["queued"], ledger["discarded_on_close"]) == (0, 1, 1)
    c.mark_sent()                                  # the frame reached the client after all
    ledger = c.ledger(published_now=12)
    assert (ledger["sent"], ledger["queued"], ledger["discarded_on_close"]) == (1, 0, 1)
    identities(ledger)
