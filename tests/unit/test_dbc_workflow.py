import pytest

from ecu_simulator.gui.dbc_workflow import ReviewTokens, changed_draft


def test_review_requires_same_draft_and_interface_instance():
    tokens = ReviewTokens()
    token = tokens.issue("draft", 4)
    with pytest.raises(ValueError, match="review"):
        tokens.consume(token, "changed", 4)
    token = tokens.issue("draft", 4)
    with pytest.raises(ValueError, match="review"):
        tokens.consume(token, "draft", 5)
    token = tokens.issue("draft", 4)
    tokens.consume(token, "draft", 4)
    with pytest.raises(ValueError, match="review"):
        tokens.consume(token, "draft", 4)


def test_update_mapping_preserves_other_values_and_comments():
    from ecu_simulator.cli import default_profile_path
    from ecu_simulator.config import load_profile_text

    text = default_profile_path().read_text() + "\n# personal working copy\n"
    dbc = {"filename": "powertrain.dbc", "sha256": "a" * 64, "messages": []}
    result = changed_draft(text, dbc)
    assert "# personal working copy" in result
    assert load_profile_text(result).dbc.filename == "powertrain.dbc"
    assert load_profile_text(text).ecus == load_profile_text(result).ecus


def test_wide_values_round_trip_through_form_text_and_yaml():
    from ecu_simulator.cli import default_profile_path
    from ecu_simulator.config import load_profile_text
    from ecu_simulator.gui.dbc_workflow import browser_numbers

    value = 2**60 + 1
    mapping = {"filename": "bench.dbc", "sha256": "a" * 64,
               "messages": [{"name": "Sample", "period_ms": 10, "values": {"Ticks": value}}]}
    browser_data = browser_numbers(mapping)
    assert browser_data["messages"][0]["values"]["Ticks"] == str(value)
    profile = load_profile_text(changed_draft(default_profile_path().read_text(), browser_data))
    assert profile.dbc.messages[0].values["Ticks"] == value
