import asyncio
import struct

import pytest

from ecu_simulator.gui import replay
from tests.integration.test_gui_control_vcan import browser, expect, server  # noqa: F401


@pytest.mark.asyncio
async def test_concurrent_replay_and_stale_stop(vcan):
    player = replay.Replay()
    recording = replay.parse_recording(b"(1.0) can0 123#AA")
    original = player.start(recording, "log", True)["id"]
    try:
        with pytest.raises(replay.RunConflict):
            player.start(recording, "again", False)
        async with asyncio.timeout(2):
            while player.info["lap"] == 1:
                await asyncio.sleep(.02)
    finally:
        await player.stop(original)
    current = player.start(recording, "new", True)["id"]
    try:
        with pytest.raises(replay.RunConflict):
            await player.stop(original)
    finally:
        await player.stop(current)


def test_full_bundled_recording_on_wire(browser, vcan, tmp_path):  # noqa: F811
    import gzip
    import socket
    import time
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from ecu_simulator.gui.capture import open_capture, read_batch

    sample = Path(replay.__file__).parent / "samples" / "kenworth-t270.candump.gz"
    recording = replay.parse_recording(gzip.decompress(sample.read_bytes()))
    with server(tmp_path) as url, open_capture(vcan) as sock, ThreadPoolExecutor(1) as pool:
        page = browser.new_page()
        page.goto(url)
        page.locator("#live-tab").click()
        page.locator("#replay-panel summary").click()

        page.locator("#replay-source").select_option("kenworth")

        def listen():
            observed = []
            deadline = time.monotonic() + 20
            while len(observed) < len(recording.frames) and time.monotonic() < deadline:
                observed.extend(read_batch(sock)[0])
                time.sleep(.003)
            return observed

        future = pool.submit(listen)
        page.locator("#replay-play").click()
        expect(page.locator("#replay-status")).to_contain_text("Finished", timeout=18000)
        expect(page.locator("#readings-only")).to_be_checked()
        page.locator("#readings-only").uncheck()
        expect(page.locator("#frame-rows tr")).to_have_count(104)
        expect(page.locator("#j1939-mode")).to_be_checked()
        expect(page.locator("#group-view")).to_have_attribute("aria-pressed", "true")
        for query in ("rpm", "EEC1", "61444", "0CF00400"):
            page.locator("#can-filter").fill(query)
            row = page.locator("#frame-rows tr")
            expect(row).to_have_count(1)
            expect(row).to_contain_text("Engine speed")
            expect(row).to_contain_text("rpm")
            expect(row).to_contain_text("ms")
        page.locator("#can-filter").fill("EEC2")
        for identifier in ("0CF00300", "0CF00331"):
            expect(page.locator("#frame-rows")).to_contain_text(identifier)
        page.locator("#can-filter").fill("")
        page.locator("#readings-only").check()
        expect(page.locator("#frame-rows tr")).to_have_count(3)
        expect(page.locator("#frame-summary")).to_contain_text("3 of 104 groups")
        page.locator("#readings-only").uncheck()
        expect(page.locator("#frame-rows tr").first).to_contain_text("EEC2")
        page.locator("#can-filter").fill("TP.DT")
        expect(page.locator("#frame-rows")).to_contain_text("fragment")
        observed = future.result()
        assert len(observed) == len(recording.frames) == 7098
        for actual, (_, frame) in zip(observed, recording.frames, strict=True):
            identifier, size, payload = struct.unpack("=IB3x8s", frame)
            extended = bool(identifier & socket.CAN_EFF_FLAG)
            assert (len(actual["id"]) == 8) == extended
            assert int(actual["id"], 16) == identifier & socket.CAN_EFF_MASK
            assert actual["bytes"] == list(payload[:size])
        elapsed = (observed[-1]["time"] - observed[0]["time"]) / 1_000_000
        assert abs(elapsed - recording.frames[-1][0] / 1e9) < .15
        page.close()


def test_uploaded_j1939_grouping_and_engine_status(browser, vcan, tmp_path):  # noqa: F811
    content = b"""(0.0) can1 0CF00400#0000000000000000
(0.1) can1 0CF00401#000000FFFFFFFFFF
(0.2) can1 0CF00402#000000401F000000
(0.3) can1 18EF1000#AA
(0.4) can1 0CEF1100#BB
(0.5) can1 123#01"""
    with server(tmp_path) as url:
        page = browser.new_page()
        page.goto(url)
        page.locator("#live-tab").click()
        page.locator("#replay-panel summary").click()
        page.locator("#replay-source").select_option("file")
        page.locator("#replay-file").set_input_files({"name": "mix.log", "mimeType": "text/plain", "buffer": content})
        page.locator("#replay-play").click()
        expect(page.locator("#replay-status")).to_contain_text("Finished")
        expect(page.locator("#j1939-mode")).not_to_be_checked()
        page.locator("#j1939-mode").check()
        rows = page.locator("#frame-rows tr")
        expect(rows).to_have_count(5)
        for source, expected in (("00", "0 rpm"), ("01", "unavailable"), ("02", "1000 rpm")):
            expect(rows.filter(has_text="0CF004" + source)).to_contain_text(expected)
        directed = rows.filter(has_text="0CEF1100")
        expect(directed).to_contain_text("61184")
        expect(directed.locator("td").nth(1)).to_have_text("2")
        expect(rows.filter(has_text="123")).not_to_contain_text("J1939")
        page.locator("#trace-view").click()
        expect(rows).to_have_count(6)
        page.close()


def test_j1939_decoding_and_replay_boundary(browser, vcan, tmp_path):  # noqa: F811
    content = b"""(0.0) can1 0CF00300#D10000FFFF0F6A7D
(0.1) can1 0CF00331#FFFFFFFFFFFFFFFF
(0.2) can1 0CF00320#007D320000000000
(0.3) can1 18EAFF0B#E5FE00
(0.4) can1 18FF0131#0000000000000000"""
    with server(tmp_path) as url:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(url)
        page.locator("#live-tab").click()
        page.locator("#replay-panel summary").click()
        page.locator("#replay-source").select_option("file")
        page.locator("#replay-file").set_input_files({"name": "loop.log", "mimeType": "text/plain", "buffer": content})
        page.locator("#replay-loop").check()
        page.locator("#j1939-mode").check()
        page.locator("#replay-play").click()
        expect(page.locator("#replay-source")).to_be_hidden()
        expect(page.locator("#replay-stop")).to_be_visible()
        rows = page.locator("#frame-rows tr")
        for identifier, value in (("0CF00300", "Accelerator pedal 0% · Engine load 0%"),
                                  ("0CF00331", "Accelerator pedal unavailable · Engine load unavailable"),
                                  ("0CF00320", "Accelerator pedal 50% · Engine load 50%"),
                                  ("18EAFF0B", "Request Engine hours · PGN 65253"),
                                  ("18FF0131", "No signal decoder")):
            expect(rows.filter(has_text=identifier)).to_contain_text(value)
        expect(rows.filter(has_text="18EAFF0B")).to_contain_text("Replay loop")
        expect(page.locator("#legend-context")).to_contain_text("same PGN and source")
        assert page.locator("#frame-scroll").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
        page.locator("#replay-stop").click()
        expect(page.locator("#replay-status")).to_contain_text("Stopped")
        page.close()


def test_default_drive_shows_changing_readings(browser, vcan, tmp_path):  # noqa: F811
    with server(tmp_path) as url:
        page = browser.new_page()
        page.goto(url)
        page.locator("#live-tab").click()
        page.locator("#replay-panel summary").click()
        expect(page.locator("#replay-source")).to_have_value("kenworth-drive")
        page.locator("#replay-play").click()
        expect(page.locator("#readings-only")).to_be_checked()
        speed = page.locator("#frame-rows tr").filter(has_text="Engine speed").locator("td").nth(6)
        expect(speed).to_contain_text("rpm")
        first = speed.inner_text()
        expect(speed).not_to_have_text(first)
        expect(page.locator("#replay-status")).to_contain_text("Finished", timeout=18000)
        expect(page.locator("#frame-rows")).not_to_contain_text("No signal decoder")
        page.locator("#readings-only").uncheck()
        expect(page.locator("#frame-rows")).to_contain_text("No signal decoder")
        page.close()


def test_drive_plot_uses_recorded_values_and_capture_clock(browser, vcan, tmp_path):  # noqa: F811
    import gzip
    from itertools import groupby
    from pathlib import Path

    with server(tmp_path) as url:
        page = browser.new_page()
        # Inspect the data supplied to the actual renderer without changing the production API.
        def observe_plot(route):
            response = route.fetch()
            probe = """;window.uPlot = new Proxy(uPlot, {construct(target, args) {
              return window.testPlot = Reflect.construct(target, args);
            }});"""
            route.fulfill(response=response, body=response.text() + probe)

        page.route("**/uPlot.iife.min.js", observe_plot)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.locator("#live-tab").click()
        page.locator("#replay-panel summary").click()
        page.locator("#replay-play").click()
        expect(page.locator("#trend-chart canvas")).to_be_visible()
        expect(page.locator(".trend-controls output").first).to_contain_text("rpm")
        expect(page.locator("#trend-source option")).to_have_count(2)
        page.locator("#capture-pause").click()
        frozen = page.evaluate("window.testPlot.data[0].at(-1)")
        expect(page.locator("#capture-pause")).to_have_text("Resume display")
        page.locator("#trend-source").select_option("49")
        expect(page.locator("#trend-status")).to_contain_text("unavailable")
        page.locator("#trend-source").select_option("0")
        assert page.evaluate("window.testPlot.data[0].at(-1)") == frozen
        page.locator("#capture-pause").click()
        expect(page.locator("#capture-pause")).to_have_text("Pause display")
        pedal = page.get_by_role("checkbox", name="Accelerator pedal")
        pedal.uncheck()
        assert not page.evaluate("window.testPlot.series[2].show")
        pedal.check()
        expect(page.locator("#replay-status")).to_contain_text("Finished", timeout=18000)
        sample = Path(replay.__file__).parent / "samples" / "kenworth-t660.candump.gz"
        recording = replay.parse_recording(gzip.decompress(sample.read_bytes()))
        expected = [[], [], []]
        for _, frame in recording.frames:
            can_id, _, payload = struct.unpack("=IB3x8s", frame)
            if can_id == 0x8CF00400:
                expected[0].append(int.from_bytes(payload[3:5], "little") / 8)
            elif can_id == 0x8CF00300:
                expected[1].append(payload[1] * 4 / 10)
                expected[2].append(payload[2])
        assert page.evaluate("window.testPlot.data[0].at(-1)") > frozen
        times, *series = page.evaluate("window.testPlot.data")
        assert times == sorted(times)
        assert 11.9 < times[-1] - times[0] < 12.2
        for line, recorded in zip(series, expected, strict=True):
            numeric = [v for v in line if v is not None]
            assert [v for v, _ in groupby(numeric)] == [v for v, _ in groupby(recorded)]
        assert not errors, errors
        page.close()
