"""Phase 12's deliberately transparent, non-OEM SecurityAccess state."""

from ecu_simulator.protocols.uds.security import MOCK_KEY, MOCK_SEED, UdsSecurity


def test_mock_key_is_the_public_seed_xor_mask_contract():
    assert MOCK_SEED == bytes.fromhex("12 34 56 78")
    assert MOCK_KEY == bytes.fromhex("B7 91 F3 DD")


def test_security_starts_locked_and_a_seed_then_key_unlocks_it():
    security = UdsSecurity(level=1, sessions={0x03})
    assert security.active_level == 0
    assert security.request_seed(1) == MOCK_SEED
    assert security.submit_key(1, MOCK_KEY) is True
    assert security.active_level == 1


def test_key_requires_a_seed_and_a_wrong_key_does_not_unlock():
    security = UdsSecurity(level=1, sessions={0x03})
    assert security.submit_key(1, MOCK_KEY) is None
    security.request_seed(1)
    assert security.submit_key(1, b"wrong") is False
    assert security.active_level == 0
    assert security.submit_key(1, MOCK_KEY) is True, "the challenge remains active after a wrong key"


def test_an_active_level_returns_a_zero_seed_and_lock_clears_it():
    security = UdsSecurity(level=1, sessions={0x03})
    security.request_seed(1)
    security.submit_key(1, MOCK_KEY)
    assert security.request_seed(1) == bytes(len(MOCK_SEED))
    security.lock("test")
    assert security.active_level == 0
    assert security.submit_key(1, MOCK_KEY) is None


def test_level_subfunctions_and_allowed_sessions_are_explicit():
    security = UdsSecurity(level=2, sessions={0x02, 0x04})
    assert security.seed_subfunction == 0x03
    assert security.key_subfunction == 0x04
    assert security.sessions == frozenset({0x02, 0x04})
    assert security.level_for_subfunction(0x03) == 2
    assert security.level_for_subfunction(0x04) == 2
    assert security.level_for_subfunction(0x01) is None

