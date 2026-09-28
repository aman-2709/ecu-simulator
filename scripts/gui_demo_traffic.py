#!/usr/bin/env python3
"""Read-only diagnostic traffic at a human pace, for watching the observer page live.

It cycles through OBD service 01 PIDs, OBD 03/07/09 and UDS 0x22/0x19 reads, a few per
second, until interrupted (Ctrl-C or SIGTERM). The addresses are the ones the bundled ICE
profiles route (ice_default.yaml, ice_scenario.yaml):

- OBD requests go out functionally on 0x7DF; the engine ECU answers on 0x7E8;
- UDS reads go out physically, on 0x7E0 (answered on 0x7E8) and 0x7E1 (answered on 0x7E9).

It only reads. It never sends a write, a clear or a session change: no OBD 04, no UDS
0x14, 0x2E, 0x31 or 0x10. A request that gets no answer within the timeout is printed as
such and the cycle goes on.

Two requests in the cycle are there on purpose although today's simulator does not serve
them: UDS 0x22 is answered with NRC 0x11 (service not supported), and OBD mode 07 gets no
answer. They show the page's negative and unanswered outcomes with real traffic.

Run it on the same interface as the simulator:

    .venv/bin/python scripts/gui_demo_traffic.py --interface vcan0 --rate 3
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from types import FrameType

import isotp

CAN_ISOTP_SF_BROADCAST = 0x0800
FUNCTIONAL = "functional 7DF->7E8"
PHYSICAL_7E0 = "physical 7E0->7E8"
PHYSICAL_7E1 = "physical 7E1->7E9"

# One pass of the cycle: (route, request). Every request here is a read.
CYCLE: tuple[tuple[str, bytes], ...] = (
    (FUNCTIONAL, bytes.fromhex("0100")),     # supported PIDs 01-20
    (FUNCTIONAL, bytes.fromhex("010C")),     # engine speed
    (FUNCTIONAL, bytes.fromhex("010D")),     # vehicle speed
    (FUNCTIONAL, bytes.fromhex("0105")),     # coolant temperature
    (FUNCTIONAL, bytes.fromhex("0104")),     # engine load
    (PHYSICAL_7E0, bytes.fromhex("22F190")),  # UDS read VIN: 0x22 is not served yet, so NRC 0x11
    (FUNCTIONAL, bytes.fromhex("0111")),     # throttle position
    (FUNCTIONAL, bytes.fromhex("012F")),     # fuel level
    (FUNCTIONAL, bytes.fromhex("03")),       # OBD stored DTCs
    (FUNCTIONAL, bytes.fromhex("010F")),     # intake air temperature
    (PHYSICAL_7E0, bytes.fromhex("1902FF")),  # UDS DTCs by status mask
    (FUNCTIONAL, bytes.fromhex("010C")),
    (FUNCTIONAL, bytes.fromhex("010D")),
    (FUNCTIONAL, bytes.fromhex("07")),       # OBD pending DTCs: mode 07 is not served yet, so no answer
    (FUNCTIONAL, bytes.fromhex("0902")),     # OBD VIN (multi-frame answer)
    (PHYSICAL_7E1, bytes.fromhex("1902FF")),  # UDS DTCs by status mask, on the second address
    (FUNCTIONAL, bytes.fromhex("0120")),     # supported PIDs 21-40
    (FUNCTIONAL, bytes.fromhex("0110")),     # mass air flow
)

FORBIDDEN_SERVICES = frozenset({0x04, 0x10, 0x14, 0x2E, 0x31})
assert not any(request[0] in FORBIDDEN_SERVICES for _, request in CYCLE), "the cycle must only read"


def physical_socket(interface: str, txid: int, rxid: int, timeout: float) -> isotp.socket:
    sock = isotp.socket(timeout=timeout)
    sock.set_opts(optflag=isotp.socket.flags.TX_PADDING, txpad=0)
    sock.bind(interface, isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=rxid, txid=txid))
    return sock


class Tester:
    """The shape of scripts/gui_m2_early_check.py Tester, with a physical path per ECU address."""

    def __init__(self, interface: str, timeout: float) -> None:
        self.functional = isotp.socket()
        self.functional.set_opts(optflag=isotp.socket.flags.TX_PADDING | CAN_ISOTP_SF_BROADCAST, txpad=0)
        self.functional.bind(interface, isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=0, txid=0x7DF))
        # The 0x7E0 socket both sends physical requests and receives every 0x7E8 answer,
        # including those to functional requests (its txid carries their flow control).
        self.e0 = physical_socket(interface, txid=0x7E0, rxid=0x7E8, timeout=timeout)
        self.e1 = physical_socket(interface, txid=0x7E1, rxid=0x7E9, timeout=timeout)

    def exchange(self, route: str, request: bytes) -> bytes | None:
        if route == FUNCTIONAL:
            self.functional.send(request)
            rx = self.e0
        elif route == PHYSICAL_7E0:
            self.e0.send(request)
            rx = self.e0
        else:
            self.e1.send(request)
            rx = self.e1
        try:
            return bytes(rx.recv())
        except TimeoutError:
            return None

    def close(self) -> None:
        for sock in (self.functional, self.e0, self.e1):
            sock.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--interface", default="vcan0", help="SocketCAN interface (default: vcan0)")
    parser.add_argument("--rate", type=float, default=3.0, help="requests per second (default: 3)")
    parser.add_argument("--timeout", type=float, default=0.5, help="seconds to wait for each answer (default: 0.5)")
    args = parser.parse_args(argv)
    if not 0 < args.rate <= 50:
        parser.error("--rate must be above 0 and at most 50 requests per second")

    def stop(signum: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    tester = Tester(args.interface, args.timeout)
    interval = 1.0 / args.rate
    sent = unanswered = 0
    print(f"read-only traffic on {args.interface} at {args.rate:g}/s; Ctrl-C to stop", flush=True)
    try:
        next_at = time.monotonic()
        while True:
            route, request = CYCLE[sent % len(CYCLE)]
            response = tester.exchange(route, request)
            sent += 1
            if response is None:
                unanswered += 1
            answer = response.hex(" ").upper() if response is not None else "no answer"
            print(f"{sent:6d} {route:18s} {request.hex(' ').upper():10s} -> {answer}", flush=True)
            next_at += interval
            delay = next_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_at = time.monotonic()
    except KeyboardInterrupt:
        pass
    finally:
        tester.close()
    print(f"stopped: {sent} requests, {unanswered} unanswered", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
