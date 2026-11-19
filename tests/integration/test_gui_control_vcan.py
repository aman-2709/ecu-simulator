"""Operator acceptance, including an independent Classical CAN frame capture."""

import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

import pytest

from ecu_simulator.cli import default_profile_path

pytest.importorskip("aiohttp")
playwright = pytest.importorskip("playwright.sync_api", reason="browser acceptance requires Playwright")
expect = playwright.expect


@contextmanager
def server(tmp_path):
    with socket.socket() as port:
        port.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{port.getsockname()[1]}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "ecu_simulator.gui.server", "--port", url.rsplit(":", 1)[1]],
        env={**os.environ, "XDG_CONFIG_HOME": str(tmp_path)},
    )
    try:
        until = time.monotonic() + 8
        while True:
            try:
                with urllib.request.urlopen(url + "/run", timeout=1):
                    break
            except urllib.error.URLError:
                if proc.poll() is not None or time.monotonic() >= until:
                    raise
                time.sleep(.05)
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=8)


@pytest.fixture
def browser():
    with playwright.sync_playwright() as engine:
        chrome = os.environ.get("ECU_GUI_CHROME") or shutil.which("google-chrome") or shutil.which("chromium")
        instance = engine.chromium.launch(executable_path=chrome)
        yield instance
        instance.close()


def start(page):
    page.locator("#start").click()
    expect(page.locator("#state")).to_have_text("Running")
    page.locator("#endpoint").select_option("engine.uds_physical")


def stop(page):
    page.locator("#stop").click()
    expect(page.locator("#state")).to_have_text("Stopped")


def send(page, payload="19 02 FF"):
    page.locator("#payload").fill(payload)
    page.locator("#send").click()


def draft(base, two=False):
    entries = "      - {code: P0001, pending: false, confirmed: true, indicator_requested: false}"
    if two:
        entries += "\n      - {code: B1477, pending: true, confirmed: false, indicator_requested: false}"
    return base.replace("      - B1477\n      - P0001", entries)


def verify_frames(capture):
    expected = [(0x7E9, bytes.fromhex("10 0B 59 02 8C 00 01 01")),
                (0x7E1, bytes.fromhex("30")), (0x7E9, bytes.fromhex("21 08 94 77 01 04"))]
    capture.settimeout(2)
    for can_id, prefix in expected:
        while True:
            frame_id, size, data = struct.unpack("=IB3x8s", capture.recv(16))
            if frame_id == can_id and data[:size].startswith(prefix):
                break


def test_gui_control_vcan_profile_wire_and_persistence(browser, vcan, tmp_path):
    page = browser.new_page()
    shipped = default_profile_path().read_bytes()
    with server(tmp_path) as url:
        page.goto(url)
        expect(page.locator("#draft")).to_have_value(shipped.decode())
        page.locator("#interface").select_option(vcan)
        expect(page.locator("#draft")).to_have_value(shipped.decode().replace("interface: vcan0", f'interface: "{vcan}"'))
        base = page.locator("#draft").input_value()
        single = draft(base)
        page.locator("#draft").fill(single)
        start(page)
        send(page)
        expect(page.locator("#raw")).to_have_text("RX  59 02 8C 00 01 01 08")
        expect(page.locator("#records li")).to_have_text(["P0001 · Confirmed · failure type 01 · status 08"])
        stop(page)
        expect(page.locator("#draft")).to_have_value(single)
        assert not list(tmp_path.rglob("*.yaml"))
        page.locator("#draft").fill(draft(base, two=True))
        start(page)
        with socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW) as capture:
            capture.bind((vcan,))
            send(page)
            expect(page.locator("#raw")).to_have_text("RX  59 02 8C 00 01 01 08 94 77 01 04")
            verify_frames(capture)
        expect(page.locator("#records li")).to_have_text([
            "P0001 · Confirmed · failure type 01 · status 08", "B1477 · Pending · failure type 01 · status 04"])
        send(page, "3E 80")
        expect(page.locator("#decoded")).to_have_text("No response received · positive response suppression requested.")
        stop(page)
        page.locator("#name").fill("Two faults")
        page.get_by_role("button", name="Save as").click()
        expect(page.locator("#notice")).to_have_text("Saved Two faults")
    with server(tmp_path) as url:
        page.goto(url)
        page.locator("#profile").select_option(label="Two faults")
        expect(page.locator("#draft")).to_have_value(draft(base, two=True))
        start(page)
        send(page)
        expect(page.locator("#raw")).to_have_text("RX  59 02 8C 00 01 01 08 94 77 01 04")
        stop(page)
    assert default_profile_path().read_bytes() == shipped


def test_gui_control_vcan_retry_stale_tabs_and_negative(browser, vcan, tmp_path):
    with server(tmp_path) as url:
        page = browser.new_page()
        page.goto(url)
        expect(page.locator("#draft")).to_have_value(default_profile_path().read_text())
        page.locator("#draft").fill("[")
        page.locator("#start").click()
        expect(page.locator("#error")).to_contain_text("valid YAML")
        expect(page.locator("#draft")).to_have_value("[")
        base = default_profile_path().read_text()
        bad = base.replace("interface: vcan0", "interface: missing_can")
        page.locator("#draft").fill(bad)
        page.locator("#start").click()
        expect(page.locator("#state")).to_have_text("Failed")
        expect(page.locator("#run-error")).to_contain_text("missing_can")
        expect(page.locator("#draft")).to_have_value(bad)
        page.locator("#draft").fill("[")
        page.locator("#start").click()
        expect(page.locator("#error")).to_contain_text("valid YAML")
        expect(page.locator("#run-error")).to_contain_text("missing_can")
        page.locator("#draft").fill(base.replace("interface: vcan0", f"interface: {vcan}"))
        start(page)
        stale = browser.new_page()
        stale.goto(url)
        expect(stale.locator("#state")).to_have_text("Running")
        # Freeze only this tab's status view. Its Send and Stop still hit the real manager.
        active = stale.evaluate("async () => (await fetch('/run')).json()")
        stale.route("**/run", lambda route: route.fulfill(json=active))
        stop(page)
        start(page)
        send(stale)
        expect(stale.locator("#error")).to_contain_text("stale")
        stale.locator("#stop").click()
        expect(stale.locator("#error")).to_contain_text("stale")
        stale.unroute("**/run")
        stale.reload()
        expect(stale.locator("#state")).to_have_text("Running")
        send(page, "3E 80")
        send(stale)
        expect(stale.locator("#error")).to_contain_text("in progress")
        expect(page.locator("#decoded")).to_have_text("No response received · positive response suppression requested.")
        send(stale)
        expect(stale.locator("#raw")).to_contain_text("59 02 8C")
        stop(page)
        faults = "    faults:\n      - {effect: nrc, sid: 0x3E, nrc: 0x22, addressing: physical, occurrences: [1]}\n"
        page.locator("#draft").fill(base.replace("    endpoints:", faults + "    endpoints:").replace("interface: vcan0", f"interface: {vcan}"))
        start(page)
        send(page, "3E 80")
        expect(page.locator("#raw")).to_have_text("RX  7F 3E 22")
        expect(page.locator("#decoded")).to_have_text("Negative response · NRC 22")
        stop(page)


def test_gui_control_vcan_reply_via_returns_complete_payload(browser, vcan, tmp_path):
    base = default_profile_path().read_text().replace("interface: vcan0", f"interface: {vcan}")
    # The request arrives on 7E1. The response uses 7E8 and expects flow control on 7E0.
    text = draft(base, two=True).replace("      - name: uds_physical", "      - name: uds_physical\n        reply_via: obd_physical")
    with server(tmp_path) as url:
        page = browser.new_page()
        page.goto(url)
        expect(page.locator("#draft")).to_have_value(default_profile_path().read_text())
        page.locator("#draft").fill(text)
        start(page)
        send(page)
        expect(page.locator("#raw")).to_have_text("RX  59 02 8C 00 01 01 08 94 77 01 04")
        stop(page)
