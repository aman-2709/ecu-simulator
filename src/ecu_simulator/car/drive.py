"""Which signals of a DBC message a whole-car run animates, and the physical values it gives them.

A car's DBC has hundreds of signals and the simulator models a handful of quantities, so the drive recognises a few
signals by unit and name and leaves every other one at its DBC default. Speed and engine speed follow the simulated
vehicle; steering angle and an accelerator pedal follow a repeatable pattern in elapsed time. Brakes and gear are
deliberately not animated, and neither is a multiplexer selector or a member of a multiplexed branch, nor a speed or
engine speed that is a set-point, target, limit or relative value. ``drive_sampler`` returns the physical values of the
recognised signals only, for ``ImportedDb.encode`` to fill the rest.
"""

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ecu_simulator.scenario.generators import SineSignal, SteppedSignal
from ecu_simulator.vehicle import VehicleState

SPEED_PATH = "vehicle.speed"  # km/h
RPM_PATH = "engine.rpm"

# Accepted unit spellings, compared after stripping spaces and lower-casing. DBC authors vary ("km/h", "kph", "KPH",
# "Deg"), so every spelling the drive recognises is listed here and nowhere else.
_KMH_UNITS = frozenset({"km/h", "kph"})
_NAMED_SPEED_UNITS = frozenset({"m/s", "mph"})  # also used by signals that are not a speed, so the name must say SPEED
_RPM_UNITS = frozenset({"rpm"})
_ANGLE_UNITS = frozenset({"deg"})
_PERCENT_UNITS = frozenset({"%"})

# km/h per one of the signal's unit: the vehicle's speed is divided by it.
_KMH_PER_UNIT = {"km/h": 1.0, "kph": 1.0, "m/s": 3.6, "mph": 1.609344}

_STEERING_NAME = re.compile(r"STEER.*ANGLE", re.IGNORECASE)

# Whole name tokens, upper-cased, that mark a speed or rpm signal as something other than the vehicle's own: a
# set-point, a target, a request, a limit, an offset, a relative speed or the decimal part of a speed. Such a signal is
# not recognised and keeps its DBC default. Steering and pedal signals are not checked against it.
_NOT_EGO_TOKENS = frozenset({"SET", "REL", "TGT", "TARGET", "TRGT", "REQ", "LIMIT", "OFFSET", "DECIMAL", "CRUISE"})

# A name's tokens are what an underscore, a lower-case letter or digit followed by a capital, or the last capital of a
# capitals run followed by a capital and a lower-case letter, separates: ``HDA_VSetReq`` is HDA, V, Set, Req.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

# Pure functions of elapsed seconds: no randomness and no wall clock, so a rerun repeats exactly. The paths only label
# what the generators drive; nothing reads them.
_STEERING = SineSignal(type="sine", path="drive.steering", centre=0, amplitude=30, period=20)
_PEDAL = SteppedSignal(type="stepped", path="drive.pedal", values=[0, 12, 0], interval=10)


class DriveRole(StrEnum):
    SPEED = "speed"
    RPM = "rpm"
    STEERING = "steering"
    PEDAL = "pedal"


def _unit(info: Mapping[str, Any]) -> str:
    return (info.get("unit") or "").strip().lower()


def _is_ego_name(name: str) -> bool:
    """False when a token of ``name`` marks it as a set-point, target, limit or relative value."""
    tokens = {token.upper() for part in name.split("_") for token in _CAMEL_BOUNDARY.split(part)}
    return tokens.isdisjoint(_NOT_EGO_TOKENS)


def classify_signal(info: Mapping[str, Any]) -> DriveRole | None:
    """The role of one signal dict from ``ImportedDb.message``, or None when the drive leaves it at its default.

    A multiplexer selector (``is_mux``) and a member of a multiplexed branch (``mux`` names its selector) are never
    driven: the encoder rejects a value for a branch member the default selector does not choose.
    """
    if info.get("is_mux") or info.get("mux"):
        return None
    unit = _unit(info)
    label = str(info["name"])
    name = label.upper()
    if unit in _KMH_UNITS or (unit in _NAMED_SPEED_UNITS and "SPEED" in name):
        return DriveRole.SPEED if _is_ego_name(label) else None
    if unit in _RPM_UNITS:
        return DriveRole.RPM if _is_ego_name(label) else None
    if unit in _ANGLE_UNITS and _STEERING_NAME.search(name):
        return DriveRole.STEERING
    if unit in _PERCENT_UNITS and "PEDAL" in name and "BRAKE" not in name:
        return DriveRole.PEDAL
    return None


@dataclass(frozen=True, slots=True)
class _Driven:
    name: str
    role: DriveRole
    divisor: float
    low: float | None
    high: float | None

    def value(self, base: float) -> float | None:
        """``base`` in this signal's unit and range, or None when it is not a finite number."""
        value = base / self.divisor
        if self.low is not None:
            value = max(value, self.low)
        if self.high is not None:
            value = min(value, self.high)
        return value if math.isfinite(value) else None


def drive_sampler(
    message: Mapping[str, Any], vehicle: VehicleState, elapsed: Callable[[], float]
) -> Callable[[], dict[str, float]]:
    """A callable returning the physical values of the message's recognised signals at this moment.

    Signals are classified once. Each call reads the vehicle and ``elapsed()`` afresh, so every wheel of one frame
    carries the same speed. A vehicle without ``engine.rpm`` leaves an rpm signal at its default, and a value that is
    not finite is left out rather than encoded.
    """
    driven = [
        _Driven(
            name=info["name"],
            role=role,
            divisor=_KMH_PER_UNIT[_unit(info)] if role is DriveRole.SPEED else 1.0,
            low=info.get("min"),
            high=info.get("max"),
        )
        for info in message["signals"]
        if (role := classify_signal(info)) is not None and (role is not DriveRole.RPM or vehicle.has(RPM_PATH))
    ]
    roles = {d.role for d in driven}
    readers: dict[DriveRole, Callable[[float], float]] = {
        DriveRole.SPEED: lambda _t: float(vehicle.get(SPEED_PATH)),
        DriveRole.RPM: lambda _t: float(vehicle.get(RPM_PATH)),
        DriveRole.STEERING: _STEERING.value_at,
        DriveRole.PEDAL: _PEDAL.value_at,
    }

    def sample() -> dict[str, float]:
        now = elapsed()
        base = {role: readers[role](now) for role in roles}
        values: dict[str, float] = {}
        for d in driven:
            value = d.value(base[d.role])
            if value is not None:
                values[d.name] = value
        return values

    return sample
