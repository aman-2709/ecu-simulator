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


@pytest.mark.parametrize(
    "text",
    [
        "a: !!bool foo\n",  # the safe constructor raises KeyError
        "a: !!int ''\n",  # IndexError
        "a: !!float ''\n",  # IndexError
        "a: !!str x\n",
        "a: !!binary Z2Vhcm5ib3g=\n",
        "a: !!set {x}\n",
        "a: !!timestamp 2001-01-01\n",
        "a: !!map {b: 1}\n",
        "a: !!seq [1]\n",
        "a: !!python/object/apply:os.system ['true']\n",
        "a: ! x\n",
        "a: !<tag:yaml.org,2002:int> 3\n",
        "!!str a: 1\n",
        "a: [!!int 1]\n",
    ],
)
def test_untrusted_text_may_not_carry_a_tag(text):
    with pytest.raises(ConfigError, match=r"^rule file uses a YAML tag at line 1; tags are not allowed in a rule file$"):
        read_yaml(text, "", kind="rule file", untrusted=True)


def test_a_profile_may_still_carry_a_tag():
    # The profile path is as it was: tags the safe constructor knows are read, and its own failures are not rewritten.
    assert read_yaml("a: !!str 3\nb: !!binary aGk=\n", "p") == {"a": "3", "b": b"hi"}
    with pytest.raises(KeyError):
        read_yaml("a: !!bool foo\n", "p")
    with pytest.raises(IndexError):
        read_yaml("a: !!int ''\n", "p")


class _FailingReader:
    """A reader whose scan passes and whose load raises ``error``, standing in for a constructor failure."""

    def __init__(self, error):
        self.error = error

    def parse(self, text):
        return iter(())

    def load(self, text):
        raise self.error


@pytest.mark.parametrize(
    "error",
    [ValueError("month must be in 1..12"), KeyError("foo"), IndexError("string index out of range"), TypeError("x"), AttributeError("x")],
    ids=lambda e: type(e).__name__,
)
def test_any_constructor_failure_in_untrusted_text_is_a_config_error_and_in_a_profile_is_not(monkeypatch, error):
    monkeypatch.setattr("ecu_simulator.config.loader._reader", lambda: _FailingReader(error))
    with pytest.raises(ConfigError, match=r"^rule file has a value that cannot be read$") as caught:
        read_yaml("a: 1\n", "", kind="rule file", untrusted=True)
    assert caught.value.__cause__ is error
    with pytest.raises(type(error)) as raised:
        read_yaml("a: 1\n", "p")
    assert raised.value is error


def test_an_integer_over_the_interpreter_limit_is_said_to_be_too_large_without_its_internals():
    with pytest.raises(ConfigError) as caught:
        read_yaml("a: " + "9" * 5000 + "\n", "", kind="rule file", untrusted=True)
    assert str(caught.value) == "rule file has a value that cannot be read: an integer in it is too large"
