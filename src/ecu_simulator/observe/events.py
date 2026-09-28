"""From an ExchangeRecord to one JSON ``exchange`` message (0010 §5). Runs in the
publisher, off the hot path. Pure functions: no state, no I/O.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ecu_simulator.ecu.router import AddressRouter
from ecu_simulator.observe.handoff import ExchangeRecord
from ecu_simulator.observe.limits import RETAINED_PAYLOAD_BYTES
from ecu_simulator.protocols.obd import masks
from ecu_simulator.protocols.obd.pids import MODE01_PIDS
from ecu_simulator.transport.socketcan import EndpointConfig

OBD_SERVICES = {
    0x01: "current data", 0x02: "freeze frame", 0x03: "stored DTCs", 0x04: "clear DTCs",
    0x05: "oxygen sensor results", 0x06: "on-board monitoring", 0x07: "pending DTCs",
    0x08: "control", 0x09: "vehicle information", 0x0A: "permanent DTCs",
}
UDS_SERVICES = {
    0x10: "DiagnosticSessionControl", 0x11: "ECUReset", 0x14: "ClearDiagnosticInformation",
    0x19: "ReadDTCInformation", 0x3E: "TesterPresent",
}


def classify(record: ExchangeRecord, router: AddressRouter) -> tuple[str, str | None]:
    routes = router.resolve(record.request)
    ecu = routes[0].ecu if routes else None
    if record.error is not None:
        return "error", ecu
    if record.response is not None:
        return "responded", ecu
    return ("no_response", ecu) if routes else ("unrouted", None)


def summarise(payload: bytes) -> str:
    if not payload:
        return "empty request"
    sid = payload[0]
    if sid == 0x01 and len(payload) == 2:
        base = payload[1]
        if masks.is_range_request(base) and 0 <= base <= masks.LAST_RANGE_BASE:
            first, last = base + 1, base + masks.RANGE_SIZE
            return f"OBD 01 {base:02X} — supported PIDs {first:02X}–{last:02X}"
        pid = MODE01_PIDS.get(base)
        return f"OBD 01 {base:02X} — {pid.name if pid else 'unknown parameter'}"
    if sid == 0x01 and len(payload) > 2:
        pids = " ".join(f"{b:02X}" for b in payload[1:])
        return f"OBD 01 {pids} — {len(payload) - 1} parameters"
    if sid in OBD_SERVICES:
        head = " ".join(f"{b:02X}" for b in payload[:2])
        return f"OBD {head} — {OBD_SERVICES[sid]}"
    if sid in UDS_SERVICES:
        return f"UDS {sid:02X} — {UDS_SERVICES[sid]}"
    return f"service 0x{sid:02X}"


def _retained(payload: bytes | None) -> tuple[str | None, int, bool]:
    if payload is None:
        return None, 0, False
    return payload[:RETAINED_PAYLOAD_BYTES].hex(), len(payload), len(payload) > RETAINED_PAYLOAD_BYTES


def encode_exchange(
    record: ExchangeRecord,
    router: AddressRouter,
    endpoints: Mapping[str, EndpointConfig],
    wall_origin: tuple[float, int],
) -> str:
    outcome, ecu = classify(record, router)
    endpoint = record.request.context if isinstance(record.request.context, EndpointConfig) else None
    tx_id: int | None = None
    if endpoint is not None:
        via = endpoints.get(endpoint.reply_via) if endpoint.reply_via else endpoint
        tx_id = via.address.tx_id if via is not None else None
    request_hex, request_len, request_truncated = _retained(record.request.payload)
    response_hex, response_len, response_truncated = _retained(record.response.payload if record.response else None)
    wall = wall_origin[0] + (record.t0_ns - wall_origin[1]) / 1e9
    event: dict[str, Any] = {
        "type": "exchange",
        "seq": record.seq,
        "t": datetime.fromtimestamp(wall, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "ecu": ecu,
        "endpoint": endpoint.name if endpoint else None,
        "rx_id": f"0x{record.request.target_address:x}",
        "tx_id": f"0x{tx_id:x}" if tx_id is not None else None,
        "functional": record.request.functional,
        "request": request_hex, "request_len": request_len, "request_truncated": request_truncated,
        "response": response_hex, "response_len": response_len, "response_truncated": response_truncated,
        "outcome": outcome,
        "error": record.error,
        "dispatch_us": record.elapsed_ns // 1000,
        "summary": summarise(record.request.payload),
    }
    return json.dumps(event, separators=(",", ":"), ensure_ascii=False)
