"""A fake ELM327 that bridges to a real kernel ISO-TP socket.

It answers ``AT`` commands itself and forwards everything else to the bus, formatting the
reply the way an ELM327 would. That lets the acceptance cases in
:mod:`tests.hardware.acceptance_cases` run against the real simulator with only the
physical serial link simulated -- which is the whole point of the simulated backend: it
regression-tests this project's Python harness on every CI run, with no hardware.

What it is **not**: evidence about any real adapter. It shows that the harness and the
simulator agree. A real ELM327 may differ in ways only Phase 8b can find.

Deliberately not a protocol converter beyond what the cases need. It implements the AT
subset those cases use, and no more -- a fake that grew a protocol search or a timing
model would start having bugs of its own, and a passing test would stop meaning anything.
"""

from __future__ import annotations

import os
import pty
import threading

import isotp

# linux/can/isotp.h; not exported by can-isotp 2.x. Mirrors tests/integration/conftest.py.
CAN_ISOTP_SF_BROADCAST = 0x0800

#: header -> (tester tx id, tester rx id), from the shipped ice_default.yaml profile.
#:
#: 0x7DF is special. A functional request is a single frame broadcast from a transmit-only
#: socket, but the answer comes back on the ECU's *physical* response id, and the flow
#: control for a multi-frame answer has to leave from the ECU's physical request id. So the
#: functional route needs two sockets, exactly as tests/integration/conftest.py's
#: FunctionalTester does -- one socket cannot be both.
ROUTES: dict[str, tuple[int, int]] = {
    "7DF": (0x7DF, 0x7E8),  # OBD broadcast, answered from the engine's physical tx
    "7E0": (0x7E0, 0x7E8),  # OBD physical
    "7E1": (0x7E1, 0x7E9),  # UDS physical
}

#: The physical pair the functional route borrows for receiving and for flow control.
FUNCTIONAL_RX_ID = 0x7E8
FUNCTIONAL_FC_TX_ID = 0x7E0

DEFAULT_HEADER = "7DF"
PROMPT = b"\r\r>"


class BridgedElm327:
    """A pty an :class:`~tests.hardware.elm327_serial.Elm327` can open, backed by vcan."""

    def __init__(self, interface: str, timeout: float = 1.0) -> None:
        self.interface = interface
        self.timeout = timeout
        self.header = DEFAULT_HEADER
        self._sockets: dict[str, isotp.socket] = {}
        self._master, self._slave = pty.openpty()
        # Held open for the object's lifetime: with no slave fd, reading the master fails
        # with EIO and the responder dies before the driver ever opens the port.
        self.port = os.ttyname(self._slave)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    # --- lifecycle ---------------------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for sock in self._sockets.values():
            try:
                sock.close()
            except OSError:
                pass
        self._sockets.clear()
        for fd in (self._master, self._slave):
            try:
                os.close(fd)
            except OSError:
                pass

    # --- the bus side ------------------------------------------------------------------

    def _open(self, rx_id: int, tx_id: int, broadcast: bool = False) -> isotp.socket:
        sock = isotp.socket(timeout=self.timeout)
        flags = isotp.socket.flags.TX_PADDING
        if broadcast:
            flags |= CAN_ISOTP_SF_BROADCAST
        sock.set_opts(optflag=flags, txpad=0x00)
        sock.bind(
            self.interface,
            isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=rx_id, txid=tx_id),
        )
        return sock

    def _channel(self, header: str) -> tuple[isotp.socket, isotp.socket]:
        """The (send, receive) pair for ``header``. The same socket unless it is functional."""
        if header not in self._sockets:
            tx_id, rx_id = ROUTES[header]
            if header == DEFAULT_HEADER:
                # Transmit-only broadcast socket for the request...
                self._sockets[header] = self._open(rx_id=0, tx_id=tx_id, broadcast=True)
                # ...and the physical pair to receive on and to send flow control from.
                self._sockets[header + "-rx"] = self._open(
                    rx_id=FUNCTIONAL_RX_ID, tx_id=FUNCTIONAL_FC_TX_ID
                )
            else:
                self._sockets[header] = self._open(rx_id=rx_id, tx_id=tx_id)
        send = self._sockets[header]
        receive = self._sockets.get(header + "-rx", send)
        return send, receive

    def _forward(self, request: bytes) -> bytes | None:
        """Put a request on the bus and return the response, or None on a timeout."""
        send, receive = self._channel(self.header)
        try:
            send.send(request)
            return receive.recv()
        except (TimeoutError, OSError):
            return None

    # --- the serial side ---------------------------------------------------------------

    def _answer_at(self, command: str) -> bytes:
        """Answer the AT subset the acceptance cases use."""
        body = command[2:].replace(" ", "").upper()
        if body.startswith("SH"):
            header = body[2:]
            if header not in ROUTES:
                return b"?" + PROMPT
            self.header = header
            return b"OK" + PROMPT
        if body == "I":
            return b"ELM327 v1.4b" + PROMPT
        if body == "@1":
            return b"OBDII to RS232 Interpreter" + PROMPT
        if body == "DPN":
            return b"6" + PROMPT
        if body == "DP":
            return b"ISO 15765-4 (CAN 11/500)" + PROMPT
        if body in ("Z", "E0", "E1", "L0", "L1", "S0", "S1", "H0", "H1", "SP6", "AT1", "AT2"):
            return b"OK" + PROMPT
        if body.startswith("ST"):
            return b"OK" + PROMPT
        # Anything else -- including AT RV and AT SP 0 -- is deliberately unimplemented, so
        # a case that needs it is declared physical-only rather than passing against a fake.
        return b"?" + PROMPT

    @staticmethod
    def _format(response: bytes) -> bytes:
        """Render a response the way an ELM327 prints it (ELM327DSJ p. 45)."""
        text = " ".join(f"{byte:02X}" for byte in response)
        if len(response) <= 7:
            return text.encode() + PROMPT
        lines = [f"{len(response):03X}"]
        for index in range(0, len(response), 7):
            chunk = response[index : index + 7]
            lines.append(f"{index // 7:X}: " + " ".join(f"{b:02X}" for b in chunk))
        return "\r".join(lines).encode() + PROMPT

    def _serve(self) -> None:
        buffer = b""
        while not self._stop.is_set():
            try:
                data = os.read(self._master, 256)
            except OSError:
                return
            if not data:
                return
            os.write(self._master, data)  # echo, as E1 does
            buffer += data
            while b"\r" in buffer:
                line, buffer = buffer.split(b"\r", 1)
                command = line.decode(errors="replace").strip()
                if not command:
                    continue
                if command.replace(" ", "").upper().startswith("AT"):
                    os.write(self._master, self._answer_at(command))
                    continue
                try:
                    request = bytes.fromhex(command.replace(" ", ""))
                except ValueError:
                    os.write(self._master, b"?" + PROMPT)
                    continue
                response = self._forward(request)
                os.write(self._master, b"NO DATA" + PROMPT if response is None else self._format(response))
