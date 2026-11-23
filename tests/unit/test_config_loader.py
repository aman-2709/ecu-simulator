"""YAML loading: safe parsing, explicit rejection, no silent data loss."""

import re

import pytest

from ecu_simulator.config import ConfigError, load_profile, load_yaml, read_yaml


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


def test_the_kind_words_the_errors_and_an_empty_source_is_left_out():
    with pytest.raises(ConfigError, match=r"^profile p.yaml is not valid YAML"):
        read_yaml("a: [1, 2\n", "p.yaml")
    with pytest.raises(ConfigError, match=r"^rule file up.yaml is not valid YAML"):
        read_yaml("a: [1, 2\n", "up.yaml", kind="rule file")
    with pytest.raises(ConfigError, match=r"^rule file is empty$"):
        read_yaml("\n", "", kind="rule file")


def test_anchors_and_merge_keys_are_still_read_unless_the_text_is_untrusted():
    assert read_yaml("a: &x 1\nb: *x\n", "p") == {"a": 1, "b": 1}
    assert read_yaml("a: &x {k: 1}\nb:\n  <<: *x\n", "p")["b"] == {"k": 1}
    for text, wording in [
        ("a: &x 1\n", "anchor at line 1"),
        ("a: 1\nb: *x\n", "alias at line 2"),
        ("b:\n  <<: {k: 1}\n", "merge key (<<) at line 2"),
    ]:
        with pytest.raises(ConfigError, match=re.escape(wording)):
            read_yaml(text, "", kind="rule file", untrusted=True)
    assert read_yaml("a: '<<'\nb: \"&x *y\"\n", "", untrusted=True) == {"a": "<<", "b": "&x *y"}


def test_untrusted_text_may_not_nest_deeper_than_the_bound():
    from ecu_simulator.config.loader import MAX_UNTRUSTED_DEPTH

    fits = "a: " + "[" * (MAX_UNTRUSTED_DEPTH - 1) + "]" * (MAX_UNTRUSTED_DEPTH - 1) + "\n"
    assert read_yaml(fits, "", untrusted=True)
    with pytest.raises(ConfigError, match="nested more than"):
        read_yaml("a: " + "[" * MAX_UNTRUSTED_DEPTH + "]" * MAX_UNTRUSTED_DEPTH + "\n", "", untrusted=True)


def test_untrusted_text_with_a_value_the_constructor_cannot_build_is_a_config_error():
    with pytest.raises(ConfigError, match="cannot be read"):
        read_yaml("a: " + "9" * 5000 + "\n", "", untrusted=True)
    with pytest.raises(ConfigError, match="cannot be read"):
        read_yaml("a: 2001-13-45\n", "", untrusted=True)
    # A profile read as before: the constructor's own ValueError is not rewritten.
    with pytest.raises(ValueError) as caught:
        read_yaml("a: 2001-13-45\n", "p")
    assert not isinstance(caught.value, ConfigError)
