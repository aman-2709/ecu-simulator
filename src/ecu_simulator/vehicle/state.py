"""Vehicle state: common signals plus exactly one powertrain.

``CommonState`` holds what every vehicle has. The powertrain supplies the rest, so an ICE
vehicle has ``engine.*`` and no ``battery.soc``, and a battery-electric vehicle the other
way round. Values are physical quantities (rpm, per cent, degrees Celsius); turning them
into wire bytes is the protocol layer's job.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from ecu_simulator.vehicle.signals import UnknownSignalError, signal_paths, split


@dataclass(slots=True)
class CommonState:
    """Signals every vehicle has, whatever its powertrain."""

    namespace = "vehicle"

    vin: str
    speed: int = 0  # km/h
    ambient_temp: float = 20.0  # degrees Celsius
    odometer: int = 0  # km
    battery_voltage: float = 12.6  # V, the control-module supply, not a traction battery
    # Which OBD standard the vehicle reports conforming to. A coded value, not a claim
    # this project makes about itself; 1 is the value commonly reported for OBD-II.
    obd_standard: int = 1


@dataclass(slots=True)
class IceState:
    """Internal combustion engine signals."""

    namespace = "engine"

    rpm: int = 0
    coolant_temp: float = 20.0  # degrees Celsius
    intake_temp: float = 20.0
    engine_load: float = 0.0  # per cent
    throttle: float = 0.0  # per cent
    maf: float = 0.0  # g/s
    map: int = 100  # intake manifold absolute pressure, kPa
    timing_advance: float = 0.0  # degrees before top dead centre
    short_fuel_trim: float = 0.0  # per cent
    long_fuel_trim: float = 0.0  # per cent
    runtime: int = 0  # seconds since engine start
    fuel_level: int = 50  # per cent
    fuel_type: int = 1  # SAE J1979 fuel type coding; 1 is gasoline


@dataclass(slots=True)
class TractionBattery:
    namespace = "battery"

    soc: float = 50.0  # per cent
    voltage: float = 400.0  # V
    current: float = 0.0  # A
    temp: float = 20.0  # degrees Celsius


@dataclass(slots=True)
class EMotor:
    namespace = "motor"

    rpm: int = 0
    torque: float = 0.0  # Nm
    temp: float = 20.0  # degrees Celsius


@dataclass(slots=True)
class Charging:
    namespace = "charging"

    active: bool = False
    power: float = 0.0  # kW


@dataclass(slots=True)
class IcePowertrain:
    kind = "ice"
    engine: IceState = field(default_factory=IceState)

    def components(self) -> tuple[Any, ...]:
        return (self.engine,)


@dataclass(slots=True)
class HevPowertrain:
    kind = "hev"
    engine: IceState = field(default_factory=IceState)
    battery: TractionBattery = field(default_factory=TractionBattery)
    motor: EMotor = field(default_factory=EMotor)
    charging: Charging = field(default_factory=Charging)

    def components(self) -> tuple[Any, ...]:
        return (self.engine, self.battery, self.motor, self.charging)


@dataclass(slots=True)
class BevPowertrain:
    kind = "bev"
    battery: TractionBattery = field(default_factory=TractionBattery)
    motor: EMotor = field(default_factory=EMotor)
    charging: Charging = field(default_factory=Charging)

    def components(self) -> tuple[Any, ...]:
        return (self.battery, self.motor, self.charging)


Powertrain = IcePowertrain | HevPowertrain | BevPowertrain

POWERTRAINS: dict[str, type[IcePowertrain] | type[HevPowertrain] | type[BevPowertrain]] = {
    IcePowertrain.kind: IcePowertrain,
    HevPowertrain.kind: HevPowertrain,
    BevPowertrain.kind: BevPowertrain,
}


def signal_types(kind: str) -> dict[str, type]:
    """Every signal path a vehicle of this powertrain kind has, mapped to its type.

    A query over the model, answerable without a configured vehicle, so configuration can
    reject a scenario that names a signal this vehicle does not have -- or one it has but
    that no generator could drive, like a VIN -- before anything is built.
    """
    powertrain = POWERTRAINS[kind]()
    components = (CommonState(vin=""), *powertrain.components())
    return {
        f"{component.namespace}.{f.name}": type(getattr(component, f.name))
        for component in components
        for f in dataclasses.fields(component)
    }


class VehicleState:
    """The composed state, addressed by dotted signal path."""

    def __init__(self, common: CommonState, powertrain: Powertrain) -> None:
        self.common = common
        self.powertrain = powertrain
        self._components: dict[str, Any] = {c.namespace: c for c in (common, *powertrain.components())}

    def __repr__(self) -> str:
        return f"VehicleState({self.powertrain.kind}, vin={self.common.vin!r})"

    @property
    def signals(self) -> dict[str, Any]:
        """Every signal path on this vehicle mapped to its current value."""
        paths: dict[str, Any] = {}
        for component in self._components.values():
            paths.update(signal_paths(component))
        return paths

    def has(self, path: str) -> bool:
        try:
            namespace, name = split(path)
        except UnknownSignalError:
            return False
        component = self._components.get(namespace)
        return component is not None and any(f.name == name for f in dataclasses.fields(component))

    def get(self, path: str) -> Any:
        """Current value of ``path``. Reading never changes state."""
        component, name = self._locate(path)
        return getattr(component, name)

    def set(self, path: str, value: Any) -> None:
        component, name = self._locate(path)
        setattr(component, name, value)

    def _locate(self, path: str) -> tuple[Any, str]:
        namespace, name = split(path)
        component = self._components.get(namespace)
        if component is None or not any(f.name == name for f in dataclasses.fields(component)):
            raise UnknownSignalError(path, self.signals.keys())
        return component, name
