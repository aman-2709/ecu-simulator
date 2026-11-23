"""Which fields of a DBC message are counters and checksums, and frames stamped with valid values for them.

``plan_message`` reads a message as ``ImportedDb.message`` describes it and decides, under a checksum rule from
``ecu_simulator.car.checksums``, which signals it advances and which it computes. ``Stamper`` then fills those fields
in each frame the way opendbc's ``CANPacker`` does, so its output is byte-identical to the packer's.

Recognition goes by the rule's own signal names, never by the broad integrity-name pattern ``ImportedDb`` uses: a
name that pattern matches but the rule does not recognise is reported in ``possible_fields`` and left at whatever the
caller's encoder wrote, its DBC default.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ecu_simulator.car.bits import BitField
from ecu_simulator.car.checksums import (
    HYUNDAI_COUNTER_SIGNALS,
    HYUNDAI_SPLIT_COUNTERS,
    RULES,
    ChecksumFn,
    is_hyundai_checksum,
)

if TYPE_CHECKING:
    # rules.py imports this module; the engine only reads ``.counters`` and ``.checksums`` from a user rule.
    from ecu_simulator.car.rules import UserRule

# The two WHL_SPD11 signals together carry one 4-bit counter.
_HYUNDAI_SPLIT_COUNTER_MAX = 15

_NOT_COMPUTED = "checksum not computed"
_USER_RULE = "computed as the supplied rule says"
_POSSIBLE = "possible integrity field held at its initial value"


class IntegrityState(StrEnum):
    NONE = "none"
    VALID = "valid"
    COUNTER_ONLY = "counter_only"
    USER_RULE = "user_rule"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class CounterField:
    """A counter signal; the frame carries ``(counter >> shift)`` masked to the field, and the counter wraps after
    ``max_value``."""

    signal: str
    field: BitField
    shift: int
    max_value: int


@dataclass(frozen=True, slots=True)
class ChecksumField:
    signal: str
    field: BitField
    fn: ChecksumFn


@dataclass(frozen=True, slots=True)
class MessagePlan:
    """What a ``Stamper`` writes into one message's frames, and why.

    ``checksums`` follows the message's signal order, which is the order the checksums are computed in.
    """

    address: int
    extended: bool
    size: int
    counters: tuple[CounterField, ...]
    checksums: tuple[ChecksumField, ...]
    state: IntegrityState
    detail: str | None
    possible_fields: tuple[str, ...]


def _counter(rule: str | None, name: str, field: BitField) -> CounterField | None:
    field_max = (1 << field.size) - 1
    if rule == "hyundai_can":
        if name in HYUNDAI_COUNTER_SIGNALS:
            return CounterField(name, field, 0, field_max)
        if name in HYUNDAI_SPLIT_COUNTERS:
            return CounterField(name, field, HYUNDAI_SPLIT_COUNTERS[name], _HYUNDAI_SPLIT_COUNTER_MAX)
        return None
    return CounterField(name, field, 0, field_max) if name == "COUNTER" else None


def _is_checksum(rule: str, name: str, field: BitField) -> bool:
    if rule == "hyundai_can":
        return is_hyundai_checksum(name, field.size)
    return name == "CHECKSUM"


def plan_message(message: Mapping[str, Any], *, rule: str | None, user: "UserRule | None" = None) -> MessagePlan:
    """The plan for ``message`` under ``rule`` (a key of ``RULES``), or with no rule only a ``COUNTER`` advancing.

    A ``user`` rule, the counter and checksum a rule file defines for this message, wins over ``rule``: the plan has
    exactly its fields and the state ``user_rule``. The named rule's own signals are then reported in
    ``possible_fields`` like any other integrity-named signal the plan does not generate.
    """
    address, extended, size = int(message["id"]), bool(message["extended"]), int(message["dlc"])

    def plan(
        state: IntegrityState,
        detail: str | None,
        counters: tuple[CounterField, ...] = (),
        checksums: tuple[ChecksumField, ...] = (),
        possible: tuple[str, ...] = (),
    ) -> MessagePlan:
        return MessagePlan(address, extended, size, counters, checksums, state, detail, possible)

    if message["layout_issue"]:
        return plan(IntegrityState.UNSUPPORTED, str(message["layout_issue"]))
    if rule is not None and rule not in RULES:
        raise ValueError(f"Unknown integrity rule {rule!r}")

    counters: list[CounterField] = []
    checksums: list[ChecksumField] = []
    uncomputed_checksum = False
    if user is not None:
        counters, checksums = list(user.counters), list(user.checksums)
    else:
        for signal in message["signals"]:
            name = str(signal["name"])
            field = BitField(int(signal["start"]), int(signal["length"]), bool(signal["little_endian"]))
            if (counter := _counter(rule, name, field)) is not None:
                counters.append(counter)
            elif rule is not None and _is_checksum(rule, name, field):
                checksums.append(ChecksumField(name, field, RULES[rule]))
            elif rule is None and name == "CHECKSUM":
                uncomputed_checksum = True

    placed: list[CounterField | ChecksumField] = [*counters, *checksums]
    for item in placed:
        if not item.field.fits(size):
            return plan(IntegrityState.UNSUPPORTED, f"{item.signal} reaches outside the {size}-byte frame")

    recognised = {c.signal for c in counters} | {c.signal for c in checksums}
    if uncomputed_checksum:
        recognised.add("CHECKSUM")
    possible = tuple(name for name in message["integrity_fields"] if name not in recognised)

    details: list[str] = []
    if user is not None:
        details.append(_USER_RULE)
    elif uncomputed_checksum:
        details.append(_NOT_COMPUTED)
    if possible:
        details.append(_POSSIBLE)
    detail = "; ".join(details) or None

    if user is not None:
        state = IntegrityState.USER_RULE
    elif rule is None:
        state = IntegrityState.COUNTER_ONLY if counters else IntegrityState.NONE
    else:
        state = IntegrityState.VALID if counters or checksums else IntegrityState.NONE
    return plan(state, detail, tuple(counters), tuple(checksums), possible)


class Stamper:
    """Stamps one message's frames with its counters and checksums, as opendbc's packer does on each call.

    Each stamper keeps its own counter; the first frame it stamps carries counter 0.
    """

    def __init__(self, plan: MessagePlan) -> None:
        self.plan = plan
        self._counter = 0

    def stamp(self, data: bytes) -> bytes:
        """A copy of ``data`` (exactly ``plan.size`` bytes) with the counters written and the checksums computed."""
        plan = self.plan
        if len(data) != plan.size:
            raise ValueError(f"Expected {plan.size} bytes, got {len(data)}")
        frame = bytearray(data)
        for counter in plan.counters:
            counter.field.write(frame, self._counter >> counter.shift)
        # The rules compute over the frame with their own field zero, and each result goes in before the next runs.
        for checksum in plan.checksums:
            checksum.field.write(frame, 0)
        for checksum in plan.checksums:
            checksum.field.write(frame, checksum.fn(plan.address, frame, checksum.field, checksum.signal))
        if plan.counters:
            self._counter = (self._counter + 1) % (plan.counters[0].max_value + 1)
        return bytes(frame)
