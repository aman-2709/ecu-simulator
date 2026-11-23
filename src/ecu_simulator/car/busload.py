"""How loaded a physical CAN bus would be, and what ``ip`` says about the interface a whole-car run targets.

``estimate_load`` counts every frame at its worst case with bit stuffing, so the number is an upper bound on what the
bus carries, not a measurement. ``check_bus_load`` is the gate before Play: a physical interface is refused when it has
no bitrate, is down, or the run would load it past ``BUS_LOAD_CEILING``. A virtual one (``vcan``, ``vxcan``) is never
refused because it has no wire to overload, so its estimate assumes 500 kbit/s and is informational. The simulator
never configures a bitrate; ``scripts/setup_can.sh`` does, and the refusal says so. ``read_link`` and
``read_link_stats`` read the interface through ``ip -j``, the way ``gui.dbc_workflow.virtual_can`` does; every failure
to read it is a ``ValueError`` that names the interface.
"""

import json
import math
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass

BUS_LOAD_CEILING = 0.70
VIRTUAL_BITRATE = 500000  # bit/s assumed for a virtual interface, which has no bit timing of its own

_VIRTUAL_KINDS = frozenset({"vcan", "vxcan"})
_DOWN_STATE = "STOPPED"
_IP_TIMEOUT_S = 2
_LARGEST_SHOWN = 3

# The name rule scripts/setup_can.sh applies (at most 15 characters of letters, digits, '_', '.' and '-'), without a
# leading '-', so a name can never be read by ip as an option. fullmatch keeps a trailing newline out.
_INTERFACE_NAME = re.compile(r"(?!-)[A-Za-z0-9_.-]{1,15}")

# Bits of a classical CAN frame that stuffing applies to, before the data: the arbitration, control and CRC fields come
# to 34 bits with an 11-bit identifier and 54 with a 29-bit one. After the stuffed region come 13 bits that are not
# stuffed (CRC delimiter, ACK slot and delimiter, end of frame and intermission).
_STUFFED_BITS = {False: 34, True: 54}
_UNSTUFFED_BITS = 13
_MAX_DLC = 8


@dataclass(frozen=True)
class LinkInfo:
    interface: str
    virtual: bool
    bitrate: int | None
    state: str | None


class BusLoadError(ValueError):
    """A run the bus cannot take. ``over`` holds the largest contributors as ``(name, share of the bus)``."""

    def __init__(self, message: str, over: Iterable[tuple[str, float]] = ()) -> None:
        super().__init__(message)
        self.over: list[tuple[str, float]] = list(over)


def frame_bits(dlc: int, extended: bool) -> int:
    """Bits one classical CAN frame takes on the wire at worst, with one stuff bit for every four after the first."""
    if not 0 <= dlc <= _MAX_DLC:
        raise ValueError(f"A classical CAN frame carries 0 to {_MAX_DLC} data bytes, not {dlc}")
    stuffed = _STUFFED_BITS[extended] + 8 * dlc
    return stuffed + _UNSTUFFED_BITS + (stuffed - 1) // 4


def _bit_rates(frames: Iterable[tuple[str, int, bool, float]]) -> list[tuple[str, float]]:
    """Each message's bits per second, in the order given."""
    rates: list[tuple[str, float]] = []
    for name, dlc, extended, period_s in frames:
        if not period_s > 0:
            raise ValueError(f"{name}: the period must be positive, not {period_s}")
        rates.append((name, frame_bits(dlc, extended) / period_s))
    return rates


def _load(rates: list[tuple[str, float]], bitrate: int) -> float:
    if bitrate <= 0:
        raise ValueError(f"The bitrate must be positive, not {bitrate}")
    return math.fsum(rate for _, rate in rates) / bitrate


def estimate_load(frames: Iterable[tuple[str, int, bool, float]], bitrate: int) -> float:
    """Worst-case bits per second of ``(name, dlc, extended, period_s)`` frames, as a fraction of ``bitrate``."""
    return _load(_bit_rates(frames), bitrate)


def check_bus_load(frames: Iterable[tuple[str, int, bool, float]], link: LinkInfo) -> float:
    """Return the estimated load of ``frames`` on ``link``, or raise ``BusLoadError`` if a run must not start."""
    rates = _bit_rates(frames)
    if link.virtual:
        return _load(rates, VIRTUAL_BITRATE)
    if link.bitrate is None:
        raise BusLoadError(
            f"{link.interface} has no bitrate set. Configure it with scripts/setup_can.sh "
            f"(sudo scripts/setup_can.sh {link.interface} 500000); the simulator never sets a bitrate"
        )
    if link.state == _DOWN_STATE:
        raise BusLoadError("interface is down")
    bitrate = link.bitrate
    load = _load(rates, bitrate)
    if load <= BUS_LOAD_CEILING:
        return load
    shares = ((name, rate / bitrate) for name, rate in rates)
    over = sorted(shares, key=lambda share: share[1], reverse=True)[:_LARGEST_SHOWN]
    contributors = ", ".join(f"{name} {share:.1%}" for name, share in over)
    raise BusLoadError(
        f"Estimated load {load:.1%} of {link.interface} at {bitrate} bit/s is above the {BUS_LOAD_CEILING:.0%} "
        f"ceiling. Largest messages: {contributors}",
        over,
    )


def _dig(data: object, *path: str) -> object:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _number(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _link_entry(interface: str, statistics: bool) -> dict[str, object]:
    """The one link ``ip -j -d [-s] link show dev <interface>`` prints, checked to be a CAN interface."""
    if not _INTERFACE_NAME.fullmatch(interface):
        raise ValueError(
            f"Invalid interface name {interface!r}: use up to 15 letters, digits, '_', '.' or '-', "
            "and do not start with '-'"
        )
    command = ["ip", "-j", "-d", *(["-s"] if statistics else []), "link", "show", "dev", interface]
    try:
        result = subprocess.run(command, check=False, capture_output=True, timeout=_IP_TIMEOUT_S)
    except FileNotFoundError as error:
        raise ValueError(f"Cannot read {interface}: ip (iproute2) is not installed") from error
    except OSError as error:
        raise ValueError(f"Cannot read {interface}: cannot run ip ({error})") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"Cannot read {interface}: ip timed out after {_IP_TIMEOUT_S} s") from error
    if result.returncode != 0:
        lines = result.stderr.decode(errors="replace").strip().splitlines()
        detail = lines[0] if lines else f"ip exited with status {result.returncode}"
        raise ValueError(f"Cannot read {interface}: {detail}")
    try:
        parsed = json.loads(result.stdout)
    except ValueError as error:
        raise ValueError(f"Cannot read {interface}: ip did not print JSON") from error
    if not isinstance(parsed, list) or not parsed or not isinstance(parsed[0], dict):
        raise ValueError(f"Cannot read {interface}: ip printed no link")
    entry: dict[str, object] = parsed[0]
    if entry.get("link_type") != "can":
        raise ValueError(f"{interface} is not a CAN interface (link type {_text(entry.get('link_type')) or 'unknown'})")
    return entry


def _link_info(interface: str, entry: dict[str, object]) -> LinkInfo:
    bitrate = _number(_dig(entry, "linkinfo", "info_data", "bittiming", "bitrate"))
    return LinkInfo(
        interface=interface,
        virtual=_text(_dig(entry, "linkinfo", "info_kind")) in _VIRTUAL_KINDS,
        bitrate=bitrate if bitrate is not None and bitrate > 0 else None,
        state=_text(_dig(entry, "linkinfo", "info_data", "state")),
    )


def read_link(interface: str) -> LinkInfo:
    """Read ``interface`` with ``ip -j -d link show``; raise ``ValueError`` if it is missing, not CAN or unreadable."""
    return _link_info(interface, _link_entry(interface, statistics=False))


def read_link_stats(interface: str) -> dict[str, int | str | None]:
    """The link state, bitrate and error counters of ``interface``, each ``None`` when ``ip`` does not print it."""
    entry = _link_entry(interface, statistics=True)
    link = _link_info(interface, entry)
    return {
        "state": link.state,
        "bitrate": link.bitrate,
        "bus_off": _number(_dig(entry, "linkinfo", "info_xstats", "bus_off")),
        "bus_errors": _number(_dig(entry, "linkinfo", "info_xstats", "bus_error")),
        "tx_dropped": _number(_dig(entry, "stats64", "tx", "dropped")),
    }
