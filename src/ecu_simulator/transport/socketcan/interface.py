"""Checks on the CAN interface and on kernel ISO-TP support, with actionable errors.

These checks are read-only. Interface configuration (creating ``vcan0``, setting a
bitrate, bringing a link up) is privileged work done by ``scripts/setup_vcan.sh`` and
``scripts/setup_can.sh`` before the simulator starts; the simulator itself runs
unprivileged.
"""

from __future__ import annotations

import errno
import fcntl
import socket
import struct

from ecu_simulator.transport.errors import InterfaceDownError, InterfaceNotFoundError, IsoTpUnsupportedError

SIOCGIFFLAGS = 0x8913
IFF_UP = 0x1
_IFNAMSIZ = 16


def interface_index(name: str) -> int:
    """Return the interface index, or raise :class:`InterfaceNotFoundError`."""
    try:
        return socket.if_nametoindex(name)
    except (OSError, ValueError) as error:
        raise InterfaceNotFoundError(
            f"CAN interface {name!r} does not exist. Create it with scripts/setup_vcan.sh (virtual) "
            f"or scripts/setup_can.sh (hardware), or pass --interface."
        ) from error


def is_interface_up(name: str) -> bool:
    """Read IFF_UP through SIOCGIFFLAGS; correct inside network namespaces, unlike sysfs."""
    interface_index(name)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        request = struct.pack("16sH14x", name.encode()[: _IFNAMSIZ - 1], 0)
        flags = struct.unpack("16sH14x", fcntl.ioctl(probe.fileno(), SIOCGIFFLAGS, request))[1]
    return bool(flags & IFF_UP)


def check_interface(name: str) -> None:
    """Raise :class:`InterfaceNotFoundError` or :class:`InterfaceDownError` unless ``name`` is usable."""
    if not is_interface_up(name):
        raise InterfaceDownError(
            f"CAN interface {name!r} exists but is down. Bring it up with: sudo ip link set up {name}"
        )


def check_isotp_support() -> None:
    """Raise :class:`IsoTpUnsupportedError` unless the kernel can create CAN_ISOTP sockets."""
    try:
        socket.socket(socket.AF_CAN, socket.SOCK_DGRAM, socket.CAN_ISOTP).close()
    except OSError as error:
        if error.errno in (errno.EPROTONOSUPPORT, errno.ESOCKTNOSUPPORT, errno.EAFNOSUPPORT):
            raise IsoTpUnsupportedError(
                "the kernel cannot create CAN_ISOTP sockets: CONFIG_CAN_ISOTP is not built or the can_isotp "
                "module is unavailable. ISO-TP is in-tree since Linux 5.10 (Ubuntu 24.04 ships it); "
                "GitHub-hosted Azure kernels do not build it. Try: sudo modprobe can_isotp"
            ) from error
        raise
