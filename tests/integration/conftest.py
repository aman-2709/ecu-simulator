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
import threading
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
            return (
                "kernel cannot create CAN_ISOTP sockets (CONFIG_CAN_ISOTP not built, e.g. GitHub-hosted Azure kernels)"
            )
        return f"cannot create CAN_ISOTP socket: {error}"
    try:
        iface_mod.interface_index(INTERFACE)
    except Exception:
        return (
            f"CAN interface {INTERFACE!r} does not exist: run scripts/setup_vcan.sh or scripts/run_integration_tests.sh"
        )
    if not iface_mod.is_interface_up(INTERFACE):
        return f"CAN interface {INTERFACE!r} is down: sudo ip link set up {INTERFACE}"
    return None


def _foreign_responder_present(interface: str) -> bool:
    """True if something already answers OBD functional requests on the interface.

    Two simulators bound to the same ids both answer every request, which makes
    every payload assertion read a stale reply. Detect it up front.
    """
    probe = FunctionalTester(interface, 0x7DF, 0x7E8, 0x7E0, timeout=0.3)
    try:
        probe.send(b"\x01\x00")
        try:
            probe.recv()
            return True
        except (TimeoutError, OSError):
            return False
    finally:
        probe.close()


@pytest.fixture(scope="session")
def vcan() -> str:
    reason = _isotp_skip_reason()
    if reason:
        pytest.skip(reason)
    if _foreign_responder_present(INTERFACE):
        pytest.fail(
            f"another OBD responder is already active on {INTERFACE!r} (a running ecu-simulator?). "
            "Stop it, or run the suite in a private namespace: scripts/run_integration_tests.sh"
        )
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
        """Functionally addressed request (like ELM327 with ATSH 7DF)."""
        self.tx.send(payload)

    def send_physical(self, payload: bytes) -> None:
        """Physically addressed request on the same channel (like ELM327 with ATSH 7E0)."""
        self.rx.send(payload)

    def recv(self) -> bytes:
        return self.rx.recv()

    def close(self) -> None:
        self.tx.close()
        self.rx.close()


def assert_silent(channel, request: bytes, probe: bytes, answer: bytes) -> None:
    """Assert the simulator says nothing to ``request``, then prove the channel still works.

    On vcan a timeout can only mean the simulator chose not to answer. On a physical bus it
    equally matches a dead adapter, a bitrate mismatch (which ELM327DSJ page 62 shows can
    present as silence), an unterminated bus, a bus-off interface or an unpowered dongle. A
    bare ``pytest.raises(TimeoutError)`` therefore passes while the bench is broken, which
    is the worst failure a test can have.

    The probe goes *after* the silence, never before. A channel proved alive beforehand may
    have died in between, and then the silence still proves nothing.
    """
    channel.send(request)
    try:
        answered = channel.recv()
    except TimeoutError:
        pass  # the silence we wanted; the probe below establishes what it means
    else:
        raise AssertionError(
            f"expected silence after {request.hex()}, but the simulator answered {answered.hex()}"
        )
    channel.send(probe)
    try:
        observed = channel.recv()
    except TimeoutError:
        raise AssertionError(
            f"the channel went dead: probe {probe.hex()} was not answered either, so the "
            f"silence after {request.hex()} proves nothing about the simulator"
        ) from None
    assert observed == answer, (
        f"probe {probe.hex()} answered {observed.hex()}, expected {answer.hex()}; "
        f"the silence after {request.hex()} proves nothing about the simulator"
    )


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
    """The simulator as a subprocess, with its console log captured line by line."""

    READY_MARKER = "ecu-simulator ready on"

    def __init__(
        self, interface: str, workdir: str, log_level: str = "INFO", profile: str | None = None
    ) -> None:
        self.interface = interface
        self.workdir = workdir
        self.log_level = log_level
        # None means the packaged ice_default.yaml, which is what almost every test wants.
        self.profile = profile
        self._start()

    def _start(self) -> None:
        interface, workdir, log_level = self.interface, self.workdir, self.log_level
        command = [sys.executable, "-m", "ecu_simulator", "--interface", interface, "--log-level", log_level]
        if self.profile is not None:
            command += ["--profile", self.profile]
        self.proc = subprocess.Popen(
            command,
            cwd=workdir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._lines: list[str] = []
        self._ready = threading.Event()
        self._reader = threading.Thread(target=self._pump_stderr, daemon=True)
        self._reader.start()

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self._lines.append(line)
            if self.READY_MARKER in line:
                self._ready.set()

    @property
    def log(self) -> str:
        return "".join(self._lines)

    def wait_ready(self, timeout: float = READY_TIMEOUT) -> None:
        """Wait for the process's own "ready" log line (not for a wire reply, which any
        other responder on the bus could produce before this process even started)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._ready.wait(0.05):
                return
            if self.proc.poll() is not None:
                self._reader.join(1)
                raise RuntimeError(f"simulator exited early with {self.proc.returncode}:\n{self.log}")
        self.terminate()
        raise RuntimeError(f"simulator did not become ready:\n{self.log}")

    def signal(self, sig: int) -> None:
        self.proc.send_signal(sig)

    def wait(self, timeout: float) -> tuple[int, str]:
        code = self.proc.wait(timeout=timeout)
        self._reader.join(timeout)
        return code, self.log

    def terminate(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self._reader.join(1)

    def restart(self) -> None:
        """Stop the process and start a fresh one, discarding the ECU state it accumulated."""
        self.terminate()
        self._start()
        self.wait_ready()


@pytest.fixture(scope="module")
def simulator(vcan: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Simulator]:
    sim = Simulator(vcan, str(tmp_path_factory.mktemp("sim")))
    try:
        sim.wait_ready()
        yield sim
    finally:
        sim.terminate()


@pytest.fixture
def mutating(simulator: Simulator) -> Iterator[Simulator]:
    """For a test that changes ECU state, restarting the simulator afterwards.

    ``simulator`` is module-scoped, which cost nothing while every request was a read.
    Since Phase 6, OBD Mode 04 and UDS 0x14 clear the shared DTC store, so a test that
    clears would decide what the tests after it see. Any test that mutates state asks for
    this fixture as well, and the process is replaced once it is done. Only one simulator
    can own the CAN identifiers on the interface at a time, so this restarts the shared
    one rather than starting a second.
    """
    yield simulator
    simulator.restart()
