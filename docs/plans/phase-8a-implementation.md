# Phase 8a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver every part of Phase 8 that does not need a physical bench, so that when
one appears the only work left is running it.

**Architecture:** Three independent strands that share no code. (1) Corrections to the
existing vcan integration suite so it survives being run individually and on a bus that
can fail. (2) `scripts/setup_can.sh` gains the four link settings a physical bus needs and
a virtual one never does, tested against a mock `ip` so no CAN device is required.
(3) A new `tests/hardware/` package holding an ELM327 serial driver and response parser,
excluded from ordinary runs and refusing to start without an explicit, non-virtual bench —
with the parser tested byte for byte against exchanges recorded in the ELM327 datasheet,
the driver tested over a pty, and the whole harness regression-tested end to end against a
fake dongle on vcan so it never first executes on bench day.

**Tech Stack:** Python 3.12, pytest 9.1, `pyserial` 3.5 (new, optional `[hardware]`
extra), bash, iproute2, `can-isotp` 2.x, kernel `CAN_ISOTP`.

**Spec:** [`docs/decisions/0008-phase-8-question-resolutions.md`](../decisions/0008-phase-8-question-resolutions.md)
(the rulings) and [`docs/decisions/0007-phase-8-hardware-validation.md`](../decisions/0007-phase-8-hardware-validation.md)
(the review: the six `setup_can.sh` gaps in §5.1, the nineteen-point ELM327 list in §6.2,
the `tests/hardware` rules in §7.1, and the fifteen acceptance criteria in §9).

## Global Constraints

- **Phase 8a changes no wire behavior.** If any protocol file under `src/` needs to
  change, stop and escalate. A difference found on hardware gets its own DEV identifier in
  Phase 8b, not a fix folded into this phase.
- **No conformance row gains `hardware validated`.** All 61 rows stay `no`. Nothing gains
  `standards validated` either, ever, from this phase.
- **V1.0 is not tagged.** Completing 8a does not move the gate; 8b does.
- **No specific adapter, dongle, part number or price is named** in any document produced
  here. The bench does not exist.
- Default install must be unchanged: `pip install -e .` pulls **no** new dependency.
- `pyserial>=3.5,<4`, in an optional `[hardware]` extra only.
- Python `>=3.12`. Run tools as `.venv/bin/pytest`, `.venv/bin/ruff`, `.venv/bin/mypy`.
  The venv is `uv`-created and **has no pip**: install with
  `uv pip install --python .venv/bin/python -e ".[dev]"`.
- Integration tests need a vcan interface. Use `scripts/run_integration_tests.sh`, which
  builds a private `vcan0` in an unprivileged user+network namespace.
- `ruff format` is **not** enforced in this repo. Never run it wholesale.
- **`mypy` is run bare, never as `mypy src tests`.** `[tool.mypy] files = ["src"]` in
  `pyproject.toml` is the project's configured scope and CI runs `mypy` with no arguments.
  Measured 2026-09-23 at `9565e13`: bare `mypy` is clean over 45 source files, while
  `mypy src tests` reports one pre-existing error in `tests/unit/test_scenario_generators.py:240`
  that is outside the gate and **is not Phase 8a's to fix**.
- Every commit message explains *why*, including what was rejected. **Never add a
  `Co-Authored-By` trailer.**

## Baseline, measured at `9565e13` on 2026-09-23

The suite total depends on whether a vcan interface is present, and both numbers are
correct in their own context — do not treat either as the only baseline.

| Condition | Result |
|---|---|
| Host has `vcan0` up (this development machine) | **890 passed, 2 xfailed**, 892 collected |
| The 53 integration tests skip (CI: the Azure kernel has no `CONFIG_CAN_ISOTP`) | **837 passed, 2 xfailed** |

The 2 xfailed are DEV-03 and DEV-11 Mode 07 and must stay xfailed — `xfail_strict = true`,
so an unexpected XPASS fails the run.

```bash
.venv/bin/python -m pytest --collect-only -q 2>&1 | tail -1                    # 892 tests collected
.venv/bin/python -m pytest tests/integration --collect-only -q 2>&1 | tail -1  # 53 tests collected
.venv/bin/python -m pytest 2>&1 | tail -1                                      # 890 passed, 2 xfailed
scripts/run_integration_tests.sh 2>&1 | tail -1                                # 53 passed
.venv/bin/mypy 2>&1 | tail -1                                                  # Success: no issues found in 45 source files
.venv/bin/ruff check .                                                         # All checks passed!
```

**Count claims in this plan are expressed as deltas from whichever of those two baselines
applies**, never as absolute totals — an absolute number would be wrong on one of the two
hosts and would rot the moment an unrelated test is added.

## Running a subset of the integration suite

`scripts/run_integration_tests.sh` forwards its arguments but **hardcodes the path**: its
last line is `pytest tests/integration -m vcan "$@"`. Passing a node ID therefore runs the
whole directory *and* that node, which is not what you want when reproducing an ordering
bug. Two ways round it:

- Selectors work through the script: `scripts/run_integration_tests.sh -k vin -vv`.
- **Ordered node IDs need plain pytest** on a host that already has `vcan0` up, which this
  development machine does. pytest honours the order node IDs are given in on the command
  line; the namespace wrapper cannot preserve it.

---

### Task 1: Make the DTC-clearing dispatch test independently runnable

`test_clearing_dtcs_is_not_mistaken_for_a_suppressed_request` sends `14 FF FF FF`, which
clears the shared DTC store, without requesting the `mutating` fixture that restarts the
simulator afterwards. The module-scoped simulator and the test's position near the end of
the file hide it today. A bench run is partly manual and partly out of order
([0007 §7.1 rule 4](../decisions/0007-phase-8-hardware-validation.md)), which is exactly
the condition that exposes it.

**Files:**
- Modify: `tests/integration/test_ecu_dispatch.py:169-174`

**Interfaces:**
- Consumes: the `mutating` fixture from `tests/integration/conftest.py:255-269`, which
  yields the module-scoped `Simulator` and calls `simulator.restart()` afterwards.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Prove the order dependency exists**

Run the DTC-reading test *after* the clearing test in one process. Plain pytest, not the
namespace wrapper, because only the command line preserves the order (see "Running a
subset" above). Needs `vcan0` up on the host:

```bash
.venv/bin/python -m pytest -p no:cacheprovider \
  "tests/integration/test_ecu_dispatch.py::test_clearing_dtcs_is_not_mistaken_for_a_suppressed_request" \
  "tests/integration/test_ecu_dispatch.py::test_reading_dtcs_can_be_asked_for_silently" -v
```

**This was run on 2026-09-23 at `9565e13` and it fails**, which is what makes the task
real rather than theoretical. Observed, verbatim:

```
>       assert uds_physical.recv() == bytes.fromhex("59028c9477010c0001010c")
E       AssertionError: assert b'Y\x02\x8c' == b'Y\x02\x8c\x...0\x01\x01\x0c'
E         - (b'Y\x02\x8c\x94w\x01\x0c\x00\x01\x01\x0c')
E         + b'Y\x02\x8c'
tests/integration/test_ecu_dispatch.py:166: AssertionError
========================= 1 failed, 1 passed in 0.60s ==========================
```

The store was cleared by the first test and never restored, so `19 02 FF` answers the
header alone. **If it passes for you, stop and re-read** — the dependency has been fixed
by something else and this plan's premise needs rechecking before you change anything.

Note the natural file order is the other way round (the reading test is at line 150, the
clearing test at line 169), which is exactly why a whole-file run hides this.

- [ ] **Step 2: Add the fixture**

In `tests/integration/test_ecu_dispatch.py`, change the signature and extend the comment:

```python
def test_clearing_dtcs_is_not_mistaken_for_a_suppressed_request(mutating, uds_physical):
    # 0x14 has no sub-function, and the second byte of its only served groupOfDTC is 0xFF.
    # Nothing masks it and nothing withholds the acknowledgement.
    #
    # This clears the shared store, so it takes `mutating` and the simulator is replaced
    # afterwards. Without it the test passes only because of where it sits in the file,
    # and a bench run -- partly manual, partly out of order -- would not grant that.
    uds_physical.send(b"\x14\xff\xff\xff")
    assert uds_physical.recv() == b"\x54"
```

- [ ] **Step 3: Re-run the two tests in the order that failed**

```bash
scripts/run_integration_tests.sh \
  tests/integration/test_ecu_dispatch.py::test_clearing_dtcs_is_not_mistaken_for_a_suppressed_request \
  tests/integration/test_ecu_dispatch.py::test_reading_dtcs_can_be_asked_for_silently
```

Expected: 2 passed.

- [ ] **Step 4: Run every DTC-dependent test on its own**

Each must pass in a process of its own — that is what "independently runnable" means:

Plain pytest, not the namespace wrapper — it hardcodes `tests/integration` and appends
arguments, so a node ID would run the whole directory *and* that node, which is not a
per-test run. Needs `vcan0` up on the host.

```bash
fail=0
for t in \
  tests/integration/test_ecu_dispatch.py::test_reading_dtcs_can_be_asked_for_silently \
  tests/integration/test_ecu_dispatch.py::test_clearing_dtcs_is_not_mistaken_for_a_suppressed_request \
  tests/integration/test_obd_isotp.py::test_uds_read_dtc_by_status_mask_on_the_wire \
  tests/integration/test_obd_isotp.py::test_clearing_over_uds_is_visible_to_obd_on_the_wire \
  tests/integration/test_obd_isotp.py::test_clearing_over_obd_is_visible_to_uds_on_the_wire ; do
  r=$(.venv/bin/python -m pytest -p no:cacheprovider "$t" -q 2>&1 | tail -1)
  printf "%-78s %s\n" "$(basename $t)" "$r"
  echo "$r" | grep -q "1 passed" || fail=1
done
[ $fail -eq 0 ] && echo "ALL INDIVIDUALLY RUNNABLE" || echo "SOME FAILED"
```

Expected: five lines each reading `1 passed`, then `ALL INDIVIDUALLY RUNNABLE`.

- [ ] **Step 5: Full integration suite, count unchanged**

```bash
scripts/run_integration_tests.sh
```

Expected: `53 passed`.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_ecu_dispatch.py
git commit -F - <<'MSG'
test(integration): make the DTC-clearing test independently runnable

test_clearing_dtcs_is_not_mistaken_for_a_suppressed_request sends 14 FF FF FF,
which clears the shared DTC store, but did not request the `mutating` fixture
that replaces the simulator afterwards. It passed only because the module-scoped
simulator and the test's position near the end of the file hid the effect from
the tests that read DTCs.

Phase 8's bench run is partly manual and partly out of order, and 0007 section
7.1 rule 4 requires each test to be individually runnable and individually
reportable. That is precisely the condition under which this order dependency
surfaces, so it is corrected before a bench exists rather than during bench time.

Rejected: a meta-test scanning the suite's source for mutating request bytes and
asserting the enclosing test takes the fixture. It would be clever, fragile, and
would fail for the wrong reasons the moment a new mutating service is added.
MSG
```

---

### Task 2: Prove silence with a known-good follow-up request

Twelve parameterisations across nine test functions prove *silence* with a bare
`pytest.raises(TimeoutError)` against the 1.0 s tester timeout. Three further tests already
follow their silence with a positive exchange and are deliberately left alone. On vcan that can only mean
the simulator chose not to answer. On a physical bus it equally matches a dead adapter, a
bitrate mismatch, an unterminated bus, a bus-off interface or an unpowered dongle — so the
test goes green while the bench is broken.

**Files:**
- Modify: `tests/integration/conftest.py` (add the helper at the end of the tester-side
  helpers section, after `FunctionalTester`)
- Modify: `tests/integration/test_ecu_dispatch.py` (9 parameterisations across 6 tests)
- Modify: `tests/integration/test_obd_isotp.py` (2 tests)
- Modify: `tests/integration/test_scenario_isotp.py` (1 test)

**Interfaces:**
- Consumes: `isotp.socket` and `FunctionalTester`, both of which expose `send(bytes)` and
  `recv() -> bytes`.
- Produces: `assert_silent(channel, request: bytes, probe: bytes, answer: bytes) -> None`,
  importable from `tests.integration.conftest`. Task 12's dry-run suite reuses it.

- [ ] **Step 1: Write the helper's own failing test**

Create `tests/unit/test_silence_helper.py`. It needs no bus — a fake channel is enough to
pin the helper's contract, which is the part that must not regress:

```python
"""The silence helper's contract, pinned without a bus.

On a physical bus a timeout is ambiguous: it also matches a dead adapter, a bitrate
mismatch, an unterminated bus or a bus-off interface. The helper exists to disambiguate,
so its own behaviour when the channel is dead is the thing worth pinning.
"""

import pytest

from tests.integration.conftest import assert_silent


class FakeChannel:
    """Replays a scripted sequence of answers; None means "time out"."""

    def __init__(self, answers: list[bytes | None]) -> None:
        self.answers = list(answers)
        self.sent: list[bytes] = []

    def send(self, payload: bytes) -> None:
        self.sent.append(payload)

    def recv(self) -> bytes:
        answer = self.answers.pop(0)
        if answer is None:
            raise TimeoutError
        return answer


def test_silence_then_a_good_answer_passes():
    channel = FakeChannel([None, b"\x41\x2f\x7f"])
    assert_silent(channel, b"\x3e\x80", probe=b"\x01\x2f", answer=b"\x41\x2f\x7f")
    assert channel.sent == [b"\x3e\x80", b"\x01\x2f"]


def test_an_answered_request_is_not_silent():
    channel = FakeChannel([b"\x7e\x00"])
    with pytest.raises(AssertionError):
        assert_silent(channel, b"\x3e\x80", probe=b"\x01\x2f", answer=b"\x41\x2f\x7f")


def test_a_dead_channel_fails_rather_than_passing():
    # The whole point: two timeouts must not read as "the simulator stayed silent".
    channel = FakeChannel([None, None])
    with pytest.raises(AssertionError, match="channel went dead"):
        assert_silent(channel, b"\x3e\x80", probe=b"\x01\x2f", answer=b"\x41\x2f\x7f")


def test_a_wrong_probe_answer_fails():
    channel = FakeChannel([None, b"\x41\x2f\x00"])
    with pytest.raises(AssertionError):
        assert_silent(channel, b"\x3e\x80", probe=b"\x01\x2f", answer=b"\x41\x2f\x7f")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv/bin/python -m pytest tests/unit/test_silence_helper.py -v
```

Expected: collection error, `ImportError: cannot import name 'assert_silent'`.

- [ ] **Step 3: Write the helper**

Append to `tests/integration/conftest.py`, after the `FunctionalTester` class:

```python
def assert_silent(channel, request: bytes, probe: bytes, answer: bytes) -> None:
    """Assert the simulator says nothing to ``request``, then prove the channel still works.

    On vcan a timeout can only mean the simulator chose not to answer. On a physical bus it
    equally matches a dead adapter, a bitrate mismatch (which ELM327DSJ page 62 shows
    presents as silence), an unterminated bus, a bus-off interface or an unpowered dongle.
    A bare ``pytest.raises(TimeoutError)`` therefore passes while the bench is broken.

    The probe goes *after* the silence, never before. A channel proved alive beforehand may
    have died in between, and then the silence still proves nothing.
    """
    channel.send(request)
    with pytest.raises(TimeoutError):
        channel.recv()
    channel.send(probe)
    try:
        observed = channel.recv()
    except TimeoutError:
        raise AssertionError(
            f"the channel went dead: probe {probe.hex()} was not answered either, so the "
            f"silence after {request.hex()} proves nothing about the simulator"
        ) from None
    assert observed == answer, (
        f"probe {probe.hex()} answered {observed.hex()}, expected {answer.hex()}; "
        f"the silence after {request.hex()} proves nothing about the simulator"
    )
```

- [ ] **Step 4: Run the helper's test to verify it passes**

```bash
.venv/bin/python -m pytest tests/unit/test_silence_helper.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Convert the nine parameterisations in `test_ecu_dispatch.py`**

Add `assert_silent` to the import at the top of the file:

```python
from tests.integration.conftest import FunctionalTester, Simulator, assert_silent, open_tester_socket
```

`01 2F` → `41 2F 7F` is the universal probe: all three routes in the shipped profile
answer it, proven by the existing `test_obd_service_on_the_functional_address_is_answered`
and `test_obd_service_on_the_uds_physical_address_is_answered`.

```python
OBD_PROBE = b"\x01\x2f"
OBD_PROBE_ANSWER = b"\x41\x2f\x7f"


@pytest.mark.parametrize("request_hex", ["1001", "1005", "1901"], ids=["session", "bad-subfunction", "read-dtc"])
def test_uds_service_on_the_functional_address_reaches_no_protocol(functional, request_hex):
    # UDS is not enabled on 0x7DF, so nothing is produced: neither the positive response
    # for a well-formed request nor a negative one for a malformed request.
    assert_silent(functional, bytes.fromhex(request_hex), OBD_PROBE, OBD_PROBE_ANSWER)


def test_unknown_service_on_the_functional_address_gets_no_response(functional):
    assert_silent(functional, b"\x22\xf1\x90", OBD_PROBE, OBD_PROBE_ANSWER)


def test_a_suppressed_positive_response_is_not_transmitted(uds_physical):
    # DEV-07, Phase 7, on the wire rather than in the handler: nothing at all is put on
    # the bus, so a tester that asked for silence waits for its own timeout.
    assert_silent(uds_physical, b"\x3e\x80", OBD_PROBE, OBD_PROBE_ANSWER)


@pytest.mark.parametrize("request_hex", ["1083", "1181"], ids=["session", "reset"])
def test_a_suppressed_service_that_is_not_tester_present_is_silent_too(uds_physical, request_hex):
    # The rule is generic, and the wire proves it for the two services DEV-07 named.
    assert_silent(uds_physical, bytes.fromhex(request_hex), OBD_PROBE, OBD_PROBE_ANSWER)


def test_reading_dtcs_can_be_asked_for_silently(uds_physical):
    # 0x19 has a sub-function, so it takes part in the same rule. This is a deliberate
    # change to what Phase 6 put on the wire for 19 82 FF, which was 7F 19 12.
    uds_physical.send(b"\x19\x02\xff")
    assert uds_physical.recv() == bytes.fromhex("59028c9477010c0001010c")
    # The probe goes after the silence: the positive read above proves the channel was
    # alive then, not that it is alive now.
    assert_silent(uds_physical, b"\x19\x82\xff", OBD_PROBE, OBD_PROBE_ANSWER)


def test_tester_present_on_the_obd_broadcast_address_reaches_no_protocol(functional):
    # UDS is not enabled on 0x7DF, so claiming 0x3E does not make it answerable there.
    assert_silent(functional, b"\x3e\x00", OBD_PROBE, OBD_PROBE_ANSWER)
```

Leave `test_the_functional_channel_still_serves_obd_after_an_ignored_uds_request`,
`test_the_channel_still_works_after_a_suppressed_response` and
`test_a_suppressed_request_does_not_hide_a_negative_response` **unchanged** — each already
follows its silence with a positive exchange, which is the pattern this helper generalises.

- [ ] **Step 6: Convert the two tests in `test_obd_isotp.py`**

Add `assert_silent` to that file's import from `tests.integration.conftest`, then:

```python
def test_supported_pid_chain_terminates_on_the_wire(functional):
    # DEV-04 corrected: walk the chain as a tester would. It ends after the last populated
    # range, and the range beyond it is not answered at all.
    functional.send(b"\x01\x00")
    assert functional.recv() == bytes.fromhex("41001e3f8013")
    functional.send(b"\x01\x20")
    assert functional.recv() == bytes.fromhex("412000020001")
    functional.send(b"\x01\x40")
    last = functional.recv()
    assert last == bytes.fromhex("414044008000")
    assert last[-1] & 0x01 == 0, "the last populated range must not claim a successor"
    assert_silent(functional, b"\x01\x60", b"\x01\x2f", b"\x41\x2f\x7f")


def test_unsupported_pid_gets_no_response(functional):
    # PID 0x01 monitor status is deferred (Phase 6 DTC store), so nothing answers it.
    assert_silent(functional, b"\x01\x01", b"\x01\x2f", b"\x41\x2f\x7f")
```

- [ ] **Step 7: Convert the one test in `test_scenario_isotp.py`**

This file runs its own profile. `01 2F` is **not** the probe here — that profile sets
`fuel_level: 50` and this plan does not assume how that encodes. Engine rpm is fixed at
800 in the same profile and is not driven by the scenario, and 800 rpm encodes as
`41 0C 0C 80`, pinned by `test_engine_rpm_is_answered_on_the_wire`.

```python
from tests.integration.conftest import Simulator, assert_silent, open_tester_socket

...

def test_tester_present_still_works_while_a_scenario_runs(vcan, scenario_simulator):
    uds = open_tester_socket(vcan, rx_id=0x7E9, tx_id=0x7E1)
    try:
        assert ask(uds, b"\x3e\x00") == b"\x7e\x00"
        # rpm is fixed at 800 in this profile and is not one of the driven signals, so it
        # is a probe whose answer does not depend on when in the scenario it is asked.
        assert_silent(uds, b"\x3e\x80", b"\x01\x0c", bytes.fromhex("410c0c80"))
    finally:
        uds.close()
```

- [ ] **Step 8: Run the full integration suite and confirm the count is unchanged**

```bash
scripts/run_integration_tests.sh 2>&1 | tail -5
```

Expected: `53 passed`. The count must not move — this is a refactor of assertions, not a
change of coverage.

- [ ] **Step 9: Run the whole suite, ruff and mypy**

```bash
set -o pipefail
.venv/bin/python -m pytest 2>&1 | tail -5
.venv/bin/ruff check .
.venv/bin/mypy
```

Expected: the baseline plus exactly 4 (the new helper tests), still 2 xfailed, no
unexpected XPASS; ruff and bare mypy clean.

- [ ] **Step 10: Commit**

```bash
git add tests/integration/conftest.py tests/integration/test_ecu_dispatch.py \
        tests/integration/test_obd_isotp.py tests/integration/test_scenario_isotp.py \
        tests/unit/test_silence_helper.py
git commit -F - <<'MSG'
test(integration): prove silence with a known-good follow-up request

Twelve parameterisations across nine test functions proved silence with a bare
pytest.raises(TimeoutError) against a 1.0 s tester timeout. On vcan that is sound: nothing but the simulator
can decide not to answer. On a physical bus the same timeout equally matches a
dead adapter, a bitrate mismatch, an unterminated bus, a bus-off interface or an
unpowered dongle, so every one of those tests would go green while the bench was
broken -- the worst failure mode a test has.

assert_silent sends the request, asserts the timeout, then sends a known-good
request on the same channel and asserts its answer. Free on vcan, load-bearing on
hardware.

The probe goes after the silence, never before. test_reading_dtcs_can_be_asked_
for_silently previously proved the channel alive and then asserted silence, which
a bus dying in between would have passed.

Three tests are deliberately untouched: they already follow their silence with a
positive exchange, and this helper only generalises what they were doing.

test_shutdown_is_clean_while_the_tick_is_running is also untouched. Its
hardware-sensitive constant is a 1.0 s bound on process exit, not a silence
assertion, and it is widened only against a measurement rather than pre-emptively.

Rejected: raising the tester timeout on hardware. A longer timeout makes a broken
bench slower to detect, not easier.
MSG
```

---

### Task 3: `setup_can.sh` — automatic bus-off recovery and link statistics

Gaps 1 and 2 of [0007 §5.1](../decisions/0007-phase-8-hardware-validation.md), ruled in
together and not separable: automatic restart cycles through a persistent fault and makes
a broken bench look healthy, and the `re-started` counter is what makes it safe.

**Files:**
- Modify: `scripts/setup_can.sh`
- Create: `tests/unit/test_setup_can_script.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the mock-`ip` harness `fake_ip_bin(tmp_path, details="") -> pathlib.Path` and
  `run_setup_can(bin_dir, *args, env=None) -> subprocess.CompletedProcess`, reused by
  Tasks 4 and 5. The script gains the environment variable `CAN_RESTART_MS` (default
  `100`, `0` disables).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_setup_can_script.py`. The script is driven against a mock `ip` on
`PATH`, so these run on any host with no CAN device and no root:

```python
"""scripts/setup_can.sh, driven against a mock `ip` on PATH.

The script's whole job is to issue the right privileged link commands, so the test
asserts the exact command sequence rather than any effect. A mock lets that run on a
host with no CAN adapter and no root -- which is every host this project has.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SETUP_CAN = REPO / "scripts" / "setup_can.sh"


def fake_ip_bin(tmp_path: pathlib.Path, details: str = "") -> pathlib.Path:
    """A directory containing a mock `ip` that logs its arguments to ip.log.

    `details` is printed verbatim for `ip -details link show`, which is how the script
    discovers whether the controller supports switchable termination.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "ip.log"
    script = bin_dir / "ip"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {log}\n'
        'if [[ "$*" == *"-details"* && "$*" == *"link show"* ]]; then\n'
        f"  cat <<'EOF'\n{details}\nEOF\n"
        "fi\n"
        "exit 0\n"
    )
    script.chmod(0o755)
    return bin_dir


def ip_calls(tmp_path: pathlib.Path) -> list[str]:
    log = tmp_path / "ip.log"
    return log.read_text().splitlines() if log.exists() else []


def run_setup_can(bin_dir: pathlib.Path, *args: str, env: dict[str, str] | None = None):
    environment = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    environment.update(env or {})
    return subprocess.run(
        ["bash", str(SETUP_CAN), *args], capture_output=True, text=True, env=environment
    )


def test_restart_ms_is_set_by_default(tmp_path):
    bin_dir = fake_ip_bin(tmp_path)
    result = run_setup_can(bin_dir, "can0", "500000")
    assert result.returncode == 0, result.stderr
    assert any("type can restart-ms 100" in call for call in ip_calls(tmp_path)), ip_calls(tmp_path)


def test_restart_ms_is_configurable(tmp_path):
    bin_dir = fake_ip_bin(tmp_path)
    run_setup_can(bin_dir, "can0", "500000", env={"CAN_RESTART_MS": "250"})
    assert any("type can restart-ms 250" in call for call in ip_calls(tmp_path))


def test_restart_ms_zero_skips_the_setting_entirely(tmp_path):
    # 0 means "leave the kernel default", which is off. The script must not send
    # `restart-ms 0` and pretend that is a choice -- it is the absence of one.
    bin_dir = fake_ip_bin(tmp_path)
    run_setup_can(bin_dir, "can0", "500000", env={"CAN_RESTART_MS": "0"})
    assert not any("restart-ms" in call for call in ip_calls(tmp_path)), ip_calls(tmp_path)


def test_statistics_are_printed_after_setup(tmp_path):
    # `ip -details link show` reports configuration but not error counters, and the
    # counters are the only thing that distinguishes "up" from "up on a working bus".
    bin_dir = fake_ip_bin(tmp_path)
    run_setup_can(bin_dir, "can0", "500000")
    assert any("-details -statistics link show can0" in call for call in ip_calls(tmp_path)), ip_calls(tmp_path)


def test_a_rejected_bitrate_stops_before_bringing_the_link_up(tmp_path):
    bin_dir = fake_ip_bin(tmp_path)
    result = run_setup_can(bin_dir, "can0", "not-a-number")
    assert result.returncode != 0
    assert "bitrate must be an integer" in result.stderr
    assert ip_calls(tmp_path) == []


def test_an_invalid_interface_name_is_refused(tmp_path):
    bin_dir = fake_ip_bin(tmp_path)
    result = run_setup_can(bin_dir, "this-name-is-far-too-long-for-an-interface")
    assert result.returncode != 0
    assert "invalid interface name" in result.stderr


def test_usage_is_printed_without_an_interface(tmp_path):
    bin_dir = fake_ip_bin(tmp_path)
    result = run_setup_can(bin_dir)
    assert result.returncode != 0
    assert "usage: setup_can.sh" in result.stderr
```

- [ ] **Step 2: Run it to verify the new assertions fail**

```bash
.venv/bin/python -m pytest tests/unit/test_setup_can_script.py -v
```

Expected: `test_restart_ms_is_set_by_default`, `test_restart_ms_is_configurable`,
`test_restart_ms_zero_skips_the_setting_entirely` and
`test_statistics_are_printed_after_setup` FAIL; the four validation tests PASS, because
the script already validates.

- [ ] **Step 3: Implement gaps 1 and 2**

In `scripts/setup_can.sh`, extend the header comment and replace the final three lines:

```bash
# Usage: sudo scripts/setup_can.sh <interface> [bitrate]     (default bitrate: 500000)
# Example: sudo scripts/setup_can.sh can0 500000
#
# Environment:
#   CAN_RESTART_MS   automatic bus-off recovery delay in ms (default 100; 0 leaves the
#                    kernel default, which is off)
#
# Needs root or CAP_NET_ADMIN. The simulator itself runs unprivileged afterwards:
#   ecu-simulator --interface can0
```

then, after the existing bitrate line and before bringing the link up:

```bash
RESTART_MS="${CAN_RESTART_MS:-100}"
[[ "$RESTART_MS" =~ ^[0-9]+$ ]] || die "CAN_RESTART_MS must be an integer in ms, got '$RESTART_MS'"

# Without this a bus-off leaves the interface down until someone notices, and it presents
# as "the simulator stopped responding" rather than as a bus fault. The `re-started`
# counter below is what keeps the recovery honest: automatic restart would otherwise cycle
# through a persistent fault and make a broken bus look healthy.
if [[ "$RESTART_MS" != "0" ]]; then
    ip link set "$IFACE" type can restart-ms "$RESTART_MS" \
        || die "cannot set restart-ms $RESTART_MS on $IFACE"
fi

ip link set up "$IFACE" || die "cannot bring $IFACE up"

# -statistics, not plain -details: `ip link set up` succeeds on an adapter attached to
# nothing, so configuration alone cannot distinguish "up" from "up on a working bus".
# The controller state and the re-started/bus-errors/error-warn/error-pass/bus-off
# counters can.
ip -details -statistics link show "$IFACE"
```

Delete the old `ip link set up` and `ip -details link show` lines so they are not issued
twice.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/unit/test_setup_can_script.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/setup_can.sh tests/unit/test_setup_can_script.py
git commit -F - <<'MSG'
feat(scripts): restart-ms and link statistics in setup_can.sh

Gaps 1 and 2 of decisions/0007 section 5.1, implemented together because they are
not separable. Automatic bus-off recovery on its own cycles through a persistent
fault and makes a broken bench look healthy; the re-started counter is what keeps
it honest, and only -statistics surfaces it.

-statistics also fixes something the script could not do before: `ip link set up`
succeeds on an adapter attached to nothing, so the old `ip -details link show`
could not distinguish "the link is up" from "the link is up on a working bus".
The controller state and the error counters can.

CAN_RESTART_MS is an environment variable rather than a third positional argument
or a flag, because the positional contract `setup_can.sh <interface> [bitrate]`
appears in the README, in the simulator's own guard messages
(transport/socketcan/interface.py) and in the testbench document, and changing it
would ripple through all of them for no gain.

0 means "leave the kernel default", and the script then sends nothing rather than
sending `restart-ms 0` as though absence were a choice.

Tested against a mock `ip` on PATH, which is what lets the script's command
sequence be asserted on a host with no CAN adapter and no root -- which is every
host this project has until Phase 8b.
MSG
```

---

### Task 4: `setup_can.sh` — guarded termination configuration

Gap 3, ruled in as optional and guarded. Guarding is mandatory, not stylistic:
`ip link set dev canX type can termination 120` fails on a controller without switchable
termination, and under `set -euo pipefail` that aborts the whole script.

**Files:**
- Modify: `scripts/setup_can.sh`
- Modify: `tests/unit/test_setup_can_script.py`

**Interfaces:**
- Consumes: `fake_ip_bin`, `ip_calls`, `run_setup_can` from Task 3.
- Produces: the environment variable `CAN_TERMINATION` (unset by default, meaning "do not
  touch termination").

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_setup_can_script.py`. The `details` string reproduces the shape
the kernel documentation shows for a controller that supports switchable termination:

```python
TERMINATION_SUPPORTED = """\
2: can0: <NOARP,UP,LOWER_UP,ECHO> mtu 16 qdisc pfifo_fast state UP qlen 10
    link/can
    can <TRIPLE-SAMPLING> state ERROR-ACTIVE restart-ms 100
    bitrate 500000 sample-point 0.875
    termination 120 [ 0, 120 ]
"""

TERMINATION_UNSUPPORTED = """\
2: can0: <NOARP,UP,LOWER_UP,ECHO> mtu 16 qdisc pfifo_fast state UP qlen 10
    link/can
    can <TRIPLE-SAMPLING> state ERROR-ACTIVE restart-ms 100
    bitrate 500000 sample-point 0.875
"""


def test_termination_is_not_touched_by_default(tmp_path):
    bin_dir = fake_ip_bin(tmp_path, details=TERMINATION_SUPPORTED)
    run_setup_can(bin_dir, "can0", "500000")
    assert not any("termination" in call for call in ip_calls(tmp_path)), ip_calls(tmp_path)


def test_termination_is_set_when_requested_and_supported(tmp_path):
    bin_dir = fake_ip_bin(tmp_path, details=TERMINATION_SUPPORTED)
    result = run_setup_can(bin_dir, "can0", "500000", env={"CAN_TERMINATION": "120"})
    assert result.returncode == 0, result.stderr
    assert any("type can termination 120" in call for call in ip_calls(tmp_path)), ip_calls(tmp_path)


def test_termination_is_skipped_with_a_warning_when_unsupported(tmp_path):
    # The controller has no switchable termination. Sending the command would fail, and
    # under `set -e` that would abort a script that had otherwise succeeded.
    bin_dir = fake_ip_bin(tmp_path, details=TERMINATION_UNSUPPORTED)
    result = run_setup_can(bin_dir, "can0", "500000", env={"CAN_TERMINATION": "120"})
    assert result.returncode == 0, result.stderr
    assert not any("type can termination" in call for call in ip_calls(tmp_path))
    assert "does not support switchable termination" in result.stderr
    assert "120" in result.stderr  # the operator is told to terminate the harness instead


def test_a_non_numeric_termination_is_refused(tmp_path):
    bin_dir = fake_ip_bin(tmp_path, details=TERMINATION_SUPPORTED)
    result = run_setup_can(bin_dir, "can0", "500000", env={"CAN_TERMINATION": "120ohm"})
    assert result.returncode != 0
    assert "CAN_TERMINATION" in result.stderr
```

- [ ] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/unit/test_setup_can_script.py -v -k termination
```

Expected: `test_termination_is_not_touched_by_default` PASSES (nothing sets termination
yet); the other three FAIL.

- [ ] **Step 3: Implement the guard**

Add `CAN_TERMINATION` to the `Environment:` header block, then insert after the
`restart-ms` block and before `ip link set up`:

```bash
if [[ -n "${CAN_TERMINATION:-}" ]]; then
    [[ "$CAN_TERMINATION" =~ ^[0-9]+$ ]] || die "CAN_TERMINATION must be an integer in ohms, got '$CAN_TERMINATION'"
    # Controllers without switchable termination reject this command, and under `set -e`
    # that would abort a run that had otherwise succeeded. `ip -details link show` reports
    # the available values as e.g. `termination 120 [ 0, 120 ]` when the controller has
    # them, so ask first.
    if ip -details link show "$IFACE" 2>/dev/null | grep -q 'termination '; then
        ip link set dev "$IFACE" type can termination "$CAN_TERMINATION" \
            || die "cannot set termination $CAN_TERMINATION on $IFACE"
    else
        echo "setup_can.sh: $IFACE does not support switchable termination;" \
             "terminate the bus in the harness instead ($CAN_TERMINATION ohm at each end" \
             "of the differential pair, two in total)" >&2
    fi
fi
```

- [ ] **Step 4: Run to verify they pass**

```bash
.venv/bin/python -m pytest tests/unit/test_setup_can_script.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/setup_can.sh tests/unit/test_setup_can_script.py
git commit -F - <<'MSG'
feat(scripts): guarded termination configuration in setup_can.sh

Gap 3 of decisions/0007 section 5.1, ruled in as optional and guarded.

The guard is mandatory rather than stylistic: `ip link set dev canX type can
termination 120` fails on a controller without switchable termination, and under
`set -euo pipefail` that aborts a script that had otherwise succeeded. The kernel
documentation shows the available values appearing in `ip -details link show` as
`termination 120 [ 0, 120 ]` when the controller has them, so the script asks
before it sets, and tells the operator to terminate the harness when it cannot.

Off by default. An unterminated bus is the commonest physical-bench mistake, but
so is over-termination: two 120 ohm resistors across the differential pair in
total, and an adapter and a dongle that each have one built in are already
correct. A script that silently added a third would create the fault it was
meant to prevent.
MSG
```

---

### Task 5: `setup_can.sh` — warn on a non-OBD bitrate without rejecting it

Gap 5. A bitrate mismatch is cheap to prevent and expensive to diagnose: per
[0008 §6](../decisions/0008-phase-8-question-resolutions.md) it surfaces as silence,
`NO DATA` or `CAN ERROR` depending on the path taken.

**Files:**
- Modify: `scripts/setup_can.sh`
- Modify: `tests/unit/test_setup_can_script.py`

**Interfaces:**
- Consumes: `fake_ip_bin`, `ip_calls`, `run_setup_can` from Task 3.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("bitrate", ["500000", "250000"])
def test_an_obd_bitrate_produces_no_warning(tmp_path, bitrate):
    bin_dir = fake_ip_bin(tmp_path)
    result = run_setup_can(bin_dir, "can0", bitrate)
    assert result.returncode == 0, result.stderr
    assert "warning" not in result.stderr.lower(), result.stderr


def test_an_unusual_bitrate_warns_but_still_configures_the_link(tmp_path):
    # Legitimate custom configurations must keep working: setup_can.sh is a generic CAN
    # setup script, and the kernel accepts 1..1000000.
    bin_dir = fake_ip_bin(tmp_path)
    result = run_setup_can(bin_dir, "can0", "125000")
    assert result.returncode == 0, result.stderr
    assert "warning" in result.stderr.lower()
    assert "125000" in result.stderr
    assert any("bitrate 125000" in call for call in ip_calls(tmp_path)), ip_calls(tmp_path)


def test_the_warning_names_the_symptoms_of_a_mismatch(tmp_path):
    # Silence is only one of three presentations, and a troubleshooting hint that named
    # just one would send the operator down the wrong path two times in three.
    bin_dir = fake_ip_bin(tmp_path)
    result = run_setup_can(bin_dir, "can0", "125000")
    for symptom in ("NO DATA", "CAN ERROR", "silen"):
        assert symptom in result.stderr, f"{symptom!r} missing from: {result.stderr}"
```

- [ ] **Step 2: Run to verify they fail**

```bash
.venv/bin/python -m pytest tests/unit/test_setup_can_script.py -v -k "bitrate or warning or symptom"
```

Expected: the two `test_an_obd_bitrate_produces_no_warning` parameterisations PASS
(nothing warns yet), the other two FAIL.

- [ ] **Step 3: Implement the warning**

Insert immediately after the existing bitrate validation line:

```bash
# ISO 15765-4 uses 500 kbit/s and 250 kbit/s, which the ELM327 selects as protocols 6 and
# 8. A mismatch is not loud: per ELM327DSJ it presents as silence, as NO DATA, or as
# CAN ERROR depending on whether the device is searching for a protocol, so it looks like
# a dead adapter or broken wiring. Warning here is far cheaper than diagnosing it there.
# This never rejects: setup_can.sh is a generic CAN setup script and the kernel accepts
# 1..1000000.
if [[ "$BITRATE" != "500000" && "$BITRATE" != "250000" ]]; then
    echo "setup_can.sh: warning: $BITRATE bit/s is not one of the OBD bitrates" \
         "(500000 or 250000). This is fine for non-OBD use. If you are talking to an" \
         "ELM327, a mismatch presents as silence, as 'NO DATA' or as 'CAN ERROR' --" \
         "never as an obvious bitrate error. Continuing." >&2
fi
```

- [ ] **Step 4: Run the whole script test file**

```bash
.venv/bin/python -m pytest tests/unit/test_setup_can_script.py -v
```

Expected: 15 passed.

- [ ] **Step 5: Run the full suite, ruff and mypy**

```bash
set -o pipefail
.venv/bin/python -m pytest 2>&1 | tail -5
.venv/bin/ruff check .
.venv/bin/mypy
```

Expected: the baseline plus 4 (Task 2) plus 15 (the setup_can.sh tests), still 2
xfailed; ruff and bare mypy clean.

- [ ] **Step 6: Commit**

```bash
git add scripts/setup_can.sh tests/unit/test_setup_can_script.py
git commit -F - <<'MSG'
feat(scripts): warn on a non-OBD bitrate without rejecting it

Gap 5 of decisions/0007 section 5.1. It warns and never rejects: setup_can.sh is
a generic CAN setup script, a non-OBD bitrate is legitimate for non-OBD use, and
the kernel accepts 1..1000000.

The warning names three symptoms, not one. decisions/0007 section 6.3 said a
bitrate mismatch "does not produce an error -- it produces silence"; re-reading
ELM327DSJ for decisions/0008 showed that is narrower than stated. The frequency
matching on page 62 applies only while searching for a protocol, and a quiet bus
passes it anyway; page 87 gives CAN ERROR for "a baud rate that does not match
the actual data rate", and NO DATA when the AT ST timer expires. A hint naming
only silence would misdirect an operator two times in three.

Rejected: reading the bitrate at simulator startup and warning there. It would be
a production code change inside the phase whose defining rule is that it changes
nothing, and it would need netlink or `ip` parsing to learn a value the operator
has just typed here.
MSG
```

---

### Task 6: `tests/hardware` opt-in collection and bench isolation

Acceptance criterion 5, and the owner's requirement that physical testing target a
dedicated isolated bench rather than an arbitrary active CAN interface. The `hardware`
marker has existed since Phase 1 but **a marker does not prevent collection** — pytest
collects first and deselects after, so a default run would import `pyserial` and fail on
a machine without the extra.

**Files:**
- Create: `tests/hardware/__init__.py` (empty)
- Create: `tests/hardware/conftest.py`
- Create: `tests/unit/test_hardware_suite_is_opt_in.py`
- Modify: `pyproject.toml` (the `[tool.pytest.ini_options]` block)

**Interfaces:**
- Consumes: `_foreign_responder_present` from `tests/integration/conftest.py`.
- Produces: `hw_bench() -> HardwareBench`, the dataclass
  `HardwareBench(can_iface: str, serial_port: str, baud: int)`, and **the two-backend seam**
  below. Environment contract: `ECU_SIM_HW_BENCH=1`, `ECU_SIM_HW_CAN_IFACE`,
  `ECU_SIM_HW_SERIAL`, optional `ECU_SIM_HW_BAUD` (default `38400`).

**Amended 2026-09-24 — the harness serves two backends, and the diagnostic logic is
written once.** The plan originally left this implicit, which would have let the bench and
CI drift into proving slightly different things.

| Backend | Transport | Where it runs | What a pass means |
|---|---|---|---|
| **Simulated** | fake ELM327 on a pty, bridged to a real ISO-TP socket on `vcan0` | `tests/integration/`, ordinary CI, no hardware | the harness and the simulator agree |
| **Physical** | real adapter on a serial device — OBDLink LX over `/dev/rfcomm*`, or a USB ELM327 | `tests/hardware/`, opt-in only, never CI | evidence about real wire, for the adapter named in the bench record |

The seam is `tests/hardware/tester.py`:

- `DiagnosticTester` — a `@runtime_checkable` `Protocol` with `at(text) -> str`,
  `ask(text) -> bytes`, `close() -> None`. Both backends satisfy it; a backend that
  forgets a method is rejected at the seam rather than halfway through a bench run.
- `AcceptanceCase(name, why, run)` — frozen and hashable, so both backends parameterise
  over the same registry and pytest ids stay stable. A bench result and a CI result can
  then be compared row by row.

It is named `DiagnosticTester`, not `Tester`: pytest collects classes whose names begin
with `Test`, and this matches the transport layer's existing `DiagnosticRequest` /
`DiagnosticResponse` vocabulary. The module imports no pyserial and opens nothing, so the
unit tests that pin the contract run everywhere.

`tests/hardware/` keeps its outright `vcan*` refusal. The simulated backend does not come
through it — it lives in `tests/integration/` and is collected normally.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_hardware_suite_is_opt_in.py`:

```python
"""tests/hardware must be invisible to an ordinary run and refuse an unsafe bench.

A marker cannot do this: pytest collects first and deselects after, so a default run
would import the suite -- and with it pyserial, which the default install does not have.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]


def collect(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    for key in list(environment):
        if key.startswith("ECU_SIM_HW_"):
            del environment[key]
    environment.update(env or {})
    return subprocess.run(
        ["python", "-m", "pytest", "--collect-only", "-q", *args],
        cwd=REPO, capture_output=True, text=True, env=environment,
    )


def test_a_default_run_does_not_collect_the_hardware_suite():
    result = collect()
    assert "tests/hardware" not in result.stdout, result.stdout[-2000:]


def test_asking_for_the_suite_by_path_still_refuses_without_opt_in():
    result = collect("tests/hardware")
    assert "tests/hardware/test_" not in result.stdout
    assert "ECU_SIM_HW_BENCH" in result.stdout + result.stderr


def test_opting_in_without_naming_the_devices_refuses():
    result = collect("tests/hardware", env={"ECU_SIM_HW_BENCH": "1"})
    assert "ECU_SIM_HW_CAN_IFACE" in result.stdout + result.stderr


def test_a_virtual_interface_is_refused():
    # A hardware suite pointed at vcan proves nothing and would silently produce
    # "hardware validated" evidence that was never on a wire.
    result = collect(
        "tests/hardware",
        env={
            "ECU_SIM_HW_BENCH": "1",
            "ECU_SIM_HW_CAN_IFACE": "vcan0",
            "ECU_SIM_HW_SERIAL": "/dev/ttyUSB0",
        },
    )
    assert "virtual" in (result.stdout + result.stderr).lower()
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest tests/unit/test_hardware_suite_is_opt_in.py -v
```

Expected: `test_a_default_run_does_not_collect_the_hardware_suite` PASSES (the directory
does not exist yet); the other three FAIL.

- [ ] **Step 3: Narrow `testpaths`**

In `pyproject.toml`, replace the `testpaths` line:

```toml
# tests/hardware is deliberately absent: it needs a physical bench and the optional
# [hardware] extra, and a marker cannot keep it out of a default run because pytest
# collects before it deselects. `pytest tests/hardware` still works and is then gated
# by that package's own conftest.
testpaths = ["tests/unit", "tests/characterization", "tests/integration"]
```

- [ ] **Step 4: Create the package and its gate**

`tests/hardware/__init__.py` is empty. `tests/hardware/conftest.py`:

```python
"""Opt-in gate and bench isolation for the physical-hardware suite.

Two separate refusals, because they protect against different mistakes:

* Collection is refused unless the bench is named explicitly. Nothing here has a default
  interface, because a default is how a suite ends up transmitting on whatever CAN
  interface happened to be up.
* A virtual interface is refused outright. A hardware suite on vcan would pass and would
  produce "hardware validated" evidence for bytes that never reached a wire.

The pre-flight in ``hw_bench`` adds the third: it refuses a bus that already has a
responder on it. On a bench nothing should answer before the simulator starts. On a
vehicle bus a real ECU would -- which is what makes this the mechanism that enforces the
Q6 ruling in decisions/0008, rather than a note asking people to be careful.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from ecu_simulator.transport.socketcan import interface as iface_mod
from tests.integration.conftest import _foreign_responder_present

BENCH_FLAG = "ECU_SIM_HW_BENCH"
IFACE_VAR = "ECU_SIM_HW_CAN_IFACE"
SERIAL_VAR = "ECU_SIM_HW_SERIAL"
BAUD_VAR = "ECU_SIM_HW_BAUD"
DEFAULT_BAUD = 38400  # ELM327DSJ: 38400 unless PP 0C was changed, or 9600 if pin 6 = 0V


@dataclass(frozen=True)
class HardwareBench:
    can_iface: str
    serial_port: str
    baud: int


def _refusal() -> str | None:
    """Why this suite must not be collected, or None when the bench is fully named."""
    if os.environ.get(BENCH_FLAG) != "1":
        return (
            f"the physical-hardware suite is opt-in: set {BENCH_FLAG}=1 to acknowledge that "
            "this transmits on a real CAN bus, and never point it at a vehicle"
        )
    iface = os.environ.get(IFACE_VAR)
    if not iface:
        return f"{IFACE_VAR} is not set: name the bench CAN interface explicitly (there is no default)"
    if iface.startswith("vcan"):
        return (
            f"{IFACE_VAR}={iface!r} is a virtual interface; the hardware suite exists to "
            "show the bytes survive real wire, and vcan would pass without proving it"
        )
    if not os.environ.get(SERIAL_VAR):
        return f"{SERIAL_VAR} is not set: name the ELM327 serial device (e.g. a USB or rfcomm tty)"
    return None


def pytest_ignore_collect(collection_path, config):  # noqa: ARG001
    if _refusal() is not None:
        return True
    return None


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/hardware" in str(item.fspath):
            item.add_marker(pytest.mark.hardware)


def pytest_configure(config: pytest.Config) -> None:
    reason = _refusal()
    if reason and config.args and any("hardware" in str(arg) for arg in config.args):
        # Asked for by name but not opted in: say why, rather than reporting "no tests ran".
        print(f"\ntests/hardware not collected: {reason}\n")


@pytest.fixture(scope="session")
def hw_bench() -> HardwareBench:
    reason = _refusal()
    if reason:
        pytest.skip(reason)
    iface = os.environ[IFACE_VAR]
    try:
        iface_mod.interface_index(iface)
    except Exception:
        pytest.skip(f"CAN interface {iface!r} does not exist: run scripts/setup_can.sh")
    if not iface_mod.is_interface_up(iface):
        pytest.skip(f"CAN interface {iface!r} is down: sudo scripts/setup_can.sh {iface}")
    if _foreign_responder_present(iface):
        pytest.fail(
            f"something already answers OBD requests on {iface!r}. This suite must run on a "
            "dedicated bench with nothing but the simulator and the tester on it. If this is "
            "a vehicle, stop: decisions/0008 rules that this simulator never transmits on a "
            "live vehicle bus."
        )
    return HardwareBench(
        can_iface=iface,
        serial_port=os.environ[SERIAL_VAR],
        baud=int(os.environ.get(BAUD_VAR, DEFAULT_BAUD)),
    )
```

- [ ] **Step 5: Add a placeholder test so collection is observable**

Create `tests/hardware/test_bench_preflight.py`:

```python
"""The smallest possible hardware test: the bench is what it claims to be.

It exists from Task 6 so the opt-in machinery has something to collect, and it is a real
check -- a bench whose interface is down or already busy fails here, clearly, before any
protocol question is asked.
"""

from tests.hardware.conftest import HardwareBench


def test_the_bench_is_named_and_reachable(hw_bench: HardwareBench):
    assert not hw_bench.can_iface.startswith("vcan")
    assert hw_bench.serial_port
    assert hw_bench.baud in (9600, 38400) or hw_bench.baud > 0
```

- [ ] **Step 6: Run the gate tests**

```bash
.venv/bin/python -m pytest tests/unit/test_hardware_suite_is_opt_in.py -v
```

Expected: 4 passed.

- [ ] **Step 7: Prove criterion 5 — the default collection count is unchanged**

```bash
.venv/bin/python -m pytest --collect-only -q 2>&1 | tail -1
```

Expected: the same total as the Task 5 baseline plus the four tests added in Step 1 — and
**no** `tests/hardware` entries.

- [ ] **Step 8: Commit**

```bash
git add tests/hardware tests/unit/test_hardware_suite_is_opt_in.py pyproject.toml
git commit -F - <<'MSG'
test(hardware): opt-in collection and bench isolation for tests/hardware

Acceptance criterion 5 of decisions/0007, and the isolation requirement in
decisions/0008 section 4.3.

The `hardware` marker has been declared since Phase 1 and cannot do this job on
its own: pytest collects before it deselects, so a default run would import the
suite and with it pyserial, which the default install deliberately does not have.
testpaths now names the three ordinary suites instead of `tests`, and the package
has its own pytest_ignore_collect gate, so `pytest tests/hardware` refuses too.

Nothing has a default interface. A default is how a suite ends up transmitting on
whatever CAN interface happened to be up, and this one transmits.

A vcan interface is refused outright rather than allowed as a convenience: a
hardware suite on a virtual interface passes while proving nothing, and would
produce "hardware validated" evidence for bytes that never reached a wire.

The pre-flight reuses _foreign_responder_present from the integration conftest.
On a dedicated bench nothing answers OBD before the simulator starts; on a
vehicle bus a real ECU would. That turns the Q6 ruling -- this simulator never
transmits on a live vehicle bus -- from a sentence in a document into a refusal
in code.
MSG
```

---

### Task 7: `pyserial` as an optional `[hardware]` extra

**Files:**
- Modify: `pyproject.toml` (`[project.optional-dependencies]`)
- Modify: `docs/modernization-plan.md` (§7.2 pyserial row)

**Interfaces:**
- Consumes: nothing.
- Produces: the extra `hardware`, installable with
  `uv pip install --python .venv/bin/python -e ".[dev,hardware]"`.

- [ ] **Step 1: Add the extra**

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5.0",
    "ruff>=0.6",
    "mypy>=1.11",
]
# Only the opt-in tests/hardware suite uses this, and CI never installs it. The ELM327 is
# a serial device whose responses end at a '>' prompt rather than at a line boundary, so
# the driver needs read-until-with-timeout and raw port setup -- exactly the part that
# hand-rolled termios code gets wrong. See docs/decisions/0008 section 2, Q2.
hardware = ["pyserial>=3.5,<4"]
```

- [ ] **Step 2: Verify the default install is unchanged**

```bash
uv pip install --python .venv/bin/python -e ".[dev]" 2>&1 | tail -3
.venv/bin/python -c "import serial" 2>&1 | tail -1
```

Expected: the install succeeds and the import raises `ModuleNotFoundError: No module named
'serial'`. **If `serial` imports, the extra has leaked into the default install — stop.**

- [ ] **Step 3: Install the extra and verify the version**

```bash
uv pip install --python .venv/bin/python -e ".[dev,hardware]" 2>&1 | tail -3
.venv/bin/python -c "import serial; print(serial.__version__)"
```

Expected: `3.5`.

- [ ] **Step 4: Record the version actually installed in the plan's §7.2**

Replace the `not installed` text in the pyserial row of
`docs/modernization-plan.md` §7.2 with the version printed in Step 3, keeping the
"Reviewed" column at `Phase 8a`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml docs/modernization-plan.md
git commit -F - <<'MSG'
build: pyserial as an optional hardware extra

Option A of decisions/0007 section 7.2, ruled in decisions/0008. The first new
dependency since Phase 4, and it lands in an extra that the default install and
CI never touch.

The real cost is not the install: it is a permanent row in the section 7.2
inventory that every later phase touching it must re-version-check under sections
11.1 and 11.5. That is accepted because the alternative is worse. The ELM327's
responses end at a '>' prompt rather than at a line boundary, so the driver needs
read-until-with-timeout over a raw port; hand-rolled termios code is exactly
where serial timeout handling goes wrong, in a suite that runs rarely enough that
the bug would sit undiscovered until bench day.

Option C -- a documented manual procedure and no suite -- was rejected for a
reason that only shows up later: hardware-validated rows are pinned to a commit,
so re-validating after Phases 9 to 14 would mean redoing the manual work every
time.
MSG
```

---

### Task 8: The ELM327 response parser, from datasheet-recorded exchanges

The parser is pure and has no I/O, so it is fully testable with no hardware and no
`pyserial`. It must live in a module that imports neither, because its tests run in CI.

**Files:**
- Create: `tests/hardware/elm327_parser.py`
- Create: `tests/unit/test_elm327_parser.py`

**Interfaces:**
- Consumes: nothing.
- Produces, imported by Tasks 9, 10 and 12:
  - `class Elm327Error(Exception)` with attribute `message: str`
  - `def clean(raw: str) -> list[str]` — NULs removed, echo and prompt stripped, split into
    non-empty lines
  - `def parse_response(raw: str, sent: str) -> bytes` — the OBD payload; raises
    `Elm327Error` for any of the datasheet's error strings
  - `ERROR_MESSAGES: tuple[str, ...]`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_elm327_parser.py`. Every fixture string here is transcribed from
ELM327DSJ, not invented:

```python
"""The ELM327 response parser, against exchanges recorded in ELM327DSJ.

Sources, all from ELM327DSJ (the revision recorded in modernization-plan.md section 7.1):

* "Multiple PID Requests", page 45 -- the two worked CAN captures, which Phase 5.1 already
  reproduces byte for byte from the simulator's side. Here they are parsed from the
  tester's side.
* "Error Messages and Alerts", pages 87-88 -- the error vocabulary.
* "Communicating with the ELM327", pages 8-9 -- CR termination with optional linefeed, the
  '>' prompt, echo on by default, and the instruction to remove NUL bytes.
"""

from __future__ import annotations

import pytest

from tests.hardware.elm327_parser import Elm327Error, clean, parse_response

# ELM327DSJ page 45, first capture: >01 04 05 0B 0C
DATASHEET_MULTILINE = "01 04 05 0B 0C\r00A\r0: 41 04 3F 05 44 0B\r1: 21 0C 17 B8 00 00 00\r\r>"
# ELM327DSJ page 45, second capture: >01 0B 04 0C 05
DATASHEET_MULTILINE_REORDERED = "01 0B 04 0C 05\r00A\r0: 41 0B 21 04 3F 0C\r1: 17 B8 05 44 00 00 00\r\r>"


def test_the_datasheet_multiline_capture_parses_to_its_ten_bytes():
    # The first line is the length, 00A = 10, so the final three 00s on the last line are
    # ISO-TP padding beyond it and are not part of the response.
    assert parse_response(DATASHEET_MULTILINE, sent="01 04 05 0B 0C") == bytes.fromhex(
        "41043F05440B210C17B8"
    )


def test_the_datasheet_reordered_capture_parses_to_its_ten_bytes():
    assert parse_response(DATASHEET_MULTILINE_REORDERED, sent="01 0B 04 0C 05") == bytes.fromhex(
        "410B21043F0C17B80544"
    )


def test_a_single_line_response_parses():
    assert parse_response("0100\r41 00 1E 3F 80 13\r\r>", sent="0100") == bytes.fromhex(
        "41001E3F8013"
    )


def test_the_echoed_command_is_stripped():
    # Echo is on by default (E1), so the command comes back before the response.
    assert clean("0100\r41 00 1E\r\r>", sent="0100") == ["41 00 1E"]


def test_nul_bytes_are_removed():
    # ELM327DSJ page 9: "if you are writing software for the ELM327, then ignore incoming
    # bytes that are of value 00 (ie. remove NULLs)".
    assert parse_response("0100\r\x0041 00 1E\r\r>", sent="0100") == bytes.fromhex("41001E")


def test_linefeeds_are_tolerated():
    # AT L1 adds a linefeed after every carriage return; AT L0 does not. Both must parse.
    assert parse_response("0100\r\n41 00 1E\r\n\r\n>", sent="0100") == bytes.fromhex("41001E")


def test_spaces_are_optional():
    # AT S0 turns off the printing of spaces.
    assert parse_response("0100\r41001E\r\r>", sent="0100") == bytes.fromhex("41001E")


@pytest.mark.parametrize(
    "message",
    ["NO DATA", "CAN ERROR", "BUS ERROR", "BUS BUSY", "DATA ERROR", "BUFFER FULL",
     "UNABLE TO CONNECT", "STOPPED", "LV RESET", "ERR94", "?"],
)
def test_every_datasheet_error_string_raises(message):
    with pytest.raises(Elm327Error) as caught:
        parse_response(f"3E80\r{message}\r\r>", sent="3E80")
    assert caught.value.message == message


def test_no_data_is_distinguishable_because_it_is_the_expected_answer_to_a_suppressed_request():
    # 0007 section 6.2 item 13: 3E 80 must produce the tester's own no-data condition, not
    # a response from the simulator. A caller needs to tell that apart from a bus fault.
    with pytest.raises(Elm327Error) as caught:
        parse_response("3E80\rNO DATA\r\r>", sent="3E80")
    assert caught.value.message == "NO DATA"


def test_searching_is_not_an_error_and_is_dropped():
    # A protocol search prints SEARCHING... before the response arrives.
    assert parse_response("0100\rSEARCHING...\r41 00 1E\r\r>", sent="0100") == bytes.fromhex("41001E")


def test_a_multiline_length_shorter_than_the_data_truncates():
    # The length line governs; anything past it is ISO-TP padding.
    assert parse_response("0902\r003\r0: 49 02 01 FF FF FF\r\r>", sent="0902") == bytes.fromhex("490201")
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest tests/unit/test_elm327_parser.py -v
```

Expected: collection error, `ModuleNotFoundError: No module named
'tests.hardware.elm327_parser'`.

- [ ] **Step 3: Write the parser**

Create `tests/hardware/elm327_parser.py`. **Imports nothing but the standard library** —
in particular not `serial` — because `tests/unit/test_elm327_parser.py` runs in CI, where
the `[hardware]` extra is not installed:

```python
"""Parse what an ELM327 prints, per ELM327DSJ.

Pure and I/O-free on purpose: it is imported by tests that run in CI, where the optional
[hardware] extra is not installed, so it must not import pyserial.

The shapes it handles, all from ELM327DSJ:

* a single line of hex bytes, with or without spaces (AT S0/S1);
* a multi-line response, where the first line is the ISO-TP length in hex and the
  following lines are numbered ``0:``, ``1:`` -- "the first line tells us that it is 00A
  (decimal 10) bytes long, so we only pay attention to the first ten bytes of the
  following lines" (page 45);
* the error vocabulary on pages 87-88.

Three device behaviours the parser has to absorb, each of which would otherwise corrupt a
byte comparison: the command is echoed back (E1 is the default), a linefeed may or may not
follow each carriage return (AT L1/L0), and NUL bytes may appear anywhere -- page 9 tells
software authors to remove them.
"""

from __future__ import annotations

import re

PROMPT = ">"

ERROR_MESSAGES: tuple[str, ...] = (
    "NO DATA",
    "CAN ERROR",
    "BUS ERROR",
    "BUS BUSY",
    "DATA ERROR",
    "BUFFER FULL",
    "UNABLE TO CONNECT",
    "STOPPED",
    "LV RESET",
    "FB ERROR",
    "ACT ALERT",
    "!ACT ALERT",
    "?",
)

_ERR_CODE = re.compile(r"^ERR\d{2}$")
_LINE_INDEX = re.compile(r"^[0-9A-F]:\s*", re.IGNORECASE)
_HEX_LENGTH = re.compile(r"^[0-9A-F]{3}$", re.IGNORECASE)


class Elm327Error(Exception):
    """The ELM327 reported a condition instead of a response."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def clean(raw: str, sent: str) -> list[str]:
    """Strip NULs, the echoed command and the prompt; return the remaining lines."""
    text = raw.replace("\x00", "")
    text = text.replace("\r\n", "\r").replace("\n", "\r")
    lines = [line.strip() for line in text.split("\r")]
    normalised_sent = sent.replace(" ", "").upper()
    result: list[str] = []
    for line in lines:
        if not line or line == PROMPT:
            continue
        if line.rstrip(PROMPT).strip() == "":
            continue
        if line.replace(" ", "").upper() == normalised_sent and not result:
            continue  # the echo, which only ever precedes the response
        if line.upper().startswith("SEARCHING"):
            continue
        result.append(line.rstrip(PROMPT).strip())
    return [line for line in result if line]


def parse_response(raw: str, sent: str) -> bytes:
    """The OBD payload the device reported, or raise Elm327Error for a reported condition."""
    lines = clean(raw, sent)
    if not lines:
        raise Elm327Error("no response")
    for line in lines:
        upper = line.upper()
        if upper in ERROR_MESSAGES or _ERR_CODE.match(upper) or upper.startswith("<"):
            raise Elm327Error(upper.lstrip("<"))

    if len(lines) > 1 and _HEX_LENGTH.match(lines[0]):
        declared = int(lines[0], 16)
        body = "".join(_LINE_INDEX.sub("", line).replace(" ", "") for line in lines[1:])
        return bytes.fromhex(body)[:declared]

    return bytes.fromhex("".join(line.replace(" ", "") for line in lines))
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/bin/python -m pytest tests/unit/test_elm327_parser.py -v
```

Expected: 21 passed.

- [ ] **Step 5: Confirm the parser imports without the extra**

```bash
.venv/bin/python -c "
import sys, importlib
sys.modules['serial'] = None            # simulate the extra being absent
importlib.import_module('tests.hardware.elm327_parser')
print('parser imports with no pyserial: OK')
"
```

Expected: `parser imports with no pyserial: OK`.

- [ ] **Step 6: Commit**

```bash
git add tests/hardware/elm327_parser.py tests/unit/test_elm327_parser.py
git commit -F - <<'MSG'
test(hardware): ELM327 response parser from datasheet-recorded exchanges

The parser is pure and I/O-free, and imports nothing but the standard library --
in particular not pyserial, because its tests run in CI where the optional
[hardware] extra is not installed. That is what confines the part of the ELM327
driver that cannot be tested without a dongle to the serial I/O alone.

Every fixture is transcribed from ELM327DSJ rather than invented: the two worked
CAN captures on page 45 (the same ones Phase 5.1 reproduces byte for byte from
the simulator's side, now parsed from the tester's side), the error vocabulary on
pages 87-88, and the framing rules on pages 8-9.

Three device behaviours it has to absorb, each of which would otherwise corrupt a
byte comparison: the command is echoed back because E1 is the default, a linefeed
may or may not follow each carriage return depending on AT L1/L0, and NUL bytes
may appear anywhere -- page 9 explicitly tells software authors to remove them,
and a stray NUL inside a hex string would otherwise fail to parse.

NO DATA is raised as a named condition rather than folded into a generic failure,
because 0007 section 6.2 item 13 needs to assert it: a suppressed positive
response is invisible to the tester except as the tester's own no-data timeout,
and that has to be distinguishable from a bus fault.
MSG
```

---

### Task 9: The ELM327 serial driver, tested over a pty

Read-until-prompt with a timeout, per the ruling. This is the only part of the suite whose
correctness a bench could still disprove, so it is kept as thin as possible and tested
against a scripted fake over a real tty.

**Files:**
- Create: `tests/hardware/elm327_serial.py`
- Create: `tests/hardware/fake_elm327.py`
- Create: `tests/unit/test_elm327_serial.py`

**Interfaces:**
- Consumes: `parse_response`, `Elm327Error` from `tests/hardware/elm327_parser.py`.
- Produces, used by Tasks 10 and 12:
  - `class Elm327(port: str, baud: int = 38400, timeout: float = 5.0)` with
    `command(text: str) -> str` (raw), `ask(text: str) -> bytes` (parsed payload),
    `at(text: str) -> str` (an AT command's text answer), `close() -> None`
  - `class FakeElm327(responses: dict[str, str])` with `port: str`, `start()`, `stop()`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_elm327_serial.py`:

```python
"""The ELM327 serial driver, against a scripted fake over a pty.

A pty is a real tty, so pyserial opens and configures it exactly as it would a USB or
rfcomm device. That makes every part of the driver except the physical link testable with
no hardware -- confirmed experimentally before this plan was written.
"""

from __future__ import annotations

import pytest

serial = pytest.importorskip("serial", reason="needs the optional [hardware] extra: pip install -e '.[hardware]'")

from tests.hardware.elm327_parser import Elm327Error  # noqa: E402
from tests.hardware.elm327_serial import Elm327  # noqa: E402
from tests.hardware.fake_elm327 import FakeElm327  # noqa: E402

RESPONSES = {
    "ATZ": "\r\rELM327 v2.1\r\r>",
    "ATI": "ELM327 v2.1\r\r>",
    "AT@1": "OBDII to RS232 Interpreter\r\r>",
    "ATE0": "OK\r\r>",
    "ATSP6": "OK\r\r>",
    "ATDPN": "6\r\r>",
    "ATRV": "12.5V\r\r>",
    "0100": "41 00 1E 3F 80 13\r\r>",
    "3E80": "NO DATA\r\r>",
    "0104050B0C": "00A\r0: 41 04 3F 05 44 0B\r1: 21 0C 17 B8 00 00 00\r\r>",
}


@pytest.fixture
def fake():
    device = FakeElm327(RESPONSES)
    device.start()
    try:
        yield device
    finally:
        device.stop()


@pytest.fixture
def elm(fake):
    device = Elm327(fake.port, baud=38400, timeout=2.0)
    try:
        yield device
    finally:
        device.close()


def test_identity_is_read_back(elm):
    assert elm.at("AT I") == "ELM327 v2.1"


def test_device_description_is_read_back(elm):
    assert elm.at("AT @1") == "OBDII to RS232 Interpreter"


def test_protocol_number_is_read_back(elm):
    assert elm.at("AT DPN") == "6"


def test_an_obd_request_returns_parsed_bytes(elm):
    assert elm.ask("01 00") == bytes.fromhex("41001E3F8013")


def test_a_multiline_response_returns_parsed_bytes(elm):
    assert elm.ask("01 04 05 0B 0C") == bytes.fromhex("41043F05440B210C17B8")


def test_no_data_raises_rather_than_returning_empty(elm):
    with pytest.raises(Elm327Error) as caught:
        elm.ask("3E 80")
    assert caught.value.message == "NO DATA"


def test_a_silent_device_times_out_rather_than_hanging(fake):
    # A device that never prints a prompt must not wedge the run. The timeout is short so
    # the test is fast; the real suite uses a longer one.
    fake.responses["0100"] = "41 00 1E"  # no prompt: the response never terminates
    device = Elm327(fake.port, baud=38400, timeout=0.3)
    try:
        with pytest.raises(TimeoutError):
            device.ask("01 00")
    finally:
        device.close()


def test_the_command_is_terminated_with_a_carriage_return(elm, fake):
    elm.at("AT I")
    assert fake.received[-1].endswith("\r"), fake.received
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest tests/unit/test_elm327_serial.py -v
```

Expected: collection error on `tests.hardware.elm327_serial`. If `pyserial` is not
installed the whole file skips with its reason instead — install the extra (Task 7 Step 3)
before continuing.

- [ ] **Step 3: Write the fake device**

Create `tests/hardware/fake_elm327.py`:

```python
"""A scripted ELM327 on a pty, for testing the driver without a dongle.

It reproduces the three framing behaviours that matter and that a naive fake would omit:
it echoes the command (E1 is the default), it terminates every response with the '>'
prompt, and it occasionally emits a NUL, which ELM327DSJ page 9 warns can happen and tells
software to strip.
"""

from __future__ import annotations

import os
import pty
import threading


class FakeElm327:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = dict(responses)
        self.received: list[str] = []
        self._master, self._slave = pty.openpty()
        # The slave fd is deliberately held open for the object's lifetime: if every slave
        # fd is closed, reading the master fails with EIO and the responder thread dies
        # before the driver ever opens the port.
        self.port = os.ttyname(self._slave)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _serve(self) -> None:
        buffer = b""
        while not self._stop.is_set():
            try:
                data = os.read(self._master, 256)
            except OSError:
                return
            if not data:
                return
            os.write(self._master, data)  # echo, as E1 does
            buffer += data
            while b"\r" in buffer:
                line, buffer = buffer.split(b"\r", 1)
                self.received.append(line.decode(errors="replace") + "\r")
                key = line.decode(errors="replace").replace(" ", "").upper()
                body = self.responses.get(key, "?\r\r>")
                os.write(self._master, b"\x00" + body.encode())

    def stop(self) -> None:
        self._stop.set()
        for fd in (self._master, self._slave):
            try:
                os.close(fd)
            except OSError:
                pass
```

- [ ] **Step 4: Write the driver**

Create `tests/hardware/elm327_serial.py`:

```python
"""Drive an ELM327 over a serial port.

Deliberately thin. This is the only part of the hardware suite whose correctness a real
dongle could still disprove, so it does as little as possible and delegates every decision
about the bytes to elm327_parser.

Read-until-prompt rather than read-line, because ELM327DSJ page 8 says software "should
always wait for either the prompt character ('>' or hex 3E) ... before beginning to send
the next command", and because a multi-line response contains several carriage returns, so
a line-based read cannot tell when the response has ended.
"""

from __future__ import annotations

import serial

from tests.hardware.elm327_parser import PROMPT, Elm327Error, clean, parse_response

DEFAULT_BAUD = 38400  # ELM327DSJ page 8: 38400 unless PP 0C changed, 9600 if pin 6 = 0V


class Elm327:
    def __init__(self, port: str, baud: int = DEFAULT_BAUD, timeout: float = 5.0) -> None:
        # 8N1 per ELM327DSJ page 8: "8 data bits, no parity bits, and 1 stop bit".
        self.port = serial.Serial(
            port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout,
        )

    def command(self, text: str) -> str:
        """Send one command and return everything up to and including the prompt."""
        self.port.reset_input_buffer()
        self.port.write(text.encode("ascii") + b"\r")
        self.port.flush()
        raw = self.port.read_until(PROMPT.encode("ascii")).decode("ascii", errors="replace")
        if PROMPT not in raw:
            raise TimeoutError(
                f"no prompt within {self.port.timeout}s after {text!r}; got {raw!r}. "
                "A silent device is also what a bitrate mismatch, an unpowered dongle or a "
                "wrong serial port look like."
            )
        return raw

    def at(self, text: str) -> str:
        """An AT command's text answer, as one line."""
        raw = self.command(text)
        lines = clean(raw, sent=text)
        if not lines:
            raise Elm327Error("no response")
        return lines[0]

    def ask(self, text: str) -> bytes:
        """An OBD/UDS request's response payload."""
        return parse_response(self.command(text), sent=text)

    def close(self) -> None:
        self.port.close()
```

Add `PROMPT` to the parser's exports if it is not already module-level — it is defined
there in Task 8.

- [ ] **Step 5: Run to verify it passes**

```bash
.venv/bin/python -m pytest tests/unit/test_elm327_serial.py -v
```

Expected: 8 passed.

- [ ] **Step 6: Confirm the suite still skips cleanly without the extra**

```bash
uv pip uninstall --python .venv/bin/python pyserial >/dev/null 2>&1
.venv/bin/python -m pytest tests/unit/test_elm327_serial.py -v 2>&1 | tail -3
uv pip install --python .venv/bin/python -e ".[dev,hardware]" >/dev/null 2>&1
```

Expected: `8 skipped`, with the reason naming the `[hardware]` extra. This is the
behaviour CI will record.

- [ ] **Step 7: Run everything, ruff and mypy**

```bash
set -o pipefail
.venv/bin/python -m pytest 2>&1 | tail -5
.venv/bin/ruff check .
.venv/bin/mypy
```

Expected: all green, with the suite total up by 8 when the `[hardware]` extra is installed
and unchanged when it is not.

mypy will **not** see `tests/hardware/elm327_serial.py` at all: `[tool.mypy] files = ["src"]`
scopes it to production code, and `ignore_missing_imports = true` is already set globally.
So no `serial.*` override is needed, and adding one would be dead configuration. If a later
phase widens mypy's scope to `tests`, revisit this — and note there is already one
pre-existing error there (`tests/unit/test_scenario_generators.py:240`) that is not
Phase 8a's to fix.

- [ ] **Step 8: Commit**

```bash
git add tests/hardware/elm327_serial.py tests/hardware/fake_elm327.py \
        tests/unit/test_elm327_serial.py pyproject.toml
git commit -F - <<'MSG'
test(hardware): ELM327 serial driver with read-until-prompt

Read-until-prompt rather than read-line, because ELM327DSJ page 8 says software
"should always wait for either the prompt character ('>' or hex 3E) ... before
beginning to send the next command", and because a multi-line response contains
several carriage returns -- a line-based read cannot tell when one has ended.

The driver is deliberately thin. It is the only part of the hardware suite whose
correctness a real dongle could still disprove, so it does as little as possible
and delegates every decision about the bytes to elm327_parser.

Tested against a scripted fake on a pty. A pty is a real tty, so pyserial opens
and configures it exactly as it would a USB or rfcomm device; this was confirmed
experimentally before the plan was written, including that read_until(b'>')
returns immediately and that the echo and an injected NUL both appear in the
stream. The fake reproduces all three, because a fake that omitted them would let
the parser's echo- and NUL-stripping rot undetected.

A device that never prints a prompt raises TimeoutError rather than hanging, and
the message names the three things that look identical from here -- a bitrate
mismatch, an unpowered dongle, the wrong serial port -- because on bench day that
is the moment someone will be staring at it.
MSG
```

---

### Task 10: `docs/hardware-testbench.md` — a template that records nothing

Acceptance criterion 6. Every field from [0007 §4.4](../decisions/0007-phase-8-hardware-validation.md)
is present and **explicitly unrecorded**. A template with plausible defaults would be the
exact failure section 11 of the plan exists to prevent.

**Files:**
- Create: `docs/hardware-testbench.md`

**Interfaces:** documentation only.

- [ ] **Step 1: Write the document**

It must contain, and nothing in it may be filled in with a guess:

1. A banner: **no bench exists; every field below is unrecorded; this document asserts
   nothing until Phase 8b fills it in.**
2. The bench by capability — the mandatory list (Linux host with `CONFIG_CAN_ISOTP`;
   SocketCAN adapter; a second node, which the ELM327 can be; USB ELM327; CAN-H, CAN-L and
   a common ground; 120 Ω at each end of the pair, two in total; power for the dongle from
   its own documentation; can-utils) and the optional list (Bluetooth ELM327; a second
   adapter as an independent observer; switchable termination; an OBD breakout).
   **No part numbers, no prices, no examples.**
3. The per-item record table from §4.4: make and model; interface and connection; driver
   or chipset with kernel module name; firmware or hardware revision as the device reports
   it; `AT I` and `AT @1` verbatim; `ip -details link show` for the CAN interface; kernel
   version and distribution; can-utils version; date tested; simulator commit tested.
   Every cell reads `not recorded`.
4. The two sourced wiring facts and the one refusal: CAN on pins 6 and 14 of the OBD
   connector (ELM327DSJ); `AT RV` reads the input voltage; **how the chosen dongle is
   powered is taken from that device's own documentation, never from this one** — the
   ELM327DSJ pin numbers are the IC's pins, not the OBD connector's.
5. The genuine-versus-clone rule from §4.3: record `AT I`, `AT @1` and `AT DP`/`AT DPN`
   verbatim and how the device was obtained; a test that fails on one adapter is an
   interoperability finding naming that adapter, never a defect of this simulator.
6. The `ECHO`-flag and before/after statistics procedure from
   [0008 §7](../decisions/0008-phase-8-question-resolutions.md), with the exact commands.
7. The **physical re-run** procedure: `ECU_SIM_HW_BENCH`/`ECU_SIM_HW_CAN_IFACE` for the
   hardware suite, and separately `ECU_SIM_CAN_IFACE=can0 .venv/bin/python -m pytest
   tests/integration` for the 38 existing tests that re-run unchanged — bracketed by the
   statistics capture, and noting that `scripts/run_integration_tests.sh` **cannot** be
   used because it builds a private network namespace and `can0` is in the host's.
8. What `hardware validated: yes` will and will not mean (§9.1), including that it never
   extends to rows that were not exercised or to other adapters.

- [ ] **Step 2: Check every field is marked unrecorded**

```bash
grep -c "not recorded" docs/hardware-testbench.md
grep -nEi "usb2can|peak|kvaser|vector|obdlink|\$[0-9]|v1\.5|elm327 v2\.1 clone" docs/hardware-testbench.md
```

Expected: at least 10 for the first; **no output** for the second. Any hit is an invented
part number or price and must be removed.

- [ ] **Step 3: Commit**

```bash
git add docs/hardware-testbench.md
git commit -m "docs: hardware testbench template with every field unrecorded"
```

---

### Task 11: Troubleshooting and `--bitrate` guidance

Acceptance criteria 3 and 7. Criterion 3 requires the bitrate guidance to be *consistent*
across three places, so they are written in one commit.

**Files:**
- Modify: `docs/hardware-testbench.md` (troubleshooting section)
- Modify: `README.md` (the `setup_can.sh` section around line 113)
- Modify: `scripts/setup_can.sh` (usage comment)

**Interfaces:** documentation only.

- [ ] **Step 1: Write the troubleshooting section**

Seven cases, the six from [0007 §8](../decisions/0007-phase-8-hardware-validation.md) plus
listen-only, which the Q4 ruling moved here from the setup script:

1. **No frames at all** — termination, wiring, common ground, and
   `ip -details -statistics link show <iface>` counters as the first diagnostic.
2. **The interface goes down and stays down** — bus-off; `CAN_RESTART_MS`; recover by hand
   with `ip link set <iface> type can restart`.
3. **A silent ELM327** — and its two other faces, `NO DATA` and `CAN ERROR`. State that
   `AT BI` bypasses initiation *and* the frequency check, and that it is a **diagnostic,
   never a normal step**: needing it means the bench is misconfigured.
4. **The ELM327 reports the wrong protocol** — `AT DPN`, `AT SP 6`; and why `AT SP 0`
   should not be the first thing tried, since a search transmits in other protocols.
5. **A device that identifies as an ELM327 but behaves differently** — §4.3.
6. **The simulator's own guard messages**, which already name their cause.
7. **Watching a bus without joining it** — `ip link set <iface> type can listen-only on`,
   with the warning that a node in listen-only **cannot transmit**, so an interface left
   in that mode is itself a cause of case 3. Restore with `listen-only off`.

- [ ] **Step 2: Make the bitrate guidance consistent in three places**

All three must say the same thing: bitrate is set by `setup_can.sh` before the simulator
starts, the simulator has no `--bitrate` and will not acquire one, the OBD bitrates are
500000 and 250000, and a mismatch is diagnosed by the three symptoms above.

README, replacing the bare example near line 113:

```markdown
### Physical CAN

Bitrate is a privileged link property, so it is set before the simulator starts. The
simulator itself runs unprivileged and has no `--bitrate` option, by design:

```bash
sudo scripts/setup_can.sh can0 500000     # 500000 and 250000 are the OBD bitrates
ecu-simulator --interface can0
```

`CAN_RESTART_MS` sets automatic bus-off recovery (default 100 ms; 0 leaves it off), and
`CAN_TERMINATION=120` enables the controller's termination resistor where it has one.

A bitrate that is not 500000 or 250000 is accepted with a warning — it is fine for non-OBD
use. Against an ELM327 a mismatch does not announce itself: it presents as silence, as
`NO DATA` or as `CAN ERROR`. See
[docs/hardware-testbench.md](docs/hardware-testbench.md) for the bench procedure.
```

- [ ] **Step 3: Check the three agree**

```bash
grep -n "500000\|250000" README.md scripts/setup_can.sh docs/hardware-testbench.md
grep -rn "\-\-bitrate" README.md scripts/ docs/ src/
```

Expected: the bitrates appear in all three files; `--bitrate` appears **only** as prose
saying the option does not exist, and **never** in `src/`.

- [ ] **Step 4: Commit**

```bash
git add README.md scripts/setup_can.sh docs/hardware-testbench.md
git commit -F - <<'MSG'
docs: bench troubleshooting and consistent --bitrate guidance

Acceptance criteria 3 and 7 of decisions/0007. Criterion 3 asks for consistency
across the README, the script's usage text and the testbench document, so all
three are written in one commit rather than drifting apart.

Listen-only is documented here rather than added to setup_can.sh, per the Q4
ruling. It is a mode a node cannot transmit from, so offering it at setup time
would invite a bench configured into exactly the silent-failure state case 3
exists to diagnose -- and the section says so, because an interface someone left
in listen-only is itself a cause of that case.

AT BI is documented as a diagnostic and explicitly not as a normal step: it
bypasses the initiation sequence and the frequency check together, so a bench
that needs it is a bench that is misconfigured.
MSG
```

---

### Task 12: The ELM327 acceptance tests, plus an automated regression test for the harness

**Mandatory. Amended 2026-09-23.** The plan originally flagged this as the one cuttable
task, on the grounds that it was the only way to learn whether the acceptance suite worked
before bench day. **That argument is now spent**: a real bench run on 2026-09-23 already
demonstrated physical CAN and Bluetooth ELM327 interoperability — 103,050 frames, zero
errors, nine responses byte-identical to the vcan goldens, multi-frame flow control
generated by a real OBDLink LX. See
[validation/phase-8-smoke-test/](../validation/phase-8-smoke-test/README.md).

**So this task is no longer about interoperability, and must not be described as if it
were.** Its purpose is narrower and permanent: **an automated regression test for this
project's own Python ELM327 harness**, exercising `Elm327`, `elm327_parser` and the
acceptance test bodies end to end against a fake dongle on `vcan`, on every CI run and
every developer machine, **with no physical hardware.**

Stated as the property it protects: the harness written in Tasks 8, 9 and 12 is code that
Phase 8b will depend on while a bench is plugged in and expensive. Without this task it
would be exercised only on bench day. With it, a refactor that breaks the parser's
multi-line handling or the driver's read-until-prompt fails in CI in seconds.

The acceptance tests are written against the `Elm327` interface. A fake dongle that bridges
AT commands to a real ISO-TP socket on vcan lets exactly the same test bodies run against
the real simulator, with only the physical serial link simulated.

Two boundaries this task does **not** cross:

- It **is not hardware validation and produces no `hardware validated` evidence.** A fake
  dongle on vcan demonstrates that our harness and our simulator agree. It says nothing
  about a real adapter, and no conformance row may cite it.
- It **does not close any item in [0007 §6.2](../decisions/0007-phase-8-hardware-validation.md)**.
  Those 19 items are Phase 8b and need the bench. This task proves the code that will run
  them is not itself broken.

**Files:**
- Create: `tests/hardware/test_elm327_acceptance.py`
- Create: `tests/hardware/bridged_elm327.py`
- Create: `tests/integration/test_elm327_acceptance_dry_run.py`

**Interfaces:**
- Consumes: the `DiagnosticTester` protocol and `AcceptanceCase` from Task 6's
  `tests/hardware/tester.py`; `Elm327` (Task 9); `FakeElm327`; `HardwareBench`.
- Produces: `ACCEPTANCE_CASES: tuple[AcceptanceCase, ...]` in
  `tests/hardware/acceptance_cases.py` — **the single registry both backends parameterise
  over**. Each case is written once against `DiagnosticTester` and never against a concrete
  adapter, which is what stops the bench and CI drifting apart.

Two thin collected modules share that registry and duplicate no logic:

- `tests/integration/test_elm327_simulated.py` — the fake dongle bridged to `vcan0`, marked
  `vcan`, collected in ordinary runs. This is the regression test for the harness.
- `tests/hardware/test_elm327_physical.py` — the real adapter from `hw_bench`, opt-in only.
  This is the one that can produce Phase 8b evidence.

Cases that cannot apply to a backend declare it, rather than being silently skipped: the
`AT RV` supply-voltage check is meaningless against a fake, and a case that a backend
cannot run is reported as not-applicable with its reason, never as a pass.

**Infrastructure limitation, measured 2026-09-24 and standing until it changes.** The
simulated backend **does not execute on GitHub-hosted runners**, for two independent
reasons:

1. CI installs `.[dev]`, not `.[dev,hardware]`, so `pytest.importorskip("serial")` skips
   the module before any fixture runs.
2. The runner kernel has no `CONFIG_CAN_ISOTP`, so the `vcan` fixture skips the whole of
   `tests/integration` regardless. The repository's own
   `scripts/probe_can_capabilities.py` job exists to record exactly this.

Installing the extra in CI would clear the first and **not** the second, so it would not
make these tests run. The kernel is the binding constraint and a GitHub-hosted runner
cannot load `can_isotp`.

> **Amended 2026-09-25.** Reason 1 no longer applies to the CI integration step. Since
> `68859fa` that step installs `.[dev,hardware]`, so the simulated backend there skips
> only for reason 2, and the annotation reports that reason by name. The unit jobs still
> install `.[dev]`, so reason 1 still applies there, to this module and to
> `tests/unit/test_elm327_serial.py`. Reason 2, and the consequence below, are unchanged.

**Consequence: a green CI run is not evidence that Task 12 passes.** It is evidence that
Task 12 was correctly *skipped*. The backend is verified locally, on a host with a vcan
interface and the `[hardware]` extra installed, and any phase report must record it as a
local result with its command, never as a CI result. Section 10 of the plan already
requires every skipped CI test to be recorded with its reason; this is one of them.

- [ ] **Step 1: Write the acceptance checks as data**

`tests/hardware/test_elm327_acceptance.py` covers [0007 §6.2](../decisions/0007-phase-8-hardware-validation.md)
items 1–18 (item 19, the `candump` capture, is a Phase 8b procedure, not a test). Each item
is one test function taking `elm`, so each is individually runnable and individually
reportable per §7.1 rule 4. The expected bytes are the ones the vcan suite already asserts
— §7.1 rule 3 forbids asserting anything new here.

- [ ] **Step 2: Write the bridged fake**

`tests/hardware/bridged_elm327.py` subclasses the pty responder, but instead of a lookup
table it: answers `AT I`, `AT @1`, `AT RV`, `AT DPN`, `AT SP 6`, `AT E0` and `AT SH xxx`
locally; forwards any hex command to an `isotp.socket` bound to the header set by the last
`AT SH` (default `0x7DF` functional, `0x7E0`/`0x7E1` physical); formats the reply in the
datasheet's single-line or multi-line form depending on length; and prints `NO DATA` when
the socket times out.

- [ ] **Step 3: Run the acceptance bodies against the simulator on vcan**

`tests/integration/test_elm327_acceptance_dry_run.py` starts the simulator on vcan,
starts the bridged fake, and runs `acceptance_checks`:

```bash
scripts/run_integration_tests.sh tests/integration/test_elm327_acceptance_dry_run.py -v
```

Expected: every check passes. A failure here is a bug in the acceptance suite, found for
free instead of on bench day.

- [ ] **Step 4: Confirm the real suite still refuses to run**

```bash
.venv/bin/python -m pytest --collect-only -q 2>&1 | grep -c "tests/hardware" || echo "0 — correct"
```

Expected: `0 — correct`.

- [ ] **Step 5: Commit**

```bash
git add tests/hardware/test_elm327_acceptance.py tests/hardware/bridged_elm327.py \
        tests/integration/test_elm327_acceptance_dry_run.py
git commit -F - <<'MSG'
test(hardware): ELM327 acceptance tests, exercised against a simulated dongle

Items 1 to 18 of decisions/0007 section 6.2, one test function each so that each
is individually runnable and individually reportable -- section 7.1 rule 4,
because a bench run is partly manual and a partial result must be recordable.

Item 19, the candump capture, is deliberately not a test. It is a Phase 8b
procedure and belongs in the testbench document.

No test here asserts a value the vcan suite does not already assert, per section
7.1 rule 3. The job of this suite is to show the same bytes survive real wire; a
new expectation belongs in the vcan suite first.

The vcan dry run is the point of the task, and it is an automated regression test
for this project's own ELM327 harness rather than an interoperability check.
Real-hardware interoperability was already demonstrated on 2026-09-23 against a
CANable and an OBDLink LX: 103,050 frames, zero errors, nine responses identical
to the vcan goldens, multi-frame flow control from the real adapter. See
docs/validation/phase-8-smoke-test/.

What that run did not do is exercise the Python harness, because the PC's own
Bluetooth never reached the adapter and the test was driven from an Android app.
So elm327_parser, Elm327 and these test bodies are the one part of Phase 8a that
Phase 8b will lean on while a bench is plugged in and expensive, and without this
task they would first execute on bench day. The bridged fake speaks the AT
commands locally and forwards hex requests to a real ISO-TP socket, so the same
test bodies run against the real simulator on every CI run, with no hardware.

This produces no `hardware validated` evidence and closes no item in 0007 section
6.2. A fake dongle on vcan shows our harness and our simulator agree; it says
nothing about any real adapter.
MSG
```

---

### Task 13: Phase 8a completion — status, conformance and the report

**Files:**
- Modify: `docs/conformance.md`
- Modify: `docs/modernization-plan.md` (Phase 8a marked complete)
- Create: `docs/validation/phase-8a-completion.md`

- [ ] **Step 1: Confirm no conformance row moved**

```bash
git diff master --stat -- docs/conformance.md
grep -c "| yes |" docs/conformance.md
grep -n "hardware validated" docs/conformance.md | head -5
```

Expected: **all 61 rows still read `hardware validated: no`.** The only permitted change to
this file is the note in Step 2. If any row changed, stop — Phase 8a cannot move that
column.

> **Amended 2026-09-25.** Two parts of this step were wrong as written:
> - `git diff master -- docs/conformance.md` cannot show whether a row moved, because the
>   file does not exist on `master` and the diff is the whole file. The meaningful check is
>   `git diff f607d73~1 -- docs/conformance.md`, the diff since this phase began.
> - The expected "61 rows" is the number of lines containing `| no |` in any column,
>   and `grep -c "| yes |"` counts matches in any column, too. Counting the
>   `hardware validated` cells gives 71 status rows: 48 `no`, 23 `n/a`, 0 `yes`.
>
> Task 13 used the corrected checks: see
> [validation/phase-8a-completion.md §6](../validation/phase-8a-completion.md).

- [ ] **Step 2: Add the note, not a status change**

Under the relevant tables, add free-text interoperability evidence per §11.8 — for example
"Interoperability evidence: the ELM327 response parser reproduces the two worked CAN
captures in ELM327DSJ page 45" — and **nothing in a status column.**

- [ ] **Step 3: Write the completion report**

Per [plan §10](../modernization-plan.md), it states: functionality implemented; behavior
directly tested; each of criteria 1–7, 14, 15 and the verification covering it; the exact
commands run and their exact counts; the regression result; ruff; mypy; the CI run and its
result; **every skipped test and its reason** — including the 53 integration tests CI skips
for want of `CONFIG_CAN_ISOTP` on the Azure kernel, and the `tests/unit/test_elm327_serial.py`
skips for want of the `[hardware]` extra; and, explicitly:

> **Criteria 8 to 13 are not verified. No bench exists. No conformance row gained
> `hardware validated`. Phase 8b is open and V1.0 is not tagged.**

- [ ] **Step 4: Full verification**

```bash
set -o pipefail
.venv/bin/python -m pytest 2>&1 | tail -5
scripts/run_integration_tests.sh 2>&1 | tail -3
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/python -m pytest --collect-only -q 2>&1 | tail -1
```

Expected: unit and characterization green with no unexpected XPASS; 53 integration passed;
ruff and mypy clean; no `tests/hardware` in the collection.

- [ ] **Step 5: Push and verify CI**

```bash
git push origin modernization
git rev-parse HEAD
```

Then poll — **the GitHub API here is unauthenticated at 60 requests per hour, and a bare
loop exhausts it in seconds**, after which everything returns 429 for the rest of the hour.
Pace any poll at 90 s or more, and use the **full 40-character SHA**: an abbreviated one
matches zero runs, which is indistinguishable from "not created yet".

```bash
curl -s "https://api.github.com/repos/aman-2709/ecu-simulator/actions/runs?head_sha=$(git rev-parse HEAD)"
```

- [ ] **Step 6: Commit**

```bash
git add docs/conformance.md docs/modernization-plan.md docs/validation/phase-8a-completion.md
git commit -m "docs: Phase 8a completion report, conformance notes, plan status"
```

---

## Self-review

**Spec coverage.** Criterion 1 → Tasks 3, 4, 5. Criterion 2 → Task 5. Criterion 3 →
Task 11. Criterion 4 → Task 6. Criterion 5 → Task 6 Step 7 and Task 12 Step 4.
Criterion 6 → Task 10. Criterion 7 → Task 11. Criterion 14 → Task 13 Step 1 and the
global constraint. Criterion 15 → Task 13 Step 4. 0008 §4.1 → Task 1. 0008 §4.2 → Task 2.
0008 §4.3 → Task 6. Q2's four sub-requirements → Tasks 7, 8, 9. Criteria 8–13 are Phase
8b and have no task here, which is the point of the split.

**Gaps deliberately left.** Gap 6 (`sample-point`) has no task: deferred by the Q4 ruling
with its unblocking condition stated. Gap 4 (`listen-only`) has no script task: it is
Task 11 documentation by the same ruling.

**Type consistency.** `parse_response(raw, sent)` and `clean(raw, sent)` take the same two
arguments everywhere; `Elm327Error.message` is used in Tasks 8 and 9; `Elm327.ask/at/command`
are named identically in Tasks 9 and 12; `HardwareBench` fields `can_iface`, `serial_port`,
`baud` are used in Tasks 6, 9 and 12; the environment variables `ECU_SIM_HW_BENCH`,
`ECU_SIM_HW_CAN_IFACE`, `ECU_SIM_HW_SERIAL`, `ECU_SIM_HW_BAUD` are spelled the same in
Tasks 6, 10, 11 and 13. `ECU_SIM_CAN_IFACE` (the existing integration variable, Task 10
Step 1 item 7) is a **different** variable from `ECU_SIM_HW_CAN_IFACE` and is not
interchangeable with it.

**Risk carried into Phase 8b.** Task 9's driver is the only component a real dongle could
disprove, which is why it is kept thin and why Task 12 regression-tests it against a fake
dongle on every run. The `hw_bench` pre-flight (Task 6) has never run against a real
interface; its `interface_index` and `is_interface_up` calls are the same ones the
integration conftest already uses on vcan, but its refusal path on a physical interface is
untested until 8b.

Note what the 2026-09-23 smoke test did **not** de-risk. It exercised the simulator over
physical CAN, not this harness: the PC's classic Bluetooth never reached the adapter, so
the run was driven from an Android app and no Python code in `tests/hardware/` executed.
**The `/dev/rfcomm*` path Phase 8a assumes remains unproven on this machine**, which is
recorded in [validation/phase-8-smoke-test §6](../validation/phase-8-smoke-test/README.md).
A USB ELM327 — which Phase 8b requires anyway — would sidestep it entirely.
