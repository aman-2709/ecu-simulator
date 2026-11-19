# Configure, run and query a simulated ECU

The operator can open the local browser GUI before an ECU exists, edit a shipped example, and test the resulting ECU over SocketCAN. Starting a run and keeping a profile are independent actions. The first release covers an existing, configured Linux CAN interface, including virtual CAN. DoIP belongs to a later phase.

## Approaches

The selected approach uses a long-lived local GUI manager and one simulator child per run. The manager keeps serving the editor after the child exits and can report a bind failure while the operator corrects the draft. This costs a small process-control protocol. Hosting the runtime in the GUI server's event loop would simplify startup communication, but would couple simulator failures and shutdown to the editor's process.

Build the control surface alongside the browser assets on the `gui` branch and use the profile and runtime behavior of `phase-12`. The manager owns control operations. The existing opt-in observer API can continue serving read-only clients independently. Verify their combined baseline before adding features. Existing observer performance and hardware acceptance gates retain their own criteria.

## Pinned integration baseline

| Local input | Full commit |
| --- | --- |
| Phase-12 tip at architecture approval (design commit) | `c21ffdf69b9a7b3fcd5fa251b03bce3b86293a87` |
| Phase-12 runtime, parent of that design-only commit | `f1d377daa2208154df02abcb6a0ff39d39f18ff2` |
| GUI and observer | `d0f1cc0eec87e0da893dba75b5ac6a2e6905481e` |
| Common ancestor | `77ca80b4c6dd078aaea29d6c05596f0f50fca5d7` |

The first implementation step is to combine the pinned trees in an isolated integration branch, record its resolved commit, and run the existing simulator and observer suites there, including their real kernel integration tests. Record passes, failures and skips against that commit. Resolve baseline regressions and verify on an ISO-TP-capable runner before feature work.

## Profile editor

The profile picker groups packaged examples and named user profiles. Selecting either loads its complete YAML into an editable working copy. The proposed first editor is a YAML text area plus an interface selector bound to `transport.interface`. Selecting an interface updates that field in the draft. If the YAML cannot be parsed, keep the text and show the error before applying a selector change. Prompt before discarding unsaved edits by changing profiles.

Start freezes a snapshot of the current draft. The manager passes it through the same YAML reader and schema checks as `config.load_profile`, including duplicate-key handling and field-path errors. Factor the common reader so it can accept text as well as a file. On validation failure the GUI stays editable and shows the diagnostic. On success pass the exact snapshot privately to the child through stdin and close the pipe. The child loads it through the same path. Starting creates no named profile. Further edits affect the next run. The running interface and endpoint choices come from the active snapshot.

**Save as** validates the draft and writes a named YAML file under the user's configuration directory (proposed default `$XDG_CONFIG_HOME/ecu-simulator/profiles`, falling back to `~/.config/ecu-simulator/profiles`). Accept a display name as a single filename component, reject path traversal and name collisions with an actionable error, and publish the complete file atomically. Only the user directory is writable through this operation. Discover saved profiles on the next launch and preserve the working copy on a save error.

## Child lifecycle

The manager serializes Start and Stop for one owned child. Expose Stopped, Starting, Running, Stopping and Failed to the browser. Start from Stopped or Failed. Cancel during Starting uses the same cleanup as Stop. Reconnecting a browser reads the manager's current state.

Assign each accepted Start a fresh opaque run ID, including after manager restart. Return it with Starting and expose it in status. Send and Stop must carry the ID of the run displayed when the operator acts. Under a manager-wide lock, compare it to the active ID before reserving an exchange, opening a socket, cancelling a request or signalling a child. Reject missing IDs as invalid and stale IDs as conflicts. On conflict refresh state and require a new operator action. Never replay an action against the replacement ID.

Use a private structured status pipe dedicated to the current child. After `app.run` has checked routes, awaited `transport.start` for every endpoint and installed its shutdown handlers, the child sends ready. Only this event for the current run moves Starting to Running. Associate notifications with the run so a late event from a stopped child cannot change the next run. Keep logs on their existing streams.

On startup exception send the cause, including the interface or endpoint when available. On early EOF, exit, or startup timeout show the best available diagnostic and reap the child. A partial bind must release every socket already opened. An unexpected exit after readiness also moves the UI to Failed. In either case keep all draft text and interface edits, disable Send and allow Start again after cleanup.

Stop disables Send, cancels the pending tester exchange, closes its socket, asks the child to terminate through the runtime's existing signal path, and waits for exit. Escalate and reap on a bounded shutdown timeout. Show Stopped once cleanup finishes. Shutting down the manager performs the same cleanup. Keep control and tester endpoints local to the machine and restrict browser mutations to the manager's own origin, including host checks for loopback access.

## UDS tester

While Running, the operator selects a physical-addressed endpoint that serves UDS in the active profile, enters a hex payload and clicks Send. Use a separate kernel ISO-TP socket on the selected interface. Transmit on the ECU's receive ID and receive on its transmit ID, honoring the endpoint's CAN identifier width. Use the same tester and decoder for virtual and physical interfaces. Interface creation, link setup and bitrate configuration remain host operations.

Allow one exchange in flight across all browser tabs. Reserve the slot atomically with run validation. A competing Send receives a busy conflict immediately. Release on completion, timeout, failure or cancellation, fenced by the owning run and exchange. Validate the input bytes before transmission, bound the response wait, and display the request and reassembled UDS response in hex. Alongside a positive `59 02` response, decode the availability mask and each four-byte record using this project's DTC representation. Show the trouble code, failure-type byte, status byte and set status flags. Derive the display entirely from received bytes. A header-only response means zero matches. Preserve raw bytes for unsupported decodes, negative responses and malformed payloads. For `3E 80`, retain the slot and listen until a response arrives or the normal deadline expires. Display a received negative response with its bytes and NRC. On deadline with silence, display exactly “No response received · positive response suppression requested.” This reports the observation without assuming the ECU received the request. Ordinary silence on `19 02 FF` reports a timeout. Transport errors and cancellation have their own outcomes. Tester errors leave the child state visible.

## Acceptance

1. Launch the GUI against an empty user profile directory on a host with kernel ISO-TP and an up `vcan0`. Select the shipped `ice_default` and choose `vcan0`. In the editor replace the engine's DTC list with a single P0001 record with `pending: false`, `confirmed: true` and `indicator_requested: false`. Leave the draft unsaved.
2. Click Start and wait for Running. Select `engine / uds_physical` (tester TX `0x7E1`, RX `0x7E9`), enter `19 02 FF`, and click Send. The same exchange must display **`59 02 8C 00 01 01 08`** and **P0001 · Confirmed**. Here `8C` is the availability mask, `00 01 01` identifies P0001 with failure type 01, and `08` is the record's status.
3. Click Stop. Verify Stopped, child exit, tester closure, draft retention and a successful restart on the same endpoints. Verify the packaged file is byte-identical and the user profile directory is still empty.
4. Exercise invalid YAML and a valid draft targeting an unavailable interface. Check the reason shown, absence of a Running transition, retained edits and successful retry after correction. Use controlled lifecycle tests for delayed readiness, partial bind, stop during startup and unexpected child exit.
5. Save the edited draft under a new name, relaunch the GUI, select the saved profile and repeat the exchange. Run browser acceptance against the real child and the kernel transport before closing implementation. Record any unavailable runner capability as unverified. On a configured physical bench repeat the same flow with `can0` and record the hardware result separately.

The YAML editor, profile storage location, private status pipe are proposed implementation choices. Exact HTTP routes, payload framing, timeout values and visual styling are left to implementation planning.

### Additional acceptance cases

- Repeat the unsaved `vcan0` run with confirmed-only P0001 followed by pending-only B1477, both with indicator off. On Classical CAN, `19 02 FF` must produce the complete 11-byte payload `59 02 8C 00 01 01 08 94 77 01 04` in the UI with two rows in wire order, P0001 · Confirmed and B1477 · Pending. Capture the first frame and consecutive frame on `0x7E9` and the tester's flow-control frame on `0x7E1` during this exchange.
- In two tabs, retain run A's ID, stop A and start B. Stale Send and Stop for A both fail before bus or lifecycle effects. During an exchange on B a simultaneous Send from another tab receives busy. Once the first finishes, B accepts the next exchange.
- Send `3E 80` to the running ECU and verify the exact suppression message at deadline. Exercise a delayed `7F 3E 22` with a controlled responder and verify the raw bytes and NRC take precedence over the silence message.
