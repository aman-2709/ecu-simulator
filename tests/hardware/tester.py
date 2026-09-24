"""The contract a diagnostic tester satisfies, and how an acceptance case is written once.

Phase 8a's ELM327 harness serves two callers that share no hardware at all:

* a **simulated** tester -- a fake ELM327 on a pty, bridged to a real kernel ISO-TP socket
  on ``vcan0``. It runs in ordinary CI with no devices attached, and its job is to prove
  this project's own Python harness is not broken (Task 12).
* a **physical** tester -- a real adapter over a serial device, currently an OBDLink LX on
  ``/dev/rfcomm*`` against the isolated CANable bench. Opt-in only, never in CI, and the
  only one of the two that can produce hardware evidence (Phase 8b).

Writing the diagnostic assertions twice would let them drift, and the bench would then
prove something subtly different from what CI proves. ``DiagnosticTester`` is the seam: both backends
satisfy it, and every :class:`AcceptanceCase` is written against it once.

What this module deliberately does **not** do: import pyserial, open anything, or know
which backend it is talking to. It is imported by unit tests that run everywhere, so it
stays pure.

Note what a passing case does and does not mean. Against the simulated backend it means
the harness and the simulator agree. Only against the physical backend is it evidence
about real wire, and even then only about the adapter named in the bench record.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class DiagnosticTester(Protocol):
    """An ELM327-compatible diagnostic tester, simulated or physical.

    Three methods, because that is all an acceptance case needs. Keeping the surface this
    small is what makes the simulated backend cheap enough to be worth having.

    Named ``DiagnosticTester`` rather than ``Tester`` for two reasons: a class whose name
    begins with "Test" is picked up by pytest's collector, and this matches the
    ``DiagnosticRequest`` / ``DiagnosticResponse`` vocabulary the transport layer already
    uses.
    """

    def at(self, text: str) -> str:
        """Send an ``AT`` command and return its answer as a single line of text."""
        ...

    def ask(self, text: str) -> bytes:
        """Send an OBD or UDS request and return the decoded response payload.

        Raises on a reported device condition -- ``NO DATA``, ``CAN ERROR`` and the rest
        of the ELM327DSJ error vocabulary -- so a case can assert on silence explicitly
        rather than inferring it from an empty result.
        """
        ...

    def close(self) -> None:
        """Release the underlying device."""
        ...


@dataclass(frozen=True)
class AcceptanceCase:
    """One diagnostic check, runnable against any :class:`DiagnosticTester`.

    ``name`` is the pytest id, so a bench result and a CI result can be compared row by
    row. ``why`` cites what the case is for -- usually an item in
    ``docs/decisions/0007-phase-8-hardware-validation.md`` section 6.2 -- so a report can
    say why a row matters rather than only that it passed.

    Frozen and hashable so the registry can be parameterised over directly and the ids
    stay stable between the two backends.
    """

    name: str
    why: str
    run: Callable[[DiagnosticTester], None]

    def __str__(self) -> str:
        return self.name
