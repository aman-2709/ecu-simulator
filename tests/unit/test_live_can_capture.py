import socket
import time

from ecu_simulator.gui.capture import FRAME, TIMESTAMP_NS, read_batch


def test_frame_format_flags_length_and_kernel_timestamp():
    sender, receiver = socket.socketpair(type=socket.SOCK_DGRAM)
    with sender, receiver:
        receiver.setsockopt(socket.SOL_SOCKET, TIMESTAMP_NS, 1)
        receiver.setblocking(False)
        before = time.time_ns() // 1000
        for can_id in (0x123, socket.CAN_EFF_FLAG | 0x123, socket.CAN_RTR_FLAG | 0x123, socket.CAN_ERR_FLAG | 0x24):
            sender.send(FRAME.pack(can_id, 2, b"\xAA\xBB"))
        frames, _ = read_batch(receiver)
        assert [(f["id"], f["kind"], f["length"], f["bytes"]) for f in frames] == [
            ("123", "", 2, [170, 187]), ("00000123", "", 2, [170, 187]),
            ("123", "RTR", 2, []), ("00000024", "ERR", 2, [170, 187]),
        ]
        times = [f["time"] for f in frames]
        assert sorted(times) == times
        assert before <= times[0] <= times[-1] <= time.time_ns() // 1000
        assert read_batch(receiver) == ([], None)


def test_wheel_capture_uses_receive_times_and_clears_data_between_runs():
    from ecu_simulator.gui.capture import WheelMonitor

    monitor = WheelMonitor("vcan0")
    run = {"id": "one", "state": "Running", "interface": "vcan0",
           "restbus": {"can_id": 170, "period_ms": 20, "started_us": 1000000}}
    data = list(bytes.fromhex("1A6F 2A0F 1A6F 2A0F"))
    frame = {"time": 1000000, "id": "0AA", "kind": "", "length": 8, "bytes": data}
    def snapshot(frames, now):
        return monitor.observe(run, frames, now)

    assert snapshot([], 1000000)["state"] == "Waiting"
    for other in [dict(frame, id="000000AA"), dict(frame, kind="RTR"), dict(frame, id="123")]:
        assert snapshot([other], 1000000)["state"] == "Waiting"
        assert "wheels" not in other
    latest = snapshot([frame, dict(frame, time=1021000)], 1050000)
    assert latest["state"] == "Live"
    assert latest["period_ms"] == 21
    assert latest["age_ms"] == 29
    assert latest["bytes"] == data
    assert latest["speeds"] == {"FR": 0, "FL": 40, "RR": 0, "RL": 40}
    assert snapshot([], 1120999)["state"] == "Live"
    assert snapshot([], 1121000)["state"] == "Stale"
    malformed = snapshot([dict(frame, time=1200000, length=1, bytes=[0])], 1200000)
    assert malformed["state"] == "Malformed"
    assert malformed["speeds"] is None
    assert malformed["bytes"] == [0]
    run["id"] = "two"
    run["restbus"]["started_us"] = 1210000
    queued = {"time": 1209000, "id": "0AA", "kind": "", "length": 8, "bytes": data}
    assert snapshot([queued], 1210000)["state"] == "Waiting"
    assert "wheels" not in queued
    assert snapshot([dict(frame, time=1220000)], 1220000)["period_ms"] is None
    run["interface"] = "can1"
    assert snapshot([frame], 1240000) is None
    run["interface"] = "vcan0"
    assert snapshot([], 1250000)["state"] == "Waiting"
    run["state"] = "Stopped"
    assert snapshot([frame], 1260000) is None
