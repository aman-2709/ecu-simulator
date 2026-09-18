"""Transport and runtime infrastructure errors.

These are infrastructure failures. They are never converted into diagnostic
responses: a missing CAN interface is an error for the operator, not a negative
response code for the tester.
"""


class TransportError(Exception):
    """Base class for every diagnostic-transport failure."""


class InterfaceNotFoundError(TransportError):
    """The CAN interface does not exist on this host."""


class InterfaceDownError(TransportError):
    """The CAN interface exists but is not up."""


class IsoTpUnsupportedError(TransportError):
    """The kernel cannot create CAN_ISOTP sockets (module missing or not built)."""


class AddressError(TransportError, ValueError):
    """An address or transport option is invalid or conflicts with another endpoint."""


class BindError(TransportError):
    """Binding an ISO-TP socket to its addresses failed."""


class TransportIOError(TransportError):
    """Sending or receiving on an open socket failed."""


class FlowControlTimeoutError(TransportIOError):
    """A multi-frame transmission was abandoned because the peer sent no flow control."""
