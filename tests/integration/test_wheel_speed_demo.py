"""Browser acceptance with independent kernel timestamps and fixed wire vectors."""

import re
import socket
import statistics
import struct
import time
from itertools import pairwise

import pytest

from tests.integration.test_gui_control_vcan import browser, expect, send, server, stop  # noqa: F401

ZERO = bytes.fromhex("1A 6F") * 4
FORTY = bytes.fromhex("2A 0F") * 4
TIMESTAMP_NS = getattr(socket, "SO_TIMESTAMPNS", 35)


def sample(sock):
    raw, ancillary, _, _ = sock.recvmsg(16, socket.CMSG_SPACE(struct.calcsize("@ll")))
    can_id, length, data = struct.unpack("=IB3x8s", raw)
    assert (can_id, length) == (0xAA, 8)
    timestamp = next(value for level, kind, value in ancillary if (level, kind) == (socket.SOL_SOCKET, TIMESTAMP_NS))
    seconds, nanos = struct.unpack("@ll", timestamp)
    return seconds * 10**9 + nanos, data


def wait_for_sample(sock, expected):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        _, data = sample(sock)
        if data == expected:
            return
    pytest.fail(f"Missing wheel-speed payload {expected.hex()}")


def assert_silent(sock):
    # Drop frames buffered before the confirmed transition.
    sock.setblocking(False)
    while True:
        try:
            sock.recv(16)
        except BlockingIOError:
            break
    sock.settimeout(0.15)
    with pytest.raises(TimeoutError):
        sock.recv(16)
    sock.settimeout(2)


def test_wheel_speed_demo(browser, vcan, tmp_path):  # noqa: F811
    with server(tmp_path) as url, socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW) as sock:
        sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER, struct.pack("=II", 0xAA, socket.CAN_SFF_MASK | socket.CAN_EFF_FLAG | socket.CAN_RTR_FLAG))
        sock.setsockopt(socket.SOL_SOCKET, TIMESTAMP_NS, 1)
        sock.settimeout(2)
        sock.bind((vcan,))
        page = browser.new_page()
        page.goto(url)
        page.locator("#profile").select_option(label="wheel_speed_demo")
        expect(page.locator("#draft")).to_have_value(re.compile(r".*restbus:", re.S))
        page.locator("#interface").select_option(vcan)
        page.locator("#start").click()
        expect(page.locator("#state")).to_have_text("Running")
        try:
            assert sample(sock)[1] == ZERO
            samples = [sample(sock) for _ in range(101)]
            assert all(data == ZERO for _, data in samples)
            intervals = [(second[0] - first[0]) / 10**6 for first, second in pairwise(samples)]
            median = statistics.median(intervals)
            p95 = statistics.quantiles(intervals, n=100, method="inclusive")[94]
            print(f"{vcan} wheel cadence (100 intervals): median={median:.3f} p95={p95:.3f} max={max(intervals):.3f} ms")
            assert 18 <= median <= 22

            sim = page.locator("#sim-tab")
            live = page.locator("#live-tab")
            live.click()
            page.locator("#capture-interface").select_option(vcan)
            page.locator("#capture-toggle").click()
            expect(page.locator("#wheel-state")).to_have_text("Live")
            wait_for_sample(sock, FORTY)
            for wheel in ("FR", "FL", "RR", "RL"):
                expect(page.locator("#wheel-" + wheel)).to_have_text(re.compile(r"^40(?:\.0+)?$"))
            expect(page.locator("#wheel-raw")).to_contain_text("2A 0F 2A 0F 2A 0F 2A 0F")
            sim.click()
            page.locator("#endpoint").select_option("engine.obd_physical")
            send(page, "01 0D")
            expect(page.locator("#exchanges details:first-child .raw")).to_have_text("RX  41 0D 28")
            page.locator("#broadcast-toggle").click()
            live.click()
            expect(page.locator("#wheel-state")).to_have_text("Stale")
            assert_silent(sock)
            sim.click()
            send(page, "3E 00")
            expect(page.locator("#exchanges details:first-child .raw")).to_have_text("RX  7E 00")
            page.locator("#broadcast-toggle").click()
            wait_for_sample(sock, FORTY)
            live.click()
            expect(page.locator("#wheel-state")).to_have_text("Live")
            wait_for_sample(sock, ZERO)
            expect(page.locator("#wheel-FR")).to_have_text(re.compile(r"^0(?:\.0+)?$"))
        finally:
            page.locator("#sim-tab").click()
            stop(page)
        assert_silent(sock)
        page.close()
