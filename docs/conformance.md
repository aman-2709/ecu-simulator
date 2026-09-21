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
through signal paths, and by the shared DTC store for services 03 and 04. Modes `0x01` to
`0x0A` are accepted as valid service identifiers; those not listed below produce no
response, Mode 07 among them (DEV-11, deferred).

`standards validated` is `no` for every row: SAE J1979 (`J1979_202505`) and its Digital
Annex (`J1979DA_202607`) are licensed and have not been read by this project. The evidence
column records what each encoding actually rests on.

| Service | PID | Description | Implemented | Unit tested | Integration tested | Hardware validated | Standards validated | Evidence |
|---|---|---|---|---|---|---|---|---|
| 0x01 | 0x00/0x20/0x40 | supported parameters | yes | yes | yes | no | no | derived from the table; continuation behavior from a two-ECU capture in the ELM327 datasheet |
| 0x01 | 0x01 | monitor status | **no** | yes (absence) | yes (absence) | n/a | no | **still deferred after Phase 6**: the store now exists, but the ELM327 datasheet describes only the first byte and refers the other three to J1979 |
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
| 0x01 | several | several parameters in one request | yes | yes | yes | no | no | two worked CAN captures in the ELM327 datasheet, reproduced byte for byte (DEV-18) |
| 0x03 | - | stored DTCs | yes | yes | yes | no | no | bytes unchanged since ce46b87; the list is now the confirmed view of the shared DTC store |
| 0x04 | - | clear DTCs | **yes** | yes | yes | no | no | `44` stated verbatim in the ELM327 datasheet; clears the store OBD and UDS share (DEV-11) |
| 0x07 | - | pending DTCs | **no** | yes (absence) | no | n/a | no | **deferred**: the `47` + count framing has no public worked example, only two open-source implementations (DEV-11) |
| 0x09 | 0x00 | supported parameters | yes | yes | no | no | no | derived from the table |
| 0x09 | 0x02 | VIN | yes | yes | yes | no | no | three independent captures agree on the item count byte (DEV-02) |
| 0x09 | 0x0A | ECU name | yes | yes | no | no | no | **byte layout unresolved (DEV-03, deferred)**; current bytes frozen by test |

Interoperability evidence: the Mode 01 values were exercised over the kernel ISO-TP path
on a vcan interface, and the supported-parameter chain, the multi-frame VIN and a
six-parameter request whose response spans several frames were verified on the wire. No hardware adapter has been used yet; that is Phase 8.

Known-wrong or unresolved behavior in these rows is tracked as DEV-03, DEV-11 and DEV-15.
DEV-18 was corrected in Phase 5.1; the three project choices its evidence did not settle
- at most six parameters, unsupported ones omitted, a repeat answered once - are recorded
in [decisions/0005-phase-5-1-multi-pid-evidence.md](decisions/0005-phase-5-1-multi-pid-evidence.md). Reads are deterministic and side-effect free since Phase 5 (DEV-09, DEV-10), but
nothing varies over time until the Phase 7 scenario engine.

## UDS services

| Service | Sub-function | Description | Implemented | Unit tested | Integration tested | Hardware validated | Standards validated |
|---|---|---|---|---|---|---|---|
| 0x10 | 0x01-0x04 | DiagnosticSessionControl | yes | yes | yes | no | no |
| 0x11 | 0x01-0x05 | ECUReset | yes | yes | yes | no | no |
| 0x14 | - | ClearDiagnosticInformation, groupOfDTC `FFFFFF` only | **yes** | yes | **yes** | no | no |
| 0x19 | 0x02 | reportDTCByStatusMask, mask required and applied | yes | yes | **yes** | no | no |
| 0x19 | 0x01, 0x0A | other report types | no | yes | no | n/a | no |
| 0x22 | - | ReadDataByIdentifier | no | yes | yes | n/a | no |
| 0x3E | 0x00 | TesterPresent, stateless | **yes** | yes | **yes** | no | no |
| 0x3E | other | subFunctionNotSupported, `7F 3E 12` | **yes** | yes | **yes** | no | no |
| - | - | NRC 0x11 for a service no enabled protocol serves | yes | yes | yes | no | no |
| - | - | `suppressPosRspMsgIndicationBit` honoured for every sub-function service | **yes** | yes | **yes** | no | no |

The session parameter record is still a placeholder: DEV-17. No session state is kept, and
0x3E keeps none either -- it starts, reads and resets no timer, and claims no ISO 14229
session compliance. S3, TesterPresent timing, the session state machine and Concurrent
TesterPresent are Phase 11.

Phase 7 closed DEV-23 and DEV-07. `3E 00` is answered `7E 00`; a sub-function this server
does not support is `7F 3E 12`; a request that is not two bytes is `7F 3E 13`. The
suppress bit is handled once, in the UDS service-dispatch layer, for every service an
explicit table declares to have a sub-function -- today 0x10, 0x11, 0x19 and 0x3E, with
0x14 deliberately absent. Bit 7 is read, masked off before the service sees the
sub-function, and the response withheld only if it turned out positive. A negative
response is never withheld: `11 85` is silent and `11 86` still answers `7F 11 12`.

Two behaviors Phase 6 established changed as a consequence, deliberately and in their own
commit, and are recorded as DEV-24: `19 82 FF` is now silence rather than `7F 19 12`, and
`19 82` is `7F 19 13` rather than `7F 19 12`. ReadDTCInformation has a sub-function, so it
takes part in the rule; the alternative was to record in that table something untrue about
the service.

Phase 6 corrected DEV-05 and the status half of DEV-16. `19 02 <mask>` is the request
shape and the mask is applied; the two-byte form is now a length error. The
DTCStatusAvailabilityMask is `0x8C`, the three status bits this project models -- bit 2
pendingDTC, bit 3 confirmedDTC, bit 7 warningIndicatorRequested -- and each record's
status is derived from the shared DTC store rather than fixed at `0x2F`. The third byte
of the DTC number stays frozen at `0x01`: it is the J2012 failure-type byte and nothing
supports any value for it. Sub-functions `0x01` and `0x0A` remain Phase 11.

Interoperability evidence: `19 02 <mask>`, a mask matching nothing, `14 FF FF FF`, an
unsupported group, and clearing in both directions between the OBD and UDS channels were
all exercised over the kernel ISO-TP path on a vcan interface. So were `3E 00`, a
suppressed `3E 80` producing nothing at all, `10 83` and `11 81` doing the same, a
suppressed read of the trouble codes, and `11 86` proving a refusal still goes out.

For 0x3E the request and response shapes are corroborated by the AUTOSAR Dcm specification
(`[SWS_Dcm_00251]`, which names 0x00 and 0x80 as its only sub-function values) and,
independently, by what udsoncan builds and what Scapy parses. The suppress-bit rule rests
on `[SWS_Dcm_00200]`, `[SWS_Dcm_00201]`, `[SWS_Dcm_00204]` and `[ECUC_Dcm_00737]`, all
re-verified against AUTOSAR CP R25-11. **That row has no interoperability evidence**:
both client libraries set the bit and expect nothing back, so neither has anything to
parse. ISO 14229-1:2026 clause 6.5 is the normative home of the rule and is licensed and
unread, so none of this is `standards validated`.

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
| DTC state shared by OBD and UDS, one clear operation | yes | yes | yes | n/a | n/a |
| DTC status byte and availability mask derived from the modelled state | yes | yes | yes | no | no |
| A clear keeps the configured trouble codes so they can be raised again | yes | yes | yes | n/a | n/a |
| Diagnostic reads observe state without advancing it | yes | yes | yes | n/a | n/a |
| Supported parameters derived from the configured vehicle | yes | yes | yes | n/a | n/a |
| Time taken from an injectable `Clock`; monotonic in production | **yes** | yes | yes | n/a | n/a |
| Six deterministic generators as pure functions of elapsed time | **yes** | yes | yes | n/a | n/a |
| Scenario applied before each request and by a periodic tick | **yes** | yes | yes | n/a | n/a |
| A timed trouble-code event is applied exactly once, ever | **yes** | yes | yes | n/a | n/a |
| Backward scenario time refused rather than reinterpreted | **yes** | yes | no | n/a | n/a |
| Scenario configuration validated before any socket opens | **yes** | yes | no | n/a | n/a |
| Clean shutdown with the periodic tick running | **yes** | yes | **yes** | no | n/a |

The runtime rows describe project behavior, not protocol behavior, so `standards
validated` does not apply to them, with one exception: the derived status byte and
availability mask are visible on the wire, so that row carries `no` like any other
protocol row. The post-clear state is deliberately narrower than either the OBD or the
UDS description of a clear, and is
[documented as a simulator transition](decisions/0004-phase-6-dtc-evidence.md) rather than
as SAE or ISO behavior. Configuration validation in particular is project input
validation: it rejects profiles this simulator cannot serve faithfully and makes no claim
about SAE J2012 trouble-code format or any other specification. See
[decisions/0002-configuration-format-and-validation.md](decisions/0002-configuration-format-and-validation.md).

The Phase 7 runtime rows describe simulation, not protocol. A scenario changes what the
vehicle *is* -- physical values and trouble-code state, through `VehicleState.set`,
`DtcStore.update` and `DtcStore.clear` and nothing else -- and the encoders go on encoding
whatever they find, unchanged. Nothing in a scenario can drop a response, delay one, force
a negative one or reach a protocol; that is fault injection, which is Phase 10. There is
no randomness anywhere, seeded or otherwise.

**The shipped `ice_default.yaml` has no scenario**, so the default configuration answers
exactly what it answered before Phase 7, and a profile without a scenario builds no runner,
starts no tick and never reads the clock at all. The feature is demonstrated by
`profiles/ice_scenario.yaml`. See
[decisions/0006-phase-7-scenario-and-testerpresent.md](decisions/0006-phase-7-scenario-and-testerpresent.md).
