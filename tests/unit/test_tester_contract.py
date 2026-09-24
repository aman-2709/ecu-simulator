"""The seam that lets one set of diagnostic cases run against two very different testers.

Phase 8a's harness has to serve two callers that share nothing physically:

* a **simulated** tester -- a fake ELM327 on a pty bridged to a real ISO-TP socket on
  vcan, which runs in ordinary CI with no hardware (Task 12);
* a **physical** tester -- a real OBDLink LX over ``/dev/rfcomm*`` on the isolated CANable
  bench, opt-in only and never in CI (Phase 8b).

If the diagnostic assertions were written twice they would drift, and the bench would end
up proving something slightly different from what CI proves. ``DiagnosticTester`` is the contract
both backends satisfy, and ``AcceptanceCase`` is how a case is written once and run
against either.

This module has no I/O and no pyserial import, so it runs everywhere.
"""

from __future__ import annotations

import pytest

from tests.hardware.tester import AcceptanceCase, DiagnosticTester


class MinimalTester:
    """The smallest object that satisfies the contract."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.closed = False

    def at(self, text: str) -> str:
        self.asked.append(text)
        return "ELM327 v1.4b"

    def ask(self, text: str) -> bytes:
        self.asked.append(text)
        return bytes.fromhex("410C0C80")

    def close(self) -> None:
        self.closed = True


class NotATester:
    """Has `ask` but not `at` or `close`."""

    def ask(self, text: str) -> bytes:
        return b""


def test_a_conforming_object_satisfies_the_protocol():
    assert isinstance(MinimalTester(), DiagnosticTester)


def test_a_partial_object_does_not_satisfy_the_protocol():
    # The point of runtime_checkable here is that a backend which forgets a method is
    # rejected at the seam rather than halfway through a bench run.
    assert not isinstance(NotATester(), DiagnosticTester)


def test_a_case_runs_against_any_conforming_tester():
    def check_rpm(tester: DiagnosticTester) -> None:
        assert tester.ask("01 0C") == bytes.fromhex("410C0C80")

    case = AcceptanceCase(name="engine-rpm", why="0007 6.2 item 6", run=check_rpm)
    tester = MinimalTester()
    case.run(tester)
    assert tester.asked == ["01 0C"]


def test_a_failing_case_surfaces_its_assertion():
    def always_fails(tester: DiagnosticTester) -> None:
        assert tester.ask("01 0C") == b"\x00", "deliberate"

    case = AcceptanceCase(name="broken", why="proves failures are not swallowed", run=always_fails)
    with pytest.raises(AssertionError, match="deliberate"):
        case.run(MinimalTester())


def test_a_case_carries_the_reason_it_exists():
    # Every case cites what it is for, so a bench report can say why a row matters rather
    # than only that it passed.
    case = AcceptanceCase(name="vin", why="0007 6.2 item 9", run=lambda t: None)
    assert case.name == "vin"
    assert "6.2" in case.why


def test_cases_are_hashable_and_comparable_for_stable_parameterisation():
    # Both backends parameterise over the same registry; pytest ids must be stable so a
    # bench result and a CI result can be compared row by row.
    a = AcceptanceCase(name="vin", why="x", run=lambda t: None)
    b = AcceptanceCase(name="vin", why="x", run=a.run)
    assert a == b
    assert len({a, b}) == 1


def test_the_case_id_is_its_name():
    case = AcceptanceCase(name="supported-pids", why="x", run=lambda t: None)
    assert str(case) == "supported-pids"
