"""DID and DTC provider registries: the UDS data-service extension points on an ECU."""

import pytest

from ecu_simulator.ecu import Ecu
from ecu_simulator.protocols.uds.providers import (
    DidProvider,
    DidRegistry,
    DtcProvider,
    DtcRecord,
    DtcRegistry,
    ProviderConflictError,
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
