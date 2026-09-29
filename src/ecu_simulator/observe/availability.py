"""Which signals have no source in a loaded profile (0010 §5, ninth revision).

A signal has a source when the profile schema can set it or the profile's scenario drives
it. Anything else holds the state model's default for the whole run, which is not a
measurement, so the observer API lists it as ``unavailable`` and the page shows "—".
``signals`` still carries the stored value: the list is additive, and the V1.0 schema,
state model and scenario engine are read here, never changed.

Computed once per runtime by the API server, never per request or per state message.
"""

from __future__ import annotations

import typing
from collections.abc import Iterable

from ecu_simulator.config.schema import Base, Profile, VehicleConfig
from ecu_simulator.vehicle import signal_types


def _section(annotation: object) -> type[Base] | None:
    """The nested schema model a ``VehicleConfig`` field holds (``EngineConfig | None``), if any."""
    for candidate in (annotation, *typing.get_args(annotation)):
        if isinstance(candidate, type) and issubclass(candidate, Base):
            return candidate
    return None


def settable(paths: Iterable[str]) -> frozenset[str]:
    """The given signal paths a profile's ``vehicle`` section can set.

    A plain ``VehicleConfig`` field sets ``vehicle.<field>``; a section field (``engine``,
    ``battery``) sets ``<section>.<field>`` for each of its model's fields. This mirrors the
    namespaces of the state model, and ``app.build_vehicle`` copies each of them over.
    Schema fields that are not signals (``vehicle.type``) drop out on the intersection.
    """
    schema: set[str] = set()
    for name, field in VehicleConfig.model_fields.items():
        section = _section(field.annotation)
        if section is None:
            schema.add(f"vehicle.{name}")
        else:
            schema.update(f"{name}.{sub}" for sub in section.model_fields)
    return frozenset(paths) & schema


def unavailable(profile: Profile) -> tuple[str, ...]:
    """Sorted signal paths on this vehicle that neither the schema nor the scenario can set."""
    paths = signal_types(profile.vehicle.type)
    driven = {signal.path for signal in profile.scenario.signals}
    return tuple(sorted(set(paths) - settable(paths) - driven))
