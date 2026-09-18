"""Integration tests against the real kernel CAN_ISOTP path on a vcan interface.

Every test here is marked ``vcan``. The session fixture skips the whole set with an
explicit reason when the environment cannot provide it:

* the kernel cannot create CAN_ISOTP sockets (GitHub-hosted Azure kernels have
  ``CONFIG_CAN_ISOTP`` disabled; see docs/decisions/0001-isotp-binding.md);
* the interface (``vcan0`` or ``$ECU_SIM_CAN_IFACE``) does not exist or is down.

Run them without root through ``scripts/run_integration_tests.sh``, which creates a
private vcan0 inside an unprivileged user+network namespace.
"""

from __future__ import annotations

import errno
import os
import socket
import struct
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass

import isotp
import pytest

from ecu_simulator.transport.socketcan import interface as iface_mod

INTERFACE = os.environ.get("ECU_SIM_CAN_IFACE", "vcan0")
READY_TIMEOUT = 5.0


def _isotp_skip_reason() -> str | None:
    try:
        socket.socket(socket.AF_CAN, socket.SOCK_DGRAM, socket.CAN_ISOTP).close()
    except OSError as error:
        if error.errno in (errno.EPROTONOSUPPORT, errno.ESOCKTNOSUPPORT, errno.EAFNOSUPPORT):
            return "kernel cannot create CAN_ISOTP sockets (CONFIG_CAN_ISOTP not built, e.g. GitHub-hosted Azure kernels)"
        return f"cannot create CAN_ISOTP socket: {error}"
    try:
        iface_mod.interface_index(INTERFACE)
    except Exception:
        return f"CAN interface {INTERFACE!r} does not exist: run scripts/setup_vcan.sh or scripts/run_integration_tests.sh"
    if not iface_mod.is_interface_up(INTERFACE):
        return f"CAN interface {INTERFACE!r} is down: sudo ip link set up {INTERFACE}"
    return None


@pytest.fixture(scope="session")
def vcan() -> str:
    reason = _isotp_skip_reason()
    if reason:
        pytest.skip(reason)
    return INTERFACE


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "/tests/integration/" in str(item.fspath) or "tests/integration" in str(item.fspath):
            item.add_marker(pytest.mark.vcan)


# --- tester-side helpers (independent of the simulator's own transport code) --------------


CAN_ISOTP_SF_BROADCAST = 0x0800  # linux/can/isotp.h; not defined by can-isotp 2.x


def open_tester_socket(interface: str, rx_id: int, tx_id: int, timeout: float = 1.0) -> isotp.socket:
    """A physical ISO-TP tester socket, padded like an ELM327 would pad."""
    sock = isotp.socket(timeout=timeout)
    sock.set_opts(optflag=isotp.socket.flags.TX_PADDING, txpad=0x00)
    sock.bind(interface, isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=rx_id, txid=tx_id))
    return sock


class FunctionalTester:
    """Tester behaving like an ELM327 on a functional request.

    The request goes out as a single frame on 0x7DF from a transmit-only socket; the
    response is received on the ECU's physical response id, and the flow control for a
    multi-frame response is sent on the ECU's physical request id (ISO 15765-4).
    """

    def __init__(self, interface: str, functional_id: int, response_id: int, physical_id: int, timeout: float = 1.0):
        self.tx = isotp.socket()
        self.tx.set_opts(optflag=isotp.socket.flags.TX_PADDING | CAN_ISOTP_SF_BROADCAST, txpad=0x00)
        self.tx.bind(interface, isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=0, txid=functional_id))
        self.rx = open_tester_socket(interface, rx_id=response_id, tx_id=physical_id, timeout=timeout)

    def send(self, payload: bytes) -> None:
        self.tx.send(payload)

    def recv(self) -> bytes:
        return self.rx.recv()

    def close(self) -> None:
        self.tx.close()
        self.rx.close()


@dataclass(frozen=True)
class Frame:
    can_id: int
    dlc: int
    data: bytes


class RawCapture:
    """Collect raw Classical CAN frames on the interface for a fixed window."""

    def __init__(self, interface: str) -> None:
        self.sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self.sock.bind((interface,))
        self.sock.settimeout(0.05)

    def collect(self, seconds: float) -> list[Frame]:
        frames: list[Frame] = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                raw = self.sock.recv(16)
            except TimeoutError:
                continue
            can_id, dlc = struct.unpack_from("<IB", raw)
            frames.append(Frame(can_id & 0x1FFFFFFF, dlc, raw[8 : 8 + dlc]))
        return frames

    def close(self) -> None:
        self.sock.close()


# --- the simulator as a subprocess -----------------------------------------------------------


class Simulator:
    def __init__(self, interface: str, workdir: str, log_level: str = "INFO") -> None:
        self.interface = interface
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "ecu_simulator", "--interface", interface, "--log-level", log_level],
            cwd=workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def wait_ready(self, timeout: float = READY_TIMEOUT) -> None:
        """Poll a functional request until the simulator answers."""
        deadline = time.monotonic() + timeout
        probe = FunctionalTester(self.interface, 0x7DF, 0x7E8, 0x7E0, timeout=0.2)
        try:
            while time.monotonic() < deadline:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"simulator exited early with {self.proc.returncode}: {self.proc.stderr.read()}")
                try:
                    probe.send(b"\x01\x00")
                    if probe.recv():
                        return
                except (TimeoutError, OSError):
                    pass
                time.sleep(0.05)
        finally:
            probe.close()
        self.terminate()
        raise RuntimeError("simulator did not become ready")

    def signal(self, sig: int) -> None:
        self.proc.send_signal(sig)

    def wait(self, timeout: float) -> tuple[int, str]:
        _, err = self.proc.communicate(timeout=timeout)
        return self.proc.returncode, err

    def terminate(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.communicate(timeout=5)


@pytest.fixture(scope="module")
def simulator(vcan: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Simulator]:
    sim = Simulator(vcan, str(tmp_path_factory.mktemp("sim")))
    try:
        sim.wait_ready()
        yield sim
    finally:
        sim.terminate()
