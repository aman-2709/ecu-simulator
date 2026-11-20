"""UDS diagnostic session state for one ECU (Phase 11).

Domain state, the way :class:`~ecu_simulator.dtc.DtcStore` is: which diagnostic session is
active, and the timing each configured session reports in its 0x10 response. It holds no
encoded bytes except that four-byte record, whose layout is fixed by what udsoncan parses and
driftregion/iso14229 builds: P2ServerMax in 1 ms units, then P2*ServerMax in 10 ms units,
both big-endian.

S3: in a non-default session, if S3Server passes without a request, the ECU is back in the
default session ([SWS_Dcm_01670]); 5000 ms unless configured ([SWS_Dcm_01679]). What counts
as a request is "any diagnostic request message" ([ECUC_Dcm_01240], ruling Q4): the protocol
calls :meth:`UdsSession.note_request` for every request it handles, whatever the outcome.
Expiry is evaluated lazily, by :meth:`UdsSession.expire_if_idle` before each request: state
is only observable through a response, so no timer task is needed and none exists. A gap of
exactly S3Server keeps the session; any longer gap ends it.

One session per ECU, shared by every endpoint that enables UDS (ruling Q2). AUTOSAR ties
the session to the connection that opened it ([SWS_Dcm_01667], [SWS_Dcm_01669]); this
simulator does not, deliberately.

**Nothing here is standards validated.** ISO 14229-1:2026 and ISO 14229-2:2021 are licensed
and unread. The evidence for every rule is in
docs/decisions/0011-phase-11-uds-session-handling.md.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ecu_simulator.clock import Clock, MonotonicClock

logger = logging.getLogger(__name__)

# [SWS_Dcm_00034]: "During Dcm initialization, the session state is set to the value 0x01".
DEFAULT_SESSION = 0x01
# [SWS_Dcm_01679]: "Dcm shall take the value of 5000ms as the S3Server timeout."
DEFAULT_S3_MS = 5000

P2_STAR_RESOLUTION_MS = 10
RECORD_FIELD_MAX = 0xFFFF


@dataclass(frozen=True, slots=True)
class SessionTiming:
    """What one session's 0x10 positive response reports."""

    p2_ms: int
    p2_star_ms: int

    def __post_init__(self) -> None:
        if not 0 <= self.p2_ms <= RECORD_FIELD_MAX:
            raise ValueError(f"p2_ms {self.p2_ms} does not fit the two-byte record field")
        if self.p2_star_ms % P2_STAR_RESOLUTION_MS:
            raise ValueError(f"p2_star_ms {self.p2_star_ms} is not a multiple of {P2_STAR_RESOLUTION_MS}")
        if not 0 <= self.p2_star_ms // P2_STAR_RESOLUTION_MS <= RECORD_FIELD_MAX:
            raise ValueError(f"p2_star_ms {self.p2_star_ms} does not fit the two-byte record field")

    def record(self) -> bytes:
        """The sessionParameterRecord: P2 in 1 ms units, P2* in 10 ms units, big-endian."""
        return self.p2_ms.to_bytes(2, "big") + (self.p2_star_ms // P2_STAR_RESOLUTION_MS).to_bytes(2, "big")


# The record UDS 0x10 sent for every session before Phase 11, 00 1E 0B B8, kept as the
# default by ruling Q3 (DEV-17).
DEFAULT_TIMING = SessionTiming(p2_ms=30, p2_star_ms=30_000)
DEFAULT_TIMINGS: Mapping[int, SessionTiming] = MappingProxyType(
    {session: DEFAULT_TIMING for session in (0x01, 0x02, 0x03, 0x04)}
)


class UdsSession:
    """The active diagnostic session of one ECU."""

    def __init__(
        self,
        timings: Mapping[int, SessionTiming] = DEFAULT_TIMINGS,
        *,
        s3_ms: int = DEFAULT_S3_MS,
        clock: Clock | None = None,
    ) -> None:
        if DEFAULT_SESSION not in timings:
            raise ValueError("the default session 0x01 must be configured")
        if s3_ms <= 0:
            raise ValueError(f"s3_ms must be positive, got {s3_ms}")
        self._timings = MappingProxyType(dict(timings))
        self._active = DEFAULT_SESSION
        self._s3_ms = s3_ms
        self._clock: Clock = clock if clock is not None else MonotonicClock()
        self._last_request: float | None = None

    def __repr__(self) -> str:
        return f"UdsSession(active=0x{self._active:02X})"

    @property
    def supported(self) -> frozenset[int]:
        return frozenset(self._timings)

    @property
    def active(self) -> int:
        """The active session as last evaluated; :meth:`expire_if_idle` brings it up to date."""
        return self._active

    @property
    def s3_ms(self) -> int:
        return self._s3_ms

    def expire_if_idle(self) -> None:
        """Return to the default session if S3Server has passed since the last request."""
        if self._active == DEFAULT_SESSION or self._last_request is None:
            return
        idle_ms = (self._clock.now() - self._last_request) * 1000
        if idle_ms > self._s3_ms:
            self.return_to_default(f"S3 timeout after {idle_ms:.0f} ms without a request")

    def note_request(self) -> None:
        """A request was handled: S3 restarts from now."""
        self._last_request = self._clock.now()

    def timing(self, session: int) -> SessionTiming:
        return self._timings[session]

    def enter(self, session: int) -> None:
        """Make ``session`` active, including when it already is ([SWS_Dcm_00307])."""
        if session not in self._timings:
            raise KeyError(f"session 0x{session:02X} is not configured")
        if session != self._active:
            logger.info("UDS session 0x%02X -> 0x%02X", self._active, session)
        self._active = session

    def return_to_default(self, reason: str) -> None:
        if self._active != DEFAULT_SESSION:
            logger.info("UDS session 0x%02X -> 0x%02X (%s)", self._active, DEFAULT_SESSION, reason)
        self._active = DEFAULT_SESSION
