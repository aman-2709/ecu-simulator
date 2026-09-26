# GUI M1 — Observer core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `ecu_simulator.observe`, a stdlib-only observer core, and prove that it
changes no reply, never mutates simulator state, and never holds the event loop for more
than one bounded turn. The core is: `HandOff`, `ObservedDispatcher`, `Publisher`, the
history ring, per-connection queues and ledgers, and snapshots.

**Architecture:** `ObservedDispatcher` wraps the existing `Dispatcher`. On each call it
appends one immutable record to a bounded `HandOff`, and does nothing else. A `Publisher`
task drains `HandOff` in bounded turns. It classifies and summarises each record, encodes
it to JSON once, appends it to a bounded history ring, and offers it to bounded
per-connection queues that each keep a ledger. Snapshots read `Runtime` and never call
`sync()`. **M1 does not touch `app.py`, `cli.py`, the transport or any protocol code.**
Nothing is wired into a running simulator until M2.

**Tech Stack:** Python 3.12+, stdlib only (`asyncio`, `collections`, `json`, `time`,
`dataclasses`), pytest with pytest-asyncio in strict mode. The existing
`tests/unit/fakes.py` provides the fake ISO-TP sockets.

**Spec:** [docs/decisions/0010-gui-observer-api.md](../decisions/0010-gui-observer-api.md)
at `310e56d`. Read it first. Section numbers below refer to it.

## Global Constraints

- Branch `gui`, worktree `.claude/worktrees/gui`. Never commit to `modernization`. Never
  merge. Push only when the owner asks.
- **No `Co-Authored-By` trailer** on any commit in this project.
- `ecu_simulator.observe` imports **only** the standard library and `ecu_simulator.*`
  (0010 §4.1).
- **Files M1 must not modify:** `src/ecu_simulator/app.py`, `cli.py`, anything under
  `transport/`, `protocols/`, `ecu/`, `vehicle/`, `dtc/` and `scenario/`, the profiles,
  `pyproject.toml` and `.github/`. Task 9 checks this with a diff.
- Limits, verbatim from 0010 §4.3:
  - `HandOff`: 4096 records / 1 MiB of payload bytes;
  - retained payload: 512 bytes;
  - history: 500 events / 2 MiB of encoded JSON;
  - per-client queue: 1024 messages / 4 MiB;
  - clients: 4;
  - forced disconnect after 5 s of continuous overflow, close code 1013;
  - `state` message: 256 KiB, at most 4 Hz;
  - publisher turn: 64 records / 1 ms;
  - closed ledgers kept: 64.
- `outcome` is exactly one of `responded`, `no_response`, `unrouted`, `error` (0010 §5).
- Snapshots **never call `sync()`** (0010 §2.1).
- Run `mypy` **bare**, never `mypy src tests`. Run `ruff check .` and never `ruff format`
  wholesale. Use `set -o pipefail` whenever pytest output is piped.
- Any test that spawns Python uses `sys.executable`, never a `.venv` path.
- The host's default `python3` is 3.10. Use the repository's `.venv/bin/python`, which is
  3.12.

## Deviation from 0010, for owner review

0010 §10 puts "the §9.1 proofs" in M1. Two of the three depend on wiring that M1 does not
build, so they move to M2:

- `run()` hands the transport the unwrapped `Dispatcher` when the API is off;
- aiohttp is never imported when the API is off.

**M1 keeps** the third proof, the differential comparison (Task 2), and adds two checks
that fit a milestone which leaves `run()` alone: `observe` imports nothing outside the
standard library (Task 1), and M1 changes no existing source file (Task 9).

## Review Focus

These are the five inputs or conditions most likely to bite that no spec sentence makes
into a test. Each is pinned by a test in the task named.

1. **A handler that raises a `BaseException` subclass other than `Exception`**, such as
   `KeyboardInterrupt` during shutdown: the wrapper must still record `error` and
   re-raise, and must not swallow it. Task 2, `test_base_exceptions_are_recorded_and_reraised`.
2. **A request whose `context` is not an `EndpointConfig`**, for example `None`, as in
   unit tests and any non-transport caller: the publisher must still produce an event,
   with `endpoint`, `rx`/`tx` and `functional` taken from the request, and `tx_id: null`.
   Task 3, `test_event_without_endpoint_context`.
3. **A response or request exactly at, or one past, the 512-byte retention limit**: the
   flags and lengths must be exact. Task 3, `test_truncation_boundary`.
4. **`after=` pointing beyond the newest event, or at 0 on an empty ring**: return no
   events and `gap: false`, and never raise. Task 4, `test_after_beyond_newest_and_empty_ring`.
5. **A connection closed twice, or closed while overflowing**: the ledger must stay final
   and the identities must hold. Task 5, `test_close_is_idempotent_and_final`.

---

## File structure

| File | Responsibility |
|---|---|
| `src/ecu_simulator/observe/__init__.py` | Public names only |
| `src/ecu_simulator/observe/limits.py` | Every §4.3 constant, in one place |
| `src/ecu_simulator/observe/handoff.py` | `ExchangeRecord`, `HandOff` |
| `src/ecu_simulator/observe/wrapper.py` | `ObservedDispatcher` |
| `src/ecu_simulator/observe/events.py` | `classify`, `summarise`, `encode_exchange`: pure functions from a record to a JSON event |
| `src/ecu_simulator/observe/history.py` | `HistoryRing` |
| `src/ecu_simulator/observe/connection.py` | `Connection`: the per-client queue, one-slot `state` / `dropped`, the ledger |
| `src/ecu_simulator/observe/publisher.py` | `Publisher`: bounded drain, fan-out, `connect()` watermark handshake, stats, `state` push |
| `src/ecu_simulator/observe/snapshots.py` | `status`, `vehicle`, `dtcs`, `ecus`, `check_state_size` |
| `tests/unit/observe/…` | One test module per source module, plus `test_ordering.py` and `test_differential.py` |
| `scripts/gui_m1_early_check.py` | The M1 early performance check (0010 §9.2) |
| `docs/validation/gui-m1-early-check.md` | Its recorded result |

`tests/unit/observe/__init__.py` must exist, because the test packages are importable
(`tests.unit.fakes`).

---

### Task 1: `limits`, `ExchangeRecord` and `HandOff`

**Files:**
- Create: `src/ecu_simulator/observe/__init__.py`, `src/ecu_simulator/observe/limits.py`, `src/ecu_simulator/observe/handoff.py`
- Test: `tests/unit/observe/__init__.py` (empty), `tests/unit/observe/test_handoff.py`, `tests/unit/observe/test_imports.py`

**Interfaces:**
- Produces:
  - `ExchangeRecord(seq: int, t0_ns: int, elapsed_ns: int, request: DiagnosticRequest, response: DiagnosticResponse | None, error: str | None)`, a `NamedTuple`;
  - `HandOff(max_records: int = HANDOFF_MAX_RECORDS, max_bytes: int = HANDOFF_MAX_BYTES)`, with:
    - `.append(record) -> bool`, which returns `False` on a drop;
    - `.popleft() -> ExchangeRecord`;
    - `__len__`, `.bytes: int`, `.dropped: int`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/observe/test_handoff.py`:

```python
from ecu_simulator.observe.handoff import ExchangeRecord, HandOff
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse


def record(seq: int, request: bytes = b"\x01\x0c", response: bytes | None = b"\x41\x0c\x0c\x80") -> ExchangeRecord:
    return ExchangeRecord(
        seq, 0, 0, DiagnosticRequest(request, 0x7DF, functional=True),
        DiagnosticResponse(response) if response is not None else None, None,
    )


def test_records_come_out_in_order():
    handoff = HandOff()
    for seq in (1, 2, 3):
        assert handoff.append(record(seq))
    assert [handoff.popleft().seq for _ in range(3)] == [1, 2, 3]
    assert len(handoff) == 0 and handoff.bytes == 0


def test_count_limit_drops_the_new_record_and_counts_it():
    handoff = HandOff(max_records=2, max_bytes=10_000)
    assert handoff.append(record(1)) and handoff.append(record(2))
    assert handoff.append(record(3)) is False
    assert handoff.dropped == 1
    assert [handoff.popleft().seq for _ in range(2)] == [1, 2]  # the new one was dropped, not the oldest


def test_byte_limit_counts_request_and_response_lengths():
    handoff = HandOff(max_records=100, max_bytes=12)
    assert handoff.append(record(1))                   # 2 + 4 = 6 bytes
    assert handoff.append(record(2))                   # 12 bytes: exactly at the limit
    assert handoff.append(record(3)) is False          # 18 > 12
    assert handoff.bytes == 12 and handoff.dropped == 1
    handoff.popleft()
    assert handoff.bytes == 6


def test_a_silent_record_counts_only_its_request():
    handoff = HandOff(max_records=10, max_bytes=2)
    assert handoff.append(record(1, response=None))
    assert handoff.bytes == 2
```

`tests/unit/observe/test_imports.py`:

```python
import ast
import pathlib
import sys

OBSERVE = pathlib.Path(__file__).resolve().parents[3] / "src" / "ecu_simulator" / "observe"


def test_observe_imports_only_stdlib_and_the_package():
    # 0010 §4.1: observe has no third-party dependency, so ordinary CI proves it on every run.
    offenders = []
    for path in sorted(OBSERVE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 else []
            )
            for name in names:
                top = name.split(".")[0]
                if top != "ecu_simulator" and top not in sys.stdlib_module_names and top != "__future__":
                    offenders.append(f"{path.name}: {name}")
    assert offenders == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/observe -q -p no:cacheprovider`
Expected: collection error, `ModuleNotFoundError: No module named 'ecu_simulator.observe'`

- [ ] **Step 3: Implement**

`src/ecu_simulator/observe/limits.py`:

```python
"""Every limit in decisions/0010 §4.3, in one place. Constants, not configuration, in v1."""

HANDOFF_MAX_RECORDS = 4096
HANDOFF_MAX_BYTES = 1 << 20
RETAINED_PAYLOAD_BYTES = 512
HISTORY_MAX_EVENTS = 500
HISTORY_MAX_BYTES = 2 << 20
CLIENT_MAX_MESSAGES = 1024
CLIENT_MAX_BYTES = 4 << 20
MAX_CLIENTS = 4
OVERFLOW_DISCONNECT_S = 5.0
CLOSE_TOO_SLOW = 1013
STATE_MAX_BYTES = 256 << 10
STATE_MIN_INTERVAL_S = 0.25
TURN_MAX_RECORDS = 64
TURN_MAX_S = 0.001
CLOSED_LEDGERS_KEPT = 64
```

`src/ecu_simulator/observe/handoff.py`:

```python
"""The single bounded buffer between the dispatch hot path and everything else (0010 §4.2).

``append`` is the only thing the hot path calls. It never copies a payload and never
formats anything: the record holds references to bytes that already exist.
"""

from __future__ import annotations

from collections import deque
from typing import NamedTuple

from ecu_simulator.observe.limits import HANDOFF_MAX_BYTES, HANDOFF_MAX_RECORDS
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse


class ExchangeRecord(NamedTuple):
    seq: int
    t0_ns: int
    elapsed_ns: int
    request: DiagnosticRequest
    response: DiagnosticResponse | None
    error: str | None


def _size(record: ExchangeRecord) -> int:
    return len(record.request.payload) + (len(record.response.payload) if record.response is not None else 0)


class HandOff:
    def __init__(self, max_records: int = HANDOFF_MAX_RECORDS, max_bytes: int = HANDOFF_MAX_BYTES) -> None:
        self._records: deque[ExchangeRecord] = deque()
        self._max_records = max_records
        self._max_bytes = max_bytes
        self.bytes = 0
        self.dropped = 0

    def __len__(self) -> int:
        return len(self._records)

    def append(self, record: ExchangeRecord) -> bool:
        """Keep ``record``, or drop it and count the drop. The dispatcher never sees a drop."""
        size = _size(record)
        if len(self._records) >= self._max_records or self.bytes + size > self._max_bytes:
            self.dropped += 1
            return False
        self._records.append(record)
        self.bytes += size
        return True

    def popleft(self) -> ExchangeRecord:
        record = self._records.popleft()
        self.bytes -= _size(record)
        return record
```

`src/ecu_simulator/observe/__init__.py`:

```python
"""Opt-in, read-only observation of a running simulator (decisions/0010).

Standard library only. Nothing here runs unless something constructs it: M1 wires none
of it into ``app.run``.
"""
```

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/observe -q -p no:cacheprovider`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/ecu_simulator/observe tests/unit/observe
git commit -m "feat(observe): bounded HandOff between the dispatch path and the observer"
```

---

### Task 2: `ObservedDispatcher` and the differential proof

**Files:**
- Create: `src/ecu_simulator/observe/wrapper.py`
- Test: `tests/unit/observe/test_wrapper.py`, `tests/unit/observe/test_differential.py`

**Interfaces:**
- Consumes: `HandOff`, `ExchangeRecord` (Task 1); `ecu_simulator.ecu.dispatcher.Dispatcher` (unchanged).
- Produces:
  - `ObservedDispatcher(inner: Callable[[DiagnosticRequest], DiagnosticResponse | None], handoff: HandOff, wake: Callable[[], None], clock_ns: Callable[[], int] = time.monotonic_ns)`, with:
    - `__call__(request) -> DiagnosticResponse | None`;
    - `.issued: int`, the number of sequence numbers issued;
    - `.inner`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/observe/test_wrapper.py`:

```python
import pytest

from ecu_simulator.observe.handoff import HandOff
from ecu_simulator.observe.wrapper import ObservedDispatcher
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse

REQ = DiagnosticRequest(b"\x01\x0c", 0x7DF, functional=True)


def wrapped(inner, handoff=None, wakes=None):
    handoff = handoff if handoff is not None else HandOff()
    ticks = iter(range(0, 10_000_000, 1000))       # each clock read advances 1000 ns
    wake = (lambda: wakes.append(1)) if wakes is not None else (lambda: None)
    return ObservedDispatcher(inner, handoff, wake, clock_ns=lambda: next(ticks)), handoff


def test_reply_is_returned_unchanged_and_one_record_is_kept():
    reply = DiagnosticResponse(b"\x41\x0c\x0c\x80")
    wakes: list[int] = []
    observed, handoff = wrapped(lambda request: reply, wakes=wakes)
    assert observed(REQ) is reply
    rec = handoff.popleft()
    assert (rec.seq, rec.elapsed_ns, rec.request, rec.response, rec.error) == (1, 1000, REQ, reply, None)
    assert len(handoff) == 0 and wakes == [1] and observed.issued == 1


def test_sequence_numbers_are_issued_even_when_handoff_drops():
    observed, handoff = wrapped(lambda request: None, handoff=HandOff(max_records=1))
    for _ in range(3):
        observed(REQ)
    assert observed.issued == 3 and handoff.dropped == 2 and handoff.popleft().seq == 1


def test_exceptions_are_recorded_and_reraised():
    def boom(request):
        raise ValueError("bad")
    observed, handoff = wrapped(boom)
    with pytest.raises(ValueError, match="bad"):
        observed(REQ)
    rec = handoff.popleft()
    assert rec.error == "ValueError" and rec.response is None


def test_base_exceptions_are_recorded_and_reraised():  # Review Focus 1
    def interrupted(request):
        raise KeyboardInterrupt
    observed, handoff = wrapped(interrupted)
    with pytest.raises(KeyboardInterrupt):
        observed(REQ)
    assert handoff.popleft().error == "KeyboardInterrupt"


def test_the_hot_path_never_calls_anything_but_wake():  # O3
    calls: list[str] = []
    handoff = HandOff()
    observed = ObservedDispatcher(lambda r: None, handoff, lambda: calls.append("wake"))
    observed(REQ)
    assert calls == ["wake"]
```

`tests/unit/observe/test_differential.py`:

```python
"""0010 §9.1: wrapping changes no reply. Two runtimes built from one profile are driven in
lockstep with the same request sequence, one bare and one wrapped. Every reply must be
byte-identical, including after the DTC-clearing requests, which mutate both sides alike.
"""

import itertools

from ecu_simulator import app
from ecu_simulator.cli import default_profile_path
from ecu_simulator.config import load_profile
from ecu_simulator.observe.handoff import HandOff
from ecu_simulator.observe.wrapper import ObservedDispatcher
from ecu_simulator.transport import DiagnosticRequest

ADDRESSES = ((0x7DF, True), (0x7E0, False), (0x7E1, False))


def requests():
    runtime = app.build_runtime(app.RuntimeConfig.build(load_profile(default_profile_path()), "vcan0"))
    sids = sorted({sid for ecu in runtime.ecus for protocol in ecu.protocols for sid in protocol.service_ids})
    for (address, functional), sid in itertools.product(ADDRESSES, sids):
        yield DiagnosticRequest(bytes([sid]), address, functional=functional)
        for second, length in itertools.product(range(256), range(2, 6)):
            yield DiagnosticRequest(bytes([sid, second]) + bytes(length - 2), address, functional=functional)


def test_wrapping_changes_no_reply():
    config = app.RuntimeConfig.build(load_profile(default_profile_path()), "vcan0")
    bare = app.build_runtime(config).dispatcher
    observed = ObservedDispatcher(app.build_runtime(config).dispatcher, HandOff(max_records=1), lambda: None)
    compared = 0
    for request in requests():
        a, b = bare(request), observed(request)
        assert (a.payload if a else None) == (b.payload if b else None), request
        compared += 1
    assert compared > 40_000  # "tens of thousands", 0010 §9.1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/observe/test_wrapper.py tests/unit/observe/test_differential.py -q -p no:cacheprovider`
Expected: `ModuleNotFoundError: No module named 'ecu_simulator.observe.wrapper'`

- [ ] **Step 3: Implement**

`src/ecu_simulator/observe/wrapper.py`:

```python
"""The only observer code on the dispatch path (0010 §4.2): time the call, append one record.

The transport sends the reply only after this returns, so everything here is added to
request-to-reply latency. That is why this is all it does.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from ecu_simulator.observe.handoff import ExchangeRecord, HandOff
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse

Handler = Callable[[DiagnosticRequest], DiagnosticResponse | None]


class ObservedDispatcher:
    def __init__(
        self,
        inner: Handler,
        handoff: HandOff,
        wake: Callable[[], None],
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self.inner = inner
        self._handoff = handoff
        self._wake = wake
        self._clock_ns = clock_ns
        self.issued = 0

    def __call__(self, request: DiagnosticRequest) -> DiagnosticResponse | None:
        self.issued += 1
        seq = self.issued
        t0 = self._clock_ns()
        try:
            response = self.inner(request)
        except BaseException as error:
            self._handoff.append(ExchangeRecord(seq, t0, self._clock_ns() - t0, request, None, type(error).__name__))
            self._wake()
            raise
        self._handoff.append(ExchangeRecord(seq, t0, self._clock_ns() - t0, request, response, None))
        self._wake()
        return response
```

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/observe -q -p no:cacheprovider`
Expected: all pass. The differential test compares more than 40,000 requests (15 SIDs ×
3 addresses × (1 + 256 × 4)) and takes a few seconds.

- [ ] **Step 5: Commit**

```bash
git add src/ecu_simulator/observe/wrapper.py tests/unit/observe/test_wrapper.py tests/unit/observe/test_differential.py
git commit -m "feat(observe): ObservedDispatcher, proved reply-transparent by a lockstep differential"
```

---

### Task 3: classification, summaries and event encoding

**Files:**
- Create: `src/ecu_simulator/observe/events.py`
- Test: `tests/unit/observe/test_events.py`

**Interfaces:**
- Consumes: `ExchangeRecord` (Task 1); `AddressRouter.resolve(request) -> tuple[Route, ...]`; `EndpointConfig` (fields `name`, `address.rx_id`, `address.tx_id`, `functional`, `reply_via`); `MODE01_PIDS`.
- Produces:
  - `classify(record, router) -> tuple[str, str | None]`, returning `(outcome, ecu_name)`;
  - `summarise(payload: bytes) -> str`;
  - `encode_exchange(record, router, endpoints: Mapping[str, EndpointConfig], wall_origin: tuple[float, int]) -> str`, the JSON text of one `exchange` message. `wall_origin` is `(time.time(), time.monotonic_ns())` captured once.

- [ ] **Step 1: Write the failing tests**

`tests/unit/observe/test_events.py`:

```python
import json

from ecu_simulator.ecu.router import AddressRouter
from ecu_simulator.observe.events import classify, encode_exchange, summarise
from ecu_simulator.observe.handoff import ExchangeRecord
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse
from ecu_simulator.transport.socketcan import EndpointConfig, IsoTpAddress

FUNC = EndpointConfig("engine.obd_functional", IsoTpAddress(0x7DF, 0x7E8), functional=True, reply_via="engine.obd_physical")
PHYS = EndpointConfig("engine.obd_physical", IsoTpAddress(0x7E0, 0x7E8))
ENDPOINTS = {e.name: e for e in (FUNC, PHYS)}
ORIGIN = (1_790_000_000.0, 0)


def router() -> AddressRouter:
    r = AddressRouter()
    r.add_functional(0x7DF, "engine", {"obd"})
    r.add_physical(0x7E0, "engine", {"obd", "uds"})
    return r


def rec(payload, response=b"\x41\x0c\x0c\x80", error=None, address=0x7DF, functional=True, context=FUNC):
    request = DiagnosticRequest(payload, address, functional=functional, context=context)
    return ExchangeRecord(7, 1_500_000, 41_000, request, DiagnosticResponse(response) if response else None, error)


def test_the_four_outcomes():
    assert classify(rec(b"\x01\x0c"), router()) == ("responded", "engine")
    assert classify(rec(b"\x07", response=None), router()) == ("no_response", "engine")
    assert classify(rec(b"\x01\x0c", response=None, address=0x7AA, functional=False, context=None), router()) == ("unrouted", None)
    assert classify(rec(b"\x01\x0c", response=None, error="ValueError"), router()) == ("error", "engine")


def test_summaries():
    assert summarise(b"\x01\x0c") == "OBD 01 0C — Engine speed"
    assert summarise(b"\x01\x0c\x0d") == "OBD 01 0C 0D — 2 parameters"
    assert summarise(b"\x09\x02") == "OBD 09 02 — vehicle information"
    assert summarise(b"\x19\x02\xff") == "UDS 19 — ReadDTCInformation"
    assert summarise(b"\x22\xf1\x90") == "service 0x22"


def test_encoded_exchange_carries_the_reply_id_of_reply_via():
    event = json.loads(encode_exchange(rec(b"\x01\x0c"), router(), ENDPOINTS, ORIGIN))
    assert event["type"] == "exchange" and event["seq"] == 7
    assert (event["endpoint"], event["rx_id"], event["tx_id"], event["functional"]) == ("engine.obd_functional", "0x7df", "0x7e8", True)
    assert (event["request"], event["response"], event["outcome"], event["dispatch_us"]) == ("010c", "410c0c80", "responded", 41)
    assert event["t"].endswith("Z")


def test_event_without_endpoint_context():  # Review Focus 2
    event = json.loads(encode_exchange(rec(b"\x01\x0c", context=None), router(), ENDPOINTS, ORIGIN))
    assert event["endpoint"] is None and event["tx_id"] is None and event["rx_id"] == "0x7df"


def test_truncation_boundary():  # Review Focus 3
    at = json.loads(encode_exchange(rec(b"\x01" + bytes(511)), router(), ENDPOINTS, ORIGIN))
    over = json.loads(encode_exchange(rec(b"\x01" + bytes(512)), router(), ENDPOINTS, ORIGIN))
    assert (at["request_len"], at["request_truncated"], len(at["request"])) == (512, False, 1024)
    assert (over["request_len"], over["request_truncated"], len(over["request"])) == (513, True, 1024)
```

`AddressRouter.add_physical(address, ecu, protocols)` and `add_functional(address, ecu,
protocols)` are the existing builder API (`ecu/router.py:51,76`), as used in
`tests/unit/test_router.py`.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/observe/test_events.py -q -p no:cacheprovider`
Expected: `ModuleNotFoundError: No module named 'ecu_simulator.observe.events'`

- [ ] **Step 3: Implement**

`src/ecu_simulator/observe/events.py`:

```python
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
        pid = MODE01_PIDS.get(payload[1])
        return f"OBD 01 {payload[1]:02X} — {pid.name if pid else 'unknown parameter'}"
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/observe/test_events.py -q -p no:cacheprovider`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/ecu_simulator/observe/events.py tests/unit/observe/test_events.py
git commit -m "feat(observe): exchange events with the four outcomes, summaries and bounded payloads"
```

---

### Task 4: `HistoryRing`

**Files:**
- Create: `src/ecu_simulator/observe/history.py`
- Test: `tests/unit/observe/test_history.py`

**Interfaces:**
- Produces:
  - `HistoryRing(max_events=HISTORY_MAX_EVENTS, max_bytes=HISTORY_MAX_BYTES)`, with:
    - `.add(seq: int, text: str) -> None`;
    - `.oldest_seq: int | None`;
    - `.last_seq: int`, which is 0 when nothing has been published;
    - `.since(after: int | None, limit: int) -> tuple[list[str], bool]`, returning `(texts oldest first, gap)`;
    - `.snapshot() -> list[tuple[int, str]]`.

- [ ] **Step 1: Write the failing tests**

```python
from ecu_simulator.observe.history import HistoryRing


def filled(n, **kw):
    ring = HistoryRing(**kw)
    for seq in range(1, n + 1):
        ring.add(seq, f"e{seq}")
    return ring


def test_count_limit_evicts_oldest():
    ring = filled(5, max_events=3)
    assert ring.oldest_seq == 3 and ring.last_seq == 5
    assert ring.since(None, 10) == (["e3", "e4", "e5"], False)


def test_byte_limit_evicts_oldest():
    ring = filled(4, max_events=100, max_bytes=5)   # each text is 2 bytes
    assert ring.oldest_seq == 3


def test_after_returns_newer_only_and_flags_gaps():
    ring = filled(10, max_events=5)                  # holds 6..10
    assert ring.since(7, 10) == (["e8", "e9", "e10"], False)
    assert ring.since(5, 10) == (["e6", "e7", "e8", "e9", "e10"], False)   # 5 = oldest - 1: no gap
    assert ring.since(2, 10) == (["e6", "e7", "e8", "e9", "e10"], True)
    assert ring.since(None, 2) == (["e9", "e10"], False)


def test_after_beyond_newest_and_empty_ring():  # Review Focus 4
    assert filled(3).since(99, 10) == ([], False)
    assert HistoryRing().since(0, 10) == ([], False)
    assert HistoryRing().oldest_seq is None and HistoryRing().last_seq == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/observe/test_history.py -q -p no:cacheprovider`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
"""The last N published exchange events, bounded by count and by encoded bytes (0010 §4.3, §4.5)."""

from __future__ import annotations

from collections import deque

from ecu_simulator.observe.limits import HISTORY_MAX_BYTES, HISTORY_MAX_EVENTS


class HistoryRing:
    def __init__(self, max_events: int = HISTORY_MAX_EVENTS, max_bytes: int = HISTORY_MAX_BYTES) -> None:
        self._events: deque[tuple[int, str]] = deque()
        self.max_events = max_events
        self._max_events = max_events
        self._max_bytes = max_bytes
        self._bytes = 0
        self.last_seq = 0

    @property
    def oldest_seq(self) -> int | None:
        return self._events[0][0] if self._events else None

    def add(self, seq: int, text: str) -> None:
        self._events.append((seq, text))
        self._bytes += len(text.encode())
        self.last_seq = seq
        while len(self._events) > self._max_events or (self._bytes > self._max_bytes and len(self._events) > 1):
            _, old = self._events.popleft()
            self._bytes -= len(old.encode())

    def since(self, after: int | None, limit: int) -> tuple[list[str], bool]:
        if after is None:
            return [text for _, text in list(self._events)[-limit:]], False
        oldest = self.oldest_seq
        gap = oldest is not None and after < oldest - 1
        return [text for seq, text in self._events if seq > after][:limit], gap

    def snapshot(self) -> list[tuple[int, str]]:
        return list(self._events)
```

- [ ] **Step 4: Run to verify it passes.** Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/ecu_simulator/observe/history.py tests/unit/observe/test_history.py
git commit -m "feat(observe): bounded history ring with after= and gap reporting"
```

---

### Task 5: `Connection`: the queue, the one-slot messages and the ledger

**Files:**
- Create: `src/ecu_simulator/observe/connection.py`
- Test: `tests/unit/observe/test_connection.py`

**Interfaces:**
- Produces:
  - `Connection(id: int, watermark: int, published_at_open: int, now: Callable[[], float] = time.monotonic, max_messages=CLIENT_MAX_MESSAGES, max_bytes=CLIENT_MAX_BYTES, overflow_disconnect_s=OVERFLOW_DISCONNECT_S)`;
  - `.offer(text: str) -> bool`: `False` on a drop, and it may close the connection with 1013;
  - `.set_state(text)`, `.set_dropped(text)`;
  - `.next_message() -> str | None`: `state`, then `dropped`, then the oldest queued exchange. `M2`'s writer task calls it and then `mark_sent()` after a successful `send_str`;
  - `.mark_sent()`;
  - `.close(code: int, published_now: int)`, which is idempotent;
  - `.closed: bool`, `.close_code: int | None`;
  - `.ledger(published_now: int) -> dict`, with the fields of 0010 §5.1.

- [ ] **Step 1: Write the failing tests**

```python
from ecu_simulator.observe.connection import Connection


def conn(**kw):
    clock = {"t": 0.0}
    c = Connection(1, watermark=10, published_at_open=10, now=lambda: clock["t"], **kw)
    return c, clock


def identities(ledger):
    assert ledger["offered"] == ledger["published_at_close"] - ledger["published_at_open"]
    assert ledger["offered"] == ledger["enqueued"] + ledger["client_dropped"]
    assert ledger["enqueued"] == ledger["sent"] + ledger["queued"] + ledger["discarded_on_close"]


def test_queue_order_and_ledger():
    c, _ = conn()
    assert c.offer("a") and c.offer("b")
    assert c.next_message() == "a"; c.mark_sent()
    ledger = c.ledger(published_now=12)
    assert (ledger["offered"], ledger["enqueued"], ledger["sent"], ledger["queued"]) == (2, 2, 1, 1)
    identities(ledger)


def test_state_and_dropped_are_one_slot_and_go_first():
    c, _ = conn()
    c.offer("x")
    c.set_state("s1"); c.set_state("s2"); c.set_dropped("d1")
    assert [c.next_message(), c.next_message(), c.next_message(), c.next_message()] == ["s2", "d1", "x", None]


def test_count_and_byte_limits_drop_new_messages():
    c, _ = conn(max_messages=2, max_bytes=100)
    assert c.offer("a") and c.offer("b") and c.offer("c") is False
    c2, _ = conn(max_messages=100, max_bytes=3)
    assert c2.offer("ab") and c2.offer("cd") is False
    identities(c.ledger(published_now=13)); identities(c2.ledger(published_now=12))


def test_five_seconds_of_continuous_overflow_closes_with_1013():
    c, clock = conn(max_messages=1)
    c.offer("a")
    c.offer("b")                          # overflow starts at t=0
    clock["t"] = 4.9; c.offer("c")
    assert not c.closed
    clock["t"] = 5.0; c.offer("d")
    assert c.closed and c.close_code == 1013
    ledger = c.ledger(published_now=14)
    assert ledger["discarded_on_close"] == 1 and ledger["queued"] == 0
    identities(ledger)


def test_overflow_timer_resets_when_the_queue_drains():
    c, clock = conn(max_messages=1)
    c.offer("a"); c.offer("b")            # overflow at t=0
    clock["t"] = 3.0; c.next_message(); c.mark_sent()
    c.offer("c")                          # accepted: the overflow run has ended
    clock["t"] = 6.0; c.offer("d")        # overflow starts again at 6.0
    assert not c.closed


def test_close_is_idempotent_and_final():  # Review Focus 5
    c, _ = conn(max_messages=1)
    c.offer("a"); c.offer("b")
    c.close(1000, published_now=12)
    first = c.ledger(published_now=99)
    c.close(1013, published_now=50)
    assert c.ledger(published_now=200) == first and c.close_code == 1000
    assert c.offer("late") is False and first["published_at_close"] == 12
    identities(first)
```

- [ ] **Step 2: Run to verify it fails.** Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""One WebSocket client's queue, its one-slot messages and its ledger (0010 §4.3, §5.1).

The ledger counts live ``exchange`` messages only. ``state`` and ``dropped`` are replaced,
never queued, so they can neither be dropped nor starve a client of the latest state.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Any

from ecu_simulator.observe.limits import CLIENT_MAX_BYTES, CLIENT_MAX_MESSAGES, CLOSE_TOO_SLOW, OVERFLOW_DISCONNECT_S


class Connection:
    def __init__(
        self,
        id: int,
        watermark: int,
        published_at_open: int,
        now: Callable[[], float] = time.monotonic,
        max_messages: int = CLIENT_MAX_MESSAGES,
        max_bytes: int = CLIENT_MAX_BYTES,
        overflow_disconnect_s: float = OVERFLOW_DISCONNECT_S,
    ) -> None:
        self.id = id
        self.watermark = watermark
        self._now = now
        self._max_messages = max_messages
        self._max_bytes = max_bytes
        self._overflow_disconnect_s = overflow_disconnect_s
        self._queue: deque[str] = deque()
        self._bytes = 0
        self._state: str | None = None
        self._dropped_notice: str | None = None
        self._overflow_since: float | None = None
        self._in_flight: str | None = None
        self.connected_at = now()
        self.closed_at: float | None = None
        self.close_code: int | None = None
        self.history_sent = 0
        self.published_at_open = published_at_open
        self._published_at_close: int | None = None
        self.offered = self.enqueued = self.client_dropped = self.sent = self.discarded_on_close = 0

    @property
    def closed(self) -> bool:
        return self.close_code is not None

    def offer(self, text: str) -> bool:
        if self.closed:
            return False
        self.offered += 1
        size = len(text.encode())
        if len(self._queue) >= self._max_messages or self._bytes + size > self._max_bytes:
            self.client_dropped += 1
            now = self._now()
            if self._overflow_since is None:
                self._overflow_since = now
            elif now - self._overflow_since >= self._overflow_disconnect_s:
                self.close(CLOSE_TOO_SLOW, published_now=self.published_at_open + self.offered)
            return False
        self._overflow_since = None
        self._queue.append(text)
        self._bytes += size
        self.enqueued += 1
        return True

    def set_state(self, text: str) -> None:
        if not self.closed:
            self._state = text

    def set_dropped(self, text: str) -> None:
        if not self.closed:
            self._dropped_notice = text

    def next_message(self) -> str | None:
        if self._state is not None:
            text, self._state = self._state, None
            return text
        if self._dropped_notice is not None:
            text, self._dropped_notice = self._dropped_notice, None
            return text
        if not self._queue:
            return None
        text = self._queue.popleft()
        self._bytes -= len(text.encode())
        self._in_flight = text
        return text

    def mark_sent(self) -> None:
        if self._in_flight is not None:
            self._in_flight = None
            self.sent += 1

    def close(self, code: int, published_now: int) -> None:
        if self.closed:
            return
        self.close_code = code
        self.closed_at = self._now()
        self._published_at_close = published_now
        self.discarded_on_close = len(self._queue) + (1 if self._in_flight is not None else 0)
        self._queue.clear()
        self._bytes = 0
        self._in_flight = None
        self._state = self._dropped_notice = None

    def ledger(self, published_now: int) -> dict[str, Any]:
        at_close = self._published_at_close if self.closed else published_now
        return {
            "id": self.id, "connected_at": self.connected_at, "closed_at": self.closed_at,
            "close_code": self.close_code, "watermark": self.watermark, "history_sent": self.history_sent,
            "published_at_open": self.published_at_open, "published_at_close": at_close,
            "offered": self.offered, "client_dropped": self.client_dropped, "enqueued": self.enqueued,
            "sent": self.sent, "queued": len(self._queue) + (1 if self._in_flight is not None else 0),
            "discarded_on_close": self.discarded_on_close,
        }
```

> Design note, pinned by the tests. A forced close from inside `offer`
> passes `published_now = published_at_open + offered`. That holds because every
> publication is offered to every open connection exactly once (Task 6), so the
> `offered = published_at_close − published_at_open` identity stays exact.

- [ ] **Step 4: Run to verify it passes.** Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/ecu_simulator/observe/connection.py tests/unit/observe/test_connection.py
git commit -m "feat(observe): per-connection queue, one-slot state and dropped notices, and the ledger"
```

---

### Task 6: `Publisher`: bounded drain, fan-out, watermark and stats

**Files:**
- Create: `src/ecu_simulator/observe/publisher.py`
- Test: `tests/unit/observe/test_publisher.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 4, 5.
- Produces:
  - `Publisher(handoff: HandOff, router: AddressRouter, endpoints: Mapping[str, EndpointConfig], *, encode=encode_exchange, monotonic=time.monotonic, max_turn_records=TURN_MAX_RECORDS, max_turn_s=TURN_MAX_S, max_clients=MAX_CLIENTS, history: HistoryRing | None = None)`;
  - `.wake()`: the callable given to `ObservedDispatcher`;
  - `async .run()`: drains until cancelled;
  - `.drain_turn() -> int`: one bounded turn; returns the number of records processed;
  - `.connect(after: int | None = None) -> tuple[Connection, dict, list[str]]`, returning `(connection, hello, history_texts)`. It raises `TooManyClients` when the limit is reached;
  - `.disconnect(conn, code)`;
  - `.published: int`, `.longest_turn_s: float`, `.refused_clients: int`, `.forced_disconnects: int`;
  - `.stats(issued: int) -> dict`: the `api` object of `GET /status`.

- [ ] **Step 1: Write the failing tests**

```python
import asyncio
import json

import pytest

from ecu_simulator.observe.handoff import ExchangeRecord, HandOff
from ecu_simulator.observe.publisher import Publisher, TooManyClients
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse


class Router:
    def resolve(self, request):
        class Route: ecu = "engine"
        return (Route(),)


def fill(handoff, n, start=1):
    for seq in range(start, start + n):
        handoff.append(ExchangeRecord(seq, 0, 0, DiagnosticRequest(b"\x01\x0c", 0x7DF, functional=True), DiagnosticResponse(b"\x41\x0c\x00\x00"), None))


def publisher(handoff, **kw):
    return Publisher(handoff, Router(), {}, encode=lambda rec, *_: json.dumps({"type": "exchange", "seq": rec.seq}), **kw)


def test_a_turn_stops_at_64_records():
    handoff = HandOff(); fill(handoff, 200)
    p = publisher(handoff)
    assert p.drain_turn() == 64 and len(handoff) == 136 and p.published == 64


def test_a_turn_stops_at_the_time_budget():
    handoff = HandOff(); fill(handoff, 50)
    clock = {"t": 0.0}
    def slow(rec, *_):
        clock["t"] += 0.0003                      # 0.3 ms per record
        return json.dumps({"seq": rec.seq})
    p = Publisher(handoff, Router(), {}, encode=slow, monotonic=lambda: clock["t"])
    assert p.drain_turn() == 4                   # 4th record takes the turn to 1.2 ms: stop after it
    assert p.longest_turn_s == pytest.approx(0.0012)


@pytest.mark.asyncio
async def test_O4_a_full_handoff_yields_to_the_loop():
    handoff = HandOff(); fill(handoff, 4096)
    p = publisher(handoff)
    log: list[str] = []
    real_turn = p.drain_turn
    def logged_turn():
        n = real_turn(); log.append("turn"); return n
    p.drain_turn = logged_turn
    task = asyncio.create_task(p.run())
    p.wake()
    await asyncio.sleep(0)
    asyncio.get_running_loop().call_soon(log.append, "marker")
    while len(handoff):
        await asyncio.sleep(0)
    task.cancel()
    assert "marker" in log and log.index("marker") < len(log) - 1   # the marker ran before the drain finished
    assert log.count("turn") >= 64


def test_watermark_handshake_has_no_gap_and_no_duplicate():
    handoff = HandOff(); fill(handoff, 5)
    p = publisher(handoff)
    p.drain_turn()                                # seq 1..5 published
    conn, hello, history = p.connect()
    assert hello == {"type": "hello", "api": 1, "watermark": 5, "oldest_seq": 1}
    assert [json.loads(t)["seq"] for t in history] == [1, 2, 3, 4, 5]
    fill(handoff, 3, start=6); p.drain_turn()
    live = [json.loads(conn.next_message())["seq"] for _ in range(3)]
    assert live == [6, 7, 8]


def test_client_limit_refuses_and_counts():
    p = publisher(HandOff(), max_clients=2)
    p.connect(); p.connect()
    with pytest.raises(TooManyClients):
        p.connect()
    assert p.refused_clients == 1


def test_ledgers_reconcile_after_quiesce():
    handoff = HandOff(); p = publisher(handoff)
    a, _, _ = p.connect(); fill(handoff, 10); p.drain_turn()
    b, _, _ = p.connect(); fill(handoff, 10, start=11); p.drain_turn()
    while a.next_message(): a.mark_sent()
    p.disconnect(b, 1000)
    stats = p.stats(issued=20)
    assert stats["issued_seq"] == stats["published"] + stats["handoff_dropped"] == 20
    for ledger in stats["connections"] + stats["closed_connections"]:
        assert ledger["offered"] == ledger["published_at_close"] - ledger["published_at_open"]
        assert ledger["offered"] == ledger["enqueued"] + ledger["client_dropped"]
        assert ledger["enqueued"] == ledger["sent"] + ledger["queued"] + ledger["discarded_on_close"]
```

- [ ] **Step 2: Run to verify it fails.** Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Drains HandOff in bounded turns, publishes to history and to every connection (0010 §4.2-4.5)."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from ecu_simulator.ecu.router import AddressRouter
from ecu_simulator.observe.connection import Connection
from ecu_simulator.observe.events import encode_exchange
from ecu_simulator.observe.handoff import HandOff
from ecu_simulator.observe.history import HistoryRing
from ecu_simulator.observe.limits import CLOSED_LEDGERS_KEPT, CLOSE_TOO_SLOW, MAX_CLIENTS, TURN_MAX_RECORDS, TURN_MAX_S
from ecu_simulator.transport.socketcan import EndpointConfig


class TooManyClients(Exception):
    """The client limit is reached. M2 turns this into HTTP 503 before the upgrade."""


class Publisher:
    def __init__(
        self,
        handoff: HandOff,
        router: AddressRouter,
        endpoints: Mapping[str, EndpointConfig],
        *,
        encode: Callable[..., str] = encode_exchange,
        monotonic: Callable[[], float] = time.monotonic,
        max_turn_records: int = TURN_MAX_RECORDS,
        max_turn_s: float = TURN_MAX_S,
        max_clients: int = MAX_CLIENTS,
        history: HistoryRing | None = None,
    ) -> None:
        self._handoff = handoff
        self._router = router
        self._endpoints = endpoints
        self._encode = encode
        self._monotonic = monotonic
        self._max_turn_records = max_turn_records
        self._max_turn_s = max_turn_s
        self._max_clients = max_clients
        self.history = history if history is not None else HistoryRing()
        self._wall_origin = (time.time(), time.monotonic_ns())
        self._event = asyncio.Event()
        self._next_id = 0
        self.connections: list[Connection] = []
        self.closed: deque[dict[str, Any]] = deque(maxlen=CLOSED_LEDGERS_KEPT)
        self.published = 0
        self.longest_turn_s = 0.0
        self.refused_clients = 0
        self.forced_disconnects = 0

    def wake(self) -> None:
        self._event.set()

    def drain_turn(self) -> int:
        start = self._monotonic()
        done = 0
        while len(self._handoff) and done < self._max_turn_records:
            record = self._handoff.popleft()
            text = self._encode(record, self._router, self._endpoints, self._wall_origin)
            self.published += 1
            self.history.add(record.seq, text)
            for conn in list(self.connections):
                conn.offer(text)
                if conn.closed:
                    self._retire(conn)
            done += 1
            if self._monotonic() - start >= self._max_turn_s:
                break
        self.longest_turn_s = max(self.longest_turn_s, self._monotonic() - start)
        return done

    async def run(self) -> None:
        while True:
            await self._event.wait()
            self._event.clear()
            while len(self._handoff):
                self.drain_turn()
                await asyncio.sleep(0)   # 0010 §4.2: yield after every bounded turn

    def connect(self, after: int | None = None) -> tuple[Connection, dict[str, Any], list[str]]:
        # One synchronous step, no await: nothing can be published between these lines (0010 §4.5).
        if len(self.connections) >= self._max_clients:
            self.refused_clients += 1
            raise TooManyClients
        watermark = self.history.last_seq
        texts, gap = self.history.since(after, limit=self.history.max_events)
        self._next_id += 1
        conn = Connection(self._next_id, watermark, self.published, now=self._monotonic)
        conn.history_sent = len(texts)
        self.connections.append(conn)
        hello: dict[str, Any] = {"type": "hello", "api": 1, "watermark": watermark, "oldest_seq": self.history.oldest_seq}
        if after is not None:
            hello["gap"] = gap
        return conn, hello, texts

    def disconnect(self, conn: Connection, code: int) -> None:
        conn.close(code, published_now=self.published)
        self._retire(conn)

    def _retire(self, conn: Connection) -> None:
        if conn in self.connections:
            self.connections.remove(conn)
            if conn.close_code == CLOSE_TOO_SLOW:
                self.forced_disconnects += 1
            self.closed.append(conn.ledger(self.published))

    def stats(self, issued: int) -> dict[str, Any]:
        return {
            "clients": len(self.connections), "issued_seq": issued, "published": self.published,
            "last_published_seq": self.history.last_seq, "oldest_seq": self.history.oldest_seq,
            "handoff_dropped": self._handoff.dropped, "refused_clients": self.refused_clients,
            "forced_disconnects": self.forced_disconnects, "longest_turn_s": self.longest_turn_s,
            "connections": [c.ledger(self.published) for c in self.connections],
            "closed_connections": list(self.closed),
        }

    def push_state(self, text: str) -> None:
        for conn in self.connections:
            conn.set_state(text)

    def push_dropped(self) -> None:
        for conn in self.connections:
            conn.set_dropped(json.dumps({"type": "dropped", "handoff_dropped": self._handoff.dropped,
                                         "client_dropped": conn.client_dropped,
                                         "forced_disconnects": self.forced_disconnects}, separators=(",", ":")))
```

`hello` carries `gap` only when `after` is given, which matches 0010 §4.5 and §5.

- [ ] **Step 4: Run to verify it passes.** Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/ecu_simulator/observe/publisher.py src/ecu_simulator/observe/history.py tests/unit/observe/test_publisher.py
git commit -m "feat(observe): Publisher with bounded turns, watermark handshake and reconciling ledgers"
```

---

### Task 7: snapshots and the 4 Hz state push

**Files:**
- Create: `src/ecu_simulator/observe/snapshots.py`
- Modify: `src/ecu_simulator/observe/publisher.py`, to add `async run_state(snapshot: Callable[[], str], interval_s=STATE_MIN_INTERVAL_S)`
- Test: `tests/unit/observe/test_snapshots.py`

**Interfaces:**
- Consumes: `Runtime` (`vehicle`, `ecus`, `runner`, `sync`, `config`); `build_endpoints(config)`.
- Produces:
  - `vehicle(runtime) -> dict`, `dtcs(runtime) -> dict`, `ecus(runtime) -> dict`;
  - `status(runtime, publisher, issued: int, started_at: float, version: str) -> dict`;
  - `state_message(runtime) -> str`;
  - `check_state_size(runtime) -> None`, which raises `ValueError` over `STATE_MAX_BYTES`.

- [ ] **Step 1: Write the failing tests**

```python
import copy
import json
from pathlib import Path

import pytest

from ecu_simulator import app
from ecu_simulator.config import load_profile
from ecu_simulator.observe import snapshots

PROFILES = Path(app.__file__).parent / "profiles"


def runtime(name="ice_default.yaml"):
    return app.build_runtime(app.RuntimeConfig.build(load_profile(PROFILES / name), "vcan0"))


@pytest.mark.parametrize("name", ["ice_default.yaml", "ice_scenario.yaml"])
def test_snapshots_serialise_and_fit(name):
    rt = runtime(name)
    for snap in (snapshots.vehicle(rt), snapshots.dtcs(rt), snapshots.ecus(rt)):
        json.dumps(snap)
    snapshots.check_state_size(rt)


def test_snapshots_never_mutate_and_never_call_sync():
    rt = runtime("ice_scenario.yaml")
    calls = []
    before = (copy.deepcopy(rt.vehicle.signals), [(s.code, s.pending, s.confirmed, s.indicator_requested) for e in rt.ecus for s in e.dtc_store], rt.runner.last_applied, rt.runner.pending_events)
    original_apply = rt.runner.apply
    rt.runner.apply = lambda t: calls.append(t)
    for _ in range(3):
        snapshots.vehicle(rt); snapshots.dtcs(rt); snapshots.ecus(rt); snapshots.state_message(rt)
    rt.runner.apply = original_apply
    after = (copy.deepcopy(rt.vehicle.signals), [(s.code, s.pending, s.confirmed, s.indicator_requested) for e in rt.ecus for s in e.dtc_store], rt.runner.last_applied, rt.runner.pending_events)
    assert before == after and calls == []


def test_vehicle_reports_as_of_the_last_application():
    rt = runtime("ice_scenario.yaml")
    assert snapshots.vehicle(rt)["as_of"] is None      # nothing applied yet
    rt.sync()
    assert snapshots.vehicle(rt)["as_of"] == rt.runner.last_applied


def test_dtcs_and_ecus_shape():
    rt = runtime()
    d = snapshots.dtcs(rt)
    assert d["engine"]["mil"] is False and {x["code"] for x in d["engine"]["codes"]} == {"B1477", "P0001"}
    e = snapshots.ecus(rt)["engine"]
    assert {p["name"] for p in e["protocols"]} == {"obd", "uds"}
    assert any(ep["name"] == "engine.obd_functional" and ep["reply_via"] == "engine.obd_physical" for ep in e["endpoints"])


def test_oversized_state_is_refused():
    rt = runtime()
    rt.vehicle.common.vin = "V" * (300 * 1024)   # test-only object, discarded after the test
    with pytest.raises(ValueError, match="256 KiB"):
        snapshots.check_state_size(rt)
```

The spy is on `runner.apply`, which `sync()` would call, so an empty `calls` proves
`sync()` was never reached. `CommonState` is a mutable `@dataclass(slots=True)`
(`vehicle/state.py:18-24`), so assigning `vin` in the last test is valid.

- [ ] **Step 2: Run to verify it fails.** Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
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


def status(runtime: app.Runtime, publisher: Any, issued: int, started_at: float, version: str) -> dict[str, Any]:
    runner = runtime.runner
    return {
        "version": version, "interface": runtime.config.interface,
        "started_at": started_at, "uptime_s": time.time() - started_at,
        "scenario": {"enabled": runner is not None,
                     "t_last_applied": runner.last_applied if runner else None,
                     "pending_events": runner.pending_events if runner else 0},
        "api": publisher.stats(issued),
    }
```

Add to `Publisher`, at the end of `publisher.py`'s class body:

```python
    async def run_state(self, snapshot: Callable[[], str], interval_s: float = STATE_MIN_INTERVAL_S) -> None:
        """Push ``state`` at most every ``interval_s``, and only when it changed (0010 §4.3)."""
        last: str | None = None
        while True:
            text = snapshot()
            if text != last:
                self.push_state(text)
                last = text
            self.push_dropped()
            await asyncio.sleep(interval_s)
```

(Import `STATE_MIN_INTERVAL_S` from `limits`.) Add a test to `test_publisher.py` using
`loop.time`-free logic: call `run_state` with a snapshot callable returning `"a"`, `"a"`
and then `"b"`, and `interval_s=0`. Cancel after 3 iterations, and assert that
`connection.next_message()` yields `"b"` and then the `dropped` notice, and never `"a"`
twice.

- [ ] **Step 4: Run to verify it passes.** Expected: all `tests/unit/observe` tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/ecu_simulator/observe/snapshots.py src/ecu_simulator/observe/publisher.py tests/unit/observe
git commit -m "feat(observe): read-only snapshots that never call sync, and change-only state push"
```

---

### Task 8: ordering tests O1 and O2, and the two-socket observation

**Files:**
- Test: `tests/unit/observe/test_ordering.py` (tests only: this task pins behaviour of code that already exists)

**Interfaces:**
- Consumes: `IsoTpTransport(..., socket_factory=factory(...), check_environment=False)`, `tests.unit.fakes.FakeIsotpSocket` (`.feed`, `.sent`, `.busy`), `ObservedDispatcher`, `Publisher`.

- [ ] **Step 1: Write the tests**

```python
import asyncio
import json

import pytest

from ecu_simulator.observe.handoff import HandOff
from ecu_simulator.observe.publisher import Publisher
from ecu_simulator.observe.wrapper import ObservedDispatcher
from ecu_simulator.transport import DiagnosticResponse
from ecu_simulator.transport.socketcan import EndpointConfig, IsoTpAddress, IsoTpTransport
from tests.unit.fakes import FakeIsotpSocket, factory

PHYSICAL = EndpointConfig("obd_physical", IsoTpAddress(0x7E0, 0x7E8))
SECOND = EndpointConfig("obd_physical_b", IsoTpAddress(0x7E0, 0x7EF))   # DEV-25's shape


class Router:
    def resolve(self, request):
        class Route: ecu = "engine"
        return (Route(),)


@pytest.fixture(autouse=True)
def _clear():
    FakeIsotpSocket.instances.clear(); yield
    for fake in FakeIsotpSocket.instances:
        if not fake.closed:
            fake.close()


async def settle(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


def instrumented(log, busy=False):
    handoff = HandOff()
    def encode(rec, *_):
        log.append(f"publish {rec.seq}")
        return json.dumps({"seq": rec.seq})
    publisher = Publisher(handoff, Router(), {}, encode=encode)
    observed = ObservedDispatcher(lambda request: DiagnosticResponse(b"\x41\x0c\x0c\x80"), handoff, publisher.wake)
    return publisher, observed


def spy_sends(log):
    for fake in FakeIsotpSocket.instances:
        original = fake.send
        def send(data, _orig=original, _fake=fake):
            n = _orig(data); log.append(f"send {data.hex()}"); return n
        fake.send = send


@pytest.mark.asyncio
async def test_O1_an_accepted_reply_is_handed_to_the_kernel_before_the_publisher_runs():
    log: list[str] = []
    publisher, observed = instrumented(log)
    transport = IsoTpTransport("vcan0", [PHYSICAL], socket_factory=factory(), check_environment=False)
    task = asyncio.create_task(publisher.run())
    await transport.start(observed); spy_sends(log)
    FakeIsotpSocket.instances[0].feed.send(b"\x01\x0c")
    await settle()
    await transport.stop(); task.cancel()
    assert log == ["send 410c0c80", "publish 1"]


@pytest.mark.asyncio
async def test_O2_a_queued_reply_may_be_published_before_it_is_sent():
    log: list[str] = []
    publisher, observed = instrumented(log)
    transport = IsoTpTransport("vcan0", [PHYSICAL], socket_factory=factory(busy=True), check_environment=False)
    task = asyncio.create_task(publisher.run())
    await transport.start(observed); spy_sends(log)
    fake = FakeIsotpSocket.instances[0]
    fake.feed.send(b"\x01\x0c")
    await settle()
    assert log == ["publish 1"]                  # queued in endpoint.pending, not sent, yet published
    assert json.loads(publisher.history.snapshot()[0][1])["seq"] == 1
    fake.busy = False
    await settle()
    await transport.stop(); task.cancel()
    assert log == ["publish 1", "send 410c0c80"]


@pytest.mark.asyncio
async def test_one_request_on_two_sockets_is_two_exchanges():
    # 0010 §4.4 last row, DEV-25: the kernel delivers one request to both sockets (measured, 0010 §12 E6).
    log: list[str] = []
    publisher, observed = instrumented(log)
    transport = IsoTpTransport("vcan0", [PHYSICAL, SECOND], socket_factory=factory(), check_environment=False)
    task = asyncio.create_task(publisher.run())
    await transport.start(observed)
    for fake in FakeIsotpSocket.instances:
        fake.feed.send(b"\x01\x0c")
    await settle()
    await transport.stop(); task.cancel()
    assert publisher.published == 2 and observed.issued == 2
```

Facts these tests rely on, checked while writing this plan:
- `FakeIsotpSocket.instances` is appended to in creation order (`tests/unit/fakes.py:28`),
  and the transport opens sockets in endpoint order, so `instances[0]` is `PHYSICAL`.
- `spy_sends` replaces the `send` attribute on the fake **instance** after `start()`.
  `IsoTpSocket.send` calls that instance's `send`, so the spy sees every attempt. A busy
  fake raises `BlockingIOError` before the spy logs, so a failed attempt is not logged.
- The transport constructor rejects only duplicate `(rx, tx)` pairs
  (`test_isotp_transport.py`: "duplicate ISO-TP address pairs"), so `SECOND` is accepted.
  If that ever changes, the two-socket test must be updated to **record the rejection**,
  not deleted: it would mean DEV-25 is narrower than recorded.

- [ ] **Step 2: Run them.** These pin existing behaviour, so they are expected to **pass**
  straight away. To prove they can fail, temporarily call `observed` from inside the fake
  `send` wrapper in O1, and watch the order assertion fail. Then revert that change.

Run: `.venv/bin/python -m pytest tests/unit/observe/test_ordering.py -v -p no:cacheprovider`
Expected: 3 passed (after the deliberate-failure check has been reverted)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/observe/test_ordering.py
git commit -m "test(observe): pin publisher-after-send ordering, the queued case, and two-socket delivery"
```

---

### Task 9: M1 early performance check, verification gates, and the M1 report

**Files:**
- Create: `scripts/gui_m1_early_check.py`, `docs/validation/gui-m1-early-check.md`

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""GUI M1 early check (decisions/0010 §9.2). In-process, no network.

1. Dispatch overhead: 20,000 calls through a bare Dispatcher against the same calls through
   ObservedDispatcher with the Publisher draining between calls.
2. Loop hold: drain a full 4096-record HandOff and report the longest publisher turn.
Stop rule: median overhead > 10 µs, p99 overhead > 50 µs, or any turn > 2 ms.
"""

from __future__ import annotations

import statistics
import sys
import time

from ecu_simulator import app
from ecu_simulator.cli import default_profile_path
from ecu_simulator.config import load_profile
from ecu_simulator.observe.handoff import HandOff
from ecu_simulator.observe.publisher import Publisher
from ecu_simulator.observe.wrapper import ObservedDispatcher
from ecu_simulator.transport import DiagnosticRequest

N = 20_000
MIX = [DiagnosticRequest(b"\x01\x0d", 0x7DF, functional=True), DiagnosticRequest(b"\x01\x10", 0x7DF, functional=True)]


def timed(handler, publisher=None) -> list[float]:
    samples = []
    for i in range(N):
        request = MIX[i % 2] if i % 100 else DiagnosticRequest(b"\x09\x02", 0x7DF, functional=True)
        t0 = time.perf_counter_ns(); handler(request); samples.append((time.perf_counter_ns() - t0) / 1000)
        if publisher is not None:
            while publisher.drain_turn():
                pass
    return sorted(samples)


def main() -> int:
    config = app.RuntimeConfig.build(load_profile(default_profile_path()), "vcan0")
    bare = timed(app.build_runtime(config).dispatcher)
    runtime = app.build_runtime(config)
    endpoints = {e.name: e for e in app.build_endpoints(config)}
    handoff = HandOff(); publisher = Publisher(handoff, runtime.router, endpoints)
    wrapped = timed(ObservedDispatcher(runtime.dispatcher, handoff, lambda: None), publisher)
    med = statistics.median(wrapped) - statistics.median(bare)
    p99 = wrapped[int(N * 0.99)] - bare[int(N * 0.99)]

    full = HandOff(); p2 = Publisher(full, runtime.router, endpoints)
    fill = ObservedDispatcher(runtime.dispatcher, full, lambda: None)
    while len(full) < 4096:
        fill(MIX[0])
    turns = 0
    while len(full):
        p2.drain_turn(); turns += 1
    print(f"bare median {statistics.median(bare):.2f} us  p99 {bare[int(N*0.99)]:.2f} us")
    print(f"wrapped median {statistics.median(wrapped):.2f} us  p99 {wrapped[int(N*0.99)]:.2f} us")
    print(f"overhead median {med:.2f} us  p99 {p99:.2f} us")
    print(f"full HandOff: {turns} turns, longest turn {p2.longest_turn_s*1e3:.3f} ms")
    stop = med > 10 or p99 > 50 or p2.longest_turn_s > 0.002
    print("STOP: report before M2" if stop else "within the M1 early-check limits")
    return 1 if stop else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it three times** and record all three outputs verbatim.

Run: `for i in 1 2 3; do .venv/bin/python scripts/gui_m1_early_check.py; done`
Expected: each run prints its figures and either "within the M1 early-check limits" or
"STOP". **On STOP, do not tune the limits. Report to the owner.**

- [ ] **Step 3: Run the verification gates**

```bash
set -o pipefail
.venv/bin/python -m pytest -p no:cacheprovider 2>&1 | tail -3        # previous 1026 passed + the new observe tests; 2 xfailed; 0 XPASS
scripts/run_integration_tests.sh 2>&1 | tail -1                      # 68 passed: unchanged
.venv/bin/ruff check .
.venv/bin/mypy
git diff --stat a57b98f..HEAD -- src/ecu_simulator/app.py src/ecu_simulator/cli.py src/ecu_simulator/transport src/ecu_simulator/protocols src/ecu_simulator/ecu src/ecu_simulator/vehicle src/ecu_simulator/dtc src/ecu_simulator/scenario src/ecu_simulator/profiles pyproject.toml .github
```

Expected:
- the last command prints **nothing**: no existing file changed;
- ruff and mypy are clean;
- the integration count is unchanged.

Then reproduce CI's shape before any push: a fresh clone, a venv with only `.[dev]`, run
inside `unshare -r -n` with `ip link set lo up`. All `tests/unit/observe` tests must pass
there, because they need no CAN and no `[gui]` extra.

- [ ] **Step 4: Write `docs/validation/gui-m1-early-check.md`**

It records:
- the host, kernel, Python and commit;
- the three script outputs, verbatim;
- the gate results, with counts;
- which 0010 §9.3 rows now run in ordinary CI: all of the M1 tests;
- that the vcan and performance rows are not yet applicable;
- the §9.1 wiring proofs deferred to M2, with their reason.

State plainly: *"An early check, not acceptance. P1–P9 are judged at M4."*

- [ ] **Step 5: Commit, and stop for owner review**

```bash
git add scripts/gui_m1_early_check.py docs/validation/gui-m1-early-check.md
git commit -m "docs(validation): GUI M1 early check and verification record"
```

Do **not** push until the owner asks, and do not start M2.

---

## Self-review

**Spec coverage.**

| 0010 requirement | M1 task |
|---|---|
| §4.2 hot path | Task 2 |
| §4.2 turn bound and O4 | Task 6 |
| O1, O2 | Task 8 |
| O3 | Task 2 |
| §4.3 limits | Tasks 1, 3, 4, 5, 6, 7 |
| §4.4 observability rows, including two sockets | Tasks 3 and 8 |
| §4.5 watermark and `after=` | Tasks 4 and 6 |
| §5 events and outcomes | Task 3 |
| §5 `/status` data | Tasks 6 and 7 |
| §5.1 ledger | Tasks 5 and 6 |
| §9.1 differential | Task 2 |
| §9.1 no third-party imports | Task 1 |
| §9.2 M1 early check | Task 9 |
| §9.3 M1 rows | Tasks 1–8 |

**Out of M1 by design:** HTTP and WebSocket serving, `--api`, `[gui]`, security checks,
the CI job and the `run()` wiring proofs (M2); the frontend (M3); the full benchmark (M4).

**Placeholder scan.** No TBD or TODO, and no deferred "implementer notes". Every
external fact the tests rely on was checked while writing, and is cited with its file and
line:
- the router builder;
- the fake-socket order;
- the transport's duplicate-pair rule;
- `vin` being assignable.

**Type consistency.** These names are used identically across tasks:
- `ExchangeRecord` fields;
- `HandOff.append` / `popleft` / `dropped` / `bytes`;
- `ObservedDispatcher(inner, handoff, wake, clock_ns)` / `.issued`;
- `encode_exchange(record, router, endpoints, wall_origin)`;
- `HistoryRing.since(after, limit) -> (texts, gap)`;
- `Connection.offer` / `next_message` / `mark_sent` / `close(code, published_now)` / `ledger(published_now)`;
- `Publisher.drain_turn` / `connect(after)` / `disconnect(conn, code)` / `stats(issued)`.
