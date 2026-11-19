# Decision 0014: Phase 12 UDS data and mock security

- Status: accepted
- Date: 2026-11-18
- Scope: Phase 12 only

## Decision

Phase 12 adds UDS `0x27` SecurityAccess and `0x2E` WriteDataByIdentifier without
representing the simulator as a secure ECU. One `UdsSecurity` object belongs to each ECU
and is shared by that ECU's UDS endpoints, matching the per-ECU session scope chosen in
[Decision 0011](0011-phase-11-uds-session-handling.md).

### Mock SecurityAccess

The simulator supports one configured security level. Level 1 uses request-seed
subfunction `0x01` and send-key subfunction `0x02`. The mock challenge is intentionally
public and deterministic:

```text
seed = 12 34 56 78
key  = seed XOR A5 A5 A5 A5 = B7 91 F3 DD
```

This is a test-fixture handshake, not cryptography. The constant seed, mask, and expected
key are public API and are not configurable, so a profile cannot make the strategy look
OEM-specific. A seed request while the level is already active returns four zero bytes.
A key without a preceding seed is NRC `0x24`; a wrong key is NRC `0x35`. Attempt limits,
delay timers, NRCs `0x36`/`0x37`, persistence across process restarts, and any OEM
algorithm are out of scope.

The security level starts locked (`0x00`). It returns to locked when S3 returns the ECU to
the default session, a restarting ECUReset occurs, or a transition from a non-default
session is requested—including re-entering that same session. A successful writable-DID
value remains in the in-memory DID store across session changes and ECUReset, but returns
to its profile value when the simulator process restarts.

### Access checks and NRCs

The checks are ordered and intentionally observable:

| Request state | Response |
|---|---|
| `0x27` or `0x2E` in the default session | NRC `0x7F`, serviceNotSupportedInActiveSession |
| Configured `0x27` level in another non-default session not allowed for that level | NRC `0x7E`, subFunctionNotSupportedInActiveSession |
| Unsupported `0x27` subfunction | NRC `0x12` |
| `0x2E` DID absent, read-only, or not writable in the active session | NRC `0x31` |
| `0x2E` DID requires a security level that is not active | NRC `0x33` |
| `0x2E` data length differs from that DID's fixed configured length | NRC `0x13` |

Both services are available only in a configured non-default session. The configured
mock security level defaults to being unlockable in extended session `0x03`; a profile
that omits `0x03` has no default unlock session. Write permissions name their sessions
explicitly and may require the configured mock level or `null` for an intentionally
unlocked write.

`0x2E` accepts exactly one two-byte DID and its data. A successful response is `6E` plus
the echoed DID. Reads through `0x22` immediately observe the new bytes.

### Profile DIDs and richer powertrains

The Phase 11 shorthand remains valid:

```yaml
dids:
  "F1A0": "01 02"
```

It means a fixed, read-only byte string. Phase 12 adds two explicit forms:

```yaml
dids:
  "F1A0":
    kind: static
    value: "01 02"
    write:
      sessions: [0x03]
      security_level: 1
  "F1A1":
    kind: signal
    signal: engine.rpm
    encoding: uint16_be
```

Static values may be writable and keep a fixed byte length. Signal DIDs are read-only
views of `VehicleState`. Their encoding is explicit—`boolean`, `uint8`, `uint16_be`,
`uint32_be`, `int16_be`, or `float32_be`—rather than inferred from a Python value.
Profiles are rejected before sockets open when a path does not exist for that powertrain,
the encoding is incompatible with its type, a write names an unavailable session, or a
write names a security level other than the configured mock level.

The vehicle profile also gains initial odometer, traction-battery current, motor, and
charging values. Packaged ICE, HEV, and BEV profiles demonstrate static writable DIDs and
typed live-signal DIDs. These are simulator-specific DIDs; no OEM identifier or data
layout is claimed.

## Evidence and standard boundary

- ISO's catalogue identifies ISO 14229-1:2026, Edition 4, as the current UDS
  application-layer standard: <https://www.iso.org/standard/87962.html>. Its licensed
  normative text was not available and no behavior here is marked standards validated.
- AUTOSAR CP R25-11 Dcm is the current public secondary specification used here:
  <https://www.autosar.org/fileadmin/standards/R25-11/CP/AUTOSAR_CP_SWS_DiagnosticCommunicationManager.pdf>.
  It supports service/session NRC `0x7F` (`[SWS_Dcm_00211]`), subfunction/session NRC
  `0x7E` (`[SWS_Dcm_00616]`), configured SecurityAccess subfunctions and NRCs `0x24`,
  `0x35`, `0x36`, and `0x37` (`[SWS_Dcm_00321]` and section 7.4.2.8), successful key
  activation (`[SWS_Dcm_00325]`), locked initialization and session-transition reset
  (`[SWS_Dcm_00138]`, `[SWS_Dcm_00139]`), and the `0x2E` DID/session/security checks
  (`[SWS_Dcm_00467]`–`[SWS_Dcm_00470]`).
- udsoncan 1.26.1 is interoperability evidence for a single-DID `0x2E` request, its DID
  echo, and the seed/key request and response shapes:
  <https://github.com/pylessard/python-udsoncan/tree/v1.26.1/udsoncan/services>.

AUTOSAR is evidence about another implementation architecture and is not substituted for
the unavailable ISO text. The XOR algorithm, signal encodings, persistence choice, and
packaged DID numbers are project behavior only.

## Acceptance

Phase 12 closes only when the same final commit passes:

1. unit tests for every SecurityAccess sequence, session/security reset, suppression,
   service/subfunction/DID gates, every `0x2E` outcome, and immediate read-after-write;
2. schema tests for both backward-compatible and richer DID forms, signal compatibility,
   session/security references, and richer vehicle fields;
3. real `vcan`/kernel `CAN_ISOTP` tests that unlock, write, read back, re-lock on session
   transition and S3, and read live signal DIDs from the ICE, HEV, and BEV profiles;
4. validation and inventory of every packaged profile;
5. the full regression suite, isolated integration suite, Ruff, mypy, and all required PR
   CI jobs.

No physical hardware is required. Phase 8b remains the held hardware gate.
