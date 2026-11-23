"""The Car panel in a real browser: choose a car, check it, play it on vcan and watch it on the bus.

The counters and checksums the live panel shows are compared with an independent raw-socket capture, decoded here with
a bit reader of the test's own; the message is found from the catalog's DBC file, parsed here too.
"""

import json
import re
import socket
import struct
import subprocess
import threading
import time
from contextlib import contextmanager
from itertools import pairwise
from pathlib import Path

from ecu_simulator.car.catalog import load_catalog
from tests.integration.test_gui_control_vcan import browser, expect, server  # noqa: F401

CATALOG_ROOT = Path(__file__).parents[2] / "src" / "ecu_simulator" / "_opendbc"
FORD = Path(__file__).parents[1] / "fixtures" / "dbc" / "ford_fusion_2018_pt.dbc"
TIMESTAMP_NS = getattr(socket, "SO_TIMESTAMPNS", 35)
MEASURED = re.compile(r"^(\d+\.\d{2}) ms\s*min (\d+\.\d{2}) / max (\d+\.\d{2}) ms$")
MARKUP = "<img src=x onerror=window.pwned=1>"
SMALL_DBC = f"""VERSION ""
BS_:
BU_: ECU
BO_ 512 Gearbox: 8 ECU
 SG_ CHECKSUM : 0|8@1+ (1,0) [0|255] "" ECU
 SG_ COUNTER : 8|4@1+ (1,0) [0|15] "" ECU
 SG_ Gear : 16|4@1+ (1,0) [0|15] "{MARKUP}" ECU
""".encode()
RULE_FILE = b"""version: 1
rules:
  - messages: [Gearbox]
    counter: {signal: COUNTER}
    checksum: {signal: CHECKSUM, algorithm: crc8, poly: 0x1D, init: 0xFF, xorout: 0xFF, covers: [1, 2, 3, 4, 5, 6, 7]}
"""


# --- the test's own DBC reading and bit decoding (independent of the production code) ------------------------------


def dbc_layouts(text: str) -> dict[str, dict]:
    """Message name -> {"id", "signals": {name: (start, length, little_endian)}} from the DBC's BO_/SG_ lines."""
    messages: dict[str, dict] = {}
    current = None
    for line in text.splitlines():
        if match := re.match(r"BO_ (\d+) (\w+)\s*:", line):
            current = {"id": int(match[1]), "signals": {}}
            messages[match[2]] = current
        elif current is not None and (match := re.match(r"\s+SG_ (\w+)\s*(?:M|m\d+)?\s*:\s*(\d+)\|(\d+)@([01])", line)):
            current["signals"][match[1]] = (int(match[2]), int(match[3]), match[4] == "1")
        elif not line.strip():
            current = None
    return messages


def read_bits(data: bytes, start: int, length: int, little_endian: bool) -> int:
    def bit(position: int) -> int:
        return (data[position // 8] >> (position % 8)) & 1

    if little_endian:
        return sum(bit(start + i) << i for i in range(length))
    value, position = 0, start  # Motorola: start is the most significant bit, counted in the DBC's sawtooth order
    for _ in range(length):
        value = (value << 1) | bit(position)
        position = position + 15 if position % 8 == 0 else position - 1
    return value


def toyota_checksum(can_id: int, data: bytes) -> int:
    """opendbc's Toyota rule, written out: identifier bytes, length and every byte but the last, modulo 256."""
    total, address = len(data), can_id
    while address:
        total += address & 0xFF
        address >>= 8
    return (total + sum(data[:-1])) & 0xFF


def corolla_layouts() -> dict[str, dict]:
    catalog = load_catalog()
    entry = catalog.dbcs[catalog.platforms["TOYOTA_COROLLA"].buses["pt"]]
    return dbc_layouts((CATALOG_ROOT / entry.file).read_text())


class Listener:
    """Drain a raw CAN socket on a thread, so the kernel's receive buffer never drops the frames the test wants."""

    def __init__(self, interface: str, can_id: int) -> None:
        self.sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self.sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER, struct.pack("=II", can_id, socket.CAN_SFF_MASK))
        self.sock.setsockopt(socket.SOL_SOCKET, TIMESTAMP_NS, 1)
        self.sock.bind((interface,))
        self.sock.settimeout(0.05)
        self.frames: list[bytes] = []
        self.times: list[float] = []  # the kernel's receive timestamp of each frame, in seconds since the epoch
        self.running = True
        self.thread = threading.Thread(target=self._drain, daemon=True)
        self.thread.start()

    def _drain(self) -> None:
        while self.running:
            try:
                raw, ancillary, _, _ = self.sock.recvmsg(16, socket.CMSG_SPACE(16))
            except TimeoutError:
                continue
            stamp = next((struct.unpack("@ll", data[:16]) for level, kind, data in ancillary
                          if level == socket.SOL_SOCKET and kind == TIMESTAMP_NS), None)
            _, length, payload = struct.unpack("=IB3x8s", raw)
            self.frames.append(payload[:length])
            self.times.append(stamp[0] + stamp[1] / 1e9 if stamp else time.time())

    def close(self) -> None:
        self.running = False
        self.thread.join(2)
        self.sock.close()


@contextmanager
def extra_vcan(name: str):
    """A second vcan inside the private namespace the integration runner creates, removed afterwards."""
    subprocess.run(["ip", "link", "add", "dev", name, "type", "vcan"], check=True)
    subprocess.run(["ip", "link", "set", "up", name], check=True)
    try:
        yield name
    finally:
        subprocess.run(["ip", "link", "del", name], check=False)


# --- page helpers ------------------------------------------------------------------------------------------------------


def choose_corolla(page) -> None:
    expect(page.locator("#draft")).not_to_have_value("")
    page.locator("#car-brand").select_option("toyota")
    page.locator("#car-platform").select_option("TOYOTA_COROLLA")
    expect(page.locator("#car-step-1")).to_have_attribute("data-state", "done")
    expect(page.locator("#car-chips [data-state=valid]")).to_have_text(re.compile(r"^[1-9]\d* valid$"))
    expect(page.locator("#car-step-3")).to_have_attribute("data-state", "active")
    more = page.locator("#car-table-more")
    expect(more).to_have_text("Show all 58 messages")
    expect(page.locator("#car-table tbody tr")).to_have_count(25)
    more.click()
    expect(page.locator("#car-table tbody tr")).to_have_count(58)


def play(page) -> None:
    page.locator("#car-play").click()
    expect(page.locator("#state")).to_have_text("Running", timeout=15000)


def stop(page) -> None:
    page.locator("#car-stop").click()
    expect(page.locator("#state")).to_have_text("Stopped")


# --- tests -------------------------------------------------------------------------------------------------------------


def test_live_panel_is_idle_before_play(browser, vcan, tmp_path):  # noqa: F811
    with server(tmp_path) as url:
        page = browser.new_page()
        page.goto(url)
        live = page.locator("#car-live")
        expect(live).to_have_class(re.compile(r"\bidle\b"))
        expect(live).to_contain_text("Starts when you press Play")
        expect(page.locator("#car-live-pause")).to_be_disabled()
        expect(page.locator("#car-live-rows tr")).to_have_count(0)
        expect(page.locator("#car-step-1")).to_have_attribute("data-state", "active")
        expect(page.locator("#car-step-1")).to_have_attribute("aria-current", "step")
        for step in ("#car-step-2", "#car-step-3"):
            expect(page.locator(step)).to_have_attribute("data-state", "todo")
            expect(page.locator(step)).not_to_have_attribute("aria-current", "step")
        expect(page.locator("#car-play")).to_be_disabled()
        expect(page.locator("#car-banner")).to_be_empty()
        page.close()


def test_walk_three_steps_on_vcan(browser, vcan, tmp_path):  # noqa: F811
    layouts = corolla_layouts()
    stamped = sorted(name for name, m in layouts.items() if {"COUNTER", "CHECKSUM"} <= m["signals"].keys())
    with server(tmp_path) as url:
        page = browser.new_page()
        page.goto(url)
        choose_corolla(page)
        table = page.locator("#car-table")
        valid = [name for name in stamped
                 if table.locator(f'tr[data-message="{name}"] .integrity').inner_text().startswith("valid")]
        assert valid, f"none of {stamped} is valid in the table"
        name = valid[0]
        layout = layouts[name]
        expect(page.locator("#car-identity")).to_contain_text(re.compile("[0-9a-f]{64}"))
        expect(page.locator("#car-identity")).to_contain_text(load_catalog().revision)
        expect(page.locator("#car-interface")).to_have_value(page.locator("#interface").input_value())
        expect(page.locator("#car-bus-load")).to_contain_text("%")
        listener = Listener(vcan, layout["id"])
        try:
            play(page)
            expect(page.locator("#car-step-1")).to_have_attribute("data-state", "done")
            expect(page.locator("#car-step-2")).to_have_attribute("data-state", "done")
            expect(page.locator("#car-step-3")).to_have_attribute("aria-current", "step")
            expect(page.locator("#car-step-3")).to_contain_text(f"Running on {vcan}")
            expect(page.locator("#car-step-3")).to_contain_text("generated drive")
            expect(page.locator("#car-live")).not_to_have_class(re.compile(r"\bidle\b"))
            row = page.locator(f'#car-live-rows tr[data-message="{name}"]')
            expect(row.locator(".counter")).to_have_text(re.compile(r"^\d+$"))
            expect(row.locator(".checksum")).to_have_text(re.compile(r"^0x[0-9A-F]{2}$"))
            expect(row.locator(".measured-period")).to_have_text(MEASURED, timeout=10000)
            expect(row.locator(".set-period")).to_have_text(re.compile(r"^\d+ ms"))
            page.locator("#car-live-pause").click()
            expect(page.locator("#car-live-pause")).to_have_attribute("aria-pressed", "true")
            counter = int(row.locator(".counter").inner_text())
            checksum = int(row.locator(".checksum").inner_text(), 16)
            time.sleep(0.3)
        finally:
            listener.close()
        frames = list(listener.frames)
        assert frames, f"the raw capture saw no 0x{layout['id']:X} frame"
        for data in frames:
            assert read_bits(data, *layout["signals"]["CHECKSUM"]) == toyota_checksum(layout["id"], data)
        seen = {(read_bits(d, *layout["signals"]["COUNTER"]), read_bits(d, *layout["signals"]["CHECKSUM"]))
                for d in frames}
        assert (counter, checksum) in seen
        page.locator("#car-live-filter").fill(f"{layout['id']:X}")
        expect(page.locator("#car-live-rows tr:visible")).to_have_count(1)
        stop(page)
        expect(page.locator("#car-banner")).to_be_empty()
        page.close()


def test_period_edit_marks_override(browser, vcan, tmp_path):  # noqa: F811
    with server(tmp_path) as url:
        page = browser.new_page()
        page.goto(url)
        choose_corolla(page)
        row = page.locator('#car-table tr[data-message="STEERING_LKA"]')
        expect(row.locator(".period-source")).not_to_have_text("set by you")
        row.get_by_role("button", name="Edit period of STEERING_LKA").click()
        row.get_by_label("Period of STEERING_LKA in ms").fill("50")
        row.get_by_role("button", name="Save").click()
        expect(row.locator(".period")).to_contain_text("50 ms")
        expect(row.locator(".period-source")).to_have_text("set by you")
        expect(page.locator("#draft")).to_have_value(re.compile(r"periods:\s*\n\s+STEERING_LKA: 50"))
        page.close()


def test_banner_names_cause_when_interface_goes_down(browser, vcan, tmp_path):  # noqa: F811
    with extra_vcan("vcancar0") as name, server(tmp_path) as url:
        page = browser.new_page()
        page.goto(url)
        choose_corolla(page)
        page.locator("#car-interface").select_option(name)
        expect(page.locator("#interface")).to_have_value(name)
        expect(page.locator("#draft")).to_have_value(re.compile(f"interface: \"?{name}"))
        expect(page.locator("#car-bus-load")).to_contain_text(name)
        play(page)
        expect(page.locator("#car-live-rows tr").first).to_be_visible()
        subprocess.run(["ip", "link", "set", "down", name], check=True)
        banner = page.locator("#car-banner")
        expect(banner).to_contain_text("Send failed", timeout=10000)
        expect(banner).to_contain_text("Network is down")
        expect(banner).to_contain_text(f"ip link set up {name}")
        expect(page.locator("#state")).not_to_have_text("Running")
        expect(page.locator("#state")).to_have_text("Failed")
        assert page.evaluate("document.getElementById('car-banner').contains(document.activeElement)")
        expect(page.locator("#car-play")).to_be_enabled()
        page.close()


def test_recorded_source_disables_integrity_column(browser, vcan, tmp_path):  # noqa: F811
    layout = corolla_layouts()["STEERING_LKA"]
    frame = bytes([0b0010_1000, 0x00, 0x00, 0x00, 0x00])  # COUNTER 20 in bits 6..1 of byte 0
    frame = frame[:4] + bytes([toyota_checksum(layout["id"], frame)])
    log = "".join(f"({1000 + i * 0.05:.3f}) vcan0 {layout['id']:03X}#{frame.hex().upper()}\n" for i in range(6))
    with server(tmp_path) as url:
        page = browser.new_page()
        page.goto(url)
        choose_corolla(page)
        expect(page.locator("#car-source-recorded")).to_be_hidden()  # step 1 is collapsed to its summary
        page.get_by_role("button", name="Change car or source").click()
        page.locator("#car-source-recorded").check()
        expect(page.locator("#car-log-file")).to_be_visible()
        expect(page.locator("#car-timing-log")).to_be_hidden()
        cells = page.locator("#car-table tbody .integrity")
        expect(cells.first).to_have_text("as recorded")
        assert set(cells.all_inner_texts()) == {"as recorded"}
        edits = page.locator("#car-table tbody button")
        assert edits.count() > 0 and all(edits.nth(i).is_disabled() for i in range(edits.count()))
        expect(page.locator("#car-interface")).to_have_value("vcan0")
        expect(page.locator("#car-interface")).to_be_disabled()
        expect(page.locator("#car-play")).to_be_disabled()
        page.locator("#car-log-file").set_input_files({"name": "lka.log", "mimeType": "text/plain",
                                                       "buffer": log.encode()})
        page.locator("#car-play").click()
        row = page.locator('#car-live-rows tr[data-message="STEERING_LKA"]')
        expect(row.locator(".counter")).to_have_text(str(read_bits(frame, *layout["signals"]["COUNTER"])))
        expect(row.locator(".checksum")).to_have_text(f"0x{frame[4]:02X}")
        expect(row.locator(".integrity")).to_have_text("as recorded")
        expect(page.locator("#car-step-3")).to_contain_text("recorded log")
        expect(page.locator("#state")).to_have_text("Stopped")  # a replay is not an ECU run
        page.close()


def test_manual_dbc_panel_still_present(browser, vcan, tmp_path):  # noqa: F811
    with server(tmp_path) as url:
        page = browser.new_page()
        page.goto(url)
        expect(page.locator("#draft")).not_to_have_value("")
        expect(page.locator("#dbc-heading")).to_be_visible()
        before = page.evaluate("""() => Boolean(document.getElementById('car-panel').compareDocumentPosition(
            document.getElementById('dbc-heading')) & Node.DOCUMENT_POSITION_FOLLOWING)""")
        assert before, "the Car panel sits above the DBC messages panel"
        page.locator("#dbc-file").set_input_files(FORD)
        page.locator("#dbc-message").select_option("EPAS_INFO")
        page.locator('[data-signal="SteMdule_U_Meas"]').fill("12")
        expect(page.locator("#dbc-raw")).to_have_text("80 00 14 00 78 00 00 00")
        page.locator("#dbc-apply").click()
        expect(page.locator("#dbc-configured")).to_contain_text("EPAS_INFO")
        page.close()


def test_upload_a_dbc_and_a_rule_file_then_check(browser, vcan, tmp_path):  # noqa: F811
    with server(tmp_path) as url:
        page = browser.new_page()
        page.goto(url)
        expect(page.locator("#draft")).not_to_have_value("")
        page.locator("#car-upload").set_input_files({"name": "gearbox.dbc", "mimeType": "text/plain",
                                                     "buffer": SMALL_DBC})
        expect(page.locator("#car-step-1")).to_have_attribute("data-state", "done")
        row = page.locator('#car-table tr[data-message="Gearbox"]')
        expect(row.locator(".integrity")).to_have_text(re.compile("^counter only"))
        expect(page.locator("#draft")).to_have_value(re.compile("filename: gearbox.dbc"))
        page.locator("#car-rule-file").set_input_files({"name": "gearbox.yaml", "mimeType": "text/plain",
                                                        "buffer": RULE_FILE})
        expect(row.locator(".integrity")).to_contain_text("valid (user rule)")
        expect(row.locator(".integrity")).to_contain_text("computed as the supplied rule says")
        expect(page.locator("#car-chips [data-state=user_rule]")).to_have_text("1 valid (user rule)")
        expect(page.locator("#draft")).to_have_value(re.compile("rule_file: [0-9a-f]{64}"))
        page.close()


def test_banner_shows_bus_load_refusal_without_starting(browser, vcan, tmp_path):  # noqa: F811
    """A physical interface cannot exist in the private namespace, so the refusal comes from a stubbed /car/check that
    returns the real response with the backend's refused bus load; the refusal path in the page is the real one."""
    refusal = "bus load 112.0% of 500 kbit/s on can0 is over the 70% ceiling"

    def refuse(route):
        body = route.fetch().json()
        body["bus_load"].update(refused=refusal, over=[{"name": "STEERING_LKA", "share": 0.4}], estimate=1.12)
        route.fulfill(json=body)

    with server(tmp_path) as url:
        page = browser.new_page()
        posts = []
        page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and r.url.endswith("/run") else None)
        page.goto(url)
        page.route("**/car/check", refuse)
        expect(page.locator("#draft")).not_to_have_value("")
        page.locator("#car-brand").select_option("toyota")
        page.locator("#car-platform").select_option("TOYOTA_COROLLA")
        banner = page.locator("#car-banner")
        expect(banner).to_contain_text(refusal)
        expect(banner).to_contain_text("STEERING_LKA")
        expect(page.locator("#car-bus-load")).to_contain_text(refusal)
        page.locator("#car-play").click()
        expect(banner).to_contain_text(refusal)
        page.wait_for_timeout(500)
        assert posts == []
        expect(page.locator("#state")).to_have_text("Stopped")
        page.unroute("**/car/check")
        play(page)
        expect(banner).to_be_empty()
        stop(page)
        page.close()


def test_no_console_errors_during_the_walk(browser, vcan, tmp_path):  # noqa: F811
    with server(tmp_path) as url:
        page = browser.new_page()
        errors = []
        page.on("console", lambda m: errors.append(f"{m.text} at {m.location.get('url')}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        page.goto(url)
        choose_corolla(page)
        row = page.locator('#car-table tr[data-message="STEERING_LKA"]')
        row.get_by_role("button", name="Edit period of STEERING_LKA").click()
        row.get_by_label("Period of STEERING_LKA in ms").fill("40")
        row.get_by_role("button", name="Save").click()
        expect(row.locator(".period-source")).to_have_text("set by you")
        page.locator("#car-rule").select_option("toyota")
        expect(page.locator("#car-chips [data-state=valid]")).to_have_text(re.compile(r"^[1-9]\d* valid$"))
        play(page)
        expect(page.locator("#car-live-rows tr").first).to_be_visible()
        expect(page.locator("#car-live-stats")).to_contain_text("tx dropped (kernel)", timeout=5000)
        page.locator("#car-live-pause").click()
        page.locator("#car-live-pause").click()
        stop(page)
        assert errors == []
        page.close()


def test_layout_has_no_horizontal_scroll_at_360px(browser, vcan, tmp_path):  # noqa: F811
    with server(tmp_path) as url:
        page = browser.new_page(viewport={"width": 360, "height": 800})
        page.goto(url)

        def overflow() -> int:
            return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")

        expect(page.locator("#car-panel")).to_be_visible()
        assert overflow() <= 0
        choose_corolla(page)
        assert overflow() <= 0
        play(page)
        expect(page.locator("#car-live-rows tr").first).to_be_visible()
        assert overflow() <= 0
        stop(page)
        page.close()


def test_untrusted_text_is_not_interpreted(browser, vcan, tmp_path):  # noqa: F811
    """Markup in a DBC's file name, in a signal unit and in a parse error the server echoes renders as text."""
    with server(tmp_path) as url:
        page = browser.new_page()
        dialogs = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        page.goto(url)
        expect(page.locator("#draft")).not_to_have_value("")
        broken = f'VERSION ""\nBO_ 512 Gear{MARKUP}: 8 ECU\n'.encode()
        page.locator("#car-upload").set_input_files({"name": "broken.dbc", "mimeType": "text/plain", "buffer": broken})
        expect(page.locator("#car-banner")).to_contain_text(MARKUP)
        page.locator("#car-upload").set_input_files({"name": f"{MARKUP}.dbc", "mimeType": "text/plain",
                                                     "buffer": SMALL_DBC})
        expect(page.locator("#car-step-1")).to_contain_text(f"{MARKUP}.dbc")
        expect(page.locator("#car-identity")).to_contain_text(f"{MARKUP}.dbc")
        expect(page.locator("#car-banner")).to_be_empty()
        play(page)
        row = page.locator('#car-live-rows tr[data-message="Gearbox"]')
        expect(row).to_contain_text(MARKUP)
        stop(page)
        assert page.locator("#car-panel img").count() == 0
        assert page.evaluate("window.pwned") is None
        assert dialogs == []
        page.close()


def test_measured_period_is_the_bus_median_with_its_spread(browser, vcan, tmp_path):  # noqa: F811
    """The busiest message's measured period is the median of the bus timestamps, with min and max, not a mean."""
    with server(tmp_path) as url:
        page = browser.new_page()
        page.goto(url)
        choose_corolla(page)
        periods = {row.get_attribute("data-message"): int(row.locator(".period-value").inner_text().split()[0])
                   for row in page.locator("#car-table tbody tr").all()
                   if row.locator("button").is_enabled()}
        name = min(periods, key=periods.get)
        layout = corolla_layouts()[name]
        header = page.get_by_role("columnheader", name=re.compile("Period measured on the bus"))
        expect(header).to_have_text("Period measured on the bus (median, min / max of the last 100 frames)")
        listener = Listener(vcan, layout["id"])
        try:
            play(page)
            cell = page.locator(f'#car-live-rows tr[data-message="{name}"] .measured-period')
            expect(cell).to_have_text(MEASURED, timeout=10000)
            page.wait_for_timeout(100 * periods[name] + 1500)
            page.locator("#car-live-pause").click()
            paused = time.time()
            shown = MEASURED.match(cell.inner_text())
            assert "median" in (cell.get_attribute("title") or "").lower()
            time.sleep(0.2)
        finally:
            listener.close()
        stamps = [t for t in listener.times if t <= paused][-101:]
        assert len(stamps) == 101, f"the raw capture saw {len(stamps)} frames of {name}"
        intervals = sorted((b - a) * 1000 for a, b in pairwise(stamps))
        raw_median = (intervals[49] + intervals[50]) / 2
        median, low, high = (float(value) for value in shown.groups())
        assert abs(median - raw_median) <= max(0.5, 0.05 * raw_median), (median, raw_median)
        assert low <= raw_median <= high, (low, raw_median, high)
        stop(page)
        page.close()


def test_measured_period_statistic_is_the_median_not_the_mean(browser, tmp_path):  # noqa: F811
    """A synthetic capture with known intervals: [12, 12, 12, 12, 25, 3, 12] ms has median 12 and mean 12.57."""
    message = {"name": "PROBE", "id": 0x123, "extended": False, "dlc": 1, "period_ms": 12, "period_source": "dbc",
               "clamped": False, "integrity": "none", "detail": None, "counters": [], "checksums": [],
               "skipped": None}
    car = {"platform": None, "dbc": {"filename": "probe.dbc", "sha256": "c" * 64}, "revision": None, "rule": None,
           "bus_load": {"estimate": 0.01, "bitrate": 500000, "assumed": True, "worst_case": True},
           "counts": {"valid": 0, "counter_only": 0, "user_rule": 0, "none": 1, "skipped": 0}}
    status = {"state": "Running", "id": "probe-run", "interface": "vcan0", "endpoints": [], "error": "",
              "restbus": None, "dbc": None, "car": car}
    times, now = [], 1_000_000_000
    for interval in (0, 12, 12, 12, 12, 25, 3, 12):
        now += interval * 1000
        times.append(now)
    frames = [{"time": t, "id": "123", "kind": "", "length": 1, "bytes": [0],
               "dbc": {"name": "PROBE", "signals": {"VALUE": {"value": 1, "unit": ""}}, "period_ms": None}}
              for t in times]
    body = '{"ready":true}\n' + json.dumps({"frames": frames, "dropped": 0}) + "\n"
    with server(tmp_path) as url:
        page = browser.new_page()
        page.route("**/run", lambda route: route.fulfill(json=status))
        page.route("**/car/run", lambda route: route.fulfill(json={**car, "messages": [message]}))
        page.route("**/capture", lambda route: route.fulfill(body=body, content_type="application/x-ndjson",
                                                             headers={"X-Capture-Id": "probe"}))
        page.route("**/car/link*", lambda route: route.fulfill(json={
            "interface": "vcan0", "virtual": True, "bitrate": None, "state": "UP", "bus_off": None,
            "bus_errors": None, "tx_dropped": 0}))
        page.goto(url)
        cell = page.locator('#car-live-rows tr[data-message="PROBE"] .measured-period')
        expect(cell).to_have_text(re.compile(r"^12\.00 ms"))
        expect(cell.locator(".measured-spread")).to_have_text("min 3.00 / max 25.00 ms")
        assert "12.57" not in cell.inner_text()
        page.close()


def test_measured_period_waits_for_five_intervals(browser, tmp_path):  # noqa: F811
    car = {"platform": None, "dbc": {"filename": "probe.dbc", "sha256": "c" * 64}, "revision": None, "rule": None,
           "bus_load": {"estimate": 0.01, "bitrate": 500000, "assumed": True, "worst_case": True},
           "counts": {"valid": 0, "counter_only": 0, "user_rule": 0, "none": 1, "skipped": 0}}
    message = {"name": "PROBE", "id": 0x123, "extended": False, "dlc": 1, "period_ms": 12, "period_source": "dbc",
               "clamped": False, "integrity": "none", "detail": None, "counters": [], "checksums": [],
               "skipped": None}
    frames = [{"time": 1_000_000_000 + i * 12_000, "id": "123", "kind": "", "length": 1, "bytes": [0],
               "dbc": {"name": "PROBE", "signals": {"VALUE": {"value": 1, "unit": ""}}, "period_ms": None}}
              for i in range(5)]
    body = '{"ready":true}\n' + json.dumps({"frames": frames, "dropped": 0}) + "\n"
    with server(tmp_path) as url:
        page = browser.new_page()
        page.route("**/run", lambda route: route.fulfill(json={
            "state": "Running", "id": "probe-run", "interface": "vcan0", "endpoints": [], "error": "",
            "restbus": None, "dbc": None, "car": car}))
        page.route("**/car/run", lambda route: route.fulfill(json={**car, "messages": [message]}))
        page.route("**/capture", lambda route: route.fulfill(body=body, content_type="application/x-ndjson"))
        page.route("**/car/link*", lambda route: route.fulfill(json={
            "interface": "vcan0", "virtual": True, "bitrate": None, "state": "UP", "bus_off": None,
            "bus_errors": None, "tx_dropped": 0}))
        page.goto(url)
        row = page.locator('#car-live-rows tr[data-message="PROBE"]')
        expect(row.locator(".values")).to_have_text("VALUE 1")
        expect(row.locator(".measured-period")).to_have_text("measuring...")
        page.close()


def describe_running_corolla(page, run_status, where: str) -> None:
    """What an adopted Corolla run must show, from the run's own status. The status names no bus, so ``where`` is the
    bus when the working copy names it, else the run's DBC file."""
    expect(page.locator("#car-step-1")).to_have_attribute("data-state", "done")
    expect(page.locator("#car-summary-1")).to_have_text(f"Toyota Corolla 2017-19 · {where} · generated drive")
    expect(page.locator("#car-step-2")).to_have_attribute("data-state", "done")
    counts = run_status["car"]["counts"]
    expect(page.locator("#car-summary-2")).to_be_visible()
    expect(page.locator("#car-summary-2")).to_contain_text(f"{counts['valid']} valid")
    expect(page.locator("#car-chips [data-state=valid]")).to_have_text(f"{counts['valid']} valid")
    expect(page.locator("#car-chips [data-state=none]")).to_have_text(f"{counts['none']} no counter or checksum")
    expect(page.locator("#car-chips [data-state=skipped]")).to_have_text(f"{counts['skipped']} skipped")
    identity = page.locator("#car-identity")
    expect(identity).to_be_visible()
    whole = page.evaluate("async () => (await fetch('/car/run')).json()")
    expect(page.locator("#car-identity-sha")).to_have_text(whole["dbc"]["sha256"])
    expect(page.locator("#car-identity-file")).to_have_text(whole["dbc"]["filename"])
    expect(page.locator("#car-identity-revision")).to_have_text(load_catalog().revision)
    expect(page.locator("#car-play")).to_be_disabled()
    expect(page.locator("#car-stop")).to_be_enabled()
    expect(page.locator("#car-live-rows tr").first).to_be_visible()


def test_reload_while_a_car_runs_describes_the_run(browser, vcan, tmp_path):  # noqa: F811
    with server(tmp_path) as url:
        page = browser.new_page()
        page.goto(url)
        choose_corolla(page)
        play(page)
        expect(page.locator("#car-run-status")).to_contain_text("elapsed")
        page.reload()
        expect(page.locator("#state")).to_have_text("Running")
        run_status = page.evaluate("async () => (await fetch('/run')).json()")
        describe_running_corolla(page, run_status, run_status["car"]["dbc"]["filename"])
        status = page.locator("#car-run-status")
        expect(status).to_contain_text(f"Running on {vcan} · generated drive")
        expect(status).to_contain_text("since this page opened")
        expect(status).not_to_contain_text("elapsed")
        stop(page)
        page.close()


def test_run_bar_start_of_a_car_profile_is_described(browser, vcan, tmp_path):  # noqa: F811
    with server(tmp_path) as url:
        page = browser.new_page()
        page.goto(url)
        expect(page.locator("#draft")).not_to_have_value("")
        page.locator("#profile").select_option("shipped/car_toyota_demo")
        expect(page.locator("#draft")).to_have_value(re.compile("platform: TOYOTA_COROLLA"))
        page.locator("#start").click()
        expect(page.locator("#state")).to_have_text("Running", timeout=15000)
        run_status = page.evaluate("async () => (await fetch('/run')).json()")
        describe_running_corolla(page, run_status, "pt bus")
        expect(page.locator("#car-run-status")).to_contain_text(f"Running on {vcan} · generated drive")
        expect(page.locator("#car-run-status")).to_contain_text("elapsed")  # seen while Starting
        stop(page)
        page.close()
