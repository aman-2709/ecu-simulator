"""What the Car panel does with a working copy: pick a car, draft it, check it, review it and measure a log.

``car_draft`` edits the YAML working copy the way ``dbc_workflow.changed_draft`` does. ``check`` and ``transmittable``
run ``prepare_car`` on a throwaway clock and a fresh vehicle, so they open no socket; the only outside call is
``read_link``, which runs ``ip``. The DBC of an upload is found by its digest in the ``DbcStore`` and its file name is
display text that never reaches a path. Everything here blocks (DBC parsing, ``ip``, the candump parse), so the routes
run it in a worker thread.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ecu_simulator import app as simulator
from ecu_simulator.car.busload import (
    VIRTUAL_BITRATE,
    BusLoadError,
    LinkInfo,
    check_bus_load,
    estimate_load,
    read_link,
)
from ecu_simulator.car.catalog import load_catalog
from ecu_simulator.car.periods import measure_log_periods, resolve_period
from ecu_simulator.car.run import PreparedCar, prepare_car
from ecu_simulator.clock import SimulatedClock
from ecu_simulator.config import ConfigError, load_profile_text
from ecu_simulator.gui.dbc_workflow import edit_draft
from ecu_simulator.gui.replay import parse_recording
from ecu_simulator.vehicle import signal_types

if TYPE_CHECKING:
    from ecu_simulator.dbc import ImportedDb

SELECTION_KEYS = ("platform", "bus", "sha256", "filename", "rule", "rule_file", "periods", "log_periods")
_TEXT_KEYS = ("platform", "bus", "sha256", "filename", "rule", "rule_file")
_MAPPING_KEYS = ("periods", "log_periods")

# The layout of the shipped profiles, so that a car chosen from one of them does not reindent the rest of the file.
SHIPPED_LAYOUT = (2, 4, 2)

# The signals the demo profile (car_toyota_demo.yaml) drives, which a car's own speed and engine signals follow.
DRIVE_SIGNALS = {
    "vehicle.speed": "path: vehicle.speed\ntype: stepped\ninterval: 10\nvalues: [0, 40, 0]\n",
    "engine.rpm": "path: engine.rpm\ntype: stepped\ninterval: 10\nvalues: [800, 2000, 800]\n",
}

INTEGRITY_STATES = ("valid", "counter_only", "user_rule", "none")
MAX_WARNINGS = 20
_SHOWN = 60


def _shown(value: object) -> str:
    text = repr(value)
    return text if len(text) <= _SHOWN else text[:_SHOWN] + "..."


# --- the catalog ------------------------------------------------------------------------------------------------------


def catalog_view() -> dict[str, Any]:
    """The platforms of the bundled catalog by brand, as much as the Choose step shows: no DBC, ECU or rate data."""
    catalog = load_catalog()
    brands: dict[str, list[dict[str, Any]]] = {}
    for platform in sorted(catalog.platforms.values(), key=lambda p: p.id):
        brands.setdefault(platform.brand, []).append(
            {"id": platform.id, "names": list(platform.names), "buses": list(platform.buses)}
        )
    return {"revision": catalog.revision, "brands": dict(sorted(brands.items())), "skipped_count": len(catalog.skipped)}


# --- the draft --------------------------------------------------------------------------------------------------------


def _check_selection(selection: Mapping[str, Any]) -> None:
    unknown = [key for key in selection if key not in SELECTION_KEYS]
    if unknown:
        raise ValueError(f"Unknown car setting {_shown(unknown[0])}: use {', '.join(SELECTION_KEYS)}")
    for key in _TEXT_KEYS:
        if selection.get(key) is not None and not isinstance(selection[key], str):
            raise ValueError(f"car.{key} must be text")
    for key in _MAPPING_KEYS:
        if selection.get(key) is not None and not isinstance(selection[key], Mapping):
            raise ValueError(f"car.{key} must map message names to milliseconds")


def _set_car(root: Any, selection: Mapping[str, Any]) -> None:
    car = root.get("car")
    if not isinstance(car, MutableMapping):
        car = {}
        root["car"] = car
    # A new source replaces the old one: the platform and the upload exclude each other, and a bus belongs to its
    # platform, so it is dropped unless the selection names one.
    if selection.get("platform") is not None:
        for key in ("sha256", "filename"):
            car.pop(key, None)
        if "bus" not in selection:
            car.pop("bus", None)
    if selection.get("sha256") is not None:
        for key in ("platform", "bus"):
            car.pop(key, None)
    for key in SELECTION_KEYS:
        if key not in selection:
            continue
        value = selection[key]
        if value is None or (key in _MAPPING_KEYS and not value):
            car.pop(key, None)
        else:
            car[key] = dict(value) if key in _MAPPING_KEYS else value
    root.pop("restbus", None)
    root.pop("dbc", None)


def _add_drive(root: Any, paths: Sequence[str], parse: Callable[[str], Any]) -> None:
    scenario = root.get("scenario")
    if not isinstance(scenario, MutableMapping):
        scenario = {}
        root["scenario"] = scenario
    signals = scenario.get("signals")
    if signals is None:
        signals = []
        scenario["signals"] = signals
    for path in paths:
        signals.append(parse(DRIVE_SIGNALS[path]))


def car_draft(text: str, selection: Mapping[str, Any]) -> str:
    """The working copy with its ``car`` section set from ``selection``, comments and key order kept.

    A key in ``selection`` sets that key of ``car``; ``None`` or, for ``periods`` and ``log_periods``, an empty mapping
    removes it, and a mapping replaces the whole one. Choosing a ``platform`` or a ``sha256`` replaces the previous
    source. ``restbus`` and ``dbc`` are removed, because a profile has one source of bus traffic. The demo's speed and
    engine speed scenario signals are added for each the vehicle has and the working copy does not already drive.
    Raises ``ValueError`` for an unknown key and ``ConfigError`` when the result is not a valid profile.
    """
    _check_selection(selection)
    drafted = edit_draft(text, lambda root, _parse: _set_car(root, selection), SHIPPED_LAYOUT)
    profile = load_profile_text(drafted)
    available = signal_types(profile.vehicle.type)
    driven = {signal.path for signal in profile.scenario.signals}
    missing = [path for path in DRIVE_SIGNALS if path in available and path not in driven]
    if not missing:
        return drafted
    drafted = edit_draft(drafted, lambda root, parse: _add_drive(root, missing, parse), SHIPPED_LAYOUT)
    load_profile_text(drafted)
    return drafted


# --- the check --------------------------------------------------------------------------------------------------------


def integrity_counts(messages: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """How many messages are in each integrity state, and how many are skipped (a skipped one is in no state)."""
    counts = dict.fromkeys(INTEGRITY_STATES, 0)
    counts["skipped"] = 0
    for message in messages:
        counts["skipped" if message["skipped"] is not None else message["integrity"]] += 1
    return counts


def _prepare(text: str) -> tuple[simulator.RuntimeConfig, PreparedCar]:
    profile = load_profile_text(text)
    if profile.car is None:
        raise ConfigError("The working copy has no car section: choose a car first")
    config = simulator.RuntimeConfig.build(profile)
    # prepare_car refuses a link the bus-load gate refuses and then returns nothing, which would hide the messages
    # from a user who has to see them to fix the cause. A virtual stand-in link keeps the gate out of the way and
    # ``check`` applies it to the real link afterwards, with the same function and the same frames.
    stand_in = LinkInfo(config.interface, True, None, None)
    prepared = prepare_car(config, simulator.build_vehicle(config), SimulatedClock(), link=stand_in)
    assert prepared is not None  # the profile has a car section
    return config, prepared


def _bus_load(interface: str, messages: Sequence[Mapping[str, Any]], link: LinkInfo | None) -> dict[str, Any]:
    shown: dict[str, Any] = {
        "interface": interface, "estimate": None, "bitrate": None, "assumed": False, "worst_case": True,
        "refused": None, "over": [], "unavailable": None,
    }
    if link is None:
        try:
            link = read_link(interface)
        except ValueError as error:
            shown["unavailable"] = str(error)
            return shown
    shown.update(interface=link.interface, bitrate=VIRTUAL_BITRATE if link.virtual else link.bitrate,
                 assumed=link.virtual)
    sent = [(m["name"], m["dlc"], m["extended"], m["period_ms"] / 1000) for m in messages if m["skipped"] is None]
    try:
        shown["estimate"] = check_bus_load(sent, link)
    except BusLoadError as error:
        shown["refused"] = str(error)
        shown["over"] = [{"name": name, "share": share} for name, share in error.over]
        if link.bitrate is not None:
            shown["estimate"] = estimate_load(sent, link.bitrate)
    return shown


def _unknown_periods(periods: Mapping[str, int], messages: Sequence[Mapping[str, Any]]) -> list[str]:
    known = {message["name"] for message in messages}
    unknown = sorted(name for name in periods if name not in known)
    warnings = [
        f"periods: {_shown(name)} is not a message of this DBC, so its override is ignored"
        for name in unknown[:MAX_WARNINGS]
    ]
    if len(unknown) > MAX_WARNINGS:
        warnings.append(f"periods: {len(unknown) - MAX_WARNINGS} more names are not messages of this DBC")
    return warnings


def check(text: str, link: LinkInfo | None = None) -> dict[str, Any]:
    """What a run of the working copy's car would send, from ``prepare_car`` with nothing started.

    The bus load is judged against ``link``, which is read from the profile's interface when not given. A link the
    gate refuses, or one that cannot be read, is reported in ``bus_load`` and the messages are still listed. Raises
    ``ConfigError`` when the car cannot be prepared at all.
    """
    config, prepared = _prepare(text)
    metadata = prepared.metadata
    messages = metadata["messages"]
    assert config.profile.car is not None
    return {
        "platform": metadata["platform"], "dbc": metadata["dbc"], "revision": metadata["revision"],
        "rule": metadata["rule"], "messages": messages, "counts": integrity_counts(messages),
        "bus_load": _bus_load(config.interface, messages, link),
        "warnings": _unknown_periods(config.profile.car.periods, messages),
    }


# --- the review -------------------------------------------------------------------------------------------------------


def transmittable(text: str) -> list[dict[str, Any]]:
    """The messages a run would send, each with its first stamped frame as the review shows it.

    The car is generated: the bytes are one sample from a fresh run, not a recording. Raises ``ConfigError`` when no
    message can be transmitted.
    """
    _, prepared = _prepare(text)
    frames = {frame.name: frame for frame in prepared.frames}
    return [
        {"name": m["name"], "id": m["id"], "extended": m["extended"], "dlc": m["dlc"], "period_ms": m["period_ms"],
         "raw": frames[m["name"]].sample().hex(" ").upper()}
        for m in prepared.metadata["messages"] if m["skipped"] is None
    ]


# --- the run status ---------------------------------------------------------------------------------------------------


def run_status(status: Mapping[str, Any]) -> dict[str, Any]:
    """The Manager's status with a car run's metadata cut to a summary, for a page that polls it every 350 ms.

    The message list is served once, by ``GET /car/run``; the Manager keeps the whole of it.
    """
    car = status.get("car")
    if not car:
        return dict(status)
    summary = {
        "platform": car["platform"], "dbc": {"filename": car["dbc"]["filename"], "sha256": car["dbc"]["sha256"]},
        "revision": car["revision"], "rule": car["rule"], "bus_load": car["bus_load"],
        "counts": integrity_counts(car["messages"]),
    }
    return {**status, "car": summary}


# --- the recorded log -------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LogMeasurement:
    """Periods measured from a log, by message name; ``clamped`` names those moved into the profile's 10 to 60000 ms;
    ``unmatched`` lists the measured identifiers no message of the DBC has."""

    periods: dict[str, int]
    clamped: tuple[str, ...]
    unmatched: list[str]


def _identifier(can_id: int, extended: bool) -> str:
    return f"0x{can_id:08X}" if extended else f"0x{can_id:03X}"


def measure_log(db: ImportedDb, raw: bytes) -> LogMeasurement:
    """Measure each message's period in a candump log, matching on identifier and extended flag.

    Raises ``ValueError`` with the replay path's message when the log is not one Classical CAN recording.
    """
    measured = measure_log_periods(parse_recording(raw))
    names = {(m["id"], m["extended"]): m["name"] for m in db.catalog("")["messages"]}
    periods: dict[str, int] = {}
    clamped: list[str] = []
    for key, milliseconds in measured.items():
        if key in names:
            # The profile accepts whole milliseconds from 10 to 60000, and resolve_period rounds and clamps to that.
            period = resolve_period(override=None, measured_ms=milliseconds, dbc_ms=None, table_hz=None)
            periods[names[key]] = period.ms
            if period.clamped:
                clamped.append(names[key])
    unmatched = [_identifier(*key) for key in sorted(measured.keys() - names.keys(), key=lambda k: (k[1], k[0]))]
    return LogMeasurement(dict(sorted(periods.items())), tuple(sorted(clamped)), unmatched)


def log_periods(db: ImportedDb, raw: bytes) -> dict[str, int]:
    """Message name to measured period in milliseconds, for the messages of ``db`` the log measures."""
    return measure_log(db, raw).periods
