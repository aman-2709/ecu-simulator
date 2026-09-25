"""The simulator on the physical bench, started by the suite and only on an empty bus.

The physical backend owns the simulator's lifecycle, as the simulated backend already
does. The order is what matters:

1. **Check the bus.** If anything already answers OBD, refuse. On a dedicated bench nothing
   answers before the simulator starts; on a vehicle a real ECU would. This is the refusal
   that keeps the ruling in docs/decisions/0008 section 2 -- this simulator never transmits
   on a live vehicle bus -- in code. It used to run while an operator-started simulator was
   already up, so the check saw that simulator, called the bench occupied and failed every
   case.
2. **Start the simulator**, and wait for its own "ready" line.
3. **Stop it whatever happens**: a failing case, a simulator that never becomes ready, a
   refused restart.

A restart runs the check again before starting, because a bus that was empty at the start
of a session is not guaranteed to still be empty after a case.

No pyserial here, so the unit suite can pin this order with fakes on a machine that
has neither the extra nor a CAN interface.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tests.integration.conftest import Simulator, _foreign_responder_present


class BenchOccupied(Exception):
    """Something other than this simulator answers OBD on the bench interface."""


class BenchSimulator:
    def __init__(
        self,
        interface: str,
        workdir: str,
        *,
        responder_present: Callable[[str], bool] | None = None,
        launch: Callable[[str, str], Any] | None = None,
    ) -> None:
        self.interface = interface
        self.workdir = workdir
        self.responder_present = responder_present or _foreign_responder_present
        self.launch = launch or Simulator
        self._process: Any = None

    def start(self) -> None:
        if self.responder_present(self.interface):
            raise BenchOccupied(
                f"something already answers OBD requests on {self.interface!r} before the simulator "
                "has started. This suite must run on a dedicated bench carrying nothing but the "
                "simulator it starts itself and the tester, so stop any simulator you started by "
                "hand. If this is a vehicle, stop: docs/decisions/0008 rules that this simulator "
                "never transmits on a live vehicle bus."
            )
        self._process = self.launch(self.interface, self.workdir)
        try:
            self._process.wait_ready()
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        if self._process is not None:
            process, self._process = self._process, None
            process.terminate()

    def restart(self) -> None:
        """Replace the process, discarding the ECU state a mutating case left behind."""
        self.stop()
        self.start()

    def __enter__(self) -> BenchSimulator:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
