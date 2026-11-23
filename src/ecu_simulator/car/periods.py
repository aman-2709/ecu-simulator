"""How often each message of a whole-car run is transmitted, and where that number came from.

``resolve_period`` takes the candidate periods for one message, best source first: the operator's override, a
measurement from a recorded log, the DBC's cycle time, the catalog's rate table, and a 100 ms default labelled
``assumed``. The answer carries its source, so the screen can say where every period came from, and says whether it had
to be moved into the 10 to 60000 ms the profile schema accepts. ``measure_log_periods`` produces the log measurement.
"""

import math
import statistics
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import Protocol, TypeGuard

DEFAULT_PERIOD_MS = 100
MIN_PERIOD_MS = 10
MAX_PERIOD_MS = 60000

# The packed frame record ``ecu_simulator.gui.replay.parse_recording`` writes, declared again because the gui package
# is an optional extra the core package must not import. gui/capture.py's FRAME holds the same layout, and a test
# compares this one with it: a 32-bit word (bit 31 extended-frame flag, bit 30 remote-request flag, the rest the
# identifier), the data length, three pad bytes and eight data bytes.
_RECORD = struct.Struct("=IB3x8s")
_EXTENDED_FLAG = 0x8000_0000
_REMOTE_FLAG = 0x4000_0000
_EXTENDED_ID_MASK = 0x1FFF_FFFF
_STANDARD_ID_MASK = 0x7FF

_MIN_INTERVALS = 2  # one interval is two frames, which cannot tell a steady period from a gap
_NS_PER_MS = 1_000_000


class PeriodSource(StrEnum):
    OVERRIDE = "override"
    LOG = "log"
    DBC = "dbc"
    TABLE = "table"
    ASSUMED = "assumed"


@dataclass(frozen=True, slots=True)
class Period:
    """A transmit period in whole milliseconds; ``clamped`` is true when the source's value fell outside the limits."""

    ms: int
    source: PeriodSource
    clamped: bool


class RecordedFrames(Protocol):
    """What ``measure_log_periods`` reads of a recording: ``frames`` as ``(offset in nanoseconds, packed record)``.

    A read-only property so a recording whose ``frames`` is a ``list`` matches, as ``gui.replay.Recording`` does.
    """

    @property
    def frames(self) -> Sequence[tuple[int, bytes]]: ...


def measure_log_periods(recording: RecordedFrames) -> dict[tuple[int, bool], float]:
    """The median interval in milliseconds between frames, for each ``(arbitration_id, extended)`` of the log.

    ``arbitration_id`` is the identifier without the flag bits, so a standard and an extended id with the same number
    are two keys. Remote frames are ignored, and so are zero and negative intervals. An id needs at least two
    positive intervals to have a measurement; one that has fewer is left out of the result.
    """
    offsets: dict[tuple[int, bool], list[int]] = {}
    for offset_ns, record in recording.frames:
        can_id, _length, _data = _RECORD.unpack(record)
        if can_id & _REMOTE_FLAG:
            continue
        extended = bool(can_id & _EXTENDED_FLAG)
        key = (can_id & (_EXTENDED_ID_MASK if extended else _STANDARD_ID_MASK), extended)
        offsets.setdefault(key, []).append(offset_ns)

    measured: dict[tuple[int, bool], float] = {}
    for key, times in offsets.items():
        intervals = [later - earlier for earlier, later in pairwise(times) if later > earlier]
        if len(intervals) >= _MIN_INTERVALS:
            measured[key] = statistics.median(intervals) / _NS_PER_MS
    return measured


def resolve_period(
    *, override: int | None, measured_ms: float | None, dbc_ms: int | None, table_hz: float | None
) -> Period:
    """The period for one message from the best source that has one.

    The operator's ``override`` is used whenever it is given, even when out of range, and is then clamped. A lower
    source that is zero, negative or not finite counts as absent and the next one is tried.
    """
    if override is not None:
        return _clamped(override, PeriodSource.OVERRIDE)
    if _usable(measured_ms):
        return _clamped(_round_half_up(measured_ms), PeriodSource.LOG)
    if dbc_ms is not None and dbc_ms > 0:
        return _clamped(dbc_ms, PeriodSource.DBC)
    if _usable(table_hz):
        return _clamped(_round_half_up(1000.0 / table_hz), PeriodSource.TABLE)
    return Period(DEFAULT_PERIOD_MS, PeriodSource.ASSUMED, False)


def _usable(value: float | None) -> TypeGuard[float]:
    return value is not None and math.isfinite(value) and value > 0


def _round_half_up(value: float) -> int:
    # The cap keeps a quotient that overflowed to infinity from reaching floor(); anything past it is clamped anyway.
    return math.floor(min(value, MAX_PERIOD_MS + 1) + 0.5)


def _clamped(ms: int, source: PeriodSource) -> Period:
    if ms < MIN_PERIOD_MS:
        return Period(MIN_PERIOD_MS, source, True)
    if ms > MAX_PERIOD_MS:
        return Period(MAX_PERIOD_MS, source, True)
    return Period(ms, source, False)
