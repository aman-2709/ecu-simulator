# Phase 10 completion report

- Phase: 10 — Fault injection (V1.1)
- Branch: `phase-10` (stacked on `phase-9`)
- Date: 2026-11-18
- Hardware required: no
- Status: implementation and local acceptance complete; final PR CI pending

## Delivered

- Per-ECU `FaultPolicy` in the synchronous ECU request pipeline.
- Deterministic SID/addressing/occurrence matching with explicit rule order and no
  randomness.
- Drop, delay, NRC override, ResponsePending then final, truncate, corrupt, and ECU
  offline effects.
- A real distinction between drop (ordinary handler and state transition happen) and
  offline (handler is bypassed).
- Scheduled transport responses using asyncio timers, with timer cancellation during
  shutdown and no blocking sleep in ECU or protocol code.
- Optional transport observation hooks for intentional silence and a peer that ignores
  the flow control required by a multi-frame response.
- A packaged `profiles/faults_demo.yaml`; default and multi-ECU profiles remain fault-free.

The semantics, standard boundary, and primary evidence are recorded in
[Decision 0013](../decisions/0013-phase-10-fault-injection.md).

## End-to-end evidence

All commands below ran on the implementation commit on 2026-11-18.

| Check | Result |
|---|---|
| Full local pytest collection | **1,202 passed, 3 strict xfailed** in 60.69 s |
| Isolated private-namespace `vcan`/kernel `CAN_ISOTP` suite | **84 passed** in 50.95 s |
| Fault-specific real-wire cases | **9 passed**: all seven effects, ignored FC/recovery, pending-timer shutdown |
| Ruff | Passed |
| mypy | Passed in 49 source files |
| Pull-request CI | Pending final branch run |

The three strict expected failures are unchanged and explicitly tied to DEV-26, DEV-11,
and DEV-03. Phase 10 neither hides nor broadens them.

## Wire observations

| Effect/path | Observed result |
|---|---|
| Drop | First physical `01 0D` was silent; the second answered `41 0D 2A` |
| Delay | `3E 00` answered `7E 00` after the configured 150 ms delay |
| NRC override | Ordinary `14 FF FF FF` processing was replaced by `7F 14 22` |
| ResponsePending | `10 03` answered `7F 10 78`, then its ordinary `50 03 00 1E 0B B8` final response |
| Truncate | Mode 09 VIN payload was shortened by exactly five bytes |
| Corrupt | Mode 03's first response byte was XORed from `43` to `42` |
| ECU offline | `11 01` was silent and did not reset the active extended session; F186 still read `03` |
| Ignored flow control | Raw tester withheld FC after the VIN first frame; kernel `ECOMM` was reported, then F186 answered normally |
| Shutdown | SIGTERM with a delayed response pending exited 0, logged clean shutdown, and produced no traceback |

## Definition of Done

| Requirement | Evidence | Result |
|---|---|---|
| Every planned effect | Unit policy cases plus real-wire cases | Met |
| Off by default | Empty policy on default profile; entire legacy regression suite unchanged | Met |
| Deterministic triggers | Explicit occurrence tests, ordering test, no random/time trigger | Met |
| Transport hooks for silence/ignored FC | Unit callback tests and raw-CAN E2E timeout/recovery | Met |
| Non-blocking delay and pending/final | Timer-order tests and real-wire timing | Met |
| Clean lifecycle | Unit timer cancellation and real process SIGTERM test | Met |
| Required PR CI | Final run pending | Pending |

## Boundary kept

Fault effects are simulator test features, not standards-conformance claims. The current
ISO 14229-1 normative text was unavailable; only AUTOSAR's public secondary description
supports the `0x78` wire value and pending/final sequence. No physical CAN or adapter was
used, so Phase 8b remains the only held hardware gate.
