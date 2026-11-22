"""Tester-side ISO-TP exchanges on the active ECU interface."""

import asyncio
from typing import Any

from ecu_simulator.config import EndpointConfigModel
from ecu_simulator.dtc.codes import GROUP_BITS
from ecu_simulator.protocols.uds import dtc
from ecu_simulator.protocols.uds.protocol import SUB_FUNCTION_SERVICES, SUPPRESS_POS_RSP_MSG_INDICATION_BIT
from ecu_simulator.transport.socketcan.isotp import IsoTpAddress, IsoTpOptions, IsoTpSocket

SUPPRESSED = "No response received · positive response suppression requested."
GROUPS = {bits: group for group, bits in GROUP_BITS.items()}
FLAGS = {
    dtc.TEST_FAILED: "Test failed",
    dtc.TEST_FAILED_THIS_OPERATION_CYCLE: "Failed this cycle",
    dtc.PENDING_DTC: "Pending",
    dtc.CONFIRMED_DTC: "Confirmed",
    dtc.TEST_NOT_COMPLETED_SINCE_LAST_CLEAR: "Incomplete since clear",
    dtc.TEST_FAILED_SINCE_LAST_CLEAR: "Failed since clear",
    dtc.TEST_NOT_COMPLETED_THIS_OPERATION_CYCLE: "Incomplete this cycle",
    dtc.WARNING_INDICATOR_REQUESTED: "Indicator requested",
}


def decode(data: bytes) -> dict[str, Any]:
    result: dict[str, Any] = {"raw": data.hex(" ").upper(), "message": "", "records": []}
    if data.startswith(b"\x7f") and len(data) >= 3:
        result["message"] = f"Negative response · NRC {data[2]:02X}"
    if data[:2] in (b"\x59\x04", b"\x59\x06"):
        if len(data) < 6:
            result["message"] = "Incomplete DTC response"
            return result
        high, low, failure, status = data[2:6]
        code = f"{GROUPS[high >> 6]}{high & 0x3F:02X}{low:02X}"
        kind = "Snapshot" if data[1] == 4 else "Extended data"
        flags = ", ".join(label for bit, label in FLAGS.items() if status & bit) or "No flags"
        result["message"] = f"{code} · {flags} · failure type {failure:02X}"
        if len(data) == 6:
            result["records"].append("No stored record")
        elif data[1] == 6 and len(data) >= 8:
            result["records"].append(f"{kind} {data[6]:02X} · data {data[7:].hex(' ').upper()}")
        elif data[1] == 4 and len(data) >= 11 and data[7] == 1:
            did = data[8:10].hex().upper()
            result["records"].append(f"{kind} {data[6]:02X} · DID {did} · data {data[10:].hex(' ').upper()}")
        else:
            result["records"].append("Record data requires a matching ECU definition")
        return result
    if not data.startswith(b"\x59\x02"):
        return result
    if len(data) < 3 or (len(data) - 3) % 4:
        result["message"] = "Incomplete DTC response"
        return result
    count = (len(data) - 3) // 4
    result["message"] = f"{count} matching DTC{'s' if count != 1 else ''} · availability {data[2]:02X}"
    for offset in range(3, len(data), 4):
        high, low, failure, status = data[offset:offset + 4]
        code = f"{GROUPS[high >> 6]}{high & 0x3F:02X}{low:02X}"
        flags = ", ".join(label for bit, label in FLAGS.items() if status & bit) or "No flags"
        result["records"].append(f"{code} · {flags} · failure type {failure:02X} · status {status:02X}")
    return result


async def _ready(sock: IsoTpSocket, *, write: bool = False) -> None:
    loop = asyncio.get_running_loop()
    event = asyncio.Event()
    add = loop.add_writer if write else loop.add_reader
    remove = loop.remove_writer if write else loop.remove_reader
    fd = sock.fileno()
    add(fd, event.set)
    try:
        await event.wait()
    finally:
        remove(fd)


async def transact(sock: IsoTpSocket, payload: bytes, timeout: float,
                   receiver: IsoTpSocket | None = None) -> dict[str, Any]:
    receiver = receiver or sock
    sent = False
    try:
        async with asyncio.timeout(timeout):
            while not sock.send(payload):
                await _ready(sock, write=True)
            sent = True
            while True:
                data = receiver.recv()
                if data is None:
                    await _ready(receiver)
                elif not (data.startswith(b"\x6a") and len(data) > 1):
                    return decode(data)
    except TimeoutError:
        suppressed = len(payload) > 1 and payload[0] in SUB_FUNCTION_SERVICES
        if not sent:
            message = "Transmission timed out"
        elif suppressed and payload[1] & SUPPRESS_POS_RSP_MSG_INDICATION_BIT:
            message = SUPPRESSED
        else:
            message = "Response timed out"
        return {"raw": "", "message": message, "records": []}


def _socket(interface: str, endpoint: EndpointConfigModel, *, listen_mode: bool = False) -> IsoTpSocket:
    address = IsoTpAddress(endpoint.tx, endpoint.rx, endpoint.mode)
    options = IsoTpOptions(tx_padding=endpoint.tx_padding, pad_byte=endpoint.pad_byte, listen_mode=listen_mode)
    return IsoTpSocket(interface, address, options)


async def exchange(interface: str, endpoint: EndpointConfigModel, payload: bytes,
                   reply: EndpointConfigModel) -> dict[str, Any]:
    if reply == endpoint:
        with _socket(interface, endpoint) as sock:
            return await transact(sock, payload, 1.5)
    # Only the return socket should issue flow control for the ECU reply.
    with _socket(interface, reply) as receiver, _socket(interface, endpoint, listen_mode=True) as sender:
        return await transact(sender, payload, 1.5, receiver)
