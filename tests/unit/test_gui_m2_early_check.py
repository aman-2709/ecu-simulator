"""Unit tests for scripts/gui_m2_early_check.py's pure helpers and cleanup path (Task 10,
extended by Task 12 for client configurations, segmented harvest and loud failures).

No CAN, no simulator, no network: the rotation, the candump pairing, the stop judgment,
the upgrade-response check, the dispatch summary, the segmenting/harvest logic and the
cleanup path are all exercised with synthetic data or fakes. The module imports aiohttp,
so this file skips cleanly where the optional [gui] extra is absent (the `.[dev]` CI job),
like the other [gui] skips (e.g. tests/unit/api/test_server_ws.py).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

aiohttp = pytest.importorskip("aiohttp", reason="needs the optional [gui] extra (aiohttp)")

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gui_m2_early_check.py"
_spec = importlib.util.spec_from_file_location("gui_m2_early_check", SCRIPT)
assert _spec is not None and _spec.loader is not None
gmec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gmec)


# ---------------------------------------------------------------------------
# --rounds below 1 (task-12 item 1): argparse error, exit 2, never a silent pass
# ---------------------------------------------------------------------------

def test_rounds_zero_is_an_argparse_error():
    with pytest.raises(SystemExit) as excinfo:
        gmec.build_parser().parse_args(["--rounds", "0"])
    assert excinfo.value.code == 2


def test_rounds_negative_is_an_argparse_error():
    with pytest.raises(SystemExit) as excinfo:
        gmec.build_parser().parse_args(["--rounds", "-1"])
    assert excinfo.value.code == 2


def test_rounds_one_is_accepted():
    args = gmec.build_parser().parse_args(["--rounds", "1"])
    assert args.rounds == 1


def test_positive_int_rejects_zero_directly():
    with pytest.raises(argparse.ArgumentTypeError):
        gmec.positive_int("0")


# ---------------------------------------------------------------------------
# Client configurations (task-12 item 3): --conditions parsing and rotation over 5
# ---------------------------------------------------------------------------

def test_default_conditions_are_todays_early_check():
    assert gmec.DEFAULT_CONDITIONS == ("1", "2", "4")


def test_parse_conditions_accepts_the_default():
    assert gmec.parse_conditions("1,2,4") == ("1", "2", "4")


def test_parse_conditions_accepts_all_five():
    assert gmec.parse_conditions("1,2,r1,r3,4") == ("1", "2", "r1", "r3", "4")


def test_parse_conditions_rejects_an_unknown_label():
    with pytest.raises(ValueError, match="unknown condition"):
        gmec.parse_conditions("1,2,r2")


def test_parse_conditions_rejects_a_duplicate():
    with pytest.raises(ValueError, match="repeat"):
        gmec.parse_conditions("1,2,2")


def test_parse_conditions_requires_condition_1():
    with pytest.raises(ValueError, match="must include condition 1"):
        gmec.parse_conditions("2,4")


def test_conditions_map_client_shapes_correctly():
    assert gmec.CONDITIONS["1"] == (False, 0, False)
    assert gmec.CONDITIONS["2"] == (True, 0, False)
    assert gmec.CONDITIONS["r1"] == (True, 1, False)
    assert gmec.CONDITIONS["r3"] == (True, 3, False)
    assert gmec.CONDITIONS["4"] == (True, 3, True)


def test_rotation_over_five_conditions():
    base = ("1", "2", "r1", "r3", "4")
    assert gmec.rotate(base, 0) == ("1", "2", "r1", "r3", "4")
    assert gmec.rotate(base, 1) == ("2", "r1", "r3", "4", "1")
    assert gmec.rotate(base, 4) == ("4", "1", "2", "r1", "r3")
    assert gmec.rotate(base, 5) == base


def test_rotation_0_is_the_base_order():
    assert gmec.rotate(("1", "2", "4"), 0) == ("1", "2", "4")


def test_rotation_1_shifts_left_by_one():
    assert gmec.rotate(("1", "2", "4"), 1) == ("2", "4", "1")


def test_parse_order_accepts_a_permutation_of_the_given_conditions():
    assert gmec.parse_order("2,4,1", ("1", "2", "4")) == ("2", "4", "1")


def test_parse_order_rejects_a_non_permutation():
    with pytest.raises(ValueError):
        gmec.parse_order("1,2,3", ("1", "2", "4"))


def test_parse_order_validates_against_the_five_condition_set():
    assert gmec.parse_order("4,r3,r1,2,1", ("1", "2", "r1", "r3", "4")) == ("4", "r3", "r1", "2", "1")
    with pytest.raises(ValueError):
        gmec.parse_order("1,2,4", ("1", "2", "r1", "r3", "4"))


def test_round_order_rotates_the_given_conditions_when_there_is_no_override():
    conditions = ("1", "2", "4")
    assert gmec.round_order(0, conditions, None) == ("1", "2", "4")
    assert gmec.round_order(1, conditions, None) == ("2", "4", "1")
    assert gmec.round_order(2, conditions, None) == ("4", "1", "2")


def test_round_order_is_the_same_fixed_order_every_round_when_overridden():
    override = ("2", "4", "1")
    assert gmec.round_order(0, ("1", "2", "4"), override) == override
    assert gmec.round_order(5, ("1", "2", "4"), override) == override


# ---------------------------------------------------------------------------
# Segmenting (task-12 item 4): equal-ish segments, 500 each at n=5000
# ---------------------------------------------------------------------------

def test_segment_sizes_splits_evenly_at_5000():
    sizes = gmec.segment_sizes(5000)
    assert sizes == [500] * 10


def test_segment_sizes_is_ten_segments_at_300():
    sizes = gmec.segment_sizes(300)
    assert sizes == [30] * 10


def test_segment_sizes_spreads_the_remainder_from_the_front():
    sizes = gmec.segment_sizes(23, segments=5)
    assert sizes == [5, 5, 5, 4, 4]
    assert sum(sizes) == 23


def test_segment_sizes_handles_fewer_requests_than_segments():
    sizes = gmec.segment_sizes(3, segments=10)
    assert sizes == [1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
    assert sum(sizes) == 3


# ---------------------------------------------------------------------------
# Harvest gap/miss detection (task-12 item 4), pure logic
# ---------------------------------------------------------------------------

def test_harvest_problem_is_none_when_contiguous():
    body = {"gap": False, "events": [{"seq": 1}, {"seq": 2}, {"seq": 3}]}
    assert gmec.harvest_problem(body, 0) is None


def test_harvest_problem_is_none_for_an_empty_batch():
    body = {"gap": False, "events": []}
    assert gmec.harvest_problem(body, 42) is None


def test_harvest_problem_detects_a_gap():
    body = {"gap": True, "oldest_seq": 501, "events": []}
    problem = gmec.harvest_problem(body, 0)
    assert problem is not None and "gap" in problem


def test_harvest_problem_detects_a_missing_seq():
    body = {"gap": False, "events": [{"seq": 1}, {"seq": 3}]}
    problem = gmec.harvest_problem(body, 0)
    assert problem is not None and "missed a seq" in problem


def test_harvest_problem_detects_a_missing_seq_at_the_start():
    body = {"gap": False, "events": [{"seq": 5}]}
    problem = gmec.harvest_problem(body, 0)
    assert problem is not None and "expected 1, got 5" in problem


@pytest.mark.asyncio
async def test_harvest_extends_dispatch_and_returns_the_new_watermark(monkeypatch):
    class _FakeResponse:
        async def json(self) -> dict:
            return {"gap": False, "oldest_seq": 1,
                     "events": [{"seq": 1, "dispatch_us": 100}, {"seq": 2, "dispatch_us": 200}]}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _FakeSession:
        def get(self, _url: str) -> Any:
            return _FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(gmec.aiohttp, "ClientSession", lambda: _FakeSession())
    dispatch: list[int] = []
    new_after, problem = await gmec.harvest(0, dispatch)
    assert problem is None
    assert new_after == 2
    assert dispatch == [100, 200]


# ---------------------------------------------------------------------------
# Reader completeness (task-12 item 2), pure logic
# ---------------------------------------------------------------------------

def test_reader_problems_empty_when_all_stopped_and_complete():
    readers = [{"exchanges": 10, "ended": "stopped"}, {"exchanges": 10, "ended": "stopped"}]
    assert gmec.reader_problems(readers, 10) == []


def test_reader_problems_flags_an_ending_other_than_stopped():
    readers = [{"exchanges": 10, "ended": "ended early: WSMsgType.CLOSE 1006"}]
    problems = gmec.reader_problems(readers, 10)
    assert any("ended" in p for p in problems)


def test_reader_problems_flags_fewer_exchanges_than_issued_seq():
    readers = [{"exchanges": 7, "ended": "stopped"}]
    problems = gmec.reader_problems(readers, 10)
    assert any("7 < issued_seq 10" in p for p in problems)


def test_reader_problems_ignores_shortfall_when_nothing_was_issued():
    readers = [{"exchanges": 0, "ended": "stopped"}]
    assert gmec.reader_problems(readers, 0) == []


# ---------------------------------------------------------------------------
# Stop judgment per round (0010 §9.2 P1-P3), judged against that round's own condition 1
# ---------------------------------------------------------------------------

def test_condition_1_is_never_judged_against_itself_on_latency():
    base = {"median_ms": 1.0, "p99_ms": 2.0, "lost": 0}
    assert gmec.judge(base, base, "1") == []


def test_median_over_the_limit_stops():
    base = {"median_ms": 1.0, "p99_ms": 2.0, "lost": 0}
    r = {"median_ms": 1.11, "p99_ms": 2.0, "lost": 0}
    reasons = gmec.judge(base, r, "2")
    assert reasons and "median" in reasons[0]


def test_median_within_the_limit_passes():
    base = {"median_ms": 1.0, "p99_ms": 2.0, "lost": 0}
    r = {"median_ms": 1.10, "p99_ms": 2.0, "lost": 0}
    assert gmec.judge(base, r, "2") == []


def test_p99_over_the_limit_stops():
    base = {"median_ms": 1.0, "p99_ms": 2.0, "lost": 0}
    r = {"median_ms": 1.0, "p99_ms": 2.51, "lost": 0}
    reasons = gmec.judge(base, r, "4")
    assert reasons and "p99" in reasons[0]


def test_any_lost_reply_stops_even_condition_1():
    base = {"median_ms": 1.0, "p99_ms": 2.0, "lost": 1}
    assert gmec.judge(base, base, "1") != []


def test_p5_problems_stop():
    base = {"median_ms": 1.0, "p99_ms": 2.0, "lost": 0}
    r = {"median_ms": 1.0, "p99_ms": 2.0, "lost": 0, "p5_problems": ["P5(a) issued != published"]}
    assert gmec.judge(base, r, "2") != []


def test_judge_works_for_a_reader_only_label():
    base = {"median_ms": 1.0, "p99_ms": 2.0, "lost": 0}
    r = {"median_ms": 1.2, "p99_ms": 2.0, "lost": 0}
    reasons = gmec.judge(base, r, "r1")
    assert reasons and "median" in reasons[0]


# ---------------------------------------------------------------------------
# Incremental cost summary (task-12 item 5)
# ---------------------------------------------------------------------------

def test_steps_returns_consecutive_pairs_present():
    assert gmec.steps(("1", "2", "4")) == [("1", "2"), ("2", "4")]


def test_steps_covers_all_five_in_order():
    assert gmec.steps(("1", "2", "r1", "r3", "4")) == [("1", "2"), ("2", "r1"), ("r1", "r3"), ("r3", "4")]


def test_steps_skips_a_missing_condition():
    assert gmec.steps(("1", "4")) == [("1", "4")]


def test_incremental_computes_wire_and_dispatch_diffs():
    a = {"median_ms": 1.0, "p99_ms": 2.0, "dispatch_all_ms": {"median_ms": 0.1, "p99_ms": 0.2}}
    b = {"median_ms": 1.2, "p99_ms": 2.3, "dispatch_all_ms": {"median_ms": 0.15, "p99_ms": 0.25}}
    inc = gmec.incremental(a, b)
    assert inc == {"median_wire_ms": 0.2, "p99_wire_ms": 0.3, "median_dispatch_ms": 0.05, "p99_dispatch_ms": 0.05}


def test_incremental_omits_dispatch_when_condition_1_has_none():
    a = {"median_ms": 1.0, "p99_ms": 2.0}
    b = {"median_ms": 1.2, "p99_ms": 2.3, "dispatch_all_ms": {"median_ms": 0.15, "p99_ms": 0.25}}
    inc = gmec.incremental(a, b)
    assert inc == {"median_wire_ms": 0.2, "p99_wire_ms": 0.3}


def test_step_summary_gives_mean_and_range():
    records = [{"median_wire_ms": 0.10}, {"median_wire_ms": 0.14}, {"median_wire_ms": 0.12}]
    summary = gmec.step_summary(records)
    assert summary == {"median_wire_ms": {"mean": 0.12, "min": 0.10, "max": 0.14}}


def test_step_summary_handles_a_key_present_in_only_some_records():
    records = [{"median_wire_ms": 0.10}, {"median_wire_ms": 0.10, "median_dispatch_ms": 0.02}]
    summary = gmec.step_summary(records)
    assert summary["median_dispatch_ms"] == {"mean": 0.02, "min": 0.02, "max": 0.02}


# ---------------------------------------------------------------------------
# Candump pairing (latencies), from a small synthetic log
# ---------------------------------------------------------------------------

def test_latencies_pairs_each_7df_with_the_next_7e8(tmp_path):
    log = tmp_path / "candump.log"
    log.write_text(
        "(100.000000) vcan0 7DF#0201000000000000\n"
        "(100.001000) vcan0 7E8#0341000000000000\n"
        "(100.500000) vcan0 7DF#0201100000000000\n"
        "(100.502500) vcan0 7E8#0341100000000000\n"
    )
    samples, lost = gmec.latencies(log)
    assert lost == 0
    assert samples == pytest.approx([1.0, 2.5])


def test_latencies_counts_missing_reply_as_lost(tmp_path):
    log = tmp_path / "candump.log"
    log.write_text(
        "(200.000000) vcan0 7DF#0201000000000000\n"
        "(200.500000) vcan0 7DF#0201100000000000\n"
        "(200.501000) vcan0 7E8#0341100000000000\n"
    )
    samples, lost = gmec.latencies(log)
    assert lost == 1
    assert samples == pytest.approx([1.0])


def test_latencies_counts_a_reply_over_1s_late_as_lost(tmp_path):
    log = tmp_path / "candump.log"
    log.write_text(
        "(300.000000) vcan0 7DF#0201000000000000\n"
        "(301.500000) vcan0 7E8#0341000000000000\n"
    )
    samples, lost = gmec.latencies(log)
    assert lost == 1
    assert samples == []


# ---------------------------------------------------------------------------
# Upgrade-response check: 101 accepted; 503, empty and garbage refused
# ---------------------------------------------------------------------------

def test_101_switching_protocols_is_accepted():
    data = b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
    assert gmec.upgrade_ok(data) is None


def test_503_is_refused_with_the_status_line():
    data = b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n"
    problem = gmec.upgrade_ok(data)
    assert problem is not None and "503" in problem


def test_empty_response_is_refused():
    problem = gmec.upgrade_ok(b"")
    assert problem is not None and "no response" in problem


def test_garbage_is_refused():
    problem = gmec.upgrade_ok(b"\x00\x01not http at all")
    assert problem is not None


class _FakeSocket:
    """recv() drip-feeds pre-set chunks, then raises like a real socket at the deadline."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    def settimeout(self, _value: float) -> None:
        pass

    def recv(self, _n: int) -> bytes:
        if not self._chunks:
            raise TimeoutError("timed out")
        return self._chunks.pop(0)


def test_read_upgrade_response_stops_at_the_deadline_with_no_response():
    raw = _FakeSocket([])
    assert gmec.read_upgrade_response(raw) == b""


def test_read_upgrade_response_assembles_chunks_until_the_header_ends():
    raw = _FakeSocket([b"HTTP/1.1 101 Switching", b" Protocols\r\n", b"Upgrade: websocket\r\n\r\n", b"stray"])
    data = gmec.read_upgrade_response(raw)
    assert data.startswith(b"HTTP/1.1 101")
    assert b"\r\n\r\n" in data


# ---------------------------------------------------------------------------
# Dispatch summary, from dispatch_us (microseconds) to median/p99 in ms
# ---------------------------------------------------------------------------

def test_dispatch_summary_is_none_for_no_samples():
    assert gmec.dispatch_summary([]) is None


def test_dispatch_summary_converts_microseconds_to_milliseconds():
    values = [100, 200, 300, 400, 500]  # us -> 0.1, 0.2, 0.3, 0.4, 0.5 ms
    summary = gmec.dispatch_summary(values)
    assert summary == {"median_ms": 0.3, "p99_ms": 0.5, "n": 5}


# ---------------------------------------------------------------------------
# Per-condition/position summary across rounds (0010 §9.2: load vs. order)
# ---------------------------------------------------------------------------

def test_position_summary_groups_by_condition_and_position():
    records = [
        {"number": "1", "position": 1, "median_ms": 1.0, "excess_ms": 0.0, "p99_ms": 2.0, "p99_excess_ms": 0.0,
         "verdict": "within limits"},
        {"number": "2", "position": 2, "median_ms": 1.05, "excess_ms": 0.05, "p99_ms": 2.1, "p99_excess_ms": 0.1,
         "verdict": "within limits"},
        {"number": "4", "position": 3, "median_ms": 1.20, "excess_ms": 0.20, "p99_ms": 2.6, "p99_excess_ms": 0.6,
         "verdict": "STOP"},
    ]
    summary = gmec.position_summary(records)
    assert summary[("2", 2)] == {"median_ms": 1.05, "excess_ms": 0.05, "p99_excess_ms": 0.1, "n": 1, "stops": 0}
    assert summary[("4", 3)] == {"median_ms": 1.2, "excess_ms": 0.2, "p99_excess_ms": 0.6, "n": 1, "stops": 1}


def test_position_summary_takes_the_median_across_repeated_rounds():
    records = [
        {"number": "4", "position": 1, "median_ms": 1.0, "excess_ms": 0.0, "p99_ms": 2.0, "p99_excess_ms": 0.0,
         "verdict": "within limits"},
        {"number": "4", "position": 1, "median_ms": 2.0, "excess_ms": 1.0, "p99_ms": 3.0, "p99_excess_ms": 1.0,
         "verdict": "STOP"},
    ]
    summary = gmec.position_summary(records)
    assert summary[("4", 1)] == {"median_ms": 1.5, "excess_ms": 0.5, "p99_excess_ms": 0.5, "n": 2, "stops": 1}


# ---------------------------------------------------------------------------
# Host info: CPU governors, injectable root so nothing real /sys is required
# ---------------------------------------------------------------------------

def test_cpu_governors_reads_each_cpu_in_order(tmp_path):
    for i, gov in enumerate(["performance", "powersave"]):
        d = tmp_path / f"cpu{i}" / "cpufreq"
        d.mkdir(parents=True)
        (d / "scaling_governor").write_text(f"{gov}\n")
    assert gmec.cpu_governors(tmp_path) == ["performance", "powersave"]


def test_cpu_governors_reports_unknown_when_unreadable(tmp_path):
    (tmp_path / "cpu0").mkdir()  # no cpufreq subdirectory at all
    assert gmec.cpu_governors(tmp_path) == ["unknown"]


# ---------------------------------------------------------------------------
# M7 (fix round 1): nproc must match the `nproc` command, not the process's own affinity-
# blind cpu_count().
# ---------------------------------------------------------------------------

def test_host_info_nproc_matches_the_scheduler_affinity_count():
    assert gmec.host_info()["nproc"] == len(os.sched_getaffinity(0))


# ---------------------------------------------------------------------------
# M3 (fix round 1): open_stalled must never leak its raw socket, on any failure path --
# a refused upgrade, a missing id, or a /status call that raises mid-poll.
# ---------------------------------------------------------------------------

class _FakeRawSocket:
    """A raw stalled socket with a canned HTTP response, like the real one after `sendall`."""

    def __init__(self, response: bytes = b"") -> None:
        self.closed = False
        self._response = response

    def close(self) -> None:
        self.closed = True

    def settimeout(self, _value: float) -> None:
        pass

    def recv(self, _n: int) -> bytes:
        data, self._response = self._response, b""
        return data


@pytest.mark.asyncio
async def test_open_stalled_closes_the_socket_on_a_refused_upgrade(monkeypatch):
    raw = _FakeRawSocket(response=b"HTTP/1.1 503 Service Unavailable\r\n\r\n")
    monkeypatch.setattr(gmec, "stalled_socket", lambda: raw)
    monkeypatch.setattr(gmec, "status", _fake_status_const({"connections": []}))

    with pytest.raises(RuntimeError, match="refused"):
        await gmec.open_stalled([])

    assert raw.closed


@pytest.mark.asyncio
async def test_open_stalled_closes_the_socket_when_the_id_never_appears(monkeypatch):
    raw = _FakeRawSocket(response=b"HTTP/1.1 101 Switching Protocols\r\n\r\n")
    monkeypatch.setattr(gmec, "stalled_socket", lambda: raw)
    monkeypatch.setattr(gmec, "status", _fake_status_const({"connections": []}))

    with pytest.raises(RuntimeError, match="never appeared"):
        await gmec.open_stalled([])

    assert raw.closed


@pytest.mark.asyncio
async def test_open_stalled_closes_the_socket_if_status_raises_mid_poll(monkeypatch):
    raw = _FakeRawSocket(response=b"HTTP/1.1 101 Switching Protocols\r\n\r\n")
    monkeypatch.setattr(gmec, "stalled_socket", lambda: raw)
    calls = {"n": 0}

    async def flaky_status() -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"connections": []}
        raise RuntimeError("status blew up")

    monkeypatch.setattr(gmec, "status", flaky_status)

    with pytest.raises(RuntimeError, match="status blew up"):
        await gmec.open_stalled([])

    assert raw.closed


def _fake_status_const(value: dict):
    async def _status() -> dict:
        return value
    return _status


# ---------------------------------------------------------------------------
# Cleanup on every path: a condition whose step raises still stops what it started.
# Fakes only -- no real processes.
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, wait_timeout_once: bool = False) -> None:
        self.signals: list[int] = []
        self.waited = 0
        self.killed = False
        self._wait_timeout_once = wait_timeout_once

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)

    def wait(self, timeout: float | None = None) -> int:
        self.waited += 1
        if self._wait_timeout_once and self.waited == 1:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0)
        return 0

    def kill(self) -> None:
        self.killed = True


class _FakeTester:
    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.closed = False

    def run(self, _reqs: list[bytes]) -> int:
        if self.fail:
            raise RuntimeError("tester blew up")
        return 0

    def close(self) -> None:
        self.closed = True


class _RaisingCloseTester:
    def close(self) -> None:
        raise RuntimeError("tester close blew up")


class _FakeFileHandle:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _empty_status() -> dict:
    """A /status "api" body that satisfies every P5 identity trivially: nothing happened."""
    return {
        "clients": 0, "connections": [], "closed_connections": [], "closed_unresolved": 0,
        "issued_seq": 0, "published": 0, "handoff_dropped": 0, "connections_opened": 0,
        "forced_disconnects": 0, "fanout_failed": 0, "writer_failed": 0,
        "closed_totals": {
            "offered": 0, "published_span": 0, "enqueued": 0, "client_dropped": 0,
            "sent": 0, "delivery_unknown": 0, "discarded_on_close": 0, "close_codes": {},
            "connections": 0, "delivery_unknown_over_allowance": 0, "delivery_unknown_by_close_code": {},
        },
    }


async def _fake_harvest_noop(after: int, _dispatch: list[int]) -> tuple[int, str | None]:
    return after, None


def _log_path(tmp_path: Path, api: bool, readers: int, stalled: bool, round_index: int = 0) -> Path:
    """The same candump filename condition() computes, so a test's pre-written log lands
    where condition() will look for it."""
    return tmp_path / f"candump-{round_index}-{int(api)}{readers}{int(stalled)}.log"


@pytest.mark.asyncio
async def test_a_failing_tester_still_stops_the_simulator_and_candump(tmp_path):
    dump = _FakeProc()
    sim = _FakeProc()
    tester = _FakeTester(fail=True)

    with pytest.raises(RuntimeError, match="tester blew up"):
        await gmec.condition(
            3, False, 0, False, tmp_path,
            start_dump=lambda _log: (dump, None), start_sim=lambda _api: sim, make_tester=lambda: tester,
        )

    assert sim.signals and sim.waited
    assert dump.signals and dump.waited
    assert tester.closed


@pytest.mark.asyncio
async def test_a_failing_simulator_start_still_stops_candump(tmp_path):
    dump = _FakeProc()

    def boom_sim(_api: bool) -> Any:
        raise RuntimeError("simulator did not become ready")

    with pytest.raises(RuntimeError, match="simulator did not become ready"):
        await gmec.condition(
            3, False, 0, False, tmp_path,
            start_dump=lambda _log: (dump, None), start_sim=boom_sim, make_tester=lambda: _FakeTester(fail=False),
        )

    assert dump.signals and dump.waited


@pytest.mark.asyncio
async def test_a_healthy_condition_still_stops_everything_on_success(tmp_path):
    log = tmp_path / "candump-0-000.log"
    log.write_text(
        "(1.000000) vcan0 7DF#0201000000000000\n"
        "(1.001000) vcan0 7E8#0341000000000000\n"
        "(1.500000) vcan0 7DF#0201100000000000\n"
        "(1.502000) vcan0 7E8#0341100000000000\n"
    )
    dump = _FakeProc()
    sim = _FakeProc()
    tester = _FakeTester(fail=False)
    fh = _FakeFileHandle()

    result = await gmec.condition(
        2, False, 0, False, tmp_path, pause_s=0.0,
        start_dump=lambda _log: (dump, fh), start_sim=lambda _api: sim, make_tester=lambda: tester,
    )

    assert sim.signals and sim.waited
    assert dump.signals and dump.waited
    assert tester.closed
    assert fh.closed
    assert result["median_ms"] > 0
    assert result["pause_s"] == 0.0
    assert "readers" not in result          # readers=0 -> no "readers" key at all


# ---------------------------------------------------------------------------
# I2 (fix round 1): the cleanup chain must be exception-safe -- each step runs whatever
# the one before it did or raised, and a timed-out wait() falls back to kill().
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_teardown_stops_sim_and_dump_even_if_tester_close_raises():
    dump = _FakeProc()
    sim = _FakeProc()

    with pytest.raises(RuntimeError, match="tester close blew up"):
        await gmec._teardown(_RaisingCloseTester(), [], asyncio.Event(), sim, dump, None)

    assert sim.signals and sim.waited
    assert dump.signals and dump.waited


@pytest.mark.asyncio
async def test_teardown_kills_a_process_whose_wait_times_out():
    dump = _FakeProc()
    sim = _FakeProc(wait_timeout_once=True)

    await gmec._teardown(None, [], asyncio.Event(), sim, dump, None)

    assert sim.killed
    assert sim.waited == 2          # one timed-out wait, then a second wait after kill()
    assert dump.signals and dump.waited == 1


@pytest.mark.asyncio
async def test_teardown_kills_dump_too_and_still_closes_its_log_handle():
    dump = _FakeProc(wait_timeout_once=True)
    fh = _FakeFileHandle()

    await gmec._teardown(None, [], asyncio.Event(), None, dump, fh)

    assert dump.killed
    assert fh.closed


@pytest.mark.asyncio
async def test_teardown_closes_the_dump_log_handle_even_if_sim_wait_raises_unexpectedly():
    class _BrokenSim(_FakeProc):
        def wait(self, timeout: float | None = None) -> int:
            raise RuntimeError("sim.wait blew up")

    dump = _FakeProc()
    fh = _FakeFileHandle()

    with pytest.raises(RuntimeError, match="sim.wait blew up"):
        await gmec._teardown(None, [], asyncio.Event(), _BrokenSim(), dump, fh)

    assert dump.signals and dump.waited
    assert fh.closed


# ---------------------------------------------------------------------------
# M5 (fix round 1), extended by task-12: the cleanup path must also cover api=True with
# readers and/or a stalled client -- reader/stalled tasks stopped, and a raising stalled
# task or a raising post-tester step must still tear everything down.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_raise_in_the_api_branch_still_stops_reader_and_stalled_tasks(tmp_path, monkeypatch):
    dump = _FakeProc()
    sim = _FakeProc()
    tester = _FakeTester(fail=False)
    stopped: list[str] = []

    async def fake_status() -> dict:
        return {"clients": 3}                          # enough for the "readers first" wait loop

    async def fake_reader(stop, seen, dispatch=None):
        await stop.wait()
        stopped.append("reader")
        return "stopped"

    async def fake_stalled(stop, reconnects, ids):
        await stop.wait()
        stopped.append("stalled")

    async def boom_status_after_harvest() -> dict:
        raise RuntimeError("status blew up in the api branch")

    monkeypatch.setattr(gmec, "harvest", _fake_harvest_noop)
    monkeypatch.setattr(gmec, "reader", fake_reader)
    monkeypatch.setattr(gmec, "stalled", fake_stalled)

    calls = {"n": 0}

    async def flaky_status() -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"clients": 3}
        raise RuntimeError("status blew up in the api branch")

    monkeypatch.setattr(gmec, "status", flaky_status)

    with pytest.raises(RuntimeError, match="status blew up in the api branch"):
        await gmec.condition(
            3, True, 3, True, tmp_path, pause_s=0.0,
            start_dump=lambda _log: (dump, None), start_sim=lambda _api: sim, make_tester=lambda: tester,
        )

    assert stopped.count("reader") == 3
    assert stopped.count("stalled") == 1
    assert sim.signals and sim.waited
    assert dump.signals and dump.waited
    assert tester.closed


@pytest.mark.asyncio
async def test_stalled_closes_its_raw_socket_when_told_to_stop(monkeypatch):
    raw = _FakeRawSocket()

    async def fake_open_stalled(ids: list[int]) -> Any:
        ids.append(1)
        return raw

    monkeypatch.setattr(gmec, "open_stalled", fake_open_stalled)
    monkeypatch.setattr(gmec, "status", _fake_status_const({"forced_disconnects": 0}))

    stop = asyncio.Event()
    stop.set()                                          # already stopped: the while body never runs
    await gmec.stalled(stop, [], [])

    assert raw.closed


@pytest.mark.asyncio
async def test_a_raising_stalled_task_produces_a_p5_problems_line_and_a_stop(tmp_path, monkeypatch):
    log = _log_path(tmp_path, True, 3, True)
    log.write_text(
        "(1.000000) vcan0 7DF#0201000000000000\n"
        "(1.001000) vcan0 7E8#0341000000000000\n"
    )
    dump = _FakeProc()
    sim = _FakeProc()
    tester = _FakeTester(fail=False)

    calls = {"n": 0}

    async def fake_status() -> dict:
        calls["n"] += 1
        state = _empty_status()
        state["clients"] = 3 if calls["n"] == 1 else 0     # 3 for the readers-first wait, then 0 to quiesce
        return state

    async def fake_reader(stop, seen, dispatch=None):
        await stop.wait()
        return "stopped"

    async def fake_stalled(stop, reconnects, ids):
        raise RuntimeError("stalled blew up")

    monkeypatch.setattr(gmec, "status", fake_status)
    monkeypatch.setattr(gmec, "reader", fake_reader)
    monkeypatch.setattr(gmec, "stalled", fake_stalled)
    monkeypatch.setattr(gmec, "harvest", _fake_harvest_noop)

    result = await gmec.condition(
        2, True, 3, True, tmp_path, round_index=0, pause_s=0.0,
        start_dump=lambda _log: (dump, None), start_sim=lambda _api: sim, make_tester=lambda: tester,
    )

    assert any("stalled-client task raised" in p for p in result["p5_problems"])
    base = {"median_ms": 0.0, "p99_ms": 0.0, "lost": 0}
    assert gmec.judge(base, result, "4") != []           # a p5_problems line means STOP


@pytest.mark.asyncio
async def test_readers_first_wait_raises_when_never_counted_in_status(tmp_path, monkeypatch):
    """task-12 item 2: the "readers first" wait must fail loudly, not fall through, when
    /status never counts the readers."""
    dump = _FakeProc()
    sim = _FakeProc()
    tester = _FakeTester(fail=False)

    async def fake_status() -> dict:
        return {"clients": 0}                            # never reaches the reader count

    async def fake_reader(stop, seen, dispatch=None):
        await stop.wait()
        return "stopped"

    monkeypatch.setattr(gmec, "status", fake_status)
    monkeypatch.setattr(gmec, "reader", fake_reader)

    with pytest.raises(RuntimeError, match="readers never reached"):
        await gmec.condition(
            3, True, 1, False, tmp_path, pause_s=0.0,
            start_dump=lambda _log: (dump, None), start_sim=lambda _api: sim, make_tester=lambda: tester,
        )

    assert sim.signals and sim.waited
    assert dump.signals and dump.waited


@pytest.mark.asyncio
async def test_an_early_ending_reader_produces_a_p5_problems_line_and_a_stop(tmp_path, monkeypatch):
    """task-12 item 2: a reader whose `ended` isn't "stopped" fails the run."""
    log = _log_path(tmp_path, True, 1, False)
    log.write_text(
        "(1.000000) vcan0 7DF#0201000000000000\n"
        "(1.001000) vcan0 7E8#0341000000000000\n"
    )
    dump = _FakeProc()
    sim = _FakeProc()
    tester = _FakeTester(fail=False)

    async def fake_status() -> dict:
        state = _empty_status()
        state["clients"] = 1
        state["issued_seq"] = 5
        state["published"] = 5
        return state

    async def fake_reader(stop, seen, dispatch=None):
        seen.extend([1, 2, 3, 4, 5])
        return "ended early: WSMsgType.CLOSE 1006"

    monkeypatch.setattr(gmec, "status", fake_status)
    monkeypatch.setattr(gmec, "reader", fake_reader)
    monkeypatch.setattr(gmec, "harvest", _fake_harvest_noop)

    result = await gmec.condition(
        2, True, 1, False, tmp_path, round_index=0, pause_s=0.0,
        start_dump=lambda _log: (dump, None), start_sim=lambda _api: sim, make_tester=lambda: tester,
    )

    assert any("ended" in p for p in result["p5_problems"])
    base = {"median_ms": 0.0, "p99_ms": 0.0, "lost": 0}
    assert gmec.judge(base, result, "r1") != []


@pytest.mark.asyncio
async def test_a_reader_short_of_issued_seq_produces_a_p5_problems_line(tmp_path, monkeypatch):
    """task-12 item 2: a reader that received fewer exchanges than the condition published."""
    log = _log_path(tmp_path, True, 1, False)
    log.write_text(
        "(1.000000) vcan0 7DF#0201000000000000\n"
        "(1.001000) vcan0 7E8#0341000000000000\n"
    )
    dump = _FakeProc()
    sim = _FakeProc()
    tester = _FakeTester(fail=False)

    async def fake_status() -> dict:
        state = _empty_status()
        state["clients"] = 1
        state["issued_seq"] = 5
        state["published"] = 5
        return state

    async def fake_reader(stop, seen, dispatch=None):
        seen.extend([1, 2, 3])           # short of issued_seq = 5
        return "stopped"

    monkeypatch.setattr(gmec, "status", fake_status)
    monkeypatch.setattr(gmec, "reader", fake_reader)
    monkeypatch.setattr(gmec, "harvest", _fake_harvest_noop)

    result = await gmec.condition(
        2, True, 1, False, tmp_path, round_index=0, pause_s=0.0,
        start_dump=lambda _log: (dump, None), start_sim=lambda _api: sim, make_tester=lambda: tester,
    )

    assert any("< issued_seq" in p for p in result["p5_problems"])
    assert result["reader_seq_ok"] is True     # strictly increasing, just short -- caught by reader_problems instead


@pytest.mark.asyncio
async def test_reader_seq_ok_is_false_for_an_empty_sequence_when_exchanges_were_published(tmp_path, monkeypatch):
    """task-12 item 2: reader_seq_ok must be false for an empty sequence when exchanges
    were published, even though `[] == sorted(set([]))` is trivially true."""
    log = _log_path(tmp_path, True, 1, False)
    log.write_text(
        "(1.000000) vcan0 7DF#0201000000000000\n"
        "(1.001000) vcan0 7E8#0341000000000000\n"
    )
    dump = _FakeProc()
    sim = _FakeProc()
    tester = _FakeTester(fail=False)

    async def fake_status() -> dict:
        state = _empty_status()
        state["clients"] = 1
        state["issued_seq"] = 5
        state["published"] = 5
        return state

    async def fake_reader(stop, seen, dispatch=None):
        return "stopped"               # never appends anything to `seen`

    monkeypatch.setattr(gmec, "status", fake_status)
    monkeypatch.setattr(gmec, "reader", fake_reader)
    monkeypatch.setattr(gmec, "harvest", _fake_harvest_noop)

    result = await gmec.condition(
        2, True, 1, False, tmp_path, round_index=0, pause_s=0.0,
        start_dump=lambda _log: (dump, None), start_sim=lambda _api: sim, make_tester=lambda: tester,
    )

    assert result["reader_seq_ok"] is False


# ---------------------------------------------------------------------------
# Full-run dispatch harvest (task-12 item 4): the harness fails a condition when the
# harvest sees a gap or a missing seq.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_harvest_gap_produces_a_p5_problems_line_and_a_stop(tmp_path, monkeypatch):
    log = _log_path(tmp_path, True, 0, False)
    log.write_text(
        "(1.000000) vcan0 7DF#0201000000000000\n"
        "(1.001000) vcan0 7E8#0341000000000000\n"
    )
    dump = _FakeProc()
    sim = _FakeProc()
    tester = _FakeTester(fail=False)

    async def fake_status() -> dict:
        return _empty_status()

    calls = {"n": 0}

    async def flaky_harvest(after: int, dispatch: list[int]) -> tuple[int, str | None]:
        calls["n"] += 1
        if calls["n"] == 1:
            return after, "harvest gap: after=0 oldest_seq=501"
        return after, None

    monkeypatch.setattr(gmec, "status", fake_status)
    monkeypatch.setattr(gmec, "harvest", flaky_harvest)

    result = await gmec.condition(
        2, True, 0, False, tmp_path, round_index=0, pause_s=0.0,
        start_dump=lambda _log: (dump, None), start_sim=lambda _api: sim, make_tester=lambda: tester,
    )

    assert any("harvest gap" in p for p in result["p5_problems"])
    base = {"median_ms": 0.0, "p99_ms": 0.0, "lost": 0}
    assert gmec.judge(base, result, "2") != []


@pytest.mark.asyncio
async def test_dispatch_all_ms_collects_every_harvested_sample(tmp_path, monkeypatch):
    log = _log_path(tmp_path, True, 0, False)
    log.write_text(
        "(1.000000) vcan0 7DF#0201000000000000\n"
        "(1.001000) vcan0 7E8#0341000000000000\n"
    )
    dump = _FakeProc()
    sim = _FakeProc()
    tester = _FakeTester(fail=False)

    async def fake_status() -> dict:
        return _empty_status()

    async def fake_harvest(after: int, dispatch: list[int]) -> tuple[int, str | None]:
        dispatch.extend([100, 200])
        return after + 2, None

    monkeypatch.setattr(gmec, "status", fake_status)
    monkeypatch.setattr(gmec, "harvest", fake_harvest)

    result = await gmec.condition(
        2, True, 0, False, tmp_path, round_index=0, pause_s=0.0,
        start_dump=lambda _log: (dump, None), start_sim=lambda _api: sim, make_tester=lambda: tester,
    )

    assert result["dispatch_all_ms"]["n"] == 2 * (gmec.SEGMENTS + 1)     # SEGMENTS pauses + the tail harvest
    assert "dispatch_last500_ms" not in result                          # dropped in favor of dispatch_all_ms


# ---------------------------------------------------------------------------
# Pause overrun (task-12 item 4 decisions): a fetch longer than the pause is visible in
# the output, never silently absorbed.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_slow_harvest_is_recorded_as_a_pause_overrun(tmp_path, monkeypatch):
    log = _log_path(tmp_path, True, 0, False)
    log.write_text(
        "(1.000000) vcan0 7DF#0201000000000000\n"
        "(1.001000) vcan0 7E8#0341000000000000\n"
    )
    dump = _FakeProc()
    sim = _FakeProc()
    tester = _FakeTester(fail=False)

    async def fake_status() -> dict:
        return _empty_status()

    async def slow_harvest(after: int, dispatch: list[int]) -> tuple[int, str | None]:
        await asyncio.sleep(0.02)          # longer than the pause_s=0.01 used below
        return after, None

    monkeypatch.setattr(gmec, "status", fake_status)
    monkeypatch.setattr(gmec, "harvest", slow_harvest)

    result = await gmec.condition(
        2, True, 0, False, tmp_path, round_index=0, pause_s=0.01,
        start_dump=lambda _log: (dump, None), start_sim=lambda _api: sim, make_tester=lambda: tester,
    )

    assert result["pause_overruns_ms"]
    assert all(v > 0 for v in result["pause_overruns_ms"])


@pytest.mark.asyncio
async def test_a_fast_harvest_never_overruns_the_pause(tmp_path, monkeypatch):
    log = _log_path(tmp_path, True, 0, False)
    log.write_text(
        "(1.000000) vcan0 7DF#0201000000000000\n"
        "(1.001000) vcan0 7E8#0341000000000000\n"
    )
    dump = _FakeProc()
    sim = _FakeProc()
    tester = _FakeTester(fail=False)

    async def fake_status() -> dict:
        return _empty_status()

    monkeypatch.setattr(gmec, "status", fake_status)
    monkeypatch.setattr(gmec, "harvest", _fake_harvest_noop)

    result = await gmec.condition(
        2, True, 0, False, tmp_path, round_index=0, pause_s=0.05,
        start_dump=lambda _log: (dump, None), start_sim=lambda _api: sim, make_tester=lambda: tester,
    )

    assert result["pause_overruns_ms"] == []
