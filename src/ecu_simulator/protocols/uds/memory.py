"""Per-ECU volatile memory and staged UDS block transfers."""

from dataclasses import dataclass, field
from zlib import crc32

from ecu_simulator.protocols.base import negative_response

MEMORY_SERVICES = frozenset({0x23, 0x3D, 0x34, 0x35, 0x36, 0x37})
BLOCK_DATA = 64


@dataclass
class Transfer:
    download: bool
    region: memoryview
    data: bytearray
    offset: int = 0
    sequence: int = 1
    last_request: bytes = b""
    last_response: bytes = b""


@dataclass
class MemoryServices:
    regions: dict[int, bytearray] = field(default_factory=lambda: {
        base: bytearray(4096) for base in (0x1000, 0x100000, 0x200000)
    })
    transfer: Transfer | None = None

    def handle(self, request: bytes) -> bytes:
        sid = request[0]
        if sid == 0x36:
            return self.block(request)
        if sid == 0x37:
            return self.finish(request)

        transferring = sid in (0x34, 0x35)
        pos = 2 if transferring else 1
        if len(request) <= pos:
            return negative_response(sid, 0x13)
        fmt = request[pos]
        addr_len, size_len = fmt & 15, fmt >> 4
        if not (1 <= addr_len <= 4 and 1 <= size_len <= 4):
            return negative_response(sid, 0x31)
        start = pos + 1
        end = start + addr_len + size_len
        if len(request) < end:
            return negative_response(sid, 0x13)
        address = int.from_bytes(request[start:start + addr_len], "big")
        size = int.from_bytes(request[start + addr_len:end], "big")
        if len(request) != end + (size if sid == 0x3D else 0):
            return negative_response(sid, 0x13)
        if size == 0 or (transferring and request[1] != 0):
            return negative_response(sid, 0x31)
        for base, buffer in self.regions.items():
            if base <= address and address + size <= base + len(buffer):
                region = memoryview(buffer)[address - base:address - base + size]
                break
        else:
            return negative_response(sid, 0x31)
        if sid == 0x23:
            if size > 4094:
                return negative_response(sid, 0x14)
            return b"\x63" + bytes(region)
        if self.transfer is not None:
            return negative_response(sid, 0x24)
        if sid == 0x3D:
            region[:] = request[end:]
            return b"\x7d" + request[1:end]
        download = sid == 0x34
        self.transfer = Transfer(download, region, bytearray(size) if download else bytearray(region))
        return bytes([sid + 0x40, 0x10, BLOCK_DATA + 2])

    def block(self, request: bytes) -> bytes:
        tx = self.transfer
        if len(request) < 2:
            return negative_response(0x36, 0x13)
        if tx is None:
            return negative_response(0x36, 0x24)
        if request == tx.last_request:
            return tx.last_response
        if request[1] != tx.sequence:
            return negative_response(0x36, 0x73)
        remaining = len(tx.data) - tx.offset
        if not remaining:
            return negative_response(0x36, 0x24)
        response = bytes([0x76, tx.sequence])
        if tx.download:
            data = request[2:]
            if not 1 <= len(data) <= min(remaining, BLOCK_DATA):
                return negative_response(0x36, 0x13)
            tx.data[tx.offset:tx.offset + len(data)] = data
            tx.offset += len(data)
        else:
            if len(request) != 2:
                return negative_response(0x36, 0x13)
            chunk = tx.data[tx.offset:tx.offset + BLOCK_DATA]
            response += chunk
            tx.offset += len(chunk)
        tx.sequence = (tx.sequence + 1) % 256
        tx.last_request, tx.last_response = request, response
        return response

    def finish(self, request: bytes) -> bytes:
        tx = self.transfer
        if tx is None or tx.offset != len(tx.data):
            return negative_response(0x37, 0x24)
        if len(request) == 5:
            if crc32(tx.data) != int.from_bytes(request[1:], "big"):
                return negative_response(0x37, 0x72)
        elif request not in (b"\x37", b"\x37\x00"):
            return negative_response(0x37, 0x31)
        if tx.download:
            tx.region[:] = tx.data
        self.transfer = None
        return b"\x77"
