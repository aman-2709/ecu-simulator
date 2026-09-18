"""Thin adaptation of the legacy OBD service layer to the new transport: bytes in, bytes out.

Request parsing is carried over verbatim from the removed socket listener so that the
wire behavior stays identical: only the first two bytes are looked at (DEV-18: a
request with several PIDs answers the first PID only).
"""

from __future__ import annotations

import logging

from ecu_simulator.obd import services

logger = logging.getLogger(__name__)


def get_sid_and_pid(request: bytes | None) -> tuple[int | None, int | None]:
    """Return ``(pid, sid)`` from the first two request bytes, ``None`` where absent."""
    pid, sid = None, None
    if request is not None:
        request_bytes_length = len(request)
        if request_bytes_length >= 1:
            sid = request[0]
        if request_bytes_length >= 2:
            pid = request[1]
    return pid, sid


def handle(payload: bytes) -> bytes | None:
    """Process one OBD request payload; ``None`` means "send nothing"."""
    requested_pid, requested_sid = get_sid_and_pid(payload)
    if requested_sid is None:
        return None
    response = services.process_service_request(requested_sid, requested_pid)
    return bytes(response) if response is not None else None
