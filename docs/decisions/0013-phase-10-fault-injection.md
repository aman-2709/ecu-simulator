# Decision 0013: Phase 10 deterministic fault injection

- Status: accepted
- Date: 2026-11-18
- Scope: Phase 10 only

## Decision

Each ECU may have an ordered list of fault rules. No rules means no `FaultPolicy` effects
and preserves every existing byte and timing path. A rule matches an optional request SID
and addressing kind, and triggers on either an explicit set of one-based matching-request
occurrences or `always`. There is no randomness, wall-clock trigger, expression language,
or scenario coupling.

Every matching rule advances its own occurrence counter on a request. If several rules
trigger together, the first configured rule is applied. This makes a profile reproducible
and keeps ordering explicit.

`FaultPolicy` wraps the ECU's ordinary synchronous handler:

| Effect | Pipeline semantics |
|---|---|
| `drop` | Run the ordinary handler and its state changes, then discard its response. |
| `delay` | Run the handler and delay its response by the configured milliseconds. |
| `nrc` | Run the handler, then replace any response with `7F <request SID> <configured NRC>`. |
| `response_pending` | Run the handler; when it produces a final response, send `7F <SID> 78` immediately and that ordinary final response after the configured delay. If the handler is silent, stay silent rather than promise a final response that does not exist. |
| `truncate` | Run the handler, then remove the configured positive number of bytes from the end while retaining at least one byte. |
| `corrupt` | Run the handler, then XOR one byte with a non-zero mask. The configured offset wraps modulo the response length so the fault always changes a byte. |
| `offline` | Do not call the ordinary handler and send nothing. No protocol or ECU state change occurs. |

Except for `offline`, a rule that needs an ordinary response remains silent when the
ordinary handler is silent. Fault injection does not turn an unsupported or deliberately
suppressed response into a positive one accidentally. `nrc` is the one explicit override:
it may manufacture its configured negative response after processing the request.

The policy is in `faults/`, has no socket or event-loop dependency, and returns the same
transport-owned `DiagnosticResponse` type as the ordinary ECU path. `DiagnosticResponse`
may carry additional scheduled responses; every delay is measured from completion of the
synchronous handler. The transport uses event-loop timers, never sleeps in an ECU or
protocol, preserves response order, and cancels timers during shutdown.

The SocketCAN transport exposes optional observation hooks for two transport outcomes:

- intentional silence when the handler returns no response, including `drop`/`offline`;
- a `FlowControlTimeoutError` when the peer ignores the flow control needed to finish a
  multi-frame response.

The Linux kernel owns ISO-TP flow control. Phase 10 does not parse raw flow-control frames
or pretend the ECU pipeline can switch kernel FC handling per request. The hook reports
the kernel's `ECOMM` outcome and lets tests or observers prove that the runtime remains
usable afterward.

## Profile shape

Rules live on the ECU they affect:

```yaml
ecus:
  engine:
    faults:
      - effect: drop
        sid: 0x01
        occurrences: [1]
      - effect: response_pending
        sid: 0x10
        occurrences: always
        final_delay_ms: 50
```

`sid` is optional and defaults to all SIDs. `addressing` is `any` by default and may be
`physical` or `functional`. Explicit occurrence lists contain unique positive integers.
Effect-specific fields are rejected on other effect types by the profile's existing
`extra="forbid"` rule.

The shipped default and multi-ECU profiles configure no faults. A separate demonstration
profile exercises all effects without changing a release-default path.

## Evidence and standard boundary

- ISO's catalogue identifies ISO 14229-1:2026, Edition 4, as the current published UDS
  application-layer standard: <https://www.iso.org/standard/87962.html>. Its normative
  text is licensed and was not used; Phase 10 is not marked standards validated.
- AUTOSAR CP R25-11 Dcm is the current public secondary specification used for the one
  wire value this phase needs. It identifies NRC `0x78` as ResponsePending, describes an
  immediate pending response followed by a required final positive or negative response,
  and permits limiting repeated pending responses (`[SWS_Dcm_00119]`,
  `[SWS_Dcm_01567]`):
  <https://www.autosar.org/fileadmin/standards/R25-11/CP/AUTOSAR_CP_SWS_DiagnosticCommunicationManager.pdf>.
- Linux's ISO-TP documentation states that multi-frame transmission waits for flow
  control and reports `ECOMM` when flow-control reception times out:
  <https://docs.kernel.org/networking/iso15765-2.html>.
- Linux's public UAPI also names the relevant testing flags and the FC-timeout interface:
  <https://github.com/torvalds/linux/blob/master/include/uapi/linux/can/isotp.h>.

Drop, delay, corruption, truncation, and offline behavior are simulator test features,
not claims about ISO-defined ECU behavior.

## Acceptance

Phase 10 closes only when the same final commit passes:

1. unit tests for every effect, rule ordering, occurrence counting, default-off behavior,
   stateful `drop` versus bypassing `offline`, and invalid configuration;
2. real `vcan`/kernel `CAN_ISOTP` tests for drop/silence plus recovery, delay, NRC
   override, response-pending then final, truncation, corruption, and offline behavior;
3. a raw-CAN tester that withholds flow control, observes the first frame and transport
   timeout, then proves the simulator still answers a new request;
4. clean shutdown with scheduled responses pending and no leaked timers or descriptors;
5. the full regression suite, Ruff, mypy, and all required PR CI jobs.

No physical hardware is required. Phase 8b remains the held hardware gate.
