import pytest

from ecu_simulator.observe.history import HistoryRing


def filled(n, **kw):
    ring = HistoryRing(**kw)
    for seq in range(1, n + 1):
        ring.add(seq, f"e{seq}")
    return ring


def test_count_limit_evicts_oldest():
    ring = filled(5, max_events=3)
    assert ring.oldest_seq == 3 and ring.last_seq == 5
    assert ring.since(None, 10) == (["e3", "e4", "e5"], False)


def test_byte_limit_evicts_oldest():
    ring = filled(4, max_events=100, max_bytes=5)   # each text is 2 bytes
    assert ring.oldest_seq == 3


def test_after_returns_newer_only_and_flags_gaps():
    ring = filled(10, max_events=5)                  # holds 6..10
    assert ring.since(7, 10) == (["e8", "e9", "e10"], False)
    assert ring.since(5, 10) == (["e6", "e7", "e8", "e9", "e10"], False)   # 5 = oldest - 1: no gap
    assert ring.since(2, 10) == (["e6", "e7", "e8", "e9", "e10"], True)
    assert ring.since(None, 2) == (["e9", "e10"], False)


def test_after_beyond_newest_and_empty_ring():  # Review Focus 4
    assert filled(3).since(99, 10) == ([], False)
    assert HistoryRing().since(0, 10) == ([], False)
    assert HistoryRing().oldest_seq is None and HistoryRing().last_seq == 0
    assert HistoryRing().since() == ([], False)


def test_limit_bounds():  # Review Focus 6 (amended)
    ring = filled(600, max_events=600)
    assert len(ring.since()[0]) == 500                        # default
    assert len(ring.since(None, 501)[0]) == 500               # clamped
    assert ring.since(None, 1) == (["e600"], False)
    assert ring.since(0, 3) == (["e1", "e2", "e3"], False)
    for bad in (0, -1):
        with pytest.raises(ValueError):
            ring.since(None, bad)
    for bad in (True, 2.0, "5"):
        with pytest.raises(TypeError):
            ring.since(None, bad)


def test_after_bounds():  # Review Focus 6 (amended)
    ring = filled(10, max_events=5)                          # holds 6..10
    assert ring.since(0, 10) == (["e6", "e7", "e8", "e9", "e10"], True)
    with pytest.raises(ValueError):
        ring.since(-1, 10)
    for bad in (True, 7.0, "7"):
        with pytest.raises(TypeError):
            ring.since(bad, 10)
