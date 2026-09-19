# Conformance and implementation status

What this simulator actually does, and how far each behavior has been verified. Created in
Phase 4 and seeded with the behavior present at that point; extended by every later phase
that changes behavior.

The five status dimensions are defined in
[modernization-plan.md section 6](modernization-plan.md). They are the only status
vocabulary used in this project:

| Dimension | Meaning |
|---|---|
| implemented | the behavior exists in this codebase |
| unit tested | pinned by a unit or characterization test |
| integration tested | exercised over the real kernel ISO-TP path on a vcan interface |
| hardware validated | exercised on physical CAN with named hardware and firmware |
| standards validated | checked against the applicable specification revision, with the clause cited |

**Nothing in this project is `standards validated`.** No row below has been checked against
the text of the applicable SAE or ISO document, because those documents are licensed and
are not available to this project. See
[modernization-plan.md section 7.1](modernization-plan.md) for the document-by-document
availability, and [known-deviations.md](known-deviations.md) for behaviors believed to be
wrong. Agreement with vehicle captures, open-source implementations or hardware adapters
does not fill that column.

`hardware validated` is empty everywhere until Phase 8, which is the physical CAN and
ELM327 acceptance phase.

Interoperability evidence, where it exists, is recorded as a note under the relevant table
rather than as a status column.

## Transport

| Behavior | Implemented | Unit tested | Integration tested | Hardware validated | Standards validated |
|---|---|---|---|---|---|
| ISO-TP over kernel `CAN_ISOTP`, Classical CAN | yes | yes | yes | no | no |
| Normal 11-bit addressing | yes | yes | yes | no | no |
| Normal fixed 29-bit addressing | no | n/a | n/a | n/a | no |
| Single-frame request and response | yes | yes | yes | no | no |
| Multi-frame response with tester flow control | yes | no | yes | no | no |
| Functional and physical sockets sharing a response id | yes | yes | yes | no | no |
| TX padding to 8-byte frames, configurable pad byte | yes | yes | yes | no | no |
| Interface and `CAN_ISOTP` availability checks | yes | yes | yes | no | no |

Interoperability evidence: the kernel behavior these rows depend on, including several
sockets sharing one functional receive identifier, was established experimentally on
Linux 6.8 and 6.17 and recorded in
[decisions/0001-isotp-binding.md](decisions/0001-isotp-binding.md). Manual verification
used can-utils `candump`, `isotpsend` and `isotprecv`.

## Addressing and routing

| Behavior | Implemented | Unit tested | Integration tested | Hardware validated | Standards validated |
|---|---|---|---|---|---|
| Functional requests on `0x7DF`, answered on `0x7E8` | yes | yes | yes | no | no |
| Physical requests on `0x7E0`, answered on `0x7E8` | yes | yes | yes | no | no |
| Physical requests on `0x7E1`, answered on `0x7E9` | yes | yes | yes | no | no |
| Response identifier = request identifier + 8 | yes | yes | yes | no | no |
| Per-address protocol eligibility | yes | yes | yes | no | no |
| Per-route unsupported-service policy | yes | yes | yes | no | no |
| Functional fan-out to several ECUs | no | yes (rejected) | no | n/a | no |

The `+ 8` convention and the `0x7DF` / `0x7E0` / `0x7E8` identifiers follow ISO 15765-4
usage as it is publicly described. The specification text has not been reviewed.

## OBD-II services

Served by the parameter table in `protocols/obd/pids.py`, which reads vehicle state
through signal paths. Modes `0x01` to `0x0A` are accepted as valid service identifiers;
those not listed below produce no response (DEV-11).

`standards validated` is `no` for every row: SAE J1979 (`J1979_202505`) and its Digital
Annex (`J1979DA_202607`) are licensed and have not been read by this project. The evidence
column records what each encoding actually rests on.

| Service | PID | Description | Implemented | Unit tested | Integration tested | Hardware validated | Standards validated | Evidence |
|---|---|---|---|---|---|---|---|---|
| 0x01 | 0x00/0x20/0x40 | supported parameters | yes | yes | yes | no | no | derived from the table; continuation behavior from a two-ECU capture in the ELM327 datasheet |
| 0x01 | 0x01 | monitor status | **no** | yes (absence) | yes (absence) | n/a | no | deferred: DTC-store semantics belong to Phase 6 and the monitor bits are not corroborated |
| 0x01 | 0x04 | calculated engine load | yes | yes | no | no | no | ELM327 datasheet capture (`04 3F`) |
| 0x01 | 0x05 | engine coolant temperature | yes | yes | no | no | no | ELM327 datasheet capture (`05 44`) |
| 0x01 | 0x06/0x07 | short and long term fuel trim, bank 1 | yes | yes | no | no | no | consistent public description; no competing formula |
| 0x01 | 0x0B | intake manifold absolute pressure | yes | yes | no | no | no | ELM327 datasheet capture (`0B 21`) |
| 0x01 | 0x0C | engine speed | yes | yes | yes | no | no | ELM327 datasheet capture (`0C 17 B8`) |
| 0x01 | 0x0D | vehicle speed | yes | yes | no | no | no | consistent public description |
| 0x01 | 0x0E | timing advance | yes | yes | no | no | no | consistent public description |
| 0x01 | 0x0F | intake air temperature | yes | yes | no | no | no | consistent public description |
| 0x01 | 0x10 | mass air flow rate | yes | yes | no | no | no | consistent public description |
| 0x01 | 0x11 | throttle position | yes | yes | no | no | no | consistent public description |
| 0x01 | 0x1C | OBD standards conformed to | yes | yes | no | no | no | consistent public description; the reported value is configuration |
| 0x01 | 0x1F | run time since engine start | yes | yes | no | no | no | consistent public description |
| 0x01 | 0x2F | fuel tank level input | yes | yes | yes | no | no | consistent public description; truncation unchanged since ce46b87 |
| 0x01 | 0x42 | control module voltage | yes | yes | no | no | no | consistent public description |
| 0x01 | 0x46 | ambient air temperature | yes | yes | no | no | no | consistent public description |
| 0x01 | 0x51 | fuel type | yes | yes | yes | no | no | consistent public description |
| 0x03 | - | stored DTCs | yes | yes | no | no | no | unchanged since ce46b87 |
| 0x04 | - | clear DTCs | **no** | yes (absence) | no | n/a | no | deferred to Phase 6: needs mutable DTC-store semantics (DEV-11) |
| 0x07 | - | pending DTCs | **no** | yes (absence) | no | n/a | no | deferred to Phase 6: needs a pending/confirmed distinction (DEV-11) |
| 0x09 | 0x00 | supported parameters | yes | yes | no | no | no | derived from the table |
| 0x09 | 0x02 | VIN | yes | yes | yes | no | no | three independent captures agree on the item count byte (DEV-02) |
| 0x09 | 0x0A | ECU name | yes | yes | no | no | no | **byte layout unresolved (DEV-03, deferred)**; current bytes frozen by test |

Interoperability evidence: the Mode 01 values were exercised over the kernel ISO-TP path
on a vcan interface, and the supported-parameter chain and the multi-frame VIN were
verified on the wire. No hardware adapter has been used yet; that is Phase 8.

Known-wrong or unresolved behavior in these rows is tracked as DEV-03, DEV-11, DEV-15 and
DEV-18. Reads are deterministic and side-effect free since Phase 5 (DEV-09, DEV-10), but
nothing varies over time until the Phase 7 scenario engine.

## UDS services

| Service | Sub-function | Description | Implemented | Unit tested | Integration tested | Hardware validated | Standards validated |
|---|---|---|---|---|---|---|---|
| 0x10 | 0x01-0x04 | DiagnosticSessionControl | yes | yes | yes | no | no |
| 0x11 | 0x01-0x05 | ECUReset | yes | yes | yes | no | no |
| 0x14 | - | ClearDiagnosticInformation | no | yes (DEV-23) | no | n/a | no |
| 0x19 | 0x02 | reportDTCByStatusMask | yes | yes | no | no | no |
| 0x19 | 0x01, 0x0A | other report types | no | yes | no | n/a | no |
| 0x22 | - | ReadDataByIdentifier | no | yes | yes | n/a | no |
| 0x3E | - | TesterPresent | no | yes (DEV-23) | no | n/a | no |
| - | - | NRC 0x11 for a service no enabled protocol serves | yes | yes | yes | no | no |
| - | - | `suppressPosRspMsgIndicationBit` honoured | no | yes (DEV-07) | no | n/a | no |

The session parameter record, DTC status bytes and session state behavior are known to be
placeholders: DEV-05, DEV-07, DEV-16, DEV-17 and DEV-23. No session state is kept.

The positive path of `0x19` sub-function `0x02` is pinned by unit tests only. The
integration suite exercises the three-byte form `19 02 <mask>`, which is rejected today
(DEV-05), not the two-byte form that returns DTCs.

The NRC 0x11 row is DEV-06, corrected in Phase 3. The value `0x11` and the
`7F <SID> <NRC>` framing are taken from the public description of ISO 14229-1. The
specification text has not been reviewed, so the row is not `standards validated`.

## Runtime

| Behavior | Implemented | Unit tested | Integration tested | Hardware validated | Standards validated |
|---|---|---|---|---|---|
| asyncio runtime, one loop, no threads | yes | yes | yes | n/a | n/a |
| SIGINT and SIGTERM shutdown with exit status 0 | yes | yes | yes | no | n/a |
| Startup failure reported with exit status 2 | yes | yes | yes | no | n/a |
| Route and endpoint consistency checked before sockets open | yes | yes | no | n/a | n/a |
| Per-ECU and per-protocol log context | yes | yes | no | n/a | n/a |
| YAML profile loaded and validated before any socket opens | yes | yes | yes | n/a | n/a |
| `validate-config` checks a profile without opening a socket | yes | yes | no | n/a | n/a |
| Malformed profile rejected with the path to every problem | yes | yes | no | n/a | n/a |
| Vehicle state addressed by dotted signal path | yes | yes | no | n/a | n/a |
| Diagnostic reads observe state without advancing it | yes | yes | yes | n/a | n/a |
| Supported parameters derived from the configured vehicle | yes | yes | yes | n/a | n/a |

The runtime rows describe project behavior, not protocol behavior, so `standards
validated` does not apply to them. Configuration validation in particular is project input
validation: it rejects profiles this simulator cannot serve faithfully and makes no claim
about SAE J2012 trouble-code format or any other specification. See
[decisions/0002-configuration-format-and-validation.md](decisions/0002-configuration-format-and-validation.md).
