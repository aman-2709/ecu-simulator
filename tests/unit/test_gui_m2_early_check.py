"""Unit tests for scripts/gui_m2_early_check.py's pure helpers and cleanup path (Task 10).

No CAN, no simulator, no network: the rotation, the candump pairing, the stop judgment,
the upgrade-response check, the dispatch summary and the cleanup path are all exercised
with synthetic data or fakes. The module imports aiohttp, so this file skips cleanly
where the optional [gui] extra is absent (the `.[dev]` CI job), like the other [gui]
skips (e.g. tests/unit/api/test_server_ws.py).
"""

from __future__ import annotations

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
# Rotation (0010 §9.2: round r runs rotation r of the base order)
# ---------------------------------------------------------------------------

def test_rotation_0_is_the_base_order():
    assert gmec.rotate((1, 2, 4), 0) == (1, 2, 4)


def test_rotation_1_shifts_left_by_one():
    assert gmec.rotate((1, 2, 4), 1) == (2, 4, 1)


def test_rotation_2_shifts_left_by_two():
    assert gmec.rotate((1, 2, 4), 2) == (4, 1, 2)


def test_rotation_3_repeats_rotation_0():
    assert gmec.rotate((1, 2, 4), 3) == (1, 2, 4)


def test_parse_order_accepts_a_permutation():
    assert gmec.parse_order("2,4,1") == (2, 4, 1)


def test_parse_order_rejects_a_non_permutation():
    with pytest.raises(ValueError):
        gmec.parse_order("1,2,3")


# ---------------------------------------------------------------------------
# I1 (fix round 1): --order is a fixed-order control, not a rotation seed.
# ---------------------------------------------------------------------------

def test_round_order_rotates_the_base_when_there_is_no_override():
    assert gmec.round_order(0, None) == (1, 2, 4)
    assert gmec.round_order(1, None) == (2, 4, 1)
    assert gmec.round_order(2, None) == (4, 1, 2)


def test_round_order_is_the_same_fixed_order_every_round_when_overridden():
    override = (2, 4, 1)
    assert gmec.round_order(0, override) == (2, 4, 1)
    assert gmec.round_order(1, override) == (2, 4, 1)
    assert gmec.round_order(2, override) == (2, 4, 1)
    assert gmec.round_order(5, override) == (2, 4, 1)


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
# Stop judgment per round (0010 §9.2 P1-P3), judged against that round's own condition 1
# ---------------------------------------------------------------------------

def test_condition_1_is_never_judged_against_itself_on_latency():
    base = {"median_ms": 1.0, "p99_ms": 2.0, "lost": 0}
    assert gmec.judge(base, base, 1) == []


def test_median_over_the_limit_stops():
    base = {"median_ms": 1.0, "p99_ms": 2.0, "lost": 0}
    r = {"median_ms": 1.11, "p99_ms": 2.0, "lost": 0}
    reasons = gmec.judge(base, r, 2)
    assert reasons and "median" in reasons[0]


def test_median_within_the_limit_passes():
    base = {"median_ms": 1.0, "p99_ms": 2.0, "lost": 0}
    r = {"median_ms": 1.10, "p99_ms": 2.0, "lost": 0}
    assert gmec.judge(base, r, 2) == []


def test_p99_over_the_limit_stops():
    base = {"median_ms": 1.0, "p99_ms": 2.0, "lost": 0}
    r = {"median_ms": 1.0, "p99_ms": 2.51, "lost": 0}
    reasons = gmec.judge(base, r, 4)
    assert reasons and "p99" in reasons[0]


def test_any_lost_reply_stops_even_condition_1():
    base = {"median_ms": 1.0, "p99_ms": 2.0, "lost": 1}
    assert gmec.judge(base, base, 1) != []


def test_p5_problems_stop():
    base = {"median_ms": 1.0, "p99_ms": 2.0, "lost": 0}
    r = {"median_ms": 1.0, "p99_ms": 2.0, "lost": 0, "p5_problems": ["P5(a) issued != published"]}
    assert gmec.judge(base, r, 2) != []


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
        {"number": 1, "position": 1, "median_ms": 1.0, "excess_ms": 0.0},
        {"number": 2, "position": 2, "median_ms": 1.05, "excess_ms": 0.05},
        {"number": 4, "position": 3, "median_ms": 1.20, "excess_ms": 0.20},
    ]
    summary = gmec.position_summary(records)
    assert summary[(2, 2)] == {"median_ms": 1.05, "excess_ms": 0.05, "n": 1}
    assert summary[(4, 3)]["n"] == 1


def test_position_summary_takes_the_median_across_repeated_rounds():
    records = [
        {"number": 4, "position": 1, "median_ms": 1.0, "excess_ms": 0.0},
        {"number": 4, "position": 1, "median_ms": 2.0, "excess_ms": 1.0},
    ]
    summary = gmec.position_summary(records)
    assert summary[(4, 1)] == {"median_ms": 1.5, "excess_ms": 0.5, "n": 2}


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


@pytest.mark.asyncio
async def test_a_failing_tester_still_stops_the_simulator_and_candump(tmp_path):
    dump = _FakeProc()
    sim = _FakeProc()
    tester = _FakeTester(fail=True)

    with pytest.raises(RuntimeError, match="tester blew up"):
        await gmec.condition(
            3, False, False, tmp_path,
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
            3, False, False, tmp_path,
            start_dump=lambda _log: (dump, None), start_sim=boom_sim, make_tester=lambda: _FakeTester(fail=False),
        )

    assert dump.signals and dump.waited


@pytest.mark.asyncio
async def test_a_healthy_condition_still_stops_everything_on_success(tmp_path):
    log = tmp_path / "candump-0-00.log"
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
        2, False, False, tmp_path,
        start_dump=lambda _log: (dump, fh), start_sim=lambda _api: sim, make_tester=lambda: tester,
    )

    assert sim.signals and sim.waited
    assert dump.signals and dump.waited
    assert tester.closed
    assert fh.closed
    assert result["median_ms"] > 0


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
# M5 (fix round 1): the cleanup path must also cover the api=True, clients=True branch --
# reader/stalled tasks stopped, and a raising stalled task must STOP the run.
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

    async def boom_last500() -> dict | None:
        raise RuntimeError("last500 blew up")

    monkeypatch.setattr(gmec, "status", fake_status)
    monkeypatch.setattr(gmec, "reader", fake_reader)
    monkeypatch.setattr(gmec, "stalled", fake_stalled)
    monkeypatch.setattr(gmec, "last500_dispatch", boom_last500)

    with pytest.raises(RuntimeError, match="last500 blew up"):
        await gmec.condition(
            3, True, True, tmp_path,
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
    log = tmp_path / "candump-0-11.log"
    log.write_text(
        "(1.000000) vcan0 7DF#0201000000000000\n"
        "(1.001000) vcan0 7E8#0341000000000000\n"
    )
    dump = _FakeProc()
    sim = _FakeProc()
    tester = _FakeTester(fail=False)

    async def fake_status() -> dict:
        return _empty_status()

    async def fake_reader(stop, seen, dispatch=None):
        await stop.wait()
        return "stopped"

    async def fake_stalled(stop, reconnects, ids):
        raise RuntimeError("stalled blew up")

    async def fake_last500() -> dict | None:
        return None

    monkeypatch.setattr(gmec, "status", fake_status)
    monkeypatch.setattr(gmec, "reader", fake_reader)
    monkeypatch.setattr(gmec, "stalled", fake_stalled)
    monkeypatch.setattr(gmec, "last500_dispatch", fake_last500)

    result = await gmec.condition(
        2, True, True, tmp_path, round_index=0,
        start_dump=lambda _log: (dump, None), start_sim=lambda _api: sim, make_tester=lambda: tester,
    )

    assert any("stalled-client task raised" in p for p in result["p5_problems"])
    base = {"median_ms": 0.0, "p99_ms": 0.0, "lost": 0}
    assert gmec.judge(base, result, 4) != []            # a p5_problems line means STOP
