"""Stateful bench services for a single simulated ECU."""

from collections.abc import Iterator
from dataclasses import dataclass

from ecu_simulator.clock import Clock
from ecu_simulator.dtc import DtcStore
from ecu_simulator.protocols.base import negative_response
from ecu_simulator.protocols.obd.pids import MODE01_PIDS
from ecu_simulator.protocols.uds.memory import MEMORY_SERVICES, MemoryServices
from ecu_simulator.protocols.uds.session import UdsSession
from ecu_simulator.vehicle import VehicleState

CONTROL_SUBFUNCTIONS = frozenset({0x28, 0x31, 0x83, 0x85})
ADDITIONAL_SERVICES = MEMORY_SERVICES | CONTROL_SUBFUNCTIONS | {0x2A}
PERIODIC_PIDS = {1: 0x0C, 2: 0x0D, 3: 0x05}


@dataclass
class Subscription:
    period: float
    due: float


class BenchControls:
    def __init__(self, dtcs: DtcStore, session: UdsSession, clock: Clock, vehicle: VehicleState | None):
        self.dtcs = dtcs
        self.session = session
        self.vehicle = vehicle
        self.clock = clock
        self.memory = MemoryServices()
        self.periodic: dict[tuple[str, int], Subscription] = {}
        self.routines: dict[int, int] = {}
        self.communication: dict[int, int] = {}
        self.timing: bytes | None = None

    def reset(self) -> None:
        self.periodic.clear()
        self.routines.clear()
        self.communication.clear()
        self.memory.transfer = None
        self.dtcs.setting_enabled = True
        self.timing = None

    def handle(self, payload: bytes, endpoint: str) -> bytes:
        sid = payload[0]
        if sid in MEMORY_SERVICES:
            return self.memory.handle(payload)
        if len(payload) < 2:
            return negative_response(sid, 0x13)
        mode = payload[1]
        if sid == 0x2A:
            return self.subscribe(mode, payload[2:], endpoint)
        if sid == 0x31:
            return self.routine(payload)
        if sid == 0x28:
            if len(payload) != 3:
                return negative_response(sid, 0x13)
            if mode > 3:
                return negative_response(sid, 0x12)
            if payload[2] not in (1, 2, 3):
                return negative_response(sid, 0x31)
            for group in (1, 2):
                if payload[2] & group:
                    self.communication[group] = mode
        elif sid == 0x85:
            if len(payload) != 2:
                return negative_response(sid, 0x13)
            if mode not in (1, 2):
                return negative_response(sid, 0x12)
            self.dtcs.setting_enabled = mode == 1
        elif sid == 0x83:
            if mode not in (1, 2, 3, 4):
                return negative_response(sid, 0x12)
            if len(payload) != (6 if mode == 4 else 2):
                return negative_response(sid, 0x13)
            default = self.session.timing(self.session.active).record()
            if mode in (1, 3):
                record = default if mode == 1 else self.timing or default
                return bytes([0xC3, mode]) + record
            self.timing = payload[2:] if mode == 4 else None
        return bytes([sid + 0x40, mode])

    def routine(self, payload: bytes) -> bytes:
        if len(payload) != 4:
            return negative_response(0x31, 0x13)
        mode = payload[1]
        rid = int.from_bytes(payload[2:], "big")
        if mode not in (1, 2, 3):
            return negative_response(0x31, 0x12)
        if rid not in (0x100, 0x101, 0x203, 0xF01, 0xF02, 0xF03, 0xFF00):
            return negative_response(0x31, 0x31)
        if mode == 1:
            if rid == 0xFF00:
                if self.memory.transfer is not None:
                    return negative_response(0x31, 0x22)
                flash = self.memory.regions[0x1000]
                flash[:] = bytes([0xFF]) * len(flash)
            self.routines[rid] = 1
        elif rid not in self.routines or (mode == 2 and self.routines[rid] != 1):
            return negative_response(0x31, 0x24)
        elif mode == 2:
            self.routines[rid] = 0
        response = b"\x71" + payload[1:]
        return response + bytes([self.routines[rid]]) if mode == 3 else response

    def subscribe(self, mode: int, ids: bytes, endpoint: str) -> bytes:
        if mode not in (1, 2, 3, 4):
            return negative_response(0x2A, 0x31)
        if not ids and mode != 4:
            return negative_response(0x2A, 0x13)
        if any(i not in PERIODIC_PIDS or self.vehicle is None
               or not MODE01_PIDS[PERIODIC_PIDS[i]].supported_by(self.vehicle) for i in ids):
            return negative_response(0x2A, 0x31)
        if mode == 4:
            for key in list(self.periodic):
                if key[0] == endpoint and (not ids or key[1] in ids):
                    del self.periodic[key]
        else:
            period = {1: 1.0, 2: 0.25, 3: 0.1}[mode]
            for ident in ids:
                self.periodic[endpoint, ident] = Subscription(period, self.clock.now() + period)
        return b"\x6a"

    def reports(self) -> Iterator[tuple[str, bytes]]:
        if not self.periodic or self.vehicle is None:
            return
        now = self.clock.now()
        for (endpoint, ident), subscription in self.periodic.items():
            if now >= subscription.due:
                subscription.due = now + subscription.period
                if self.communication.get(1, 0) in (0, 2):
                    data = MODE01_PIDS[PERIODIC_PIDS[ident]].read(self.vehicle)
                    yield endpoint, bytes([0x6A, ident]) + data
