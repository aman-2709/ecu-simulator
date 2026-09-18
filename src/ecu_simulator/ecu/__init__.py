"""ECU model: address routing and per-ECU service dispatch.

Nothing here opens a socket or imports a transport implementation; the only transport
types used are the addressing-only records in :mod:`ecu_simulator.transport.messages`.
"""

from ecu_simulator.ecu.ecu import Ecu, ServiceConflictError
from ecu_simulator.ecu.router import AddressRouter, RouteConflictError

__all__ = ["AddressRouter", "Ecu", "RouteConflictError", "ServiceConflictError"]
