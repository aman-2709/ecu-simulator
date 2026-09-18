"""Thin adaptation of the legacy UDS service layer to the new transport: bytes in, bytes out.

Behavior is carried over from the removed socket listener: an empty payload gets no
response, everything else goes to ``uds.services`` unchanged.
"""

from __future__ import annotations

from ecu_simulator.uds import services


def handle(payload: bytes) -> bytes | None:
    """Process one UDS request payload; ``None`` means "send nothing"."""
    if len(payload) < 1:
        return None
    response = services.process_service_request(payload)
    return bytes(response) if response is not None else None
