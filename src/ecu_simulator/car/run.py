"""A whole car on its CAN bus: every transmittable message of one DBC, with valid counters, checksums and periods.

``prepare_car`` reads the profile's ``car`` section and decides, before any socket is opened, what a run sends. The
DBC comes from the catalog platform's bus or from an upload given by digest (the upload's file name is display text
and never a path). The integrity rule of a message is, best first: the rule file's rule for the messages it matches,
the profile's ``rule``, the catalog's rule for that DBC, none. Each message's period comes from ``resolve_period``.

A message that cannot be sent is listed with the reason and the run goes on: CAN FD, a SecOC authenticator, a layout
error, a collision with the simulated ECUs' own diagnostic identifiers, or an encode failure in a trial encode of the
first drive sample or of each driven signal alone at its lowest and its highest value. The run is refused when nothing
is left to send, or when the bus-load gate refuses the link.

Every frame is sampled as ``sync()``, the drive values, ``ImportedDb.encode`` and the message's own ``Stamper``, so a
counter advances once per transmitted frame and runs on across a loop of the scenario. An encode error at run time is
not caught: it leaves the sample, stops the publisher and ends the run with that error.
"""

import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ecu_simulator.app import RuntimeConfig, diagnostic_ids
from ecu_simulator.car.busload import VIRTUAL_BITRATE, BusLoadError, LinkInfo, check_bus_load, read_link
from ecu_simulator.car.catalog import Catalog, DbcEntry, Platform, load_catalog, rule_for_sha
from ecu_simulator.car.drive import drive_sampler
from ecu_simulator.car.integrity import IntegrityState, MessagePlan, Stamper, plan_message
from ecu_simulator.car.periods import Period, resolve_period
from ecu_simulator.car.rules import RuleStore, UserRule
from ecu_simulator.clock import Clock
from ecu_simulator.config import ConfigError
from ecu_simulator.config.schema import CarConfig
from ecu_simulator.publisher import CyclicFrame, wire_id
from ecu_simulator.vehicle import VehicleState

CLASSICAL_MAX_DLC = 8

# opendbc's SecOC DBC (toyota_secoc_pt_generated) marks every authenticated message with a 28-bit signal named exactly
# AUTHENTICATOR, and its SECOC_SYNCHRONIZATION message carries one as well; no signal there is named SECOC. The wider
# pattern also catches a customer DBC that names the MAC or freshness value after SecOC.
_SECOC = re.compile("SECOC|AUTHENTICATOR", re.IGNORECASE)

SKIP_CAN_FD = "CAN FD"
SKIP_SECOC = "SecOC authenticator"


class CarDatabase(Protocol):
    """What the run reads of an ``ImportedDb``."""

    def catalog(self, filename: str) -> dict[str, Any]: ...

    def encode(self, name: str, values: dict[str, float], *, integrity_ok: bool = False) -> bytes: ...


@dataclass(frozen=True, slots=True)
class CarMessage:
    """One message of the car's DBC as the run treats it; ``skipped`` is the reason it is not sent, or None."""

    name: str
    id: int
    extended: bool
    dlc: int
    period: Period
    integrity: IntegrityState
    detail: str | None
    counters: tuple[str, ...]
    checksums: tuple[str, ...]
    skipped: str | None

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name, "id": self.id, "extended": self.extended, "dlc": self.dlc,
            "period_ms": self.period.ms, "period_source": self.period.source.value, "clamped": self.period.clamped,
            "integrity": self.integrity.value, "detail": self.detail,
            "counters": list(self.counters), "checksums": list(self.checksums), "skipped": self.skipped,
        }


@dataclass
class PreparedCar:
    metadata: dict[str, Any]
    frames: list[CyclicFrame]


@dataclass(frozen=True, slots=True)
class _Source:
    database: CarDatabase
    filename: str
    sha256: str
    entry: DbcEntry | None
    platform: Platform | None


def _catalog_entry(catalog: Catalog, sha256: str) -> DbcEntry | None:
    return next((e for e in catalog.dbcs.values() if sha256 in (e.sha256, e.source_sha256)), None)


def _source(car: CarConfig, catalog: Catalog, database: CarDatabase | None) -> _Source:
    platform: Platform | None = None
    if car.platform is not None:
        platform = catalog.platforms[car.platform]
        bundled = catalog.dbcs[platform.buses[car.bus]]
        entry: DbcEntry | None = bundled
        sha256, filename = bundled.sha256, Path(bundled.file).name
    else:
        assert car.sha256 is not None and car.filename is not None  # the schema requires both for an upload
        sha256, filename = car.sha256, car.filename
        entry = _catalog_entry(catalog, sha256)
    if database is None:
        from ecu_simulator.dbc import DbcStore

        try:
            database = DbcStore().load(sha256)
        except (ValueError, OSError) as error:
            raise ConfigError(f"car dbc {filename!r} ({sha256}): {error}") from error
    return _Source(database, filename, sha256, entry, platform)


def _user_rules(car: CarConfig, messages: Sequence[Mapping[str, Any]]) -> Mapping[str, UserRule]:
    if car.rule_file is None:
        return {}
    try:
        return RuleStore().load(car.rule_file).resolve(messages)
    except (ValueError, OSError) as error:  # RuleError is a ValueError and carries the dotted path in its text
        raise ConfigError(f"car.rule_file: {error}") from error


def _skip_before_plan(message: Mapping[str, Any]) -> str | None:
    if int(message["dlc"]) > CLASSICAL_MAX_DLC:
        return SKIP_CAN_FD
    if message["layout_issue"]:
        return f"layout error: {message['layout_issue']}"
    if any(_SECOC.search(str(signal["name"])) for signal in message["signals"]):
        return SKIP_SECOC
    return None


def _sampler(
    name: str,
    database: CarDatabase,
    values: Callable[[], dict[str, float]],
    stamper: Stamper,
    sync: Callable[[], None] | None,
) -> Callable[[], bytes]:
    def sample() -> bytes:
        if sync is not None:
            sync()
        return stamper.stamp(database.encode(name, values(), integrity_ok=True))

    return sample


def _trial_values(message: Mapping[str, Any], first: dict[str, float]) -> list[dict[str, float]]:
    """The first drive sample, then each driven signal alone at its lowest and highest value (where it has one).

    The drive clamps a driven value into the signal's range, so the ends of that range are the values a run can reach
    that the first sample may not; every other signal stays at its default, as the encoder fills it.
    """
    limits = {str(s["name"]): (s.get("min"), s.get("max")) for s in message["signals"]}
    trials = [first]
    for signal in first:
        trials += [{signal: float(end)} for end in limits[signal] if end is not None]
    return trials


def prepare_car(
    config: RuntimeConfig,
    vehicle: VehicleState,
    clock: Clock,
    *,
    sync: Callable[[], None] | None = None,
    link: LinkInfo | None = None,
    database: CarDatabase | None = None,
) -> PreparedCar | None:
    """The frames and metadata of the profile's car, or None when the profile has no ``car``.

    Raises ``ConfigError`` when the DBC or rule file cannot be loaded or does not fit, when no message can be sent, and
    when the link cannot be read or the bus-load gate refuses it (the ``BusLoadError`` is the cause). ``link`` defaults
    to ``read_link(config.interface)``.
    """
    car = config.profile.car
    if car is None:
        return None
    catalog = load_catalog()
    source = _source(car, catalog, database)
    messages: list[Mapping[str, Any]] = source.database.catalog(source.filename)["messages"]
    user_rules = _user_rules(car, messages)
    rule = car.rule or rule_for_sha(source.sha256)
    rates = catalog.rates_hz.get(source.platform.brand, {}) if source.platform is not None else {}
    occupied = diagnostic_ids(config)
    origin = clock.now()

    def elapsed() -> float:
        return clock.now() - origin

    listed: list[CarMessage] = []
    frames: list[CyclicFrame] = []
    for message in messages:
        name, can_id, extended = str(message["name"]), int(message["id"]), bool(message["extended"])
        period = resolve_period(
            override=car.periods.get(name),
            measured_ms=car.log_periods.get(name),
            dbc_ms=message["period_ms"],
            table_hz=rates.get(name),
        )
        plan: MessagePlan | None = None
        skipped = _skip_before_plan(message)
        if skipped is None:
            plan = plan_message(message, rule=rule, user=user_rules.get(name))
            if plan.state is IntegrityState.UNSUPPORTED:
                skipped = f"layout error: {plan.detail}"
            elif (can_id, extended) in occupied:
                skipped = f"collides with {occupied[can_id, extended]} on 0x{can_id:X}"
        if plan is not None and skipped is None:
            values = drive_sampler(message, vehicle, elapsed)
            try:
                for trial in _trial_values(message, values()):
                    source.database.encode(name, trial, integrity_ok=True)
            except ValueError as error:
                skipped = f"encode failed: {error}"
            else:
                sample = _sampler(name, source.database, values, Stamper(plan), sync)
                frames.append(CyclicFrame(name, wire_id(can_id, extended), period.ms / 1000, sample))
                listed.append(CarMessage(
                    name, can_id, extended, int(message["dlc"]), period, plan.state, plan.detail,
                    tuple(c.signal for c in plan.counters), tuple(c.signal for c in plan.checksums), None,
                ))
                continue
        listed.append(CarMessage(
            name, can_id, extended, int(message["dlc"]), period, IntegrityState.UNSUPPORTED, None, (), (), skipped,
        ))

    if not frames:
        reasons = Counter(m.skipped.split(":")[0] if m.skipped else "" for m in listed)
        summary = ", ".join(f"{count} {reason}" for reason, count in reasons.items()) or "the DBC has no messages"
        raise ConfigError(f"car dbc {source.filename!r}: no message can be transmitted ({summary})")

    if link is None:
        try:
            link = read_link(config.interface)
        except ValueError as error:
            raise ConfigError(f"car: {error}") from error
    sent = [(m.name, m.dlc, m.extended, m.period.ms / 1000) for m in listed if m.skipped is None]
    try:
        estimate = check_bus_load(sent, link)
    except BusLoadError as error:
        raise ConfigError(f"car: {error}") from error

    metadata: dict[str, Any] = {
        "platform": source.platform.id if source.platform is not None else None,
        "dbc": {"filename": source.filename, "sha256": source.sha256,
                "name": source.entry.name if source.entry is not None else None},
        "revision": catalog.revision if source.entry is not None else None,
        "rule": rule,
        "bus_load": {
            "estimate": estimate,
            "bitrate": VIRTUAL_BITRATE if link.virtual else link.bitrate,
            "assumed": link.virtual,
            "worst_case": True,
        },
        "messages": [m.metadata() for m in listed],
    }
    return PreparedCar(metadata, frames)
