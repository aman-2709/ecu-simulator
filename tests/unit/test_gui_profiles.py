from concurrent.futures import ThreadPoolExecutor

import pytest

from ecu_simulator.cli import default_profile_path
from ecu_simulator.config import ConfigError, load_profile_text
from ecu_simulator.gui.profiles import Profiles, change_interface


def test_draft_matches_file_and_changes_only_interface(tmp_path):
    profiles = Profiles(tmp_path)
    text = profiles.read("shipped", "ice_default")
    assert text == default_profile_path().read_text()
    changed = change_interface(text, "can0")
    assert load_profile_text(changed).transport.interface == "can0"
    assert changed.replace('"can0"', 'vcan0') == text
    assert profiles.read("shipped", "ice_default") == text
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("text", ["[", "version: 1\nversion: 2", "version: 42"])
def test_invalid_text_is_rejected_before_save(tmp_path, text):
    with pytest.raises(ConfigError):
        Profiles(tmp_path).save("demo", text)
    assert not list(tmp_path.iterdir())


def test_named_profiles_are_atomic_create_only(tmp_path):
    profiles = Profiles(tmp_path)
    text = default_profile_path().read_text()
    def save(_):
        try:
            profiles.save("Test bench", text)
            return True
        except FileExistsError:
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(save, range(2))) == [False, True]
    assert Profiles(tmp_path).read("user", "Test bench") == text
    assert profiles.names()["user"] == ["Test bench"]
    with pytest.raises(ValueError):
        profiles.save("../escape", text)
