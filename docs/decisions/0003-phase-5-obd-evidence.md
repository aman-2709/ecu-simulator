# 0003 — Phase 5 evidence review: OBD supported-PID and Mode 09 corrections

Status: accepted 2026-09-18, before any Phase 5 wire change.

Produced under the documentation and standards verification gate
([modernization-plan.md section 11](../modernization-plan.md)). It covers only DEV-02,
DEV-03 and DEV-04. J1979-2, J1979-3, DoIP and other roadmap standards were not researched.

## 1. Applicable documentation

| Item | Revision | Source type | Full text available? | Relevant to this phase |
|---|---|---|---|---|
| SAE J1979 | `J1979_202505`, reaffirmed 2025-05-23; last technical revision `J1979_201702` (Feb 2017); originally issued Dec 1991 | Official publisher listing (SAE Mobilus) | **No** — purchase or subscription | Defines the service 01 and 09 response formats all three deviations concern |
| SAE J1979-DA (Digital Annex) | **`J1979DA_202607`, revised 2026-07-16** (current). `J1979DA_202508` and `J1979DA_202504` are superseded/historical | Official publisher listing (SAE Mobilus) | **No** — purchase or subscription | Holds the registry of data identifiers; would settle the PID 0A field layout |
| ISO 15031-5 | International equivalent of J1979 | Publisher listing | **No** — licensed | Same content; equally unavailable |
| SAE J2012 | not reviewed | — | n/a | **Not applicable.** These three deviations do not touch DTC encoding |
| ISO 15765-4 | not reviewed | — | n/a | **Not applicable.** No transport or framing rule changes; see note below |
| ELM327 datasheet | ELM327DSJ, Elm Electronics | Public vendor datasheet | **Yes** | Tester-side behavior and worked CAN captures for service 09 and service 01 |

**The normative text is unavailable.** Neither J1979 nor its Digital Annex can be read by
this project. Nothing below is a conformance claim, and no row in
[conformance.md](../conformance.md) gains `standards validated` from this phase.

Recorded explicitly, for the avoidance of doubt:

- current Digital Annex revision checked: **`J1979DA_202607`** (2026-07-16);
- normative text available: **no**;
- therefore **none** of the Phase 5 corrections are `standards validated`;
- **no claim is made that the public evidence gathered below reflects every requirement of
  `J1979DA_202607`.** It reflects observed tester and vehicle behavior and public technical
  description, nothing more. A later revision may add, narrow or contradict requirements
  this project cannot see.

The revision metadata was corrected from `J1979DA_202508` to `J1979DA_202607` after this
review was first written. The publicly visible publisher metadata for the current revision
describes the same scope, a global registry of regulated emissions and propulsion related
data identifiers, and revealed nothing that contradicts the evidence or conclusions below,
which are therefore unchanged.

Note on ISO 15765-4: DEV-02 does not change the response length, so the existing ISO-TP
multi-frame path is untouched. DEV-03 would change it from 22 to 23 bytes, which stays
multi-frame and is segmented by the kernel; no framing rule of ours changes either way.

## 2. Evidence sources used

- **P1** ELM327 datasheet, "Multiline Responses", pages 42 to 43. Public primary document
  for the tester side. Contains worked CAN captures.
- **P2** Real CAN capture of service 09 PID 02 on a 2016 Jeep Cherokee, published at
  summivox.wordpress.com. Blog, therefore not normative; used only as capture evidence.
- **P3** Real vehicle capture of services 09 PID 02 and PID 0A via the Scapy automotive
  tooling, published in the dissec.to knowledge base.
- **S1** Wikipedia "OBD-II PIDs". Secondary. Several other hits (Grokipedia, HandWiki,
  scribd copies, several vendor pages) are derivatives of this table and were **not**
  counted as independent corroboration.
- **O1** Open-source implementations (Scapy automotive, python-OBD and similar).
  Interoperability evidence only; never treated as normative.

## 3. DEV-04 — supported-PID continuation bit

| Field | Content |
|---|---|
| Current behavior | Bit 0 of the 4-byte mask is set for every range from 0x00 to 0xC0, and for Mode 09 PID 00, regardless of whether any PID exists in the next range. Today: `01 00` → `41 00 08 08 00 01`, `01 40` → `41 40 00 00 80 01`, `01 60` → `41 60 00 00 00 01`, and so on to `01 C0`; Mode 09 `09 00` → `49 00 40 40 00 01` |
| Proposed behavior | Bit 0 set only when at least one PID exists in the next range. The advertised chain then terminates after the last populated range, and range PIDs that are no longer advertised are no longer answered |
| Applicable standard | SAE J1979 / ISO 15031-5, service 01 PID 00 and its continuations |
| Normative text available | No |
| Official/public documentation | **P1** publishes a real CAN capture of `01 00` answered by two ECUs: engine `41 00 BE 3E B8 11` and transmission `41 00 80 10 80 00`. The transmission's last byte is `0x00`, so its bit 0 is clear; it supports only PIDs 0x01, 0x0C and 0x11 and advertises no next range. The engine's last byte is `0x11`, bit 0 set, and it does have PIDs beyond 0x20 |
| Independent secondary references | **S1** and several independent pages state that bit 0 corresponds to PID 0x20, which is itself the "PIDs supported 0x21-0x40" identifier, so the bit is by construction a claim to support the next range query |
| Capture evidence | **P1** above is decisive: one real ECU sets the bit and one clears it, in the same capture, matching their actual PID coverage. **P3** shows an ECU whose mask includes PID20 and which does answer 0x20 and 0x40 |
| Open-source interoperability | Testers walk the chain while bit 0 is set and stop when it clears (**O1**). Not relied upon |
| Conflicting evidence | None found. Note a nuance: no source states a *requirement* to clear the bit. Strictly, bit 0 means "PID 0x20 is supported", and today's simulator does answer 0x20, so its behavior is self-consistent rather than self-contradictory. What the capture evidence establishes is that real ECUs clear the bit when they have nothing further, and that today's simulator advertises four empty ranges in service 01 and a whole nonexistent range chain in service 09, where no PID above 0x0D is defined at all |
| Recommendation | **Implement in Phase 5.** Corroborated by a real two-ECU capture in a public primary document plus consistent secondary description, with no conflicting evidence. It removes advertisement of ranges that contain nothing and saves a tester four useless round trips |
| Conformance status after | implemented yes, unit tested yes, integration tested yes, hardware validated no, standards validated **no**. Interoperability evidence: matches the per-ECU continuation behavior captured in the ELM327 datasheet |

Implementation note for the change itself: clearing the bit must be matched by no longer
answering the range PIDs that are no longer advertised, otherwise the simulator advertises
one thing and answers another. Both halves belong in the same commit.

## 4. DEV-02 — Mode 09 PID 02 VIN item count

| Field | Content |
|---|---|
| Current behavior | `49 02 00` followed by the 17-byte VIN. Total 20 bytes. The count byte is present but zero |
| Proposed behavior | `49 02 01` followed by the same 17 bytes. Total 20 bytes, **unchanged** |
| Applicable standard | SAE J1979 / ISO 15031-5, service 09 PID 02 |
| Normative text available | No |
| Official/public documentation | **P1** states it directly for a CAN vehicle: "The third byte (the '01'), tells the number of data items that are to follow (the vehicle can only have one VIN, and this agrees with that)", with the worked capture `014` then `0: 49 02 01 31 44 34`, `1: 47 50 30 30 52 35 35`, `2: 42 31 32 33 34 35 36`. Total 0x14 = 20 bytes. **P1** shows the same pattern for service 09 PID 04, `49 04 01` followed by a 16-byte calibration identifier |
| Independent secondary references | **S1** describes the byte after the PID as the number of data items, value 1 for the VIN |
| Capture evidence | Three independent real captures agree: **P1** (CAN vehicle, chip vendor datasheet), **P2** (2016 Jeep Cherokee, `7E8 # 10 14 49 02 01 53 48 48` then consecutive frames), **P3** (Mercedes, decoded as count 1) |
| Open-source interoperability | Decoders skip one count byte after the PID (**O1**). Not relied upon |
| Conflicting evidence | None found. No source describes a zero count byte; today's `0x00` appears to be an upstream defect |
| Recommendation | **Implement in Phase 5.** Three independent captures, one of them in a public primary document that also explains the byte, and no conflicting evidence. The response length does not change, so the existing multi-frame path and its flow control are unaffected, satisfying the compatibility condition |
| Conformance status after | implemented yes, unit tested yes, integration tested yes, hardware validated no, standards validated **no**. Interoperability evidence: byte-for-byte agreement with captures in the ELM327 datasheet and two real vehicles |

## 5. DEV-03 — Mode 09 PID 0A ECU name framing

| Field | Content |
|---|---|
| Current behavior | `49 0A` followed by 20 bytes, with no count byte, and the name **left**-padded with NULs: today `49 0a 00 00 00 00 00 00 00` + `ECU_SIMULATOR`. Total 22 bytes |
| Proposed behavior | `49 0A 01` followed by a 20-byte name field. Total 23 bytes. Padding layout per specification, which the register itself already marks as only medium confidence |
| Applicable standard | SAE J1979 / ISO 15031-5 service 09 PID 0A, and the J1979 Digital Annex for the field layout |
| Normative text available | No |
| Official/public documentation | **P1 contains no service 09 PID 0A example.** It documents PID 02 and PID 04 only. The count byte for PID 0A is therefore inferred from the pattern of the other two service 09 identifiers, which is exactly the inference this review was told not to make |
| Independent secondary references | **S1** states a 20-byte ASCII name "right-padded with null chars (0x00)", and separately lists PID 09 as an ECU-name message count for PID 0A. Most other hits are derivatives of that same table and are not independent |
| Capture evidence | **P3** decodes one real ECU name as `ECM\x00-EngineControl` with count 1. This establishes that a count byte exists and is 1, and that the name is **not** left-padded, since it begins with "ECM". It does not establish the full 23-byte payload: the decoded value is 18 characters, contains an embedded NUL, and the trailing bytes are not shown. No second independent capture of PID 0A was found |
| Open-source interoperability | Implementations read a count byte then a 20-byte field (**O1**). Under the decision rule, an open-source implementation alone is not sufficient support |
| Conflicting evidence | No direct conflict, but an unresolved question. The one capture shows an embedded NUL inside the name, which is consistent with a fixed-width field rather than simple trailing padding. Whether the 20 bytes are right-padded, or a structured fixed field, cannot be settled from what is available |
| Recommendation | **Defer.** The count byte is well supported, but the exact byte layout is not independently corroborated, and the user's rule for this deviation requires exactly that. Implementing only the count byte would change the wire once now and again later, and would leave the padding direction contradicting the only capture evidence available |
| Conformance status | Unchanged. The row stays as it is in `conformance.md`, and the DEV-03 strict xfail stays in place |

What would unblock DEV-03: the J1979 or J1979-DA text for service 09 PID 0A, or two or
more independent real captures from different vehicles showing the complete payload
including the trailing bytes.

## 6. Outcome

| Deviation | Decision | Standards validated after |
|---|---|---|
| DEV-04 supported-PID continuation bit | Implement in Phase 5 | no |
| DEV-02 Mode 09 VIN item count | Implement in Phase 5 | no |
| DEV-03 Mode 09 ECU name framing | Defer; xfail retained | unchanged |

DEV-04 and DEV-02 are independent of each other and are implemented as separate commits,
each with its own characterization transition. Deferring DEV-03 does not block the rest of
Phase 5.
