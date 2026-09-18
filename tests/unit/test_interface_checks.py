import errno
import socket

import pytest

from ecu_simulator.transport import InterfaceDownError, InterfaceNotFoundError, IsoTpUnsupportedError
from ecu_simulator.transport.socketcan import check_interface, check_isotp_support
from ecu_simulator.transport.socketcan import interface as iface_mod


def test_missing_interface_is_actionable():
    with pytest.raises(InterfaceNotFoundError, match="'nosuchcan9'.*setup_vcan.sh"):
        iface_mod.interface_index("nosuchcan9")
    with pytest.raises(InterfaceNotFoundError):
        check_interface("nosuchcan9")


def test_loopback_reports_up_via_ioctl():
    # "lo" exists on every Linux host and is up; the ioctl path must agree.
    assert iface_mod.interface_index("lo") >= 1
    assert iface_mod.is_interface_up("lo") is True
    check_interface("lo")


def test_down_interface_is_reported(monkeypatch):
    monkeypatch.setattr(iface_mod, "is_interface_up", lambda name: False)
    with pytest.raises(InterfaceDownError, match="ip link set up vcan0"):
        check_interface("vcan0")


def test_isotp_support_check_maps_eprotonosupport(monkeypatch):
    def no_isotp(*args, **kwargs):
        raise OSError(errno.EPROTONOSUPPORT, "Protocol not supported")

    monkeypatch.setattr(iface_mod.socket, "socket", no_isotp)
    with pytest.raises(IsoTpUnsupportedError, match="CONFIG_CAN_ISOTP"):
        check_isotp_support()


def test_isotp_support_check_reraises_unrelated_errors(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError(errno.EMFILE, "Too many open files")

    monkeypatch.setattr(iface_mod.socket, "socket", boom)
    with pytest.raises(OSError, match="Too many open files"):
        check_isotp_support()


def test_isotp_support_on_this_kernel_matches_raw_probe():
    try:
        socket.socket(socket.AF_CAN, socket.SOCK_DGRAM, socket.CAN_ISOTP).close()
        supported = True
    except OSError as error:
        supported = error.errno not in (errno.EPROTONOSUPPORT, errno.ESOCKTNOSUPPORT, errno.EAFNOSUPPORT)
    if supported:
        check_isotp_support()
    else:
        with pytest.raises(IsoTpUnsupportedError):
            check_isotp_support()
