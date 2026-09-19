# 0005 — Phase 5.1 evidence review: several Mode 01 parameters in one request

Status: proposed 2026-09-19, before any Phase 5.1 wire change.

Produced under the documentation and standards verification gate
([modernization-plan.md section 11](../modernization-plan.md)). It covers **DEV-18 only**.
No other deviation, service or parameter is in scope, and no DTC work is touched:
Phase 5.1 exists precisely so that a Mode 01 request-parser change is not smuggled into
the Phase 6 DTC phase.

## 1. Applicable documentation

| Item | Revision | Source type | Full text available? | Relevant to this phase |
|---|---|---|---|---|
| SAE J1979 | `J1979_202505`, reaffirmed 2025-05-23; last technical revision `J1979_201702` | Official publisher listing (SAE Mobilus) | **No** — purchase or subscription | Defines whether and how several parameters may be requested in one service 01 message |
| ISO 15031-5 | International equivalent of J1979 | Publisher listing | **No** — licensed | Same content |
| ISO 15765-4 | not reviewed | — | **No** | Named by the evidence as the reason the feature is CAN-only. No framing rule of ours changes; the kernel segments whatever we produce |
| SAE J1979-DA | `J1979DA_202607`, revised 2026-07-16 | Official publisher listing | **No** | Not needed. This phase changes no parameter encoding |
| ELM327 datasheet | ELM327DSJ, Elm Electronics | Public vendor datasheet | **Yes** | "Multiple PID Requests", p. 45. Two worked CAN captures and the six-parameter limit |

Revisions for J1979 and its Digital Annex are reused from the Phase 5 review
([0003](0003-phase-5-obd-evidence.md)) and were not re-researched; nothing has changed
since 2026-09-18.

**The normative text is unavailable.** Nothing below is a conformance claim, and no row in
[conformance.md](../conformance.md) gains `standards validated` from this phase.

## 2. Evidence sources used

- **P1** ELM327 datasheet ELM327DSJ, "Multiple PID Requests", page 45. Public primary
  vendor document containing two worked CAN captures.
- **O1** Open-source implementations. Not relied on for this change and not cited below:
  the tester-side libraries read elsewhere in this project (`python-OBD`, Scapy) build
  single-parameter requests and would demonstrate nothing about a server's behavior here.

Only one source is used, and that is deliberate: **P1** is a public primary document that
states the rule, states the limit, and shows the bytes twice.

## 3. DEV-18 — several Mode 01 parameters in one request

| Field | Content |
|---|---|
| Feature / DEV | **DEV-18** |
| Existing behavior | Only the second request byte is read. `01 0D 0C` is answered `41 0D <speed>`; every parameter after the first is discarded. Measured, not recalled: `ObdProtocol.handle` takes `payload[1]` and comments the rest away as DEV-18 |
| Proposed behavior | Every requested Mode 01 parameter, up to six, is answered in one response: `41` once, then each parameter identifier followed by its data, in the order requested |
| Applicable standard | SAE J1979 / ISO 15031-5 service 01; the CAN-only restriction is attributed to ISO 15765-4 |
| Normative text available | **No** |
| Official / public documentation | **P1**, verbatim: "The SAE J1979 (ISO 15031-5) standard allows requesting multiple PIDs with one message, but only if you connect to the vehicle with CAN (ISO 15765-4). Up to six parameters may be requested at once, and the reply is one message that contains all of the responses." |
| Independent secondary references | None sought. **P1** is a primary public document that both states the rule and demonstrates it; a secondary table restating it would add nothing |
| Capture evidence | **P1** publishes two worked CAN captures of the same four parameters in different orders. `>01 04 05 0B 0C` answered `00A` / `0: 41 04 3F 05 44 0B` / `1: 21 0C 17 B8 00 00 00`, that is ten bytes `41 04 3F 05 44 0B 21 0C 17 B8`. `>01 0B 04 0C 05` answered `00A` / `0: 41 0B 21 04 3F 0C` / `1: 17 B8 05 44 00 00 00`, that is `41 0B 21 04 3F 0C 17 B8 05 44`. The trailing `00`s are ISO-TP padding beyond the stated length of 0x0A and are not part of the response |
| Open-source interoperability | Not used. See **O1** above |
| Conflicting evidence | None found |
| Recommendation | **Implement in Phase 5.1, not standards validated.** A public primary document states the rule, fixes the limit at six, and shows the exact bytes twice. This is the strongest evidence this project has assembled for any wire change so far: the two captures are byte-reproducible by the simulator and are used as the tests |
| Conformance status after | implemented yes, unit tested yes, integration tested yes, hardware validated no, standards validated **no**. Interoperability evidence: the two worked CAN captures in the ELM327 datasheet are reproduced byte for byte by a test vehicle configured to the values they show |

### 3.1 What the response looks like

**P1**'s second capture settles the framing that a one-parameter-at-a-time reading would
leave ambiguous:

- the positive-response service byte `41` appears **once**, not once per parameter;
- each parameter identifier is echoed immediately before its own data bytes;
- the response carries no count field and no separator;
- the reply is "one message", so it is one ISO-TP transfer, segmented by the kernel when
  it exceeds a single frame.

**P1** also states that "the order in which the PIDs appear in the response does not have
to match the order in which they were requested", and its second capture does in fact
answer in request order. This project answers in request order: it is what the captures
show, it is the least surprising, and the permission to reorder is not an obligation.

### 3.2 Questions the evidence does not answer

Four behaviors are needed to implement this and are **not** settled by **P1** or by any
source found. Each is decided below as a project choice, recorded as such, and tested. None
is presented as specification behavior.

| Question | Decision | Reason |
|---|---|---|
| More than six parameters requested | Answer the first six; ignore the remainder | Today the parser ignores every byte after the first parameter. This moves the cut-off from one to six and changes nothing else about how surplus bytes are treated, so no request answered today gets a shorter answer. `01` plus six parameters is seven bytes, exactly one CAN single frame, which is visibly why the limit is six |
| A requested parameter this vehicle does not support | Omit it from the response; if none of the requested parameters is supported, send nothing | Exactly today's behavior generalised. A single unsupported parameter is answered with silence today, and that stays true. Answering the supported subset is what makes a partial request useful; refusing the whole request because one parameter is missing would discard data the tester asked for and could get |
| The same parameter requested twice | Answer it once, in the position of its first occurrence | Preserves today's bytes for `01 0C 0C`, which is answered `41 0C <rpm>` now and after. Echoing a parameter twice would be a wire change with nothing behind it |
| A supported-parameter range identifier mixed with data parameters, for example `01 00 0C` | Resolve it by the same rules as any other parameter, including the DEV-04 advertised-range check | The range identifiers are parameters. Treating them specially inside a multi-parameter request and not outside one would be an inconsistency with no source |

### 3.3 Explicitly out of scope

- **Mode 09.** **P1**'s section and both captures are service 01. Mode 09 keeps reading
  one parameter and is untouched.
- **Mode 03 and DEV-15.** The trailing-byte echo stays exactly as it is. Mode 03 does not
  take parameters and this change does not reach it.
- **Every parameter encoding.** No formula, length or signal path changes. The response
  bytes for any single parameter are identical before and after.
- **The non-CAN restriction.** **P1** limits the feature to CAN. This simulator only
  speaks ISO-TP over CAN, so there is no other transport on which to withhold it, and no
  protocol-detection logic is introduced.

## 4. Outcome

| Deviation | Decision | Standards validated after |
|---|---|---|
| DEV-18, several Mode 01 parameters in one request | Implement in Phase 5.1 | no |

One wire change, one commit, preceded by a characterization test pinning today's
first-parameter-only behavior, and carrying the transition of the DEV-18 register entry
from open to fixed.
