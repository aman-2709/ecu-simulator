"""DtcStore: the diagnostic state OBD and UDS both read, and the one clear they share.

The store holds domain state only. It never holds an encoded byte, and neither protocol
owns it. What each flag means, and why there are three rather than four, is in
docs/decisions/0004-phase-6-dtc-evidence.md.
"""

import pytest

from ecu_simulator.dtc import DtcState, DtcStore


def store(*entries):
    return DtcStore(entries or (DtcState("B1477", pending=True, confirmed=True), DtcState("P0001", pending=True)))


# --- construction ---------------------------------------------------------------------------


def test_an_empty_store_has_no_codes_and_no_indicator():
    empty = DtcStore()
    assert empty.codes == ()
    assert empty.confirmed == ()
    assert empty.pending == ()
    assert empty.indicator_on is False


def test_entries_keep_the_order_they_were_configured_in():
    assert store().codes == ("B1477", "P0001")


def test_a_duplicate_code_is_rejected():
    with pytest.raises(ValueError, match="P0001"):
        DtcStore([DtcState("P0001"), DtcState("P0001")])


def test_the_store_does_not_alias_the_states_it_is_given():
    # Otherwise a caller that reuses a template, or two ECUs built from one list, would
    # silently share flags and a clear on one would clear the other.
    template = DtcState("P0001", pending=True, confirmed=True)
    one, two = DtcStore([template]), DtcStore([template])
    one.clear()
    assert two.state("P0001").confirmed is True
    assert template.confirmed is True


def test_a_state_defaults_to_no_flags_set():
    state = DtcState("P0001")
    assert (state.pending, state.confirmed, state.indicator_requested) == (False, False, False)


# --- views ----------------------------------------------------------------------------------


def test_pending_and_confirmed_are_independent_views():
    entries = (
        DtcState("P0001", pending=True, confirmed=True),
        DtcState("P0002", pending=True),
        DtcState("P0003", confirmed=True),
        DtcState("P0004"),
    )
    assert tuple(s.code for s in store(*entries).pending) == ("P0001", "P0002")
    assert tuple(s.code for s in store(*entries).confirmed) == ("P0001", "P0003")


def test_the_indicator_is_on_when_any_code_requests_it():
    assert store().indicator_on is False
    assert store(DtcState("P0001", confirmed=True, indicator_requested=True)).indicator_on is True


def test_a_code_can_be_looked_up_by_name():
    assert store().state("P0001").pending is True
    assert store().state("P9999") is None


# --- clearing -------------------------------------------------------------------------------


def test_clearing_resets_every_flag():
    cleared = store(DtcState("P0001", pending=True, confirmed=True, indicator_requested=True))
    cleared.clear()
    state = cleared.state("P0001")
    assert (state.pending, state.confirmed, state.indicator_requested) == (False, False, False)


def test_clearing_keeps_the_configured_codes_so_they_can_be_raised_again():
    # The deliberate simulator transition: identity survives a clear, so a later scenario
    # or fault source re-asserts a configured code without rebuilding configuration.
    # docs/decisions/0004-phase-6-dtc-evidence.md, W12.
    cleared = store()
    cleared.clear()
    assert cleared.codes == ("B1477", "P0001")
    assert cleared.confirmed == () and cleared.pending == () and cleared.indicator_on is False


def test_a_cleared_code_can_be_raised_again():
    raised = store()
    raised.clear()
    raised.update("P0001", pending=True, confirmed=True)
    assert tuple(s.code for s in raised.confirmed) == ("P0001",)
    assert raised.codes == ("B1477", "P0001")


def test_clearing_an_empty_store_is_harmless():
    empty = DtcStore()
    empty.clear()
    assert empty.codes == ()


# --- updating -------------------------------------------------------------------------------


def test_an_update_changes_only_the_flags_it_names():
    updated = store()
    updated.update("B1477", confirmed=False)
    state = updated.state("B1477")
    assert (state.pending, state.confirmed) == (True, False)


def test_updating_an_unconfigured_code_is_rejected():
    with pytest.raises(KeyError, match="P9999"):
        store().update("P9999", pending=True)
