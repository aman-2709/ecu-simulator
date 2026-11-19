"""Per-ECU mock SecurityAccess state (Phase 12).

The public fixed XOR handshake exists for deterministic tester automation. It is not a
credential, cryptographic primitive, or model of an OEM algorithm; see Decision 0014.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

logger = logging.getLogger(__name__)

LOCKED_LEVEL = 0
MOCK_SEED = bytes.fromhex("12 34 56 78")
MOCK_XOR_MASK = bytes.fromhex("A5 A5 A5 A5")
MOCK_KEY = bytes(seed ^ mask for seed, mask in zip(MOCK_SEED, MOCK_XOR_MASK, strict=True))


class UdsSecurity:
    """One configured mock security level and its current challenge state."""

    def __init__(self, level: int = 1, sessions: Iterable[int] = (0x03,)) -> None:
        if not isinstance(level, int) or not 1 <= level <= 0x3F:
            raise ValueError(f"security level must be in 1..63, got {level!r}")
        session_set = frozenset(sessions)
        if any(not isinstance(session, int) or not 1 <= session <= 0x7E for session in session_set):
            raise ValueError(f"security sessions must contain session bytes, got {sorted(session_set)!r}")
        self._level = level
        self._sessions = session_set
        self._active_level = LOCKED_LEVEL
        self._seed_requested = False

    @property
    def level(self) -> int:
        return self._level

    @property
    def sessions(self) -> frozenset[int]:
        return self._sessions

    @property
    def active_level(self) -> int:
        return self._active_level

    @property
    def seed_subfunction(self) -> int:
        return self._level * 2 - 1

    @property
    def key_subfunction(self) -> int:
        return self._level * 2

    def level_for_subfunction(self, subfunction: int) -> int | None:
        if subfunction in (self.seed_subfunction, self.key_subfunction):
            return self._level
        return None

    def request_seed(self, level: int) -> bytes:
        self._require_level(level)
        if self._active_level == level:
            return bytes(len(MOCK_SEED))
        self._seed_requested = True
        return MOCK_SEED

    def submit_key(self, level: int, key: bytes) -> bool | None:
        """Return true/false for a compared key, or ``None`` when no seed preceded it."""
        self._require_level(level)
        if not self._seed_requested:
            return None
        if key != MOCK_KEY:
            return False
        self._seed_requested = False
        self._active_level = level
        logger.info("UDS mock security level %d unlocked", level)
        return True

    def lock(self, reason: str) -> None:
        if self._active_level != LOCKED_LEVEL:
            logger.info("UDS security level %d -> locked (%s)", self._active_level, reason)
        self._active_level = LOCKED_LEVEL
        self._seed_requested = False

    def _require_level(self, level: int) -> None:
        if level != self._level:
            raise ValueError(f"security level {level} is not configured")
