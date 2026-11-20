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
        page.get_by_role("button", name="Tester Present", exact=True).click()
        expect(page.locator("#payload")).to_have_value("3E 00")
        page.get_by_role("button", name="Read DTCs").click()
        expect(page.locator("#payload")).to_have_value("19 02 FF")
        expect(page.locator("#exchanges details")).to_have_count(0)
        page.locator("#send").click()
        expect(page.locator("#exchanges details:first-child .raw")).to_have_text("RX  59 02 8C 00 01 01 08")
        expect(page.locator("#exchanges details:first-child .records li")).to_have_text(["P0001 · Confirmed · failure type 01 · status 08"])
        page.get_by_role("button", name="Tester Present · suppress positive response").click()
        expect(page.locator("#payload")).to_have_value("3E 80")
        page.locator("#send").click()
        expect(page.locator("#exchanges details")).to_have_count(2)
        first_read = page.locator("#exchanges details").last
        first_read.locator("summary").click()
        expect(first_read.locator(".records")).to_contain_text("P0001 · Confirmed")
        expect(first_read.locator(".raw")).to_be_visible()
        stop(page)
        expect(page.locator("#draft")).to_have_value(single)
        assert not list(tmp_path.rglob("*.yaml"))
        page.locator("#draft").fill(draft(base, two=True))
        start(page)
        with socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW) as capture:
            capture.bind((vcan,))
            send(page)
            expect(page.locator("#exchanges details:first-child .raw")).to_have_text("RX  59 02 8C 00 01 01 08 94 77 01 04")
            verify_frames(capture)
        expect(page.locator("#exchanges details:first-child .records li")).to_have_text([
            "P0001 · Confirmed · failure type 01 · status 08", "B1477 · Pending · failure type 01 · status 04"])
        send(page, "3E 80")
        expect(page.locator("#exchanges details:first-child .outcome")).to_have_text("No response received · positive response suppression requested.")
        expect(page.locator("#exchanges details")).to_have_count(2)
        earlier = page.locator("#exchanges details").last
        earlier.locator("summary").click()
        expect(earlier.locator(".raw")).to_have_text("RX  59 02 8C 00 01 01 08 94 77 01 04")
        expect(earlier.locator(".records li")).to_have_count(2)
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
        expect(page.locator("#exchanges details:first-child .raw")).to_have_text("RX  59 02 8C 00 01 01 08 94 77 01 04")
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
        expect(page.locator("#exchanges details:first-child .outcome")).to_have_text("No response received · positive response suppression requested.")
        send(stale)
        expect(stale.locator("#exchanges details:first-child .raw")).to_contain_text("59 02 8C")
        stop(page)
        faults = "    faults:\n      - {effect: nrc, sid: 0x3E, nrc: 0x22, addressing: physical, occurrences: [1]}\n"
        page.locator("#draft").fill(base.replace("    endpoints:", faults + "    endpoints:").replace("interface: vcan0", f"interface: {vcan}"))
        start(page)
        send(page, "3E 80")
        expect(page.locator("#exchanges details:first-child .raw")).to_have_text("RX  7F 3E 22")
        expect(page.locator("#exchanges details:first-child .outcome")).to_have_text("Negative response · NRC 22")
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
        expect(page.locator("#exchanges details:first-child .raw")).to_have_text("RX  59 02 8C 00 01 01 08 94 77 01 04")
        stop(page)


def test_live_can_trace_and_groups_match_independent_capture(browser, vcan, tmp_path):
    """Read-only capture spans ECU lifetime and preserves kernel frame order."""
    with server(tmp_path) as url:
        page = browser.new_page()
        page.goto(url)
        expect(page.locator("#state")).to_have_text("Stopped")
        page.get_by_role("button", name="Live CAN", exact=True).click(timeout=2000)
        page.get_by_label("Capture interface").select_option(vcan)
        page.get_by_role("button", name="Start capture").click()
        expect(page.locator("#capture-status")).to_contain_text("Capturing")
        with socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW) as raw:
            raw.bind((vcan,))
            def inject(value):
                raw.send(struct.pack("=IB3x8s", 0x123, 2, bytes([0xA0, value])))
            inject(1)
            expect(page.locator("#frame-rows")).to_contain_text("A0 01")
            page.get_by_role("button", name="Pause display").click()
            inject(2)
            page.wait_for_timeout(250)
            expect(page.locator("#frame-rows")).not_to_contain_text("A0 02")
            page.get_by_role("button", name="Resume display").click()
            page.get_by_role("button", name="By CAN ID").click()
            expect(page.get_by_role("columnheader", name="Latest signal / service", exact=True)).to_be_visible()
            expect(page.get_by_role("columnheader", name="Latest value")).to_be_visible()
            expect(page.locator("#byte-legend")).to_be_visible()
            expect(page.locator("#frame-rows tr")).to_have_count(1)
            expect(page.locator("#frame-rows td").nth(1)).to_have_text("2")
            expect(page.locator("#frame-rows mark")).to_have_text("02")
            page.get_by_role("button", name="Simulator", exact=True).click()
            page.locator("#draft").fill(draft(default_profile_path().read_text(), two=True))
            start(page)
            send(page)
            expect(page.locator("#exchanges details:first-child .raw")).to_have_text("RX  59 02 8C 00 01 01 08 94 77 01 04")
            expect(page.locator("#exchanges details:first-child .records li")).to_have_count(2)
            raw.settimeout(2)
            wire = []
            while len(wire) < 4:
                can_id, length, payload = struct.unpack("=IB3x8s", raw.recv(16))
                if can_id in (0x7E1, 0x7E9):
                    wire.append((f"{can_id:03X}", str(length), payload[:length].hex(" ").upper()))
            assert [data[:2] for _, _, data in wire] == ["03", "10", "30", "21"]
            page.get_by_role("button", name="Live CAN", exact=True).click()
            page.get_by_role("button", name="Trace", exact=True).click()
            expect(page.locator("#byte-legend")).to_be_hidden()
            expect(page.get_by_role("columnheader", name="Signal / service", exact=True)).to_be_visible()
            expect(page.locator("#frame-rows tr")).to_have_count(6)
            rows = page.locator("#frame-rows tr").all()
            assert [(cells[1], cells[-2], cells[-1]) for cells in
                    (row.locator("td").all_text_contents() for row in rows[2:])] == wire
            expect(page.locator("#frame-rows .frame-type")).to_have_text(
                ["Single frame", "First frame", "Flow control", "Consecutive frame"])
            services = page.locator("#frame-rows tr td:nth-child(4)")
            expect(services).to_have_text(["—", "—", "Read DTCs by status mask", "—", "—", "—"])
            badges = page.locator("#frame-rows .frame-type")
            expect(badges.nth(2)).to_have_css("background-color", "rgb(238, 230, 250)")
            for index in (0, 1, 3):
                expect(badges.nth(index)).to_have_css("background-color", "rgba(0, 0, 0, 0)")
            page.get_by_role("button", name="By CAN ID").click()
            expect(page.locator("#frame-rows .frame-type")).to_have_text(["Flow control", "Consecutive frame"])
            page.get_by_role("button", name="Trace", exact=True).click()
            page.get_by_role("button", name="Simulator", exact=True).click()
            send(page, "3E 00")
            expect(page.locator("#exchanges details:first-child .raw")).to_have_text("RX  7E 00")
            send(page, "3E 80")
            expect(page.locator("#exchanges details:first-child .outcome")).to_contain_text("suppression requested")
            stop(page)
            page.get_by_role("button", name="Live CAN", exact=True).click()
            expect(services).to_have_text([
                "—", "—", "Read DTCs by status mask", "—", "—", "—", "Tester Present",
                "Tester Present · positive response", "Tester Present · suppression requested"])
            page.get_by_role("button", name="By CAN ID").click()
            expect(services).to_have_text(["—", "Tester Present · suppression requested", "Tester Present · positive response"])
            page.get_by_role("button", name="Trace", exact=True).click()
            for frame_id, data in [(0x7DF, "02 01 0D"), (0x7E8, "03 41 0D 00"),
                                   (0x7DF, "02 01 10"), (0x7E8, "04 41 10 01 5E")]:
                raw.send(struct.pack("=IB3x8s", frame_id, 8, bytes.fromhex(data)))
            expect(page.locator("#frame-rows .request .frame-type")).to_have_text(["Request", "Request"])
            expect(page.locator("#frame-rows tr.request").first).to_have_css("display", "table-row")
            expect(page.locator("#frame-rows .reply strong")).to_have_text(["0 km/h", "3.50 g/s"])
            expect(page.locator("#frame-rows tr").last.locator(".padding-byte")).to_have_count(3)
            raw.send(struct.pack("=IB3x8s", 0x7E8, 8, bytes.fromhex("03 41 0D 2A")))
            expect(page.locator("#frame-rows .reply mark")).to_have_text("42 km/h")
            page.get_by_label("Filter CAN ID").fill("0x7E9")
            expect(page.locator("#frame-rows tr")).to_have_count(3)
            page.get_by_role("button", name="Stop capture").click()
            expect(page.locator("#capture-status")).to_contain_text("Stopped")
        page.close()
