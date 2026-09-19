# 0004 — Phase 6 evidence review: shared DTC state, OBD modes 03/04/07, UDS 0x14 and 0x19/02

Status: accepted 2026-09-19, before any Phase 6 wire change, with the decisions recorded
in section 12. Implemented in Phase 6.

Produced under the documentation and standards verification gate
([modernization-plan.md section 11](../modernization-plan.md)). It covers only the wire
behavior Phase 6 owns. Later UDS work (sessions, 0x22, 0x19 sub-functions 0x01 and 0x0A,
0x3E) was not researched, and neither were J1979-2, J1979-3 or DoIP.

[0003](0003-phase-5-obd-evidence.md) is the worked example this record follows.

## 1. Phase 6 scope, taken from the plan

From [modernization-plan.md section 4, Phase 6](../modernization-plan.md) verbatim:

> `DtcStore` (pending, confirmed, stored, MIL) with one `clear()` operation; OBD 03/04/07
> and UDS 0x14 and 0x19/02 with the status-mask fix; protocol encoders in
> `protocols/*/dtc.py`. Legacy `uds/` deleted.

Plus the standing architecture rules that bind this phase:

- rule 6 — UDS data services delegate to the `DidProvider` and `DtcProvider` registries
  on the ECU;
- rule 7 — DTC clearing is one operation on the shared `DtcStore`; OBD Mode 04 and UDS
  0x14 both call it and neither owns the transition.

Commit sequence for Phase 6 (section 9): `feat(dtc): DtcStore with a single clear
operation`, `fix(uds): 0x19 0x02 requires and honours DTCStatusMask`, `feat(uds): 0x14
ClearDiagnosticInformation via DtcStore.clear`, `refactor: delete legacy uds package`.

Deviations assigned to Phase 6: **DEV-05**, **DEV-11**, **DEV-16**, **DEV-23** (the 0x14
half only). `config/legacy.py` goes with the legacy UDS module.

### In scope

| Area | Phase 6 owns |
|---|---|
| shared mutable DTC state | `DtcStore` per ECU with one `clear()`; initial state from the profile |
| OBD DTC behavior | Mode 03 re-sourced from the store; Mode 04 and Mode 07 implemented (DEV-11) |
| UDS DTC behavior | 0x14 implemented (DEV-23); 0x19/02 request framing and mask filtering (DEV-05); per-DTC status byte derived from the store (DEV-16, status half) |
| encoders | `protocols/obd/dtc.py` and `protocols/uds/dtc.py`; `dtc_utils.py` retired into them |
| deletion | legacy `uds/` package, `config/legacy.py`, and the frozen `LegacyUdsProtocol` |
| rewrite, no wire change | 0x10 and 0x11 move out of the legacy module byte-for-byte |

### Explicitly **not** in scope, from the same plan

| Deferred to | Behavior |
|---|---|
| Phase 7 | UDS 0x3E TesterPresent (the other half of DEV-23); scenario-driven DTC events |
| Phase 11 | 0x19 sub-functions 0x01 and 0x0A (DEV-16 second half); session state; `suppressPosRspMsgIndicationBit` (DEV-07) if not taken in 7 |
| unassigned | OBD Mode 0A permanent DTCs; Mode 06; freeze frames; DTC snapshots and extended data; severity; mirror memory; `MemorySelection` |
| frozen | DEV-03 Mode 09 PID 0A; DEV-15 Mode 03 trailing-byte echo |

Mode 01 PID 0x01 is **not** listed in the Phase 6 scope sentence. It reaches this review
only because Phase 5 deferred it *to* the DTC store; section 8 below decides it on
evidence rather than on that association.

## 2. Applicable documentation

| Item | Version/revision | Source type | Full text available? | Relevant to this phase |
|---|---|---|---|---|
| **ISO 14229-1** | **ISO 14229-1:2026, Edition 4, published 2026-06-05.** Cancels and replaces ISO 14229-1:2020 (Edition 3) and ISO 14229-1:2020/Amd 1:2022 | Official publisher listing (ISO catalogue entry 87962) and an official national-body distributor listing | **No** — purchase or subscription | Defines 0x14 ClearDiagnosticInformation, 0x19 ReadDTCInformation sub-function 0x02, the DTCAndStatusRecord, the DTC status byte and the DTCStatusAvailabilityMask |
| SAE J1979 | `J1979_202505`, reaffirmed 2025-05-23; last technical revision `J1979_201702` | Official publisher listing (SAE Mobilus) | **No** | Defines OBD services 03, 04, 07 and service 01 PID 01. Revision reused from the Phase 5 review; not re-researched |
| SAE J1979-DA | `J1979DA_202607`, revised 2026-07-16 | Official publisher listing (SAE Mobilus) | **No** | Registry of data identifiers. Would settle the service 01 PID 01 monitor-bit layout. Revision reused from Phase 5 |
| **SAE J2012** | **`J2012_202509`, revised 2025-09-01**; supersedes `J2012_201612`; originally issued 1992-03-01 | Official publisher listing (SAE Mobilus) | **No** | Defines the DTC format, including the three-byte form whose third byte this project currently fixes at 0x01 |
| SAE J2012-DA | Current revision **not established**. The publisher index returns `J2012DA_202403` as the most recent catalogued entry; a 2025 edition appears in reseller listings. **No revision numbered `J2012DA_202607` was found**; that identifier belongs to J1979-DA | Official publisher listing (SAE Mobilus), incomplete | **No** | **Not applicable to Phase 6.** It is a registry of standardized DTC numbers and failure-type byte values. This phase encodes whatever trouble codes the profile declares and does not depend on any standardized code meaning. Not researched further, per the scope rule |
| ISO 15031-5 | International equivalent of J1979 | Publisher listing | **No** | Same content as J1979; equally unavailable |
| ISO 15031-6 | International equivalent of J2012 | Publisher listing | **No** | Same content as J2012; equally unavailable |
| ELM327 datasheet | ELM327DSJ, Elm Electronics | Public vendor datasheet | **Yes** | Tester side. Documents mode 03 on CAN, mode 04 and its effects, and the mode 01 PID 01 first byte |
| AUTOSAR CP R24-11, SWS Diagnostic Event Manager | R24-11, document ID 19 | Public specification of another standards organisation | **Yes** | Defines the UDS status byte bit by bit and the post-clear state, citing ISO 14229-1 as its source |
| AUTOSAR CP R24-11, SWS Diagnostic Communication Manager | R24-11, document ID 18 | Public specification of another standards organisation | **Yes** | Defines 0x14 and 0x19/02 server behavior including the status-mask AND rule, citing ISO 14229-1 as its source |

### Note on AUTOSAR

The two AUTOSAR documents are freely downloadable, official, and normatively reference
ISO 14229-1. They are nonetheless **not** ISO 14229-1. They describe what an AUTOSAR
Dem/Dcm stack must do; where they restate an ISO requirement they are a faithful
secondary rendering of it, and where they add AUTOSAR-specific configuration they are
not ISO at all, and this review cannot tell the two apart without the ISO text. They are
therefore used here as strong corroborating documentation and **never** as a conformance
claim. Under [section 11.2](../modernization-plan.md) they rank below source 1 (the
applicable specification) and above source 5 (open-source implementations).

## 3. Normative-text availability

Recorded explicitly, for the avoidance of doubt:

| Item | Current revision | Full normative text available | Official metadata available | Behavior in this phase that depends on it |
|---|---|---|---|---|
| ISO 14229-1 | **ISO 14229-1:2026 (Ed. 4)** | **No** | Yes | 0x14 request/response, 0x19/02 request/response, DTC status byte, availability mask, NRC selection |
| SAE J1979 | `J1979_202505` | **No** | Yes | OBD mode 03, 04, 07 framing; mode 01 PID 01 layout |
| SAE J1979-DA | `J1979DA_202607` | **No** | Yes | mode 01 PID 01 monitor-bit layout |
| SAE J2012 | `J2012_202509` | **No** | Yes | three-byte DTC format and its failure-type byte |
| SAE J2012-DA | not established | **No** | Partial | nothing in this phase |
| ISO 15031-5 / 15031-6 | — | **No** | Yes | same content as J1979 / J2012 |

No portion of any of these documents is visible to this project. The ISO catalogue page
for 14229-1 is served behind a challenge that blocks automated retrieval; the revision
and publication date above come from the ISO catalogue record as indexed and from an
official national-body distributor listing, not from the document. SAE Mobilus exposes
title, revision date, supersession and abstract only.

**Consequently:**

- nothing in Phase 6 will be marked `standards validated`;
- no requirement below is reconstructed from memory of any specification;
- no open-source implementation is treated as normative;
- no DTC-status meaning, request field or response field is invented. Where this review
  proposes a value that the evidence does not fix, it says so and labels it a
  project-defined choice.

## 4. Evidence sources used

- **P1** ELM327 datasheet ELM327DSJ, "Interpreting Trouble Codes" (p. 34) and "Resetting
  Trouble Codes" (p. 35). Public primary vendor document with worked examples.
- **P2** AUTOSAR CP R24-11 `AUTOSAR_CP_SWS_DiagnosticEventManager.pdf`. Public. Cited
  requirements: the UDS status bit 0–7 definitions (§2.1 acronym table),
  `[SWS_Dem_00385]`, `[SWS_Dem_00060]`, `[SWS_Dem_00657]`, `[SWS_Dem_01203]`.
- **P3** AUTOSAR CP R24-11 `AUTOSAR_CP_SWS_DiagnosticCommunicationManager.pdf`. Public.
  Cited requirements: `[SWS_Dcm_00247]`, `[SWS_Dcm_01263]`, `[SWS_Dcm_01265]`,
  `[SWS_Dcm_00008]`, `[SWS_Dcm_00377]`, `[SWS_Dcm_01644]`, and the `Dem_SetDTCFilter`
  API description.
- **S1** Snap-on Triton-D8 diagnostics manual, "OBDII Service Modes". Tool-vendor
  technical reference, independent of **P1**. Secondary.
- **S2** Wikipedia "OBD-II PIDs". Secondary. Derivative pages were not counted as
  independent corroboration.
- **O1** `python-udsoncan` (pylessard), source read at `master`:
  `udsoncan/services/ClearDiagnosticInformation.py`,
  `udsoncan/services/ReadDTCInformation.py`, `udsoncan/common/dtc.py`.
- **O2** Scapy `scapy/contrib/automotive/uds.py` and
  `scapy/contrib/automotive/obd/services.py`, read at `master`.
- **O3** `python-OBD` (brendan-w), `obd/commands.py` and `obd/decoders.py`, read at
  `master`.

**O1**, **O2** and **O3** are three mutually independent implementations. They are
interoperability evidence only.

## 5. Evidence matrix, one row group per wire behavior

Current behavior below was measured, not remembered, by dispatching each request through
`app.build_dispatcher` on the shipped `ice_default.yaml` profile (DTCs `B1477`, `P0001`):

```
0x7E0  03        -> 43 02 94 77 00 01      0x7E1  19 02       -> 59 02 FF 94 77 01 2F 00 01 01 2F
0x7E0  04        -> (silence)              0x7E1  19 02 FF    -> 7F 19 13
0x7E0  07        -> (silence)              0x7E1  19 82 FF    -> 7F 19 13
0x7E0  04 00     -> (silence)              0x7E1  14 FF FF FF -> 7F 14 11
0x7E0  01 01     -> (silence)              0x7E1  14          -> 7F 14 11
```

### W1 — OBD Mode 03, re-sourced from the DtcStore

| Field | Content |
|---|---|
| Feature / DEV | no DEV; consequence of the `DtcStore` introduction |
| Existing behavior | `43 <count> <2-byte code>…` over every trouble code in the ECU's configured list, unconditionally |
| Proposed behavior | Identical framing; the list becomes "every DTC in the store whose **confirmed** flag is set". With the profile default (section 6) the bytes are unchanged: `43 02 94 77 00 01` |
| Applicable specification | SAE J1979 / ISO 15031-5 service 03 |
| Normative text available | No |
| Official / public material | **P1**: "the ISO 15765-4 (CAN) protocol … adds an extra data byte (in the second position), showing how many data items (DTCs) are to follow", which is the count byte already emitted. **S1**: "This mode displays stored emission related DTCs" |
| Independent secondary | **S1** and **S2** agree that mode 03 reports *stored/confirmed* codes and mode 07 *pending* ones, and describe the two-trip relationship between them |
| Open-source interoperability | **O2** `OBD_S03_PR` = `43` + count + N × 2-byte DTC. **O3** decodes mode 03 by skipping "the mode and DTC_count bytes" |
| Capture evidence | **P1** gives a non-CAN worked example (`43 01 33 00 00 00 00`) and states the CAN difference in the same paragraph. No new capture was sought: the framing is unchanged from behavior already shipped and pinned |
| Conflicting evidence | None. Note **P1**'s non-CAN example pads with `00 00` pairs; that padding does not apply to the CAN count-byte form and is not proposed |
| Recommendation | **Implement.** Framing unchanged, so the only risk is the source of the list, which section 6 keeps byte-compatible by default |

### W2 — OBD Mode 04, ClearEmissionsRelatedData

| Field | Content |
|---|---|
| Feature / DEV | **DEV-11** (clear half) |
| Existing behavior | `04` is a claimed SID and produces silence |
| Proposed behavior | `44`, a single byte with no data, after calling the shared `DtcStore.clear()` |
| Applicable specification | SAE J1979 / ISO 15031-5 service 04 |
| Normative text available | No |
| Official / public material | **P1**, verbatim: "A response of 44 from the vehicle indicates that the mode request has been carried out, the information erased, and the MIL turned off." **P1** also enumerates the effects: reset the number of trouble codes, erase any DTCs, erase freeze frame data, erase the DTC that initiated the freeze frame, erase all oxygen sensor test data, erase mode 06 and 07 information, and **not** erase permanent (mode 0A) codes |
| Independent secondary | **S1**: mode 04 "erases all stored data, including any enhanced codes and freeze frame information" and "resets monitors to Not Ready and turns the MIL off" |
| Open-source interoperability | **O2** `OBD_S04_PR` carries the service byte and nothing else. **O3** maps `CLEAR_DTC` (`b"04"`) to the `drop` decoder, i.e. no payload is expected |
| Capture evidence | None sought; three sources agree the response carries no data and there is nothing byte-variable to capture |
| Conflicting evidence | None. **P1** notes some vehicles require preconditions (engine not running) before responding; the simulator has no such precondition and always answers |
| Recommendation | **Implement, not standards validated.** One public primary vendor document states the response byte explicitly, an independent tool-vendor reference agrees on the effects, and two independent implementations expect an empty payload |

### W3 — OBD Mode 07, pending DTCs

| Field | Content |
|---|---|
| Feature / DEV | **DEV-11** (pending half) |
| Existing behavior | `07` is a claimed SID and produces silence |
| Proposed behavior | `47 <count> <2-byte code>…` over every DTC in the store whose **pending** flag is set; `47 00` when none |
| Applicable specification | SAE J1979 / ISO 15031-5 service 07 |
| Normative text available | No |
| Official / public material | **P1** lists "07 - show 'pending' trouble codes" among the ten modes and refers to "mode 06 and 07 information" as data that mode 04 erases. **P1 contains no worked mode 07 example**, so the `47` + count framing is not stated by it |
| Independent secondary | **S1**: "Mode seven provides a record of pending DTCs that set during the last completed drive cycle", and the two-trip description distinguishing pending from the mode-three list. **S2** agrees |
| Open-source interoperability | **O2** `OBD_S07_PR` = `47` + count + N × 2-byte DTC, structurally identical to `OBD_S03_PR`. **O3** maps `GET_CURRENT_DTC` (`b"07"`) to the *same* `dtc` decoder it uses for mode 03, which skips the mode and count bytes. Two independent implementations therefore treat 03 and 07 as the same payload shape |
| Capture evidence | **None found.** A search for a real mode 07 CAN response returned no usable capture |
| Conflicting evidence | None found. The gap is absence of primary evidence for the framing, not disagreement |
| Recommendation | **Implement, not standards validated**, and record the weaker basis. The response SID follows the `+0x40` rule **P1** states generally ("A mode 02 request is answered with a 42, a mode 03 with a 43, etc."), and the count-byte framing rests on two independent implementations plus structural identity with mode 03, whose count byte **P1** does document. This is materially weaker than W2. The alternative — leaving mode 07 silent — is worse for a testbench, because the pending/confirmed split is the whole point of the DTC store and DEV-11 pins the expected `47` |

### W4 — OBD Mode 01 PID 0x01, monitor status

| Field | Content |
|---|---|
| Feature / DEV | no DEV; `DEFERRED_MODE01_PIDS` in `protocols/obd/pids.py`, carried forward from Phase 5 |
| Existing behavior | Not advertised in the supported-PID mask and not answered; silence |
| Proposed behavior | Four bytes: A = MIL bit plus a 7-bit confirmed-DTC count, B/C/D = monitor supported and readiness bits |
| Applicable specification | SAE J1979 / ISO 15031-5 service 01 PID 01, with the J1979 Digital Annex for the monitor-bit registry |
| Normative text available | No |
| Official / public material | **P1** documents byte A and only byte A: "this byte does double duty, with the most significant bit being used to indicate that the malfunction indicator lamp (MIL …) has been turned on … while the other 7 bits of this byte provide the actual number of stored trouble codes", with the worked response `41 01 81 07 65 04`. For the rest **P1 explicitly refers the reader elsewhere**: "The remaining bytes in the response provide information on the types of tests supported by that particular module (see the J1979 document for further information)" |
| Independent secondary | **S2** publishes a full B/C/D layout including the spark-versus-compression bit. Most other hits are derivatives of it and were not counted |
| Open-source interoperability | **O3**'s `status` decoder implements a B/C/D layout consistent with **S2**, including the ignition-type branch |
| Capture evidence | **P1**'s single response, which exercises byte A only |
| Conflicting evidence | None found, but the public primary document declines to describe three of the four bytes and names the unavailable specification instead |
| Recommendation | **Defer.** Byte A is well corroborated and could be derived from the store today. Bytes B, C and D rest on one secondary table and implementations derived from the same lineage, which is exactly the standard this project applied when deferring DEV-03. Emitting three invented bytes to ship one good one is the wrong trade. What would unblock it: the J1979 or J1979-DA text, or two or more independent real captures of `01 01` from different vehicles with the monitor set decoded |

### W5 — UDS 0x14 ClearDiagnosticInformation

| Field | Content |
|---|---|
| Feature / DEV | **DEV-23** (0x14 half) |
| Existing behavior | No protocol claims SID 0x14, so the route's unsupported-service policy answers `7F 14 11` on a physical address. Measured above |
| Proposed behavior | Request `14 <groupOfDTC high> <mid> <low>`, exactly 4 bytes. `FF FF FF` means all DTCs and calls the shared `DtcStore.clear()`. Positive response is the single byte `54`, no data. Any other group value: `7F 14 31` (requestOutOfRange). Any other length, including the 5-byte `MemorySelection` form: `7F 14 13` |
| Applicable specification | ISO 14229-1:2026 clause for ClearDiagnosticInformation |
| Normative text available | No |
| Official / public material | **P3** `[SWS_Dcm_00247]`, `[SWS_Dcm_01263]`: "UDS Service ClearDiagnosticInformation (0x14) requests an ECU to clear the error memory. The service request contains the parameter: groupOfDTC", passed on as a DTC selection with `DTCFormat: DEM_DTC_FORMAT_UDS`. `[SWS_Dcm_01265]`: "In case Dem_GetDTCSelectionResultForClearDTC() returns DEM_WRONG_DTC, the Dcm shall send a NRC 0x31 (RequestOutOfRange)." **P2** `[SWS_Dem_01203]`: for DTC group 'all DTCs' the Dem "shall reset all event and DTC status bytes and clear event related data" |
| Independent secondary | — |
| Open-source interoperability | **O1** builds the request as SID + three bytes packed high/mid/low, defaults the group to `0xFFFFFF` documented as "all DTCs", declares `_no_response_data = True`, and lists exactly three supported NRCs: 0x13, 0x22, 0x31. **O2** `UDS_CDTCI` = service + `groupOfDTCHighByte` + `MiddleByte` + `LowByte`; `UDS_CDTCIPR` = the single byte `0x54`. Two independent implementations agree byte for byte |
| Capture evidence | None sought |
| Conflicting evidence | None. One nuance: **O1** adds an optional fifth `memory_selection` byte "introduced in ISO-14229-1:2020". The proposal rejects that form with NRC 0x13 rather than implementing it, because this simulator has one fault memory and supporting a selector it cannot honour would be worse than refusing it |
| Recommendation | **Implement, not standards validated.** Request shape, response shape and the 0x31 choice are each supported by an official public specification of another body plus two independent implementations. The *internal* effect of the clear is a separate question, answered in W12 |

### W6 — UDS 0x19/0x02 request framing

| Field | Content |
|---|---|
| Feature / DEV | **DEV-05** |
| Existing behavior | Exactly two bytes are accepted (`19 02` returns every DTC); the three-byte form carrying `DTCStatusMask` is rejected with `7F 19 13`, and the mask is never applied. Measured above |
| Proposed behavior | The request is exactly three bytes: `19 02 <DTCStatusMask>`. Two bytes become `7F 19 13`; four or more become `7F 19 13`. A DTC is reported when `(status & DTCStatusMask) != 0` |
| Applicable specification | ISO 14229-1:2026, ReadDTCInformation sub-function `reportDTCByStatusMask` |
| Normative text available | No |
| Official / public material | **P3**: "UDS Service 0x19 with subfunctions 0x02 or 0x13 requests the DTCs (and their associated status) that match certain conditions. The service request contains the parameter: DTCStatusMask." The `Dem_SetDTCFilter` description states the filter rule verbatim: "The server shall perform a bit-wise logical AND-ing operation between the parameter DTCStatusMask and the current UDS status in the server. In addition to the DTCStatusAvailabilityMask, the server shall return all DTCs for which the result of the AND-ing operation is non-zero [i.e. (statusOfDTC & DTCStatusMask) != 0]. The server shall process only the DTC Status bits that it is supporting … If no DTCs within the server match the masking criteria specified in the client's request, no DTC or status information shall be provided following the DTCStatusAvailabilityMask byte in the positive response message." |
| Independent secondary | — |
| Open-source interoperability | **O1** raises `ValueError('status_mask must be provided for subfunction 0x%02x')` for `reportDTCByStatusMask`: the byte is mandatory, not optional. **O2** makes `DTCStatusMask` a present field whenever `reportType` is one of `0x01, 0x02, 0x07, 0x08, 0x0f, 0x11, 0x12, 0x13` |
| Capture evidence | None sought |
| Conflicting evidence | None. The current two-byte acceptance appears to be an upstream defect and matches no source |
| Recommendation | **Implement, not standards validated.** Request framing is corroborated three ways. Note that this is **two** wire changes, not one: accepting the three-byte form, and *rejecting* the two-byte form that works today. Both belong to DEV-05 and both must flip their characterization lines in the same commit |

### W7 — UDS 0x19/0x02 DTCStatusAvailabilityMask value

| Field | Content |
|---|---|
| Feature / DEV | **DEV-16** (availability half) |
| Existing behavior | The constant `0xFF`, claiming all eight status bits are supported, while every status byte emitted is the unrelated constant `0x2F` |
| Proposed behavior | The OR of the status bits this simulator actually models. With the model in section 6 that is `0x8C` — bit 2 pendingDTC, bit 3 confirmedDTC, bit 7 warningIndicatorRequested. Emitted status bytes are ANDed with it |
| Applicable specification | ISO 14229-1:2026 |
| Normative text available | No |
| Official / public material | **P2** `Dem_GetDTCStatusAvailabilityMask`: "The value DTCStatusMask indicates the supported DTC status bits from the Dem. All supported information is indicated by setting the corresponding status bit to 1. See ISO14229-1." `[SWS_Dem_00657]`: "The Dem module shall mask all DTC status bytes … provided to the Dcm with the DTC status availability mask (by performing a bit-wise AND operation)." **P3** `[SWS_Dcm_01644]` places that value in the response |
| Independent secondary | — |
| Open-source interoperability | **O1** and **O2** both read the byte immediately after the sub-function echo as the availability mask; neither constrains its value |
| Capture evidence | None sought |
| Conflicting evidence | None on the *meaning*. The **value** is not fixed by any source: it is a property of the server. `0x8C` is therefore a **project-defined value**, correct by construction for the model this project implements, and it is not a standards claim |
| Recommendation | **Implement, not standards validated.** Keeping `0xFF` while modelling three bits would advertise five bits the simulator cannot set, and would make a tester's `19 02 01` (testFailed) request look supported and return nothing, which is the confusing outcome. Deriving the byte is the honest option and the derivation rule is well documented |

### W8 — UDS 0x19/0x02 per-DTC status byte

| Field | Content |
|---|---|
| Feature / DEV | **DEV-16** (status half) |
| Existing behavior | Every record carries the constant `0x2F` |
| Proposed behavior | The byte is derived per DTC from the store: bit 2 set when pending, bit 3 when confirmed, bit 7 when that DTC requests the indicator. Bits 0, 1, 4, 5 and 6 are never set and are excluded from the availability mask. With the profile default of section 6 the byte becomes `0x0C` |
| Applicable specification | ISO 14229-1:2026, DTC status byte |
| Normative text available | No |
| Official / public material | **P2** defines all eight bits in its acronym table, each "… bit of the UDS status byte", and closes with "UDS status byte — Status byte as defined in ISO 14229-1, based on DTC level". Bit 2 pendingDTC: "Indicates whether or not a diagnostic test has reported a testFailed result at any time during the current or last completed operation cycle." Bit 3 confirmedDTC: "Indicates whether a malfunction was detected enough times to warrant that the DTC is desired to be stored in long-term memory." Bit 7 warningIndicatorRequested: "Report the status of any warning indicators associated with a particular DTC." |
| Independent secondary | — |
| Open-source interoperability | **O1** `Dtc.Status.get_byte_as_int()` assigns 0x01 testFailed, 0x02 testFailedThisOperationCycle, 0x04 pending, 0x08 confirmed, 0x10 testNotCompletedSinceLastClear, 0x20 testFailedSinceLastClear, 0x40 testNotCompletedThisOperationCycle, 0x80 warningIndicatorRequested. **O2**'s `dtcStatus` flag table is bit-for-bit identical. Three independent renderings of the bit layout agree |
| Capture evidence | None sought |
| Conflicting evidence | None on the layout. The evidence does **not** establish which bits a simulator ought to set, only what each means. Setting only bits 2, 3 and 7 is a project decision, justified by section 6: those are the only three pieces of state this project can honestly claim to know |
| Recommendation | **Implement, not standards validated.** The layout is corroborated three independent ways. The *selection* of modelled bits is project-defined, stated as such, and made visible on the wire through the availability mask of W7 rather than hidden behind a `0xFF` that would be untrue |

### W9 — the third byte of the UDS DTC number

| Field | Content |
|---|---|
| Feature / DEV | **DEV-16** (third-byte half) |
| Existing behavior | Every UDS DTC record is `<J2012 high> <J2012 low> 0x01 <status>`; the third byte is the constant `0x01` |
| Proposed behavior | **No change.** The byte stays `0x01` and moves into `protocols/uds/dtc.py` unaltered |
| Applicable specification | SAE J2012 `J2012_202509` / ISO 15031-6, three-byte DTC format; the failure-type registry lives in the J2012 Digital Annex |
| Normative text available | No, for either document |
| Official / public material | None available |
| Independent secondary | — |
| Open-source interoperability | **O2**'s `DTC` packet is 2 bits system, 2 bits type, 12 bits numeric value code, then an `additional_information_code` byte — that is, the third byte is a failure-type/additional-information field, not part of the code the profile declares |
| Capture evidence | None |
| Conflicting evidence | None, because nothing supports any particular value. The profile declares five-character codes such as `P0001`, which carry no failure type; whatever this project puts in the third byte is invented |
| Recommendation | **Keep frozen; do not touch in Phase 6.** Changing `0x01` to `0x00` or to a configured value would be a wire change with no evidence behind it in either direction. DEV-16 is therefore split: its status half is fixed in Phase 6 (W8), its third-byte half stays open, and its 0x19 sub-function 0x01/0x0A half stays assigned to Phase 11. What would unblock it: the J2012 text for the failure-type byte, or a decision to make it profile-configurable, which is a design change rather than a correction |

### W10 — 0x19 negative-response ordering, a consequence of W6

| Field | Content |
|---|---|
| Feature / DEV | side effect of **DEV-05**; no DEV of its own |
| Existing behavior | The length check runs first: any 2-byte 0x19 request reaches the sub-function check, and any 3-byte one is rejected with `7F 19 13` before the sub-function is looked at. So `19 01` → `7F 19 12`, `19 0A` → `7F 19 12`, `19 00` → `7F 19 12`, `19 82 FF` → `7F 19 13`, `19` → `7F 19 13`. Measured above |
| Proposed behavior | Check a 1-byte minimum (`19` alone → `7F 19 13`), then the sub-function, then the length required *by that sub-function*. Unchanged: `19` → `7F 19 13`, `19 01` → `7F 19 12`, `19 0A` → `7F 19 12`, `19 00` → `7F 19 12`. **Changed:** `19 82 FF` → `7F 19 12` instead of `7F 19 13` |
| Applicable specification | ISO 14229-1:2026, general server response behavior (the order in which a server checks SID, sub-function and length) |
| Normative text available | **No, and this is the gap that matters here.** The check order is precisely what the unavailable text would settle |
| Official / public material | None located that states the ordering |
| Independent secondary | — |
| Open-source interoperability | Not applicable: **O1**, **O2** and **O3** are client-side and never choose a server's NRC |
| Capture evidence | None |
| Conflicting evidence | None, because no evidence was found either way |
| Recommendation | **Implement the ordering above, not standards validated, and record the one changed response explicitly.** The ordering is chosen to keep every currently pinned negative response unchanged except where DEV-05 forces a change; it is not claimed to be the ISO order. `19 82 FF` moves because sub-function `0x82` (the `suppressPosRspMsgIndicationBit` form, DEV-07, out of scope here) is now reached instead of being hidden behind a length check. That single transition needs its own characterization line in the DEV-05 commit |

### W11 — trailing bytes on the new OBD modes

| Field | Content |
|---|---|
| Feature / DEV | new behavior introduced alongside **DEV-11**; interacts with frozen **DEV-15** |
| Existing behavior | Mode 03 echoes a trailing request byte into the response (`03 00` → `43 00 02 94 77 00 01`), which is DEV-15 and is frozen as undetermined. Modes 04 and 07 are silent whatever follows them |
| Proposed behavior | Mode 04 and Mode 07 **ignore** any trailing byte: `04 00` → `44`, `07 00` → `47 …`. Mode 03 keeps its echo unchanged |
| Applicable specification | SAE J1979 / ISO 15031-5, malformed-request handling |
| Normative text available | No |
| Official / public material | **P1** shows both requests sent bare (`>03`, and mode 04 with no parameter). The optional digit an ELM327 accepts after a request is consumed by the adapter and never reaches the bus |
| Independent secondary | — |
| Open-source interoperability | **O2**'s `OBD_S04` and `OBD_S07` define the service byte and no further request field; **O3** sends `b"04"` and `b"07"` bare |
| Capture evidence | None |
| Conflicting evidence | None on what a well-formed request looks like; nothing at all on what a server should do with a malformed one, which is exactly DEV-15's open question |
| Recommendation | **Ignore trailing bytes on the new modes, and do not extend the DEV-15 echo to them.** The echo is a defect this project has decided it cannot resolve; propagating it into two new services would multiply an unexplained behavior instead of containing it. Record the asymmetry in DEV-15 so that whoever resolves it knows three services are involved |

### W12 — what `clear()` actually does

| Field | Content |
|---|---|
| Feature / DEV | plan rule 7; shared by **DEV-11** (Mode 04) and **DEV-23** (0x14) |
| Existing behavior | No clear operation exists anywhere. The configured DTC list is immutable for the life of the process |
| Proposed behavior | One `DtcStore.clear()`. It resets every entry's pending, confirmed and indicator-requested flags to false and clears the store-level indicator; it does **not** remove the DTC identities, which stay in the store so a later scenario can raise them again. Both callers invoke the same method and neither adds behavior of its own |
| Applicable specification | SAE J1979 service 04 for the OBD side; ISO 14229-1:2026 for 0x14 |
| Normative text available | No, for either |
| Official / public material | **P1** enumerates the OBD effects: reset the number of trouble codes, erase any DTCs, erase freeze frame data, erase the DTC that initiated the freeze frame, erase all oxygen sensor test data, erase mode 06 and 07 information, and explicitly **not** erase permanent (mode 0A) codes; and "the MIL turned off". **P2** `[SWS_Dem_00385]` gives the UDS side: "After a clear command has been applied to a specific DTC the Dem module shall set the UDS status byte to 0x50 (readiness bits 4 and 6 set to 1, and all others are set to zero)", with the note that this value "represents the delivery status (initial state) of this byte as well". **P2** `[SWS_Dem_01203]`: for group 'all DTCs' the Dem resets all status bytes and clears related data |
| Independent secondary | **S1**: mode 04 "erases all stored data … resets monitors to Not Ready and turns the MIL off" — "monitors to Not Ready" is the same statement as **P2**'s readiness bits 4 and 6 |
| Open-source interoperability | Not applicable: all three are clients and observe no server state |
| Capture evidence | None |
| Conflicting evidence | **A real difference between the two protocols, and it is the reason this row exists.** The two descriptions do not specify the same post-clear state. **P2** keeps the DTC present with status `0x50`; **P1** speaks of erasing the codes. Under **P2**'s model a subsequent `19 02 FF` would still return every supported DTC, each with status `0x50`, because `0x50 & 0xFF != 0`. Under **P1**'s model mode 03 returns nothing |
| Recommendation | **Implement the subset that both agree on, and document the simulator-specific transition.** Both agree that after a clear: no DTC is pending, none is confirmed or stored, and the indicator is off. That is the whole of what this project models, so both wire views agree: `03` → `43 00`, `07` → `47 00`, `19 02 FF` → `59 02 8C` with no records. The divergence is avoided rather than resolved: because bits 4 and 6 are outside this simulator's availability mask (W7), the question "should they be 1 after a clear" does not arise on the wire — **P3**'s "the server shall process only the DTC Status bits that it is supporting" is what makes that consistent. Record it as a deliberate, documented simulator state transition, not as ISO or J1979 behavior. Retaining identities rather than deleting them is likewise project-defined; it keeps a cleared profile re-raisable by the Phase 7 scenario engine and costs nothing on the wire |

### W13 — moving 0x10 and 0x11 out of the legacy module

| Field | Content |
|---|---|
| Feature / DEV | none; **DEV-07** and **DEV-17** must survive untouched |
| Existing behavior | `10 01`–`10 04` → `50 <type> 00 1E 0B B8`; `11 01`–`11 05` → `51 <type>` with the extra `0F` byte for `11 04`; `10 05`/`10 00` → `7F 10 12`; `11 81` → `7F 11 12`; short and long forms → `7F .. 13` |
| Proposed behavior | **Byte-identical.** The legacy `uds/` package is deleted and these two services are reimplemented in `protocols/uds/`, reproducing every byte above, including the DEV-07 rejection of `11 81` and the DEV-17 fixed parameter record |
| Applicable specification | ISO 14229-1:2026, services 0x10 and 0x11 |
| Normative text available | No |
| Official / public material | Not needed: nothing changes |
| Recommendation | **Reimplement with no wire change.** Phase 6 is not authorised to fix DEV-07 or DEV-17 and must not do so incidentally. Use the Phase 5 method: a differential script over the whole request space — every SID this ECU claims × every second byte 0x00–0xFF × lengths 1 to 5 — run against the legacy module and the replacement until the only differences are the ones listed in W5, W6, W7, W8 and W10 |

## 6. Recommended shared DTC-state model

### Layering

```
                       DtcStore                      domain state, no wire bytes
                 (identity + 3 flags)
                           |
        +------------------+------------------+
        |                                     |
 protocols/obd/dtc.py                 protocols/uds/dtc.py
 2-byte J2012 encoding                DtcRecord: 3-byte number + status byte
 mode 03 = confirmed view             registered on the ECU's DtcRegistry
 mode 07 = pending view               0x19/02 reads through the registry
 mode 04 -> store.clear()             0x14 -> the same store.clear()
```

The store is owned by the `Ecu`, as `Ecu.dtc_store`. The existing `Ecu.dtcs` attribute is
a `DtcRegistry`, which is the **UDS-side** provider registry from plan rule 6; the two
must not be conflated and the attribute should be renamed `Ecu.dtc_providers` in the same
commit that introduces the store, or the codebase will have two things called `dtcs`.

Neither encoder owns the store, and neither protocol may mutate it except through
`clear()`. `DtcRecord`, which already exists in `protocols/uds/providers.py`, is a UDS
encoding — a three-byte number and a status byte — and it stays on the UDS side of that
line. It is produced by an adapter that reads the store; the store never holds one.

### The state itself

Per DTC entry, exactly three flags plus the identity:

| Field | Type | Justification |
|---|---|---|
| `code` | the profile's five-character trouble code | already validated by the schema; the only identity the project has |
| `pending` | bool | **P2** bit 2; the distinction OBD mode 07 exists to report |
| `confirmed` | bool | **P2** bit 3; what OBD mode 03 reports and what "stored" means |
| `indicator_requested` | bool | **P2** bit 7; the per-DTC half of MIL |

Store level: `indicator_on` is derived, `any(entry.indicator_requested)`. It is not
independently settable, because a MIL that disagrees with every DTC is a state this
project cannot justify.

**"Stored" is not a fourth flag.** The plan's Phase 6 sentence lists "pending, confirmed,
stored, MIL", but the evidence makes `stored` and `confirmed` the same state seen from two
protocols: **S1** calls mode 03 "stored emission related DTCs", and **P2** defines the
confirmedDTC bit as the malfunction being "desired to be stored in long-term memory". A
separate flag would have no source that distinguishes it and nothing on the wire that
could tell the two apart. Recommendation: **three flags, with "stored" defined as the OBD
name for `confirmed`.** This is a deliberate narrowing of the plan sentence and needs the
user's agreement before implementation.

Everything else stays out: no operation cycles, no test-completion bits, no fault
detection counters, no freeze frames, no severity, no permanent DTCs, no mirror memory.
Each of those is a bit in someone's status byte, and none of them is state this project
can currently produce honestly.

### Initial state from the profile

The schema today is `dtcs: [str]`. It must be able to express the flags, and it must keep
today's bytes for a profile that does not:

```yaml
dtcs:
  - B1477                  # shorthand: pending = true, confirmed = true, indicator = false
  - code: P0001
    pending: true
    confirmed: false
    indicator_requested: false
```

The shorthand default is chosen to preserve W1: with `pending` and `confirmed` both true,
mode 03 returns exactly the bytes it returns today, and the derived UDS status byte is
`0x0C`. `indicator_requested` defaults to false, which is unobservable in Phase 6 because
PID 0x01 is deferred (W4) and therefore carries no wire risk; the alternative, deriving
the indicator from `confirmed`, would bake a policy into the store that the Phase 7
scenario engine should own.

## 7. Recommendation per deviation

| DEV | Scope in Phase 6 | Recommendation | Standards validated after |
|---|---|---|---|
| **DEV-05** | 0x19/02 request framing and mask filtering | **Implement** (W6). Both halves — accept `19 02 <mask>`, reject the 2-byte form — in one commit, with W10's `19 82 FF` transition pinned alongside. The strict xfail `test_0x19_02_with_status_mask_corrected_is_answered_positively` flips in that commit; `test_0x19_02_without_status_mask_returns_all_dtcs_with_fixed_status` is rewritten in it. Filtering is `(status & mask) != 0` per **P3**, applied to a **project-defined** status byte — the parsing and the filtering rule are corroborated, the bit values are ours | no |
| **DEV-11** | OBD modes 04 and 07 | **Implement** (W2, W3). Two commits, `44` first. Both strict xfails flip. Mode 07's framing evidence is weaker than Mode 04's and the conformance row must say so | no |
| **DEV-16** | UDS DTC status and third byte | **Split.** Status byte and availability mask: implement (W7, W8). Third byte `0x01`: **freeze**, no evidence either way (W9). Sub-functions 0x01 and 0x0A: unchanged, Phase 11. The register entry needs rewriting into three parts | no |
| **DEV-23** | 0x14 and 0x3E | **0x14: implement** (W5). Its strict xfail `test_0x14_clear_diagnostic_information_corrected` flips. **0x3E: untouched**, Phase 7; its xfail stays strict and must not become an unexpected XPASS when the legacy module is replaced | no |
| **DEV-15** | Mode 03 trailing byte | **Unchanged and still open.** Update the entry to record that modes 04 and 07 deliberately do not echo (W11), so three services are now in its blast radius | unchanged |
| **DEV-07** | `suppressPosRspMsgIndicationBit` | **Unchanged.** Out of Phase 6 scope and must survive the legacy rewrite byte for byte (W13). Note W10 makes `19 82 FF` reach the sub-function check, which is a visible consequence but not a fix | unchanged |
| **DEV-17** | session parameter record | **Unchanged.** Must survive the rewrite byte for byte (W13) | unchanged |
| **DEV-03** | Mode 09 PID 0A | Untouched, frozen | unchanged |
| **DEV-18** | multi-PID Mode 01 | **Not in Phase 6 and raised for decision.** The plan assigned it to Phase 5 and Phase 5 did not implement it. Clean evidence already exists — the ELM327 datasheet's "Multiple PID Requests" section with a worked CAN capture. It is an OBD Mode 01 change, unrelated to DTC state, and absorbing it into Phase 6 silently would break the one-wire-change-per-commit discipline. Recommend a decision: take it as a separate commit inside Phase 6, or reassign it to Phase 7 | no |

## 8. Behaviors safe to implement now

1. `DtcStore` with the three-flag model of section 6 and one `clear()` (W12).
2. OBD Mode 03 re-sourced from the store, bytes unchanged by default (W1).
3. OBD Mode 04 → `44`, via `DtcStore.clear()` (W2).
4. OBD Mode 07 → `47 <count> …`, weaker evidence, recorded as such (W3).
5. UDS 0x14 → `54`, groupOfDTC `FF FF FF` only, NRC 0x31 otherwise, NRC 0x13 on any other
   length, via the same `DtcStore.clear()` (W5).
6. UDS 0x19/02 three-byte request with mask filtering (W6), derived availability mask
   (W7) and derived status byte (W8), with the negative-response ordering of W10.
7. Modes 04 and 07 ignoring trailing bytes (W11).
8. Byte-identical reimplementation of 0x10 and 0x11, then deletion of `uds/`,
   `dtc_utils.py` and `config/legacy.py` (W13).

## 9. Behaviors to defer

| Behavior | Reason | What would unblock it |
|---|---|---|
| Mode 01 PID 0x01 monitor status | bytes B, C and D rest on one secondary lineage; the public primary document declines to describe them (W4) | J1979 or J1979-DA text, or two independent real captures of `01 01` decoded |
| UDS DTC third byte | no evidence for any value, including the current one (W9) | J2012 text, or a decision to make it configurable |
| 0x19 sub-functions 0x01 and 0x0A | Phase 11 in the plan | — |
| 0x3E TesterPresent | Phase 7 in the plan | — |
| `MemorySelection` on 0x14 and 0x17 | one fault memory in this simulator | a multi-memory model, and ISO 14229-1:2026 text |
| status bits 0, 1, 4, 5, 6 | need an operation-cycle and monitor-completion model this project does not have | the Phase 7 scenario engine, and then evidence for each bit |
| DEV-15 trailing-byte echo | undetermined since Phase 0 | J1979 text on malformed-request handling |

## 10. Conformance status expected after implementation

`standards validated` is **no** for every row. `hardware validated` is **no** everywhere
until Phase 8.

| Service | Sub-function / PID | Implemented | Unit tested | Integration tested | Evidence note |
|---|---|---|---|---|---|
| OBD 0x03 | — | yes | yes | yes | count-byte framing from the ELM327 datasheet; list is the store's confirmed view |
| OBD 0x04 | — | **yes** (was no) | yes | yes | `44` stated verbatim in the ELM327 datasheet; effects corroborated by an independent tool vendor |
| OBD 0x07 | — | **yes** (was no) | yes | yes | **framing from two independent implementations only**; no primary worked example found |
| OBD 0x01 | 0x01 | no | yes (absence) | yes (absence) | deferred: monitor bits not corroborated; byte A alone is |
| UDS 0x14 | — | **yes** (was no) | yes | yes | request and response shape from AUTOSAR Dcm plus two independent implementations; group `FFFFFF` only |
| UDS 0x19 | 0x02 | yes, corrected | yes | **yes** (was no) | mask now required and applied; filter rule `(status & mask) != 0` |
| UDS 0x19 | 0x01, 0x0A | no | yes | no | Phase 11 |
| UDS 0x10, 0x11 | — | yes, unchanged | yes | yes | reimplemented byte-for-byte; DEV-07 and DEV-17 intact |
| UDS 0x3E | — | no | yes (DEV-23) | no | Phase 7 |

New runtime rows: "DTC state shared by OBD and UDS, one clear operation" and "DTC status
byte derived from modelled state, availability mask derived from it" — both project
behavior, `standards validated` not applicable to the first and `no` for the second.

## 11. Outcome

| Item | Decision | Standards validated after |
|---|---|---|
| DtcStore, three flags, one clear | Implement; "stored" folded into `confirmed` — **needs user agreement**, it narrows the plan sentence | n/a |
| OBD Mode 03 re-sourced | Implement, bytes unchanged | no |
| OBD Mode 04 (DEV-11) | Implement | no |
| OBD Mode 07 (DEV-11) | Implement, weaker evidence recorded | no |
| OBD Mode 01 PID 0x01 | **Defer** | unchanged |
| UDS 0x14 (DEV-23) | Implement, group `FFFFFF` only | no |
| UDS 0x19/02 framing and filtering (DEV-05) | Implement, two wire changes | no |
| UDS availability mask and status byte (DEV-16) | Implement | no |
| UDS DTC third byte (DEV-16) | **Freeze** | unchanged |
| 0x10, 0x11 rewrite | No wire change, proved by a differential run | unchanged |
| DEV-18 multi-PID | **Raised for decision**, not absorbed | — |

## 12. Decisions taken on this review

Recorded 2026-09-19, when this review was accepted, so the record says what was decided
and not only what was proposed.

| Question this review raised | Decision |
|---|---|
| Fold "stored" into `confirmed`? | **Yes.** Three flags: `pending`, `confirmed`, `indicator_requested`. Documented in `dtc/store.py` as a project-domain modeling decision for the behavior currently needed, explicitly **not** a claim that SAE or ISO define the two concepts as equivalent |
| Ship OBD Mode 07 on two-implementation evidence? | **No, defer.** Two open-source implementations and no sufficiently independent public worked wire example is below the bar section 11 sets for new wire behavior. The generic `pending` state is implemented and tested, because it is useful domain state; the Mode 07 response handler is not. DEV-11 is split so Mode 04 reads fixed while Mode 07 reads deferred, and the register names what would unblock it |
| Where does DEV-18 go? | **Phase 5.1**, its own gated phase, completed before Phase 6 began. It belongs to the Mode 01 request parser and has no relationship to DTC state. See [0005](0005-phase-5-1-multi-pid-evidence.md) |
| What does `clear()` leave behind? | Configured identity remains; `pending`, `confirmed` and `indicator_requested` all false. Every protocol view then reports nothing. Documented as a deliberately limited simulator transition supported by the modelled subset, not a universal SAE or ISO post-clear state. The AUTOSAR post-clear byte `0x50` is **not** reproduced: bits 4 and 6 are outside the advertised availability mask, and modelling them only to reproduce a byte the simulator cannot otherwise express would be inventing state |
| Availability mask | Derived from the modelled bits, `0x8C`, not hard-coded `0xFF`. A test asserts no producible status byte sets a bit outside it |
| UDS DTC third byte | **Frozen** at `0x01`, exactly as characterized |
| DEV-05 as one commit or two? | **Three**, because they separated cleanly: the sub-function/length ordering first, on its own, with `19 82 FF` pinned before and after; then the mask requirement and filtering; then the derived status byte. Each has distinct tests and its own before-and-after record |
| 0x14 group handling | `FFFFFF` only. Any other group is `7F 14 31` and clears nothing; any other length, including the five-byte `MemorySelection` form, is `7F 14 13`. Both paths tested |

### Discovered during implementation

Two things this review did not anticipate, both recorded here because they are consequences
of making ECU state mutable for the first time:

- `DtcStore` must **copy** the states it is constructed from. It mutates them, so aliasing
  one would let two stores built from a shared list clear each other. Found by a test that
  passed alone and failed in the suite.
- The integration suite's simulator subprocess is module-scoped, which cost nothing while
  every request was a read. A test that clears now decides what the tests after it see, so
  a `mutating` fixture restarts the process for those tests.

## Revision check, 2026-09-19

This record cites AUTOSAR **R24-11**. R24-11 has since been found to be superseded by
**R25-11**, which was published and is the current release. Every requirement cited here
was re-checked against R25-11 and is present, with the load-bearing texts identical. The
evidence, the recommendations and the implemented behavior are unaffected; read every
"R24-11" below as "R25-11, unchanged from R24-11 in every respect this record relies on".
See section 7.1 of the plan.
