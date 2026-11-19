"""Read-only views of a Runtime (0010 §5). Never calls ``sync()`` (0010 §2.1): state is
reported as last applied, with the scenario time it was applied at.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Sequence
from typing import Any

from ecu_simulator import app
from ecu_simulator.observe.limits import STATE_MAX_BYTES


def vehicle(runtime: app.Runtime, unavailable: Sequence[str]) -> dict[str, Any]:
    """``unavailable`` is ``availability.unavailable(profile)``, computed once by the caller
    (0010 §5, ninth revision). ``signals`` still carries every stored value, listed or not.

    A ``float`` signal that is not ``math.isfinite`` is sanitised to ``None`` and its path
    listed in ``nonfinite`` -- unless it is already in ``unavailable``, which wins (§8.2): a
    path with no source cannot also have produced an invalid reading. Builds new containers;
    never touches ``runtime``.
    """
    unavailable_set = set(unavailable)
    signals: dict[str, Any] = {}
    nonfinite = []
    for path, value in runtime.vehicle.signals.items():
        if isinstance(value, float) and not math.isfinite(value):
            signals[path] = None
            if path not in unavailable_set:
                nonfinite.append(path)
        else:
            signals[path] = value
    nonfinite.sort()
    return {
        "kind": runtime.vehicle.powertrain.kind,
        "vin": runtime.vehicle.common.vin,
        "signals": signals,
        "as_of": runtime.runner.last_applied if runtime.runner is not None else None,
        "unavailable": list(unavailable),
        "nonfinite": nonfinite,
    }


def dtcs(runtime: app.Runtime) -> dict[str, Any]:
    return {
        ecu.name: {
            "codes": [{"code": s.code, "pending": s.pending, "confirmed": s.confirmed,
                       "indicator_requested": s.indicator_requested} for s in ecu.dtc_store],
            "mil": ecu.dtc_store.indicator_on,
        }
        for ecu in runtime.ecus
    }


def ecus(runtime: app.Runtime) -> dict[str, Any]:
    endpoints = app.build_endpoints(runtime.config)
    return {
        ecu.name: {
            "endpoints": [
                {"name": e.name, "rx_id": f"0x{e.address.rx_id:x}", "tx_id": f"0x{e.address.tx_id:x}",
                 "functional": e.functional, "receive": e.receive, "reply_via": e.reply_via,
                 "padding": e.options.tx_padding}
                for e in endpoints if e.name.startswith(f"{ecu.name}.")
            ],
            "protocols": [{"name": p.name, "sids": sorted(p.service_ids)} for p in ecu.protocols],
        }
        for ecu in runtime.ecus
    }


def state_message(runtime: app.Runtime, unavailable: Sequence[str]) -> str:
    # allow_nan=False is the guard of last resort (§8.3, §14.1 C11): vehicle() already
    # sanitises signals, but dtcs() and as_of are not sanitised, so a non-finite value
    # reaching either of those raises ValueError here instead of producing invalid JSON.
    return json.dumps({"type": "state", "vehicle": vehicle(runtime, unavailable), "dtcs": dtcs(runtime)},
                      separators=(",", ":"), allow_nan=False)


def check_state_size(text: str) -> None:
    """The size rule, on an already encoded ``state_message``: the caller encodes it once."""
    size = len(text.encode())
    if size > STATE_MAX_BYTES:
        raise ValueError(f"state message is {size} bytes, over the 256 KiB limit (decisions/0010 §4.3)")


def status(
    runtime: app.Runtime, publisher: Any, issued: int, started_at: float, version: str, profile: str,
) -> dict[str, Any]:
    """``GET /status`` (0010 §5). ``profile`` is passed in because the Runtime does not know
    where its profile came from: M2 passes the path the command line loaded.
    """
    runner = runtime.runner
    # /status stays lenient, to answer during a state-encoding failure: so a non-finite
    # t_last_applied is sent as null here (M3b §8.2), never as a non-JSON token.
    t_last_applied = runner.last_applied if runner else None
    if isinstance(t_last_applied, float) and not math.isfinite(t_last_applied):
        t_last_applied = None
    return {
        "version": version, "interface": runtime.config.interface, "profile": profile,
        "started_at": started_at, "uptime_s": time.time() - started_at,
        "scenario": {"enabled": runner is not None,
                     "t_last_applied": t_last_applied,
                     "pending_events": runner.pending_events if runner else 0},
        "api": publisher.stats(issued),
    }
