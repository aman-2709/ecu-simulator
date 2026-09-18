"""Project-owned boundary around the kernel ``CAN_ISOTP`` socket.

The Linux kernel implements ISO 15765-2. This module drives it through the
``can-isotp`` 2.x package (``isotp.socket``), as decided in
docs/decisions/0001-isotp-binding.md, and exposes only what the simulator needs:
open with addresses and options, non-blocking receive, send, ``fileno`` for the
event loop, and close. Nothing else in the project imports ``isotp``.

Two can-isotp 2.x details are handled here on purpose:

* the constructor's ``timeout`` argument is applied only when it is greater than
  zero, so non-blocking mode is set with an explicit ``settimeout(0.0)``;
* ``isotp.socket.flags`` lacks ``SF_BROADCAST``, ``CF_BROADCAST`` and
  ``DYN_FC_PARMS``; they are not needed by the simulator and are not defined here.
"""

from __future__ import annotations

import errno
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import isotp

from ecu_simulator.transport.errors import (
    AddressError,
    BindError,
    FlowControlTimeoutError,
    InterfaceNotFoundError,
    IsoTpUnsupportedError,
    TransportIOError,
)
from ecu_simulator.transport.messages import AddressingMode

logger = logging.getLogger(__name__)

CAN_SFF_MASK = 0x7FF
CAN_EFF_MASK = 0x1FFFFFFF

_ISOTP_MODES = {
    AddressingMode.NORMAL_11BIT: isotp.AddressingMode.Normal_11bits,
    AddressingMode.NORMAL_FIXED_29BIT: isotp.AddressingMode.Normal_29bits,
}


@dataclass(frozen=True, slots=True)
class IsoTpAddress:
    """RX and TX CAN identifiers of one ISO-TP socket (normal addressing)."""

    rx_id: int
    tx_id: int
    mode: AddressingMode = AddressingMode.NORMAL_11BIT

    def __post_init__(self) -> None:
        limit = CAN_SFF_MASK if self.mode is AddressingMode.NORMAL_11BIT else CAN_EFF_MASK
        for name, value in (("rx_id", self.rx_id), ("tx_id", self.tx_id)):
            if not isinstance(value, int) or not 0 <= value <= limit:
                raise AddressError(f"{name} {value!r} is not a valid {self.mode.value} CAN identifier (0..0x{limit:X})")
        if self.rx_id == self.tx_id:
            raise AddressError(f"rx_id and tx_id must differ (both 0x{self.rx_id:X})")

    def __str__(self) -> str:
        width = 3 if self.mode is AddressingMode.NORMAL_11BIT else 8
        return f"rx 0x{self.rx_id:0{width}X} / tx 0x{self.tx_id:0{width}X}"


@dataclass(frozen=True, slots=True)
class IsoTpOptions:
    """Socket options the simulator sets. Defaults reproduce the legacy behavior: no padding."""

    tx_padding: bool = False
    pad_byte: int = 0x00
    block_size: int = 0
    st_min: int = 0

    def __post_init__(self) -> None:
        for name, value in (("pad_byte", self.pad_byte), ("block_size", self.block_size), ("st_min", self.st_min)):
            if not isinstance(value, int) or not 0 <= value <= 0xFF:
                raise AddressError(f"{name} must be a byte value 0..255, got {value!r}")


SocketFactory = Callable[[], Any]


class IsoTpSocket:
    """One kernel ISO-TP socket, opened non-blocking.

    ``socket_factory`` exists for unit tests: it must return an object with the
    ``isotp.socket`` methods used below.
    """

    def __init__(
        self,
        interface: str,
        address: IsoTpAddress,
        options: IsoTpOptions | None = None,
        *,
        socket_factory: SocketFactory = isotp.socket,
    ) -> None:
        self.interface = interface
        self.address = address
        self.options = options or IsoTpOptions()
        self._factory = socket_factory
        self._sock: Any = None

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def open(self) -> None:
        if self._sock is not None:
            return
        try:
            sock = self._factory()
        except OSError as error:
            raise _map_socket_creation_error(error) from error
        try:
            if self.options.tx_padding:
                sock.set_opts(optflag=isotp.socket.flags.TX_PADDING, txpad=self.options.pad_byte)
            sock.set_fc_opts(bs=self.options.block_size, stmin=self.options.st_min)
            sock.bind(
                self.interface,
                isotp.Address(_ISOTP_MODES[self.address.mode], rxid=self.address.rx_id, txid=self.address.tx_id),
            )
            # can-isotp applies the constructor timeout only when > 0; be explicit.
            sock.settimeout(0.0)
        except OSError as error:
            sock.close()
            raise _map_bind_error(error, self.interface, self.address) from error
        self._sock = sock
        logger.debug(
            "opened ISO-TP socket on %s (%s, padding=%s)", self.interface, self.address, self.options.tx_padding
        )

    def fileno(self) -> int:
        return int(self._require().fileno())

    def recv(self) -> bytes | None:
        """Return one complete ISO-TP payload, or ``None`` when nothing is pending."""
        sock = self._require()
        try:
            data = sock.recv()
        except BlockingIOError:
            return None
        except OSError as error:
            raise _map_io_error(error, "receive", self.address) from error
        return bytes(data) if data is not None else None

    def send(self, payload: bytes) -> bool:
        """Queue one payload for transmission.

        Returns ``False`` when the kernel is still transmitting a previous multi-frame
        payload on this socket (``EAGAIN``); the caller retries when the socket becomes
        writable. Raises :class:`TransportIOError` for anything else.
        """
        sock = self._require()
        try:
            sock.send(payload)
        except BlockingIOError:
            return False
        except OSError as error:
            raise _map_io_error(error, "send", self.address) from error
        return True

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            sock.close()
            logger.debug("closed ISO-TP socket on %s (%s)", self.interface, self.address)

    def __enter__(self) -> IsoTpSocket:
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _require(self) -> Any:
        if self._sock is None:
            raise TransportIOError(f"ISO-TP socket {self.address} on {self.interface} is not open")
        return self._sock


def _map_socket_creation_error(error: OSError) -> IsoTpUnsupportedError | TransportIOError:
    if error.errno in (errno.EPROTONOSUPPORT, errno.ESOCKTNOSUPPORT, errno.EAFNOSUPPORT):
        return IsoTpUnsupportedError(
            "the kernel cannot create CAN_ISOTP sockets: CONFIG_CAN_ISOTP is not built or the "
            "can_isotp module is unavailable (in-tree since Linux 5.10; not shipped for GitHub-hosted "
            f"Azure kernels). Underlying error: {error}"
        )
    return TransportIOError(f"cannot create CAN_ISOTP socket: {error}")


def _map_bind_error(error: OSError, interface: str, address: IsoTpAddress) -> BindError | InterfaceNotFoundError:
    if error.errno == errno.ENODEV:
        return InterfaceNotFoundError(
            f"CAN interface {interface!r} does not exist. Create it with scripts/setup_vcan.sh "
            f"(virtual) or scripts/setup_can.sh (hardware), or pass --interface."
        )
    return BindError(f"cannot bind ISO-TP socket ({address}) on {interface!r}: {error}")


def _map_io_error(error: OSError, operation: str, address: IsoTpAddress) -> TransportIOError:
    if error.errno == errno.ECOMM:
        return FlowControlTimeoutError(
            f"multi-frame transmission on {address} abandoned: the peer sent no flow control (ECOMM)"
        )
    return TransportIOError(f"ISO-TP {operation} failed on {address}: {error}")
