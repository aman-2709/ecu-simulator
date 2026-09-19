"""Shared diagnostic trouble code state, and the code arithmetic both protocols need."""

from ecu_simulator.dtc.codes import code_number, is_valid
from ecu_simulator.dtc.store import DtcState, DtcStore

__all__ = ["DtcState", "DtcStore", "code_number", "is_valid"]
