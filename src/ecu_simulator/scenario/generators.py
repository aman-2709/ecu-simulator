"""Deterministic signal generators: a physical value as a pure function of elapsed time.

Each generator is a validated configuration object that can answer one question --
"what is this signal at `t` seconds?" -- and nothing else. It holds no state, reads no
clock, performs no I/O, and knows nothing about what the signal it drives means or how any
protocol encodes it. ``value_at(t)`` called twice with the same ``t`` returns the same
number, and two generators built from the same configuration agree at every ``t``.

There is no randomness anywhere, seeded or otherwise. Nothing in this phase needs any, so
there is nothing to seed; a phase that later wants noise brings its own seed with it.

The plan names exactly six generators and these are they. Times are elapsed seconds since
the runtime started, so every scenario begins at ``t = 0`` and a restart replays it from
the configured initial state. See docs/decisions/0006-phase-7-scenario-and-testerpresent.md
section 5.1.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SignalBase(BaseModel):
    """Common to every generator: the signal it drives, and unknown keys are a mistake.

    ``populate_by_name`` exists because two parameters in the configuration vocabulary --
    ``from`` on a ramp and ``for`` on a sequence step -- are Python keywords and reach the
    model under an alias.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True, validate_assignment=True)

    path: str = Field(min_length=3)

    @field_validator("path")
    @classmethod
    def path_is_dotted(cls, value: str) -> str:
        namespace, separator, name = value.partition(".")
        if not separator or not namespace or not name:
            raise ValueError(f"signal path {value!r} must be '<namespace>.<name>', for example 'vehicle.speed'")
        return value

    def value_at(self, t: float) -> float:  # pragma: no cover - every subclass overrides
        raise NotImplementedError


class ConstantSignal(SignalBase):
    """A value that does not change. Useful to pin one signal while others move."""

    type: Literal["constant"]
    value: float

    def value_at(self, t: float) -> float:
        return self.value


class RampSignal(SignalBase):
    """Linear from ``from`` to ``to`` across ``[0, over]``, holding ``to`` afterwards."""

    type: Literal["ramp"]
    start: float = Field(alias="from")
    to: float
    over: float = Field(gt=0)

    def value_at(self, t: float) -> float:
        if t <= 0:
            return self.start
        if t >= self.over:
            return self.to
        return self.start + (self.to - self.start) * (t / self.over)


class SineSignal(SignalBase):
    """``centre + amplitude * sin(2*pi*t/period + phase)``.

    The one generator that goes through libm. Its last bit could in principle differ
    between platforms and the OBD encoders truncate, so a value sitting exactly on a
    truncation boundary could encode to a different byte on a different machine. Recorded
    as a known risk: the wire-level byte-exact assertions use ``constant``, ``stepped``
    and ``timeline``, none of which involves a transcendental function.
    """

    type: Literal["sine"]
    centre: float
    amplitude: float
    period: float = Field(gt=0)
    phase: float = 0.0

    def value_at(self, t: float) -> float:
        return self.centre + self.amplitude * math.sin(2 * math.pi * t / self.period + self.phase)


class SteppedSignal(SignalBase):
    """A repeating staircase: ``values[floor(t/interval) mod len(values)]``."""

    type: Literal["stepped"]
    values: list[float] = Field(min_length=1)
    interval: float = Field(gt=0)

    def value_at(self, t: float) -> float:
        step = int(t // self.interval) if t > 0 else 0
        return self.values[step % len(self.values)]


class SequenceStep(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    value: float
    duration: float = Field(alias="for", gt=0)


class SequenceSignal(SignalBase):
    """Each value held for its own duration, in order, once; the last value holds.

    The difference from ``stepped`` is that a sequence runs to its end and stays there.
    Both are in the plan's list and both earn their name.
    """

    type: Literal["sequence"]
    steps: list[SequenceStep] = Field(min_length=1)

    def value_at(self, t: float) -> float:
        elapsed = 0.0
        for step in self.steps:
            elapsed += step.duration
            if t < elapsed:
                return step.value
        return self.steps[-1].value


class TimelinePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: float = Field(ge=0)
    value: float


class TimelineSignal(SignalBase):
    """The value of the latest point whose ``at`` has arrived; the first before that."""

    type: Literal["timeline"]
    points: list[TimelinePoint] = Field(min_length=1)

    @field_validator("points")
    @classmethod
    def points_ascend(cls, value: list[TimelinePoint]) -> list[TimelinePoint]:
        times = [point.at for point in value]
        if times != sorted(times):
            raise ValueError(f"timeline points must be in ascending 'at' order, got {times}")
        return value

    def value_at(self, t: float) -> float:
        current = self.points[0].value
        for point in self.points:
            if point.at > t:
                break
            current = point.value
        return current


# Pydantic 2.13 forbids a before, wrap or plain validator on a discriminated union's tag,
# so there is no shorthand that infers `type:` from the other keys present. A scenario
# entry states what it is. Configuration that drives wire values should be unambiguous in
# the file anyway, and the discriminator is what makes an invalid entry report its errors
# against the generator it claims to be rather than against all six.
SignalScenario = Annotated[
    ConstantSignal | RampSignal | SineSignal | SteppedSignal | SequenceSignal | TimelineSignal,
    Field(discriminator="type"),
]
