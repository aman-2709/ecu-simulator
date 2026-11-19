# GUI M2: API server implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task by task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the M1 observer core over an opt-in, loopback-only HTTP and WebSocket API
(`--api HOST:PORT`, `[gui]` extra) without changing a single reply when the API is off.
The M2 early check runs on vcan.

**Architecture:**
- `ecu_simulator.api.options` is stdlib-only. It parses and validates `--api` and defines
  `ApiStartupError`, so the CLI and `app.run` can use both without aiohttp.
- `ecu_simulator.api.server.ApiServer` is the **only** module that imports aiohttp. It
  owns the `HandOff`, the `Publisher` and the `ObservedDispatcher`, primes the initial
  state, serves the routes and runs one writer task per WebSocket client.
- `run()` imports it lazily, only when `api` is given, and hands the transport the wrapped
  dispatcher. With `api=None` the transport gets the plain `Dispatcher`, as today.

**Tech stack:**
- Python 3.12+;
- aiohttp `>=3.13,<4` in the new `[gui]` extra. The server, runner and close-code APIs
  used below were checked on 3.13.5 and 3.14.3 on 2026-09-27;
- pytest with pytest-asyncio in strict mode;
- can-isotp and `candump` for the vcan work.

**Spec:** [docs/decisions/0010-gui-observer-api.md](../decisions/0010-gui-observer-api.md),
fifth revision (the fourth came with this plan's first draft, `84a2d6c`). Read it first. Section numbers
below refer to it.

**Builds on:** M1 at `a450dad` (`origin/gui`): `ecu_simulator.observe`, and the writer
contract in the `observe/connection.py` docstring.

## Owner decisions this plan carries (2026-09-27)

Items 5 and 6 were added in a revision after the owner reviewed the first draft (`84a2d6c`);
the tasks they touch say so in their tests and code. Item 8 was added at M2 approval, before any
code.

1. **`gui` becomes a CI push branch.** Task 7 adds `gui` to `on.push.branches` in
   `.github/workflows/ci.yml` **on branch `gui` only**. The `modernization` workflow, the
   V1.0 gate and the Phase 8b gate are unchanged. On hosted runners the vcan tests keep
   skipping with the **explicit CAN_ISOTP reason**, and every job's annotation lists its
   skip reasons by name.
2. **Initial state before the first WebSocket connection.** `ApiServer.__init__` computes
   `state_message(runtime)` once, after the 256 KiB check, and calls
   `publisher.push_state()`. This happens before the listening socket is bound, so the
   state is there before any client can connect: the first client receives `hello`, then
   `state`, then history. `run_state` keeps it fresh after that (Task 4).
3. **P5 must reconcile more than 64 closed clients over the M4 soak.** Two layers:
   - **cumulative accounting on the server (Task 3):**
     - `closed_totals` sums every closed connection's ledger, including those evicted
       from the 64 retained `closed_connections`;
     - it has `close_codes` counts, and adds each connection only once its in-flight send
       has resolved;
     - `connections_opened` and `closed_unresolved` sit alongside it.
   - **durable output from the harness (0010 §9.2, used from M4):**
     - the M4 harness appends every `GET /status` poll and every close it observes to
       JSONL files under `docs/validation/`;
     - the union of polled `closed_connections` ids is checked against
       `1..connections_opened`.
4. **Frontend file tests and rendering acceptance belong to M3.** M2 serves exactly one
   static file, a placeholder page at `/`, and its static tests cover only that file.
5. **A cancelled send is `delivery_unknown`, not `sent`** (owner review, 2026-09-27).
   A `send_str` cancelled while under way may or may not have reached the client. The
   ledger records it as a fourth outcome, `delivery_unknown`, alongside `sent`, `queued`
   and `discarded_on_close`:
   - it is carried in every ledger, in `closed_totals`, and in the identities;
   - any `delivery_unknown > 0` makes that benchmark condition's P5 and P6 result
     **inconclusive**, and so does any closed connection still unresolved at quiesce.
     Inconclusive means reported with the counts, never passed (Tasks 2, 3, 5 and 9;
     0010 §5.1 and §9.2).
6. **The WebSocket `Origin` is checked against this request's validated `Host`** (owner
   review, 2026-09-27).
   - The only accepted origin is `http://` plus the request's own `Host`, which has already
     passed the 421 check.
   - A page from `http://localhost:P` cannot open the socket through Host `127.0.0.1:P`,
     and the reverse is refused too (Task 5).
7. **No change to the V1.0 or Phase 8b gates.**
8. **A `send_str` exception is `failed` only when non-delivery is known** (owner, at M2
   approval, 2026-09-27). Anything else is `delivery_unknown`.
   - `send` raises `NotDelivered` (Task 2) only when it knows nothing was written. Every
     other exception is `delivery_unknown`, and so is a cancellation.
   - The server's `send` (Task 5) knows exactly one such case: before calling `send_str`,
     it checks, synchronously, whether the socket is already closed or its transport is
     closing.
   - Why that check is enough, from reading aiohttp 3.13.5 and 3.14.3 `web_ws.py` and
     `_websocket/writer.py`, with compression off as this plan sets it:
     - `send_str` reaches `transport.write()` without suspending;
     - its own pre-write refusals come first: the writer's `_closing` flag, which only
       `ws.close()` sets, **after** it has set `ws.closed`, and `transport.is_closing()`.
       Both are covered by that synchronous check;
     - so any exception after the check passes arises at or after `transport.write()`,
       typically "Connection lost" from the drain wait. By then the frame is in the
       transport buffer, so delivery is unknown. Nothing in this plan touches
   `modernization`, and `gui` is not merged before V1.0 is tagged.

## Global constraints

- **Where to work:**
  - branch `gui`, worktree `.claude/worktrees/gui`, with the worktree's own `.venv`
    (created by `uv venv` plus `-e .[dev,hardware,gui]`). The main `.venv` imports the
    main checkout's `src`;
  - never commit to `modernization`, never merge, and push only when the owner asks;
  - **no `Co-Authored-By` trailer.**
- **Imports:**
  - `ecu_simulator.observe` stays **stdlib-only**; the M1 test enforces this;
  - `ecu_simulator.api.options` is stdlib-only;
  - only `ecu_simulator/api/server.py` may import aiohttp (Task 1 test).
- **aiohttp usage:**
  - pin `aiohttp>=3.13,<4`, and do not use APIs absent from 3.13: for example,
    `WebSocketResponse(decode_text=…)` does not exist there;
  - set `writer_limit` explicitly, because its default differs between 3.13 (64 KiB) and
    3.14 (256 KiB).
- **Limits** (0010 §4.3, verbatim):
  - 4 clients, then 503 before the upgrade;
  - forced close with 1013, `"client too slow"`, after 5 s of continuous overflow;
  - request body at most 1 KiB, else 413;
  - any client data message closes with 1008;
  - state at most 256 KiB, else refuse to start with exit 2.
- **Security** (0010 §6):
  - `--api` accepts only `127.0.0.1`, `::1` or `localhost`, else exit 2;
  - `Host` must be the bound address or `localhost`, with the bound port, else 421;
  - the WebSocket `Origin` must be the server's own origin, else 403 before the upgrade;
  - no CORS headers ever;
  - only `GET` and the upgrade are allowed: every other method, **including `HEAD`**,
    gets 405.
- **Exit statuses:** 2 for every `--api` startup failure (bad address, missing extra, busy
  port, oversized state), and nothing opens a CAN socket first.
- **Tooling:**
  - `mypy` bare; `ruff check .`, never `ruff format` wholesale; line length 120;
  - `set -o pipefail` when piping pytest;
  - tests that spawn Python use `sys.executable`;
  - never `pkill -f`: kill by exact PID.
- **Namespaces:** a fresh `unshare -r -n` namespace has `lo` **down**. Every namespace
  script here runs `ip link set lo up` first. The HTTP API needs loopback.

## Review Focus

These are the five inputs most likely to bite that no spec sentence makes into a test.
Each is pinned in the task named.

1. **A client that stops reading while the writer is blocked in `send_str`.** The forced
   1013 must still close the socket, free the client slot and finalise the ledger, even
   though the writer never returns to `next_message()`. Task 5,
   `test_a_stalled_client_is_closed_1013_and_frees_its_slot`.
2. **A client that vanishes mid-send**, for example a reset or a closed tab. The writer's
   exception must end that connection only, with the ledger resolved per owner decision 8
   (known non-delivery → `mark_failed`, otherwise delivery_unknown), and must never reach
   the event loop's unhandled-exception log. Task 5,
   `test_a_client_that_disconnects_mid_stream_is_retired_cleanly`.
3. **Host header variants:**
   - allowed: `localhost:PORT`, and `LOCALHOST:PORT` (hosts are case-insensitive);
   - 421: a missing `Host` (HTTP/1.0), `127.0.0.1` with no port, the right host with the
     wrong port, `[::1]:PORT` while bound to IPv4, and any other name.

   Task 4, `test_host_header_rules`.
4. **A busy port, or an unwritable state, when `--api` is given.** The simulator must exit
   with status 2 **before opening any CAN socket**. Task 6,
   `test_a_busy_api_port_fails_before_any_can_socket_opens`.
5. **Shutdown while clients are connected** (SIGINT or `stop`). Every client gets 1001, the
   process finishes within a bounded time, and no "Task was destroyed but it is pending"
   warning appears. Task 6, `test_shutdown_closes_clients_with_1001`, and Task 8's
   subprocess test.

## File structure

| File | Responsibility | Task |
|---|---|---|
| `pyproject.toml` | the `gui` extra, and static files as package data | 1 |
| `src/ecu_simulator/api/__init__.py` | package marker, stdlib only | 1 |
| `src/ecu_simulator/api/options.py` | `ApiOptions`, `parse_api`, `allowed_hosts`, `ApiStartupError` (stdlib) | 1 |
| `src/ecu_simulator/observe/connection.py` | wake-ups, `take_state`, writer contract text | 2 |
| `src/ecu_simulator/observe/writer.py` | `run_writer`: the contract as code (stdlib) | 2 |
| `src/ecu_simulator/observe/publisher.py` | cumulative totals, `connection_options` | 3 |
| `src/ecu_simulator/api/server.py` | `ApiServer`: HTTP routes, guard middleware, WebSocket, lifecycle (aiohttp) | 4, 5 |
| `src/ecu_simulator/api/static/index.html` | the one placeholder page M2 serves | 4 |
| `src/ecu_simulator/app.py` | `run(..., api=None)` and `main(config, api=None)` | 6 |
| `src/ecu_simulator/cli.py` | `--api HOST:PORT` | 6 |
| `.github/workflows/ci.yml` | `gui` push branch, `api` job, skip reasons in annotations | 7 |
| `scripts/run_integration_tests.sh` | `ip link set lo up` in the namespace | 8 |
| `tests/integration/conftest.py` | `Simulator(extra_args=…)` | 8 |
| `scripts/run_gui_m2_early_check.sh`, `scripts/gui_m2_early_check.py` | the M2 early check on vcan | 9 |
| `docs/validation/gui-m2-early-check.md` | its record | 9 |

These tests need nothing extra, and run in every `.[dev]` job:
- `tests/unit/test_gui_extra_is_optional.py`
- `tests/unit/test_api_options.py`
- `tests/unit/test_api_wiring.py`
- `tests/unit/observe/test_writer.py`
- the new tests in `tests/unit/observe/test_publisher.py`

These need aiohttp. Each module starts with `pytest.importorskip("aiohttp", reason=…)`,
so a `.[dev]` job skips it **with that reason**:
- `tests/unit/api/__init__.py`
- `tests/unit/api/conftest.py`
- `tests/unit/api/test_server_http.py`
- `tests/unit/api/test_server_ws.py`
- `tests/unit/api/test_run_with_api.py`

`tests/integration/test_api_vcan.py` uses the `vcan` fixture **before** aiohttp is
checked, so on a hosted runner it skips with the CAN_ISOTP reason.

---

### Task 1: the `[gui]` extra, `api.options`, and "aiohttp only in `server.py`"

**Files:**
- Modify: `pyproject.toml`
- Create: `src/ecu_simulator/api/__init__.py`, `src/ecu_simulator/api/options.py`
- Test: `tests/unit/test_gui_extra_is_optional.py`, `tests/unit/test_api_options.py`

**Interfaces:**
- Produces:
  - `ApiStartupError(Exception)`;
  - `ApiOptions(host: str, port: int, profile: str, version: str)`, frozen. `host` is the
    literal **bind** address, `"127.0.0.1"` or `"::1"`;
  - `parse_api(value: str, profile: str, version: str) -> ApiOptions`;
  - `allowed_hosts(bind_host: str, port: int) -> frozenset[str]`, lowercase `host:port`
    strings.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_api_options.py`:

```python
import pytest

from ecu_simulator.api.options import ApiOptions, ApiStartupError, allowed_hosts, parse_api


@pytest.mark.parametrize(
    ("value", "host", "port"),
    [("127.0.0.1:8765", "127.0.0.1", 8765), ("localhost:8765", "127.0.0.1", 8765),
     ("::1:8765", "::1", 8765), ("[::1]:8765", "::1", 8765), ("127.0.0.1:0", "127.0.0.1", 0)],
)
def test_loopback_forms_are_accepted(value, host, port):
    assert parse_api(value, "p.yaml", "1.0") == ApiOptions(host, port, "p.yaml", "1.0")


@pytest.mark.parametrize(
    "value",
    ["0.0.0.0:8765", "192.168.1.20:8765", "127.0.0.2:8765", "example.com:8765", ":8765", "[::]:8765"],
)
def test_non_loopback_hosts_are_refused(value):
    with pytest.raises(ApiStartupError, match="loopback"):
        parse_api(value, "p.yaml", "1.0")


@pytest.mark.parametrize("value", ["127.0.0.1", "127.0.0.1:", "127.0.0.1:x", "127.0.0.1:70000", "127.0.0.1:-1"])
def test_malformed_values_are_refused(value):
    with pytest.raises(ApiStartupError):
        parse_api(value, "p.yaml", "1.0")


def test_allowed_hosts():
    assert allowed_hosts("127.0.0.1", 8765) == {"127.0.0.1:8765", "localhost:8765"}
    assert allowed_hosts("::1", 8765) == {"[::1]:8765", "localhost:8765"}
```

`tests/unit/test_gui_extra_is_optional.py`:

```python
"""aiohttp is an opt-in extra (decisions/0010 §4.1, §9.1). These tests hold whether or not
the developer running them has the extra installed, except the one that says so by name."""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import tomllib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PYPROJECT = tomllib.loads((REPO / "pyproject.toml").read_text())
SRC = REPO / "src" / "ecu_simulator"
BLOCK_AIOHTTP = (
    "import sys\n"
    "class Block:\n"
    "    def find_spec(self, name, path=None, target=None):\n"
    "        if name == 'aiohttp' or name.startswith('aiohttp.'):\n"
    "            raise ImportError('aiohttp blocked by the test')\n"
    "        return None\n"
    "sys.meta_path.insert(0, Block())\n"
)


def test_the_gui_extra_pins_aiohttp_to_a_major_version():
    (spec,) = PYPROJECT["project"]["optional-dependencies"]["gui"]
    assert spec.startswith("aiohttp") and ">=" in spec and "<4" in spec, spec


def test_aiohttp_is_neither_a_runtime_nor_a_dev_dependency():
    for spec in PYPROJECT["project"]["dependencies"] + PYPROJECT["project"]["optional-dependencies"]["dev"]:
        assert "aiohttp" not in spec, spec


def test_only_the_server_module_imports_aiohttp():
    offenders = [
        str(path.relative_to(SRC)) for path in SRC.rglob("*.py")
        if ("import aiohttp" in path.read_text() or "from aiohttp" in path.read_text())
        and path != SRC / "api" / "server.py"
    ]
    assert offenders == [], offenders


def test_the_simulator_validates_a_profile_with_aiohttp_unavailable():
    program = BLOCK_AIOHTTP + "from ecu_simulator import cli\nraise SystemExit(cli.main(['validate-config']))\n"
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_aiohttp_is_not_installed_in_this_environment():
    # 0010 §9.1, the stronger "not installed" form. It runs in the .[dev] CI jobs, and skips
    # by name wherever the [gui] extra is installed.
    if importlib.util.find_spec("aiohttp") is not None:
        pytest.skip("aiohttp is installed (a [gui] environment): the 'not installed' form runs in the .[dev] jobs")
    assert importlib.util.find_spec("aiohttp") is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_api_options.py tests/unit/test_gui_extra_is_optional.py -q -p no:cacheprovider`
Expected: collection error, `No module named 'ecu_simulator.api'`. The pyproject test would
fail with `KeyError: 'gui'`.

- [ ] **Step 3: Implement**

`pyproject.toml`: after `hardware = [...]`, add:

```toml
# The observer API and its browser page (docs/decisions/0010). Opt-in: nothing in the
# default install imports aiohttp, and `ecu-simulator` without --api never does (§9.1).
gui = ["aiohttp>=3.13,<4"]
```

and extend the wheel artifacts:

```toml
artifacts = ["src/ecu_simulator/profiles/*.yaml", "src/ecu_simulator/api/static/*"]
```

`src/ecu_simulator/api/__init__.py`:

```python
"""The opt-in observer API (decisions/0010). Importing this package imports no aiohttp:
only ``api.server`` does, and only ``app.run`` with ``--api`` imports that."""
```

`src/ecu_simulator/api/options.py`:

```python
"""``--api HOST:PORT``: parsing and the loopback rule (decisions/0010 §6). Standard library only."""

from __future__ import annotations

from dataclasses import dataclass

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


class ApiStartupError(Exception):
    """The API cannot start: bad address, missing [gui] extra, busy port or oversized state. Exit status 2."""


@dataclass(frozen=True)
class ApiOptions:
    host: str       # the literal bind address: "127.0.0.1" or "::1"
    port: int       # 0 asks the kernel for a free port (tests)
    profile: str    # reported by GET /status
    version: str    # reported by GET /status


def parse_api(value: str, profile: str, version: str) -> ApiOptions:
    host, sep, port_text = value.rpartition(":")
    if not sep or not host:
        raise ApiStartupError(f"--api expects HOST:PORT, got {value!r}")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if host not in LOOPBACK_HOSTS:
        raise ApiStartupError(
            f"--api host must be 127.0.0.1, ::1 or localhost (loopback only, decisions/0010 §6), got {host!r}"
        )
    if not port_text.isdigit() or int(port_text) > 65535:
        raise ApiStartupError(f"--api port must be 0-65535, got {port_text!r}")
    # "localhost" binds IPv4 only, so the bound address and the Host allowlist are exact.
    return ApiOptions("127.0.0.1" if host == "localhost" else host, int(port_text), profile, version)


def allowed_hosts(bind_host: str, port: int) -> frozenset[str]:
    literal = f"[{bind_host}]" if ":" in bind_host else bind_host
    return frozenset({f"{literal}:{port}", f"localhost:{port}"})
```

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_api_options.py tests/unit/test_gui_extra_is_optional.py -q -p no:cacheprovider`
Expected: all pass. With `[gui]` installed in the worktree `.venv`,
`test_aiohttp_is_not_installed_in_this_environment` **skips, with its reason**. Record the
skip.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/ecu_simulator/api tests/unit/test_api_options.py tests/unit/test_gui_extra_is_optional.py
git commit -m "feat(api): the optional [gui] extra and loopback-only --api parsing"
```

Then install the extra into the worktree venv:
`uv pip install --python .venv/bin/python -e '.[dev,hardware,gui]'`.

---

### Task 2: connection wake-ups, `take_state`, and `run_writer` (the writer contract as code)

**Files:**
- Modify: `src/ecu_simulator/observe/connection.py`
- Create: `src/ecu_simulator/observe/writer.py`
- Test: `tests/unit/observe/test_writer.py`, and additions to `tests/unit/observe/test_connection.py`

**Interfaces:**
- Consumes: M1's `Connection`.
- Produces:
  - `Connection.take_state() -> str | None`;
  - `Connection.mark_unknown() -> None`, with a new ledger field `delivery_unknown: int`;
  - `async Connection.wait_changed() -> None`;
  - `async Connection.wait_closed() -> None`;
  - `async run_writer(conn, send: Callable[[str], Awaitable[None]], hello: dict, history: list[str]) -> None`;
  - `class NotDelivered(Exception)` in `observe/writer.py`. A `send` raises it only when it
    **knows** nothing was written (owner decision 8).

**Contract changes, decided here:**
- **Three ways a send resolves.**
  - A `send` that **returned** is `sent` (`mark_sent`).
  - One that raised **`NotDelivered`** is `discarded_on_close` (`mark_failed`): it is known
    not to have been written.
  - One that raised **anything else**, or was **cancelled** while under way, is
    `delivery_unknown` (`mark_unknown`): it may or may not have reached the client, and the
    ledger does not guess (owner decision 8). The identity becomes
  `enqueued = sent + delivery_unknown + queued + discarded_on_close`.
- **Message order.** The writer sends `hello`, then `take_state()`, then history, then
  loops on `next_message()`. That gives 0010 §4.5's order: hello, state, history, live.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/observe/test_connection.py`, and update its `identities()` helper, and
the identity assertions in `test_publisher.py`, to
`ledger["enqueued"] == ledger["sent"] + ledger["delivery_unknown"] + ledger["queued"] + ledger["discarded_on_close"]`:

```python
import asyncio


def test_take_state_empties_the_slot():
    c, _ = conn()
    c.set_state("s")
    assert c.take_state() == "s" and c.take_state() is None and c.next_message() is None


@pytest.mark.asyncio
async def test_wait_changed_wakes_on_offer_state_dropped_and_close():
    for poke in (lambda c: c.offer("x"), lambda c: c.set_state("s"), lambda c: c.set_dropped("d"),
                 lambda c: c.close(1000, published_now=10)):
        c, _ = conn()
        waiter = asyncio.create_task(c.wait_changed())
        await asyncio.sleep(0)
        assert not waiter.done()
        poke(c)
        await asyncio.wait_for(waiter, 1)


def test_mark_unknown_resolves_the_in_flight_exchange_as_delivery_unknown():
    for close_first in (False, True):
        c, _ = conn()
        c.offer("x")
        c.next_message()
        if close_first:
            c.close(1001, published_now=11)
        c.mark_unknown()
        c.mark_sent()                                     # nothing in flight any more: a no-op
        ledger = c.ledger(published_now=11)
        assert (ledger["sent"], ledger["delivery_unknown"], ledger["queued"], ledger["discarded_on_close"]) == (0, 1, 0, 0)
        identities(ledger)


@pytest.mark.asyncio
async def test_wait_closed_wakes_only_on_close():
    c, _ = conn()
    waiter = asyncio.create_task(c.wait_closed())
    c.offer("x")
    await asyncio.sleep(0)
    assert not waiter.done()
    c.close(1013, published_now=11)
    await asyncio.wait_for(waiter, 1)
```

`tests/unit/observe/test_writer.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/observe/test_writer.py tests/unit/observe/test_connection.py -q -p no:cacheprovider`
Expected: `No module named 'ecu_simulator.observe.writer'`. The connection tests fail with
`AttributeError: 'Connection' object has no attribute 'take_state'`.

- [ ] **Step 3: Implement**

In `connection.py`:

1. Add `import asyncio`.
2. In `__init__`:

   ```python
           self._changed = asyncio.Event()       # a message may be available, or the connection closed
           self._closed_event = asyncio.Event()
   ```
3. Call `self._changed.set()` at the end of the enqueue branch of `offer`, and in
   `set_state` and `set_dropped`, after the slot is filled.
4. In `_close`, add `self._changed.set()` and `self._closed_event.set()`.
5. Add the counter `self.delivery_unknown = 0` beside `sent`. In `ledger()`, add the key
   `"delivery_unknown": self.delivery_unknown`.
6. Add these methods:

   ```python
       def take_state(self) -> str | None:
           """The pending ``state``, for the writer to send between ``hello`` and history (0010 §4.5)."""
           text, self._state = self._state, None
           return text

       async def wait_changed(self) -> None:
           """Until something may be sendable or the connection closed. Spurious wake-ups are harmless."""
           await self._changed.wait()
           self._changed.clear()

       async def wait_closed(self) -> None:
           await self._closed_event.wait()

       def mark_unknown(self) -> None:
           """The writer's ``send_str`` was cancelled while under way: delivery is unknown.

           Neither sent nor discarded. P5 treats any such exchange as inconclusive (0010 §9.2).
           """
           if self._in_flight is not None:
               self._in_flight = None
               self.delivery_unknown += 1
   ```
7. Extend the module docstring's contract:
   - add a step 0: "send `hello`, then `take_state()` if not `None`, then the history, then
     loop";
   - replace step 3 with the three outcomes: `mark_sent()` if `send_str` returned;
     `mark_failed()` only if non-delivery is **known**, meaning `NotDelivered`; and
     `mark_unknown()` for any other exception, or a cancellation while under way;
   - state the identity
     `enqueued = sent + delivery_unknown + queued + discarded_on_close`.

`src/ecu_simulator/observe/writer.py`:

```python
"""One WebSocket client's writer task: the contract in connection.py, as code.

Standard library only. ``send`` is the socket's ``send_str``, injected, so the contract is
tested without aiohttp and holds for any transport M2 or later puts behind it.
"""

from __future__ import annotations

import asyncio
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
    for text in history:
        await send(text)
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
```

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/observe -q -p no:cacheprovider`
Expected: all pass. That is 57 from M1, plus 4 connection tests and 6 writer tests.

- [ ] **Step 5: Commit**

```bash
git add src/ecu_simulator/observe tests/unit/observe
git commit -m "feat(observe): writer task, connection wake-ups and take_state; the writer contract as code"
```

---

### Task 3: cumulative accounting for P5, and per-connection options

**Files:**
- Modify: `src/ecu_simulator/observe/publisher.py`
- Test: additions to `tests/unit/observe/test_publisher.py`

**Interfaces:**
- Produces:
  - `Publisher(..., connection_options: Mapping[str, Any] | None = None)`, passed as
    keyword arguments to every `Connection`. Tests and the M2 early check use it; `run()`
    never does;
  - `Publisher.connections_opened: int`;
  - `stats()` gains `connections_opened`, `closed_totals` and `closed_unresolved`.

**`closed_totals`, exactly:**
- It is a dict with:
  - `connections`;
  - `published_span`, the sum of `published_at_close − published_at_open`;
  - `history_sent`, `offered`, `client_dropped`, `enqueued`, `sent`,
    `delivery_unknown` and `discarded_on_close`;
  - `close_codes`, a map from the code as a string to a count.
- A closed connection is added **once**, when it is closed **and** its `queued` is 0,
  meaning any in-flight send has resolved. Until then it counts in `closed_unresolved`.
- It then satisfies, at every instant:
  - `offered = published_span`;
  - `offered = enqueued + client_dropped`;
  - `enqueued = sent + delivery_unknown + discarded_on_close`.
- `connections_opened = clients + closed_totals.connections + closed_unresolved` holds at
  every `stats()` call.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/observe/test_publisher.py`:

```python
def test_totals_reconcile_beyond_the_64_retained_ledgers():
    handoff = HandOff()
    p = Publisher(handoff, Router(), {}, encode=lambda rec, *_: json.dumps({"seq": rec.seq}),
                  connection_options={"max_messages": 2, "overflow_disconnect_s": 0.0})
    seq = 1
    for i in range(200):
        c, _, _ = p.connect()
        fill(handoff, 3, start=seq)
        seq += 3
        p.drain_turn()                                   # 3 offered: 2 enqueued, then a forced 1013 on the 3rd
        if i % 2:
            p.disconnect(c, 1000)                        # already forced: stays 1013, idempotent
    stats = p.stats(issued=seq - 1)
    totals = stats["closed_totals"]
    assert len(stats["closed_connections"]) == 64 and totals["connections"] == 200
    assert stats["connections_opened"] == stats["clients"] + totals["connections"] + stats["closed_unresolved"] == 200
    assert totals["offered"] == totals["published_span"] == totals["enqueued"] + totals["client_dropped"]
    assert totals["enqueued"] == totals["sent"] + totals["delivery_unknown"] + totals["discarded_on_close"]
    assert totals["delivery_unknown"] == 0
    assert totals["close_codes"] == {"1013": 200} and stats["forced_disconnects"] == 200


def test_an_unresolved_close_is_added_to_the_totals_only_when_it_resolves():
    handoff = HandOff()
    p = publisher(handoff)
    c, _, _ = p.connect()
    fill(handoff, 1)
    p.drain_turn()
    c.next_message()                                      # in flight
    p.disconnect(c, 1001)
    stats = p.stats(issued=1)
    assert (stats["closed_unresolved"], stats["closed_totals"]["connections"]) == (1, 0)
    c.mark_sent()
    stats = p.stats(issued=1)
    assert (stats["closed_unresolved"], stats["closed_totals"]["connections"], stats["closed_totals"]["sent"]) == (0, 1, 1)


def test_delivery_unknown_is_carried_into_the_totals():
    handoff = HandOff()
    p = publisher(handoff)
    c, _, _ = p.connect()
    fill(handoff, 1)
    p.drain_turn()
    c.next_message()
    p.disconnect(c, 1001)
    c.mark_unknown()
    totals = p.stats(issued=1)["closed_totals"]
    assert (totals["connections"], totals["sent"], totals["delivery_unknown"]) == (1, 0, 1)
    assert totals["enqueued"] == totals["sent"] + totals["delivery_unknown"] + totals["discarded_on_close"]


def test_connection_options_reach_every_connection():
    p = Publisher(HandOff(), Router(), {}, connection_options={"max_messages": 1})
    c, _, _ = p.connect()
    assert c.offer("a") and c.offer("b") is False
```

- [ ] **Step 2: Run them to verify they fail**

Expected: `TypeError: Publisher.__init__() got an unexpected keyword argument 'connection_options'`.

- [ ] **Step 3: Implement**

In `publisher.py`:

```python
TOTAL_FIELDS = (
    "history_sent", "offered", "client_dropped", "enqueued", "sent", "delivery_unknown", "discarded_on_close",
)


def _empty_totals() -> dict[str, Any]:
    return {"connections": 0, "published_span": 0, **dict.fromkeys(TOTAL_FIELDS, 0), "close_codes": {}}
```

Changes to `Publisher`:
- **Constructor:** add the keyword `connection_options: Mapping[str, Any] | None = None`,
  and store `self._connection_options = dict(connection_options or {})`,
  `self.connections_opened = 0`, `self._unresolved: list[Connection] = []` and
  `self._totals = _empty_totals()`.
- **`connect`:** construct with
  `Connection(self._next_id, watermark, self.published, now=self._monotonic, **self._connection_options)`,
  then increment `self.connections_opened`.
- **`_retire`:** after `self.closed.append(conn)`, add `self._unresolved.append(conn)` and
  `self._fold()`.
- **New method:**

  ```python
      def _fold(self) -> None:
          # 0010 §5.1, cumulative: every closed connection, once, when its in-flight send has resolved.
          waiting = []
          for conn in self._unresolved:
              ledger = conn.ledger(self.published)
              if ledger["queued"]:
                  waiting.append(conn)
                  continue
              totals = self._totals
              totals["connections"] += 1
              totals["published_span"] += ledger["published_at_close"] - ledger["published_at_open"]
              for field in TOTAL_FIELDS:
                  totals[field] += ledger[field]
              code = str(ledger["close_code"])
              totals["close_codes"][code] = totals["close_codes"].get(code, 0) + 1
          self._unresolved = waiting
  ```
- **`stats()`:** call `self._fold()` after `self._retire_closed()`, and add:

  ```python
              "connections_opened": self.connections_opened,
              "closed_totals": {**self._totals, "close_codes": dict(self._totals["close_codes"])},
              "closed_unresolved": len(self._unresolved),
  ```

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/observe -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/ecu_simulator/observe/publisher.py tests/unit/observe/test_publisher.py
git commit -m "feat(observe): cumulative closed-connection totals so P5 reconciles past 64 closes"
```

---

### Task 4: `ApiServer`: lifecycle, guard middleware, REST routes, the one page, initial state

**Files:**
- Create: `src/ecu_simulator/api/server.py`, `src/ecu_simulator/api/static/index.html`
- Test: `tests/unit/api/__init__.py` (empty), `tests/unit/api/support.py`, `tests/unit/api/conftest.py`, `tests/unit/api/test_server_http.py`

**Interfaces:**
- Consumes:
  - `Runtime` and `build_endpoints` from `app`;
  - `ApiOptions`, `ApiStartupError` and `allowed_hosts` (Task 1);
  - `Publisher(connection_options=…)` (Task 3);
  - `snapshots`, including `status(..., profile)` (M1);
  - `ObservedDispatcher` and `HandOff` (M1).
- Produces:
  - `ApiServer(runtime, endpoints, options, *, state_interval_s=STATE_MIN_INTERVAL_S, connection_options=None)`;
  - `.handler`: the `ObservedDispatcher` to give the transport;
  - `.publisher`;
  - `async .start()`: binds, raises `ApiStartupError`, then starts the publisher tasks;
  - `async .stop()`;
  - `.port: int | None`: the bound port.

- [ ] **Step 1: Write the failing tests**

`tests/unit/api/support.py`. It imports aiohttp and the server lazily, so collecting it
never fails on a `.[dev]` install; the test modules skip first:

```python
import asyncio


def build(**kw):
    from ecu_simulator import app
    from ecu_simulator.api.options import ApiOptions
    from ecu_simulator.api.server import ApiServer
    from ecu_simulator.cli import default_profile_path
    from ecu_simulator.config import load_profile

    config = app.RuntimeConfig.build(load_profile(default_profile_path()), "vcan0")
    runtime = app.build_runtime(config)
    options = ApiOptions("127.0.0.1", 0, profile="profiles/ice_default.yaml", version="test")
    return ApiServer(runtime, app.build_endpoints(config), options, **kw)


def url(server, path):
    return f"http://127.0.0.1:{server.port}{path}"


async def raw_request(port: int, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    data = await reader.read(65536)
    writer.close()
    await writer.wait_closed()
    return data
```

`tests/unit/api/conftest.py`. pytest-asyncio runs in **strict** mode, so async fixtures
need `pytest_asyncio.fixture`:

```python
import pytest_asyncio

from tests.unit.api.support import build


@pytest_asyncio.fixture
async def server():
    s = build()
    await s.start()
    yield s
    await s.stop()


@pytest_asyncio.fixture
async def session():
    import aiohttp

    async with aiohttp.ClientSession() as client:
        yield client
```

`tests/unit/api/test_server_http.py`:

```python
import json
import socket

import pytest

aiohttp = pytest.importorskip("aiohttp", reason="needs the optional [gui] extra (aiohttp)")

from ecu_simulator.api.options import ApiStartupError  # noqa: E402
from ecu_simulator.observe import snapshots  # noqa: E402
from tests.unit.api.support import build, raw_request, url  # noqa: E402

ROUTES = ["/api/v1/status", "/api/v1/vehicle", "/api/v1/dtcs", "/api/v1/ecus", "/api/v1/exchanges"]


@pytest.mark.asyncio
async def test_every_route_answers_json(server, session):
    for path in ROUTES:
        async with session.get(url(server, path)) as r:
            assert r.status == 200 and r.content_type == "application/json", path
            body = await r.json()
    async with session.get(url(server, "/api/v1/status")) as r:
        status = await r.json()
    assert status["profile"] == "profiles/ice_default.yaml" and status["api"]["issued_seq"] == 0
    assert body == {"watermark": 0, "oldest_seq": None, "gap": False, "events": []}


def test_the_initial_state_is_ready_before_the_server_starts():
    s = build()                                           # constructed, not started: no socket, no task
    conn, _, _ = s.publisher.connect()
    assert conn.take_state() == snapshots.state_message(s.runtime)


@pytest.mark.asyncio
async def test_exchanges_reflect_dispatched_requests(server, session):
    from ecu_simulator.transport import DiagnosticRequest
    server.handler(DiagnosticRequest(b"\x01\x0c", 0x7DF, functional=True))
    server.publisher.drain_turn()
    async with session.get(url(server, "/api/v1/exchanges?after=0&limit=10")) as r:
        body = await r.json()
    assert (body["watermark"], body["gap"], [e["request"] for e in body["events"]]) == (1, False, ["010c"])


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["limit=0", "limit=x", "limit=1.5", "after=-1", "after=", "after=1e3"])
async def test_bad_exchange_queries_are_400(server, session, query):
    async with session.get(url(server, f"/api/v1/exchanges?{query}")) as r:
        assert r.status == 400


@pytest.mark.asyncio
async def test_every_other_method_is_405(server, session):
    for path in ROUTES + ["/", "/api/v1/events"]:
        for method in ("POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            async with session.request(method, url(server, path)) as r:
                assert r.status == 405, (method, path)


@pytest.mark.asyncio
async def test_host_header_rules(server, session):  # Review Focus 3
    port = server.port
    for host in (f"localhost:{port}", f"LOCALHOST:{port}", f"127.0.0.1:{port}"):
        async with session.get(url(server, "/api/v1/status"), headers={"Host": host}) as r:
            assert r.status == 200, host
    for host in ("127.0.0.1", f"127.0.0.1:{port + 1}", f"[::1]:{port}", f"evil.example:{port}"):
        async with session.get(url(server, "/api/v1/status"), headers={"Host": host}) as r:
            assert r.status == 421, host
    reply = await raw_request(port, b"GET /api/v1/status HTTP/1.0\r\n\r\n")   # no Host at all
    assert reply.startswith(b"HTTP/1.0 421") or reply.startswith(b"HTTP/1.1 421"), reply[:40]


@pytest.mark.asyncio
async def test_no_response_carries_cors_headers(server, session):
    async with session.get(url(server, "/api/v1/status"), headers={"Origin": "http://evil.example"}) as r:
        assert not [h for h in r.headers if h.lower().startswith("access-control-")]


@pytest.mark.asyncio
async def test_a_body_over_1_kib_is_413(server, session):
    async with session.get(url(server, "/api/v1/status"), data=b"x" * 1025) as r:
        assert r.status == 413


@pytest.mark.asyncio
async def test_the_placeholder_page_is_served(server, session):
    # M2 serves exactly this one file. Frontend files and rendering are M3's (decisions/0010 §7).
    async with session.get(url(server, "/")) as r:
        assert r.status == 200 and r.content_type == "text/html"
        assert "/api/v1/status" in await r.text()


@pytest.mark.asyncio
async def test_a_busy_port_is_an_api_startup_error():
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    try:
        s = build()
        s.options = type(s.options)("127.0.0.1", holder.getsockname()[1], "p", "t")
        with pytest.raises(ApiStartupError, match="cannot listen"):
            await s.start()
    finally:
        holder.close()


def test_an_oversized_state_refuses_to_construct(monkeypatch):
    monkeypatch.setattr(snapshots, "STATE_MAX_BYTES", 10)
    with pytest.raises(ApiStartupError, match="256 KiB"):
        build()
```

`snapshots.check_state_size` reads the module global `STATE_MAX_BYTES` when it is
called, so monkeypatching the module attribute works. The error message keeps the literal
"256 KiB".

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/api -q -p no:cacheprovider`
Expected: `No module named 'ecu_simulator.api.server'`.

- [ ] **Step 3: Implement**

`src/ecu_simulator/api/static/index.html`:

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>ECU simulator observer</title></head>
<body>
<h1>ECU simulator observer</h1>
<p>The browser view arrives in milestone M3. The read-only API is live:
<a href="/api/v1/status">/api/v1/status</a>, <a href="/api/v1/vehicle">/api/v1/vehicle</a>,
<a href="/api/v1/dtcs">/api/v1/dtcs</a>, <a href="/api/v1/ecus">/api/v1/ecus</a>,
<a href="/api/v1/exchanges">/api/v1/exchanges</a>, and the WebSocket <code>/api/v1/events</code>.</p>
</body>
</html>
```

`src/ecu_simulator/api/server.py`:

```python
"""The observer API server (decisions/0010 §5, §6). The only module that imports aiohttp.

It owns the observer: HandOff, Publisher and the ObservedDispatcher that run() hands the
transport. The initial state is computed here, before the socket is bound, so the first
WebSocket client receives it (0010 §4.5).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from importlib import resources
from typing import Any

from aiohttp import WSCloseCode, WSMsgType, web

from ecu_simulator import app
from ecu_simulator.api.options import ApiOptions, ApiStartupError, allowed_hosts
from ecu_simulator.observe import snapshots
from ecu_simulator.observe.connection import Connection
from ecu_simulator.observe.handoff import HandOff
from ecu_simulator.observe.limits import CLOSE_TOO_SLOW, STATE_MIN_INTERVAL_S
from ecu_simulator.observe.publisher import Publisher, TooManyClients
from ecu_simulator.observe.wrapper import ObservedDispatcher
from ecu_simulator.observe.writer import run_writer
from ecu_simulator.transport.socketcan import EndpointConfig

logger = logging.getLogger(__name__)

MAX_BODY = 1024                 # 0010 §4.3: incoming HTTP body
WS_CLOSE_TIMEOUT_S = 2.0        # a stalled client cannot answer the close handshake
WS_WRITER_LIMIT = 64 * 1024     # explicit: the default differs between aiohttp 3.13 and 3.14
WRITER_GRACE_S = 2.0            # a send under way when the socket closes gets this long to resolve
EVENTS = "/api/v1/events"
INT = re.compile(r"-?[0-9]{1,19}")
Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def _query_int(request: web.Request, name: str) -> int | None:
    value = request.query.get(name)
    if value is None:
        return None
    if not INT.fullmatch(value):
        raise web.HTTPBadRequest(text=f"{name} must be an integer")
    return int(value)


class ApiServer:
    def __init__(
        self,
        runtime: app.Runtime,
        endpoints: Iterable[EndpointConfig],
        options: ApiOptions,
        *,
        state_interval_s: float = STATE_MIN_INTERVAL_S,
        connection_options: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            snapshots.check_state_size(runtime)
        except ValueError as error:
            raise ApiStartupError(str(error)) from error
        self.runtime = runtime
        self.options = options
        self.handoff = HandOff()
        self.publisher = Publisher(self.handoff, runtime.router, {e.name: e for e in endpoints},
                                   connection_options=connection_options)
        self.handler = ObservedDispatcher(runtime.dispatcher, self.handoff, self.publisher.wake)
        # Owner decision 2026-09-27: state exists before the first client can connect.
        self.publisher.push_state(snapshots.state_message(runtime))
        self.started_at = time.time()
        self.port: int | None = None
        self._state_interval_s = state_interval_s
        self._allowed: frozenset[str] = frozenset()
        self._runner: web.AppRunner | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._sockets: set[web.WebSocketResponse] = set()
        self._page = resources.files("ecu_simulator.api").joinpath("static/index.html").read_bytes()

    def application(self) -> web.Application:
        @web.middleware
        async def guard(request: web.Request, handler: Handler) -> web.StreamResponse:
            host = request.host.lower()
            if host not in self._allowed:
                raise web.HTTPMisdirectedRequest(text="Host not allowed (decisions/0010 §6)")
            if request.content_length is not None and request.content_length > MAX_BODY:
                raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY, actual_size=request.content_length)
            upgrade = request.path == EVENTS and request.method == "GET"
            # Same-origin against THIS request's validated Host: localhost and 127.0.0.1 are
            # different origins and are never substituted for one another (owner, 2026-09-27).
            if upgrade and (request.headers.get("Origin") or "").lower() != f"http://{host}":
                raise web.HTTPForbidden(text="Origin must match Host (decisions/0010 §6)")
            return await handler(request)

        application = web.Application(middlewares=[guard], client_max_size=MAX_BODY)
        routes: list[tuple[str, Handler]] = [
            ("/", self._index),
            ("/api/v1/status", self._status),
            ("/api/v1/vehicle", self._vehicle),
            ("/api/v1/dtcs", self._dtcs),
            ("/api/v1/ecus", self._ecus),
            ("/api/v1/exchanges", self._exchanges),
            (EVENTS, self._events),
        ]
        for path, handler in routes:
            application.router.add_get(path, handler, allow_head=False)
        return application

    async def start(self) -> None:
        self._runner = web.AppRunner(self.application(), access_log=None, shutdown_timeout=2.0)
        await self._runner.setup()
        try:
            await web.TCPSite(self._runner, self.options.host, self.options.port).start()
        except OSError as error:
            await self._runner.cleanup()
            self._runner = None
            raise ApiStartupError(
                f"--api cannot listen on {self.options.host}:{self.options.port}: {error.strerror}"
            ) from error
        self.port = int(self._runner.addresses[0][1])
        self._allowed = allowed_hosts(self.options.host, self.port)
        state = lambda: snapshots.state_message(self.runtime)  # noqa: E731
        self._tasks = [
            asyncio.create_task(self.publisher.run()),
            asyncio.create_task(self.publisher.run_state(state, self._state_interval_s)),
        ]
        logger.info("observer API on http://%s/", sorted(self._allowed)[0])

    async def stop(self) -> None:
        for ws in list(self._sockets):
            await ws.close(code=WSCloseCode.GOING_AWAY, message=b"server shutdown")
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _index(self, request: web.Request) -> web.Response:
        return web.Response(body=self._page, content_type="text/html", charset="utf-8")

    async def _status(self, request: web.Request) -> web.Response:
        return web.json_response(snapshots.status(
            self.runtime, self.publisher, self.handler.issued, self.started_at,
            self.options.version, self.options.profile,
        ))

    async def _vehicle(self, request: web.Request) -> web.Response:
        return web.json_response(snapshots.vehicle(self.runtime))

    async def _dtcs(self, request: web.Request) -> web.Response:
        return web.json_response(snapshots.dtcs(self.runtime))

    async def _ecus(self, request: web.Request) -> web.Response:
        return web.json_response(snapshots.ecus(self.runtime))

    async def _exchanges(self, request: web.Request) -> web.Response:
        after, limit = _query_int(request, "after"), _query_int(request, "limit")
        history = self.publisher.history
        try:
            texts, gap = history.since(after, limit)
        except (TypeError, ValueError) as error:
            raise web.HTTPBadRequest(text=str(error)) from error
        oldest = "null" if history.oldest_seq is None else str(history.oldest_seq)
        body = (f'{{"watermark":{history.last_seq},"oldest_seq":{oldest},'
                f'"gap":{"true" if gap else "false"},"events":[{",".join(texts)}]}}')
        return web.Response(text=body, content_type="application/json")

    async def _events(self, request: web.Request) -> web.StreamResponse:
        raise web.HTTPNotImplemented(text="Task 5")
```

The events are joined as already-encoded text: each is encoded once, by the publisher
(0010 §4.2).

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/api -q -p no:cacheprovider`
Expected: all pass. On `/api/v1/events`, `test_every_other_method_is_405` gets 405 from
the router before the placeholder handler runs.

- [ ] **Step 5: Commit**

```bash
git add src/ecu_simulator/api tests/unit/api
git commit -m "feat(api): ApiServer with loopback guard, REST routes, one page and state primed before binding"
```

---

### Task 5: the WebSocket route

**Files:**
- Modify: `src/ecu_simulator/api/server.py`, replacing `_events` and adding `_close_when_forced`
- Test: `tests/unit/api/test_server_ws.py`

**Interfaces:**
- Consumes: `Publisher.connect`, `TooManyClients`, `run_writer`, `NotDelivered`, `Connection.wait_closed`.
- Produces:
  - `WS /api/v1/events?after=S`, following 0010 §4.5, §4.3 and §6;
  - `send_via(ws, request) -> Send` in `server.py`: the one place that decides "known not
    delivered" (owner decision 8).

- [ ] **Step 1: Write the failing tests**

`tests/unit/api/test_server_ws.py`:

```python
import asyncio
import json
import socket
import time

import pytest

aiohttp = pytest.importorskip("aiohttp", reason="needs the optional [gui] extra (aiohttp)")

from ecu_simulator.observe import snapshots  # noqa: E402
from ecu_simulator.transport import DiagnosticRequest  # noqa: E402
from tests.unit.api.support import build, url  # noqa: E402

REQ = DiagnosticRequest(b"\x01\x0c", 0x7DF, functional=True)


def origin(server):
    return f"http://127.0.0.1:{server.port}"


async def next_json(ws):
    msg = await asyncio.wait_for(ws.receive(), 2)
    assert msg.type == aiohttp.WSMsgType.TEXT, msg
    return json.loads(msg.data)


async def next_exchange(ws):
    # run_state sets a one-slot `dropped` notice every 250 ms (0010 §4.3); it may come first.
    while (event := await next_json(ws))["type"] != "exchange":
        assert event["type"] == "dropped", event
    return event


async def publish(server, n):
    for _ in range(n):
        server.handler(REQ)
    for _ in range(50):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_hello_state_history_then_live(server, session):
    await publish(server, 2)                                          # seq 1, 2 go to history
    async with session.ws_connect(url(server, "/api/v1/events"), origin=origin(server)) as ws:
        hello = await next_json(ws)
        assert hello == {"type": "hello", "api": 1, "watermark": 2, "oldest_seq": 1}
        assert await next_json(ws) == json.loads(snapshots.state_message(server.runtime))
        assert [(await next_json(ws))["seq"] for _ in range(2)] == [1, 2]   # history is sent directly
        await publish(server, 1)
        live = await next_exchange(ws)
        assert (live["type"], live["seq"], live["outcome"]) == ("exchange", 3, "responded")


@pytest.mark.asyncio
async def test_origin_missing_or_foreign_is_403_before_the_upgrade(server, session):
    for bad in (None, "http://evil.example", f"http://127.0.0.1:{server.port + 1}"):
        with pytest.raises(aiohttp.WSServerHandshakeError) as info:
            await session.ws_connect(url(server, "/api/v1/events"), origin=bad)
        assert info.value.status == 403, bad
    assert server.publisher.stats(issued=0)["connections_opened"] == 0


@pytest.mark.asyncio
async def test_origin_must_match_the_host_of_the_same_request(server, session):
    port = server.port
    events = url(server, "/api/v1/events")
    for host, good, bad in ((f"127.0.0.1:{port}", f"http://127.0.0.1:{port}", f"http://localhost:{port}"),
                            (f"localhost:{port}", f"http://localhost:{port}", f"http://127.0.0.1:{port}")):
        with pytest.raises(aiohttp.WSServerHandshakeError) as info:     # the other loopback name: refused
            await session.ws_connect(events, origin=bad, headers={"Host": host})
        assert info.value.status == 403, (host, bad)
        async with session.ws_connect(events, origin=good, headers={"Host": host}) as ws:   # its own origin: accepted
            assert (await next_json(ws))["type"] == "hello"
    with pytest.raises(aiohttp.WSServerHandshakeError) as info:
        await session.ws_connect(events, origin=f"https://127.0.0.1:{port}")           # scheme is part of the origin
    assert info.value.status == 403


@pytest.mark.asyncio
async def test_a_fifth_client_is_503_before_the_upgrade(server, session):
    sockets = [await session.ws_connect(url(server, "/api/v1/events"), origin=origin(server)) for _ in range(4)]
    with pytest.raises(aiohttp.WSServerHandshakeError) as info:
        await session.ws_connect(url(server, "/api/v1/events"), origin=origin(server))
    assert info.value.status == 503 and server.publisher.refused_clients == 1
    for ws in sockets:
        await ws.close()


@pytest.mark.asyncio
async def test_a_bad_after_is_400_before_the_upgrade(server, session):
    for query in ("after=-1", "after=x"):
        with pytest.raises(aiohttp.WSServerHandshakeError) as info:
            await session.ws_connect(url(server, f"/api/v1/events?{query}"), origin=origin(server))
        assert info.value.status == 400, query


@pytest.mark.asyncio
async def test_a_client_data_message_closes_1008(server, session):
    async with session.ws_connect(url(server, "/api/v1/events"), origin=origin(server)) as ws:
        await next_json(ws)                                           # hello
        await ws.send_str("hi")
        while (await asyncio.wait_for(ws.receive(), 2)).type == aiohttp.WSMsgType.TEXT:
            pass
        assert ws.close_code == 1008


@pytest.mark.asyncio
async def test_a_client_that_disconnects_mid_stream_is_retired_cleanly(server, session, caplog):  # Review Focus 2
    ws = await session.ws_connect(url(server, "/api/v1/events"), origin=origin(server))
    await next_json(ws)
    await ws.close()
    await publish(server, 200)
    deadline = time.monotonic() + 3
    while server.publisher.stats(issued=server.handler.issued)["clients"] and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    stats = server.publisher.stats(issued=server.handler.issued)
    assert stats["clients"] == 0 and stats["closed_unresolved"] == 0 and stats["closed_totals"]["connections"] == 1
    totals = stats["closed_totals"]
    assert totals["enqueued"] == totals["sent"] + totals["delivery_unknown"] + totals["discarded_on_close"]
    assert "Task exception was never retrieved" not in caplog.text


STALLED_FINDING: dict[str, int] = {}


@pytest.mark.asyncio
async def test_a_stalled_client_is_closed_1013_and_frees_its_slot():  # Review Focus 1
    s = build(connection_options={"max_messages": 4, "overflow_disconnect_s": 0.2})
    await s.start()
    raw = socket.socket()
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    raw.connect(("127.0.0.1", s.port))
    raw.sendall(
        f"GET /api/v1/events HTTP/1.1\r\nHost: 127.0.0.1:{s.port}\r\nOrigin: http://127.0.0.1:{s.port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n".encode()
    )                                                                 # then never reads again
    try:
        deadline = time.monotonic() + 20
        while s.publisher.forced_disconnects == 0 and time.monotonic() < deadline:
            await publish(s, 1000)                                    # enough to fill every buffer
        assert s.publisher.forced_disconnects == 1
        while s.publisher.stats(issued=s.handler.issued)["clients"] and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        stats = s.publisher.stats(issued=s.handler.issued)
        assert stats["clients"] == 0 and stats["closed_totals"]["close_codes"] == {"1013": 1}
        totals = stats["closed_totals"]
        assert totals["enqueued"] == totals["sent"] + totals["delivery_unknown"] + totals["discarded_on_close"]
        STALLED_FINDING["delivery_unknown"] = totals["delivery_unknown"]    # read by the next test
        async with aiohttp.ClientSession() as session:                # the slot is free again
            async with session.ws_connect(url(s, "/api/v1/events"), origin=origin(s)) as ws:
                assert (await next_json(ws))["type"] == "hello"
    finally:
        raw.close()
        await s.stop()


def test_a_stalled_clients_blocked_send_is_not_delivery_unknown():
    # The measurement behind 0010 P5(h) for M4 condition 4: if a stalled client's blocked
    # send resolves as delivery_unknown, every condition-4 run is inconclusive. Kept separate
    # so the lifecycle test above stays a clean pass or fail.
    if "delivery_unknown" not in STALLED_FINDING:
        pytest.skip("the stalled-client lifecycle test did not run in this session")
    assert STALLED_FINDING["delivery_unknown"] == 0


class FakeTransport:
    def __init__(self, closing):
        self.closing = closing

    def is_closing(self):
        return self.closing


class FakeWs:
    def __init__(self, closed=False, error=None):
        self.closed, self.error, self.sent = closed, error, []

    async def send_str(self, text):
        if self.error:
            raise self.error
        self.sent.append(text)


class FakeRequest:
    def __init__(self, closing=False, transport=True):
        self.transport = FakeTransport(closing) if transport else None


@pytest.mark.asyncio
async def test_send_via_raises_not_delivered_only_when_nothing_can_be_written():  # owner decision 8
    from ecu_simulator.api.server import send_via
    from ecu_simulator.observe.writer import NotDelivered
    for ws, request in ((FakeWs(closed=True), FakeRequest()), (FakeWs(), FakeRequest(closing=True)),
                        (FakeWs(), FakeRequest(transport=False))):
        with pytest.raises(NotDelivered):
            await send_via(ws, request)("x")
        assert ws.sent == []                                            # send_str was never called
    ok = FakeWs()
    await send_via(ok, FakeRequest())("x")
    assert ok.sent == ["x"]
    with pytest.raises(ConnectionResetError):                            # after the pre-check: not NotDelivered
        await send_via(FakeWs(error=ConnectionResetError("Connection lost")), FakeRequest())("x")


@pytest.mark.asyncio
async def test_shutdown_closes_clients_with_1001(session):  # Review Focus 5
    s = build()
    await s.start()
    ws = await session.ws_connect(url(s, "/api/v1/events"), origin=origin(s))
    await next_json(ws)
    started = time.monotonic()
    await s.stop()
    while (await asyncio.wait_for(ws.receive(), 3)).type == aiohttp.WSMsgType.TEXT:
        pass
    assert ws.close_code == 1001 and time.monotonic() - started < 5
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/api/test_server_ws.py -q -p no:cacheprovider`
Expected: the handshake tests fail with status 501, the placeholder from Task 4, and the
stream tests fail the same way.

- [ ] **Step 3: Implement.** Replace `_events` in `server.py`:

```python
    async def _events(self, request: web.Request) -> web.StreamResponse:
        after = _query_int(request, "after")
        try:
            conn, hello, history = self.publisher.connect(after)
        except TooManyClients:
            raise web.HTTPServiceUnavailable(text="too many clients (decisions/0010 §4.3)") from None
        except (TypeError, ValueError) as error:
            raise web.HTTPBadRequest(text=str(error)) from error
        ws = web.WebSocketResponse(timeout=WS_CLOSE_TIMEOUT_S, compress=False, max_msg_size=MAX_BODY,
                                   writer_limit=WS_WRITER_LIMIT)
        try:
            await ws.prepare(request)
        except BaseException:
            self.publisher.disconnect(conn, WSCloseCode.ABNORMAL_CLOSURE)   # recorded, never sent
            raise
        self._sockets.add(ws)
        writer = asyncio.create_task(run_writer(conn, send_via(ws, request), hello, history))
        closer = asyncio.create_task(self._close_when_forced(conn, ws))
        try:
            async for msg in ws:
                if msg.type in (WSMsgType.TEXT, WSMsgType.BINARY):
                    await ws.close(code=WSCloseCode.POLICY_VIOLATION, message=b"v1 accepts no client messages")
                    break
        finally:
            self._sockets.discard(ws)
            self.publisher.disconnect(conn, ws.close_code or WSCloseCode.GOING_AWAY)  # a forced 1013 stays 1013
            closer.cancel()
            try:
                await asyncio.wait_for(writer, WRITER_GRACE_S)   # a timeout cancels it: delivery_unknown
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as error:                        # the socket went away mid-send: resolved as failed
                logger.debug("writer for connection %d ended: %r", conn.id, error)
            with contextlib.suppress(asyncio.CancelledError):
                await closer
        return ws

    async def _close_when_forced(self, conn: Connection, ws: web.WebSocketResponse) -> None:
        # The writer may be blocked in send_str on a client that stopped reading, so the
        # forced close cannot wait for it (Review Focus 1).
        await conn.wait_closed()
        if conn.close_code == CLOSE_TOO_SLOW:
            await ws.close(code=WSCloseCode.TRY_AGAIN_LATER, message=b"client too slow")
```

Add at module level, and import `NotDelivered` and `Send` from `ecu_simulator.observe.writer`:

```python
def send_via(ws: Any, request: Any) -> Send:
    """``ws.send_str`` with the one known non-delivery made explicit (owner decision 8).

    With compression off, ``send_str`` reaches ``transport.write()`` without suspending, and
    its own pre-write refusals are the two checked here, synchronously. So a refusal here
    means nothing was written (``NotDelivered``); any exception from ``send_str`` itself
    arises at or after the write, and the writer records it as delivery_unknown.
    """
    async def send(text: str) -> None:
        transport = request.transport
        if ws.closed or transport is None or transport.is_closing():
            raise NotDelivered("socket closed or closing: nothing written")
        await ws.send_str(text)
    return send
```

Delete the Task 4 placeholder.

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/api -q -p no:cacheprovider`, three times.
Expected: all pass, every time. The stalled-client test finishes in seconds, not at its
20 s bound. **If it is flaky, do not raise the bound.** Diagnose instead: the buffer that
absorbs the backlog is the likely cause. Record the finding.

**Expected stalled-client finding.** By the reading of aiohttp in owner decision 8, a stalled
client's writer is blocked in the drain wait **after** its frame was written, so the forced
close most likely resolves that exchange as `delivery_unknown` = 1. If
`test_a_stalled_clients_blocked_send_is_not_delivery_unknown` fails that way:
- do **not** change its assertion;
- mark it `@pytest.mark.xfail(strict=True, reason="stalled-client finding: blocked send resolves as delivery_unknown=<measured>; M4 condition 4 P5(h) decision pending")`;
- record the measured value in the report.

`strict=True` means the test turns red if the behaviour ever changes. The owner decides
what P5(h) does with condition 4.

- [ ] **Step 5: Commit**

```bash
git add src/ecu_simulator/api/server.py tests/unit/api/test_server_ws.py
git commit -m "feat(api): WebSocket events with hello/state/history ordering, 503/400/403 before upgrade, 1008 and 1013"
```

---

### Task 6: `run()` and CLI wiring, and the §9.1 proofs deferred from M1

**Files:**
- Modify: `src/ecu_simulator/app.py` (`run`, `main`), `src/ecu_simulator/cli.py`
- Test: `tests/unit/test_api_wiring.py` (no aiohttp), `tests/unit/api/test_run_with_api.py`

**Interfaces:**
- Produces:
  - `app.run(config, *, stop=None, install_signal_handlers=True, transport_factory=IsoTpTransport, clock=None, api: ApiOptions | None = None)`;
  - `app.main(config, api: ApiOptions | None = None) -> int`: exit 2 on `ApiStartupError`;
  - CLI `--api HOST:PORT`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_api_wiring.py`:

```python
"""decisions/0010 §9.1, the two proofs M1 deferred: with the API off, run() hands the
transport the plain Dispatcher, and aiohttp is never imported."""

import asyncio
import subprocess
import sys

import pytest

from ecu_simulator import app, cli
from ecu_simulator.api.options import ApiOptions, ApiStartupError
from ecu_simulator.config import load_profile
from ecu_simulator.ecu.dispatcher import Dispatcher
from tests.unit.test_cli import isolated_logging  # noqa: F401  (fixture: cli.main configures logging)


def shipped():
    return app.RuntimeConfig.build(load_profile(cli.default_profile_path()), "vcan0")


class Capture:
    handlers: list = []
    instances: list = []

    def __init__(self, interface, endpoints):
        Capture.instances.append(self)

    async def start(self, handler):
        Capture.handlers.append(handler)

    async def stop(self):
        pass


@pytest.mark.asyncio
async def test_api_off_hands_the_transport_the_plain_dispatcher():
    Capture.handlers.clear()
    stop = asyncio.Event()
    stop.set()
    await app.run(shipped(), stop=stop, install_signal_handlers=False, transport_factory=Capture)
    (handler,) = Capture.handlers
    assert type(handler) is Dispatcher


def test_api_off_never_imports_aiohttp():
    program = (
        "import asyncio, sys\n"
        "from ecu_simulator import app, cli\n"
        "from ecu_simulator.config import load_profile\n"
        "class T:\n"
        "    def __init__(self, i, e): pass\n"
        "    async def start(self, h): pass\n"
        "    async def stop(self): pass\n"
        "async def go():\n"
        "    stop = asyncio.Event(); stop.set()\n"
        "    config = app.RuntimeConfig.build(load_profile(cli.default_profile_path()), 'vcan0')\n"
        "    await app.run(config, stop=stop, install_signal_handlers=False, transport_factory=T)\n"
        "asyncio.run(go())\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] == 'aiohttp'))\n"
    )
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_the_cli_refuses_a_non_loopback_api_with_exit_2(isolated_logging, monkeypatch):
    called = []
    monkeypatch.setattr(app, "main", lambda *a, **k: called.append(1) or 0)
    assert cli.main(["--api", "0.0.0.0:8765"]) == 2 and called == []


def test_the_cli_refuses_api_without_the_extra_with_exit_2(isolated_logging, monkeypatch):
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: None if name == "aiohttp" else object())
    called = []
    monkeypatch.setattr(app, "main", lambda *a, **k: called.append(1) or 0)
    assert cli.main(["--api", "127.0.0.1:8765"]) == 2 and called == []


def test_the_cli_passes_parsed_options_to_app_main(isolated_logging, monkeypatch):
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: object())
    seen = []
    monkeypatch.setattr(app, "main", lambda config, api=None: seen.append(api) or 0)
    assert cli.main(["--api", "localhost:8765"]) == 0
    (options,) = seen
    assert (options.host, options.port, options.profile) == ("127.0.0.1", 8765, str(cli.default_profile_path()))


def test_app_main_turns_an_api_startup_error_into_exit_2(monkeypatch):
    async def failing(config, api=None):
        raise ApiStartupError("busy")
    monkeypatch.setattr(app, "run", failing)
    assert app.main(shipped(), ApiOptions("127.0.0.1", 1, "p", "t")) == 2
```

`tests/unit/api/test_run_with_api.py`:

```python
import asyncio

import pytest

aiohttp = pytest.importorskip("aiohttp", reason="needs the optional [gui] extra (aiohttp)")

import socket  # noqa: E402

from ecu_simulator import app  # noqa: E402
from ecu_simulator.api.options import ApiOptions, ApiStartupError  # noqa: E402
from ecu_simulator.observe.wrapper import ObservedDispatcher  # noqa: E402
from tests.unit.test_api_wiring import Capture, shipped  # noqa: E402


@pytest.mark.asyncio
async def test_api_on_hands_the_transport_the_observed_dispatcher():
    Capture.handlers.clear()
    stop = asyncio.Event()
    stop.set()
    await app.run(shipped(), stop=stop, install_signal_handlers=False, transport_factory=Capture,
                  api=ApiOptions("127.0.0.1", 0, "p", "t"))
    (handler,) = Capture.handlers
    assert isinstance(handler, ObservedDispatcher)


@pytest.mark.asyncio
async def test_a_busy_api_port_fails_before_any_can_socket_opens():  # Review Focus 4
    Capture.instances.clear()
    Capture.handlers.clear()
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    try:
        with pytest.raises(ApiStartupError):
            await app.run(shipped(), install_signal_handlers=False, transport_factory=Capture,
                          api=ApiOptions("127.0.0.1", holder.getsockname()[1], "p", "t"))
    finally:
        holder.close()
    assert Capture.handlers == [], "the transport must not have been started"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_api_wiring.py tests/unit/api/test_run_with_api.py -q -p no:cacheprovider`
Expected:
- `run() got an unexpected keyword argument 'api'`;
- the CLI tests fail with argparse's "unrecognized arguments: --api".

`test_api_off_hands_the_transport_the_plain_dispatcher` and
`test_api_off_never_imports_aiohttp` **pass already**: they pin today's behaviour. Record
that. Then prove they can fail: temporarily wrap the dispatcher in `run()`, watch the
first fail, and revert.

- [ ] **Step 3: Implement**

In `app.py`, import `from ecu_simulator.api.options import ApiOptions, ApiStartupError`.
It is stdlib only, which the Task 1 test enforces. Then:

```python
def _api_server(runtime: Runtime, endpoints: list[EndpointConfig], api: ApiOptions) -> Any:
    try:
        from ecu_simulator.api.server import ApiServer  # the only aiohttp import (decisions/0010 §4.1)
    except ImportError as error:
        raise ApiStartupError("--api needs the optional [gui] extra: pip install 'ecu-simulator[gui]'") from error
    return ApiServer(runtime, endpoints, api)
```

In `run`, add the keyword `api: ApiOptions | None = None`. After `check_routes(...)`:

```python
    server = _api_server(runtime, endpoints, api) if api is not None else None
    handler = server.handler if server is not None else runtime.dispatcher
    if server is not None:
        await server.start()           # a busy port fails here, before the transport exists
    transport = transport_factory(config.interface, endpoints)
```

Make these changes to the rest of `run`:
- call `await transport.start(handler)` instead of `runtime.dispatcher`;
- in `finally`, call `await server.stop()` before `await transport.stop()`, guarded by
  `if server is not None:`.

Moving `transport_factory(...)` after the API start keeps
`test_run_rejects_inconsistent_routes_before_opening_sockets` true, because the routes are
still checked first. Import `Any` if it is missing.

`main` becomes:

```python
def main(config: RuntimeConfig, api: ApiOptions | None = None) -> int:
    """Run to completion; exit status 0 on clean shutdown, 2 on a transport/startup failure."""
    try:
        asyncio.run(run(config, api=api))
    except (TransportError, ApiStartupError) as error:
        logger.error("%s", error)
        return 2
    return 0
```

In `cli.py`:
- add `import importlib.util`, and `from ecu_simulator.api.options import ApiStartupError, parse_api`;
- add the argument:

  ```python
      parser.add_argument(
          "--api",
          metavar="HOST:PORT",
          help="serve the read-only observer API and page on a loopback address, e.g. 127.0.0.1:8765 "
          "(needs the [gui] extra; off by default)",
      )
  ```
- in `main`, before `config = ...`:

  ```python
      api = None
      if args.api is not None:
          try:
              api = parse_api(args.api, str(path), package_version())
          except ApiStartupError as error:
              log.error("%s", error)
              return 2
          if importlib.util.find_spec("aiohttp") is None:
              log.error("--api needs the optional [gui] extra: pip install 'ecu-simulator[gui]'")
              return 2
  ```
- and call `app.main(config, api)` instead of `app.main(config)`.

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit -q -p no:cacheprovider`
Expected: all pass, with the existing `test_app.py` and `test_cli.py` unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/ecu_simulator/app.py src/ecu_simulator/cli.py tests/unit/test_api_wiring.py tests/unit/api/test_run_with_api.py
git commit -m "feat(app): --api wiring; API-off hands the transport the plain Dispatcher and imports no aiohttp"
```

---

### Task 7: CI: `gui` push branch, the `[gui]` job, explicit skip reasons

**Files:**
- Modify: `.github/workflows/ci.yml` (on `gui` only)

- [ ] **Step 1: Edit `on.push.branches`**

```yaml
on:
  push:
    # gui is the GUI track (decisions/0010). It is listed here on branch gui only; the
    # modernization workflow and the V1.0 / Phase 8b gates are unchanged.
    branches: [master, modernization, gui]
  pull_request:
```

- [ ] **Step 2: Report skip reasons in the `test` job.** Replace the notice line in the
  `pytest` step with:

```yaml
          skips=$(grep -oE 'SKIPPED \[[0-9]+\] [^:]+:[0-9]+: .*' pytest.log | sed -E 's/^SKIPPED \[([0-9]+)\] [^:]+:[0-9]+: /\1x /' | sort | uniq | paste -sd ';' - || true)
          echo "::notice title=pytest (Python ${{ matrix.python-version }})::${summary} | coverage ${total}${skips:+ | skips: $skips}"
```

  `addopts = "-ra"` already prints `SKIPPED` lines. On hosted runners the vcan tests then
  report "kernel cannot create CAN_ISOTP sockets (CONFIG_CAN_ISOTP not built, …)"
  **explicitly**, and the aiohttp tests report "needs the optional [gui] extra".

- [ ] **Step 3: Add the `api` job**

```yaml
  api:
    name: API tests with the [gui] extra (Python 3.12)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: python -m pip install --upgrade pip
      - run: python -m pip install -e ".[dev,gui]"
      - name: pytest with the [gui] extra
        run: |
          set -o pipefail
          pytest --color=no -p no:cacheprovider | tee pytest-gui.log
          summary=$(grep -E 'passed|failed|error' pytest-gui.log | tail -1 | sed -E 's/^=+ *//; s/ *=+$//')
          skips=$(grep -oE 'SKIPPED \[[0-9]+\] [^:]+:[0-9]+: .*' pytest-gui.log | sed -E 's/^SKIPPED \[([0-9]+)\] [^:]+:[0-9]+: /\1x /' | sort | uniq | paste -sd ';' - || true)
          echo "::notice title=pytest [gui] (Python 3.12)::${summary}${skips:+ | skips: $skips}"
```

- [ ] **Step 4: Make the `can-capabilities` integration step install `[gui]`**, so that
  `test_api_vcan.py` skips there with the CAN_ISOTP reason too:
  `python -m pip install -q -e ".[dev,hardware,gui]"`.

- [ ] **Step 5: Check the YAML locally**

Run: `.venv/bin/python -c "from ruamel.yaml import YAML; d=YAML(typ='safe').load(open('.github/workflows/ci.yml')); print(list(d['on']['push']['branches']), sorted(d['jobs']))"`
Expected: `['master', 'modernization', 'gui'] ['api', 'can-capabilities', 'lint', 'test']`.
ruamel.yaml is already a runtime dependency, and YAML 1.2 keeps `on` as a string key.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run on gui pushes, add the [gui] API job, and name every skip reason in annotations"
```

This produces a CI result only when the owner asks for a push. After a push, check the run
through the unauthenticated API with the **full 40-character SHA** in `head_sha=`, poll at
120 s or slower, and check the returned `head_sha`. Report every job's summary and skip
annotation verbatim. Report the vcan tests as **skipped with the CAN_ISOTP reason, never as
validated**.

---

### Task 8: vcan integration: a real ISO-TP request becomes a WebSocket `exchange`

**Files:**
- Modify: `scripts/run_integration_tests.sh`, `tests/integration/conftest.py`
- Create: `tests/integration/test_api_vcan.py`

- [ ] **Step 1: Bring `lo` up in the namespace.** In `scripts/run_integration_tests.sh`,
  make the first line inside the `unshare` command `ip link set lo up`. Then run the
  existing suite to prove nothing changed:

Run: `scripts/run_integration_tests.sh -q -p no:cacheprovider 2>&1 | tail -1`
Expected: `68 passed`.

- [ ] **Step 2: Add `extra_args` to `Simulator`.** In `conftest.py`:
  - add `extra_args: Sequence[str] = ()` to `Simulator.__init__`;
  - store `self.extra_args = tuple(extra_args)`;
  - append it to `command` in `_start`;
  - import `Sequence` from `collections.abc`.

- [ ] **Step 3: Write the test**

`tests/integration/test_api_vcan.py`:

```python
"""decisions/0010 §9.3: a real kernel ISO-TP request produces the matching WebSocket event.

The vcan fixture runs before aiohttp is looked for, so on a hosted runner this skips with
the CAN_ISOTP reason, which is the one that matters (0009)."""

import asyncio
import json
import socket

import pytest

from tests.integration.conftest import FunctionalTester, Simulator


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_an_isotp_request_appears_on_the_websocket(vcan, tmp_path):
    aiohttp = pytest.importorskip("aiohttp", reason="needs the optional [gui] extra (aiohttp)")
    port = free_port()
    sim = Simulator(vcan, str(tmp_path), extra_args=["--api", f"127.0.0.1:{port}"])
    tester = None
    try:
        sim.wait_ready()
        tester = FunctionalTester(vcan, 0x7DF, 0x7E8, 0x7E0)
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(f"http://127.0.0.1:{port}/api/v1/events",
                                          origin=f"http://127.0.0.1:{port}") as ws:
                kinds = [json.loads((await asyncio.wait_for(ws.receive(), 3)).data)["type"] for _ in range(2)]
                assert kinds == ["hello", "state"]
                await asyncio.to_thread(tester.send, b"\x01\x0c")
                reply = await asyncio.to_thread(tester.recv)
                while (event := json.loads((await asyncio.wait_for(ws.receive(), 3)).data))["type"] == "dropped":
                    pass                                        # the 4 Hz one-slot notice may come first
        assert reply[:2] == b"\x41\x0c"
        assert (event["type"], event["request"], event["response"]) == ("exchange", "010c", reply.hex())
        assert (event["rx_id"], event["tx_id"], event["functional"], event["outcome"]) == ("0x7df", "0x7e8", True, "responded")
    finally:
        if tester is not None:
            tester.close()
        sim.terminate()
```

`Simulator.terminate()` exists (`conftest.py`, checked when this plan was written).

- [ ] **Step 4: Run it**

Run: `scripts/run_integration_tests.sh -k api_vcan -v -p no:cacheprovider`
Expected: 1 passed. Then run `scripts/run_integration_tests.sh -q -p no:cacheprovider`.
Expected: 69 passed.

Also run it **without** a vcan interface, to prove the skip reason:
`.venv/bin/python -m pytest tests/integration/test_api_vcan.py -rs -p no:cacheprovider`
must skip with the interface or CAN_ISOTP reason, **not** the aiohttp one.

- [ ] **Step 5: Commit**

```bash
git add scripts/run_integration_tests.sh tests/integration/conftest.py tests/integration/test_api_vcan.py
git commit -m "test(integration): a real ISO-TP request appears as a WebSocket exchange; lo up in the namespace"
```

---

### Task 9: the M2 early check on vcan (0010 §9.2), the record, and stop

**Files:**
- Create: `scripts/run_gui_m2_early_check.sh`, `scripts/gui_m2_early_check.py`, `docs/validation/gui-m2-early-check.md`

**What it is:**
- M4 conditions **1** (API off), **2** (API on, 0 clients) and **4** (API on, 3 reading
  clients and 1 stalled, reconnected when forced off), at **5,000 requests** each, one
  round, on vcan in a private namespace.
- Wire latency is taken from `candump -L` by the `analyze.py` pairing rule: a request frame
  on 0x7DF, to the **first** 0x7E8 frame before the next request.
- **Stop rule, the M4 thresholds applied early:**
  - median ≤ condition 1 + 0.10 ms;
  - p99 ≤ condition 1 + 0.50 ms;
  - lost replies = 0.

  If any is missed, print `STOP` and report before M3.
- For conditions 2 and 4 it also checks P5 at quiesce: (a), the §5.1 identities on every
  retained ledger and on `closed_totals`, and
  `connections_opened = clients + closed_totals.connections + closed_unresolved`. P5 is
  judged at M4; here it is exercised early.
- **What it is not:** it is not the M4 benchmark. It has one round, no condition 3 or 5,
  and no RSS soak.

- [ ] **Step 1: Write `scripts/run_gui_m2_early_check.sh`**

```bash
#!/usr/bin/env bash
# The GUI M2 early check (decisions/0010 §9.2) in a private user+network namespace:
# lo up, a private vcan0, candump, the simulator and the tester. Nothing on the host changes.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
exec unshare -r -n bash -euo pipefail -c '
    ip link set lo up
    ip link add dev vcan0 type vcan
    ip link set up vcan0
    exec "$0" scripts/gui_m2_early_check.py "$@"
' "$PYTHON" "$@"
```

- [ ] **Step 2: Write `scripts/gui_m2_early_check.py`**

```python
#!/usr/bin/env python3
"""GUI M2 early check (decisions/0010 §9.2) -- run through scripts/run_gui_m2_early_check.sh.

On vcan, in a namespace: conditions 1, 2 and 4 at N requests each, one round. Wire latency
comes from candump -L, pairing each 0x7DF request with the first 0x7E8 frame before the
next request. It prints per-condition figures, P5 at quiesce for 2 and 4, and "STOP" if an
M4 latency criterion is already missed. Early, not acceptance: P1-P9 are judged at M4.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import aiohttp
import isotp

IFACE = "vcan0"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
CAN_ISOTP_SF_BROADCAST = 0x0800
MIX = [b"\x01\x0d", b"\x01\x10"]
EVERY_100 = [b"\x01\x00", b"\x01\x20", b"\x01\x40", b"\x09\x02"]
FRAME = re.compile(r"\((\d+\.\d+)\)\s+\S+\s+([0-9A-F]{3})#([0-9A-F]*)")


def requests(n: int) -> list[bytes]:
    out: list[bytes] = []
    while len(out) < n:
        out.extend(EVERY_100 if len(out) % 100 == 0 and out else [MIX[len(out) % 2]])
    return out[:n]


class Tester:
    """The same shape as tests/integration/conftest.py FunctionalTester."""

    def __init__(self) -> None:
        self.tx = isotp.socket()
        self.tx.set_opts(optflag=isotp.socket.flags.TX_PADDING | CAN_ISOTP_SF_BROADCAST, txpad=0)
        self.tx.bind(IFACE, isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=0, txid=0x7DF))
        self.rx = isotp.socket(timeout=1.0)
        self.rx.set_opts(optflag=isotp.socket.flags.TX_PADDING, txpad=0)
        self.rx.bind(IFACE, isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=0x7E8, txid=0x7E0))

    def run(self, reqs: list[bytes]) -> int:
        lost = 0
        for payload in reqs:
            self.tx.send(payload)
            try:
                self.rx.recv()
            except TimeoutError:
                lost += 1
        return lost

    def close(self) -> None:
        self.tx.close()
        self.rx.close()


def start_simulator(api: bool) -> subprocess.Popen[str]:
    """Start it and wait for its own "ready" line; drain stderr so the pipe never fills."""
    cmd = [sys.executable, "-m", "ecu_simulator", "--interface", IFACE, "--log-level", "INFO"]
    if api:
        cmd += ["--api", f"127.0.0.1:{PORT}"]
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, cwd=tempfile.mkdtemp())
    ready = threading.Event()

    def pump() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            if "ecu-simulator ready on" in line:
                ready.set()

    threading.Thread(target=pump, daemon=True).start()
    if not ready.wait(10):
        proc.kill()
        raise RuntimeError("simulator did not become ready within 10 s")
    return proc


def latencies(log: Path) -> tuple[list[float], int]:
    frames = [(float(m[1]), m[2]) for line in log.read_text().splitlines() if (m := FRAME.search(line))]
    samples, lost = [], 0
    starts = [i for i, (_, cid) in enumerate(frames) if cid == "7DF"]
    for k, i in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(frames)
        reply = next((t for t, cid in frames[i + 1:end] if cid == "7E8"), None)
        if reply is None or reply - frames[i][0] > 1.0:
            lost += 1
        else:
            samples.append((reply - frames[i][0]) * 1000)
    return sorted(samples), lost


async def reader(stop: asyncio.Event, seen: list[int]) -> None:
    async with aiohttp.ClientSession() as s, s.ws_connect(f"{BASE}/api/v1/events", origin=BASE) as ws:
        while not stop.is_set():
            try:
                msg = await asyncio.wait_for(ws.receive(), 0.5)
            except TimeoutError:
                continue
            if msg.type != aiohttp.WSMsgType.TEXT:
                return
            event = json.loads(msg.data)
            if event.get("type") == "exchange":
                seen.append(event["seq"])


def stalled_socket() -> socket.socket:
    raw = socket.socket()
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    raw.connect(("127.0.0.1", PORT))
    raw.sendall(
        f"GET /api/v1/events HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\nOrigin: {BASE}\r\nUpgrade: websocket\r\n"
        "Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
        .encode()
    )
    return raw


async def status() -> dict:
    async with aiohttp.ClientSession() as s, s.get(f"{BASE}/api/v1/status") as r:
        return (await r.json())["api"]


async def stalled(stop: asyncio.Event, reconnects: list[int]) -> None:
    raw, forced = stalled_socket(), 0
    while not stop.is_set():
        await asyncio.sleep(0.5)
        now = (await status())["forced_disconnects"]
        if now > forced:                                          # 0010 §9.2 condition 4: reconnect at once
            forced = now
            raw.close()
            raw = stalled_socket()
            reconnects.append(now)
    raw.close()


def p5(api: dict) -> list[str]:
    problems = []
    if api["issued_seq"] != api["published"] + api["handoff_dropped"]:
        problems.append("P5(a) issued != published + handoff_dropped")
    ledgers = api["connections"] + api["closed_connections"]
    for led in ledgers:
        if led["offered"] != led["published_at_close"] - led["published_at_open"] or \
           led["offered"] != led["enqueued"] + led["client_dropped"] or \
           led["enqueued"] != led["sent"] + led["delivery_unknown"] + led["queued"] + led["discarded_on_close"]:
            problems.append(f"P5(b) ledger {led['id']}")
    t = api["closed_totals"]
    if not (t["offered"] == t["published_span"] == t["enqueued"] + t["client_dropped"]
            and t["enqueued"] == t["sent"] + t["delivery_unknown"] + t["discarded_on_close"]):
        problems.append("P5(b) closed_totals")
    if api["connections_opened"] != api["clients"] + t["connections"] + api["closed_unresolved"]:
        problems.append("connections_opened does not reconcile")
    if t["close_codes"].get("1013", 0) != api["forced_disconnects"]:
        problems.append("P5(e) 1013 closes != forced_disconnects")
    if t["close_codes"].get("1011", 0) != api["fanout_failed"] + api["writer_failed"]:
        problems.append("P5(e) 1011 closes != fanout_failed + writer_failed")
    return problems


ALLOWED = ("1013", "1006")   # 0010 P5(h): at most one delivery_unknown per forced or reset connection


def allowed_unknown(api: dict) -> dict[str, int]:
    """The delivery_unknown counts P5(h) allows, reported explicitly with the results."""
    by_code = api["closed_totals"]["delivery_unknown_by_close_code"]
    return {code: by_code.get(code, 0) for code in ALLOWED if by_code.get(code, 0)}


def unresolved(api: dict) -> list[str]:
    """Beyond the P5(h) allowance: the condition is inconclusive, never passed (0010 §9.2 P5(h))."""
    notes = []
    t = api["closed_totals"]
    if t["delivery_unknown_over_allowance"]:
        notes.append(f"{t['delivery_unknown_over_allowance']} closed connection(s) over the P5(h) allowance")
    for code, unknown in t["delivery_unknown_by_close_code"].items():
        if code in ALLOWED and unknown > t["close_codes"].get(code, 0):
            notes.append(f"delivery_unknown {unknown} > {t['close_codes'].get(code, 0)} connections closed {code}")
        elif code not in ALLOWED and unknown:
            notes.append(f"delivery_unknown {unknown} on connections closed {code} (allowance is 0)")
    for led in api["connections"] + api["closed_connections"]:
        limit = 1 if str(led["close_code"]) in ALLOWED else 0
        if led["delivery_unknown"] > limit:
            notes.append(f"connection {led['id']}: delivery_unknown {led['delivery_unknown']} > {limit}")
    if api["closed_unresolved"]:
        notes.append(f"closed_unresolved = {api['closed_unresolved']} at quiesce")
    return notes


async def condition(n: int, api: bool, clients: bool, workdir: Path) -> dict:
    log = workdir / f"candump-{int(api)}{int(clients)}.log"
    dump = subprocess.Popen(["candump", "-L", IFACE], stdout=log.open("w"))
    sim = start_simulator(api)
    stop, tasks, seen, reconnects = asyncio.Event(), [], [[], [], []], []
    if clients:
        tasks = [asyncio.create_task(reader(stop, seen[i])) for i in range(3)]
        tasks.append(asyncio.create_task(stalled(stop, reconnects)))
        await asyncio.sleep(0.5)
    tester = Tester()
    started = time.monotonic()
    tester_lost = await asyncio.to_thread(tester.run, requests(n))
    elapsed = time.monotonic() - started
    tester.close()
    await asyncio.sleep(1.0)                                         # let the publisher drain
    result: dict = {"api": api, "clients": clients, "requests": n, "seconds": round(elapsed, 2),
                    "tester_timeouts": tester_lost}
    if api:
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        for _ in range(40):                                          # quiesce: every close resolved
            state = await status()
            if state["clients"] == 0 and state["closed_unresolved"] == 0:
                break
            await asyncio.sleep(0.25)
        result["p5_problems"] = p5(state)
        result["inconclusive"] = unresolved(state)
        result["delivery_unknown_allowed"] = allowed_unknown(state)   # reported, never silently passed
        result["forced_disconnects"] = state["forced_disconnects"]
        result["connections_opened"] = state["connections_opened"]
        result["reader_seq_ok"] = all(s == sorted(set(s)) for s in seen)
    sim.send_signal(signal.SIGINT)
    sim.wait(10)
    dump.send_signal(signal.SIGINT)
    dump.wait(5)
    samples, lost = latencies(log)
    if not samples:
        raise RuntimeError(f"no request/reply pairs in {log}: the capture or the tester failed")
    result.update(median_ms=round(statistics.median(samples), 3), p99_ms=round(samples[int(len(samples) * 0.99)], 3),
                  lost=lost, paired=len(samples))
    return result


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=5000)
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix="gui-m2-"))
    results = [await condition(args.n, False, False, work), await condition(args.n, True, False, work),
               await condition(args.n, True, True, work)]
    base = results[0]
    stop = inconclusive = False
    for number, r in zip((1, 2, 4), results, strict=True):
        print(f"condition {number}: {json.dumps(r)}")
        if number != 1:
            stop |= r["median_ms"] > base["median_ms"] + 0.10 or r["p99_ms"] > base["p99_ms"] + 0.50
        stop |= r["lost"] > 0 or bool(r.get("p5_problems"))
        inconclusive |= bool(r.get("inconclusive"))
    print(f"captures in {work}")
    if stop:
        print("STOP: report before M3")
        return 1
    if inconclusive:
        print("INCONCLUSIVE: delivery the ledger cannot vouch for; report the counts, not a pass")
        return 2
    print("within the M2 early-check limits")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 3: Run it three times** and keep every output verbatim.

Run: `for i in 1 2 3; do scripts/run_gui_m2_early_check.sh; done`
Expected: each run prints three condition lines and one of: "within the M2 early-check
limits", "STOP" or "INCONCLUSIVE". **On STOP or INCONCLUSIVE, do not tune anything. Report
it with its counts.**

- [ ] **Step 4: Run the gates on the final commit**

```bash
set -o pipefail
.venv/bin/python -m pytest -p no:cacheprovider -rs 2>&1 | tail -3   # [dev,hardware,gui]: 1083 + the M2 tests; 2 xfailed
scripts/run_integration_tests.sh -q -p no:cacheprovider 2>&1 | tail -1   # 69 passed
.venv/bin/ruff check . && .venv/bin/mypy
```

Then two CI-shaped runs, each in a fresh clone inside `unshare -r -n` with `lo` up:
- `.[dev]` only: every `tests/unit/api` module skips with the "[gui] extra" reason;
  `test_aiohttp_is_not_installed_in_this_environment` runs and passes;
- `.[dev,gui]`: the `api` tests run; the "not installed" test skips by name.

Record every skip, grouped by reason, for both.

- [ ] **Step 5: Write `docs/validation/gui-m2-early-check.md`**

Record:
- the host, kernel, CPU governor, Python, aiohttp version and commit;
- the three runs verbatim;
- the gate results, with both CI-shaped skip lists;
- whether any CI result exists. If the owner asked for a push, give the run id, the
  **full** `head_sha`, every job summary and the skip annotations, **and say that the vcan
  and early-check results are local, not CI**.

State plainly: *"An early check on vcan, one round, conditions 1, 2 and 4 only; not the M4
benchmark. P1–P9 are judged at M4."*

- [ ] **Step 6: Commit and stop**

```bash
git add scripts/run_gui_m2_early_check.sh scripts/gui_m2_early_check.py docs/validation/gui-m2-early-check.md
git commit -m "docs(validation): GUI M2 early check on vcan and verification record"
```

Do not push unless asked, and do not start M3.

---

## Revision after the Task 1–6 checkpoint (owner, 2026-09-27)

The owner reviewed `de7e431`, pushed it, and asked for these changes before Tasks 7–9. 0010's
sixth revision carries the spec side. Latency and reply-loss criteria (P1–P4) are unchanged.

**R1. `delivery_unknown` per close code** (`observe/publisher.py`). `_fold` also sums each
closed ledger's `delivery_unknown` into `closed_totals["delivery_unknown_by_close_code"]`, a
map from the close code as a string to the sum. A code is added only when the sum is
non-zero; `stats()` returns a copy.
- Test: a connection closed 1013 with one unknown, and one closed 1000 with none, gives
  `{"1013": 1}`.

**R2. `writer_failed`** (`observe/publisher.py`, `api/server.py`). `Publisher` gains a
`writer_failed: int` counter and `fail_writer(conn)`, which increments it and closes the
connection with 1011 through `disconnect`. `stats()` reports `writer_failed`.
- The server's writer done-callback (`_writer_ended`, from the fix wave) calls
  `publisher.fail_writer(conn)` instead of `disconnect(conn, 1011)`.
- Also, from the fix-wave re-review: return early when the writer's exception is
  `NotDelivered`, explicitly, instead of relying on the transport state.
- Tests: the publisher unit test checks `writer_failed == 1`, `close_codes == {"1011": 1}`
  and `fanout_failed == 0`. The server test
  `test_a_writer_that_dies_on_an_open_socket_closes_it_1011` also asserts
  `writer_failed == 1` and `fanout_failed == 0`, and `test_a_connection_the_publisher_abandons_is_closed_1011`
  asserts `writer_failed == 0`.

**R3. The P5(h) allowance, pinned in tests** (`tests/unit/api`).
- Add a helper to `tests/unit/api/support.py`,
  `check_delivery_unknown_allowance(stats) -> dict[str, int]`. It asserts the P5(h) rule:
  - for each code in 1013 and 1006, `delivery_unknown_by_close_code[code]` is at most
    `close_codes[code]`;
  - every other code has 0;
  - every open and retained ledger is within its limit: 1 if closed 1013 or 1006, else 0;
  - `closed_unresolved` is 0.

  It returns the allowed counts, so tests can **assert them explicitly**.
- Use it in the stalled-client test, asserting `{"1013": 1}`; the backpressured-reset
  test, asserting `{"1006": 1}`; and the live-reset test, asserting `{}`.
- Use it also in the tests of healthy clients: the order test, the 1008 test and the
  shutdown test. Each must assert `{}`: zero unknowns.
- **The strict xfail `test_a_stalled_clients_blocked_send_is_not_delivery_unknown` stays.**
  Its assertion (`== 0`) and `strict=True` are unchanged, so a behaviour change still turns
  it red. Only its reason text is updated to: "stalled-client finding: delivery_unknown=1
  per forced close, within the 0010 P5(h) allowance and reported explicitly; kept strict so
  any change is noticed". It must still be reported as XFAIL, never as a pass.

**R4. A request body stalled mid-chunk gets 408** (`api/server.py`).
- Add `BODY_TIMEOUT_S = 2.0` and an `ApiServer(..., body_timeout_s=BODY_TIMEOUT_S)` keyword.
- In the guard, wrap the chunked read in `asyncio.wait_for(..., body_timeout_s)`. On
  timeout, raise `web.HTTPRequestTimeout(text="request body not received in time (decisions/0010 §4.3)")`.
- Test `test_a_body_stalled_mid_chunk_is_408`:
  - server built with `body_timeout_s=0.2`;
  - a raw request with `Transfer-Encoding: chunked`, `800\r\n`, then 100 bytes, then
    silence;
  - the reply starts with `HTTP/1.1 408` within 2 s;
  - a following normal `GET /api/v1/status` gets 200 (the server is not wedged).
- Also add the deferred test: a bad Host with a chunked body gets 421 without the body
  being read. A body that is never sent must not delay the 421.

**R6. Per-connection allowance counter** (owner, 2026-09-27, after round 3).
- `_fold` increments `closed_totals["delivery_unknown_over_allowance"]` for each closed
  connection whose `delivery_unknown` exceeds its allowance: 1 if its close code is 1013 or
  1006, otherwise 0. The per-code sums alone cannot catch this once ledgers are evicted.
- The test helper `check_delivery_unknown_allowance` also asserts the counter is 0.
- Publisher test: more than 64 closes, where one early connection closed 1013 carries two
  unknowns and one closed 1013 carries none. After the offender is evicted from
  `closed_connections`:
  - the counter is 1;
  - the per-code sum check alone would pass, and the test shows that too;
  - with no offender, the counter is 0.
- The two older 1011 publisher tests (`test_fanout_failure_closes_only_that_connection` and
  `test_abandon_keeps_the_ledger_exact_when_the_clock_also_fails`) assert
  `close_codes["1011"] == fanout_failed + writer_failed` and `writer_failed == 0`.

**R5. Task 9's script** (above) applies the revised P5(e) and P5(h) rules, and prints the
allowed `delivery_unknown` counts per condition. The early check's STOP rule and latency
thresholds are unchanged.

## Self-review

**Spec coverage** (0010, fifth revision):

| Requirement | Task |
|---|---|
| §4.1 `ApiServer` in `api`, aiohttp only there | 1, 4 |
| §4.3 limits: 4 clients / 503, 1013 after overflow, 1 KiB body / 413, 1008, 256 KiB state | 4, 5 |
| §4.5 hello, state, history, live; `after=` and `gap`; initial state primed | 2, 4, 5 |
| §5 routes and `/status`, including `profile` and the cumulative fields | 3, 4 |
| §5.1 ledgers, cumulative totals, writer contract | 2, 3, 5 |
| §6 loopback-only bind, `Host` 421, `Origin` 403, no CORS, GET-only / 405 including HEAD | 1, 4, 5 |
| §9.1 API off: plain `Dispatcher`, no aiohttp import, not installed in `.[dev]` | 1, 6 |
| §9.2 M2 early check (conditions 1, 2, 4 on vcan) and P5 exercised early | 9 |
| §9.3 the `[gui]` CI job, explicit skips, `gui` push branch | 7 |
| §9.3 vcan integration: ISO-TP request to WebSocket event | 8 |
| §7 / §10: frontend files and rendering acceptance in M3 | out of M2 by design; M2 serves one page |

**Out of M2 by design:** the frontend views and their file tests (M3), uPlot (M3b), the
full benchmark and the RSS soak (M4), and the M4 harness JSONL (specified in 0010 §9.2,
built in M4).

**Placeholder scan.** Task 4 has a deliberate temporary `_events` returning 501, which
Task 5 replaces; Task 5 Step 2 depends on it. There is no other TBD.

**Type consistency.** These names are used identically across tasks:
- `ApiOptions(host, port, profile, version)`;
- `parse_api(value, profile, version)`;
- `ApiServer(runtime, endpoints, options, *, state_interval_s, connection_options)` with
  `.handler`, `.publisher`, `.port`, `.start()` and `.stop()`;
- `run_writer(conn, send, hello, history)`;
- `Publisher(..., connection_options=)`;
- `stats()` keys `connections_opened`, `closed_totals` and `closed_unresolved`;
- `Connection.mark_sent` / `mark_failed` / `mark_unknown`, and the ledger key `delivery_unknown`;
- `NotDelivered` and `send_via(ws, request)`.

## Investigation after the Task 9 STOP (owner, 2026-09-28)

The early check stopped in all six runs: condition 4's median exceeded condition 1's by
0.107–0.116 ms against +0.10 ms, with the order fixed at 1→2→4, one round each. M3 stays
paused. These two tasks find out whether the miss follows client load or run order, and
prepare, without deciding, the 0010 question of how M4 proves a forced 1013 close.
**Unchanged:** the request mix, 5,000 requests per condition, the median (+0.10 ms) and p99
(+0.50 ms) limits, lost replies = 0, the P5 checks. Nothing in `src/` changes. The CPU
governor stays as it is (`powersave`); it is recorded, never changed.

### Task 10: harness hardening (test-first)

**Files:** Modify `scripts/gui_m2_early_check.py`. Create `tests/unit/test_gui_m2_early_check.py`.

1. **Cleanup on every path.** `condition()` must stop the simulator, candump, the reader
   and stalled-client tasks, the tester's sockets and the stalled raw socket on every exit,
   including an exception at any step (try/finally, or `contextlib.ExitStack` /
   `AsyncExitStack`). No process started by a condition outlives it.
2. **A refused stalled-client connect or reconnect fails the run.**
   - The raw stalled socket must read the HTTP upgrade response and require
     `HTTP/1.1 101`; anything else (a 503, a close, no response within a deadline) is a
     failure carrying the status line.
   - If the new connection id never appears in `/status`, that is a failure too
     (`stalled_ids` empty or short of the connects made).
   - Failure means: the condition's `p5_problems` gets a line naming it, so `main()` prints
     `STOP` (exit 1), or the condition raises and the run ends non-zero. Never silent.
3. **Rotated rounds.** `--rounds R` (default 1). Round r (0-based) runs the conditions in
   rotation r of `(1, 2, 4)`: round 0 = 1,2,4; round 1 = 2,4,1; round 2 = 4,1,2; then
   repeat. `--order` may instead give an explicit order such as `2,4,1`. Each round is
   judged on its own condition 1: the same stop rules as today, per round. The output
   prints every condition of every round as one JSON line, with its round and position.
4. **Recorded per condition:**
   - `rate_rps` = requests / elapsed seconds of the tester loop;
   - wire latency as today (median, p99, paired, lost), from candump;
   - dispatch latency from the events' `dispatch_us`: for condition 4, every exchange a
     reader received (reader 0 is enough); for conditions 2 and 4, the final
     `GET /api/v1/exchanges?limit=500` after the tester finishes (the history keeps 500),
     labelled as the last 500. Report median and p99 in ms. Condition 1 has no API: none.
     Do not add a client or a poll to condition 2; that would change the condition.
   - once per run: the CPU governor of every CPU (from
     `/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`, "unknown" if unreadable),
     `nproc`, and the kernel release.
5. **A summary** after all rounds: for each condition, its median in each position
   (first, second, third) and the excess over that round's condition 1, so load and order
   can be read off directly.
6. **Unit tests without CAN or a simulator**: extract pure helpers and test them:
   the rotation, the candump pairing (`latencies`, from a small synthetic log), the stop
   judgment per round, the upgrade-response check (101 accepted; 503, empty and garbage
   refused), the dispatch summary, and the cleanup path (a condition whose step raises
   still stops what it started: use fakes or injected starters, not real processes).
   The module imports aiohttp: the test module must skip with a named reason when the
   `[gui]` extra is absent (the `.[dev]` CI job), like the other `[gui]` skips.
7. Gates: the namespace suite, `ruff check .`, `mypy`. One smoke run with `-n 300 --rounds 3`
   through `scripts/run_gui_m2_early_check.sh`, reported verbatim (its verdict is not
   the result: 300 requests is a smoke run).

### Task 11: rotated 5,000-request runs, the record, and the M4 forced-close proposal

**Files:** Modify `docs/validation/gui-m2-early-check.md`. Create
`docs/validation/gui-m2-early-check-runs/` holding each run's stdout verbatim.

1. Run `scripts/run_gui_m2_early_check.sh --rounds 3` (5,000 requests) **three times**:
   nine rounds, each condition three times in each position. No `src/` edits in the tree
   while it runs. Keep every run's full stdout verbatim.
2. Add a section to the record: environment (governor, CPUs, kernel, commit), a table of
   median/p99 wire latency, dispatch latency and rate per condition per round and
   position, and the verdict of every round, each as printed.
3. **Load or order.** State which the data shows, against these tests:
   - load: condition 4's excess over its round's condition 1 stays above +0.10 ms, or
     near it, in every position;
   - order: the excess tracks position (e.g. the condition run first is slower whatever
     it is).
   If neither, say "undetermined" and why. State the noise (the spread of condition 1)
   beside it. Do not tune thresholds; a STOP stays a STOP.
4. **M4 forced 1013 closes: a proposal for the owner, not a decision.** From the measured
   buffer absorption (about 4,950 messages; a forced close needs about 23,000–26,000
   requests), evaluate each option for whether it keeps condition 4 honest as a latency
   condition, what it proves, what it costs, and what 0010 text changes:
   (a) a smaller receive buffer for the stalled client; (b) a dedicated forced-close
   sub-run outside the timed conditions; (c) driving the stalled client to overflow before
   timing starts; (d) proving the 1013 path by unit and integration tests only, and saying
   so in P5(e). End with a recommendation, marked as awaiting the owner's decision.

## Client-cost diagnostic and the M4 forced-close revision (owner, 2026-09-28)

After the rotated runs (miss follows client load, cause not established), the owner asked
for: a focused rotated diagnostic of the client configurations with full-run dispatch and
wire timing; two harness failures made loud; representative raw captures kept with the
record; and 0010 revised for a per-round forced-close run. M3 stays paused. **Unchanged:**
the request mix, 5,000 requests per condition, the median (+0.10 ms) and p99 (+0.50 ms)
limits against the same round's condition 1, lost = 0, the P5 checks, the governor.
Nothing in `src/` changes.

### Task 12: harness: loud failures, client configurations, full-run dispatch

**Files:** Modify `scripts/gui_m2_early_check.py`, `tests/unit/test_gui_m2_early_check.py`.

1. **`--rounds` below 1 fails**: argparse error (exit 2), never "within the limits".
2. **An early-ending reader fails the run**: a reader whose `ended` is anything but
   `"stopped"`, a reader that was never counted in `/status` by the "readers first" wait
   (the wait must fail, not fall through), or a reader that received fewer exchanges than
   the condition published, puts a line in `p5_problems` (so STOP). `reader_seq_ok` must
   be false for an empty sequence when exchanges were published.
3. **Client configurations.** `--conditions` takes a comma list from: `1` (API off),
   `2` (API on, 0 clients), `r1` (API on, 1 reading client), `r3` (API on, 3 reading
   clients), `4` (API on, 3 reading clients and 1 stalled, as today). Default `1,2,4`
   (today's early check, unchanged). Rotation and `--order` work over whatever set is
   given: round r uses rotation r of the list; `--order` fixes one order for every round
   and must be a permutation of the set. Condition 1 must be in the set: each round is
   judged against its own condition 1, with the unchanged limits, for every other
   condition.
4. **Full-run dispatch, without adding load during requests.** The tester sends its
   requests in 10 equal segments (500 each at 5,000). Between segments it pauses; in every
   API condition the pause fetches `GET /api/v1/exchanges?after=<last seq seen>&limit=500`
   and keeps each exchange's `dispatch_us`. The pause is identical in every condition
   (condition 1 makes the same pause without the fetch, for the same fixed time: choose a
   fixed pause, e.g. 50 ms, longer than the fetch, and record it). Wire timing is not
   taken during pauses (no requests are in flight). The result: every exchange's
   `dispatch_us` in every API condition (`dispatch_all_ms`: n, median, p99), and the
   harness fails the condition if the harvest has a `gap` or misses a seq. Keep
   `dispatch_reader0_ms` for reader conditions as a cross-check. Drop `dispatch_last500_ms`
   or keep it; say which.
5. **Incremental cost** in the summary: per round, the median and p99 differences, wire and
   dispatch, for each step 1→2, 2→r1, r1→r3, r3→4 (as present), then the mean and range
   across rounds, and each condition's excess over its round's condition 1 with the
   verdict.
6. **Captures:** `--captures DIR` writes the candump logs there instead of a temp dir.
7. Unit tests without CAN for each of the above (segmenting, harvest gap/miss detection,
   condition parsing and rotation over 5, the incremental summary, `--rounds 0`,
   early-ending reader verdicts). Gates as before; one smoke run
   `-n 300 --rounds 5 --conditions 1,2,r1,r3,4`, verbatim, not a result.

### Task 13: 0010 seventh revision: the per-round forced-close run

**Files:** Modify `docs/decisions/0010-gui-observer-api.md` (and the M4 wording only where
0010 references it).

- Add a revision note at the top: seventh revision, 2026-09-28, owner decision, citing the
  M2 early-check record.
- §9.2: M4 adds, **in every round**, a separate forced-close run outside the timed
  conditions: API on, 3 reading clients and 1 stalled client that the harness reconnects
  each time it is forced off. It passes only if the server counts **three 1013 closes**
  (`forced_disconnects` rises by at least 3, `close_codes["1013"]` agrees) **and** three
  reconnections are accepted (HTTP 101, new connection id in `/status`), all **within
  60 s** of its start. It is checked against **P5** (all identities at quiesce, including
  P5(e) and P5(h)) and **P9** (loop hold time). Its latency is **reported separately and
  is not judged against P1/P2**.
- Correct the condition 4 row: at 20,000 requests the stalled client is expected to
  overflow (after about 6,000 messages at the M2 rates) but not to be forced off; the
  forced path is covered by the forced-close run and by the unit and loopback tests named
  in P5(e).
- Keep every other criterion and threshold unchanged. Quote nothing from the record as a
  result that M4 has not produced.

### Task 14: diagnostic runs, the record, and the captures

**Files:** Modify `docs/validation/gui-m2-early-check.md`; create
`docs/validation/gui-m2-early-check-runs/diag-run-{1,2,3}.txt` and
`docs/validation/gui-m2-early-check-captures/` (xz-compressed candump logs with sha256).

1. Run `scripts/run_gui_m2_early_check.sh --rounds 5 --conditions 1,2,r1,r3,4 --captures
   <dir>` three times (15 rounds: each configuration three times in each position), from a
   clean tree, no other load started by this session. Keep every stdout verbatim.
2. Record: environment, per-condition per-round table (wire median/p99, full-run dispatch
   median/p99, rate, verdict), the incremental cost of each client configuration (mean and
   range, wire and dispatch), and what the data says about where condition 4's excess comes
   from. The cause stays a hypothesis unless the data isolates it; the STOP stays a STOP.
3. Captures: keep, xz-compressed, one complete diagnostic run's candump logs and one
   complete run of the earlier rotated investigation (still in `/tmp`, sha256 recorded),
   with a sha256 of each compressed and uncompressed file, and a note of what each is.

## Owner decisions after the diagnostic, and the M3a mockup (owner, 2026-09-28)

The owner pushed `6bbad2c..e5f3a8b` and decided the open questions. **The M2 median-latency
STOP stays open and is not accepted.** M3a visual work proceeds as an offline mockup only;
wiring views to live API data waits for the owner's visual feedback.

### Task 15: 0010 eighth revision and record notes

**Files:** Modify `docs/decisions/0010-gui-observer-api.md`, `docs/validation/gui-m2-early-check.md`.

- 0010 status line: make it accurate (M1 and M2 built on `gui`; M2's early check is a
  STOP that remains open and is not accepted; M3 not started beyond an offline mockup;
  M4 not run). Keep "Proposed" unless the owner has accepted the record.
- M4 timed conditions (1–5) run **without segment pauses**; segmented runs (the M2
  diagnostic harness's segment-and-harvest mode) are **diagnostics only** and never judge
  P1–P9. Say how dispatch timing is then taken at M4 without pauses, or that it is
  reported only from readers / the diagnostic, without inventing a mechanism.
- The forced-close run is also judged on: **zero lost diagnostic replies** (the tester's
  requests all answered, as P3 measures lost replies) and **zero drops for the healthy
  readers** (`client_dropped` = 0 and no `discarded_on_close` beyond what P6 allows for
  them), while **reporting** the stalled client's drops (`client_dropped`,
  `discarded_on_close`, `delivery_unknown`) as expected, not judged. Update "No criterion
  other than P5 and P9 judges this run" accordingly; latency stays reported, not judged
  against P1/P2.
- Revision note at the top (eighth revision, 2026-09-28, owner).
- Record: a short "Owner decisions (2026-09-28)" note: the STOP stays open and is not
  accepted; next performance investigation is internal timestamps in the simulator
  (receive, dispatch start/end, reply send), not blocking M3a visual work; M4 timed
  conditions unsegmented; forced-close run criteria as above; P3/P6 question closed.

### Task 16: M3a offline static dashboard mockup

**Files:** Create `docs/mockups/m3a-dashboard/` (not package data: nothing here is served
by `ApiServer`, and nothing in `src/` changes).

- Views from 0010 §7: status bar (connection, interface, uptime, scenario time, drops),
  vehicle signals table, DTC panel, exchange log (filter by ECU, service, outcome; pause;
  clear; gap markers where `seq` jumps).
- Static HTML, CSS and plain JavaScript modules; no build step, no npm, no CDN, no web
  fonts; opens from `file://` offline. Read-only: no control that writes or sends.
- Sample data in the exact §5 shapes, captured from a real simulator in a private
  namespace (`/api/v1/status`, `/vehicle`, `/dtcs`, `/ecus`, `/exchanges` after real
  ISO-TP requests), plus a hand-made gap and non-`responded` outcomes, each marked as
  such. Every view is clearly labelled SAMPLE DATA, not live.
- Screenshots of each view at desktop width and one narrow width, and exact instructions
  to open it locally.

## M3a: the live frontend (owner, 2026-09-28)

The owner wants to review the **live** GUI. The mockup under `docs/mockups/m3a-dashboard/`
is not the frontend and is not shipped; no sample data is used anywhere in M3a. **Open and
unchanged:** the M2 median-latency STOP (not accepted), and every V1.0 / Phase 8b gate.
M3b (sparklines, uPlot) is not in scope. Stop for the owner's review after the live demo.

### Task 17: serve the frontend files, and the M3a page

**Files:** `src/ecu_simulator/api/static/` (replace the placeholder `index.html`; add
`app.css`, `app.js`), `src/ecu_simulator/api/server.py` (serve exactly those files),
`tests/unit/api/test_server_http.py` (or a new frontend-files test module).

- **Serving (0010 §6, §7, §9.3):** each frontend file is served at a fixed route with its
  content type (`text/html`, `text/css`, `text/javascript`), from package data, read once
  at startup; no directory listing, no path parameters, nothing else under the static
  directory is reachable. The guard (Host allowlist, Origin on the upgrade) applies as
  today. Tests: every frontend file is served with its content type and body; a request
  for any other path, including a real file name under `static/` not on the list, is 404;
  `HEAD`/`POST` behave as the existing routes do.
- **The page uses only the running simulator's read-only API:** `GET /api/v1/status`,
  `/vehicle`, `/dtcs`, `/ecus`, `/exchanges`, and `WS /api/v1/events?after=S` (`hello`,
  `exchange`, `state`, `dropped`). Same origin (relative URLs; the WS URL built from
  `location.host`). No other network access, no CDN, no fonts, no build step. It sends
  nothing but GETs and the WS upgrade, and never sends on the socket.
- **Views (0010 §7):** status bar (connection, interface, profile, uptime, scenario time,
  drops: `handoff_dropped`, this client's `client_dropped`, `forced_disconnects`); vehicle
  signals; DTC panel with MIL; exchange log filterable by ECU, service (first request
  byte) and outcome, with pause and clear, gap markers wherever `seq` jumps (§4.5), and
  `dispatch_us` labelled as dispatcher time. The browser does no protocol decoding beyond
  the service byte; `summary` comes from the API.
- **Honest states:** *loading* until the first responses arrive; *empty* when there are
  no exchanges / no DTCs / no scenario (say which, from the data: e.g. `as_of: null`
  means no scenario); *disconnected* when the socket closes or a fetch fails — show when
  it was last live, mark every view's data as stale (not cleared, not presented as
  current), and reconnect with bounded backoff using `after=<last seq>`, marking a gap if
  the history no longer covers it. A refused connection (503 too many clients, 403, 421)
  is shown with its reason, not retried in a tight loop.
- **View-only controls:** filters, pause (holds and counts new rows, shows them on resume)
  and clear change only the browser's view; nothing is sent to the simulator.
- **Status refresh:** `GET /status` at a modest fixed interval (state it; ≥ 1 s), since
  the WS carries no status message; vehicle and DTCs from `state` messages after the
  initial GETs.
- **Rendering safety:** DOM built with `textContent` / `createElement`, never `innerHTML`
  with data. Consider `Content-Security-Policy` and `X-Content-Type-Options: nosniff` on
  the frontend responses; if added, say so and test the headers.
- Gates: the namespace suite, integration via `scripts/run_integration_tests.sh`, ruff,
  mypy. No JavaScript test framework (0010 §7).

### Task 18: the live demo on vcan, screenshots, and startup commands

**Files:** `scripts/run_gui_demo.sh` (namespace demo), `scripts/gui_demo_traffic.py`
(read-only diagnostic traffic: OBD and UDS read requests only), `docs/validation/gui-m3a-live-demo.md`
(commands, what was run, screenshots), `docs/validation/gui-m3a-live-demo/*.png`.

- In a private namespace: vcan0, the simulator with `ice_scenario.yaml` and
  `--api 127.0.0.1:8765`, the traffic generator, and headless Chrome taking screenshots of
  the live page: loading (if capturable), live with exchanges and a moving scenario,
  a filter and a paused view, the disconnected state after the simulator is stopped, and
  one narrow width. Nothing on the host's `vcan0` or `can0`.
- Owner startup commands for the owner's own browser (host network, owner's choice of
  interface), with the host-`vcan0` caveat stated.

## M3a after the owner's first live review (owner, 2026-09-28)

The live page works on real data. The owner asked for three fixes, then live desktop and
narrow screenshots, the relevant tests, a push of the M3a commits and the hosted CI
result. **Unchanged:** all data live, the UI read-only, the M2 latency STOP open.

### Task 19: correct summaries for the supported-PID range requests

**Files:** `src/ecu_simulator/observe/events.py`, its unit tests.

- `summarise` calls `01 00` and `01 20` "unknown parameter" because it looks them up in
  `MODE01_PIDS`, which holds no range identifiers, although the simulator answers them
  (`protocols/obd/masks.py`). Use `masks.is_range_request` so a range request reads, e.g.,
  `OBD 01 00 — supported PIDs 01–20` and `OBD 01 20 — supported PIDs 21–40` (every base
  0x00…0xE0), while a real unknown PID still reads "unknown parameter". Summaries stay in
  Python (0010 §5); the browser does not decode.
- Focused tests first: 00, 20, 40, E0, a known PID, an unknown non-range PID, and the
  multi-PID form unchanged.

### Task 20: desktop readability, panel balance, and polled-status labelling

**Files:** `src/ecu_simulator/api/static/{index.html,app.css,app.js}`.

- Larger text and controls at desktop widths (the owner's 2000 px screenshot reads too
  small): body, table and control sizes, and hit targets.
- Rebalance the side panels against the log: the signal and trouble-code panels are too
  narrow at wide viewports; give them more width and let the log keep the rest.
- Label the status bar's polled counters (seq issued/oldest, clients, drops, uptime,
  scenario time) as refreshed every 2 s by `GET /status`, which may briefly trail the
  live exchange log's last seq; say so where the numbers are shown, not only in the
  footer.
- Still live-only, read-only, no API change. Frontend-file tests stay green.
- Live desktop (about 1440 and about 2000 px) and narrow (390 px) screenshots from a real
  simulator run in a namespace, replacing the M3a demo set where they differ.

## M3a follow-up after the owner's second review (owner, 2026-09-29)

Hosted CI at `1d9bb32` noted. **Explicitly open:** the 68 hosted CAN_ISOTP skips (vcan
never validated on hosted runners) and the M2 median-latency STOP.

### Task 21: log view fixes

**Files:** `src/ecu_simulator/api/static/{app.css,app.js}`.

- Keep the accessible Pend./Conf./Lamp labels (short visible headers, full words for
  screen readers and on hover).
- "N new rows below" counts **all** rows below the viewport (of the rows the current
  filters show), not only rows that arrived after the reader scrolled up.
- The jump control never covers log data: it sits outside the scrolling rows (for
  example in the log header or a reserved strip), never overlaid on a row.
- Verify live, in a namespace, at 1440, 2000 and 390 px; frontend-file tests stay green.

Scenario analysis (ice_scenario.yaml, ScenarioRunner, OBD mapping) is a report only; no
new scenario, DBC feature or decision record 0011 until the owner decides.

## Near-term moving-vehicle demo and unavailable metadata (owner, 2026-09-29)

Approved from `docs/plans/moving-vehicle-demo-design.md`: the stepped stopgap at location L3
and the additive "unavailable" metadata. **Unchanged:** the scenario engine and the V1.0
state model (`vehicle/state.py`, `scenario/`, `config/`). **Deferred:** the timeline
interpolate/repeat extension, the distance generator, PIDs 0xA6 and 0x31. **Open:** the M2
latency STOP and the hosted CAN_ISOTP gap. The nonfinite-profile crash is a separate
modernization defect (DEV-26), recorded on its own branch, never mixed into this work.

### Task 22: the stepped 90-second demo profile

**Files:** create `docs/examples/ice_drive_cycle_stepped.yaml` and a test module under
`tests/unit/` that loads it by path.

- Exactly the stepped profile of the design's §4: `interval: 1`, 90 values each for
  speed, rpm, throttle and load; coolant the non-repeating `ramp`; no odometer; loaded with
  `--profile docs/examples/ice_drive_cycle_stepped.yaml`.
- Tests: it validates through the real loader; **every stepped list has the same length
  (90) and the same interval**; key OBD replies (`01 0C 0D 11 04 05`) at the design's
  representative whole-second times equal the design's bytes; the loop boundary (just
  before, at, and just after 90 s and 180 s) gives the design's bytes; coolant in the
  second cycle is higher than at the same cycle time in the first.

### Task 23: additive "unavailable" metadata, and "—" in the GUI

**Files:** `src/ecu_simulator/observe/snapshots.py` (or a new `observe/` helper),
`src/ecu_simulator/api/static/app.js`/`app.css`, `docs/decisions/0010-gui-observer-api.md`
(ninth revision: §5 `GET /vehicle` and WS `state` gain `unavailable`), tests under
`tests/unit/observe` and `tests/unit/api`.

- `GET /vehicle` and every WS `state` message gain `unavailable`: a sorted list of dotted
  signal paths that have **no source** in the loaded profile — not settable by the profile
  schema and not driven by its scenario — computed once at startup from the profile, the
  schema and the scenario. With the shipped profiles and the stepped demo it is
  `["vehicle.odometer"]`. Every other field is unchanged; `signals` still carries the
  stored value (additive, no V1.0 change).
- The GUI shows a listed signal's value as "—" with an "unavailable, no source" label (text,
  not colour alone, and in the stale state too).
- Tests: the list for `ice_default`, `ice_scenario` and the stepped demo; a scenario that
  drives a signal removes it from the list; `/vehicle` and a WS `state` message both carry
  it; the existing API shape tests still pass apart from the added key.

### Task 24: live demo of the moving vehicle on vcan

In a namespace, the simulator with `--profile docs/examples/ice_drive_cycle_stepped.yaml
--api 127.0.0.1:8765` and the traffic generator; live screenshots at 1440, 2000 and 390 px
showing the vehicle moving across a loop boundary and the odometer as "—"; recorded in
`docs/validation/gui-m3a-live-demo.md`.

## M3b checkpoint 1: observer JSON and health safeguards (owner, 2026-09-30)

Authority: `docs/plans/gui-m3b-graphs-design.md` at `c63a9c4` (§4.1, §8.1-8.3, §12.1, §14.1,
§16, §17 checkpoint 1) and decision 0010's tenth to twelfth revisions (§4.3, §5, §9.3). The
owner accepted C9 (an exhausted recovery episode needs "Retry now"; ordinary disconnects keep
the automatic reconnect); that is page behaviour, built at checkpoint 2. Checkpoint 1 is
server code and Python tests only: nothing under `api/static/`, no uPlot, no
`scripts/gui_fault_server.py`. Faults are injected in-process by the tests (`VehicleState.set`,
monkeypatching `snapshots` functions, a raising snapshot callable); no profile file carries a
non-finite value, and DEV-26 is not touched. The diagnostic path is not touched.

Global constraints: `.venv/bin/python` (3.12); `ruff check .` (never `ruff format`), bare
`mypy`, line length 120; tests only inside `unshare -r -n`; never touch the host vcan0/can0;
no Co-Authored-By trailer, no amend, no force, no push. **Sanitising never mutates runtime
state**: it builds new containers and never writes to the `VehicleState`, the runner or the
DTC stores.

### Task 26: `nonfinite`, sanitising, and the strict state encoder

**Files:** `src/ecu_simulator/observe/snapshots.py`; `tests/unit/observe/test_snapshots.py`;
`tests/unit/api/test_server_http.py`, `tests/unit/api/test_server_ws.py`.

- `snapshots.vehicle(runtime, unavailable)`: in `signals`, a value that is a Python `float`
  and not `math.isfinite` becomes `None`; every other value is unchanged. A new key
  `nonfinite`: the sorted list of those paths **not** in `unavailable`, always present,
  possibly empty. A path in `unavailable` whose value is non-finite is sent as `None` and
  listed only in `unavailable` (§8.2 precedence).
- `state_message` encodes with `json.dumps(..., separators=(",", ":"), allow_nan=False)`.
- Tests (§12.1 "The non-finite rule"): `nan`, `inf`, `-inf` set with `VehicleState.set`;
  finite floats, ints, bools and strings unchanged; `nonfinite == []` with none; precedence
  (the path passed in `unavailable` and set non-finite); `state_message` parses under
  `json.loads(..., parse_constant=<raises>)`. **Non-mutation:** after `vehicle()` and
  `state_message()`, the runtime still holds the non-finite values (`math.isnan` / `== inf`
  via `VehicleState.get`), every other stored value is unchanged, and a second call gives an
  equal result.
- **The guard, at the encoder** (§12.1, C11): with `snapshots.dtcs` monkeypatched to return a
  well-formed ECU entry with one extra float field `nan`, and separately with the runner's
  last applied time set to `inf` (the test may set the runner's private `_last_applied`),
  `state_message` raises `ValueError` ("Out of range float values are not JSON compliant"),
  and the same payload encodes without error under default `json.dumps` (shown in the test).
- API (happy path; `GET /vehicle` still uses the existing `web.json_response` here):
  `GET /vehicle` with a non-finite signal set in the runtime answers 200 with `null` and
  `nonfinite`; the WS `state` after `hello`, and a later pushed `state`, carry `null` and
  `nonfinite`. `test_server_ws.py:63`'s exact `vehicle` key set gains `nonfinite`.

### Task 27: the state task: containment, encoding health, the first-good publish

**Files:** `src/ecu_simulator/observe/publisher.py`; `tests/unit/observe/test_publisher.py`.

- `Publisher.__init__` gains keyword `wall: Callable[[], float] = time.time` (the wall clock
  for `state_encoding`'s timestamps, beside the existing `monotonic`), and the attributes
  `state_encode_failed = 0`, `vehicle_encode_failed = 0`, `state_encoding = {"ok": True,
  "last_ok_at": None, "last_failed_at": None}`, and a private must-publish flag.
- `run_state` is exactly §8.3's pseudocode: `try: text = snapshot()`; `except Exception`
  → `state_encode_failed += 1`, `ok = False`, `last_failed_at = wall()`,
  `_log_once("state encode", error)`, must-publish set; `else` → `ok = True`,
  `last_ok_at = wall()`, push if must-publish or the text differs from the last pushed
  text, then clear must-publish. `push_dropped()` runs every turn, failed or not; then
  `await asyncio.sleep(interval_s)`. `CancelledError` is not caught. `ok` is only ever the
  stored result of the latest attempt; nothing compares timestamps.
- `push_initial_state(text)`: `push_state(text)`, then `ok = True`, `last_ok_at = wall()`.
  (Task 28's `ApiServer` uses it for the startup push.)
- `vehicle_encode_failure(error)`: `vehicle_encode_failed += 1`,
  `_log_once("vehicle encode", error)`; never touches `state_encoding`.
- `stats()` gains `state_encode_failed`, `vehicle_encode_failed`, and `state_encoding` (a
  copy, so a caller cannot change it).
- Tests (§12.1 "The state task, encoding health and the first-good publish", all bullets):
  raise-once-then-new-text (task alive, counter 1, `ok` false then true, timestamps from an
  injected `wall`, last good state kept on every connection during the failure, new text
  pushed, `push_dropped` ran in the failed turn); same-value recovery (identical text pushed
  again: each connection's one-slot state is set and a registered connection's
  `next_message()` returns it; the following identical attempt pushes nothing); `ok` sequence
  true/false/true under a frozen clock and under a clock going backwards; a connection
  registered during a failure gets the last good text; logging once per exception type
  (`caplog`), a second type logs again; `CancelledError` ends the task. **The guard, through
  `run_state`** (§12.1, C11): with a real `snapshots.state_message` snapshot over a runtime
  whose DTC part (monkeypatched) or `as_of` carries a non-finite float, the failure is
  contained (counter, `ok` false, task running, no push, pending state = last good text);
  after the injected value is removed, the next attempt succeeds, `ok` true, and the
  first-good text is pushed.

### Task 28: strict `GET /vehicle`, the startup refusal, `/status`, and 0010's markers

**Files:** `src/ecu_simulator/api/server.py`, `src/ecu_simulator/observe/snapshots.py` (only
if the startup split needs it); `tests/unit/api/test_server_http.py`,
`tests/unit/api/test_server_ws.py`; `docs/decisions/0010-gui-observer-api.md`,
`docs/plans/gui-m3b-graphs-design.md` (status line only).

- `GET /vehicle`: build `snapshots.vehicle` and encode with `json.dumps(allow_nan=False)`
  (e.g. `web.json_response(..., dumps=functools.partial(json.dumps, allow_nan=False))`). Any
  `Exception` building or encoding → `publisher.vehicle_encode_failure(error)` and HTTP 500
  with the text `vehicle state could not be encoded (decisions/0010 §4.3)`.
- Startup: the size rule keeps its message. **Every other** exception building or encoding
  the initial state (including a `ValueError` from the strict guard) becomes
  `ApiStartupError` whose message names the exception type. The initial push uses
  `publisher.push_initial_state`, so `/status` starts with `ok: true` and `last_ok_at` set.
- `GET /status` `api` carries `state_encode_failed`, `vehicle_encode_failed`, `state_encoding`
  (through `Publisher.stats`).
- Tests (§12.1 "The API", every bullet not done in Task 26): full-state health against
  `GET /vehicle` (DTC part raising while running: `/status` `ok == false`, counter rising;
  `GET /vehicle` 200 and `ok` still false after it); residual `GET /vehicle` failure (500 with
  the text, `vehicle_encode_failed` rises, `state_encoding` unchanged, `GET /status` 200);
  the `/status` fields; startup with a non-finite runtime value (starts, sanitised,
  `ok == true`); startup residual failure (`ApiStartupError` naming the type); same-value
  recovery over the wire with no scenario (`hello`, `state`; the snapshot raises for three
  attempts, then recovers to identical text; the client receives a second, identical
  `state` and no further `state`). Faults through monkeypatching `snapshots` functions in the
  test, never a private server seam.
- 0010 and the design's status line: the checkpoint-1 items (§4.3 rows, §5 fields and the
  non-finite rule, §9.3 rows, the status lines, §10's M3b row) change from "specified, not
  (yet) implemented" to implemented at M3b checkpoint 1; the page items stay designed, not
  implemented. A thirteenth-revision note says so. Nothing else in 0010 changes.

### Task 29: the overhead measurement (§17, 1.5)

**Files:** `scripts/gui_m3b_state_cost.py`; `docs/validation/gui-m3b-overhead.md`.

- `scripts/gui_m3b_state_cost.py`: builds the runtime from `ice_scenario.yaml` (as
  `gui_m1_early_check.py` builds from a profile, interface `vcan0`, no socket opened),
  computes `unavailable`, applies the scenario once so `as_of` is set, warms up, then times
  **10,000** `snapshots.state_message` calls with `perf_counter_ns`, and reports median,
  p99 and max per call in µs, plus separately the build alone (`vehicle` + `dtcs`) and the
  encode alone, so build and encode costs are distinguishable. It prints the path of the
  `ecu_simulator` package it imported. It works unchanged against the code before and
  after checkpoint 1 (same function signatures).
- Runs, same host and same `.venv` Python: **before** = the `c63a9c4` source (exported with
  `git archive` into the scratchpad and put first on `PYTHONPATH`), **after** = the
  checkpoint-1 head; three runs each. The M1 early check (`scripts/gui_m1_early_check.py`)
  before and after, three runs each, reporting `longest_turn_s`.
- The record reports **state build/encode cost and the publisher turn in separate
  sections**, never combined; the host, Python version, commits, commands and the raw
  output; and states that the state task is not a publisher turn. The optional M2 early
  check is not run at checkpoint 1. The M2 latency STOP stays open and is not affected.

## M3b checkpoint 2: graph rendering and the browser checks (owner, 2026-09-30)

Authority: `docs/plans/gui-m3b-graphs-design.md` (§4.3-4.4, §5, §6, §7, §8.4-8.6, §9, §10,
§11, §12.2-12.4, §13, §14.1 C1-C10, §16, §17 checkpoint 2) and decision 0010 (§7, §9.3,
§10). Checkpoint 1 (server JSON and health) is pushed at `71cf40e`. The owner accepted C9:
an exhausted recovery episode needs "Retry now"; ordinary disconnects keep M3a's automatic
reconnect. The shipped page stays live-data-only: no test mode, no fault switch, no sample
data. Tests and checks are Chrome over CDP in a namespace; Firefox is manual only (§13).

Global constraints (as checkpoint 1, plus): no build step, npm or CDN at run time; the CSP
is unchanged; the vendored uPlot files are byte-identical to the npm tarball and pinned by
SHA-256; nothing in `ecu/`, `transport/`, `protocols/`, `scenario/` or the hot path
changes; the fault-injection harness changes nothing under `src/` and uses only public
seams (public module functions and public attributes of the runtime objects); a capture or
demo run never touches the host vcan0/can0 and runs only in a namespace
(`scripts/run_gui_demo.sh`'s guard). M3b is not accepted at the end of checkpoint 2: the
owner's §13 checklist, including the manual Chrome and Firefox CSP checks, decides.

### Task 31: vendor uPlot 1.6.32 and serve it

**Files:** `src/ecu_simulator/api/static/uPlot.iife.min.js`, `uPlot.min.css`,
`uPlot-LICENSE.txt`; `src/ecu_simulator/api/server.py` (`FRONTEND`);
`tests/unit/api/test_frontend_files.py`.

- Download `https://registry.npmjs.org/uplot/-/uplot-1.6.32.tgz`; copy `dist/uPlot.iife.min.js`,
  `dist/uPlot.min.css` and `LICENSE` (as `uPlot-LICENSE.txt`) byte-identical. Their SHA-256
  must equal §5.1's three values; stop if any differs.
- Three `FRONTEND` rows (§10): `/uPlot.iife.min.js` `text/javascript`, `/uPlot.min.css`
  `text/css`, `/uPlot-LICENSE.txt` `text/plain`. Same headers as the other frontend files.
- Tests: the file test's own route list gains the three rows (body, type, charset, headers,
  "exactly the served files", read once); a new test pins the three SHA-256 values against
  the files on disk; the existing ban on `http://`, `https://`, `//cdn` and "sample" keeps
  applying to `index.html`, `app.js` and `app.css` only (the vendored header has a URL).
- §12.4 by hand: build a wheel (`.venv/bin/python -m pip wheel --no-deps -w <scratch> .`) and
  list it; record in the report that the three files are under `ecu_simulator/api/static/`.
- No page change in this task (Task 33 links the files).

### Task 32: page health: connection, data validity, recovery requirement and episodes

**Files:** `src/ecu_simulator/api/static/app.js`, `app.css`, `index.html` (only the
"Retry now" control and the banner/tag text hooks, if needed).

Implements §8.4-8.6 exactly, with no graphs yet:
- `S.phase` becomes `S.conn` (`loading`, `live`, `down`, `refused`), same meaning; `isLive()`
  and `poll()` test `S.conn` only, so a page whose data is last known keeps polling (C8).
- `S.data` (`current` / `last-known`) with a reason (`connecting`, `malformed`, `encoding`,
  or both malformed and encoding) and the time of the last applied valid `state`; the
  single recovery requirement with its kinds and clearing rules (§8.4 table); "valid state"
  as §8.4 defines it (parsed, type, objects, current socket `S.gen`, current run).
- Malformed frames (fail `JSON.parse` or not an object) are counted, never swallowed:
  "Malformed messages N, last HH:MM:SS UTC" once N > 0; `state_encode_failed` shown once > 0.
- Recovery episodes (§8.5): the resync is a reconnect; one budget of 3 attempts per episode
  (waits 1, 2, 4 s); every attempt outcome listed in §8.5 counts; reset only by a successful
  recovery; exhaustion → "Could not recover: … Retry now", no automatic attempt; "Retry now"
  starts a new episode; a new `started_at` ends the episode (M3a restart path). Outside an
  episode, M3a's backoff is unchanged. One retry timer (`S.retryTimer`), never overlapping;
  at most one attempt in flight.
- Marking: stale (unchanged M3a styling, takes precedence) vs last known (dotted top edge
  and "Last known, HH:MM:SS UTC" on the vehicle and DTC panels — and the graphs panel once
  Task 33 adds it — values not faded, the log not marked; lamp amber outline "Connected,
  last known data" / "Connected, waiting for state"; a banner naming the cause for
  `malformed` and `encoding`). "Live" = `S.conn` live and `S.data` current (§8.6).
- Log lines "Resynchronised after an unreadable message" / "Resynchronised after the
  simulator's state recovered".
- The `<body>` diagnostic attributes of §12.2: `data-conn`, `data-data`, `data-reason`,
  `data-health`, `data-episode`, `data-attempts`, `data-timers`, `data-polls`,
  `data-malformed-total`. Written by the page, never read by it.
- Expose a hook Task 33 uses: a function the health code calls when `S.data` enters
  `last-known` (`malformed`/`encoding`) or `S.conn` goes `down`, so rings can set a pending
  break (§6.7, §8.4).
- Check: the existing M3a capture run (`scripts/run_gui_demo.sh`) still passes; plus a
  short CDP smoke in the namespace showing `data-health` = `live` on a healthy page and a
  dispatched `{bad` frame producing `last-known`/`malformed` then recovery. (The full §12.2
  matrix is Task 34.)

### Task 33: the graphs section: markup, layout, rings, drawing

**Files:** `index.html`, `app.js`, `app.css`.

Implements §5.2, §5.3, §6, §7, §9 and the graph half of §8.4:
- Markup and layout (§5.2, §7): `uPlot.min.css` linked before `app.css`; `<script
  src="uPlot.iife.min.js" defer>` before `app.js`; the collapsible "Signal graphs" section
  above the log in the main column, open on every load (not saved), toggle button with
  `aria-expanded`/`aria-controls`, closed head "Signal graphs — hidden, still recording";
  wide shell second column as a flex column (graphs `flex: none`, log `flex: 1 1 auto;
  min-height: 0`); five `<figure>` cards in `repeat(auto-fit, minmax(11rem, 1fr))`; plot
  height `clamp(3rem, 9vh, 4.75rem)`; compact head buttons; 390 px two-row head, one card
  per row; the footer links `uPlot-LICENSE.txt`.
- Signals, units and scales exactly §5.3; `UNITS` gains `"engine.rpm": "rpm"`.
- Rings exactly §6.3 (Float64Array(4096) ×2 per signal, store on change or around a gap,
  equal `as_of` replaces, 600 s horizon keeping the newest older point, 4096 cap).
- Time base `as_of` (§6.1); step-hold with `paths.stepped({align: 1})` and the draw-time
  right-edge point (§6.2); gaps as `null` with `spanGaps: false`, never a line to or from
  an invalid value (§8.4); windows 30 s / 2 min (default) / 10 min with `aria-pressed`,
  saved under `ecu-simulator.graphs.window` with every access in try/catch (§6.4); the
  missing-signal and missing-time cases (§6.5); loop boundaries need nothing special
  (§6.6); disconnect breaks and "No data from t = A to t = B s (disconnected)" (§6.7);
  restart clears with the note (§6.8); pause buffers and resume jumps to latest, separate
  from the log's pause (§6.9); hide stops drawing only.
- Drawing coalesced with `requestAnimationFrame`; ResizeObserver on the card grid calls
  `setSize` with the floored card content width (§7); axis font from computed style;
  cursor, legend and selection off; nothing animated; plot containers `aria-hidden`.
- Fallback: `typeof uPlot !== "function"` → "Graphs unavailable: the chart library did
  not load", the rest of the page works (§10).
- The per-graph `data-*` attributes of §12.2, exactly as listed, written after each ring
  update and draw, never read by the page.
- Check: the M3a capture run still passes; a CDP smoke on the stepped demo shows five
  cards drawing, line 1 equal to the signal table, no horizontal overflow at 1440, 2000
  and 390, and at least 5 full log rows at 1440 × 900 with the section open (if not, reduce
  the plot height first, §5.2).

### Task 34: the fault-injection server and the capture-run checks

**Files:** `scripts/gui_fault_server.py`; `scripts/gui_demo_capture.py` (new M3b checks,
the WebSocket wrapper); `scripts/run_gui_demo.sh` (an M3b mode if needed).

- `scripts/gui_fault_server.py` exactly §12.3, using public seams only: it wraps the
  public `app.build_runtime` to capture the runtime and replace `runtime.runner.apply` on
  that instance (`--nonfinite PATH:START:END`; with no scenario, set once on a timer), and
  replaces the public `snapshots.state_message` / `snapshots.dtcs` / `snapshots.vehicle`
  module functions during the window (`--state-fault START:END[:vehicle|dtcs]`), returning
  the real result otherwise. It runs the real `app.run(..., api=...)` in-process on vcan
  in the namespace, from a shipped profile or the stepped demo. No traffic by default.
- The WebSocket wrapper (§12.2) installed with `Page.addScriptToEvaluateOnNewDocument`.
- Every §12.2 case, reading only visible text, the tags/banner and the `data-*`
  attributes, with its pass rule as written; the overflow check gains `#graphs` and each
  `.uplot` at 1440, 390 and 2000; the 1440 × 900 log-rows check; the agreement check on
  every live screenshot. The `--long` bounded-history case is included (about 11 min).
- The cost measurement of §11.3 (heap samples, a DevTools trace, `Performance.getMetrics`
  `TaskDuration`, canvas sizes) at 1440 × 900 and 390 × 844 over 60 s with traffic, saved
  with the run. Recorded, not judged.
- Each case reports pass/fail; a failing case is a finding to fix in the page (Task 32/33
  code) within this task's fix loop, not a reason to relax its pass rule. A pass rule that
  proves contradictory is reported, not changed.

### Task 35: the M3b live-demo record and 0010

**Files:** `docs/validation/gui-m3b-live-demo.md` (and its screenshots directory);
`docs/decisions/0010-gui-observer-api.md` (status lines, §7, §9.3, §10 M3b row);
`docs/plans/gui-m3b-graphs-design.md` (status line only).

- The record: commands; host, Chrome version; every §12.2 case with its result; the §11.3
  measurements next to §11's estimates (estimates stay labelled as such where not
  measured); screenshots at 1440 × 900, 2000 and 390 (normal, hidden, paused, stale, last
  known, invalid value, restart note, no scenario); the §13 checklist copied with every box
  **unticked** for the owner, and the Firefox and Chrome CSP items marked unverified.
- 0010 fourteenth revision: checkpoint 2 implemented; M3b **not accepted** until the owner's
  §13 checklist, including both CSP checks, passes. The M2 latency STOP, the hosted
  CAN_ISOTP gap, the Phase 8b gate and the V1.0 branch rule stay open.

### Task 36: remaining checks, durable traces, and the acceptance list (owner, 2026-09-30)

The owner reviewed the desktop layout live and keeps it; graph readouts match the signal
table. **Files:** `scripts/gui_demo_capture.py`, `docs/validation/gui-m3b-live-demo.md` and
its directory; `app.css` only if the new measured case fails and the fix keeps the
desktop layout the owner approved.

- A new measured case: at 1440 × 900 with the restart note **and** a gap note visible
  together, count full log rows in `#logwrap` (requirement: at least 5). Report the number
  and the section height; if it fails, report it as a finding and do not change the layout
  without a ruling.
- Re-run the full `--m3b` matrix and `--m3b-long` on the final page code, so every case is
  on one commit, plus the M3a and `--moving` captures.
- The performance traces of that run (1440 × 900 and 390 × 844) are committed under
  `docs/validation/gui-m3b-live-demo/traces/` with their sizes and the commit they were
  taken on; nothing is left only in a session scratchpad.
- The record: the browser main-thread load (mostly M3a's log rebuild) is an **open
  finding**, not "recorded, not judged"; a section "Remaining acceptance items" lists
  explicitly what is still needed for M3b acceptance; M3b is not marked accepted.

## M3b follow-ups after the owner's review of checkpoint 2 (owner, 2026-10-01)

Each task is a separately reviewable local commit set; nothing is pushed after `0a3f14f`
until the owner asks. No sliders or other features. M3b acceptance, the manual Chrome and
Firefox CSP checks, and every existing gate stay open.

### Task 37: one shared notice line for the restart note and the gap notes

**Files:** `app.js`, `app.css`; `scripts/gui_demo_capture.py` (cases that read the notes).
- The restart / "scenario time went back" note joins the shared gap line, joined by " · ",
  instead of taking its own line. Every notice keeps its complete text; nothing is
  truncated or hidden. At 1440 the common case (restart note plus one gap note) should fit
  one line; where it wraps, it wraps (never ellipsis, never clipped).
- Verify, with both complete notices actually rendered, at 1440 × 900: at least 5 full log
  rows, and report the clearance (rows region minus what 5 rows need). If 5 rows are not
  reached because the line wraps, the next lever is a few px of plot height — report before
  taking it. Rerun the affected desktop and narrow checks: the log-rows cases (steady,
  disconnect, encoding, restart + gap), overflow at 1200, 1440, 2000 and 390, and every case
  that reads the notes.

### Task 38: deterministic 503 followed by automatic recovery

**Files:** `scripts/gui_demo_capture.py`; the record.
- Map each four-client case (variant A, variant B) to the behaviours it proves, in the record.
- A new case, variant C, with no race: the script holds the fourth slot as soon as the page's
  old socket closes; it waits until the server's `refused_clients` rises (a real 503 to the
  page's attempt); it then releases the slot. Pass: the page's next scheduled attempt (within
  the episode's waits) succeeds **without** "Retry now" being pressed; health returns to
  `live`; `data-episode` = `none` and `data-attempts` = 0 (the budget reset); `data-timers`
  never above 1; no two attempts less than 0.9 s apart; `refused_clients` rose by exactly the
  refused attempts. Then a second malformed frame starts a fresh episode at attempt 1 (the
  budget really reset).

### Task 39: the browser main-thread investigation (no product change)

**Files:** a script or capture mode for the comparison runs; `docs/validation/` record of
the investigation, with its traces committed beside it.
- Equivalent runs (same profile, same traffic rate, same retained exchange-row count
  recorded at start and end, 60 s, 1440 × 900 and 390 × 844): log running vs log paused
  ("Pause view"); graphs shown vs hidden.
- Reconcile the arithmetic from the traces: actual rebuild counts and summed durations of the
  log render; JavaScript time separated from style/layout/paint/composite and other work;
  nested trace events not double-counted (self time or top-level task time only); totals
  that add up to the reported busy time, with the remainder named.
- A proposal: the smallest justified fix, with its expected effect from the numbers. It is
  **not** implemented in this task. The M2 latency STOP is separate and unaffected.

### Task 40: an isolated interactive launcher for the owner's fault checks

**Files:** `scripts/gui_fault_session.sh` (or similar); instructions in the record.
- Starts a private user+network namespace with its own lo and vcan0, the fault server with
  the chosen fault (`--nonfinite …` or `--state-fault …`) and profile, and a **visible**
  Chrome on the page, on the owner's X display. The host simulator, its port and the host
  vcan0 are never touched.
- Chrome uses a fresh, separate, launcher-created profile directory. The browser sandbox is
  kept (no `--no-sandbox`); X access control is kept (no `xhost +`; the owner's
  `XAUTHORITY` is passed through). The launcher verifies and prints that the browser runs in
  the namespace (its network namespace differs from the host's, and the page is served from
  the namespace's own loopback).
- On exit it removes only the processes and files it created (by exact PID and its own
  directories). If desktop access fails from the namespace, it reports the blocker and
  stops; it never weakens the sandbox or X access to work around it.
- The record explains how the owner completes the two fault checks with it; screenshots do
  not complete a behavioural check; the manual CSP checks stay outstanding.

### Task 41: an incomplete `state` is a data fault (owner, 2026-10-01)

**Files:** `app.js`; `scripts/gui_demo_capture.py`; the record.
- Finding (pushed `0a3f14f`, `app.js:391-394`): `onState()` returns silently when `vehicle` or
  `dtcs` is not an object, unless a recovery attempt is in flight. In a healthy session the
  page stays Live with its previous data.
- First reproduce it through the browser harness (the WebSocket wrapper dispatches a
  `{"type":"state","vehicle":…}` frame missing `dtcs`, and one missing `vehicle`, on a
  healthy page) and record the observed attributes.
- Fix: a recognised `state` message that cannot be applied is a data fault through the
  existing bounded recovery mechanism (the same requirement and episode as an unreadable
  frame: last known at once, resync, budget, Retry now on exhaustion). Unknown message
  types stay ignored, for compatibility.
- Regression case: a healthy session receives an incomplete `state`, then the resync's
  socket delivers unchanged valid data; pass when the page went last known at once and
  returned to Live only after the valid `state` on the new socket, with the budget reset.

## M3b main-thread fix: a bounded, navigable exchange log (owner, 2026-10-01)

Authority: the investigation `docs/validation/gui-m3b-main-thread.md` (its proposal, the
numbers, and what it could not establish), and the owner's instruction of 2026-10-01. Browser
only: no API rate, diagnostic-path or M2 latency-criteria change; the page keeps its
2,000-exchange retention. Separately reviewable commits; stop before pushing; no slider
controls; the manual checklist and every existing gate stay open; M3b is not accepted.

**Central design rule.** The log renders a **window** of at most `LOG_WINDOW` (200) *matching*
exchanges, chosen after the filters are applied to all retained exchanges. The window is
**following** (its end is the newest matching exchange, and it slides as exchanges arrive)
**or pinned** (its end is a fixed exchange id). Leaving "follow" by the reader's own input,
or using "Older", pins the window at the newest exchange shown at that moment, so rows never
vanish from under a reader who is inspecting history; exchanges that arrive meanwhile are
counted, not drawn, and "Jump to newest" (which also resumes following) brings them in.
"Newer" and "Older" move a pinned window by `LOG_STEP` (100) matching exchanges; reaching the
newest resumes following. The reader's anchor row (by exchange id) keeps its on-screen offset
when the window shifts.

**Four kinds of absent rows, each worded differently and never confused:**
1. *outside this window*: matching, retained, reachable with Older / Newer / Jump to newest;
2. *hidden by filters*: the existing "N exchanges hidden by filters (not a gap)" lines, plus
   a count for any outside the window;
3. *sequence gap* markers (and connection / restart notes): shown where they fall inside the
   window; those outside it are counted ("2 gap markers and 1 connection note in older
   rows") beside the Older control;
4. *left the page's 2,000-row cap*: the existing trimmed note; unchanged meaning.
Payload expansion (`view.expanded`, by entry id) is kept for rows that leave and re-enter the
window. Held rows (pause) and "Clear view" keep their present meaning.

**Measurement protocol (all later tasks).** Full retained buffer = 2,000 exchanges, filled by
a short burst from the harness traffic script, then measured at the standard rate
(about 3.8 requests/s, stepped demo, 60 s) at 1440 × 900 and 390 × 844. Report: busy %, the
`renderLog` count and summed duration (and p95/max per call), long-task count and maximum,
Event Timing (input delay + processing + presentation) for scripted clicks and scrolls
(Pause, Jump to newest, Older, a filter change), rows rendered and rows retained at start
and end, the actual request rate. Intermediate runs: numbers only, no trace committed. The
committed baseline traces stay as they are; the final after-fix runs commit one trace per
width. One uncommitted baseline run on the current head confirms the committed baselines
are still comparable. The shipped CSS and JS are what is measured; nothing is disabled
only for the benchmark. A diagnostic run (labelled so) may disable one thing to attribute
cost, and is not the reported comparison.

### Task 42: the pure selection logic, with a committed check

**Files:** `app.js` (one marked block, DOM-free), `scripts/gui_log_check.js`,
`tests/unit/api/test_log_selection_js.py`.
- A block between `// ---- log selection (pure, no DOM) ----` and `// ---- end log selection ----`
  in `app.js`, with no DOM or page-state access (everything passed in), implementing: given
  the entry list (exchanges and markers, in id order), a filter predicate, `clearedAfter`,
  `pauseAfter` (or none), a window end (id or "newest") and a size, return the items to
  render (entries and "N hidden by filters" runs inside the window), the first / last shown
  exchange ids, and the counts: matching, older matching, newer matching, hidden by filters
  (total, and outside the window), gap / note markers outside the window (older and newer),
  held, in view. Plus the movement helpers: the end id for Older / Newer by a step, and the
  anchor for a filter change (nearest matching exchange at or before the previous pin).
- `scripts/gui_log_check.js` extracts that block from `app.js` text, evaluates it in a
  `vm` context, and checks: window size and order; filters applied to all retained rows
  before windowing; hidden runs only between shown rows; markers inside the window kept in
  position and markers outside only counted; clear / pause boundaries; a pinned window not
  moving as entries are appended; following sliding; Older / Newer stepping and resuming
  following at the newest; the filter-change anchor; empty and tiny lists; all-hidden; the
  2,000-entry case; the counts adding up (matching = older + shown + newer).
- `test_log_selection_js.py` runs the check with `node` (skipped, with the reason
  "node is not installed", when missing; hosted runners have node).
- No page behaviour changes in this task.

### Task 43: the windowed log: render, navigation, markers, position

**Files:** `app.js`, `app.css`, `index.html`.
- `renderLog` uses the selection block. Controls inside the log table (real buttons,
  keyboard-reachable): an Older row at the top ("N older exchanges match your filters;
  show 100 older", with the marker counts beside it) when there are older matching
  exchanges; a Newer row at the bottom when pinned with newer ones; "Jump to newest" (the
  existing control) resumes following and scrolls to the newest. The follow / pin rule and
  the anchor-row offset of the central design rule. The log's count line reads, for
  example, "200 of 1,873 matching shown (2,000 retained)", and the existing wording for
  hidden-by-filter, gap, trimmed and held stays distinct.
- The existing jump control's "new rows below" count includes newer exchanges outside the
  window, stated as such ("+ M beyond this window"); no information is lost.
- Smoke (throwaway, namespace, a full buffer at 1440 and 390): live following; scroll away
  pins; Older / Newer / Jump; filters; pause / resume; expansion kept; markers; no
  horizontal overflow; the M3a and `--m3b` captures still pass (cases reading log rows
  adapt only where the window changes their meaning, and say so).

### Task 44: render only what changed; paused logs stay frozen

**Files:** `app.js`.
- A render signature (window start / end ids, shown count, filter key, cleared / pause
  boundary, the expansion set, trimmed counters, marker counts). The row list is rebuilt
  only when it changes; a following window with no new matching exchange, a pinned window
  receiving arrivals, and a paused log receiving traffic update only counters (held count,
  newer-beyond-window count, filter counts, count line) and only when their text changes.
  No `replaceChildren` of unchanged rows; no repeated rebuild of the frozen log while paused.
- Measure (numbers only, protocol above) with Tasks 43 + 44 in place. Report whether
  `renderLog` is still a major share of busy time under live following. If it is, report
  and propose an incremental append / trim of the live tail; do not build it.

### Task 45: the continuous animation (conditional on a diagnostic)

**Files:** `app.css` (possibly `app.js`).
- A labelled diagnostic: lamp animation off only, vs on, otherwise identical, at full
  buffer. If the lamp's `beat` (box-shadow, `app.css:119`) is a measurable share, make the
  smallest change that keeps a clear connection status and the signal-change flash: for
  example the same 2 s rhythm on a compositor-only property (opacity / transform of a
  ring element), or a pulse only on change. If it is not a measurable share, change
  nothing and report. `prefers-reduced-motion` behaviour is unchanged.

### Task 46: the browser cases, measurements and the record

**Files:** `scripts/gui_demo_capture.py`, `docs/validation/gui-m3b-main-thread.md`,
`docs/validation/gui-m3b-live-demo.md`, one trace pair under `docs/validation/gui-m3b-perf/`.
- `--m3b` cases at both widths with a full retained buffer: live following (the window
  slides, the DOM row count stays ≤ the window, newest visible); pause / resume (paused:
  no rebuild, held counter rises; resume jumps in); filtering across all retained rows
  (a filter matching only old exchanges still finds them); older-history navigation (Older,
  Newer, position kept, Jump to newest, no row disappears under a pinned reader);
  eviction (past 2,000, the trimmed note and counters, window consistency); reconnect
  markers (a disconnect and a resumed stream produce the markers where they fall, and the
  outside-window counts); payload expansion kept; the four absent-row wordings present.
- The protocol's measurements: an uncommitted baseline on the head before the fix, then
  after-fix runs; intermediate numbers in the record; one after-fix trace per width committed.
  A visible-browser measurement through the launcher mechanism (GPU on) on the owner's
  desktop, short runs, if it works; the owner's own Chrome is the alternative.
- The record: before / after table (busy %, `renderLog` count and sum, p95 / max, long
  tasks, Event Timing, rows rendered / retained, request rate), what was and was not
  measured, the 40 % estimate stated as an earlier estimate and not a target, the open
  items; M3b stays "implemented, not accepted".

## M3b follow-ups after the log-fix review (owner, 2026-10-03)

The owner's authorization: items 1-3 below, the record wording, the owner checklist, and the
publication of the final `gui` head for code review (not M3b acceptance). Out of scope:
artifact-storage migration (LFS), sliders, opendbc, and the separate performance / hardware /
release tracks. The owner approved these behaviours as built: leaving follow pins the window;
reaching the bottom of an older window does not jump to live data; Clear view resumes
following; Older / Newer move by overlapping 100-row steps while preserving the reading
position (the step still shrinks when needed to keep the anchor row in the window, and a press
with the window's far end already on screen scrolls instead of moving; the record says so).
The unidentified full-suite failure is NOT to be chased by repeated runs under load: validation
saves complete output (`-rfE`), and a recurrence is investigated by its identified test.

### Task 47: Older / Newer / Jump to newest together in the log header

**Files:** `index.html`, `app.js`, `app.css`; `scripts/gui_demo_capture.py`.
- The Older and Newer buttons move out of the table's `#log-head` / `#log-tail` rows into the
  log panel's header, beside "Jump to newest" (`#btn-follow`), in the order Older, Newer, Jump
  to newest. The explanatory rows stay in the table as plain text (the "outside this window"
  wording and the marker counts, now pointing at the header controls, no buttons), so there is
  one tab stop per direction. Visible text "Older" / "Newer" / "Jump to newest"; each
  accessible name starts with its visible text; no numbers in labels.
- An unavailable direction (nothing older / newer) is `aria-disabled="true"`, never
  `disabled`, so a pressed button that becomes unavailable keeps focus; pressing it does
  nothing.
- Focus is predictable: after Older or Newer the focus stays on the pressed header button (the
  header never scrolls away), however many times it is pressed; the earlier "focus goes to the
  log box" rule is removed. After "Jump to newest", which hides itself when the log is
  following again, focus moves to the log box (`#logwrap`), because the control it was on has
  gone. Focus is never left on `body` by these actions.
- The anchor-only step rule, the step-0 scroll, the full-window re-pin and everything else in
  Task 43's behaviour are unchanged.
- Vertical budget: at least 5 full log rows at 1440 × 900 in the steady, pinned (with the
  "beyond this view" count), disconnect-gap, encoding-gap and the worst wrapped
  three-notice states, with the header measured in each. If the header wraps at 1440, shorten
  the count line, not the plots. Narrower widths may wrap; no horizontal overflow at 1200,
  1440, 2000 and 390.
- Update the capture cases that assert the old behaviour (`--m3b-log` navigation, focus and
  wording cases; the M3a jump check), keeping each rule's meaning.

### Task 48: no automatic announcements from the continuously changing log counter

**Files:** `index.html`, `app.js`; `scripts/gui_demo_capture.py`.
- Remove `aria-live` from `#log-count`. The visible counts are unchanged. `#linkstate`
  (`role="status"`, the connection and fault banner) stays. The page has exactly these two live
  regions today (grep); confirm no other continuously changing region is announced.
- Capture check: the live regions on the page are exactly `#linkstate`; `#log-count` and every
  other continuously updating element have no live-region ancestor; during 20 s of traffic the
  live region's mutation count is 0 while the log counter text does change; on a disconnect and
  on a fault, `#linkstate` does change (the meaningful announcement is preserved). Automation
  verifies markup and mutations, not what a screen reader says; the record says so.

### Task 49: prune expansion state on eviction; the "not following, not pinned" state

**Files:** `app.js`, `index.html` (a read-only diagnostic attribute only if needed);
`scripts/gui_demo_capture.py`.
- `view.expanded` entries are removed when their entry is evicted from `S.entries`
  (`trim()`); expansion of rows still retained is kept. Regression: expand a row, evict it past
  the 2,000 cap, and show the expansion state no longer holds it (a small read-only
  diagnostic count the harness reads, written by the page and never read by it, like the
  page's other `data-*` diagnostics).
- Investigate `setFollow(false)` with no exchange shown (`view.lastId == null`), which leaves
  `follow = false, endId = null`: reproduce it through the harness (a filter that matches
  nothing while marker rows are drawn and scrolled). If it violates reader position or
  navigation (a window that keeps sliding while the page treats the reader as pinned), fix it:
  with no exchange shown there is nothing to pin, so the log stays following. Add a focused
  regression; if it cannot be reproduced or does not violate behaviour, report the evidence
  and change nothing.

### Task 50: the record, the owner checklist, and publication

**Files:** the records under `docs/validation/`; no code.
- Correct the performance record's 48 ms versus 32 ms wording: name the conditions of each
  measurement (headless Chrome 151, GPU disabled, 1440 × 900, full buffer, about 3.8 requests/s,
  p95 of a scripted click's Event Timing, the page with the old box-shadow beat versus the
  ring, two runs per width, 390 unchanged). The visible-browser result stays unverified.
- Update the rulings list (the focus rule replaced; the approvals above recorded as owner
  approvals), mark the keyboard-Newer and `#log-count` findings and the two cleanups resolved
  as plain lines with what automation verified and what still needs the owner's observation;
  no box ticked; M3b not accepted.
- An ordered owner checklist with commands: the visual review, the Chrome and Firefox CSP
  checks, and the isolated fault checks; automation-verified items marked apart from items
  that need the owner's observation.
- After review, the affected checks and the required gates: push the final `gui` head as a
  fast-forward and report the exact SHA and hosted CI with skip reasons.

### Task 47b: five full log rows in the paused, stale and last-known states (owner, 2026-10-03/04)

The owner required five fully visible exchange rows at 1440 × 900 in the filtered-and-pinned state
and in the other states identified. Measured after Task 47: filtered + pinned 5 (+38 px),
filtered following 5 (+39), pinned with a long count 4 (the region has room; one full row
was a marker row), **paused 4 (−21 px), stale 4 (−17), "Last known" 3 (−42)**. The header is
one line in every state, so header text cannot recover them; the paused line (58 px) and the
connection banner (54 px stale, 64 px last known) take the height.
- Lever 1 (no information removed): make the log's filter bar fit one row at 1440 (the design
  record already noted that fitting it on one row returns about 50 px): tighter spacing and
  chip padding first; moving the Pause view / Clear view buttons up beside the other log view
  buttons only if spacing alone is not enough. Controls stay readable (font size, targets) with
  full accessible labels. Measure all six states again.
- Lever 2, only for a state still short of five rows after lever 1: compact the paused line to
  one line, and shorten banner wording that repeats information shown elsewhere (never
  information that exists nowhere else). Measure again.
- If a state is still short, STOP and report with numbers. Document the minimum clearance; no
  extra pixel target beyond five full rows.

### Task 51: live announcements, ticking values and the recovery announcement (owner, 2026-10-04)

The full requirement and design are in `.superpowers/sdd/gui-m2-implementation/task-51-brief.md`
(local). In short: the retry countdown and the "Ns ago" elapsed time stay visible but outside
any live region; one always-mounted, visually hidden `#announce` (role=status, polite, atomic) at
body level carries every transition announcement; recovery wording follows the connection and
data-validity model ("Connection restored; data current" after an outage that included a
disconnect, "Live data restored." for a data fault without a disconnect, nothing while data
remains last known, each recovery once, none on first load or on healthy polls); Retry now,
reconnect timing and the retry budget unchanged; verification by markup and mutation checks, with
screen-reader behaviour left to the owner.

### Task 52: five full log rows in paused+stale and pinned+stale (owner, 2026-10-04)

Paused while stale and stale while pinned are supported combinations under the five-full-row
requirement at 1440 x 900. Measured after Task 51b: paused while stale 4 rows (rows region 237 px,
about 27 px short); stale while pinned 4 rows (the log header wraps to 72.1 px: stale tag + count +
"N rows below + M beyond this window"). Fix both without removing information and without shrinking
the graphs further: candidates are the paused text inline with the Pause/Resume/Clear row, and
keeping the log header on one line by shortening its text or moving the stale tag. Measure before
choosing. If a larger layout change is needed, STOP and report the concrete alternative first.
Remove the "recorded, not required" wording (record and capture case); both become required cases.

### Task 53: Pause toggles without aria-pressed (owner, 2026-10-04)

The log and graph Pause/Resume buttons keep their changing labels, accessible names, behaviour,
focus and paused styling, and lose `aria-pressed`. The graph time-window selectors keep
`aria-pressed`. Anything keyed on `[aria-pressed="true"]` for the paused look or pause cases
(`app.css`, `app.js`, `scripts/gui_demo_capture.py`) moves to a class or `data-paused`.

### Task 54: announcement policy (owner, 2026-10-04; refines Task 51)

Routine automatic retry starts and repeated failures stay visible in the banner but are silent.
Announced once each: the initial disconnect or data fault, a materially different fault, recovery
exhaustion ("Could not recover"), confirmed recovery (existing wordings; nothing while data stays
last known). Countdowns and "Ns ago" stay silent; retry timing and budgets unchanged.
"Materially different fault" is defined in the record (e.g. disconnect -> data fault or the reverse,
or a different cause). Harness: the "a retry attempt starting" assertion now expects NO announcement
and a visible banner change; zero mutations between ticks, load-time-outage recovery cases and no
duplicates stay.

### Task 55: record, review, push

Update `docs/validation/gui-m3b-live-demo.md` (Tasks 47-51 section, rulings, ordered owner
checklist, remaining-deficit wording, chattiness decision resolved, Pause aria-pressed resolved),
focused regression checks, required gates, review, fast-forward push of the reviewed head, hosted
CI report, short owner checklist. M3b stays "implemented, not accepted".
