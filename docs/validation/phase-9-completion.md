# Phase 9 completion report

- Phase: 9 — Multi-ECU (V1.1)
- Branch: `phase-9` (stacked on `phase-11`)
- Date: 2026-11-18
- Hardware required: no
- Status: implementation and local acceptance complete; final PR CI pending

## Delivered

- Packaged `profiles/multi_ecu.yaml` with engine and transmission ECUs, eight ISO-TP
  endpoints, shared 11-bit and normal-fixed 29-bit functional request identifiers, and
  distinct physical response identifiers.
- Endpoint-aware routing. Linux delivers the broadcast through each bound kernel socket;
  each request carries the receiving endpoint identity to exactly one route and still
  produces at most one response from the dispatcher.
- Profile-selectable `normal_11bit` and `normal_fixed_29bit` endpoint modes. Normal-fixed
  physical target/source reversal and functional physical-reply consistency are rejected
  at profile load instead of failing at socket bind.
- Endpoint-level startup consistency checks across endpoint name, CAN identifier,
  addressing mode, and physical/functional kind.
- `ecu-simulator list-ecus`, which validates and inventories a profile without opening a
  CAN socket.
- Real kernel `CAN_ISOTP` fan-out tests for 11-bit and normal-fixed 29-bit requests.

The design and public evidence are recorded in
[Decision 0012](../decisions/0012-phase-9-multi-ecu.md).

## End-to-end evidence

All commands below ran on the implementation commit on 2026-11-18.

| Check | Result |
|---|---|
| Full local pytest collection | **1,148 passed, 3 strict xfailed** in 56.54 s |
| Isolated private-namespace `vcan`/kernel `CAN_ISOTP` suite | **75 passed** in 46.05 s |
| 11-bit functional fan-out | One request on `0x7DF`; replies observed on `0x7E8` and `0x7E9` |
| Normal-fixed 29-bit functional fan-out | One request on `0x18DB33F1`; replies observed on `0x18DAF110` and `0x18DAF118` |
| `list-ecus` against packaged multi-ECU profile | Two ECUs and all eight endpoints printed; exit 0 |
| `validate-config` against packaged multi-ECU profile | Valid, two ECUs, eight endpoints; exit 0 |
| Ruff | Passed |
| mypy | Passed in 47 source files |
| Pull-request CI | Pending final branch run |

The three strict expected failures are unchanged and explicitly tied to DEV-26, DEV-11,
and DEV-03. Phase 9 neither hides nor broadens them.

## Definition of Done

| Requirement | Evidence | Result |
|---|---|---|
| `profiles/multi_ecu.yaml` | Packaged profile loads and builds two ECUs/eight endpoints | Met |
| Functional fan-out integration test | Independent tester receives both 11-bit replies through real kernel sockets | Met |
| `list-ecus` | Public CLI acceptance test and direct command | Met |
| 29-bit normal-fixed addressing | Schema, socket, app assembly, and real functional wire test | Met |
| Full regression suite | 1,148 passed; only the same three strict xfails | Met |
| Required PR CI | Final run pending | Pending |

## Boundary kept

Phase 9 does not claim physical CAN or adapter validation. Phase 8b remains open and is
the only held hardware gate. Per-tester UDS session ownership also remains outside this
phase; each configured ECU has its own Phase 11 session object, as recorded in Decision
0012.
