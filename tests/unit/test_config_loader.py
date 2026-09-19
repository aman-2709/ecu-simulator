"""YAML loading: safe parsing, explicit rejection, no silent data loss."""

import pytest

from ecu_simulator.config import ConfigError, load_profile, load_yaml


def write(tmp_path, text, name="p.yaml"):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_a_mapping_is_returned_for_a_well_formed_document(tmp_path):
    assert load_yaml(write(tmp_path, "a: 1\nb: [x, y]\n")) == {"a": 1, "b": ["x", "y"]}


def test_hexadecimal_can_identifiers_arrive_as_integers(tmp_path):
    assert load_yaml(write(tmp_path, "rx: 0x7DF\n"))["rx"] == 0x7DF


def test_yaml_1_1_boolean_traps_do_not_apply(tmp_path):
    # ruamel's YAML 1.2 core schema keeps these as strings; PyYAML would coerce them.
    loaded = load_yaml(write(tmp_path, "name: no\nmode: on\nlabel: 12:30\n"))
    assert loaded == {"name": "no", "mode": "on", "label": "12:30"}


def test_a_duplicate_key_is_rejected_rather_than_silently_overwritten(tmp_path):
    # The decisive reason for the parser choice: PyYAML keeps the last and says nothing.
    with pytest.raises(ConfigError, match="duplicate"):
        load_yaml(write(tmp_path, "a: 1\na: 2\n"))


def test_arbitrary_object_construction_is_refused(tmp_path):
    with pytest.raises(ConfigError):
        load_yaml(write(tmp_path, "!!python/object/apply:os.system ['echo pwned']\n"))


def test_a_syntax_error_names_the_file(tmp_path):
    path = write(tmp_path, "a: [1, 2\n")
    with pytest.raises(ConfigError, match=path.name):
        load_yaml(path)


def test_a_missing_file_is_reported_clearly(tmp_path):
    with pytest.raises(ConfigError, match="nosuch.yaml"):
        load_profile(tmp_path / "nosuch.yaml")


def test_an_empty_document_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="empty"):
        load_yaml(write(tmp_path, "\n"))


def test_a_document_that_is_not_a_mapping_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="mapping"):
        load_yaml(write(tmp_path, "- 1\n- 2\n"))
