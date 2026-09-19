"""Profile schema: a malformed profile is rejected at load with the path to the problem.

This is project input validation, not standards validation. The accepted DTC
representation is the one the existing encoder already accepts and the characterization
tests already pin; see docs/decisions/0002-configuration-format-and-validation.md.
"""

import copy

import pytest

from ecu_simulator.config import ConfigError, load_profile, parse_profile

VALID = {
    "version": 1,
    "transport": {"interface": "vcan0"},
    "vehicle": {"vin": "TESTVIN0123456789", "type": "ice", "engine": {"fuel_level": 50, "fuel_type": 1}},
    "ecus": {
        "engine": {
            "name": "ECU_SIMULATOR",
            "dtcs": ["B1477", "P0001"],
            "dids": {},
            "endpoints": [
                {
                    "name": "obd_functional",
                    "rx": 0x7DF,
                    "tx": 0x7E8,
                    "addressing": "functional",
                    "protocols": ["obd"],
                    "answer_unsupported": False,
                    "tx_padding": True,
                    "pad_byte": 0x00,
                    "reply_via": "obd_physical",
                },
                {
                    "name": "obd_physical",
                    "rx": 0x7E0,
                    "tx": 0x7E8,
                    "addressing": "physical",
                    "protocols": ["obd", "uds"],
                    "tx_padding": True,
                    "pad_byte": 0x00,
                },
                {
                    "name": "uds_physical",
                    "rx": 0x7E1,
                    "tx": 0x7E9,
                    "addressing": "physical",
                    "protocols": ["obd", "uds"],
                    "tx_padding": False,
                },
            ],
        }
    },
}


def profile(**overrides):
    data = copy.deepcopy(VALID)
    for dotted, value in overrides.items():
        node = data
        *parents, leaf = dotted.split(".")
        for key in parents:
            node = node[int(key)] if isinstance(node, list) else node[key]
        if isinstance(node, list):
            node[int(leaf)] = value
        elif value is ...:
            del node[leaf]
        else:
            node[leaf] = value
    return data


def rejects(match, **overrides):
    with pytest.raises(ConfigError, match=match) as excinfo:
        parse_profile(profile(**overrides))
    return str(excinfo.value)


# --- the valid profile ---------------------------------------------------------------------


def test_the_valid_profile_parses():
    config = parse_profile(VALID)
    assert config.transport.interface == "vcan0"
    assert config.vehicle.vin == "TESTVIN0123456789"
    assert config.vehicle.type == "ice"
    assert list(config.ecus) == ["engine"]
    engine = config.ecus["engine"]
    assert engine.name == "ECU_SIMULATOR"
    assert engine.dtcs == ["B1477", "P0001"]
    assert [e.name for e in engine.endpoints] == ["obd_functional", "obd_physical", "uds_physical"]
    assert engine.endpoints[0].protocols == ["obd"]
    # answer_unsupported is the raw key; answers_unsupported is the resolved policy, which
    # defaults to answering on a physical route and staying silent on a broadcast.
    assert engine.endpoints[0].answers_unsupported is False
    assert engine.endpoints[1].answers_unsupported is True
    assert engine.endpoints[1].answer_unsupported is None


def test_the_unsupported_service_policy_defaults_by_addressing_and_is_overridable():
    config = parse_profile(profile(**{"ecus.engine.endpoints.0.answer_unsupported": True}))
    broadcast, physical = config.ecus["engine"].endpoints[0], config.ecus["engine"].endpoints[1]
    assert broadcast.functional is True and broadcast.answers_unsupported is True
    assert physical.functional is False and physical.answers_unsupported is True
    silent = parse_profile(profile(**{"ecus.engine.endpoints.1.answer_unsupported": False}))
    assert silent.ecus["engine"].endpoints[1].answers_unsupported is False


def test_the_shipped_profile_file_loads():
    config = load_profile("profiles/ice_default.yaml")
    assert config.vehicle.vin == "TESTVIN0123456789"
    assert config.ecus["engine"].endpoints[0].rx == 0x7DF


# --- vehicle ---------------------------------------------------------------------------------


def test_vin_longer_than_seventeen_characters_is_rejected():
    # DEV-14: the legacy code silently substituted a default VIN at request time.
    message = rejects("vin", **{"vehicle.vin": "X" * 18})
    assert "vehicle" in message and "vin" in message


@pytest.mark.parametrize("vin", ["", "  ", "SHORTVIN"])
def test_vin_must_be_present_and_plausible(vin):
    if vin.strip():
        parse_profile(profile(**{"vehicle.vin": vin}))  # shorter VINs are allowed, as before
    else:
        rejects("vin", **{"vehicle.vin": vin})


@pytest.mark.parametrize("level", [-5, 101, 1000])
def test_fuel_level_outside_zero_to_one_hundred_is_rejected(level):
    # DEV-14: a negative level passed validation and raised OverflowError at request time.
    message = rejects("fuel_level", **{"vehicle.engine.fuel_level": level})
    assert "engine" in message


@pytest.mark.parametrize("fuel_type", [0, 24, -1])
def test_fuel_type_outside_the_coded_range_is_rejected(fuel_type):
    rejects("fuel_type", **{"vehicle.engine.fuel_type": fuel_type})


def test_an_unknown_vehicle_type_is_rejected_and_lists_the_known_ones():
    message = rejects("type", **{"vehicle.type": "steam"})
    assert "ice" in message and "bev" in message


def test_an_ice_profile_may_not_carry_a_traction_battery():
    message = rejects("battery", **{"vehicle.battery": {"soc": 50.0}})
    assert "ice" in message


# --- DTCs (DEV-13) ----------------------------------------------------------------------------


@pytest.mark.parametrize("dtc", ["B147", "B14777", "X1477", "B9477", "B14Z7", "", "b1477"])
def test_malformed_dtcs_are_rejected_at_load(dtc):
    # DEV-13: the encoder silently skipped anything it could not parse.
    message = rejects("dtc", **{"ecus.engine.dtcs": [dtc]})
    assert repr(dtc) in message or dtc in message


@pytest.mark.parametrize("dtc", ["P0001", "C1234", "B1477", "U3FFF", "P0000"])
def test_every_dtc_the_encoder_accepts_is_accepted(dtc):
    config = parse_profile(profile(**{"ecus.engine.dtcs": [dtc]}))
    assert config.ecus["engine"].dtcs == [dtc]


def test_more_than_255_dtcs_is_rejected():
    rejects("dtcs", **{"ecus.engine.dtcs": ["P0001"] * 256})


def test_duplicate_dtcs_are_rejected():
    rejects("duplicate", **{"ecus.engine.dtcs": ["P0001", "P0001"]})


# --- addressing --------------------------------------------------------------------------------


def test_a_can_identifier_outside_the_eleven_bit_range_is_rejected():
    rejects("rx", **{"ecus.engine.endpoints.1.rx": 0x800})


def test_rx_and_tx_must_differ():
    message = rejects("differ", **{"ecus.engine.endpoints.2.tx": 0x7E1})
    assert "0x7E1" in message


def test_duplicate_endpoint_names_are_rejected():
    rejects("duplicate endpoint name", **{"ecus.engine.endpoints.2.name": "obd_physical"})


def test_duplicate_rx_and_tx_pairs_are_rejected():
    endpoints = copy.deepcopy(VALID["ecus"]["engine"]["endpoints"])
    endpoints[2] = {**endpoints[2], "rx": 0x7E0, "tx": 0x7E8}
    rejects("duplicate", **{"ecus.engine.endpoints": endpoints})


def test_reply_via_must_name_a_known_endpoint():
    message = rejects("reply_via", **{"ecus.engine.endpoints.0.reply_via": "nosuch"})
    assert "nosuch" in message


def test_an_unknown_addressing_mode_is_rejected():
    rejects("addressing", **{"ecus.engine.endpoints.1.addressing": "extended"})


def test_an_unknown_protocol_is_rejected_and_lists_the_known_ones():
    message = rejects("protocol", **{"ecus.engine.endpoints.1.protocols": ["doip"]})
    assert "obd" in message and "uds" in message


def test_an_endpoint_must_enable_at_least_one_protocol():
    rejects("protocols", **{"ecus.engine.endpoints.1.protocols": []})


def test_pad_byte_must_be_one_byte():
    rejects("pad_byte", **{"ecus.engine.endpoints.1.pad_byte": 256})


# --- ECUs and structure -------------------------------------------------------------------------


def test_at_least_one_ecu_is_required():
    rejects("ecus", ecus={})


def test_an_ecu_needs_at_least_one_endpoint():
    rejects("endpoints", **{"ecus.engine.endpoints": []})


def test_can_identifiers_may_not_be_shared_between_ecus():
    data = copy.deepcopy(VALID)
    data["ecus"]["tcm"] = copy.deepcopy(data["ecus"]["engine"])
    data["ecus"]["tcm"]["endpoints"] = [
        {"name": "tcm_physical", "rx": 0x7E0, "tx": 0x7EA, "addressing": "physical", "protocols": ["uds"]}
    ]
    with pytest.raises(ConfigError, match="0x7E0"):
        parse_profile(data)


def test_a_second_ecu_is_accepted_when_its_identifiers_are_distinct():
    data = copy.deepcopy(VALID)
    data["ecus"]["tcm"] = {
        "name": "TCM_SIMULATOR",
        "dtcs": [],
        "endpoints": [
            {"name": "tcm_physical", "rx": 0x7E2, "tx": 0x7EA, "addressing": "physical", "protocols": ["uds"]}
        ],
    }
    config = parse_profile(data)
    assert sorted(config.ecus) == ["engine", "tcm"]


def test_unknown_keys_are_rejected_rather_than_ignored():
    message = rejects("interfce", **{"transport.interfce": "vcan0"})
    assert "transport" in message


def test_an_unsupported_schema_version_is_rejected():
    rejects("version", version=2)


def test_a_missing_required_section_is_reported_with_its_path():
    message = rejects("vehicle", vehicle=...)
    assert "vehicle" in message


# --- error reporting -----------------------------------------------------------------------------


def test_every_problem_is_reported_not_only_the_first():
    data = profile(**{"vehicle.vin": "X" * 18, "vehicle.engine.fuel_level": -5})
    with pytest.raises(ConfigError) as excinfo:
        parse_profile(data)
    message = str(excinfo.value)
    assert "vin" in message and "fuel_level" in message


def test_the_error_names_the_profile_when_one_was_loaded_from_disk(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\n")
    with pytest.raises(ConfigError, match="bad.yaml"):
        load_profile(bad)
