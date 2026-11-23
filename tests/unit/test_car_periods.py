"""Where each message's transmit period comes from: the operator, a recorded log, the DBC, the rate table or a default.

Recordings are built the way the upload path builds them, with ``parse_recording`` on small candump text, so the packed
frame layout and its extended and remote flags come from the real producer.
"""

import dataclasses
import socket
import subprocess
import sys
from dataclasses import dataclass

import pytest

from ecu_simulator.car import periods
from ecu_simulator.car.periods import (
    DEFAULT_PERIOD_MS,
    MAX_PERIOD_MS,
    MIN_PERIOD_MS,
    Period,
    PeriodSource,
    measure_log_periods,
    resolve_period,
)
from ecu_simulator.gui import capture
from ecu_simulator.gui.replay import parse_recording

BASE_S = 1000  # candump stamps are absolute; only the offsets from the first frame matter


def line(us: int, ident: str = "123", payload: str = "00") -> bytes:
    """One candump line, ``us`` microseconds after the base time."""
    seconds, micro = divmod(us, 1_000_000)
    return f"({BASE_S + seconds}.{micro:06d}) can0 {ident}#{payload}\n".encode()


def recording(*lines: bytes):
    return parse_recording(b"".join(lines))


def us_from_ms(milliseconds: float) -> int:
    return round(milliseconds * 1000)


def resolve(
    *, override: int | None = None, measured_ms: float | None = None, dbc_ms: int | None = None,
    table_hz: float | None = None,
) -> Period:
    return resolve_period(override=override, measured_ms=measured_ms, dbc_ms=dbc_ms, table_hz=table_hz)


# resolve_period


def test_priority_order():
    everything = {"override": 40, "measured_ms": 30.0, "dbc_ms": 20, "table_hz": 25.0}
    assert resolve(**everything) == Period(40, PeriodSource.OVERRIDE, False)
    everything.pop("override")
    assert resolve(**everything) == Period(30, PeriodSource.LOG, False)
    everything.pop("measured_ms")
    assert resolve(**everything) == Period(20, PeriodSource.DBC, False)
    everything.pop("dbc_ms")
    assert resolve(**everything) == Period(40, PeriodSource.TABLE, False)
    assert resolve() == Period(DEFAULT_PERIOD_MS, PeriodSource.ASSUMED, False)


def test_period_source_values_are_the_wire_names():
    assert [source.value for source in PeriodSource] == ["override", "log", "dbc", "table", "assumed"]
    assert isinstance(PeriodSource.LOG, str) and PeriodSource.LOG == "log"


def test_table_hz_converts_to_ms():
    assert resolve(table_hz=50.0) == Period(20, PeriodSource.TABLE, False)
    assert resolve(table_hz=0.5) == Period(2000, PeriodSource.TABLE, False)
    assert resolve(table_hz=3.0) == Period(333, PeriodSource.TABLE, False)  # 333.33 ms


def test_below_floor_is_clamped_and_labelled():
    assert MIN_PERIOD_MS == 10
    assert resolve(override=5) == Period(10, PeriodSource.OVERRIDE, True)
    assert resolve(measured_ms=2.0) == Period(10, PeriodSource.LOG, True)
    assert resolve(dbc_ms=1) == Period(10, PeriodSource.DBC, True)
    assert resolve(table_hz=1000.0) == Period(10, PeriodSource.TABLE, True)
    assert resolve(table_hz=1e308) == Period(10, PeriodSource.TABLE, True)  # 1e-305 ms


def test_the_floor_itself_is_not_clamped():
    assert resolve(override=10) == Period(10, PeriodSource.OVERRIDE, False)
    assert resolve(measured_ms=9.5) == Period(10, PeriodSource.LOG, False)  # rounds up to the floor
    assert resolve(measured_ms=9.49) == Period(10, PeriodSource.LOG, True)  # rounds to 9, then clamped


def test_above_ceiling_is_clamped_and_labelled():
    assert MAX_PERIOD_MS == 60000
    assert resolve(override=60001) == Period(60000, PeriodSource.OVERRIDE, True)
    assert resolve(override=10**400) == Period(60000, PeriodSource.OVERRIDE, True)
    assert resolve(measured_ms=90_000.0) == Period(60000, PeriodSource.LOG, True)
    assert resolve(dbc_ms=120_000) == Period(60000, PeriodSource.DBC, True)
    assert resolve(table_hz=0.001) == Period(60000, PeriodSource.TABLE, True)  # 1,000,000 ms
    assert resolve(table_hz=1e-320) == Period(60000, PeriodSource.TABLE, True)  # 1000 / 1e-320 overflows to infinity


def test_the_ceiling_itself_is_not_clamped():
    assert resolve(override=60000) == Period(60000, PeriodSource.OVERRIDE, False)
    assert resolve(measured_ms=60000.4) == Period(60000, PeriodSource.LOG, False)  # rounds down to the ceiling
    assert resolve(measured_ms=60000.5) == Period(60000, PeriodSource.LOG, True)  # rounds to 60001, then clamped


def test_assumed_default():
    assert DEFAULT_PERIOD_MS == 100
    assert resolve() == Period(100, PeriodSource.ASSUMED, False)


def test_half_rounds_up():
    # Python's round() sends 12.5 to 12 (banker's rounding); the period rounds half up.
    assert resolve(measured_ms=12.5).ms == 13
    assert resolve(measured_ms=13.5).ms == 14
    assert resolve(measured_ms=12.49).ms == 12
    assert resolve(table_hz=80.0).ms == 13  # 12.5 ms


@pytest.mark.parametrize("measured", [0.0, -5.0, float("nan"), float("inf"), float("-inf")])
def test_non_positive_and_nan_measurements_fall_through(measured):
    assert resolve(measured_ms=measured, dbc_ms=20) == Period(20, PeriodSource.DBC, False)


@pytest.mark.parametrize("dbc", [0, -1, -1000])
def test_non_positive_dbc_cycle_time_falls_through(dbc):
    assert resolve(dbc_ms=dbc, table_hz=25.0) == Period(40, PeriodSource.TABLE, False)


@pytest.mark.parametrize("hz", [0.0, -50.0, float("nan"), float("inf"), float("-inf")])
def test_non_positive_and_nan_table_rates_fall_through(hz):
    assert resolve(table_hz=hz) == Period(DEFAULT_PERIOD_MS, PeriodSource.ASSUMED, False)


def test_non_positive_and_nan_sources_fall_through():
    every_bad_source = {"measured_ms": float("nan"), "dbc_ms": 0, "table_hz": 0.0}
    assert resolve(**every_bad_source) == Period(DEFAULT_PERIOD_MS, PeriodSource.ASSUMED, False)
    assert resolve(**{**every_bad_source, "table_hz": 10.0}) == Period(100, PeriodSource.TABLE, False)


@pytest.mark.parametrize(("override", "expected"), [(0, 10), (-5, 10)])
def test_an_override_is_clamped_never_dropped(override, expected):
    # The operator chose it, so a bad value is reported as clamped to the operator rather than silently replaced.
    assert resolve(override=override, measured_ms=30.0) == Period(expected, PeriodSource.OVERRIDE, True)


def test_period_is_frozen():
    period = resolve(override=20)
    with pytest.raises(dataclasses.FrozenInstanceError):
        period.ms = 30  # type: ignore[misc]


def test_resolve_period_is_deterministic():
    sources = {"override": None, "measured_ms": 12.5, "dbc_ms": 20, "table_hz": 50.0}
    assert [resolve(**sources) for _ in range(3)] == [Period(13, PeriodSource.LOG, False)] * 3


# measure_log_periods


def test_measure_median_of_irregular_intervals():
    # Intervals 10, 20, 10 ms: the median is 10, the mean would be 13.33.
    log = recording(*(line(us_from_ms(t)) for t in (0, 10, 30, 40)))
    result = measure_log_periods(log)
    assert result == {(0x123, False): 10.0}
    assert type(result[(0x123, False)]) is float


def test_even_count_median_is_the_mean_of_the_middle_pair():
    # Intervals 10, 20, 10, 60 ms sort to 10, 10, 20, 60: the median is (10 + 20) / 2.
    log = recording(*(line(us_from_ms(t)) for t in (0, 10, 30, 40, 100)))
    assert measure_log_periods(log) == {(0x123, False): 15.0}


def test_sub_millisecond_intervals_keep_their_precision():
    log = recording(line(0), line(1500), line(3000))
    assert measure_log_periods(log) == {(0x123, False): 1.5}


def test_single_frame_id_has_no_measurement():
    log = recording(line(0, "123"), line(us_from_ms(10), "200"), line(us_from_ms(20), "200"), line(us_from_ms(30), "200"))
    assert measure_log_periods(log) == {(0x200, False): 10.0}


def test_two_frames_of_an_id_have_only_one_interval_and_no_measurement():
    log = recording(line(0), line(us_from_ms(10)))
    assert measure_log_periods(log) == {}


def test_two_frames_with_the_same_timestamp_give_no_period():
    log = recording(line(us_from_ms(5)), line(us_from_ms(5)))
    assert measure_log_periods(log) == {}
    # Three frames, two of them at one instant, leave a single positive interval.
    assert measure_log_periods(recording(line(0), line(0), line(us_from_ms(10)))) == {}


def test_equal_timestamps_ignored():
    # Intervals 0, 0, 10, 20: counting the zeros would give a median of 5 ms.
    log = recording(*(line(us_from_ms(t)) for t in (0, 0, 0, 10, 30)))
    assert measure_log_periods(log) == {(0x123, False): 15.0}


def test_negative_intervals_are_ignored():
    # parse_recording refuses a log that runs backwards, so this recording is built by hand.
    @dataclass
    class Handmade:
        frames: list[tuple[int, bytes]]

    packed = recording(line(0)).frames[0][1]
    # Intervals 10, -5, 20, 20 ms: counting the negative one would give a median of 15 ms.
    offsets = [us_from_ms(t) * 1000 for t in (0, 10, 5, 25, 45)]
    assert measure_log_periods(Handmade([(offset, packed) for offset in offsets])) == {(0x123, False): 20.0}


def test_remote_frames_ignored():
    log = recording(
        line(0, "123"), line(us_from_ms(5), "123", "R"), line(us_from_ms(10), "123"), line(us_from_ms(15), "123", "R"), line(us_from_ms(20), "123"),
    )
    assert measure_log_periods(log) == {(0x123, False): 10.0}


def test_an_id_seen_only_in_remote_frames_is_absent():
    log = recording(*(line(us_from_ms(t), ident, "R") for t in (0, 10, 20) for ident in ("123", "00000123")))
    assert measure_log_periods(log) == {}


def test_extended_and_standard_ids_with_same_number_are_separate():
    standard = [(t, "123") for t in (0, 10, 20, 30, 40)]
    extended = [(t, "00000123") for t in (5, 30, 55)]
    log = recording(*(line(us_from_ms(t), ident) for t, ident in sorted(standard + extended)))
    assert measure_log_periods(log) == {(0x123, False): 10.0, (0x123, True): 25.0}


def test_extended_ids_are_keyed_without_the_flag_bits():
    log = recording(*(line(us_from_ms(t), "18DAF110") for t in (0, 20, 40)))
    assert measure_log_periods(log) == {(0x18DAF110, True): 20.0}


def test_each_id_is_measured_on_its_own_frames():
    frames = [(0, "100"), (3, "200"), (10, "100"), (13, "200"), (20, "100"), (30, "200"), (40, "200")]
    result = measure_log_periods(recording(*(line(us_from_ms(t), ident) for t, ident in frames)))
    assert result == {(0x100, False): 10.0, (0x200, False): 10.0}


def test_an_empty_recording_measures_nothing():
    @dataclass
    class Empty:
        frames: list[tuple[int, bytes]]

    assert measure_log_periods(Empty([])) == {}


def test_measure_log_periods_is_deterministic():
    log = recording(*(line(us_from_ms(t), ident) for t, ident in [(0, "100"), (1, "200"), (11, "100"), (14, "200"), (22, "100")]))
    first = measure_log_periods(log)
    assert first == measure_log_periods(log)
    assert list(first) == list(measure_log_periods(log))


# The layout is the replay parser's, declared again so the core package never imports the gui one.


def test_record_layout_matches_the_replay_parser():
    log = recording(line(0, "123"), line(1, "00000123", "AABB"), line(2, "123", "R"))
    standard, extended, remote = (periods._RECORD.unpack(record) for _, record in log.frames)
    assert standard == (0x123, 1, b"\x00" + bytes(7))
    assert extended[0] == socket.CAN_EFF_FLAG | 0x123 and extended[1] == 2
    assert remote[0] == socket.CAN_RTR_FLAG | 0x123
    assert periods._RECORD.format == capture.FRAME.format
    assert periods._RECORD.size == capture.FRAME.size == len(log.frames[0][1])


def test_flag_constants_match_the_kernel_values():
    assert periods._EXTENDED_FLAG == socket.CAN_EFF_FLAG
    assert periods._REMOTE_FLAG == socket.CAN_RTR_FLAG
    assert periods._EXTENDED_ID_MASK == socket.CAN_EFF_MASK
    assert periods._STANDARD_ID_MASK == socket.CAN_SFF_MASK


def test_module_does_not_import_gui_at_runtime():
    program = (
        "import ecu_simulator.car.periods, sys; "
        "assert not any(m.startswith('ecu_simulator.gui') for m in sys.modules), "
        "sorted(m for m in sys.modules if m.startswith('ecu_simulator.gui'))"
    )
    result = subprocess.run([sys.executable, "-I", "-c", program], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
