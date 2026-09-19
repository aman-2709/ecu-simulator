"""Profile schema, validated by Pydantic v2.

Every constraint here is project input validation: it rejects a profile this simulator
cannot serve faithfully. None of it is a standards conformance claim, and none of it adds
`standards validated` to any row in docs/conformance.md. In particular the accepted
diagnostic-trouble-code representation is the one the existing encoder already accepts and
the Phase 0 characterization tests already pin (DEV-13); the rule is moved to load time so
a malformed entry is reported with its path instead of being silently skipped.

Ranges on vehicle values exist for the same reason (DEV-14): the legacy code substituted a
default at request time, or passed validation and raised `OverflowError` while encoding.

The schema is multi-ECU from V1.0. The shipped profile defines one ECU; a second is added
by adding a key under `ecus`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ecu_simulator.config.errors import ConfigError

SCHEMA_VERSION = 1

CAN_SFF_MAX = 0x7FF
VIN_MAX_LENGTH = 17
ECU_NAME_MAX_LENGTH = 20
MAX_DTCS = 255
FUEL_TYPE_MAX = 23

KNOWN_PROTOCOLS = ("obd", "uds")
KNOWN_VEHICLE_TYPES = ("ice", "hev", "bev")

# The grammar the legacy encoder accepts, pinned by tests/characterization/test_dtc_golden.py:
# a group letter, a type digit, then three hexadecimal digits. Project validation, not J2012.
DTC_GROUPS = ("P", "C", "B", "U")
DTC_TYPES = ("0", "1", "2", "3")
DTC_HEX = "0123456789ABCDEF"

CanId = Annotated[int, Field(ge=0, le=CAN_SFF_MAX)]
Byte = Annotated[int, Field(ge=0, le=0xFF)]
Percent = Annotated[int, Field(ge=0, le=100)]


class Base(BaseModel):
    """Unknown keys are a mistake in a configuration file, never something to ignore."""

    model_config = ConfigDict(extra="forbid", strict=False, validate_assignment=True)


class TransportConfig(Base):
    interface: str = Field(min_length=1)


class EngineConfig(Base):
    fuel_level: Percent = 50
    fuel_type: Annotated[int, Field(ge=1, le=FUEL_TYPE_MAX)] = 1
    rpm: Annotated[int, Field(ge=0, le=20000)] = 0
    coolant_temp: float = 20.0
    intake_temp: float = 20.0
    engine_load: Annotated[float, Field(ge=0.0, le=100.0)] = 0.0
    throttle: Annotated[float, Field(ge=0.0, le=100.0)] = 0.0
    maf: Annotated[float, Field(ge=0.0, le=655.35)] = 0.0
    map: Annotated[int, Field(ge=0, le=255)] = 100
    timing_advance: Annotated[float, Field(ge=-64.0, le=63.5)] = 0.0  # A / 2 - 64
    # -100 and 99.21875 are exactly the ends of the byte field: (A - 128) * 100 / 128.
    short_fuel_trim: Annotated[float, Field(ge=-100.0, le=99.21875)] = 0.0
    long_fuel_trim: Annotated[float, Field(ge=-100.0, le=99.21875)] = 0.0
    runtime: Annotated[int, Field(ge=0, le=65535)] = 0


class BatteryConfig(Base):
    soc: Annotated[float, Field(ge=0.0, le=100.0)] = 50.0
    voltage: Annotated[float, Field(ge=0.0)] = 400.0
    temp: float = 20.0


class VehicleConfig(Base):
    vin: str = Field(min_length=1, max_length=VIN_MAX_LENGTH)
    type: Literal["ice", "hev", "bev"]
    engine: EngineConfig | None = None
    battery: BatteryConfig | None = None
    speed: Annotated[int, Field(ge=0, le=255)] = 0
    ambient_temp: float = 20.0
    battery_voltage: Annotated[float, Field(ge=0.0, le=65.535)] = 12.6
    obd_standard: Annotated[int, Field(ge=1, le=255)] = 1

    @field_validator("vin")
    @classmethod
    def vin_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("vin must not be blank")
        return value

    @model_validator(mode="after")
    def components_match_the_vehicle_type(self) -> VehicleConfig:
        if self.type == "bev" and self.engine is not None:
            raise ValueError("a bev vehicle has no engine section")
        if self.type == "ice" and self.battery is not None:
            raise ValueError("an ice vehicle has no traction battery section")
        return self


class EndpointConfigModel(Base):
    name: str = Field(min_length=1)
    rx: CanId
    tx: CanId
    addressing: Literal["physical", "functional"]
    protocols: list[str] = Field(min_length=1)
    answer_unsupported: bool | None = None
    tx_padding: bool = False
    pad_byte: Byte = 0x00
    reply_via: str | None = None

    @field_validator("protocols")
    @classmethod
    def protocols_are_known(cls, value: list[str]) -> list[str]:
        unknown = [p for p in value if p not in KNOWN_PROTOCOLS]
        if unknown:
            raise ValueError(f"unknown protocol(s) {unknown}; known protocols are {list(KNOWN_PROTOCOLS)}")
        if len(set(value)) != len(value):
            raise ValueError(f"duplicate protocol in {value}")
        return value

    @model_validator(mode="after")
    def addresses_differ(self) -> EndpointConfigModel:
        if self.rx == self.tx:
            raise ValueError(f"rx and tx must differ (both 0x{self.rx:X})")
        return self

    @property
    def functional(self) -> bool:
        return self.addressing == "functional"

    @property
    def answers_unsupported(self) -> bool:
        """Default: a physical request names one ECU and is answered; a broadcast is not."""
        if self.answer_unsupported is not None:
            return self.answer_unsupported
        return not self.functional


class DtcConfigModel(Base):
    """One configured trouble code and the state the simulator starts it in.

    A bare string is the shorthand for a code that is pending and confirmed and asks for
    no indicator, which is the state that makes service 03 answer exactly the bytes it
    answered before Phase 6. The long form names the flags the shared DtcStore models; it
    models no others, because no others are state this project can produce honestly. See
    docs/decisions/0004-phase-6-dtc-evidence.md.
    """

    code: str
    pending: bool = True
    confirmed: bool = True
    indicator_requested: bool = False

    @model_validator(mode="before")
    @classmethod
    def accept_a_bare_code(cls, value: Any) -> Any:
        return {"code": value} if isinstance(value, str) else value

    @field_validator("code")
    @classmethod
    def code_is_encodable(cls, value: str) -> str:
        if not _is_encodable_dtc(value):
            raise ValueError(
                f"dtc {value!r} cannot be encoded: expected a group letter "
                f"{list(DTC_GROUPS)}, a type digit {list(DTC_TYPES)}, then three "
                "uppercase hexadecimal digits, for example 'P0001'"
            )
        return value


class EcuConfig(Base):
    name: str = Field(min_length=1, max_length=ECU_NAME_MAX_LENGTH)
    endpoints: list[EndpointConfigModel] = Field(min_length=1)
    dtcs: list[DtcConfigModel] = Field(default_factory=list, max_length=MAX_DTCS)
    dids: dict[str, str] = Field(default_factory=dict)

    @field_validator("dtcs")
    @classmethod
    def dtc_codes_are_unique(cls, value: list[DtcConfigModel]) -> list[DtcConfigModel]:
        codes = [entry.code for entry in value]
        duplicates = sorted({code for code in codes if codes.count(code) > 1})
        if duplicates:
            raise ValueError(f"duplicate dtc(s) {duplicates}")
        return value

    @field_validator("dids")
    @classmethod
    def dids_are_in_range(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            try:
                number = int(key, 16)
            except ValueError as error:
                raise ValueError(f"did {key!r} is not a hexadecimal identifier") from error
            if not 0 <= number <= 0xFFFF:
                raise ValueError(f"did {key!r} is outside the two-byte range")
        return value

    @model_validator(mode="after")
    def endpoints_are_consistent(self) -> EcuConfig:
        names = [e.name for e in self.endpoints]
        duplicate_names = sorted({n for n in names if names.count(n) > 1})
        if duplicate_names:
            raise ValueError(f"duplicate endpoint name(s) {duplicate_names}")
        pairs = [(e.rx, e.tx) for e in self.endpoints]
        duplicate_pairs = sorted({f"rx 0x{rx:X} / tx 0x{tx:X}" for rx, tx in pairs if pairs.count((rx, tx)) > 1})
        if duplicate_pairs:
            raise ValueError(f"duplicate endpoint address pair(s) {duplicate_pairs}")
        for endpoint in self.endpoints:
            if endpoint.reply_via is not None and endpoint.reply_via not in names:
                raise ValueError(
                    f"endpoint {endpoint.name!r} has reply_via {endpoint.reply_via!r}, which is not an endpoint"
                )
        return self


class Profile(Base):
    version: Literal[1]
    transport: TransportConfig
    vehicle: VehicleConfig
    ecus: dict[str, EcuConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def receive_addresses_are_unique_across_ecus(self) -> Profile:
        owners: dict[int, str] = {}
        for ecu_name, ecu in self.ecus.items():
            for endpoint in ecu.endpoints:
                owner = owners.get(endpoint.rx)
                if owner is not None and owner != ecu_name and endpoint.addressing == "physical":
                    raise ValueError(
                        f"receive identifier 0x{endpoint.rx:X} is claimed by both {owner!r} and {ecu_name!r}"
                    )
                if endpoint.addressing == "physical":
                    owners[endpoint.rx] = ecu_name
        return self


def _is_encodable_dtc(dtc: str) -> bool:
    """Exactly what ``dtc_utils.is_dtc_valid`` accepts, checked at load instead of silently skipped."""
    return (
        isinstance(dtc, str)
        and len(dtc) == 5
        and dtc[0] in DTC_GROUPS
        and dtc[1] in DTC_TYPES
        and all(c in DTC_HEX for c in dtc[2:5])
    )


def parse_profile(data: dict[str, Any], source: str | None = None) -> Profile:
    """Validate a parsed profile, reporting every problem with its path."""
    try:
        return Profile.model_validate(data)
    except ValidationError as error:
        raise ConfigError(_format(error, source)) from error


def _format(error: ValidationError, source: str | None) -> str:
    where = f"profile {source}" if source else "profile"
    lines = [f"{where} is invalid ({error.error_count()} problem(s)):"]
    for detail in error.errors():
        path = ".".join(str(part) for part in detail["loc"]) or "(root)"
        lines.append(f"  {path}: {detail['msg']}")
    return "\n".join(lines)
