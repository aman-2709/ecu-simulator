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
