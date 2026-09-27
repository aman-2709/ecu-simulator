"""Read-only views of a Runtime (0010 §5). Never calls ``sync()`` (0010 §2.1): state is
reported as last applied, with the scenario time it was applied at.
"""

from __future__ import annotations

import json
import time
from typing import Any

from ecu_simulator import app
from ecu_simulator.observe.limits import STATE_MAX_BYTES


def vehicle(runtime: app.Runtime) -> dict[str, Any]:
    return {
        "kind": runtime.vehicle.powertrain.kind,
        "vin": runtime.vehicle.common.vin,
        "signals": dict(runtime.vehicle.signals),
        "as_of": runtime.runner.last_applied if runtime.runner is not None else None,
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


def state_message(runtime: app.Runtime) -> str:
    return json.dumps({"type": "state", "vehicle": vehicle(runtime), "dtcs": dtcs(runtime)}, separators=(",", ":"))


def check_state_size(runtime: app.Runtime) -> None:
    size = len(state_message(runtime).encode())
    if size > STATE_MAX_BYTES:
        raise ValueError(f"state message is {size} bytes, over the 256 KiB limit (decisions/0010 §4.3)")


def status(
    runtime: app.Runtime, publisher: Any, issued: int, started_at: float, version: str, profile: str,
) -> dict[str, Any]:
    """``GET /status`` (0010 §5). ``profile`` is passed in because the Runtime does not know
    where its profile came from: M2 passes the path the command line loaded.
    """
    runner = runtime.runner
    return {
        "version": version, "interface": runtime.config.interface, "profile": profile,
        "started_at": started_at, "uptime_s": time.time() - started_at,
        "scenario": {"enabled": runner is not None,
                     "t_last_applied": runner.last_applied if runner else None,
                     "pending_events": runner.pending_events if runner else 0},
        "api": publisher.stats(issued),
    }
