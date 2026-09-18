"""Golden tests for the legacy configuration and address modules.

Pins that configuration is a module global loaded at import time, that the shipped
ecu_config.json produces the documented defaults, that the response CAN ID is derived by a
fixed +8 offset, and that a malformed address terminates the process (DEV-14).
"""
import pytest

from ecu_simulator import addresses, ecu_config


def test_shipped_config_values():
    assert ecu_config.get_vin() == "TESTVIN0123456789"
    assert ecu_config.get_ecu_name() == "ECU_SIMULATOR"
    assert ecu_config.get_fuel_level() == 50
    assert ecu_config.get_fuel_type() == 1
    assert ecu_config.get_dtcs() == ["B1477", "P0001"]
    assert ecu_config.get_can_interface() == "vcan0"


def test_shipped_addresses_parse_from_hex_strings():
    assert ecu_config.get_obd_broadcast_address() == 0x7DF
    assert ecu_config.get_obd_ecu_address() == 0x7E0
    assert ecu_config.get_uds_ecu_address() == 0x7E1


def test_response_ids_are_request_ids_plus_eight():
    assert addresses.OBD_TARGET_ADDRESS == 0x7E8
    assert addresses.UDS_TARGET_ADDRESS == 0x7E9
    assert addresses.ECU_ADDRESSES == [0x7DF, 0x7E0, 0x7E1]
    assert addresses.TARGET_ADDRESSES == [0x7E8, 0x7E9]


def test_config_is_a_module_global_loaded_at_import():
    assert isinstance(ecu_config.CONFIG, dict)
    assert ecu_config.CONFIG_FILE.endswith("ecu_config.json")


def test_malformed_address_terminates_the_process(capsys):
    # DEV-14: create_address prints the error and calls exit(1) instead of raising.
    with pytest.raises(SystemExit) as excinfo:
        ecu_config.create_address("not-hex")
    assert excinfo.value.code == 1
    assert "not-hex" in capsys.readouterr().out
