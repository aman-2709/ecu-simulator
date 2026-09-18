"""Diagnostic transports.

A transport moves diagnostic payloads between the simulator and a tester. It knows
addresses and link-layer details only: nothing in this package may import ECU,
OBD, UDS, vehicle-state or DTC modules (enforced by a unit test).
"""

from ecu_simulator.transport.errors import (
    AddressError,
    BindError,
    FlowControlTimeoutError,
    InterfaceDownError,
    InterfaceNotFoundError,
    IsoTpUnsupportedError,
    TransportError,
    TransportIOError,
)
from ecu_simulator.transport.messages import AddressingMode, DiagnosticRequest, DiagnosticResponse

__all__ = [
    "AddressError",
    "AddressingMode",
    "BindError",
    "DiagnosticRequest",
    "DiagnosticResponse",
    "FlowControlTimeoutError",
    "InterfaceDownError",
    "InterfaceNotFoundError",
    "IsoTpUnsupportedError",
    "TransportError",
    "TransportIOError",
]
