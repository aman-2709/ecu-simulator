"""asyncio ISO-TP transport: one event loop, one kernel socket per endpoint.

An *endpoint* is one bound ISO-TP socket identified by an opaque name. Requests
arriving on it are handed to a synchronous handler as :class:`DiagnosticRequest`
records whose ``context`` is the :class:`EndpointConfig`; the handler's
:class:`DiagnosticResponse` is sent back on the same socket, whose TX identifier is
the response address. The transport never inspects payloads.

Functional addressing uses the kernel behavior verified in
docs/decisions/0001-isotp-binding.md: several sockets may share one functional RX
identifier with distinct TX identifiers, so an OBD-capable ECU simply has a functional
endpoint (rx 0x7DF / tx 0x7E8) next to its physical one (rx 0x7E0 / tx 0x7E8).
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ecu_simulator.transport.errors import AddressError, TransportError, TransportIOError
from ecu_simulator.transport.messages import DiagnosticRequest, DiagnosticResponse
from ecu_simulator.transport.socketcan.interface import check_interface, check_isotp_support
from ecu_simulator.transport.socketcan.isotp import IsoTpAddress, IsoTpOptions, IsoTpSocket, SocketFactory

logger = logging.getLogger(__name__)

RequestHandler = Callable[[DiagnosticRequest], DiagnosticResponse | None]


@dataclass(frozen=True, slots=True)
class EndpointConfig:
    """One ISO-TP socket to open: an opaque name, its addresses, and its options."""

    name: str
    address: IsoTpAddress
    functional: bool = False
    options: IsoTpOptions = field(default_factory=IsoTpOptions)


@dataclass(slots=True)
class _Endpoint:
    config: EndpointConfig
    socket: IsoTpSocket
    pending: deque[bytes] = field(default_factory=deque)
    writer_armed: bool = False


class IsoTpTransport:
    """Owns the sockets and the readiness callbacks; delegates payloads to ``handler``."""

    def __init__(
        self,
        interface: str,
        endpoints: Sequence[EndpointConfig],
        *,
        socket_factory: SocketFactory | None = None,
        check_environment: bool = True,
    ) -> None:
        if not endpoints:
            raise AddressError("at least one endpoint is required")
        names = [e.name for e in endpoints]
        if len(set(names)) != len(names):
            raise AddressError(f"duplicate endpoint names: {sorted({n for n in names if names.count(n) > 1})}")
        pairs = [(e.address.mode, e.address.rx_id, e.address.tx_id) for e in endpoints]
        if len(set(pairs)) != len(pairs):
            # The kernel accepts identical (rx, tx) pairs on several sockets; the simulator must not.
            dupes = sorted({f"rx 0x{rx:X} / tx 0x{tx:X}" for (_, rx, tx) in pairs if pairs.count((_, rx, tx)) > 1})
            raise AddressError(f"duplicate ISO-TP address pairs: {dupes}")
        self.interface = interface
        self._configs = list(endpoints)
        self._factory = socket_factory
        self._check_environment = check_environment
        self._endpoints: list[_Endpoint] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._handler: RequestHandler | None = None

    @property
    def endpoints(self) -> tuple[EndpointConfig, ...]:
        return tuple(self._configs)

    @property
    def is_running(self) -> bool:
        return self._loop is not None

    async def start(self, handler: RequestHandler) -> None:
        """Open every socket and register readiness callbacks on the running loop."""
        if self._loop is not None:
            raise TransportError("transport already started")
        if self._check_environment:
            check_isotp_support()
            check_interface(self.interface)
        loop = asyncio.get_running_loop()
        opened: list[_Endpoint] = []
        try:
            for config in self._configs:
                kwargs = {"socket_factory": self._factory} if self._factory is not None else {}
                sock = IsoTpSocket(self.interface, config.address, config.options, **kwargs)
                sock.open()
                opened.append(_Endpoint(config, sock))
        except TransportError:
            for endpoint in opened:
                endpoint.socket.close()
            raise
        self._endpoints = opened
        self._handler = handler
        self._loop = loop
        for endpoint in self._endpoints:
            loop.add_reader(endpoint.socket.fileno(), self._on_readable, endpoint)
            logger.info(
                "listening on %s %s (%s%s)",
                self.interface,
                endpoint.config.address,
                endpoint.config.name,
                ", functional" if endpoint.config.functional else "",
            )

    async def stop(self) -> None:
        """Remove callbacks and close every socket. Safe to call more than once."""
        loop, self._loop = self._loop, None
        if loop is None:
            return
        for endpoint in self._endpoints:
            fd = endpoint.socket.fileno()
            loop.remove_reader(fd)
            if endpoint.writer_armed:
                loop.remove_writer(fd)
            endpoint.socket.close()
        self._endpoints = []
        self._handler = None
        logger.info("transport on %s stopped", self.interface)

    # -- readiness callbacks (run on the loop thread) -------------------------------------

    def _on_readable(self, endpoint: _Endpoint) -> None:
        try:
            payload = endpoint.socket.recv()
        except TransportIOError as error:
            # e.g. FlowControlTimeoutError: our earlier multi-frame send was abandoned.
            logger.warning("%s: %s", endpoint.config.name, error)
            return
        if payload is None:
            return
        config = endpoint.config
        request = DiagnosticRequest(
            payload=payload,
            target_address=config.address.rx_id,
            functional=config.functional,
            addressing_mode=config.address.mode,
            context=config,
        )
        assert self._handler is not None
        try:
            response = self._handler(request)
        except Exception:
            logger.exception("%s: handler failed for request %s", config.name, payload.hex())
            return
        if response is None:
            return
        if response.delay:
            raise NotImplementedError("DiagnosticResponse.delay is reserved for fault injection")
        self._send(endpoint, response.payload)

    def _send(self, endpoint: _Endpoint, payload: bytes) -> None:
        if not endpoint.pending:
            try:
                if endpoint.socket.send(payload):
                    return
            except TransportIOError as error:
                logger.error("%s: %s", endpoint.config.name, error)
                return
        endpoint.pending.append(payload)
        self._arm_writer(endpoint)

    def _arm_writer(self, endpoint: _Endpoint) -> None:
        if not endpoint.writer_armed and self._loop is not None:
            self._loop.add_writer(endpoint.socket.fileno(), self._on_writable, endpoint)
            endpoint.writer_armed = True

    def _on_writable(self, endpoint: _Endpoint) -> None:
        while endpoint.pending:
            try:
                if not endpoint.socket.send(endpoint.pending[0]):
                    return  # still busy; stay armed
            except TransportIOError as error:
                logger.error("%s: dropping queued response: %s", endpoint.config.name, error)
            endpoint.pending.popleft()
        if self._loop is not None:
            self._loop.remove_writer(endpoint.socket.fileno())
        endpoint.writer_armed = False
