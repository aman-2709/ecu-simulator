"""Unit tests for scripts/gui_m2_early_check.py's pure helpers and cleanup path (Task 10).

No CAN, no simulator, no network: the rotation, the candump pairing, the stop judgment,
the upgrade-response check, the dispatch summary and the cleanup path are all exercised
with synthetic data or fakes. The module imports aiohttp, so this file skips cleanly
where the optional [gui] extra is absent (the `.[dev]` CI job), like the other [gui]
skips (e.g. tests/unit/api/test_server_ws.py).
"""

from __future__ import annotations

import importlib.util
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
# Cleanup on every path: a condition whose step raises still stops what it started.
# Fakes only -- no real processes.
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self) -> None:
        self.signals: list[int] = []
        self.waited = False

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return 0


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


@pytest.mark.asyncio
async def test_a_failing_tester_still_stops_the_simulator_and_candump(tmp_path):
    dump = _FakeProc()
    sim = _FakeProc()
    tester = _FakeTester(fail=True)

    with pytest.raises(RuntimeError, match="tester blew up"):
        await gmec.condition(
            3, False, False, tmp_path,
            start_dump=lambda _log: dump, start_sim=lambda _api: sim, make_tester=lambda: tester,
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
            start_dump=lambda _log: dump, start_sim=boom_sim, make_tester=lambda: _FakeTester(fail=False),
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

    result = await gmec.condition(
        2, False, False, tmp_path,
        start_dump=lambda _log: dump, start_sim=lambda _api: sim, make_tester=lambda: tester,
    )

    assert sim.signals and sim.waited
    assert dump.signals and dump.waited
    assert tester.closed
    assert result["median_ms"] > 0
