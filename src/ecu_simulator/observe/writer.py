"""One WebSocket client's writer task: the contract in connection.py, as code.

Standard library only. ``send`` is the socket's ``send_str``, injected, so the contract is
tested without aiohttp and holds for any transport M2 or later puts behind it.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from ecu_simulator.observe.connection import Connection

Send = Callable[[str], Awaitable[None]]


class NotDelivered(Exception):
    """Raised by a ``send`` that knows nothing was written: the only failure counted as failed."""


async def run_writer(conn: Connection, send: Send, hello: dict[str, Any], history: list[str]) -> None:
    """hello, the current state, the history, then live messages until ``conn`` closes.

    Returns when the connection is closed. Raises what ``send`` raises, after resolving the
    ledger for the message it was sending.
    """
    await send(json.dumps(hello, separators=(",", ":")))
    state = conn.take_state()
    if state is not None:
        await send(state)
    for item in history:
        await send(item)
    while not conn.closed:
        text = conn.next_message()
        if text is None:
            await conn.wait_changed()
            continue
        try:
            await send(text)
        except NotDelivered:
            conn.mark_failed()    # known: nothing was written
            raise
        except BaseException:     # any other error, or cancellation: it may have been written
            conn.mark_unknown()
            raise
        conn.mark_sent()
