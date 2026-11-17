# 0011 — Phase 11 evidence and design review: UDS session handling

Status: proposed 2026-11-17, before any Phase 11 code. **Awaiting the owner's rulings on the
open questions in section 13.** Nothing in `src/` or `tests/` changes until they are given.

Produced under the documentation and standards verification gate
([modernization-plan.md section 11](../modernization-plan.md)). It covers only what Phase 11
touches. No OBD service, 0x27, 0x2E, DoIP, CAN FD, J1979-2 or later-roadmap standard was
researched. [0006](0006-phase-7-scenario-and-testerpresent.md) is the shape this record
follows.

## 1. Phase 11 scope, taken from the current plan

From [modernization-plan.md section 4](../modernization-plan.md), verbatim and complete:

> ### Phase 11 — UDS session handling (V1.1)
>
> Session state machine, S3 timeout, TesterPresent timing, 0x22 with providers, 0x19/01 and
> /0A, configurable P2/P2* in the 0x10 record, 0x11 effects on session.

Section 9 assigns no commits ("V1.1 and later commits follow the phase list above with
numbering continued"). Section 3 maps Phase 11 to V1.1.

### 1.1 Where the project stands

Measured at `modernization` HEAD `77ca80b`, not recalled:

| Item | State |
|---|---|
| Phase 8a | Accepted conditionally 2026-09-25; one DoD line outstanding (fake-dongle harness not run in ordinary CI) |
| Phase 8b, the V1.0 gate | **Open.** No bench. V1.0 is not tagged |
| Phases 9 and 10 | **Not started.** Nothing in Phase 11 depends on either, except as recorded in section 10 |
| Branch `gui` | Carries the observer API; not merged into `modernization` until V1.0 is tagged. `master` currently points at the `gui` merge, `d0f1cc0` |
| Local suite at `77ca80b` | `940 passed, 55 skipped, 3 xfailed`. The 55 skips are the vcan suite: this review machine has no `CAN_ISOTP`, so **no integration test was run for this review** |

Plan section 4, Phase 8b: "Phases 9 to 11 may proceed while 8b is open, provided nothing
misrepresents the hardware-validation status." Section 12, question Q1, finds that Phase 11
cannot land on `modernization` without changing two V1.0 bench cases, so that sentence does
not settle where the work goes.

### 1.2 Scope, itemised

| Item | In Phase 11 | Note |
|---|---|---|
| Session state machine: default, programming, extended, safety | yes | sections 5 and W1 |
| S3 timeout returning to the default session | yes | W2 |
| TesterPresent timing: 0x3E restarts S3 | yes | W3 |
| Which other requests restart S3 | yes, **needs a ruling** | W3, Q4 |
| Concurrent TesterPresent (functional `3E 80`) | **no** | Unreachable: UDS is on no functional route in any shipped profile, and functional UDS needs Phase 9 fan-out. Section 10 |
| Configurable P2/P2* in the 0x10 record (DEV-17) | yes | W4; defaults **need a ruling**, Q3 |
| 0x11 effects on session | yes | W5 |
| 0x22 with providers | yes | W6 to W8; DID set and profile format **need rulings**, Q5 and Q6 |
| 0x19/01 reportNumberOfDTCByStatusMask | yes | W9; DTCFormatIdentifier **needs a ruling**, Q7 |
| 0x19/0A reportSupportedDTCs | yes | W10 |
| Session-gating NRCs 0x7F and 0x7E | **no** | Plan Phase 12: "session and security gating NRCs" |
| Security level reset on session change | **no** | No security state exists until Phase 12; the hook is named in section 5 |
| NRC 0x78 responsePending, honouring P2/P2* | **no** | Phase 10 fault injection ("response pending then final") |

### 1.3 Deviations assigned to Phase 11

| DEV | What Phase 11 does |
|---|---|
| **DEV-16**, sub-function half | 0x19/01 and /0A implemented. The third-byte freeze is **not** touched, but Q7 interacts with it |
| **DEV-17** | Session state introduced; the 0x10 record becomes configurable per session. Whether its default bytes change is Q3 |

No other DEV is in scope. DEV-03, DEV-11 Mode 07, DEV-15, DEV-25 and DEV-26 are unchanged;
section 9 lists the pins that must still pass.

## 2. Applicable documentation

| Item | Version/revision | Source type | Full text available? | Relevant to this phase |
|---|---|---|---|---|
| ISO 14229-1 | **ISO 14229-1:2026, Edition 4, published 2026-06** | Reused from the section 7.1 inventory; re-confirmed 2026-11-17 against the DIN Media committee listing, which lists 2026-06 as the current edition | **No** — licensed | 0x10, 0x11, 0x19/01, 0x19/0A, 0x22 request and response formats; the sessionParameterRecord; F186 and F190; DTCFormatIdentifier values |
| ISO 14229-2 | **ISO 14229-2:2021, Edition 2, published 2021-10**; cancels and replaces ISO 14229-2:2013 (withdrawn) | ISO catalogue record 77322 ("Published", stage 60.60); DIN Media committee listing; Standard Norge listing | **No** — licensed. Only catalogue metadata was used | Defines P2, P2*, S3 and when S3 starts and stops. **New to the inventory**; plan section 7.1 named it only so Phase 11 would know it is needed |
| AUTOSAR CP SWS Diagnostic Communication Manager | **R25-11**, document ID 18, dated 2025-11-27 | Public specification of another standards organisation. Downloaded from `autosar.org/fileadmin/standards/R25-11/CP/` on 2026-11-17 (802 pages). **R26-11 is not published**: the corresponding R26-11 URL returns 404 on the same date | **Yes** | Sections 7.2.4.3, 7.2.4.10, 7.2.4.13, 7.2.4.14, 7.3.4.3, 7.4.2.2, 7.4.2.3, 7.4.2.5.1, 7.4.2.5.2, 7.4.2.6, 7.4.2.16; configuration 10.2.5.20.4 |
| AUTOSAR CP SWS Diagnostic Event Manager | **R25-11**, document ID 19 | Public, same source and date | **Yes** | `[SWS_Dem_00936]` Dem_DTCTranslationFormatType, `[ECUC_Dem_00720]` DemTypeOfDTCSupported |
| udsoncan | **1.26.1**, released 2026-06-25 (PyPI); source read at tag `v1.26.1` | Open-source client, interoperability only | n/a | 0x10 response timing decode, 0x19/01 response decode, 0x22 response parse, the DID constants |
| Scapy | **2.8.0**, released 2026-10-02 (PyPI); `scapy/contrib/automotive/uds.py` read at tag `v2.8.0` | Open-source dissector, interoperability only | n/a | 0x19 response field layout and its DTCFormatIdentifier enumeration |
| driftregion/iso14229 | `main` at `d018adc`, 2026-09-29; latest tag `v0.9.0` | Open-source **server** implementation in C, interoperability only | n/a | The only server-side cross-check: S3 handling, P2/P2* encoding, 0x22 multi-DID handling |

The AUTOSAR revision is current: R25-11 is the release Phase 6 and Phase 7 moved to, and no
newer one exists. udsoncan, Scapy and driftregion are not project dependencies and are not
added to plan section 7.2; they are cited the way 0006 cited udsoncan and Scapy.

## 3. Normative-text availability

| Item | Current revision | Full normative text available | Official metadata available | Behavior in this phase that depends on it |
|---|---|---|---|---|
| ISO 14229-1 | ISO 14229-1:2026 (Ed. 4) | **No** | Yes | Every request and response format in section 4; the DID numbers; the DTCFormatIdentifier values |
| ISO 14229-2 | ISO 14229-2:2021 (Ed. 2) | **No** | Yes | The S3 start and stop rules, S3 tolerance, and default P2/P2* values |

**The normative text of both is unavailable.** Nothing in Phase 11 will be marked
`standards validated`. Two items cannot be settled from accessible material and are put to
the owner rather than guessed: **which requests restart S3** (W3) and **the default P2/P2*
values** (W4). AUTOSAR delegates both to ISO 14229-2 in so many words: `[SWS_Dcm_01668]`
"start and stop this timer according to ISO14229-2", `[SWS_Dcm_00027]` "handle the following
protocol timing parameters in compliance with ISO14229-2".

## 4. Evidence matrix for every wire-visible change

Current behavior, measured through `build_runtime(...).dispatcher` with the shipped profile
at `77ca80b`:

```
0x7E1  10 01      -> 50 01 00 1E 0B B8
0x7E1  10 03      -> 50 03 00 1E 0B B8
0x7E1  10 02      -> 50 02 00 1E 0B B8
0x7E1  10 04      -> 50 04 00 1E 0B B8
0x7E1  10 05      -> 7F 10 12
0x7E1  10 83      -> (nothing)
0x7E1  11 01      -> 51 01
0x7E1  3E 00      -> 7E 00
0x7E1  22 F1 86   -> 7F 22 11     (no protocol claims 0x22; the route's DEV-06 policy)
0x7E1  22 F1 90   -> 7F 22 11
0x7E1  22         -> 7F 22 11
0x7E1  19 01 FF   -> 7F 19 12
0x7E1  19 0A      -> 7F 19 12
0x7E1  19 81 FF   -> 7F 19 12
```

Every line above is already pinned by a test except `22 F1 86`, `22` and `19 81 FF`; Phase 11
pins those three in their own commit before changing them, as Phase 7 did for `7F 3E 11`.

### W1 — Session state machine (0x10)

| Field | Content |
|---|---|
| Feature / DEV | **DEV-17**, its "session changes have no effect" half |
| Existing behavior | `10 01` to `10 04` are answered and change nothing |
| Proposed behavior | One active session per ECU, `0x01` at start (`[SWS_Dcm_00034]`). `10 xx` for a supported session makes it active **after** the positive response is produced, including when it is already active. A refused `10 xx` changes nothing. Suppressed `10 8x` changes the session silently |
| Normative text available | **No** |
| Official public material | `[SWS_Dcm_00307]`: unsupported session type is NRC 0x12, and "the following steps are processed even if the requested session type is equal to the already running session type (see ISO14229-1 Section 9.2)". `[SWS_Dcm_00311]`: the new session and its P2/P2* are set in the send confirmation, i.e. after the response. `[SWS_Dcm_00034]`: default session at initialisation |
| Open-source interoperability | driftregion sets the session and starts S3 for any non-default session in its 0x10 handler. udsoncan's client tracks nothing server-side |
| Conflicting evidence | None |
| Recommendation | **Implement, not standards validated.** Keep today's set of sessions (`01`–`04`) so no answer to a refused or accepted `10 xx` changes |

### W2 — S3 timeout

| Field | Content |
|---|---|
| Feature / DEV | Plan item "S3 timeout"; no DEV |
| Existing behavior | No timer exists |
| Proposed behavior | In a non-default session, if no request restarts S3 for **S3Server**, the ECU is in the default session. Default S3Server 5000 ms, configurable per ECU |
| Official public material | `[SWS_Dcm_01670]` "If the S3Server elapses, the Dcm shall switch back to default session." `[SWS_Dcm_01679]` "If the parameter DcmS3ServerTimeoutOverwrite is not configured, Dcm shall take the value of 5000ms as the S3Server timeout." `[SWS_Dcm_01680]` makes it configurable. Both are also present in R24-11 (checked 2026-11-17); the Phase 6 and Phase 7 reviews did not need them |
| Open-source interoperability | driftregion: `UDS_SERVER_DEFAULT_S3_MS` is **5100**, commented "ISO14229-2 2013 Table 5: 5000 -0/+200 ms"; on expiry it returns to the default session and clears its security level. That tolerance cites a withdrawn edition and is not used here |
| Conflicting evidence | 5000 vs 5100 is a tolerance question, not a conflict about the nominal value |
| Recommendation | **Implement 5000 ms, configurable, not standards validated.** How expiry is evaluated is section 6 |

### W3 — What restarts S3 (TesterPresent timing)

| Field | Content |
|---|---|
| Feature / DEV | Plan item "TesterPresent timing"; DEV-23 recorded S3 as Phase 11 |
| Existing behavior | 0x3E is stateless by construction and tested as such (`test_tester_present_changes_nothing_a_later_request_can_see`) |
| Proposed behavior | `3E 00` and `3E 80` restart S3. **Which other requests restart it is Q4** |
| Official public material | AUTOSAR 7.4.2.16: TesterPresent "is used to keep one or multiple servers in a diagnostic session being different than the defaultSession". `[SWS_Dcm_01668]` defers start and stop to ISO 14229-2, which is unread |
| Open-source interoperability | **driftregion restarts S3 only in its 0x10 handler (entering a non-default session) and its 0x3E handler**; no other request restarts it (`src/server.c`, the only three assignments to `s3_session_timeout_timer`) |
| Conflicting evidence | None accessible. The available server implementation is not normative, and the ISO 14229-2 text that settles the behavior is unavailable |
| Recommendation | **Put to the owner, Q4.** Implementing either is a guess at ISO 14229-2 that section 11.3 does not allow without a ruling |

### W4 — The 0x10 sessionParameterRecord, configurable per session

| Field | Content |
|---|---|
| Feature / DEV | **DEV-17** |
| Existing behavior | `00 1E 0B B8` for every session: P2 = 30 ms, P2* = 3000 × 10 ms = 30 s |
| Proposed behavior | Four bytes: P2ServerMax big-endian in 1 ms units, then P2*ServerMax big-endian in 10 ms units, from the active session's configuration. **The default values are Q3** |
| Official public material | AUTOSAR configures both per session (`[ECUC_Dcm_00766]` P2ServerMax in seconds, range 0..1; `[ECUC_Dcm_00768]` P2*ServerMax in seconds, range 0..100) and states "This value is reported to the tester within the response to the 'Session Control' service". **It states neither the byte layout nor any default value** ("DCM configuration tools must convert this float value to the appropriate value format") |
| Open-source interoperability | **udsoncan 1.26.1**, for ISO 14229-1 2013 and later: exactly four bytes, `p2_server_max = a / 1000` and `p2_star_server_max = b * 10 / 1000`, and it rejects any other length. **driftregion** packs P2 at "resolution: 1ms" and P2* at "resolution: 10ms". Two independent implementations, one client and one server, agree byte for byte. Scapy keeps the record as raw bytes and decodes nothing |
| Defaults found | driftregion server defaults P2 50 ms, P2* 5000 ms. AUTOSAR gives none. ISO 14229-2 is unread |
| Conflicting evidence | None on the layout. The current 30 ms / 30 s has no source either; it is legacy |
| Recommendation | **Implement the layout, not standards validated.** Configuration in integer milliseconds, P2* required to be a multiple of 10, both range-checked to what the two bytes carry, so no value can be silently rounded or truncated, and DEV-26's nonfinite problem cannot arise for these fields |

### W5 — 0x11 effects on session

| Field | Content |
|---|---|
| Feature / DEV | Plan item "0x11 effects on session" |
| Existing behavior | `11 01`–`11 05` are answered and change nothing |
| Proposed behavior | After a positive response to `01` hardReset, `02` keyOffOnReset or `03` softReset, the ECU is in the default session with S3 stopped. `04` and `05` (rapid power shutdown) change no session. A suppressed `11 81` resets silently. Bytes on the wire are unchanged |
| Official public material | `[SWS_Dcm_00373]` and `[SWS_Dcm_00594]` separate `01`–`03`, which request a reset, from `04`/`05`, which only switch the rapid-power-shutdown mode (`[SWS_Dcm_00818]`). After a reset the Dcm initialises: default session (`[SWS_Dcm_00034]`). `[SWS_Dcm_00834]`: "After sending the positive response of EcuReset ... the Dcm shall ignore all further requests during reset-processing" |
| Open-source interoperability | driftregion, on a positive 0x11, stops receiving and schedules the reset after a power-down delay, for **every** reset type including `04`; the session after restart is left to the application |
| Conflicting evidence | On `04`/`05` only: driftregion treats them as resets, AUTOSAR as a mode switch. AUTOSAR ranks higher (section 11.2) |
| Recommendation | **Implement the session effect, not standards validated.** Do **not** model the dead time of `[SWS_Dcm_00834]`: nothing accessible gives its length, and an unresponsive ECU is Phase 10's "ECU offline". A reset does not clear the DTC store (fault memory is not volatile) and does not restart the scenario. Q8 confirms |

### W6 — 0x22 request framing and the response

| Field | Content |
|---|---|
| Feature / DEV | Plan item "0x22 with providers" |
| Existing behavior | `7F 22 11` for every 0x22 request |
| Proposed behavior | Request `22` followed by one or more two-byte DIDs. `62`, then for each supported DID in request order the DID and its data. A request that is not `1 + 2n` bytes with `n >= 1` is `7F 22 13`. A response longer than the transport can carry is `7F 22 14` |
| Official public material | `[SWS_Dcm_00253]` "according to ISO 14229-1:2020"; "the tester can request the value of one or more DIDs". `[SWS_Dcm_00696]` and `[SWS_Dcm_00272]`: NRC 0x13 for a short or malformed request. `[SWS_Dcm_01059]`: NRC 0x14 for a response longer than the buffer. AUTOSAR configures the maximum number of DIDs per request (`DcmDspMaxDidToRead`) and gives no default |
| Open-source interoperability | udsoncan's parser walks `DID, data, DID, data …` from the first byte after `62`; driftregion builds exactly that and returns 0x13 when `(length − 1)` is odd or zero |
| Conflicting evidence | None on the framing |
| Recommendation | **Implement, not standards validated.** No per-request DID count limit. The 0x14 limit is the largest message the kernel `CAN_ISOTP` binding in use sends; 4095, the largest length a classical first frame's 12-bit length field expresses, is the candidate, and **is to be confirmed experimentally against can-isotp 2.0.7 and the kernel before it is written** (section 11.5). Duplicated DIDs in one request are answered once each, in order, exactly as asked |

### W7 — 0x22 when some or all DIDs are unsupported

| Field | Content |
|---|---|
| Proposed behavior | Unsupported DIDs are left out of the response. Only when **none** is supported is the answer `7F 22 31` |
| Official public material | `[SWS_Dcm_00438]` "for every requested DID the Dcm module shall check if the DID is supported ... If none of the requested DIDs is supported, the Dcm module shall send NRC 0x31" |
| Open-source interoperability | **driftregion refuses the whole request** with the NRC of the first DID its application does not answer positively |
| Conflicting evidence | **Yes**, and resolved by section 11.2's source priority: an official public specification outranks an open-source implementation |
| Recommendation | **Implement AUTOSAR's rule, not standards validated** |

### W8 — The DIDs served

| Field | Content |
|---|---|
| Proposed behavior | **F186** ActiveDiagnosticSession: one byte, the active session. **F190** VIN: `vehicle.vin` as ASCII. Plus whatever the profile's `dids:` declares (Q6) |
| Official public material | `[SWS_Dcm_00085]`: "The DSP submodule shall manage internally a read access for the dataIdentifier 0xF186 (ActiveDiagnosticSessionDataIdentifier) defined in ISO14229-1" |
| Open-source interoperability | udsoncan's `DataIdentifier` constants: `ActiveDiagnosticSession = 0xF186`, `VIN = 0xF190`; its name lookup cites "ISO-14229:2006, Annex F" |
| Open points | The F186 payload being the bare session byte rests on AUTOSAR naming the DID and on it being "the active session"; no accessible source states the byte layout. A VIN shorter than 17 characters: OBD Mode 09 left-pads it with NULs to 17 (`test_a_short_vin_is_left_padded_to_seventeen_bytes`); F190 has no evidence either way. Q5 |
| Recommendation | **F186 and F190, put to the owner as Q5**, with the short-VIN rule stated there |

### W9 — 0x19/01 reportNumberOfDTCByStatusMask

| Field | Content |
|---|---|
| Feature / DEV | **DEV-16**, sub-function half |
| Existing behavior | `19 01 FF` → `7F 19 12` |
| Proposed behavior | Request exactly `19 01 <mask>`, else `7F 19 13` after the sub-function check (the Phase 6 ordering). Response `59 01 <availability 8C> <DTCFormatIdentifier> <count high> <count low>`, the count taken with the same filter 0x19/02 applies: `(status & mask) != 0` |
| Official public material | `[SWS_Dcm_01642]`: response is DTCStatusAvailabilityMask, DTCFormatIdentifier ("Value returned by Dem_GetTranslationType()"), DTCCount. `[SWS_Dcm_01643]`: the same `Dem_SetDTCFilter` as 0x02, the mask "from request" |
| Open-source interoperability | udsoncan: after the report-type byte, availability, format, then a `>H` count; it rejects a shorter response. Scapy: the same fields in the same order for report types 0x01, 0x07, 0x11, 0x12 |
| Conflicting evidence | On the format byte only; see Q7 |
| Recommendation | **Implement, not standards validated.** The format byte is Q7 |

### W10 — 0x19/0A reportSupportedDTCs

| Field | Content |
|---|---|
| Feature / DEV | **DEV-16**, sub-function half |
| Existing behavior | `19 0A` → `7F 19 12` |
| Proposed behavior | Request exactly `19 0A`, else `7F 19 13`. Response `59 0A 8C` followed by a DTCAndStatusRecord for **every configured code**, in profile order, including those whose status is `00` |
| Official public material | `[SWS_Dcm_01644]` gives 0x0A the same response layout as 0x02. `[SWS_Dcm_01645]` sets the filter's DTCStatusMask to `0x00` for 0x0A, i.e. no status filtering |
| Open-source interoperability | udsoncan builds 0x0A with no parameter and parses it with 0x02's record parser; Scapy parses both with one `DTCAndStatusRecord` list |
| Conflicting evidence | None |
| Recommendation | **Implement, not standards validated.** "Supported" is "configured on this ECU", which is the only identity `DtcStore` has |

## 5. Proposed session model

| Concern | Proposal |
|---|---|
| Where the state lives | A small `UdsSession` object, domain state owned by the `Ecu` beside `dtc_store`, holding the active session and the S3 deadline. `UdsProtocol` receives it at construction, as it receives `dtcs` today |
| Scope | **One session per ECU**, shared by both physical UDS endpoints of the shipped profile (`0x7E0` and `0x7E1`). AUTOSAR ties S3 to "the client on the DcmDslConnection that requested the non-default session" (`[SWS_Dcm_01669]`); following that needs the route or endpoint in `ServiceRequest`, which plan rule 3 and the `protocols/base.py` contract keep out on purpose. **Q2** |
| Default | `0x01` at start and after any return to default |
| On entering any session | the session is set after the response is produced; S3 is started for a non-default session and stopped for the default one |
| On leaving a non-default session | the hook where Phase 12 resets the security level (`[SWS_Dcm_00139]`). Empty in Phase 11 |
| What session state changes on the wire in Phase 11 | the 0x10 record (per session), F186, and nothing else. No service is refused for being in the wrong session until Phase 12 |
| OBD | OBD requests neither read nor restart the UDS session. AUTOSAR's protocol preemption, where "an enhanced session can be ended by a OBD session", is not modelled |

## 6. Proposed timing model

| Concern | Proposal |
|---|---|
| Time source | The existing `Clock`, read by `UdsSession`. `SimulatedClock` in every test |
| Expiry | **Evaluated lazily, before each UDS request is served.** If the deadline has passed, the session is default before the request is considered. No timer task, no tick |
| Why lazy is enough | Session state is observable only through a UDS response; the wire cannot tell lazy from eager expiry. The observer API that could show the difference lives on `gui`, which does not merge until V1.0 |
| Where S3 restarts from | The instant the request is served. The response is produced in the same synchronous call and the transport sends it immediately; ISO 14229-2's exact reference point (request reception vs. response confirmation) is unread, and the difference is the transmission time of one ISO-TP message |
| The boundary | The session survives a gap of exactly S3Server and expires on any longer gap. Tests assert at S3 − 1 ms, S3 and S3 + 1 ms so the rule is visible |
| A consequence worth naming | Today a profile without a scenario never reads the clock (`build_runner`'s docstring says so). After Phase 11, every ECU that registers UDS reads it on every UDS request. That docstring and the composition root change accordingly |

## 7. Proposed configuration

Shown for approval, not final; it follows Q2 to Q6. Integer milliseconds throughout.

```yaml
ecus:
  engine:
    uds:
      s3_ms: 5000                 # [SWS_Dcm_01679] default
      sessions:                   # absent entries keep today's set 01-04
        0x01: { p2_ms: 50, p2_star_ms: 5000 }
        0x02: { p2_ms: 50, p2_star_ms: 5000 }
        0x03: { p2_ms: 50, p2_star_ms: 5000 }
        0x04: { p2_ms: 50, p2_star_ms: 5000 }
    dids:
      0xF1A0: "01 02 03 04"       # Q6: hex bytes, served as is
```

Validation at load, path-qualified: `s3_ms` 1..65535; `p2_ms` 0..65535; `p2_star_ms`
0..655350 and a multiple of 10; session numbers 0x01..0x7E (`[ECUC_Dcm_00765]`: "0, 127 and
all values above 127 are reserved"); a profile DID may not be F186 or F190 (those have one
owner, the simulator) nor fall in F400–F8FF, AUTOSAR's OBD DID range, which is Phase 16's
(OBDonUDS) to define.

## 8. DEV identifiers Phase 11 will change

| DEV | Change | Pins that move, each in its own commit with the reason |
|---|---|---|
| **DEV-16** | sub-function half fixed; the third byte stays frozen | `test_0x19_negative_responses` (`1901` and `190a` in both the golden and unit files), `test_0x19_an_unsupported_subfunction_is_0x12_at_any_length` / `…_rejected_as_one_at_any_length` (`1901ff`, `190aff`), `test_0x19_a_report_type_this_server_lacks_is_still_refused_with_or_without_the_bit` (`1981`, `1901`) |
| **DEV-17** | session state and a per-session record | Only if Q3 changes the default bytes: `test_0x10_positive_response_with_fixed_session_parameter_record`, `test_0x10_answers_with_the_fixed_session_parameter_record`, `test_0x10_with_the_suppress_bit_sends_nothing`, `test_dispatcher_answers_by_sid_on_every_enabled_route`, integration `SESSION_RESPONSE` in `test_ecu_dispatch.py` and `test_uds_session_control_and_negative_response`, and **hardware** `_uds_services_on_the_physical_address` |
| none (0x22 is a new service) | `7F 22 11` stops being the "unclaimed service" probe | unit/integration `test_unknown_service_on_a_physical_address_gets_nrc_0x11`, and **hardware** `_an_unclaimed_service_is_refused_with_nrc_11`. They need a SID no phase plans to claim; Q9 |

`tests/unit/test_route_eligibility.py` and `tests/unit/test_ecu.py` use spy and fake
protocols whose bytes are their own; they are unaffected.

## 9. DEV identifiers explicitly preserved

| DEV | Behavior preserved | Pinned by |
|---|---|---|
| **DEV-03** | Mode 09 PID 0A framing | `test_mode09_pid0a_frozen.py` and its strict xfail |
| **DEV-07, DEV-24** | Suppress-bit handling, unchanged; it now also covers `19 81 <mask>` and `19 8A`, which is the existing rule applied to new report types, not a new rule | the Phase 7 suppress-bit tests |
| **DEV-11 Mode 07** | silence | its strict xfail |
| **DEV-15** | Mode 03 trailing-byte echo | its three tests |
| **DEV-16, third byte** | `0x01` | `test_the_frozen_third_byte_is_a_named_constant_so_the_freeze_is_visible` |
| **DEV-25, DEV-26** | open, unscheduled | their existing pins |
| Every Phase 6 and Phase 7 behavior not named in section 8 | 0x14, 0x19/02, availability `0x8C`, `3E 00` / `3E 80` bytes | the existing suites |

## 10. Behaviors to defer

| Behavior | Reason |
|---|---|
| Session-gating NRCs 0x7F / 0x7E | Plan Phase 12 |
| Security level and its reset on session change | Plan Phase 12; the hook exists (section 5) |
| Concurrent TesterPresent, functional `3E 80` | Needs UDS on a functional route, i.e. Phase 9 fan-out; per-connection rules are Q2 |
| NRC 0x78 and real P2/P2* response timing | Phase 10 |
| The post-reset dead time of `[SWS_Dcm_00834]` | No evidence of its length; Phase 10 "ECU offline" |
| 0x83 AccessTimingParameter | Not in the plan |
| OBD DIDs F400–F8FF over 0x22 | Phase 16 |
| Session-dependent DID readability (`[SWS_Dcm_00434]`, 0x31) | Session gating, Phase 12 |

## 11. Planned acceptance criteria and how each is verified

| Criterion | Verification |
|---|---|
| Default session at start; `10 03` then `22 F1 86` reads `62 F1 86 03` | unit and integration |
| Re-entering the active session restarts S3 and answers normally | unit with `SimulatedClock` |
| A refused `10 xx` changes no session | unit |
| `10 83` changes the session and sends nothing | unit and integration |
| S3: alive at S3 − 1 ms and at S3, default at S3 + 1 ms | unit with `SimulatedClock` |
| `3E 00` and `3E 80` keep a non-default session alive past S3 | unit with `SimulatedClock`; integration with real time and a short `s3_ms` profile |
| Whatever Q4 rules for other requests | unit, both directions (a request that restarts S3 and one that does not) |
| 0x10 record per session, from configuration; defaults per Q3 | unit, characterization transition if Q3 changes bytes |
| Bad timing configuration rejected at load with its path | schema tests for each rule in section 7 |
| `11 01`/`02`/`03` return to default; `11 04`/`05` do not; `11 81` resets silently; the DTC store survives a reset | unit |
| 0x22: framing, multi-DID order, partial support omitted, none supported `0x31`, `0x13`, `0x14` | unit; integration for single- and multi-frame |
| `19 01 <mask>` counts with the 0x02 filter; `19 0A` lists every configured code | unit, characterization transitions, integration |
| Each previously unpinned measured line in section 4 pinned before it changes | characterization commit first |
| No `sleep`, no timer task added; protocols still import nothing from `transport/` | existing isolation tests plus a grep test for the new module |
| Every preserved deviation still preserved | full suite, section 9 pins named in the completion report |
| Regression | full unit, characterization and integration suites; `ruff`; `mypy`; CI on 3.12 and 3.13; plan section 10 report |

**Integration tests could not be run on the machine that wrote this review** (no
`CAN_ISOTP`). The completion report must come from a vcan host, or record them as not run.

## 12. Two findings the plan did not anticipate

**F1 — Phase 11 cannot land on `modernization` without moving the V1.0 bench.** Two cases in
`tests/hardware/acceptance_cases.py`, which Phase 8b has not yet run, assert bytes Phase 11
changes: `10 01 → 50 01 00 1E 0B B8` (only if Q3 changes the defaults) and
`22 F1 90 → 7F 22 11` (always, because 0x22 stops being unclaimed). If Phase 11 merges first,
the V1.0 bench either runs against V1.1 code or has to be rewritten first, and V1.0 is then
tagged with V1.1 behavior in it. That is Q1.

**F2 — the shipped ECU has two physical UDS connections.** `obd_physical` (0x7E0) and
`uds_physical` (0x7E1) both enable UDS on one ECU. A per-ECU session means a tester on one
can keep alive, or end, a session another tester opened on the other. AUTOSAR forbids exactly
that (`[SWS_Dcm_01667]`, `[SWS_Dcm_01669]`). That is Q2.

## 13. Open questions for the owner

| # | Question | Options | Recommendation |
|---|---|---|---|
| **Q1** | Where does Phase 11 land, given F1? | **A** branch `phase-11` from `modernization`, merged after V1.0 is tagged, as `gui` is. **B** merge into `modernization` now and amend the two bench cases. **C** hold Phase 11 until 8b passes | **A.** It keeps "V1.0 is not tagged without 8b's evidence" meaning what it says, and costs one rebase |
| **Q2** | Session scope, given F2 | **A** one session per ECU, the AUTOSAR difference recorded in `conformance.md`. **B** one session per endpoint, which adds the route name to `ServiceRequest` and changes the protocol contract | **A** for Phase 11, recorded as a deliberate simplification; B belongs with Phase 9's multi-tester work if anyone needs it |
| **Q3** | Default P2/P2* | **A** keep `30 ms / 30 s` as the default for every session, so no byte changes and DEV-17's remaining half is "configurable" only. **B** change the default to `50 ms / 5000 ms`, matching the only server implementation available, with the characterization and bench transitions in section 8 | **A.** No accessible source supports either pair over the other; B moves a bench case for a value that is still a guess. Profiles can set 50 / 5000 |
| **Q4** | Which requests restart S3 | **A** every UDS request this ECU serves, positive, negative or suppressed. **B** only 0x3E and 0x10, as driftregion does. **C** implement A or B behind a profile switch | **None.** W3 has conflicting interoperability evidence and the deciding text is ISO 14229-2. If a choice is needed without the text, **A** is the one under which no tester that sends 0x3E loses a session it would keep under B, so it is the more permissive failure |
| **Q5** | Built-in DIDs | F186 (session byte) and F190 (VIN), or neither and everything from the profile. And for a VIN shorter than 17: send it as is, or left-pad with NUL as Mode 09 does | **Both, VIN sent as is.** A short VIN is a profile choice; padding it would put bytes on the wire the profile did not state, and Mode 09's padding is its own pinned behavior, not evidence for 0x22 |
| **Q6** | What `dids:` values mean | The field exists since Phase 4 as `dict[str, str]` with no defined meaning and no reader. **A** a string of hex bytes served verbatim. **B** a typed signal reference into `VehicleState` | **A** now; B is "richer profiles", Phase 12 |
| **Q7** | DTCFormatIdentifier in `59 01` | `0x00` ISO 15031-6 / J2012-DA_DTCFormat_00 (AUTOSAR: "2 byte format"); `0x01` ISO 14229-1 format ("3 byte format"); `0x04` J2012-DA_DTCFormat_04. AUTOSAR's own description of `0x04` in `[ECUC_Dem_00720]` reads "SAE_J2012-DA_DTCFormat_00 (3 byte format)", which contradicts `[SWS_Dem_00936]`. udsoncan names all five; Scapy names only `0x00`–`0x03` | **`0x04`**, because the first two bytes are the J2012 encoding and the third sits where J2012's three-byte form puts the failure type. **But** advertising it makes the frozen third byte (DEV-16) a claim about failure type. If that is unacceptable, `0x01` claims least |
| **Q8** | Reset effects | Confirm: `01`–`03` return to default, `04`/`05` do not, no dead time, DTC store and scenario untouched | **Confirm** |
| **Q9** | The new "unclaimed service" probe | Any SID no phase plans to claim; `0x28` CommunicationControl is a V1.0 non-goal and in no phase | **`28 01`** in the unit, integration and (under Q1 A, later) bench cases |

## 14. Proposed commit sequence

Numbering continues plan section 9. One wire change per commit, each preceded by its pin.

61. `docs(decisions): Phase 11 evidence and design review` (this record)
62. `test(uds): pin 22 F1 86, 22 and 19 81 FF before they change`
63. `test: move the unclaimed-service probe from 0x22 to 0x28` (Q9)
64. `feat(config): uds timing and session configuration, validated at load`
65. `feat(uds): session state machine and per-session parameter record (DEV-17)`
66. `feat(uds): S3 timeout and TesterPresent timing`
67. `feat(uds): ECUReset returns the ECU to the default session`
68. `feat(uds): 0x22 ReadDataByIdentifier with F186, F190 and profile DIDs`
69. `fix(uds): 0x19 report types 0x01 and 0x0A (DEV-16)`
70. `docs: Phase 11 conformance, deviations, plan and decision outcome`
