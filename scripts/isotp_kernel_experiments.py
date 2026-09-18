#!/usr/bin/env python3
"""Spike: how should Python drive the Linux kernel CAN_ISOTP socket?

Evidence generator for docs/decisions/0001-isotp-binding.md. It runs the same
experiments against two candidates and prints a report:

  Candidate A  stdlib ``socket(AF_CAN, SOCK_DGRAM, CAN_ISOTP)`` plus the minimal
               option wrapper ``StdlibIsoTp`` defined below (constants and struct
               layouts copied from ``linux/can/isotp.h``).
  Candidate B  ``isotp.socket`` from the can-isotp 2.x package (kernel binding).

Experiments (all on a vcan interface):

  E1  option coverage and constant cross-check (A's constants vs B's)
  E2  single-frame round trip, TX padding observed on the wire (raw CAN sniffer)
  E3  multi-frame round trip with kernel flow control, frames observed
  E4  file descriptor, non-blocking recv, asyncio.add_reader wake-up
  E5  errors: missing interface, duplicate bind, no flow control (ECOMM), close
  E6  29-bit normal fixed addressing round trip
  E7  CAN FD link-layer options (needs interface MTU 72)
  E8  shared functional RX id: (0x7DF->0x7E8) and (0x7DF->0x7E9) bound at once
  E9  can-utils cross-check: isotpsend as the tester against Candidate A

Usage:
  scripts/isotp_kernel_experiments.py --private-netns      # no root: unshare -r -n
  scripts/isotp_kernel_experiments.py --interface vcan0    # existing interface

``--private-netns`` re-executes itself inside an unprivileged user+network
namespace, creates vcan0 there (MTU 72 so E7 can run) and leaves nothing behind
on the host. It needs the vcan kernel module to be auto-loadable.

This is spike code: it is not part of the simulator and may be deleted once the
decision record is final.
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import json
import os
import platform
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

# --------------------------------------------------------------------------------------
# Candidate A: stdlib socket + minimal wrapper. Constants from linux/can/isotp.h.
# --------------------------------------------------------------------------------------

SOL_CAN_ISOTP = socket.SOL_CAN_BASE + socket.CAN_ISOTP  # 100 + 6
CAN_ISOTP_OPTS = 1
CAN_ISOTP_RECV_FC = 2
CAN_ISOTP_TX_STMIN = 3
CAN_ISOTP_RX_STMIN = 4
CAN_ISOTP_LL_OPTS = 5

CAN_ISOTP_LISTEN_MODE = 0x0001
CAN_ISOTP_EXTEND_ADDR = 0x0002
CAN_ISOTP_TX_PADDING = 0x0004
CAN_ISOTP_RX_PADDING = 0x0008
CAN_ISOTP_CHK_PAD_LEN = 0x0010
CAN_ISOTP_CHK_PAD_DATA = 0x0020
CAN_ISOTP_HALF_DUPLEX = 0x0040
CAN_ISOTP_FORCE_TXSTMIN = 0x0080
CAN_ISOTP_FORCE_RXSTMIN = 0x0100
CAN_ISOTP_RX_EXT_ADDR = 0x0200
CAN_ISOTP_WAIT_TX_DONE = 0x0400
CAN_ISOTP_SF_BROADCAST = 0x0800
CAN_ISOTP_CF_BROADCAST = 0x1000
CAN_ISOTP_DYN_FC_PARMS = 0x2000

CAN_EFF_FLAG = 0x80000000
CAN_MTU = 16
CANFD_MTU = 72

# struct can_isotp_options { u32 flags; u32 frame_txtime; u8 ext_address; u8 txpad_content;
#                            u8 rxpad_content; u8 rx_ext_address; }
_OPTS = struct.Struct("=IIBBBB")
# struct can_isotp_fc_options { u8 bs; u8 stmin; u8 wftmax; }
_FC = struct.Struct("=BBB")
# struct can_isotp_ll_options { u8 mtu; u8 tx_dl; u8 tx_flags; }
_LL = struct.Struct("=BBB")


class StdlibIsoTp:
    """The whole of Candidate A's project-owned code."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_CAN, socket.SOCK_DGRAM, socket.CAN_ISOTP)

    def set_opts(
        self,
        flags: int = 0,
        frame_txtime: int = 0,
        txpad: int = 0,
        rxpad: int = 0,
        ext_address: int = 0,
        rx_ext_address: int = 0,
    ) -> None:
        self.sock.setsockopt(
            SOL_CAN_ISOTP, CAN_ISOTP_OPTS, _OPTS.pack(flags, frame_txtime, ext_address, txpad, rxpad, rx_ext_address)
        )

    def set_fc_opts(self, bs: int = 0, stmin: int = 0, wftmax: int = 0) -> None:
        self.sock.setsockopt(SOL_CAN_ISOTP, CAN_ISOTP_RECV_FC, _FC.pack(bs, stmin, wftmax))

    def set_ll_opts(self, mtu: int = CAN_MTU, tx_dl: int = 8, tx_flags: int = 0) -> None:
        self.sock.setsockopt(SOL_CAN_ISOTP, CAN_ISOTP_LL_OPTS, _LL.pack(mtu, tx_dl, tx_flags))

    def bind(self, interface: str, rx_id: int, tx_id: int) -> None:
        self.sock.bind((interface, rx_id, tx_id))

    def send(self, data: bytes) -> int:
        return self.sock.send(data)

    def recv(self, bufsize: int = 4095) -> bytes:
        return self.sock.recv(bufsize)

    def fileno(self) -> int:
        return self.sock.fileno()

    def setblocking(self, flag: bool) -> None:
        self.sock.setblocking(flag)

    def settimeout(self, timeout: float | None) -> None:
        self.sock.settimeout(timeout)

    def close(self) -> None:
        self.sock.close()


A_WRAPPER_LINES = 0  # filled at import time below


# --------------------------------------------------------------------------------------
# Candidate B: can-isotp 2.x isotp.socket
# --------------------------------------------------------------------------------------
try:
    import isotp  # type: ignore[import-not-found]

    ISOTP_VERSION = getattr(isotp, "__version__", "unknown")
except ImportError:  # pragma: no cover - the spike needs the package for candidate B
    isotp = None
    ISOTP_VERSION = "not installed"


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    kernel: str = platform.release()
    python: str = platform.python_version()
    can_isotp: str = ISOTP_VERSION
    interface: str = ""
    results: list[Result] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append(Result(name, ok, detail))
        print(f"  [{'ok ' if ok else 'NO '}] {name}: {detail}", flush=True)


class RawSniffer:
    """Records raw CAN frames so padding and multi-frame behaviour can be observed."""

    def __init__(self, interface: str, fd: bool = False) -> None:
        self.sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        if fd:
            self.sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FD_FRAMES, 1)
        self.sock.bind((interface,))
        self.sock.settimeout(0.05)
        self.frames: list[tuple[int, int, bytes]] = []
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop:
            try:
                raw = self.sock.recv(CANFD_MTU)
            except TimeoutError:
                continue
            except OSError:
                break
            can_id, dlc = struct.unpack_from("<IB", raw)
            data = raw[8 : 8 + dlc]
            self.frames.append((can_id & 0x1FFFFFFF, dlc, data))

    def stop(self) -> list[tuple[int, int, bytes]]:
        self._stop = True
        self._thread.join()
        self.sock.close()
        return self.frames


def fmt_frames(frames: list[tuple[int, int, bytes]]) -> str:
    return " ; ".join(f"{cid:03X}#{data.hex()}(dlc{dlc})" for cid, dlc, data in frames)


def make_a(interface: str, rx: int, tx: int, padding: bool = True, **ll) -> StdlibIsoTp:
    s = StdlibIsoTp()
    if padding:
        s.set_opts(flags=CAN_ISOTP_TX_PADDING, txpad=0x00)
    s.set_fc_opts(bs=0, stmin=0)
    if ll:
        s.set_ll_opts(**ll)
    s.bind(interface, rx, tx)
    return s


def make_b(interface: str, rx: int, tx: int, padding: bool = True, mode=None, timeout=None, **ll):
    s = isotp.socket(timeout=timeout)
    if padding:
        s.set_opts(optflag=isotp.socket.flags.TX_PADDING, txpad=0x00)
    s.set_fc_opts(bs=0, stmin=0)
    if ll:
        s.set_ll_opts(**ll)
    mode = mode or isotp.AddressingMode.Normal_11bits
    s.bind(interface, isotp.Address(mode, rxid=rx, txid=tx))
    return s


def wrapper_line_count() -> int:
    src = open(__file__, encoding="utf-8").read()
    start = src.index("SOL_CAN_ISOTP = ")
    end = src.index("A_WRAPPER_LINES = 0")
    return sum(1 for line in src[start:end].splitlines() if line.strip() and not line.strip().startswith("#"))


# --------------------------------------------------------------------------------------
# Experiments
# --------------------------------------------------------------------------------------


def e1_options(rep: Report) -> None:
    print("E1 option coverage and constant cross-check")
    rep.add("A owned code (non-blank, non-comment lines)", True, str(wrapper_line_count()))
    if isotp is None:
        rep.add("B available", False, "can-isotp not installed")
        return
    f = isotp.socket.flags
    a_flags = {
        "LISTEN_MODE": CAN_ISOTP_LISTEN_MODE,
        "EXTEND_ADDR": CAN_ISOTP_EXTEND_ADDR,
        "TX_PADDING": CAN_ISOTP_TX_PADDING,
        "RX_PADDING": CAN_ISOTP_RX_PADDING,
        "CHK_PAD_LEN": CAN_ISOTP_CHK_PAD_LEN,
        "CHK_PAD_DATA": CAN_ISOTP_CHK_PAD_DATA,
        "HALF_DUPLEX": CAN_ISOTP_HALF_DUPLEX,
        "FORCE_TXSTMIN": CAN_ISOTP_FORCE_TXSTMIN,
        "FORCE_RXSTMIN": CAN_ISOTP_FORCE_RXSTMIN,
        "RX_EXT_ADDR": CAN_ISOTP_RX_EXT_ADDR,
        "WAIT_TX_DONE": CAN_ISOTP_WAIT_TX_DONE,
        "SF_BROADCAST": CAN_ISOTP_SF_BROADCAST,
        "CF_BROADCAST": CAN_ISOTP_CF_BROADCAST,
        "DYN_FC_PARMS": CAN_ISOTP_DYN_FC_PARMS,
    }
    missing_in_b = [n for n in a_flags if not hasattr(f, n)]
    mism = [n for n, v in a_flags.items() if hasattr(f, n) and getattr(f, n) != v]
    rep.add("A flag constants equal B's where B defines them", not mism, f"mismatch: {mism}" if mism else "all equal")
    rep.add("B defines every kernel flag", not missing_in_b, f"missing in B: {missing_in_b}" if missing_in_b else "all")
    header = "/usr/include/linux/can/isotp.h"
    if os.path.exists(header):
        text = open(header).read()
        bad = []
        for n, v in a_flags.items():
            for line in text.splitlines():
                if line.startswith(f"#define CAN_ISOTP_{n}"):
                    if int(line.split()[2], 16) != v:
                        bad.append(n)
        rep.add("A constants equal the kernel uapi header", not bad, f"{header}; mismatch: {bad}" if bad else header)
    rep.add(
        "B has public settimeout/gettimeout",
        hasattr(isotp.socket, "settimeout") and hasattr(isotp.socket, "gettimeout"),
        "",
    )
    b_methods = [
        m
        for m in ("set_opts", "set_fc_opts", "set_ll_opts", "fileno", "close", "bind", "send", "recv")
        if hasattr(isotp.socket, m)
    ]
    rep.add("B option methods", len(b_methods) == 8, ", ".join(b_methods))
    ll = isotp.socket.LinkLayerProtocol
    rep.add("B exposes CAN FD link-layer enum", hasattr(ll, "CAN_FD"), f"CAN={ll.CAN}, CAN_FD={ll.CAN_FD}")
    rep.add("B exposes underlying socket", hasattr(isotp.socket(), "_socket"), "isotp.socket()._socket")


def roundtrip(
    rep: Report, label: str, ecu, tester, request: bytes, response: bytes, sniffer: RawSniffer | None
) -> None:
    t0 = time.monotonic()
    tester.send(request)
    got_req = ecu.recv()
    ecu.send(response)
    got_resp = tester.recv()
    dt = (time.monotonic() - t0) * 1000
    ok = got_req == request and got_resp == response
    detail = f"{dt:.1f} ms"
    if sniffer is not None:
        time.sleep(0.05)
        frames = sniffer.stop()
        detail += "; wire: " + fmt_frames(frames)
        dlcs = {dlc for _, dlc, _ in frames}
        rep.add(f"{label}: all frames DLC 8 (TX padding)", dlcs == {8}, f"dlcs={sorted(dlcs)}")
    rep.add(f"{label}: payload round trip", ok, detail)


def e2_e3_roundtrips(rep: Report, iface: str) -> None:
    print("E2/E3 single- and multi-frame round trips with TX padding (sniffer on the wire)")
    vin = b"\x49\x02\x01" + b"TESTVIN0123456789"
    cases = [("A ecu / B tester", make_a, make_b), ("B ecu / A tester", make_b, make_a)]
    for label, mk_ecu, mk_tester in cases:
        if isotp is None:
            continue
        sn = RawSniffer(iface)
        ecu = mk_ecu(iface, 0x7E0, 0x7E8)
        tester = mk_tester(iface, 0x7E8, 0x7E0)
        roundtrip(rep, f"{label} SF", ecu, tester, b"\x01\x0d", b"\x41\x0d\x00", sn)
        sn = RawSniffer(iface)
        roundtrip(rep, f"{label} MF(20B)", ecu, tester, b"\x09\x02", vin, sn)
        ecu.close()
        tester.close()


def e4_fd_nonblocking_asyncio(rep: Report, iface: str) -> None:
    print("E4 file descriptor, non-blocking recv, asyncio.add_reader")
    a = make_a(iface, 0x7E0, 0x7E8)
    rep.add("A fileno()", isinstance(a.fileno(), int) and a.fileno() > 0, str(a.fileno()))
    a.setblocking(False)
    try:
        a.recv()
        rep.add("A non-blocking recv with nothing pending", False, "returned data unexpectedly")
    except BlockingIOError as e:
        rep.add("A non-blocking recv with nothing pending", True, f"raises BlockingIOError errno={e.errno}")
    a.close()

    if isotp is not None:
        b = make_b(iface, 0x7E0, 0x7E8, timeout=0.0)
        fd = b.fileno()
        rep.add("B fileno()", isinstance(fd, int) and fd > 0, f"{fd} (== _socket.fileno(): {fd == b._socket.fileno()})")
        rep.add(
            "B constructor timeout=0.0 makes the socket non-blocking",
            b.gettimeout() == 0.0,
            f"gettimeout()={b.gettimeout()} (constructor applies timeout only when > 0)",
        )
        b.settimeout(0.0)
        rep.add(
            "B settimeout(0.0) makes the socket non-blocking", b.gettimeout() == 0.0, f"gettimeout()={b.gettimeout()}"
        )
        try:
            r = b.recv()
            rep.add("B non-blocking recv with nothing pending", r is None, f"returned {r!r}")
        except BlockingIOError as e:
            rep.add("B non-blocking recv with nothing pending", True, f"raises BlockingIOError errno={e.errno}")
        except Exception as e:  # noqa: BLE001 - spike: record whatever happens
            rep.add("B non-blocking recv with nothing pending", False, f"raises {type(e).__name__}: {e}")
        b.close()

    async def run_asyncio(label: str, ecu, tester) -> None:
        loop = asyncio.get_running_loop()
        woke = loop.create_future()
        loop.add_reader(ecu.fileno(), lambda: woke.done() or woke.set_result(time.monotonic()))
        t0 = time.monotonic()
        tester.send(b"\x01\x0c")
        try:
            t1 = await asyncio.wait_for(woke, 1.0)
            data = ecu.recv()
            rep.add(
                f"{label} asyncio.add_reader wake-up", data == b"\x01\x0c", f"{(t1 - t0) * 1000:.2f} ms, recv={data!r}"
            )
        except TimeoutError:
            rep.add(f"{label} asyncio.add_reader wake-up", False, "no wake-up within 1 s")
        finally:
            loop.remove_reader(ecu.fileno())

    a = make_a(iface, 0x7E0, 0x7E8)
    a.setblocking(False)
    ta = make_a(iface, 0x7E8, 0x7E0)
    asyncio.run(run_asyncio("A", a, ta))
    a.close()
    ta.close()
    if isotp is not None:
        b = make_b(iface, 0x7E0, 0x7E8)
        b.settimeout(0.0)
        tb = make_a(iface, 0x7E8, 0x7E0)
        asyncio.run(run_asyncio("B", b, tb))
        b.close()
        tb.close()


def e5_errors(rep: Report, iface: str) -> None:
    print("E5 error and cleanup behaviour")
    for label, mk in (
        ("A", lambda: make_a("nosuchcan9", 0x7E0, 0x7E8)),
        ("B", lambda: make_b("nosuchcan9", 0x7E0, 0x7E8)),
    ):
        if label == "B" and isotp is None:
            continue
        try:
            mk()
            rep.add(f"{label} bind to missing interface", False, "no error")
        except OSError as e:
            rep.add(
                f"{label} bind to missing interface", e.errno == errno.ENODEV, f"{type(e).__name__} errno={e.errno} {e}"
            )
        except Exception as e:  # noqa: BLE001
            rep.add(f"{label} bind to missing interface", False, f"{type(e).__name__}: {e}")

    a1 = make_a(iface, 0x7E0, 0x7E8)
    try:
        dup = make_a(iface, 0x7E0, 0x7E8)
        dup.close()
        rep.add("kernel accepts a second socket with identical rx/tx ids", True, "yes: no EADDRINUSE (informational)")
    except OSError as e:
        rep.add("kernel accepts a second socket with identical rx/tx ids", True, f"no: errno={e.errno} (informational)")
    a1.close()
    a2 = make_a(iface, 0x7E0, 0x7E8)
    rep.add("A rebind same ids after close", True, "ok")
    a2.close()
    try:
        a2.recv()
        rep.add("A recv after close", False, "no error")
    except OSError as e:
        rep.add("A recv after close", e.errno == errno.EBADF, f"errno={e.errno}")

    # no flow control: nobody answers on 0x7E0, multi-frame send must fail with ECOMM after N_Bs
    for label, mk in (("A", lambda: make_a(iface, 0x7E8, 0x7E0)), ("B", lambda: make_b(iface, 0x7E8, 0x7E0))):
        if label == "B" and isotp is None:
            continue
        s = mk()
        s.settimeout(2.0)
        t0 = time.monotonic()
        try:
            s.send(b"\x2e\xf1\x90" + b"X" * 20)
            # without WAIT_TX_DONE the error may surface on the next call
            try:
                s.recv()
                rep.add(f"{label} multi-frame send without FC", False, "no error surfaced")
            except OSError as e:
                ms = (time.monotonic() - t0) * 1000
                where = f"surfaced on recv: errno={e.errno} ({errno.errorcode.get(e.errno)}) after {ms:.0f} ms"
                rep.add(f"{label} multi-frame send without FC", e.errno == errno.ECOMM, where)
            except Exception as e:  # noqa: BLE001
                rep.add(f"{label} multi-frame send without FC", False, f"recv raised {type(e).__name__}: {e}")
        except OSError as e:
            ms = (time.monotonic() - t0) * 1000
            where = f"raised on send: errno={e.errno} ({errno.errorcode.get(e.errno)}) after {ms:.0f} ms"
            rep.add(f"{label} multi-frame send without FC", e.errno == errno.ECOMM, where)
        s.close()


def e6_29bit(rep: Report, iface: str) -> None:
    print("E6 29-bit normal fixed addressing (ISO 15765-4 style ids)")
    ecu_rx, ecu_tx = 0x18DA10F1, 0x18DAF110
    a = make_a(iface, ecu_rx | CAN_EFF_FLAG, ecu_tx | CAN_EFF_FLAG)
    ta = make_a(iface, ecu_tx | CAN_EFF_FLAG, ecu_rx | CAN_EFF_FLAG)
    sn = RawSniffer(iface)
    roundtrip(rep, "A 29-bit SF", a, ta, b"\x01\x0d", b"\x41\x0d\x2a", sn)
    a.close()
    ta.close()
    if isotp is not None:
        b = make_b(iface, ecu_rx, ecu_tx, mode=isotp.AddressingMode.Normal_29bits)
        tb = make_a(iface, ecu_tx | CAN_EFF_FLAG, ecu_rx | CAN_EFF_FLAG)
        sn = RawSniffer(iface)
        roundtrip(rep, "B 29-bit SF (Normal_29bits)", b, tb, b"\x01\x0d", b"\x41\x0d\x2a", sn)
        b.close()
        tb.close()


def e7_canfd(rep: Report, iface: str) -> None:
    print("E7 CAN FD link-layer options")
    # sysfs shows the host namespace, so ask iproute2 (works inside a private netns too)
    link = json.loads(subprocess.check_output(["ip", "-j", "link", "show", iface], text=True))
    mtu = int(link[0]["mtu"])
    rep.add("interface MTU", True, str(mtu))
    payload = bytes(range(60))
    for label, mk in (
        ("A", lambda: make_a(iface, 0x7E0, 0x7E8, mtu=CANFD_MTU, tx_dl=64)),
        ("B", lambda: make_b(iface, 0x7E0, 0x7E8, mtu=isotp.socket.LinkLayerProtocol.CAN_FD, tx_dl=64)),
    ):
        if label == "B" and isotp is None:
            continue
        try:
            ecu = mk()
        except OSError as e:
            rep.add(f"{label} FD ll_opts bind", False, f"errno={e.errno} {e}")
            continue
        tester = make_a(iface, 0x7E8, 0x7E0, mtu=CANFD_MTU, tx_dl=64)
        sn = RawSniffer(iface, fd=True)
        try:
            tester.send(b"\x22\xf1\x90")
            ecu.recv()
            ecu.send(payload)
            got = tester.recv()
            time.sleep(0.05)
            frames = sn.stop()
            rep.add(
                f"{label} FD 60-byte payload in one frame",
                got == payload and any(dlc >= 62 for _, dlc, _ in frames),
                "wire: " + fmt_frames(frames),
            )
        except OSError as e:
            sn.stop()
            rep.add(f"{label} FD round trip", False, f"errno={e.errno} {e} (interface MTU {mtu})")
        ecu.close()
        tester.close()


def e8_shared_functional_rx(rep: Report, iface: str, rounds: int = 20) -> None:
    print("E8 shared functional RX id 0x7DF with distinct TX ids")
    try:
        ecu1 = make_a(iface, 0x7DF, 0x7E8)
        ecu2 = make_a(iface, 0x7DF, 0x7E9)
        rep.add("bind (0x7DF->0x7E8) and (0x7DF->0x7E9) together", True, "both binds succeeded")
    except OSError as e:
        rep.add(
            "bind (0x7DF->0x7E8) and (0x7DF->0x7E9) together",
            False,
            f"errno={e.errno} ({errno.errorcode.get(e.errno)}) {e}",
        )
        return
    # tester: functional transmit socket (SF only), and two physical listeners for the responses
    tx_func = StdlibIsoTp()
    tx_func.set_opts(flags=CAN_ISOTP_TX_PADDING | CAN_ISOTP_SF_BROADCAST, txpad=0x00)
    try:
        tx_func.bind(iface, 0, 0x7DF)  # rx 0 with SF_BROADCAST: tx-only functional socket
        rep.add("tester functional tx-only socket (SF_BROADCAST)", True, "bind rx=0 tx=0x7DF ok")
    except OSError as e:
        rep.add(
            "tester functional tx-only socket (SF_BROADCAST)", False, f"errno={e.errno} {e}; falling back to rx=0x7E8"
        )
        tx_func = make_a(iface, 0x7E8, 0x7DF, padding=True)
    l1 = make_a(iface, 0x7E8, 0x7E0)
    l2 = make_a(iface, 0x7E9, 0x7E1)
    for s in (ecu1, ecu2, l1, l2):
        s.settimeout(0.5)
    sn = RawSniffer(iface)
    both = 0
    resp_ok = 0
    detail = ""
    for i in range(rounds):
        tx_func.send(b"\x01\x00")
        try:
            r1 = ecu1.recv()
            r2 = ecu2.recv()
        except TimeoutError:
            detail = f"round {i}: one ECU socket did not receive the functional request"
            break
        if r1 == r2 == b"\x01\x00":
            both += 1
        ecu1.send(b"\x41\x00\x08\x08\x00\x01")
        ecu2.send(b"\x41\x00\x00\x00\x00\x01")
        try:
            a1 = l1.recv()
            a2 = l2.recv()
        except TimeoutError:
            detail = f"round {i}: a physical response was not received"
            break
        if a1 == b"\x41\x00\x08\x08\x00\x01" and a2 == b"\x41\x00\x00\x00\x00\x01":
            resp_ok += 1
    time.sleep(0.05)
    frames = sn.stop()
    ids = sorted({cid for cid, _, _ in frames})
    rep.add(
        f"functional request received by both sockets, {rounds} rounds", both == rounds, f"{both}/{rounds} {detail}"
    )
    rep.add(
        f"independent physical responses on 0x7E8 and 0x7E9, {rounds} rounds",
        resp_ok == rounds,
        f"{resp_ok}/{rounds}; wire ids seen: {[f'{i:03X}' for i in ids]}; first 3 frames: {fmt_frames(frames[:3])}",
    )
    # does the kernel refuse a third socket with the same (rx, tx) pair as ecu1? (informational)
    try:
        dup = make_a(iface, 0x7DF, 0x7E8)
        dup.close()
        rep.add("third socket with identical (0x7DF->0x7E8) pair", True, "accepted by the kernel (no EADDRINUSE)")
    except OSError as e:
        rep.add(
            "third socket with identical (0x7DF->0x7E8) pair",
            True,
            f"refused: errno={e.errno} ({errno.errorcode.get(e.errno)})",
        )
    if isotp is not None:
        try:
            b1 = make_b(iface, 0x7DF, 0x7EA)
            b1.close()
            rep.add("B can add a further (0x7DF->0x7EA) socket alongside A's", True, "ok")
        except OSError as e:
            rep.add("B can add a further (0x7DF->0x7EA) socket alongside A's", False, f"errno={e.errno} {e}")
    for s in (ecu1, ecu2, tx_func, l1, l2):
        s.close()


def e9_can_utils(rep: Report, iface: str) -> None:
    print("E9 can-utils cross-check (isotpsend/isotprecv as an independent tester)")
    if not (shutil.which("isotpsend") and shutil.which("isotprecv")):
        rep.add("can-utils present", False, "isotpsend/isotprecv not found")
        return
    ecu = make_a(iface, 0x7E0, 0x7E8)
    ecu.settimeout(2.0)
    vin = b"\x49\x02\x01" + b"TESTVIN0123456789"
    # no -l: isotprecv exits after one PDU, so its (block-buffered) stdout is flushed
    recv = subprocess.Popen(["isotprecv", "-s", "7E0", "-d", "7E8", iface], stdout=subprocess.PIPE, text=True)
    time.sleep(0.2)
    subprocess.run(["isotpsend", "-s", "7E0", "-d", "7E8", iface], input="09 02\n", text=True, check=True, timeout=5)
    req = ecu.recv()
    ecu.send(vin)
    try:
        out, _ = recv.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        recv.kill()
        out, _ = recv.communicate()
    got = bytes.fromhex(out.split("\n")[0].replace(" ", "")) if out.strip() else b""
    rep.add(
        "isotpsend -> A ecu -> isotprecv (20-byte VIN)",
        req == b"\x09\x02" and got == vin,
        f"isotprecv printed {out.strip()!r}",
    )
    ecu.close()


# --------------------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------------------


def run_all(iface: str, only: list[str] | None) -> Report:
    rep = Report(interface=iface)
    print(f"kernel {rep.kernel}, python {rep.python}, can-isotp {rep.can_isotp}, interface {iface}\n")
    experiments = {
        "e1": lambda: e1_options(rep),
        "e2": lambda: e2_e3_roundtrips(rep, iface),
        "e4": lambda: e4_fd_nonblocking_asyncio(rep, iface),
        "e5": lambda: e5_errors(rep, iface),
        "e6": lambda: e6_29bit(rep, iface),
        "e7": lambda: e7_canfd(rep, iface),
        "e8": lambda: e8_shared_functional_rx(rep, iface),
        "e9": lambda: e9_can_utils(rep, iface),
    }
    for name, fn in experiments.items():
        if only and name not in only:
            continue
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - spike: never abort the whole report
            rep.add(f"{name} crashed", False, f"{type(e).__name__}: {e}")
        print()
    return rep


def private_netns(argv: list[str]) -> int:
    """Re-exec inside an unprivileged user+net namespace with a fresh vcan0 (MTU 72)."""
    inner = (
        "ip link add dev vcan0 type vcan && ip link set vcan0 mtu 72 && ip link set up vcan0 && "
        f"exec {sys.executable} {os.path.abspath(__file__)} --interface vcan0 " + " ".join(argv)
    )
    return subprocess.call(["unshare", "-r", "-n", "bash", "-c", inner])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--interface", default="vcan0")
    p.add_argument("--private-netns", action="store_true", help="run inside `unshare -r -n` with a private vcan0")
    p.add_argument("--only", nargs="*", help="subset of experiments: e1 e2 e4 e5 e6 e7 e8 e9")
    p.add_argument("--json", help="also write the report as JSON to this path")
    p.add_argument("--summary", action="store_true", help="print a one-line pass/fail summary at the end")
    args = p.parse_args()
    if args.private_netns:
        rest = [a for a in sys.argv[1:] if a != "--private-netns"]
        return private_netns(rest)
    rep = run_all(args.interface, args.only)
    failed = [r.name for r in rep.results if not r.ok]
    if args.json:
        with open(args.json, "w") as f:
            json.dump(
                {
                    "kernel": rep.kernel,
                    "python": rep.python,
                    "can_isotp": rep.can_isotp,
                    "interface": rep.interface,
                    "results": [r.__dict__ for r in rep.results],
                },
                f,
                indent=2,
            )
    if args.summary:
        print(
            f"SUMMARY kernel={rep.kernel} python={rep.python} can-isotp={rep.can_isotp} "
            f"positive={len(rep.results) - len(failed)}/{len(rep.results)} negative={failed}"
        )
    return 0


A_WRAPPER_LINES = wrapper_line_count() if __name__ == "__main__" else 0

if __name__ == "__main__":
    sys.exit(main())
