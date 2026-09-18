import errno

import isotp
import pytest

from ecu_simulator.transport import (
    AddressError,
    AddressingMode,
    BindError,
    FlowControlTimeoutError,
    InterfaceNotFoundError,
    IsoTpUnsupportedError,
    TransportIOError,
)
from ecu_simulator.transport.socketcan import IsoTpAddress, IsoTpOptions, IsoTpSocket
from tests.unit.fakes import FakeIsotpSocket, factory

ADDR = IsoTpAddress(rx_id=0x7E0, tx_id=0x7E8)


@pytest.fixture(autouse=True)
def _clear_instances():
    FakeIsotpSocket.instances.clear()
    yield
    for fake in FakeIsotpSocket.instances:
        if not fake.closed:
            fake.close()


# --- address and option validation -------------------------------------------------------


@pytest.mark.parametrize("rx, tx", [(0x800, 0x7E8), (0x7E0, 0x800), (-1, 0x7E8), (0x7E0, 0x7E0)])
def test_invalid_11bit_addresses_are_rejected(rx, tx):
    with pytest.raises(AddressError):
        IsoTpAddress(rx_id=rx, tx_id=tx)


def test_29bit_addresses_accept_extended_ids_and_reject_overflow():
    IsoTpAddress(rx_id=0x18DA10F1, tx_id=0x18DAF110, mode=AddressingMode.NORMAL_FIXED_29BIT)
    with pytest.raises(AddressError):
        IsoTpAddress(rx_id=0x18DA10F1, tx_id=0x7E8, mode=AddressingMode.NORMAL_11BIT)
    with pytest.raises(AddressError):
        IsoTpAddress(rx_id=0x20000000, tx_id=0x7E8, mode=AddressingMode.NORMAL_FIXED_29BIT)


def test_str_formats_ids_by_mode():
    assert str(ADDR) == "rx 0x7E0 / tx 0x7E8"
    wide = IsoTpAddress(rx_id=0x18DA10F1, tx_id=0x18DAF110, mode=AddressingMode.NORMAL_FIXED_29BIT)
    assert str(wide) == "rx 0x18DA10F1 / tx 0x18DAF110"


@pytest.mark.parametrize("field, value", [("pad_byte", 256), ("pad_byte", -1), ("block_size", 300), ("st_min", "0")])
def test_invalid_options_are_rejected(field, value):
    with pytest.raises(AddressError):
        IsoTpOptions(**{field: value})


def test_options_default_to_no_padding():
    assert IsoTpOptions() == IsoTpOptions(tx_padding=False, pad_byte=0, block_size=0, st_min=0)


# --- open: configuration order and content -------------------------------------------------


def test_open_without_padding_sets_fc_binds_and_goes_nonblocking():
    sock = IsoTpSocket("vcan0", ADDR, socket_factory=factory())
    sock.open()
    (fake,) = FakeIsotpSocket.instances
    names = [c[0] for c in fake.calls]
    assert names == ["set_fc_opts", "bind", "settimeout"]
    assert fake.calls[0][1] == {"bs": 0, "stmin": 0}
    interface, address = fake.calls[1][1]
    assert interface == "vcan0"
    assert (address.get_rx_arbitration_id(), address.get_tx_arbitration_id()) == (0x7E0, 0x7E8)
    assert address.is_rx_29bits() is False
    assert fake.timeout == 0.0
    assert sock.is_open and sock.fileno() == fake.fileno()


def test_open_with_padding_sets_tx_padding_flag_and_pad_byte():
    IsoTpSocket("vcan0", ADDR, IsoTpOptions(tx_padding=True, pad_byte=0xAA), socket_factory=factory()).open()
    (fake,) = FakeIsotpSocket.instances
    assert fake.calls[0] == ("set_opts", {"optflag": isotp.socket.flags.TX_PADDING, "txpad": 0xAA})


def test_open_with_29bit_address_uses_normal_29bits_mode():
    wide = IsoTpAddress(rx_id=0x18DA10F1, tx_id=0x18DAF110, mode=AddressingMode.NORMAL_FIXED_29BIT)
    IsoTpSocket("vcan0", wide, socket_factory=factory()).open()
    (fake,) = FakeIsotpSocket.instances
    address = fake.calls[1][1][1]
    assert address.is_rx_29bits() is True
    assert address.get_rx_arbitration_id() & 0x1FFFFFFF == 0x18DA10F1


def test_open_is_idempotent_and_close_is_idempotent():
    sock = IsoTpSocket("vcan0", ADDR, socket_factory=factory())
    sock.open()
    sock.open()
    assert len(FakeIsotpSocket.instances) == 1
    sock.close()
    sock.close()
    assert FakeIsotpSocket.instances[0].closed and not sock.is_open


def test_context_manager_opens_and_closes():
    with IsoTpSocket("vcan0", ADDR, socket_factory=factory()) as sock:
        assert sock.is_open
    assert not sock.is_open and FakeIsotpSocket.instances[0].closed


# --- error translation -------------------------------------------------------------------------


def test_missing_interface_maps_to_interface_not_found_and_closes_socket():
    sock = IsoTpSocket("nosuch0", ADDR, socket_factory=factory(bind_error=OSError(errno.ENODEV, "No such device")))
    with pytest.raises(InterfaceNotFoundError, match="nosuch0.*setup_vcan.sh"):
        sock.open()
    assert FakeIsotpSocket.instances[0].closed and not sock.is_open


def test_other_bind_failures_map_to_bind_error():
    sock = IsoTpSocket("vcan0", ADDR, socket_factory=factory(bind_error=OSError(errno.EADDRNOTAVAIL, "boom")))
    with pytest.raises(BindError, match="rx 0x7E0 / tx 0x7E8"):
        sock.open()


def test_kernel_without_isotp_maps_to_unsupported():
    def no_isotp():
        raise OSError(errno.EPROTONOSUPPORT, "Protocol not supported")

    with pytest.raises(IsoTpUnsupportedError, match="CONFIG_CAN_ISOTP"):
        IsoTpSocket("vcan0", ADDR, socket_factory=no_isotp).open()


def test_recv_returns_none_when_nothing_pending_and_payload_otherwise():
    sock = IsoTpSocket("vcan0", ADDR, socket_factory=factory())
    sock.open()
    (fake,) = FakeIsotpSocket.instances
    assert sock.recv() is None
    fake.feed.send(b"\x01\x0d")
    assert sock.recv() == b"\x01\x0d"


def test_recv_maps_ecomm_to_flow_control_timeout():
    sock = IsoTpSocket("vcan0", ADDR, socket_factory=factory())
    sock.open()
    FakeIsotpSocket.instances[0].recv_error = OSError(errno.ECOMM, "Communication error on send")
    with pytest.raises(FlowControlTimeoutError):
        sock.recv()


def test_send_reports_busy_and_maps_other_errors():
    sock = IsoTpSocket("vcan0", ADDR, socket_factory=factory(busy=True))
    sock.open()
    assert sock.send(b"\x41\x0d\x00") is False
    fake = FakeIsotpSocket.instances[0]
    fake.busy = False
    assert sock.send(b"\x41\x0d\x00") is True and fake.sent == [b"\x41\x0d\x00"]

    def boom(data):
        raise OSError(errno.ENETDOWN, "Network is down")

    fake.send = boom  # type: ignore[method-assign]
    with pytest.raises(TransportIOError, match="send failed"):
        sock.send(b"\x41")


def test_operations_on_closed_socket_raise_transport_io_error():
    sock = IsoTpSocket("vcan0", ADDR, socket_factory=factory())
    with pytest.raises(TransportIOError, match="not open"):
        sock.recv()
    with pytest.raises(TransportIOError, match="not open"):
        sock.fileno()
