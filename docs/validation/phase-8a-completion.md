# Phase 8a — Completion Report

**Result: complete, with one Definition-of-Done line not met as written (§4.2). Accepting
it is the owner's decision.** Every acceptance criterion assigned to Phase 8a (1–7, 14, 15
of [decisions/0007 §9](../decisions/0007-phase-8-hardware-validation.md)) is met and
verified without hardware.

> **Criteria 8 to 13 are not verified. No Phase 8b bench run has taken place. No
> conformance row gained `hardware validated`. Phase 8b is open and V1.0 is not tagged.**

| | |
|---|---|
| Phase | 8a — Hardware-test preparation, no bench required (V1.0) |
| Branch | `modernization` |
| Revision under test | **`8f17b9e9c4a804f26180691cb7d31e0b54e551f4`** (`8f17b9e`). This report and its status notes are committed on top, documentation only |
| Phase 8a commits | `f607d73` … `8f17b9e` (§8) |
| Automated result, this host | **1026 passed, 2 xfailed**, 0 XPASS; integration **68 passed**; ruff and mypy clean |
| CI | run **36191480055** on `8f17b9e`, **success**, 4/4 jobs |
| What CI actually executed | **937** of the 1028 tests in the default collection. **89 were skipped** (§5) |
| `hardware validated` after this phase | **no row reads `yes`**. 71 status rows: 48 `no`, 23 `n/a` (§6) |
| `standards validated` after this phase | **no row reads `yes`**. 51 `no`, 20 `n/a` |

## 1. Functionality delivered

- **`setup_can.sh`**: automatic bus-off recovery (`restart-ms`, default 100 ms;
  `CAN_RESTART_MS=0` explicitly disables it), link statistics before and after,
  guarded termination (`CAN_TERMINATION`, skipped with a warning where the controller
  lacks it), and a warning, not a rejection, for a bitrate other than 500000 or 250000.
- **Bitrate guidance** consistent across README, the script's usage text and the
  testbench record. There is no `ecu-simulator --bitrate`, by design.
- **`docs/hardware-testbench.md`**, populated from the 2026-09-23 smoke-test evidence,
  with troubleshooting (§6.0 to §6.7).
- **`tests/hardware`**: an opt-in suite that is invisible to a default run. It refuses to
  collect without `ECU_SIM_HW_BENCH=1` and both devices named, and refuses any `vcan*`
  interface. It also refuses a bus that already has an OBD responder.
- **pyserial as an optional `[hardware]` extra**, an ELM327 response parser tested against
  exchanges recorded in ELM327DSJ, and a thin serial driver tested over a
  pseudo-terminal.
- **Task 12 acceptance cases**, written once against a `DiagnosticTester` protocol and
  run by two backends: *simulated* (a pty-backed fake ELM327 bridged to the kernel ISO-TP
  path on vcan) and *physical* (a real adapter on the bench).
- **Three corrections to the integration suite** required by
  [0008 §4](../decisions/0008-phase-8-question-resolutions.md): a DTC-clearing test that
  runs on its own (Task 1), silence proved by a known-good follow-up request (Task 2), and
  bench isolation (Task 6).
- **Added during Task 12 acceptance, 2026-09-25:** the physical backend now owns the
  simulator's lifecycle (`6fd5132`, §7.1).

**No production code changed.** `git diff f607d73~1..8f17b9e -- src/` is empty, and so is
the same diff over `tests/characterization/`.

## 2. Commands and results

All run at `8f17b9e` on this host: kernel `6.8.0-138-generic`, Python 3.12.12, host
`vcan0` up, `.[dev,hardware]` installed.

```bash
set -o pipefail
.venv/bin/python -m pytest                          # 1026 passed, 2 xfailed, 0 XPASS
scripts/run_integration_tests.sh                    # 68 passed (private namespace)
scripts/run_integration_tests.sh -k "acceptance_case or registry_is_not_empty"
                                                    # 15 passed, 53 deselected
.venv/bin/ruff check .                              # All checks passed!
.venv/bin/mypy                                      # no issues found in 45 source files
.venv/bin/python -m pytest --collect-only -q        # 1028 collected; 0 under tests/hardware
.venv/bin/python -m pytest tests/unit/test_setup_can_script.py \
  tests/unit/test_hardware_suite_is_opt_in.py tests/unit/test_hardware_extra_is_optional.py \
  tests/unit/test_hardware_bench_lifecycle.py tests/unit/test_elm327_parser.py \
  tests/unit/test_elm327_serial.py                  # 104 passed (34+8+7+7+27+21)
```

**Physical backend, end to end, without hardware.** In a namespace, `tests/hardware` ran
with `ECU_SIM_HW_CAN_IFACE=cantest9` (a vcan-type interface deliberately given a
non-`vcan` name) and the `BridgedElm327` fake as the serial device. No simulator was
started by hand: **17 passed, 1 failed**. The failure is `supply-voltage`: the fake
answers `AT RV` with `?` because it has no supply, which is why that case is registered
as physical-only. With a simulator started by hand, the suite refuses the bench, and an
exact process count confirms it starts no second one. **This proves the harness and its
lifecycle, not any hardware.**

The expected failures are unchanged: `DEV-11` (Mode 07) and `DEV-03` (Mode 09 PID 0A),
both strict.

## 3. Acceptance criteria

| # | Criterion | Status | Verification |
|---|---|---|---|
| 1 | `setup_can.sh` handles bitrate; accepted gaps from 0007 §5.1 implemented | **Met** | `tests/unit/test_setup_can_script.py`, 34 tests against a mock `ip` on `PATH`, interface `cantest9`, never `can0`. Gaps 1, 2, 3 and 5 are Tasks 3–5. Gap 6 (`sample-point`) is deferred by the 0008 Q4 ruling; gap 4 (`listen-only`) is documentation only (§6.7) by the same ruling. The vcan suite shows no regression: 68 passed |
| 2 | Warns on a non-OBD bitrate | **Met** | Same file: the warning's text and exit status on direct invocation |
| 3 | Bitrate guidance present and consistent | **Met** | Review of README 113–119, `scripts/setup_can.sh` 4–19 and `docs/hardware-testbench.md` §3. All three say 500000/250000 and that there is no `--bitrate` |
| 4 | `tests/hardware` exists, is opt-in, skips with a reason without a bench | **Met** | `tests/unit/test_hardware_suite_is_opt_in.py` (8 tests, with and without the marker and the environment variables, in a subprocess). The `hw_bench` fixture skips with a reason when the interface is missing or down |
| 5 | `tests/hardware` never collected by a default run | **Met** | `pytest --collect-only`: 0 items under `tests/hardware`. The default collection grew during 8a only through new tests in `tests/unit` and `tests/integration` |
| 6 | Testbench record carries every 0007 §4.4 field | **Met** | Review: make and model, interface and connection, driver, firmware, `AT I` / `AT @1`, `ip -details link show`, kernel and distribution, can-utils version, date and commit are all present. Serial numbers are withheld (§5 of that document) |
| 7 | Troubleshooting covers the six cases in 0007 §8 | **Met** | Sections 6.1 to 6.6, one per case, plus §6.7 `listen-only` from 0008 |
| 8 | Every 0007 §6.2 test passes against a real ELM327 over physical CAN | **Not verified** | No USB ELM327 bench run. `tests/hardware` has never executed against a real adapter. Phase 8b |
| 9 | Bluetooth acceptance (optional for V1.0) | **Not verified** | The Python harness has never driven the LX: `/dev/rfcomm*` has never been bound on this host. The phone-driven evidence in §6.1 is interoperability evidence, not this criterion |
| 10 | 250 kbit/s | **Not attempted** | Phase 8b |
| 11 | `candump` confirms the bytes for a representative subset | **Not verified as a criterion** | Captures from two phone-driven runs are archived (§6.1), but they are not the Phase 8b bench run |
| 12 | Rows exercised on hardware gain `hardware validated: yes`, and no others | **Not verified** | No bench run, so no row may move, and none did |
| 13 | No row gained `standards validated` | **Holds at `8f17b9e`** | 0 rows read `yes`. Formally a Phase 8b criterion, re-checked there |
| 14 | No wire behavior changed | **Met** | No file under `src/` or `tests/characterization/` changed; full suite unchanged apart from added tests. No protocol file was touched, so no differential comparison was required |
| 15 | Full regression, CI green | **Met, with the §5 caveat** | §2 and §4.1. CI green on `8f17b9e`, but CI executes 937 of 1028 tests |

## 4. Definition of Done (modernization-plan.md, Phase 8a)

| DoD line | Status |
|---|---|
| Criteria 1–7, 14, 15 met and verified without hardware | **Met** (§3) |
| A default `pytest` collects the same number as before | **Met in substance**: `tests/hardware` contributes 0; every change in the count is a new test in the ordinary suites |
| `tests/hardware` refuses without an explicit opt-in naming both devices, and refuses `vcan*` | **Met** (criterion 4) |
| ELM327 parser tested byte for byte against ELM327DSJ exchanges | **Met**: 27 tests, including both worked CAN captures on page 45 |
| **ELM327 harness exercised end to end against a fake dongle on vcan in an ordinary CI run** | **Not met as written.** See §4.2 |
| Completion report states no row gained `hardware validated` and V1.0 is not tagged | **Met** (this document) |

### 4.1 CI

Run **36191480055**, `head_sha` `8f17b9e9c4a804f26180691cb7d31e0b54e551f4`, conclusion
**success**:

| Job | Result | Annotation |
|---|---|---|
| Lint and type check | success | — |
| Unit and characterization tests (Python 3.12) | success | `937 passed, 55 skipped, 2 xfailed`; coverage `TOTAL 1968 52 97%` |
| Unit and characterization tests (Python 3.13) | success | same |
| Probe vcan and can_isotp on the runner | success | `vcan integration tests: 1 passed, 67 skipped \| skip reason: kernel cannot create CAN_ISOTP sockets`; runner kernel `6.17.0-1022-azure`; ISO-TP experiments skipped, socket cannot bind |

### 4.2 The DoD line CI cannot meet

The harness runs end to end against a fake dongle on vcan **on this host**: 15 simulated
cases and 17 physical-backend cases, §2. **It does not run in CI**, and a green CI run is
evidence that it was *skipped*, not that it passed. GitHub-hosted runners boot the
`linux-azure` kernel flavour, which has no `can_isotp`. Installing the `[hardware]` extra
in CI (`68859fa`) removed the second, independent reason for skipping, so the job now
reports only the kernel cause. The line cannot be met on hosted runners at all.
[decisions/0009](../decisions/0009-self-hosted-vcan-runner.md) proposes a runner that can
meet it; that proposal is not implemented. The owner decides whether Phase 8a closes with
this line recorded as unmet, or waits for 0009.

## 5. Tests CI does not execute, and why

| Job | Tests | Reason |
|---|---|---|
| Unit (3.12 and 3.13) | **53** in `tests/integration` (all but the simulated-ELM327 file) | `vcan` fixture: *kernel cannot create CAN_ISOTP sockets* |
| Unit (3.12 and 3.13) | **21** in `tests/unit/test_elm327_serial.py` | Module-level `importorskip("serial")`: the job installs `.[dev]` only. **pytest reports this as 1 skip** |
| Unit (3.12 and 3.13) | **15** in `tests/integration/test_elm327_simulated.py` | Same, reported as 1 skip |
| Probe job, integration step | 67 of 68 | *kernel cannot create CAN_ISOTP sockets*. The one pass is `test_the_registry_is_not_empty`, which touches no CAN |
| Probe job, ISO-TP experiments | all | the CAN_ISOTP socket cannot bind |
| All jobs | `tests/hardware`, 18 | Never collected, by design (criterion 5) |

So "55 skipped" means **89 tests not executed**: 1028 − 937 − 2 xfailed. Every one of
them passed on this host (§2).

## 6. Conformance

No status cell changed. Diffing `docs/conformance.md` over the phase shows only the
interoperability-evidence prose added under the Transport and OBD-II tables (§11.8 of
the plan), with no table row touched.

**The "61 rows" figure was wrong.** 0007, 0008, the Phase 7 report, the smoke-test record,
this phase's plan and, until this commit, the testbench record and the 2026-09-25 LX
report all said "all 61 rows read `hardware validated: no`". 61 is the number of lines in
`conformance.md` containing `| no |` **in any column**. Counting the `hardware validated`
cells gives **71 status rows: 48 `no`, 23 `n/a`, 0 `yes`**, and the table has not changed
since Phase 7 (`a4d19ca`). The substantive claim, that nothing is hardware validated, was
always true. The two living documents are corrected. The dated records are left as they
were written.

### 6.1 Hardware interoperability evidence, recorded and not acceptance

| Run | What drove the adapter | What it shows |
|---|---|---|
| [2026-09-23 smoke test](phase-8-smoke-test/README.md), `f607d73` | OBDLink LX, Android app, Bluetooth | 103,050 frames, 0 errors; nine responses byte-identical to the vcan goldens |
| [2026-09-25 LX run](phase-8-lx-bluetooth-2026-09-25/README.md), `68859fa` | OBDLink LX, Android app, Bluetooth | 12,093 frames, 0 errors, median reply 0.51 ms. Against the 16 Task 12 cases: **5 confirmed byte-for-byte, 2 consistent with the capture, 9 not exercised** |

Neither run executed `tests/hardware`, drove the adapter from Python, or used a USB
ELM327. **The Python-to-LX hardware run and mandatory USB ELM327 acceptance stay open for
Phase 8b.**

## 7. Defects and unexpected behavior found during the phase

1. **The physical backend could not pass on hardware** (fixed, `6fd5132`). Its
   documented procedure started the simulator by hand, and the bench check then reported
   that simulator as a foreign responder, so all 18 tests errored before a byte reached
   the adapter. The suite now checks the bus, then starts its own simulator, re-checks
   before every restart, and always stops it. Seven fake-driven unit tests pin the order.
2. **The CI annotation masked the CAN_ISOTP reason** (fixed, `68859fa`). It reported the
   first `SKIPPED` line, which after `2faf891` was the missing-pyserial skip.
3. **`FORCE_COLOR` would silently empty the annotation** (hardened, `f6b1fb7`). Found by
   a second Claude session working on this branch. It is not a live failure, because CI
   does not set the variable.
4. **The "61 rows" miscount** (§6).
5. **A miscount in conversation**: the LX run was first reported as 6/2/8. The committed
   report gives 5/2/9 and records the correction.
6. **0009 first excluded Azure as a cloud provider** (corrected, `8f17b9e`). The evidence
   concerns only the `linux-azure` kernel flavour.
7. **Stale plan text, not changed here.** Task 12's CI-limitation amendment gives "CI
   installs `.[dev]`" as one of two reasons the simulated backend skips in CI. The
   integration job has installed `[hardware]` since `68859fa`, so only the kernel reason
   remains. Task 13 Step 1's `git diff master -- docs/conformance.md` is not a useful
   check, because that file does not exist on `master`. §6 uses the phase-start diff
   instead.
8. **Observed, not fixed:** `scripts/setup_can.sh --help` treats `--help` as an interface
   name and exits with *"interface '--help' does not exist"*. It exits before touching
   anything.
9. **Observed, not fixed:** `auto-search-finds-protocol-6` passed against the fake in the
   §2 end-to-end run. The registry already marks it physical-only because "passing would
   prove nothing" against the fake, so the pass carries no weight.

## 8. Phase 8a commits

| Commit | Task |
|---|---|
| `f607d73` | 0008: questions resolved, phase split |
| `a21f845`, `718f774`, `70239e6` | `.gitignore` fix, smoke-test evidence, Task 12 made mandatory |
| `6749838` | Task 1: DTC-clearing test independently runnable |
| `7484ea9` | Task 2: silence proved with a follow-up request |
| `1e0fe76`, `1db591d` | Task 3: `restart-ms` and statistics; `CAN_RESTART_MS=0` fix |
| `2398f47` | Task 4: guarded termination |
| `9b3f409` | Task 5: non-OBD bitrate warning |
| `6f4476b`, `9c143b9` | Task 6: opt-in collection and bench isolation; `sys.executable` fix |
| `2a581c7` | Task 7: `[hardware]` extra |
| `a634556` | Task 8: ELM327 response parser |
| `6660ba2` | Task 9: ELM327 serial driver |
| `46503e7` | Task 10: testbench record |
| `53f73aa` | Task 11: troubleshooting and bitrate guidance |
| `2faf891` | Task 12: acceptance cases, two backends |
| `68859fa`, `f6b1fb7` | CI: `[hardware]` installed, skip reason by name, `--color=no` |
| `48413a3` | 2026-09-25 LX Bluetooth evidence |
| `22aa1e1`, `8f17b9e` | 0009 proposal and its Azure correction |
| `6fd5132` | Physical backend owns the simulator lifecycle |

## 9. Not tested, and why

- Anything on a real ELM327 driven by this harness, over USB or Bluetooth: there is no
  USB adapter, and the PC's Bluetooth has never reached the LX (smoke test §6).
- 250 kbit/s, and every other Phase 8b bench item.
- The vcan suites in CI: the hosted kernel has no `can_isotp` (§4.2).
