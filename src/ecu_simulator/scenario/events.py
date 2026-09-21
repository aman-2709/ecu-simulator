"""Timed diagnostic-trouble-code events.

An event says "at `at` seconds, do this one thing to this code". The actions are exactly
what :class:`~ecu_simulator.dtc.DtcStore` already supports, one action to one store call.
Nothing here can create a trouble code, change an encoding, or set a status bit the store
does not model: a scenario decides *when* a fault appears, never *what a fault looks like
on the wire*.

See docs/decisions/0006-phase-7-scenario-and-testerpresent.md section 5.2, and 0004 for
what the store's flags mean and why it models only these three.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Each maps to one DtcStore call. `clear_all` is DtcStore.clear(), the same operation OBD
# Mode 04 and UDS 0x14 call; the rest are DtcStore.update() with one flag named.
DtcAction = Literal[
    "raise_pending",
    "raise_confirmed",
    "request_indicator",
    "clear_code",
    "clear_all",
]

# The actions that act on one named code, and what each sets. One action sets one flag:
# the store models three and each is addressable on its own, so a scenario that wants a
# code both pending and confirmed says so with two events rather than having one action
# decide for it. The profile's bare-code shorthand is the initial state and is a different
# question; nothing here changes what the shorthand means.
CODE_ACTIONS: dict[str, dict[str, bool]] = {
    "raise_pending": {"pending": True},
    "raise_confirmed": {"confirmed": True},
    "request_indicator": {"indicator_requested": True},
    "clear_code": {"pending": False, "confirmed": False, "indicator_requested": False},
}


class DtcEvent(BaseModel):
    """One scheduled change to one ECU's trouble-code state."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    at: float = Field(ge=0)
    action: DtcAction
    code: str | None = None

    @model_validator(mode="after")
    def the_action_and_the_code_agree(self) -> DtcEvent:
        if self.action == "clear_all":
            if self.code is not None:
                raise ValueError("action 'clear_all' clears every code on the ECU and takes no 'code'")
        elif self.code is None:
            raise ValueError(f"action {self.action!r} needs the 'code' it acts on")
        return self
