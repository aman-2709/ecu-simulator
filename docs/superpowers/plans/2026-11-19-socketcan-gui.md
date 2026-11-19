# SocketCAN GUI implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement the tasks. Check each box after verification.

**Goal** — Edit a profile, start its ECU, exchange raw UDS bytes on SocketCAN, inspect decoded DTCs and stop the run from a local browser.

**Architecture** — A loopback manager owns a simulator child, its immutable launch snapshot and the tester exchange. A tab owns its editable draft. The child reports readiness on a private pipe. Both virtual and physical CAN use the same Linux ISO-TP tester.

**Tech stack** — Python 3.12+, asyncio, the existing YAML reader and profile schema, aiohttp from the GUI extra, repository-native HTML/JS, Linux SocketCAN, pytest and a real browser.

**Spec** — [Approved architecture and acceptance cases](../specs/2026-11-19-socketcan-gui-design.md)

## Global constraints

Shipped profiles are immutable. Starting requires no file save. Validate through the existing profile loader. Running requires all configured sockets to be open. Send and Stop target the displayed run ID. One exchange may be active across all tabs. V1 covers existing vcan0 and can0 interfaces, raw UDS requests and decoding 59 02. DoIP is deferred.

## Review focus

- A startup failure after the first bind releases the endpoints and preserves the draft (tasks 3 and 6).
- A stopped child's late notification or exchange cleanup cannot alter its replacement (tasks 3 and 4).
- Concurrent saves with the same name leave exactly one complete profile (task 2).
- A negative response just before a suppression deadline is visible to the operator (tasks 4 and 6).
- A truncated DTC record keeps its original bytes and produces a decode error (task 4).

## 1. Verify the combined baseline before feature work

- [ ] In an isolated worktree branch from local phase-12 `c21ffdf69b9a7b3fcd5fa251b03bce3b86293a87` (runtime `f1d377daa2208154df02abcb6a0ff39d39f18ff2`) and merge GUI `d0f1cc0eec87e0da893dba75b5ac6a2e6905481e`. Confirm `git merge-base` returns `77ca80b4c6dd078aaea29d6c05596f0f50fca5d7`. Carry this approved spec and plan forward as documentation. Record the actual merged code SHA.
- [ ] Review the four paths changed on both lines: `src/ecu_simulator/app.py`, `src/ecu_simulator/cli.py`, `tests/integration/conftest.py` and `docs/modernization-plan.md`. Preserve phase-12 runtime behavior, observer hooks and both lines' test fixtures. Verify both pinned tips are ancestors with `git merge-base --is-ancestor` for each.
- [ ] In the worktree environment install the repository's dev, gui and hardware test extras. Run `python -m pytest tests/unit tests/characterization -ra` with Node available for GUI checks, then `bash scripts/run_integration_tests.sh -ra` with kernel ISO-TP. Confirm the observer unit/API suites and `tests/integration/test_api_vcan.py` ran alongside the simulator suites. Repeat on an equipped runner if required cases skipped.
- [ ] Record SHA, commands, counts and reasons for skips. Resolve integration regressions and rerun both suites before starting task 2. Keep the existing observer latency gate and physical bench results separately identified.

## 2. Validate drafts and save named profiles

- [ ] Add failing `tests/unit/test_gui_profiles.py` cases for text/file validation parity (invalid syntax, duplicate keys and invalid field paths), interface changes preserving unrelated draft content, packaged profile immutability, path traversal, round-trip named saves and two simultaneous creates of the same name. Run `python -m pytest tests/unit/test_gui_profiles.py` and confirm failure on the missing interfaces.
- [ ] In `config/loader.py` and `config/__init__.py` under `src/ecu_simulator/`, share parsing through `load_profile_text(text: str, source: str) -> Profile`. Retain existing path-based errors and the safe YAML reader. Add `gui/profiles.py` for listing, reading, validated create-only saves and updating the draft interface. Keep the user's YAML formatting and comments outside the edited field. Safely publish complete profiles in the XDG directory from the spec.
- [ ] Rerun the new tests and `python -m pytest tests/unit/test_config_schema.py tests/characterization/test_config_golden.py`. Commit the verified profile changes.

## 3. Supervise the child and identify each run

- [ ] Add `tests/unit/test_gui_manager.py`. With controllable child events, verify Starting before ready, Failed with a reason on startup error, rejection of concurrent Start, Stop during Starting, unexpected exit, timeout escalation, and a successful retry after partial bind. Deliver an old ready/exit after starting a replacement and assert its state and ID are intact. Run the file and confirm the tests fail before implementation.
- [ ] Extend `app.run` in `app.py` with an optional readiness callback after successful transport startup and signal-handler installation. Preserve CLI and observer behavior. Add `gui/child.py` to read YAML from stdin, revalidate, start the existing runtime and write newline-delimited ready/error JSON on a dedicated inherited descriptor. Close unused pipe ends in both processes.
- [ ] In `gui/manager.py`, implement async `start(text)`, `stop(run_id)` and a status snapshot containing state, run ID, active interface, eligible endpoints and error. Mint a UUID for each accepted Start. Serialize run-state transitions, scope every callback to its child, retain bounded diagnostics, and reap before permitting retry. Stop claims Stopping under the lock, then cancels and awaits any reserved exchange and terminates/reaps its child outside the lock. Use bounded startup and shutdown timers injectable in tests.
- [ ] Run `python -m pytest tests/unit/test_gui_manager.py tests/unit/test_app.py tests/unit/api/test_run_with_api.py`. Commit once the lifecycle and existing CLI/observer tests pass.

## 4. Exchange and decode real tester payloads

- [ ] Add `tests/unit/test_gui_tester.py`. Drive a controlled socket and deadline. Assert complete single/multiple DTC records, header-only zero matches, malformed record tail, raw unknown responses, negative responses, ordinary timeout and send errors. For 3E 80 assert the exchange is pending before the deadline, a late `7F 3E 22` wins, and silence through the deadline returns the exact text in task 6. Run the new suite to confirm failure.
- [ ] Add `gui/tester.py` with an async exchange accepting the active interface, selected endpoint, request bytes and a monotonic deadline. Reuse `transport/socketcan/isotp.py` with tester TX/RX reversed from the ECU perspective. Let the kernel handle flow control and segmentation. Validate hex and endpoint eligibility before opening the socket. Return request, response bytes if received, and a distinct result for suppression silence, timeout, transport failure or cancellation. Recognize suppression only for services with a subfunction using the runtime's existing service classification.
- [ ] Decode complete `59 02` records from received bytes using the DTC group/type mapping in `dtc/codes.py` and status flags in `protocols/uds/dtc.py`. Return the availability mask and ordered records including failure type and raw status. Keep the raw payload on every decode outcome.
- [ ] Extend manager tests with two simultaneous sends, stale Send and Stop after restart, and Stop during a pending exchange. Assert the losing requests cause zero socket writes, cancellations or signals. Implement `send(run_id, endpoint, payload)` with one manager-wide reservation acquired atomically with ID/state validation. Avoid holding the state lock during I/O. Release by owner token in cleanup. Run tester and manager suites and commit.

## 5. Connect the editor and controls to the manager

- [ ] Add HTTP contract tests in `tests/unit/api/test_gui_control.py` for profile listing/loading/saving, draft interface changes, status, Start, Send and Stop. Test missing ID (400), stale ID and busy (409 with distinct reasons), wrong Origin and invalid Host. Run the suite and confirm it fails before adding routes.
- [ ] Add a separate `ecu-simulator-gui` entry point in `pyproject.toml` and local aiohttp application in `gui/server.py`. Serve GET /profiles, /profiles/{source}/{name} and /run. Use POST /profiles for Save as, /draft/interface for edits, /run for Start, /run/{id}/send and /run/{id}/stop for run-scoped actions. Reject ID-less mutations on the send/stop routes. Reuse `api/options.py` loopback and Host rules and validate Origin on mutations. Bind a single manager to the application lifetime.
- [ ] Serve the manager screen from new `api/static/control.html` and `control.js` using `app.css`. Show grouped profiles, editable YAML, interface selector, Save as, Start/Stop, status/error, eligible endpoint, hex input and raw/decoded result. Preserve draft on errors and stop. Confirm before replacing dirty text. Keep the displayed run ID with each action and result, refresh on conflict and require another click. Polling status must preserve drafts and must never retarget an already-issued action. Disable Send until Running.
- [ ] Verify HTTP tests, existing frontend asset tests and a browser smoke test of draft validation, then commit. Preserve the existing read-only observer entry point.

## 6. Prove the operator workflow in the browser

- [ ] Add a browser-driven pytest suite in `tests/integration/test_gui_control_vcan.py` and document its test-only browser dependency and launch command. Start the actual manager, child and browser with an isolated XDG directory on the same CAN-enabled host. Run the acceptance suite with `bash scripts/run_integration_tests.sh -k gui_control_vcan -ra` on a runner with loopback up inside the test namespace (update the script to bring up lo if needed). Check a real browser executed every case.
- [ ] From unsaved edits to ice_default use engine.uds_physical on vcan0. For confirmed-only P0001, assert `19 02 FF` yields `59 02 8C 00 01 01 08` beside P0001 · Confirmed. Stop from the UI, verify child exit, restart, no named save and identical shipped bytes.
- [ ] Add pending-only B1477 after P0001. Assert the complete displayed payload is `59 02 8C 00 01 01 08 94 77 01 04` and the two ordered decoded rows have statuses 08 and 04. Capture CAN_RAW on Classical vcan0 before clicking Send. Require an 11-byte first frame on 0x7E9, flow-control Continue To Send on 0x7E1 and a consecutive frame on 0x7E9, in that order.
- [ ] For `3E 80` assert silence produces exactly “No response received · positive response suppression requested.” Verify that a controlled negative reply produces its raw bytes and NRC. Use two browser tabs to exercise stale Send/Stop against a replacement and busy while the other tab awaits suppression. Verify the original exchange finishes and the active run accepts another request.
- [ ] Verify retained edits and retry after invalid YAML and missing-interface startup failure. Save as a named profile, restart the manager, select the saved copy and repeat the request. Run the full baseline suites plus browser acceptance and record SHA, counts and wire evidence before closing software acceptance. Repeat the same tester flow on an available configured can0 bench, recording physical results independently. Commit the acceptance tests and results.
