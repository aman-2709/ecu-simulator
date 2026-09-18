"""OBD-II (SAE J1979 / ISO 15031-5 conventions) protocol package."""

from ecu_simulator.protocols.obd.legacy import LEGACY_OBD_SIDS, LegacyObdProtocol

__all__ = ["LEGACY_OBD_SIDS", "LegacyObdProtocol"]
