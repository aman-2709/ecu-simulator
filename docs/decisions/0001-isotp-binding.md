# 0001: How Python drives the kernel ISO-TP socket

Status: accepted, 2026-09-18. Phase 2A spike. Evidence generator:
`scripts/isotp_kernel_experiments.py`.

## Problem statement

The simulator needs ISO 15765-2 (ISO-TP) framing between diagnostic payloads and
CAN frames. The Linux kernel `CAN_ISOTP` socket is the required implementation; the
project must not implement ISO-TP itself. The open question was how Python should drive
that socket: through the standard library with a small in-repository option wrapper, or
through the maintained `can-isotp` package's `isotp.socket` class that the legacy code
already used (at its 1.x API).

A second question had to be settled on real kernels: can several ISO-TP sockets share the
functional RX CAN ID (0x7DF) with distinct physical TX IDs? The answer decides whether
multi-ECU functional addressing needs a custom raw-CAN single-frame parser.

## Requirements

- Kernel `CAN_ISOTP` only; no user-space ISO-TP.
- TX padding to 8-byte frames, configurable pad byte.
- Flow-control parameters (block size, STmin).
- 11-bit normal addressing now; 29-bit normal fixed addressing later.
- CAN FD link-layer options (MTU 72, `tx_dl` up to 64) reachable later without changing
  the binding.
- A file descriptor and non-blocking mode usable with `asyncio.add_reader`.
- Clear error behavior for a missing interface and for flow-control timeouts.
- Minimal project-owned code; a binding we can replace behind one interface.

## Candidate A: stdlib socket plus minimal wrapper

`socket.socket(AF_CAN, SOCK_DGRAM, CAN_ISOTP)`, `bind((interface, rx_id, tx_id))`, and
`setsockopt` calls with three struct layouts copied from `linux/can/isotp.h`:
`can_isotp_options` (`=IIBBBB`), `can_isotp_fc_options` (`=BBB`),
`can_isotp_ll_options` (`=BBB`), plus the option numbers and 14 flag constants that the
stdlib does not expose (`socket` provides `CAN_ISOTP` and `SOL_CAN_BASE` only).

Project-owned code measured in the spike: 60 non-blank, non-comment lines, of which
about 25 are constants and three `struct.Struct` layouts.

## Candidate B: can-isotp 2.x `isotp.socket`

`can-isotp` 2.0.7, module `isotp`. `isotp.socket()` wraps the same kernel socket:
`set_opts`, `set_fc_opts`, `set_ll_opts`, `bind(interface, isotp.Address(...))`,
`send`, `recv`, `fileno`, `settimeout`, `gettimeout`, `close`. Its own packing is
`=LLBBBB` / `=BBB` / `=BBB`, identical in size and order to Candidate A's.

Project-owned code: none beyond the call sites.

## Experiments

All experiments ran on the development host inside an unprivileged user and network
namespace so that no host configuration changed:

```
unshare -r -n bash -c '
  ip link add dev vcan0 type vcan && ip link set vcan0 mtu 72 && ip link set up vcan0 &&
  exec python scripts/isotp_kernel_experiments.py --interface vcan0'
```

which is what `scripts/isotp_kernel_experiments.py --private-netns --summary` does.
The namespace and its `vcan0` disappear when the process exits; `ip link show vcan0` on
the host reported "does not exist" before and after every run.

Environment: Ubuntu 22.04.5, kernel `6.8.0-138-generic` (Ubuntu HWE), Python 3.12.12,
can-isotp 2.0.7, can-utils installed, `/usr/include/linux/can/isotp.h` present.

| Id | Experiment |
|---|---|
| E1 | Option coverage; A's constants checked against B and against the kernel uapi header |
| E2/E3 | Single-frame and 20-byte multi-frame round trips, each candidate as ECU with the other as tester, raw-CAN sniffer recording every frame |
| E4 | `fileno()`, non-blocking `recv()` with nothing pending, `asyncio.add_reader` wake-up |
| E5 | Bind to a missing interface, duplicate (rx, tx) bind, rebind after close, recv after close, multi-frame send with no flow control |
| E6 | 29-bit normal fixed IDs 0x18DA10F1 / 0x18DAF110 |
| E7 | CAN FD link-layer options with MTU 72 and `tx_dl` 64, 60-byte payload |
| E8 | Shared functional RX: sockets (0x7DF→0x7E8) and (0x7DF→0x7E9), 20 rounds, tester on a tx-only `SF_BROADCAST` socket |
| E9 | can-utils `isotpsend` / `isotprecv` as an independent tester against Candidate A |

## Results

Summary line of the final run: `positive=43/45`, the two negatives both concern
Candidate B's API surface, not behavior on the wire.

| Criterion | Candidate A | Candidate B |
|---|---|---|
| Project-owned code | 60 lines incl. 3 struct layouts and 14 flags | 0 |
| Custom Linux ABI packing | yes, verified equal to the kernel header | none owned; identical layouts inside the package |
| Readability | explicit but low level | named methods and enums |
| Socket-option coverage | complete (all 5 options, all 14 flags) | all 5 options; flags `SF_BROADCAST`, `CF_BROADCAST`, `DYN_FC_PARMS` missing from `isotp.socket.flags`, but `set_opts(optflag=0x0800)` accepts the raw value |
| TX padding | `set_opts(flags=TX_PADDING, txpad=0)`; every frame on the wire DLC 8 | `set_opts(optflag=flags.TX_PADDING, txpad=0)`; identical wire result |
| Flow control | `set_fc_opts(bs, stmin, wftmax)` | same |
| 11-bit addressing | round trips pass | round trips pass |
| 29-bit addressing | `rx_id \| CAN_EFF_FLAG`; round trip passes | `isotp.Address(AddressingMode.Normal_29bits, ...)`; round trip passes, frames identical |
| CAN FD link-layer options | `set_ll_opts(mtu=72, tx_dl=64)`; 60-byte payload in one DLC-64 frame | `set_ll_opts(mtu=LinkLayerProtocol.CAN_FD, tx_dl=64)`; identical |
| Non-blocking | `setblocking(False)`; `recv` raises `BlockingIOError` (EAGAIN) | constructor ignores `timeout=0.0` (applies only when > 0); `settimeout(0.0)` works; `recv` raises `BlockingIOError` |
| File descriptor | `fileno()` | `fileno()`, equal to the underlying socket's |
| `asyncio.add_reader` | wake-up in 0.06 ms, payload received | wake-up in 0.04 ms, payload received |
| Send/receive | kernel ISO-TP; identical frames | identical frames |
| Missing interface | `OSError` ENODEV | `OSError` ENODEV |
| No flow control on multi-frame send | `OSError` ECOMM surfaced on the next `recv`, after 1000 ms | identical |
| Close/cleanup | `recv` after close raises EBADF; rebinding the same IDs after close works | same underlying socket |
| Dependency and upgrade risk | none; the uapi is stable | one pure-Python package; the 1.x to 2.x break was in `bind()`'s argument, now `bind(interface, Address)` |
| Testability | equal: both are wrapped by one project interface | equal |

Cross-checks: each candidate as ECU passed against the other as tester, and Candidate A
passed against can-utils (`isotprecv` printed the 20-byte VIN). The wire captures for A
and B are byte-identical in every experiment, as expected since the kernel does the work.

Kernel behavior discovered along the way: a second socket may bind an identical (rx, tx)
pair; the kernel raises no `EADDRINUSE`. Mainline `net/can/isotp.c` `isotp_bind()` has no
cross-socket conflict check. Duplicate ECU addresses must therefore be rejected by the
simulator's configuration validation, not left to the kernel.

## Shared functional RX experiment

Procedure (E8), on kernel 6.8.0-138-generic, Python 3.12.12, Candidate A sockets:

1. Bind ECU socket 1 rx 0x7DF / tx 0x7E8 and ECU socket 2 rx 0x7DF / tx 0x7E9, both with
   TX padding.
2. Bind a tester transmit-only socket with `CAN_ISOTP_SF_BROADCAST`, rx 0, tx 0x7DF, and
   two tester listeners rx 0x7E8 / tx 0x7E0 and rx 0x7E9 / tx 0x7E1.
3. For 20 rounds: send `01 00` on the functional socket; `recv` on both ECU sockets; send
   a distinct positive response from each ECU socket; `recv` on both listeners.
4. Record every frame with a raw-CAN sniffer.

Result: both binds succeeded; the functional request was received by both ECU sockets in
20 of 20 rounds; both physical responses arrived on 0x7E8 and 0x7E9 in 20 of 20 rounds;
wire IDs seen were exactly 0x7DF, 0x7E8, 0x7E9. A third socket (0x7DF→0x7E8) and a
Candidate B socket (0x7DF→0x7EA) could be added alongside. Repeated three further times
with identical results. The kernel source explains why: `isotp_bind()` registers rx
reception per socket and performs no conflict check between sockets.

Conclusion: kernel ISO-TP sockets support shared functional RX with distinct TX IDs. No
custom `CAN_RAW` single-frame parser will be written; multi-ECU functional addressing
uses one functional ISO-TP socket per OBD-capable ECU, generalizing the original
two-socket design.

## GitHub-hosted runner findings

Runner: `ubuntu-latest`, Ubuntu 24.04.5, kernel `6.17.0-1022-azure`, kernel package
`linux-image-6.17.0-1022-azure`, Python 3.12.14.

- Before any action: `vcan` and `can_isotp` absent, `vcan0` cannot be created,
  `CAN_ISOTP` socket creation fails with `EPROTONOSUPPORT`.
- `linux-modules-extra-6.17.0-1022-azure` exists in `noble-updates` and installs
  (version 6.17.0-1022.22).
- After installing it: `modprobe vcan` succeeds, `vcan0` is created and brought up,
  `CAN_RAW` bind succeeds, can-utils are installable.
- `modprobe can_isotp` still fails: the module is not present in any package for the
  Azure kernel, and `CAN_ISOTP` socket creation still returns `EPROTONOSUPPORT`. The CI
  probe records the on-disk CAN module list and the kernel's `CONFIG_CAN_ISOTP` value
  to make this explicit.

Consequences for CI: the informational probe job keeps the modules-extra install because
it makes `vcan` and raw CAN usable on the runner. ISO-TP integration tests cannot run on
GitHub-hosted runners with this kernel; they run locally, and in CI they skip with the
recorded reason until a runner kernel ships `can_isotp` or a self-hosted runner exists.
Kernel-dependent tests are not made required.

## Decision

Use Candidate B, `can-isotp` 2.x `isotp.socket`, to drive the kernel `CAN_ISOTP`
socket, behind one project-owned `IsoTpSocket` interface in `transport/socketcan/`.

Rationale, from the evidence:

- Both candidates produced byte-identical wire behavior and identical error behavior;
  there is no technical advantage on the wire for owning the ABI code.
- Candidate B exposes `fileno()` and public `settimeout()`/`gettimeout()`, and
  `asyncio.add_reader` woke up correctly. The disqualifying condition did not occur.
- Candidate A's only concrete advantages are the three missing flag constants and a
  cleaner non-blocking switch. Both are covered by two lines at the call site: pass the
  integer flag value to `set_opts(optflag=...)` and call `settimeout(0.0)` after
  construction instead of relying on the constructor.
- Removing the dependency would cost 60 owned lines of ABI code and a maintenance duty
  the package already carries.

Rules for the call sites:

- Pin `can-isotp>=2.0,<3`.
- Never rely on the constructor's `timeout` argument for non-blocking mode; call
  `settimeout(0.0)` explicitly.
- Define the three missing flag values (`SF_BROADCAST` 0x0800, `CF_BROADCAST` 0x1000,
  `DYN_FC_PARMS` 0x2000) once in the transport module, with a comment pointing at
  `linux/can/isotp.h`, if they are ever needed.
- Keep everything that touches `isotp` inside `transport/socketcan/isotp.py` so that
  Candidate A can replace it without touching protocols, ECUs, or tests.

## Rejected alternative

Candidate A, stdlib socket with an in-repository wrapper. Rejected because it provides no
observed technical advantage beyond two trivial API wrinkles in Candidate B, while adding
project-owned ABI packing code. The spike wrapper is kept only inside
`scripts/isotp_kernel_experiments.py` as reference; it is not production code.

## Consequences

- Runtime dependency on `can-isotp` remains; `python-can` stays a dev dependency only.
- Multi-ECU functional addressing is a kernel-socket concern; no raw-CAN parsing in the
  project.
- Configuration validation must reject duplicate (request, response) ID pairs because the
  kernel will not.
- ISO-TP integration tests run on local Linux with in-tree `can_isotp`, including without
  root via `unshare -r -n`; CI skips them on the GitHub-hosted Azure kernel with the
  probe result as the reason.
- Flow-control timeouts surface as `OSError(ECOMM)` on the next socket call, about one
  second after the failed send; the transport layer must map this to a transport error and
  keep serving.

## Future CAN FD considerations

`set_ll_opts(mtu=72, tx_dl=64)` worked on both candidates, and a 60-byte payload went out
as one DLC-64 frame on a `vcan` with MTU 72. The transport configuration only needs `fd`
and `tx_dl` fields validated against the interface MTU; the binding does not change. Real
hardware validation and the `tx_flags` bit-rate-switch flag remain for the CAN FD phase.
