"""Golden tests for the configuration the simulator ships with.

Phase 0 pinned these values as read from the package's ecu_config.json, alongside the
module-global import-time loading and the exit(1) on a malformed address. Phase 4 deleted
that module, ecu_config.json and addresses.py, and moved the same values into
src/ecu_simulator/profiles/ice_default.yaml. The values pinned here are unchanged; what
changed is where they come from and that a malformed profile is now rejected at load
rather than substituted or fatal at import (DEV-13, DEV-14).
"""

import pytest

from ecu_simulator import app
from ecu_simulator.cli import default_profile_path
from ecu_simulator.config import ConfigError, load_profile, parse_profile


def shipped():
    return load_profile(default_profile_path())


def test_shipped_config_values():
    profile = shipped()
    engine = profile.ecus["engine"]
    assert profile.vehicle.vin == "TESTVIN0123456789"
    assert engine.name == "ECU_SIMULATOR"
    assert profile.vehicle.engine.fuel_level == 50
    assert profile.vehicle.engine.fuel_type == 1
    # Phase 6 gave each configured trouble code a state, so the entries are records
    # rather than strings. The codes, their order and the bytes they produce are
    # unchanged; a bare string still means pending and confirmed.
    assert [d.code for d in engine.dtcs] == ["B1477", "P0001"]
    assert all(d.pending and d.confirmed and not d.indicator_requested for d in engine.dtcs)
    assert profile.transport.interface == "vcan0"


def test_shipped_addresses():
    endpoints = {e.name: e for e in shipped().ecus["engine"].endpoints}
    assert endpoints["obd_functional"].rx == 0x7DF
    assert endpoints["obd_physical"].rx == 0x7E0
    assert endpoints["uds_physical"].rx == 0x7E1


def test_response_ids_are_request_ids_plus_eight():
    endpoints = {e.name: e for e in shipped().ecus["engine"].endpoints}
    assert endpoints["obd_functional"].tx == 0x7E8
    assert endpoints["obd_physical"].tx == endpoints["obd_physical"].rx + 8 == 0x7E8
    assert endpoints["uds_physical"].tx == endpoints["uds_physical"].rx + 8 == 0x7E9


def test_the_shipped_profile_drives_the_same_wire_addresses_as_before():
    config = app.RuntimeConfig.build(shipped())
    pairs = {(e.address.rx_id, e.address.tx_id) for e in app.build_endpoints(config)}
    assert pairs == {(0x7DF, 0x7E8), (0x7E0, 0x7E8), (0x7E1, 0x7E9)}


def test_configuration_is_no_longer_a_module_global_loaded_at_import():
    # The audit's finding: configuration and pre-encoded responses were module globals
    # evaluated at import. Loading a profile is now an explicit call.
    import importlib

    assert not hasattr(importlib.import_module("ecu_simulator.app"), "CONFIG")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("ecu_simulator.ecu_config")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("ecu_simulator.addresses")


def test_a_malformed_address_is_rejected_at_load_instead_of_terminating_the_process():
    # DEV-14: create_address() printed the error and called exit(1). Now the schema
    # rejects it and the caller decides what to do.
    profile = shipped().model_dump()
    profile["ecus"]["engine"]["endpoints"][1]["rx"] = "not-hex"
    with pytest.raises(ConfigError, match="rx"):
        parse_profile(profile)
