import asyncio
import json

import pytest

from ecu_simulator.observe.connection import Connection
from ecu_simulator.observe.writer import NotDelivered, run_writer

HELLO = {"type": "hello", "api": 1, "watermark": 5, "oldest_seq": 1}


def conn():
    return Connection(1, watermark=5, published_at_open=5, now=lambda: 0.0)


def identities(c):
    ledger = c.ledger(published_now=5 + c.offered)
    assert ledger["offered"] == ledger["enqueued"] + ledger["client_dropped"]
    assert ledger["enqueued"] == (ledger["sent"] + ledger["delivery_unknown"] + ledger["queued"]
                                  + ledger["discarded_on_close"])
    return ledger


async def settle(n=10):
    for _ in range(n):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_order_is_hello_state_history_then_live():
    c = conn()
    c.set_state("S")
    c.offer("L1")
    sent: list[str] = []
    async def send(text):
        sent.append(text)
    task = asyncio.create_task(run_writer(c, send, HELLO, ["H4", "H5"]))
    await settle()
    c.offer("L2")
    await settle()
    c.close(1000, published_now=7)
    await asyncio.wait_for(task, 1)
    assert [json.loads(sent[0])["type"]] + sent[1:] == ["hello", "S", "H4", "H5", "L1", "L2"]
    assert identities(c)["sent"] == 2                     # history and state are not ledger-counted


@pytest.mark.asyncio
async def test_without_a_state_the_writer_goes_straight_to_history():
    c = conn()
    sent: list[str] = []
    async def send(text):
        sent.append(text)
    task = asyncio.create_task(run_writer(c, send, HELLO, ["H1"]))
    await settle()
    c.close(1000, published_now=5)
    await asyncio.wait_for(task, 1)
    assert sent[1:] == ["H1"]


@pytest.mark.asyncio
async def test_a_known_non_delivery_is_marked_failed_and_raised():
    c = conn()
    c.offer("L1")
    async def send(text):
        if text == "L1":
            raise NotDelivered("socket closing: nothing written")
    with pytest.raises(NotDelivered):
        await run_writer(c, send, HELLO, [])
    ledger = identities(c)
    assert (ledger["sent"], ledger["delivery_unknown"], ledger["queued"], ledger["discarded_on_close"]) == (0, 0, 0, 1)


@pytest.mark.asyncio
async def test_any_other_send_error_is_delivery_unknown():  # owner decision 8
    c = conn()
    c.offer("L1")
    async def send(text):
        if text == "L1":
            raise ConnectionResetError("Connection lost")   # e.g. from the drain wait, after the write
    with pytest.raises(ConnectionResetError):
        await run_writer(c, send, HELLO, [])
    ledger = identities(c)
    assert (ledger["sent"], ledger["delivery_unknown"], ledger["queued"], ledger["discarded_on_close"]) == (0, 1, 0, 0)


@pytest.mark.asyncio
async def test_a_cancelled_send_is_delivery_unknown():
    c = conn()
    c.offer("L1")
    blocked = asyncio.Event()
    async def send(text):
        if text == "L1":
            blocked.set()
            await asyncio.Event().wait()                    # never returns
    task = asyncio.create_task(run_writer(c, send, HELLO, []))
    await asyncio.wait_for(blocked.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    ledger = identities(c)
    assert (ledger["sent"], ledger["delivery_unknown"], ledger["queued"]) == (0, 1, 0)


@pytest.mark.asyncio
async def test_a_forced_close_during_a_blocked_send_resolves_when_the_send_returns():
    c = Connection(1, watermark=5, published_at_open=5, now=lambda: 0.0, max_messages=1, overflow_disconnect_s=0.0)
    c.offer("L1")
    release = asyncio.Event()
    async def send(text):
        if text == "L1":
            await release.wait()
    task = asyncio.create_task(run_writer(c, send, HELLO, []))
    await settle()
    c.offer("L2")                                         # queued
    c.offer("L3")                                         # overflow at t=0 with a 0 s budget: forced 1013
    assert c.closed and c.close_code == 1013
    release.set()
    await asyncio.wait_for(task, 1)
    ledger = identities(c)
    assert (ledger["sent"], ledger["discarded_on_close"], ledger["client_dropped"], ledger["delivery_unknown"]) == (1, 1, 1, 0)
