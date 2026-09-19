"""Diagnostic trouble code state, shared by the OBD and UDS views of one ECU.

This is domain state: a trouble code's identity and what this simulator knows about it.
It holds no encoded bytes. The two-byte OBD form and the three-byte UDS form with its
status byte are produced by :mod:`ecu_simulator.protocols.obd.dtc` and
:mod:`ecu_simulator.protocols.uds.dtc`, which read the store and never mutate it.

Three flags, not four. ``confirmed`` is the state OBD service 03 calls "stored": the
Snap-on service-mode reference describes mode 03 as reporting stored emission-related
codes, and the AUTOSAR Dem describes the confirmedDTC bit as the malfunction being
"desired to be stored in long-term memory". **Folding the two together is a modeling
decision for this project, made because it is the distinction the behavior we serve
actually needs. It is not a claim that SAE or ISO define "stored" and "confirmed" as
equivalent in general.** Evidence and reasoning:
docs/decisions/0004-phase-6-dtc-evidence.md.

Nothing here models operation cycles, test completion or fault-detection counters. Those
are bits in a UDS status byte this project cannot fill honestly, so they are absent from
the state and from the advertised status availability mask alike.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace


@dataclass(slots=True)
class DtcState:
    """One trouble code and what is currently true of it.

    ``code`` is the profile's five-character trouble code, the only identity this project
    has. The flags default to false: a code the configuration does not raise is known to
    the simulator but is not reported by any protocol view.
    """

    code: str
    pending: bool = False
    confirmed: bool = False
    indicator_requested: bool = False

    def clear(self) -> None:
        """Reset every runtime flag; the identity is untouched."""
        self.pending = False
        self.confirmed = False
        self.indicator_requested = False


class DtcStore:
    """The trouble codes one ECU knows about, in configuration order."""

    def __init__(self, entries: Iterable[DtcState] = ()) -> None:
        """Takes a copy of each entry, so the store owns the state it is asked to hold.

        A :class:`DtcState` is mutable and the store mutates it; aliasing one would let a
        reused template, or two ECUs built from one list, share flags and clear each other.
        """
        self._entries: dict[str, DtcState] = {}
        for entry in entries:
            if entry.code in self._entries:
                raise ValueError(f"duplicate trouble code {entry.code!r} in the DTC store")
            self._entries[entry.code] = replace(entry)

    def __repr__(self) -> str:
        return f"DtcStore({list(self._entries)})"

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[DtcState]:
        return iter(self._entries.values())

    def __contains__(self, code: object) -> bool:
        return code in self._entries

    # -- reading -------------------------------------------------------------------------------

    @property
    def codes(self) -> tuple[str, ...]:
        """Every configured code, whether or not it is currently raised."""
        return tuple(self._entries)

    def state(self, code: str) -> DtcState | None:
        return self._entries.get(code)

    @property
    def pending(self) -> tuple[DtcState, ...]:
        """Codes failing on the current or last completed operation cycle."""
        return tuple(entry for entry in self._entries.values() if entry.pending)

    @property
    def confirmed(self) -> tuple[DtcState, ...]:
        """Codes stored in long-term memory; what OBD service 03 reports."""
        return tuple(entry for entry in self._entries.values() if entry.confirmed)

    @property
    def indicator_on(self) -> bool:
        """Derived, never set on its own: an indicator no code asks for is not a state."""
        return any(entry.indicator_requested for entry in self._entries.values())

    # -- writing -------------------------------------------------------------------------------

    def update(
        self,
        code: str,
        *,
        pending: bool | None = None,
        confirmed: bool | None = None,
        indicator_requested: bool | None = None,
    ) -> DtcState:
        """Change the flags named, leaving the others alone.

        The code must already be configured: this simulator reports the trouble codes its
        profile declares, and inventing one at runtime would put a code on the wire that
        no configuration accounts for.
        """
        entry = self._entries.get(code)
        if entry is None:
            raise KeyError(f"trouble code {code!r} is not configured on this ECU")
        if pending is not None:
            entry.pending = pending
        if confirmed is not None:
            entry.confirmed = confirmed
        if indicator_requested is not None:
            entry.indicator_requested = indicator_requested
        return entry

    def clear(self) -> None:
        """The one clear operation. OBD Mode 04 and UDS 0x14 both call this and nothing else.

        Every runtime flag is reset and every configured code stays. The available OBD and
        UDS descriptions of a clear do not agree on the state a code is left in, so this
        implements only what both agree on -- nothing pending, nothing confirmed, no
        indicator -- and keeps the identity so a later scenario can raise it again. That is
        a deliberate simulator transition, documented in
        docs/decisions/0004-phase-6-dtc-evidence.md, not a universal SAE or ISO post-clear
        state.
        """
        for entry in self._entries.values():
            entry.clear()
