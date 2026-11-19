# Decision 0012: Phase 9 multi-ECU routing and normal-fixed addressing

- Status: accepted
- Date: 2026-11-18
- Scope: Phase 9 only

## Decision

Phase 9 will use one kernel ISO-TP socket per configured endpoint. A functional CAN
request is therefore delivered once through each ECU endpoint that is bound to the
shared receive identifier. The transport puts that endpoint's stable name on the
`DiagnosticRequest`; the router uses it to select exactly one route and the dispatcher
still returns at most one response. There is no list-of-responses or fan-out loop in the
ECU pipeline.

Routes and receiving endpoints are checked by endpoint name, addressing kind, CAN
identifier, and addressing mode before sockets are opened. A request constructed without
an endpoint name remains valid when its address resolves to zero or one route. It is
rejected as ambiguous when several routes share that functional address.

Profiles select either `normal_11bit` (the default) or `normal_fixed_29bit` per endpoint.
The latter accepts only the ISO-TP normal-fixed identifier layout:

- physical request: `0x18DA<ecu><tester>`;
- physical response: `0x18DA<tester><ecu>`;
- functional request: `0x18DB<group><tester>`;
- functional response: the response identifier of that ECU's named physical endpoint.

A normal-fixed functional endpoint must therefore name a normal-fixed physical
`reply_via` endpoint. The functional request's tester/source byte must match that
physical endpoint, and both endpoints must have the same response identifier. These are
project input checks over the identifier format; they are not a claim that this project
has independently validated the ISO text.

`list-ecus` is a read-only CLI command. It loads and validates the selected profile, then
writes a stable human-readable inventory to stdout: profile ECU key and configured name,
followed by every endpoint's addressing kind, mode, receive/transmit identifiers, and
eligible protocols. It never opens a socket.

The shipped `multi_ecu.yaml` profile contains engine and transmission ECUs. Each has a
distinct 11-bit physical endpoint, a shared 11-bit functional request identifier, a
canonical normal-fixed physical endpoint, and a shared normal-fixed functional request
identifier. Both functional paths are exercised through real `vcan` and kernel
`CAN_ISOTP`, with the two replies observed on distinct response identifiers.

## Why this seam

The kernel already owns ISO-TP reassembly, flow control, and delivery. Preserving one
request/one response at the transport callback keeps the protocol and ECU interfaces
small. Endpoint identity is transport metadata, not an ECU or protocol object, and is
stripped with the opaque transport context before the ECU handles the request.

Batching several ECU responses in the dispatcher would duplicate fan-out the kernel has
already performed, make response timing artificial, and require the transport callback
to return routing instructions. A dispatcher loop would also be wrong: it would process
one socket delivery as though it came from all sockets and could send replies through the
wrong ISO-TP state machine.

Session ownership does not change in Phase 9. Each configured ECU owns its own Phase 11
UDS session object. Per-tester session ownership would require a stable tester identity
and remains outside this phase; normal 11-bit addressing does not convey one.

## Primary evidence

- Linux kernel ISO-TP documentation describes functional addressing as 1-to-N and lists
  normal, extended, and mixed addressing formats:
  <https://docs.kernel.org/networking/iso15765-2.html>.
- Linux SocketCAN documentation defines `CAN_EFF_FLAG` and the 29-bit identifier mask:
  <https://docs.kernel.org/networking/can.html>.
- Linux's public ISO-TP UAPI defines `CAN_ISOTP_SF_BROADCAST`, used by the independent
  tester in the integration test:
  <https://github.com/torvalds/linux/blob/master/include/uapi/linux/can/isotp.h>.
- The maintained `python-can-isotp` addressing documentation records the normal-fixed
  physical `0x18DA<TA><SA>` and functional `0x18DB<TA><SA>` layouts:
  <https://github.com/pylessard/python-can-isotp/blob/v2.x/doc/source/isotp/addressing.rst>.
- ISO's catalogue identifies ISO 15765-2:2024 as the current published transport/network
  layer edition and ISO 15765-4:2021 as the current published emissions-related edition:
  <https://www.iso.org/standard/84211.html> and
  <https://www.iso.org/standard/78384.html>. The normative text is licensed and was not
  used as an implementation source here.

## Acceptance

Phase 9 closes only when all of the following pass on the same final commit:

1. the full local unit, characterization, and ordinary integration collection;
2. the isolated real-`vcan` integration suite;
3. a functional 11-bit request producing replies from both ECUs on distinct identifiers;
4. a functional normal-fixed 29-bit request producing replies from both ECUs on distinct
   identifiers;
5. `list-ecus` against the packaged multi-ECU profile without opening a socket;
6. Ruff, mypy, and every required PR CI job.

Physical CAN hardware is not required for this phase. Phase 8b remains the only held
hardware gate.
