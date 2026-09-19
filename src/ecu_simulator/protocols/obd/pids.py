"""Mode 01 parameter definitions: physical value in, wire bytes out.

Each :class:`PidDefinition` names the vehicle signals it reads and the function that
encodes them. The encoder is the only place a physical quantity becomes bytes: an engine
turning at 2500 rpm is ``engine.rpm == 2500`` in
:class:`~ecu_simulator.vehicle.VehicleState`, never a pre-encoded pair of bytes.

A parameter is supported when every signal it reads exists on the configured vehicle, so
a battery-electric profile advertises no engine parameters without anything having to say
so. The supported-parameter masks are computed from this table
(:mod:`ecu_simulator.protocols.obd.masks`), not hard-coded.

**Evidence.** None of these encodings has been checked against SAE J1979 or its Digital
Annex; that text is not available to this project
(docs/decisions/0003-phase-5-obd-evidence.md). Each definition records where its encoding
came from, and `docs/conformance.md` carries `standards validated = no` for every one.
Two strengths of evidence appear here:

* ``CAPTURE`` — corroborated byte for byte by a worked capture in the public ELM327
  datasheet, which shows ``01 04 05 0B 0C`` answered with
  ``41 04 3F 05 44 0B 21 0C 17 B8``: load 0x3F, coolant 0x44, manifold pressure 0x21 and
  1518 rpm as 0x17B8.
* ``PUBLIC`` — consistent public technical description and universal agreement among
  independent implementations, with no competing formula found, but no capture checked
  here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from types import MappingProxyType

from ecu_simulator.vehicle import VehicleState

CAPTURE = "ELM327 datasheet worked CAN capture (01 04 05 0B 0C -> 41 04 3F 05 44 0B 21 0C 17 B8)"
PUBLIC = "consistent public technical description; no competing formula found"

PID_MAX = 0xFF


@dataclass(frozen=True, slots=True)
class PidDefinition:
    """One Mode 01 parameter."""

    pid: int
    name: str
    unit: str
    signals: tuple[str, ...]
    encode: Callable[[VehicleState], bytes]
    length: int
    evidence: str

    def supported_by(self, vehicle: VehicleState) -> bool:
        """True when the configured vehicle carries every signal this parameter reads."""
        return all(vehicle.has(signal) for signal in self.signals)

    def read(self, vehicle: VehicleState) -> bytes:
        """Encode the parameter. Reading observes state; it never advances it."""
        data = self.encode(vehicle)
        if len(data) != self.length:
            raise ValueError(f"PID 0x{self.pid:02X} encoder produced {len(data)} bytes, expected {self.length}")
        return data


# -- encoding helpers -------------------------------------------------------------------------
#
# Each clamps to the range the wire field can represent. Clamping here is not the same as
# silently accepting bad configuration: the profile schema rejects out-of-range
# configuration at load (DEV-14). This is the last line of defence for values a future
# scenario engine computes, and it keeps an encoder from raising mid-response.
#
# Conversions truncate toward zero rather than rounding. That is what this project has
# always done for the fuel level, where 50 per cent encodes as 0x7F and not 0x80, and
# changing it would be an unreviewed wire change. Truncation is applied uniformly so the
# encoders do not each pick their own rule.


def _byte(value: float) -> bytes:
    return bytes([max(0, min(255, int(value)))])


def _word(value: float) -> bytes:
    return max(0, min(0xFFFF, int(value))).to_bytes(2, "big")


def _percent_to_byte(percent: float) -> bytes:
    """0..100 per cent over the full byte range: A * 100 / 255."""
    return _byte(percent * 255.0 / 100.0)


def _temp_to_byte(celsius: float) -> bytes:
    """A - 40 degrees Celsius."""
    return _byte(celsius + 40.0)


def _trim_to_byte(percent: float) -> bytes:
    """(A - 128) * 100 / 128 per cent."""
    return _byte(percent * 128.0 / 100.0 + 128.0)


def _definition(
    pid: int,
    name: str,
    unit: str,
    signals: tuple[str, ...],
    encode: Callable[[VehicleState], bytes],
    length: int,
    evidence: str,
) -> PidDefinition:
    return PidDefinition(pid, name, unit, signals, encode, length, evidence)


MODE01_DEFINITIONS: tuple[PidDefinition, ...] = (
    _definition(
        0x05,
        "Engine coolant temperature",
        "degC",
        ("engine.coolant_temp",),
        lambda v: _temp_to_byte(v.get("engine.coolant_temp")),
        1,
        CAPTURE,
    ),
    _definition(
        0x0D,
        "Vehicle speed",
        "km/h",
        ("vehicle.speed",),
        lambda v: _byte(v.get("vehicle.speed")),
        1,
        PUBLIC,
    ),
    _definition(
        0x2F,
        "Fuel tank level input",
        "%",
        ("engine.fuel_level",),
        lambda v: _percent_to_byte(v.get("engine.fuel_level")),
        1,
        PUBLIC,
    ),
    _definition(
        0x51,
        "Fuel type",
        "coded",
        ("engine.fuel_type",),
        lambda v: _byte(v.get("engine.fuel_type")),
        1,
        PUBLIC,
    ),
)

MODE01_PIDS: MappingProxyType[int, PidDefinition] = MappingProxyType(
    {definition.pid: definition for definition in MODE01_DEFINITIONS}
)

# Deliberately absent, recorded so the omission is visible rather than looking accidental:
#
#   0x01 monitor status since DTCs cleared — its first byte carries the malfunction
#   indicator lamp state and the confirmed-DTC count, which are DtcStore semantics
#   belonging to Phase 6, and its remaining bytes are readiness-monitor flags that differ
#   between spark and compression ignition. Deferred rather than guessed.
DEFERRED_MODE01_PIDS: MappingProxyType[int, str] = MappingProxyType(
    {0x01: "monitor status: needs the Phase 6 DTC store, and the monitor bits are not corroborated"}
)


def supported_pids(vehicle: VehicleState, definitions: Iterable[PidDefinition] | None = None) -> frozenset[int]:
    """Which Mode 01 parameters this vehicle can answer."""
    table = MODE01_DEFINITIONS if definitions is None else definitions
    return frozenset(d.pid for d in table if d.supported_by(vehicle))
