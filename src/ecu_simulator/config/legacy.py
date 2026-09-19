"""The data the frozen legacy OBD and UDS modules read.

A temporary seam. Those modules were written around a module-global configuration loaded
at import; rather than rewrite them in this phase, they read a :class:`LegacyData` whose
accessors have the same names the old configuration module had, and the runtime replaces
it from the profile at startup. Phase 5 deletes the legacy OBD package and Phase 6 the UDS
package, and their replacements read :class:`~ecu_simulator.vehicle.VehicleState` and the
per-ECU configuration directly; this module goes with them.

Because the legacy modules keep the data in globals, one process serves one ECU's vehicle
data. That is true of V1.0 anyway, and the profile schema is already multi-ECU for the
phases that remove the limitation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ecu_simulator.config.schema import EcuConfig, Profile


@dataclass(frozen=True, slots=True)
class LegacyData:
    """Accessors named as the removed configuration module named them."""

    vin: str
    ecu_name: str
    fuel_level: int
    fuel_type: int
    dtcs: list[str] = field(default_factory=list)

    def get_vin(self) -> str:
        return self.vin

    def get_ecu_name(self) -> str:
        return self.ecu_name

    def get_fuel_level(self) -> int:
        return self.fuel_level

    def get_fuel_type(self) -> int:
        return self.fuel_type

    def get_dtcs(self) -> list[str]:
        return list(self.dtcs)


# The Phase 0 characterization baseline, used until a profile is loaded. It matches
# profiles/ice_default.yaml so that importing a legacy module without a profile still
# produces the bytes the golden tests pin.
DEFAULT_DATA = LegacyData(
    vin="TESTVIN0123456789",
    ecu_name="ECU_SIMULATOR",
    fuel_level=50,
    fuel_type=1,
    dtcs=["B1477", "P0001"],
)


def legacy_data(profile: Profile, ecu: EcuConfig) -> LegacyData:
    """The validated profile reduced to what the legacy modules ask for."""
    engine = profile.vehicle.engine
    return LegacyData(
        vin=profile.vehicle.vin,
        ecu_name=ecu.name,
        fuel_level=engine.fuel_level if engine is not None else DEFAULT_DATA.fuel_level,
        fuel_type=engine.fuel_type if engine is not None else DEFAULT_DATA.fuel_type,
        dtcs=list(ecu.dtcs),
    )
