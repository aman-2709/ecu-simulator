"""DID and DTC provider registries: the UDS data-service extension points on an ECU."""

import pytest

from ecu_simulator.ecu import Ecu
from ecu_simulator.protocols.uds.providers import (
    DidProvider,
    DidRegistry,
    DidWriteAccess,
    DtcProvider,
    DtcRecord,
    DtcRegistry,
    ProviderConflictError,
    WritableDidProvider,
)


class StaticDids:
    def __init__(self, name, values):
        self.name = name
        self._values = dict(values)

    @property
    def data_identifiers(self):
        return frozenset(self._values)

    def read(self, did):
        return self._values.get(did)


class StaticDtcs:
    def __init__(self, name, records):
        self.name = name
        self._records = list(records)

    def dtc_records(self):
        return iter(self._records)


# --- DtcRecord ------------------------------------------------------------------------------


def test_dtc_record_encodes_three_byte_dtc_and_status():
    assert DtcRecord(0x947701, 0x2F).to_bytes() == b"\x94\x77\x01\x2f"


@pytest.mark.parametrize("dtc, status", [(0x1000000, 0x2F), (-1, 0x2F), (0x947701, 0x100)])
def test_dtc_record_rejects_out_of_range_values(dtc, status):
    with pytest.raises(ValueError):
        DtcRecord(dtc, status)


# --- DidRegistry -----------------------------------------------------------------------------


def test_did_registry_reads_through_the_owning_provider():
    registry = DidRegistry()
    vin = StaticDids("vin", {0xF190: b"TESTVIN0123456789"})
    registry.register(vin)
    assert registry.read(0xF190) == b"TESTVIN0123456789"
    assert registry.read(0xF191) is None
    assert registry.provider_for(0xF190) is vin
    assert dict(registry.data_identifiers) == {0xF190: "vin"}


def test_did_claimed_by_two_providers_is_a_conflict_and_registers_nothing():
    registry = DidRegistry()
    registry.register(StaticDids("a", {0xF190: b"x"}))
    with pytest.raises(ProviderConflictError, match=r"0xF190.*'a'.*'b'"):
        registry.register(StaticDids("b", {0xF190: b"y", 0xF187: b"z"}))
    assert registry.read(0xF187) is None


def test_did_registry_rejects_duplicate_names_empty_and_out_of_range_dids():
    registry = DidRegistry()
    registry.register(StaticDids("a", {0x0100: b"x"}))
    with pytest.raises(ProviderConflictError, match="'a'"):
        registry.register(StaticDids("a", {0x0200: b"y"}))
    with pytest.raises(ValueError, match="no data identifiers"):
        registry.register(StaticDids("empty", {}))
    with pytest.raises(ValueError, match="0x10000"):
        registry.register(StaticDids("wide", {0x10000: b"z"}))
    with pytest.raises(ValueError, match="'f190'"):
        registry.register(StaticDids("stringly", {"f190": b"z"}))


def test_structural_provider_checks():
    assert isinstance(StaticDids("a", {1: b""}), DidProvider)
    assert isinstance(StaticDtcs("d", []), DtcProvider)
    assert not isinstance(object(), DidProvider)


def test_registry_writes_only_through_a_provider_that_explicitly_claims_write_access():
    from ecu_simulator.protocols.uds.dids import StaticDidProvider

    access = DidWriteAccess(sessions=frozenset({0x03}), security_level=1, length=2)
    provider = StaticDidProvider("coding", {0xF1A0: b"\x01\x02"}, writable={0xF1A0: access})
    registry = DidRegistry()
    registry.register(provider)
    assert isinstance(provider, WritableDidProvider)
    assert registry.write_access(0xF1A0) == access
    registry.write(0xF1A0, b"\x03\x04")
    assert registry.read(0xF1A0) == b"\x03\x04"
    assert registry.write_access(0xF190) is None


def test_a_writable_provider_may_not_claim_an_unowned_or_wrong_length_did():
    from ecu_simulator.protocols.uds.dids import StaticDidProvider

    with pytest.raises(ValueError, match="not in its data identifiers"):
        StaticDidProvider(
            "coding",
            {0xF1A0: b"\x01\x02"},
            writable={0xF1A1: DidWriteAccess(frozenset({3}), 1, 2)},
        )
    with pytest.raises(ValueError, match="length"):
        StaticDidProvider(
            "coding",
            {0xF1A0: b"\x01\x02"},
            writable={0xF1A0: DidWriteAccess(frozenset({3}), 1, 1)},
        )


@pytest.mark.parametrize(
    "encoding, value, expected",
    [
        ("boolean", True, "01"),
        ("uint8", 42, "2a"),
        ("uint16_be", 800, "0320"),
        ("uint32_be", 100_000, "000186a0"),
        ("int16_be", -250, "ff06"),
        ("float32_be", 80.0, "42a00000"),
    ],
)
def test_signal_did_provider_uses_the_declared_wire_encoding(encoding, value, expected):
    from ecu_simulator.protocols.uds.dids import SignalDid, SignalDidProvider
    from ecu_simulator.vehicle import CommonState, IcePowertrain, VehicleState

    vehicle = VehicleState(CommonState(vin="VIN", speed=42), IcePowertrain())
    vehicle.set("vehicle.speed", value)
    provider = SignalDidProvider("signals", vehicle, {0xF1A1: SignalDid("vehicle.speed", encoding)})
    assert provider.read(0xF1A1).hex() == expected


def test_signal_dids_are_read_only():
    from ecu_simulator.protocols.uds.dids import SignalDid, SignalDidProvider
    from ecu_simulator.vehicle import CommonState, IcePowertrain, VehicleState

    vehicle = VehicleState(CommonState(vin="VIN"), IcePowertrain())
    provider = SignalDidProvider("signals", vehicle, {0xF1A1: SignalDid("engine.rpm", "uint16_be")})
    registry = DidRegistry()
    registry.register(provider)
    assert not isinstance(provider, WritableDidProvider)
    assert registry.write_access(0xF1A1) is None


# --- DtcRegistry ----------------------------------------------------------------------------


def test_dtc_registry_concatenates_providers_in_registration_order():
    registry = DtcRegistry()
    registry.register(StaticDtcs("engine", [DtcRecord(0x947701, 0x2F)]))
    registry.register(StaticDtcs("misfire", [DtcRecord(0x000101, 0x2F), DtcRecord(0x030001, 0x08)]))
    assert registry.read(0xFF) == [DtcRecord(0x947701, 0x2F), DtcRecord(0x000101, 0x2F), DtcRecord(0x030001, 0x08)]


def test_dtc_registry_filters_by_status_mask():
    registry = DtcRegistry()
    registry.register(
        StaticDtcs("d", [DtcRecord(0x000101, 0x2F), DtcRecord(0x030001, 0x08), DtcRecord(0x040001, 0x00)])
    )
    assert registry.read(0x08) == [DtcRecord(0x000101, 0x2F), DtcRecord(0x030001, 0x08)]
    assert registry.read(0x00) == []
    assert [p.name for p in registry.providers] == ["d"]


def test_dtc_registry_rejects_duplicate_names_and_bad_masks():
    registry = DtcRegistry()
    registry.register(StaticDtcs("d", []))
    with pytest.raises(ProviderConflictError, match="'d'"):
        registry.register(StaticDtcs("d", []))
    with pytest.raises(ValueError):
        registry.read(0x100)


# --- wired onto the ECU ----------------------------------------------------------------------


def test_ecu_owns_one_registry_of_each_kind():
    ecu = Ecu("engine")
    assert isinstance(ecu.dids, DidRegistry) and isinstance(ecu.dtc_providers, DtcRegistry)
    assert Ecu("tcm").dids is not ecu.dids


def test_ecu_accepts_injected_registries():
    dids, providers = DidRegistry(), DtcRegistry()
    ecu = Ecu("engine", dids=dids, dtc_providers=providers)
    assert ecu.dids is dids and ecu.dtc_providers is providers
