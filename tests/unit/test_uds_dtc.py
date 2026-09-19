"""The UDS view of the shared DTC store: which status bits this project can set.

Bits 2, 3 and 7 are the only ones modelled, so they are the only ones advertised. The
layout itself is corroborated by the AUTOSAR Dem specification and by udsoncan and Scapy,
which agree bit for bit; which of those bits a simulator sets is this project's decision.
docs/decisions/0004-phase-6-dtc-evidence.md.
"""

import itertools

import pytest

from ecu_simulator.dtc import DtcState, DtcStore
from ecu_simulator.protocols.uds import DtcRegistry, DtcStoreProvider
from ecu_simulator.protocols.uds import dtc as uds_dtc


def records(*entries, mask=0xFF):
    providers = DtcRegistry()
    providers.register(DtcStoreProvider("engine", DtcStore(entries)))
    return providers.read(mask)


# --- the modelled bits ------------------------------------------------------------------------


def test_the_availability_mask_is_exactly_the_bits_that_are_modelled():
    assert uds_dtc.AVAILABILITY_MASK == 0x8C
    assert uds_dtc.AVAILABILITY_MASK == (
        uds_dtc.PENDING_DTC | uds_dtc.CONFIRMED_DTC | uds_dtc.WARNING_INDICATOR_REQUESTED
    )


@pytest.mark.parametrize("flags", list(itertools.product([False, True], repeat=3)))
def test_no_producible_status_byte_sets_a_bit_outside_the_advertised_mask(flags):
    pending, confirmed, indicator = flags
    state = DtcState("P0001", pending=pending, confirmed=confirmed, indicator_requested=indicator)
    assert uds_dtc.status_byte(state) & ~uds_dtc.AVAILABILITY_MASK == 0


@pytest.mark.parametrize(
    "state, expected",
    [
        (DtcState("P0001"), 0x00),
        (DtcState("P0001", pending=True), 0x04),
        (DtcState("P0001", confirmed=True), 0x08),
        (DtcState("P0001", pending=True, confirmed=True), 0x0C),
        (DtcState("P0001", indicator_requested=True), 0x80),
        (DtcState("P0001", pending=True, confirmed=True, indicator_requested=True), 0x8C),
    ],
)
def test_every_modelled_combination_of_flags_has_its_status_byte(state, expected):
    assert uds_dtc.status_byte(state) == expected


def test_the_unmodelled_bits_are_never_set():
    every = DtcState("P0001", pending=True, confirmed=True, indicator_requested=True)
    byte = uds_dtc.status_byte(every)
    for bit in (
        uds_dtc.TEST_FAILED,
        uds_dtc.TEST_FAILED_THIS_OPERATION_CYCLE,
        uds_dtc.TEST_NOT_COMPLETED_SINCE_LAST_CLEAR,
        uds_dtc.TEST_FAILED_SINCE_LAST_CLEAR,
        uds_dtc.TEST_NOT_COMPLETED_THIS_OPERATION_CYCLE,
    ):
        assert byte & bit == 0


# --- records ---------------------------------------------------------------------------------


def test_a_record_is_the_two_byte_code_the_frozen_failure_type_and_the_status():
    # DEV-16: the third byte stays 0x01, deliberately frozen for want of evidence.
    assert records(DtcState("B1477", pending=True, confirmed=True))[0].to_bytes().hex() == "9477010c"


def test_a_code_with_no_flags_produces_a_zero_status_and_matches_no_mask():
    assert records(DtcState("P0001"), mask=0xFF) == []


def test_records_follow_the_store_after_a_clear():
    store = DtcStore([DtcState("B1477", pending=True, confirmed=True)])
    providers = DtcRegistry()
    providers.register(DtcStoreProvider("engine", store))
    assert len(providers.read(0xFF)) == 1
    store.clear()
    assert providers.read(0xFF) == []
