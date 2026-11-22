"""Local DBC import, bench publication and captured-signal browser acceptance."""

import re
import socket
import struct
from pathlib import Path

from tests.integration.test_gui_control_vcan import browser, expect, server, stop  # noqa: F401

FORD = Path(__file__).parents[1] / "fixtures" / "dbc" / "ford_fusion_2018_pt.dbc"
EXPECTED = bytes.fromhex("80 00 14 00 78 00 00 00")  # Voltage byte = (12 V - 6 V) / 0.05.


def test_import_ford_and_observe_actual_wire(browser, vcan, tmp_path):  # noqa: F811
    with server(tmp_path) as url, socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW) as raw:
        raw.bind((vcan,))
        raw.settimeout(2)
        page = browser.new_page()
        page.goto(url)
        expect(page.locator("#draft")).not_to_have_value("")
        page.locator("#dbc-file").set_input_files(FORD)
        page.locator("#dbc-message").select_option("EPAS_INFO")
        page.locator('[data-signal="SteMdule_U_Meas"]').fill("12")
        expect(page.locator("#dbc-raw")).to_have_text(EXPECTED.hex(" ").upper())
        page.locator("#dbc-apply").click()
        expect(page.locator("#dbc-configured")).to_contain_text("EPAS_INFO")
        page.locator("#dbc-file").set_input_files({"name": "invalid.dbc", "mimeType": "text/plain", "buffer": b"!"})
        expect(page.locator("#dbc-status")).to_contain_text("Invalid DBC")
        expect(page.locator("#dbc-message")).to_have_value("EPAS_INFO")
        expect(page.locator("#dbc-configured")).to_contain_text("EPAS_INFO")
        page.locator("#name").fill("Ford instrument bench")
        page.get_by_role("button", name="Save as").click()
        expect(page.locator("#notice")).to_contain_text("Saved")
        page.reload()
        page.locator("#profile").select_option("user/Ford%20instrument%20bench")
        expect(page.locator("#dbc-configured")).to_contain_text("EPAS_INFO")
        page.locator("#interface").select_option(vcan)
        page.locator("#start").click()
        expect(page.locator("#state")).to_have_text("Running")
        try:
            assert struct.unpack("=IB3x8s", raw.recv(16)) == (0x82, 8, EXPECTED)
            page.locator("#live-tab").click()
            page.locator("#capture-interface").select_option(vcan)
            page.locator("#capture-toggle").click()
            expect(page.locator("#frame-rows")).to_contain_text("EPAS_INFO")
            expect(page.locator("#frame-rows")).to_contain_text("SteMdule_U_Meas 12 Volts")
            expect(page.locator("#frame-rows")).to_contain_text("80 00 14 00 78 00 00 00")
        finally:
            page.locator("#sim-tab").click()
            stop(page)
        # A moved profile can still be inspected before restoring its local DBC artifact.
        next((tmp_path / "ecu-simulator/dbc").glob("*.dbc")).unlink()
        page.reload()
        page.locator("#profile").select_option("user/Ford%20instrument%20bench")
        expect(page.locator("#dbc-status")).to_contain_text("Missing DBC artifact")
        expect(page.locator("#draft")).to_have_value(re.compile(r".*EPAS_INFO", re.S))
        page.locator("#dbc-file").set_input_files(FORD)
        expect(page.locator("#dbc-configured")).to_contain_text("EPAS_INFO")
        page.close()


def test_passive_import_and_change_decoder_during_capture(browser, vcan, tmp_path):  # noqa: F811
    mux = b"""VERSION ""
BS_:
BU_: ECU
BO_ 291 Branch: 2 ECU
 SG_ Selector M : 0|8@1+ (1,0) [0|1] "" ECU
 SG_ Voltage m0 : 8|8@1+ (1,0) [0|255] "V" ECU
 SG_ Current m1 : 8|8@1+ (1,0) [0|255] "A" ECU
BO_ 2147483939 Extended: 2 ECU
 SG_ Temp : 0|8@1+ (1,0) [0|255] "degC" ECU
"""
    with server(tmp_path) as url, socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW) as source:
        source.bind((vcan,))
        page = browser.new_page()
        page.goto(url)
        expect(page.locator("#draft")).not_to_have_value("")
        page.locator("#live-tab").click()
        page.locator("#capture-interface").select_option(vcan)
        page.locator("#capture-toggle").click()
        expect(page.locator("#capture-status")).to_contain_text("Capturing")
        page.locator(".capture-dbc summary").click()
        with page.expect_response(lambda r: r.url.endswith("/capture") and r.request.method == "POST" and r.ok):
            page.locator("#live-dbc-file").set_input_files({"name": "mux.dbc", "mimeType": "text/plain", "buffer": mux})
        expect(page.locator("#live-dbc-status")).to_contain_text("mux.dbc")
        for identifier, payload in [(0x123, b"\x00\x0C"), (0x123, b"\x01\x03"),
                                    (socket.CAN_EFF_FLAG | 0x123, b"\x19\x00"), (0x444, b"\xAB")]:
            source.send(struct.pack("=IB3x8s", identifier, len(payload), payload))
        rows = page.locator("#frame-rows")
        expect(rows).to_contain_text("Voltage 12 V")
        expect(rows).to_contain_text("Current 3 A")
        expect(rows).to_contain_text("Temp 25 degC")
        expect(rows).to_contain_text("444")
        with page.expect_response(lambda r: r.url.endswith("/capture") and r.request.method == "POST" and r.ok):
            page.locator("#live-dbc-file").set_input_files(
                Path(__file__).parents[2] / "src/ecu_simulator/_opendbc/toyota_nodsu_pt_generated.dbc"
            )
        source.send(struct.pack("=IB3x8s", 0xAA, 8, bytes.fromhex("2A0F" * 4)))
        expect(rows).to_contain_text("WHEEL_SPEED_FR 40 km/h")
        page.close()


def test_shipped_bmw_drive_runs_without_import(browser, vcan, tmp_path):  # noqa: F811
    with server(tmp_path) as url:
        page = browser.new_page()
        page.goto(url)
        page.locator("#profile").select_option("shipped/bmw_e9x_demo_drive", timeout=3000)
        expect(page.locator("#dbc-configured")).to_contain_text("WheelSpeeds")
        page.locator("#interface").select_option(vcan)
        page.locator("#start").click()
        expect(page.locator("#state")).to_have_text("Running")
        try:
            page.locator("#live-tab").click()
            page.locator("#capture-interface").select_option(vcan)
            page.locator("#capture-toggle").click()
            rows = page.locator("#frame-rows")
            expect(rows).to_contain_text("BatteryVoltage 14")
            for signal in ("SteeringPosition 0", "TurnSignalIdle 1", "CruiseControlInactiveFlag 1", "IgnOff 0"):
                expect(rows).to_contain_text(signal)
            for speed, brake, wipers in ((0, 1, 0), (20, 0, 1), (40, 0, 1), (20, 0, 0), (0, 1, 0)):
                for wheel in ("FL", "FR", "RL", "RR"):
                    expect(rows.locator("tr").filter(has_text="WheelSpeeds").last).to_contain_text(
                        f"Wheel_{wheel} {speed} kph", timeout=7000)
                for message, signal in (("WiperSwitch", f"AutoWipersOn {wipers}"),
                                        ("InstrumentClusterStatus_KOMBI", f"HandbrakeActive {brake}")):
                    expect(rows.locator("tr").filter(has_text=message).last).to_contain_text(signal)
        finally:
            page.locator("#sim-tab").click()
            stop(page)
            page.close()


def test_replay_recorded_timing_and_stop(browser, vcan, tmp_path):  # noqa: F811
    from ecu_simulator.gui.capture import open_capture, read_batch

    with server(tmp_path) as url, open_capture(vcan) as listener:
        page = browser.new_page()
        page.goto(url)
        page.locator("#live-tab").click()
        page.locator("#replay-panel summary").click()
        page.locator("#replay-source").select_option("file")
        file_input = page.locator("#replay-file")
        file_input.set_input_files({"name": "bad.log", "mimeType": "text/plain",
                                    "buffer": b"(0.0) can0 321#11\ninvalid"})
        page.locator("#replay-play").click()
        expect(page.locator("#replay-status")).to_contain_text("Line 2")
        assert not read_batch(listener)[0]
        page.locator(".capture-dbc summary").click()
        with page.expect_response(lambda r: r.url.endswith("/capture") and r.request.method == "POST" and r.ok):
            page.locator("#live-dbc-file").set_input_files(FORD)
        content = b"(123.000) can0 321#11\n(123.120) can0 00000321#2233\n(123.120) can0 082 [8] 80 00 14 00 78 00 00 00"
        file_input.set_input_files({"name": "drive.log", "mimeType": "text/plain", "buffer": content})
        page.locator("#replay-play").click()
        expect(page.locator("#replay-status")).to_contain_text("Finished")
        expect(page.locator("#frame-rows")).to_contain_text("00000321")
        expect(page.locator("#frame-rows")).to_contain_text("SteMdule_U_Meas 12 Volts")
        frames, _ = read_batch(listener)
        assert [(f["id"], f["bytes"]) for f in frames] == [
            ("321", [17]), ("00000321", [34, 51]), ("082", list(EXPECTED))]
        assert 80_000 < frames[1]["time"] - frames[0]["time"] < 200_000
        assert frames[2]["time"] - frames[1]["time"] < 30_000

        page.locator("#replay-source").select_option("kenworth")
        page.locator("#replay-loop").check()
        page.locator("#replay-play").click()
        expect(page.locator("#replay-status")).to_contain_text("Playing")
        expect(page.locator("#frame-rows")).to_contain_text("Engine speed")
        page.locator("#replay-stop").click()
        expect(page.locator("#replay-status")).to_contain_text("Stopped")
        page.close()
