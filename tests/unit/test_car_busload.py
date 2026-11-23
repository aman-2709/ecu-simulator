"""How loaded a physical CAN bus would be, and what ``ip`` says about the interface a whole-car run targets.

The ``ip`` output below was captured from iproute2 5.15 and is copied verbatim: ``can0`` is a gs_usb adapter on the
bench host, read with ``ip -j -d link show dev can0`` (and with ``-s`` added for the counters), ``vcan0`` and ``vx0``
are virtual interfaces made in a private network namespace, and ``lo`` is a link that is not CAN at all. Tests that need
a different state or counter edit a parsed copy of it, so the keys stay the ones iproute2 prints.
"""

import copy
import json
import subprocess
import sys
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from ecu_simulator.car.busload import (
    BUS_LOAD_CEILING,
    BusLoadError,
    LinkInfo,
    check_bus_load,
    estimate_load,
    frame_bits,
    read_link,
    read_link_stats,
)

# ip -j -d link show dev can0
CAN0_DETAIL = '[{"ifindex":1126,"ifname":"can0","flags":["NOARP","UP","LOWER_UP","ECHO"],"mtu":16,"qdisc":"pfifo_fast","operstate":"UP","linkmode":"DEFAULT","group":"default","txqlen":10,"link_type":"can","promiscuity":0,"min_mtu":0,"max_mtu":0,"linkinfo":{"info_kind":"can","info_data":{"state":"ERROR-ACTIVE","restart_ms":0,"bittiming":{"bitrate":500000,"sample_point":0.875,"tq":125,"prop_seg":6,"phase_seg1":7,"phase_seg2":2,"sjw":1},"bittiming_const":{"name":"gs_usb","tseg1":{"min":1,"max":16},"tseg2":{"min":1,"max":8},"sjw":{"min":1,"max":4},"brp":{"min":1,"max":1024},"brp_inc":1},"clock":48000000}},"num_tx_queues":1,"num_rx_queues":1,"gso_max_size":65536,"gso_max_segs":65535,"parentbus":"usb","parentdev":"1-9.4:1.0"}]'
# ip -j -d -s link show dev can0
CAN0_STATS = '[{"ifindex":1126,"ifname":"can0","flags":["NOARP","UP","LOWER_UP","ECHO"],"mtu":16,"qdisc":"pfifo_fast","operstate":"UP","linkmode":"DEFAULT","group":"default","txqlen":10,"link_type":"can","promiscuity":0,"min_mtu":0,"max_mtu":0,"linkinfo":{"info_kind":"can","info_data":{"state":"ERROR-ACTIVE","restart_ms":0,"bittiming":{"bitrate":500000,"sample_point":0.875,"tq":125,"prop_seg":6,"phase_seg1":7,"phase_seg2":2,"sjw":1},"bittiming_const":{"name":"gs_usb","tseg1":{"min":1,"max":16},"tseg2":{"min":1,"max":8},"sjw":{"min":1,"max":4},"brp":{"min":1,"max":1024},"brp_inc":1},"clock":48000000},"info_xstats":{"restarts":0,"bus_error":0,"arbitration_lost":0,"error_warning":0,"error_passive":0,"bus_off":0}},"num_tx_queues":1,"num_rx_queues":1,"gso_max_size":65536,"gso_max_segs":65535,"parentbus":"usb","parentdev":"1-9.4:1.0","stats64":{"rx":{"bytes":272,"packets":34,"errors":0,"dropped":0,"over_errors":0,"multicast":0},"tx":{"bytes":272,"packets":34,"errors":0,"dropped":0,"carrier_errors":0,"collisions":0}}}]'
# ip -j -d link show dev vcan0, and the same with -s
VCAN0_DETAIL = '[{"ifindex":2,"ifname":"vcan0","flags":["NOARP","UP","LOWER_UP"],"mtu":72,"qdisc":"noqueue","operstate":"UNKNOWN","linkmode":"DEFAULT","group":"default","txqlen":1000,"link_type":"can","promiscuity":0,"min_mtu":0,"max_mtu":0,"linkinfo":{"info_kind":"vcan"},"num_tx_queues":1,"num_rx_queues":1,"gso_max_size":65536,"gso_max_segs":65535}]'
VCAN0_STATS = '[{"ifindex":2,"ifname":"vcan0","flags":["NOARP","UP","LOWER_UP"],"mtu":72,"qdisc":"noqueue","operstate":"UNKNOWN","linkmode":"DEFAULT","group":"default","txqlen":1000,"link_type":"can","promiscuity":0,"min_mtu":0,"max_mtu":0,"linkinfo":{"info_kind":"vcan"},"num_tx_queues":1,"num_rx_queues":1,"gso_max_size":65536,"gso_max_segs":65535,"stats64":{"rx":{"bytes":0,"packets":0,"errors":0,"dropped":0,"over_errors":0,"multicast":0},"tx":{"bytes":0,"packets":0,"errors":0,"dropped":0,"carrier_errors":0,"collisions":0}}}]'
# ip -j -d link show dev vx0, one end of a vxcan pair
VX0_DETAIL = '[{"ifindex":4,"link":"vx1","ifname":"vx0","flags":["NOARP","M-DOWN"],"mtu":72,"qdisc":"noop","operstate":"DOWN","linkmode":"DEFAULT","group":"default","txqlen":1000,"link_type":"can","promiscuity":0,"min_mtu":0,"max_mtu":0,"linkinfo":{"info_kind":"vxcan"},"num_tx_queues":1,"num_rx_queues":1,"gso_max_size":65536,"gso_max_segs":65535}]'
# ip -j -d link show dev lo
LO_DETAIL = '[{"ifindex":1,"ifname":"lo","flags":["LOOPBACK","UP","LOWER_UP"],"mtu":65536,"qdisc":"noqueue","operstate":"UNKNOWN","linkmode":"DEFAULT","group":"default","txqlen":1000,"link_type":"loopback","address":"00:00:00:00:00:00","broadcast":"00:00:00:00:00:00","promiscuity":0,"min_mtu":0,"max_mtu":0,"inet6_addr_gen_mode":"eui64","num_tx_queues":1,"num_rx_queues":1,"gso_max_size":65536,"gso_max_segs":65535}]'
# ip -j -d link show dev nope0, which exits 1 and prints this to stderr
MISSING_STDERR = 'Device "nope0" does not exist.\n'

PHYSICAL = LinkInfo("can0", False, 500000, "ERROR-ACTIVE")
VIRTUAL = LinkInfo("vcan0", True, None, None)


class FakeIp:
    """Stands in for ``subprocess.run`` and records what it was asked to run."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> "subprocess.CompletedProcess[bytes]":
        self.calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, self.returncode, self.stdout.encode(), self.stderr.encode())


@pytest.fixture
def ip(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeIp]:
    def install(stdout: str = "", stderr: str = "", returncode: int = 0) -> FakeIp:
        fake = FakeIp(stdout, stderr, returncode)
        monkeypatch.setattr(subprocess, "run", fake)
        return fake

    return install


def link_json(text: str) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = json.loads(text)
    return copy.deepcopy(parsed)


def frames_at(count: int, dlc: int, extended: bool, period_s: float) -> Iterator[tuple[str, int, bool, float]]:
    return ((f"Message{index}", dlc, extended, period_s) for index in range(count))


# --- frame size and load ---------------------------------------------------------------------------------------------


def test_frame_bits_standard_8_bytes_is_135() -> None:
    assert frame_bits(8, False) == 135


def test_frame_bits_extended_8_bytes() -> None:
    assert frame_bits(8, True) == 160


def test_frame_bits_with_no_data_bytes() -> None:
    assert frame_bits(0, False) == 34 + 13 + 33 // 4
    assert frame_bits(0, True) == 54 + 13 + 53 // 4


@pytest.mark.parametrize("dlc", [-1, 9, 64])
@pytest.mark.parametrize("extended", [False, True])
def test_dlc_out_of_range_is_rejected(dlc: int, extended: bool) -> None:
    with pytest.raises(ValueError, match=str(dlc)):
        frame_bits(dlc, extended)


def test_one_message_20ms_on_500k() -> None:
    assert estimate_load([("EngineData", 8, False, 0.02)], 500000) == pytest.approx(0.0135)


def test_estimate_load_adds_every_message_and_reads_a_generator_once() -> None:
    frames = [("A", 8, False, 0.02), ("B", 8, True, 0.01), ("C", 0, False, 0.1)]
    expected = (135 / 0.02 + 160 / 0.01 + frame_bits(0, False) / 0.1) / 250000
    assert estimate_load(iter(frames), 250000) == pytest.approx(expected)


@pytest.mark.parametrize("bitrate", [0, -500000])
def test_estimate_load_rejects_a_non_positive_bitrate(bitrate: int) -> None:
    with pytest.raises(ValueError, match="bitrate"):
        estimate_load([("A", 8, False, 0.02)], bitrate)


@pytest.mark.parametrize("period_s", [0.0, -0.02])
def test_estimate_load_rejects_a_non_positive_period(period_s: float) -> None:
    with pytest.raises(ValueError, match="EngineData"):
        estimate_load([("EngineData", 8, False, period_s)], 500000)


def test_estimate_load_rejects_a_dlc_outside_0_to_8() -> None:
    with pytest.raises(ValueError, match="9"):
        estimate_load([("A", 9, False, 0.02)], 500000)


# --- the refusal -----------------------------------------------------------------------------------------------------


def test_virtual_never_refuses_even_over_100_percent() -> None:
    frames = [(f"M{index}", 8, True, 0.001) for index in range(10)]
    load = check_bus_load(frames, VIRTUAL)
    assert load == pytest.approx(estimate_load(frames, 500000))
    assert load > 1.0


def test_virtual_ignores_a_bitrate_and_state_the_link_happens_to_carry() -> None:
    frames = [("A", 8, True, 0.001)] * 5
    link = LinkInfo("vx0", True, 125000, "STOPPED")
    assert check_bus_load(frames, link) == pytest.approx(estimate_load(frames, 500000))


def test_physical_under_the_ceiling_returns_the_load() -> None:
    frames = [("EngineData", 8, False, 0.02)]
    assert check_bus_load(frames, PHYSICAL) == pytest.approx(0.0135)
    assert check_bus_load(frames, LinkInfo("can0", False, 250000, "ERROR-ACTIVE")) == pytest.approx(0.027)


def test_physical_over_ceiling_names_the_three_largest_messages() -> None:
    frames = [
        ("WheelSpeeds", 8, False, 0.002),  # 135 bits every 2 ms, 13.5 % of a 500 kbit/s bus
        ("SteeringAngle", 8, True, 0.001),  # 32 %
        ("EngineData", 8, False, 0.001),  # 27 %
        ("Seatbelt", 8, False, 0.01),  # 2.7 %
        ("GearBox", 8, False, 0.0015),  # 18 %
    ]
    with pytest.raises(BusLoadError) as caught:
        check_bus_load(frames, PHYSICAL)
    error = caught.value
    assert isinstance(error, ValueError)
    assert [name for name, _ in error.over] == ["SteeringAngle", "EngineData", "GearBox"]
    assert [share for _, share in error.over] == pytest.approx([0.32, 0.27, 0.18])
    for name in ("SteeringAngle", "EngineData", "GearBox"):
        assert name in str(error)
    assert "Seatbelt" not in str(error)
    assert "WheelSpeeds" not in str(error)
    assert "ceiling" in str(error)
    assert "70%" in str(error)
    assert "setup_can.sh" not in str(error)


def test_over_ceiling_names_every_message_when_fewer_than_three_exist() -> None:
    with pytest.raises(BusLoadError) as caught:
        check_bus_load([("OnlyOne", 8, True, 0.0002)], PHYSICAL)
    assert [name for name, _ in caught.value.over] == ["OnlyOne"]


def test_physical_without_bitrate_refuses_with_setup_hint() -> None:
    link = LinkInfo("can0", False, None, "ERROR-ACTIVE")
    with pytest.raises(BusLoadError) as caught:
        check_bus_load([("EngineData", 8, False, 0.02)], link)
    message = str(caught.value)
    assert "scripts/setup_can.sh" in message
    assert "can0" in message
    assert "ceiling" not in message
    assert caught.value.over == []


def test_physical_link_down_refuses() -> None:
    link = LinkInfo("can0", False, 500000, "STOPPED")
    with pytest.raises(BusLoadError, match="^interface is down$") as caught:
        check_bus_load([("EngineData", 8, False, 0.02)], link)
    assert caught.value.over == []


def test_down_wins_over_the_ceiling() -> None:
    link = LinkInfo("can0", False, 500000, "STOPPED")
    with pytest.raises(BusLoadError) as caught:
        check_bus_load(frames_at(20, 8, True, 0.001), link)
    assert str(caught.value) == "interface is down"
    assert caught.value.over == []


def test_load_at_exactly_the_ceiling_is_allowed() -> None:
    # 4375 extended 8-byte frames of 160 bits each second are 700000 bit/s: exactly 70 % of 1 Mbit/s.
    link = LinkInfo("can0", False, 1_000_000, "ERROR-ACTIVE")
    assert BUS_LOAD_CEILING == 0.70
    assert check_bus_load(frames_at(4375, 8, True, 1.0), link) == BUS_LOAD_CEILING
    with pytest.raises(BusLoadError, match="ceiling"):
        check_bus_load(frames_at(4376, 8, True, 1.0), link)


def test_a_generator_of_frames_is_enough() -> None:
    assert check_bus_load(frames_at(3, 8, False, 0.02), PHYSICAL) == pytest.approx(3 * 0.0135)


def test_the_three_refusals_are_told_apart_by_their_message() -> None:
    frames = list(frames_at(20, 8, True, 0.001))
    messages = []
    for link in (LinkInfo("can0", False, None, "ERROR-ACTIVE"), LinkInfo("can0", False, 500000, "STOPPED"), PHYSICAL):
        with pytest.raises(BusLoadError) as caught:
            check_bus_load(frames, link)
        messages.append(str(caught.value))
    assert len(set(messages)) == 3


# --- reading the link ------------------------------------------------------------------------------------------------


def test_read_link_parses_can_and_vcan_json(ip: Callable[..., FakeIp]) -> None:
    fake = ip(stdout=CAN0_DETAIL)
    assert read_link("can0") == PHYSICAL
    assert fake.calls[0][0] == ["ip", "-j", "-d", "link", "show", "dev", "can0"]
    assert fake.calls[0][1]["timeout"] == 2

    ip(stdout=VCAN0_DETAIL)
    assert read_link("vcan0") == VIRTUAL

    ip(stdout=VX0_DETAIL)
    assert read_link("vx0") == LinkInfo("vx0", True, None, None)


def test_read_link_reports_a_stopped_interface_and_one_with_no_bitrate(ip: Callable[..., FakeIp]) -> None:
    link = link_json(CAN0_DETAIL)
    link[0]["linkinfo"]["info_data"]["state"] = "STOPPED"
    ip(stdout=json.dumps(link))
    assert read_link("can0") == LinkInfo("can0", False, 500000, "STOPPED")

    del link[0]["linkinfo"]["info_data"]["bittiming"]
    ip(stdout=json.dumps(link))
    assert read_link("can0") == LinkInfo("can0", False, None, "STOPPED")

    del link[0]["linkinfo"]["info_data"]
    ip(stdout=json.dumps(link))
    assert read_link("can0") == LinkInfo("can0", False, None, None)


def test_read_link_missing_interface_names_it(ip: Callable[..., FakeIp]) -> None:
    ip(stderr=MISSING_STDERR + "second line\n", returncode=1)
    with pytest.raises(ValueError) as caught:
        read_link("nope0")
    assert "nope0" in str(caught.value)
    assert 'Device "nope0" does not exist.' in str(caught.value)
    assert "second line" not in str(caught.value)


def test_read_link_missing_interface_without_stderr_still_names_it(ip: Callable[..., FakeIp]) -> None:
    ip(returncode=2)
    with pytest.raises(ValueError, match="nope0"):
        read_link("nope0")


def test_read_link_rejects_a_non_can_interface(ip: Callable[..., FakeIp]) -> None:
    ip(stdout=LO_DETAIL)
    with pytest.raises(ValueError, match="lo is not a CAN interface"):
        read_link("lo")


@pytest.mark.parametrize("stdout", ["", "not json", "[]", "{}", "[1]", '[{"ifname": "can0"}]'])
def test_read_link_rejects_output_it_cannot_read(ip: Callable[..., FakeIp], stdout: str) -> None:
    ip(stdout=stdout)
    with pytest.raises(ValueError, match="can0"):
        read_link("can0")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (FileNotFoundError(2, "No such file or directory: 'ip'"), "not installed"),
        (PermissionError(13, "Permission denied"), "cannot run ip"),
        (subprocess.TimeoutExpired(["ip"], 2), "timed out"),
    ],
)
def test_read_link_reports_ip_missing_and_timeout_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected: str
) -> None:
    def broken(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(subprocess, "run", broken)
    for read in (read_link, read_link_stats):
        with pytest.raises(ValueError, match="can0") as caught:
            read("can0")
        assert expected in str(caught.value)
        assert caught.value.__cause__ is error


@pytest.mark.parametrize(
    "name", ["-x", "--help", "-j", "a b", "", "x" * 16, "can0;ls", "can0\n", "can0/1", "can:0", "é"]
)
def test_interface_name_cannot_become_an_ip_option(ip: Callable[..., FakeIp], name: str) -> None:
    fake = ip(stdout=CAN0_DETAIL)
    for read in (read_link, read_link_stats):
        with pytest.raises(ValueError, match="interface name"):
            read(name)
    assert fake.calls == []


@pytest.mark.parametrize("name", ["can0", "vcan0", "slcan0", "vx0", "can0.12", "can_1", "x" * 15])
def test_ordinary_interface_names_reach_ip(ip: Callable[..., FakeIp], name: str) -> None:
    fake = ip(stdout=CAN0_DETAIL)
    read_link(name)
    assert fake.calls[0][0][-2:] == ["dev", name]


# --- the counters ----------------------------------------------------------------------------------------------------


def test_read_link_stats_reads_the_counters_from_where_iproute2_prints_them(ip: Callable[..., FakeIp]) -> None:
    link = link_json(CAN0_STATS)
    link[0]["linkinfo"]["info_xstats"].update(bus_off=3, bus_error=17, restarts=3, error_warning=5)
    link[0]["stats64"]["tx"]["dropped"] = 11
    link[0]["stats64"]["rx"]["dropped"] = 99
    fake = ip(stdout=json.dumps(link))
    assert read_link_stats("can0") == {
        "state": "ERROR-ACTIVE",
        "bitrate": 500000,
        "bus_off": 3,
        "bus_errors": 17,
        "tx_dropped": 11,
    }
    assert fake.calls[0][0] == ["ip", "-j", "-d", "-s", "link", "show", "dev", "can0"]
    assert fake.calls[0][1]["timeout"] == 2


def test_read_link_stats_on_an_idle_adapter(ip: Callable[..., FakeIp]) -> None:
    ip(stdout=CAN0_STATS)
    assert read_link_stats("can0") == {
        "state": "ERROR-ACTIVE",
        "bitrate": 500000,
        "bus_off": 0,
        "bus_errors": 0,
        "tx_dropped": 0,
    }


def test_read_link_stats_returns_none_for_missing_counters(ip: Callable[..., FakeIp]) -> None:
    ip(stdout=VCAN0_STATS)
    assert read_link_stats("vcan0") == {
        "state": None,
        "bitrate": None,
        "bus_off": None,
        "bus_errors": None,
        "tx_dropped": 0,
    }

    link = link_json(CAN0_STATS)
    del link[0]["linkinfo"]["info_xstats"]
    del link[0]["stats64"]
    ip(stdout=json.dumps(link))
    assert read_link_stats("can0") == {
        "state": "ERROR-ACTIVE",
        "bitrate": 500000,
        "bus_off": None,
        "bus_errors": None,
        "tx_dropped": None,
    }


def test_read_link_stats_ignores_a_counter_that_is_not_a_number(ip: Callable[..., FakeIp]) -> None:
    link = link_json(CAN0_STATS)
    link[0]["linkinfo"]["info_xstats"]["bus_off"] = "many"
    link[0]["linkinfo"]["info_xstats"]["bus_error"] = True
    link[0]["stats64"]["tx"]["dropped"] = None
    ip(stdout=json.dumps(link))
    stats = read_link_stats("can0")
    assert (stats["bus_off"], stats["bus_errors"], stats["tx_dropped"]) == (None, None, None)


def test_read_link_stats_raises_the_same_errors_as_read_link(ip: Callable[..., FakeIp]) -> None:
    ip(stderr=MISSING_STDERR, returncode=1)
    with pytest.raises(ValueError, match="nope0"):
        read_link_stats("nope0")
    ip(stdout=LO_DETAIL)
    with pytest.raises(ValueError, match="lo is not a CAN interface"):
        read_link_stats("lo")
    ip(stdout="not json")
    with pytest.raises(ValueError, match="can0"):
        read_link_stats("can0")


# --- layering --------------------------------------------------------------------------------------------------------


def test_module_is_a_leaf_that_imports_only_the_standard_library() -> None:
    program = (
        "import sys; import ecu_simulator.car.busload; "
        "print(sorted(m for m in sys.modules if m.startswith('ecu_simulator')))"
    )
    result = subprocess.run([sys.executable, "-I", "-c", program], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['ecu_simulator', 'ecu_simulator.car', 'ecu_simulator.car.busload']"
