"""scripts/setup_can.sh, driven against a mock ``ip`` on PATH.

The script's whole job is to issue the right privileged link commands, so these tests
assert the exact command sequence rather than any effect. A mock lets that run on a host
with no CAN adapter and no root -- which is every host this project has until Phase 8b.

**The tests never name a real interface.** ``TEST_IFACE`` is deliberately an interface that
does not exist on any developer machine, so that if the PATH mock were ever bypassed the
real ``ip`` would fail on a missing interface rather than reconfiguring somebody's live
bus. A bench host running these tests has a working ``can0`` and this suite must not be
able to touch it.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SETUP_CAN = REPO / "scripts" / "setup_can.sh"

# Not can0, and not any name a real bench would use. See the module docstring.
TEST_IFACE = "cantest9"


def fake_ip_bin(tmp_path: pathlib.Path, details: str = "") -> pathlib.Path:
    """A directory containing a mock ``ip`` that logs its arguments to ip.log.

    ``details`` is printed verbatim for ``ip -details link show``, which is how the script
    discovers whether the controller supports switchable termination.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "ip.log"
    script = bin_dir / "ip"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {log}\n'
        'if [[ "$*" == *"-details"* && "$*" == *"link show"* ]]; then\n'
        f"  cat <<'EOF'\n{details}\nEOF\n"
        "fi\n"
        "exit 0\n"
    )
    script.chmod(0o755)
    return bin_dir


def ip_calls(tmp_path: pathlib.Path) -> list[str]:
    log = tmp_path / "ip.log"
    return log.read_text().splitlines() if log.exists() else []


def run_setup_can(bin_dir: pathlib.Path, *args: str, env: dict[str, str] | None = None):
    environment = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    for key in ("CAN_RESTART_MS", "CAN_TERMINATION"):
        environment.pop(key, None)
    environment.update(env or {})
    return subprocess.run(
        ["bash", str(SETUP_CAN), *args], capture_output=True, text=True, env=environment
    )


# --- existing behaviour, pinned so this task cannot regress it -------------------------------


def test_usage_is_printed_without_an_interface(tmp_path):
    result = run_setup_can(fake_ip_bin(tmp_path))
    assert result.returncode != 0
    assert "usage: setup_can.sh" in result.stderr


def test_an_invalid_interface_name_is_refused(tmp_path):
    result = run_setup_can(fake_ip_bin(tmp_path), "this-name-is-far-too-long-for-an-interface")
    assert result.returncode != 0
    assert "invalid interface name" in result.stderr


def test_a_rejected_bitrate_stops_before_touching_the_link(tmp_path):
    bin_dir = fake_ip_bin(tmp_path)
    result = run_setup_can(bin_dir, TEST_IFACE, "not-a-number")
    assert result.returncode != 0
    assert "bitrate must be an integer" in result.stderr
    assert ip_calls(tmp_path) == []


def test_the_positional_contract_is_unchanged(tmp_path):
    # `setup_can.sh <interface> [bitrate]` appears in the README, in the simulator's own
    # guard messages (transport/socketcan/interface.py) and in the testbench document.
    # Task 3 adds environment variables precisely so this does not have to move.
    bin_dir = fake_ip_bin(tmp_path)
    assert run_setup_can(bin_dir, TEST_IFACE).returncode == 0
    assert any(f"link set {TEST_IFACE} type can bitrate 500000" in c for c in ip_calls(tmp_path))


def test_an_explicit_bitrate_is_honoured(tmp_path):
    bin_dir = fake_ip_bin(tmp_path)
    run_setup_can(bin_dir, TEST_IFACE, "250000")
    assert any(f"link set {TEST_IFACE} type can bitrate 250000" in c for c in ip_calls(tmp_path))


# --- gap 1: automatic bus-off recovery -------------------------------------------------------


def test_restart_ms_is_set_by_default(tmp_path):
    bin_dir = fake_ip_bin(tmp_path)
    result = run_setup_can(bin_dir, TEST_IFACE, "500000")
    assert result.returncode == 0, result.stderr
    assert any("type can restart-ms 100" in c for c in ip_calls(tmp_path)), ip_calls(tmp_path)


def test_restart_ms_is_configurable(tmp_path):
    bin_dir = fake_ip_bin(tmp_path)
    run_setup_can(bin_dir, TEST_IFACE, "500000", env={"CAN_RESTART_MS": "250"})
    assert any("type can restart-ms 250" in c for c in ip_calls(tmp_path))


# An interface that already has automatic recovery armed. restart-ms is a persistent link
# property: nothing in this script, including `ip link set <iface> down`, clears it.
ALREADY_ARMED = """\
15: cantest9: <NOARP,UP,LOWER_UP,ECHO> mtu 16 qdisc pfifo_fast state UP qlen 10
    link/can
    can state ERROR-ACTIVE restart-ms 100
    bitrate 500000 sample-point 0.875
"""


def test_restart_ms_zero_explicitly_disables_recovery(tmp_path):
    # Regression, found in review of Task 3. The first implementation omitted the command
    # entirely for 0, reasoning that 0 meant "leave the kernel default". That conflates the
    # default on a freshly loaded driver with whatever the interface is currently set to.
    # On an interface previously configured with restart-ms 100, omitting the command
    # leaves recovery armed -- the opposite of what was asked for.
    bin_dir = fake_ip_bin(tmp_path, details=ALREADY_ARMED)
    result = run_setup_can(bin_dir, TEST_IFACE, "500000", env={"CAN_RESTART_MS": "0"})
    assert result.returncode == 0, result.stderr
    assert any("type can restart-ms 0" in c for c in ip_calls(tmp_path)), ip_calls(tmp_path)


def test_restart_ms_zero_disables_recovery_on_a_fresh_interface_too(tmp_path):
    # The script cannot know the prior state and must not have to. It always states the
    # value it wants, so the outcome does not depend on what ran before it.
    bin_dir = fake_ip_bin(tmp_path)
    run_setup_can(bin_dir, TEST_IFACE, "500000", env={"CAN_RESTART_MS": "0"})
    assert any("type can restart-ms 0" in c for c in ip_calls(tmp_path)), ip_calls(tmp_path)


def test_restart_ms_is_always_stated_whatever_the_value(tmp_path):
    # The property this fix establishes: after setup_can.sh, restart-ms is whatever the
    # operator asked for, never whatever a previous run happened to leave behind.
    for value, expected in (("0", "restart-ms 0"), ("100", "restart-ms 100"), ("500", "restart-ms 500")):
        run_dir = tmp_path / f"case{value}"
        run_dir.mkdir()
        bin_dir = fake_ip_bin(run_dir, details=ALREADY_ARMED)
        run_setup_can(bin_dir, TEST_IFACE, "500000", env={"CAN_RESTART_MS": value})
        assert any(f"type can {expected}" in c for c in ip_calls(run_dir)), (value, ip_calls(run_dir))


def test_a_non_numeric_restart_ms_is_refused(tmp_path):
    bin_dir = fake_ip_bin(tmp_path)
    result = run_setup_can(bin_dir, TEST_IFACE, "500000", env={"CAN_RESTART_MS": "soon"})
    assert result.returncode != 0
    assert "CAN_RESTART_MS" in result.stderr


# --- gap 2: statistics, which is what keeps gap 1 honest -------------------------------------


def test_statistics_are_printed_after_setup(tmp_path):
    # `ip -details link show` reports configuration but not error counters, and the
    # counters are the only thing that distinguishes "up" from "up on a working bus".
    bin_dir = fake_ip_bin(tmp_path)
    run_setup_can(bin_dir, TEST_IFACE, "500000")
    assert any(f"-details -statistics link show {TEST_IFACE}" in c for c in ip_calls(tmp_path)), ip_calls(
        tmp_path
    )


def test_the_link_is_brought_up_before_statistics_are_read(tmp_path):
    # Reading counters from a down interface would report a baseline that the operator
    # then mistakes for a healthy bus.
    bin_dir = fake_ip_bin(tmp_path)
    run_setup_can(bin_dir, TEST_IFACE, "500000")
    calls = ip_calls(tmp_path)
    up = next(i for i, c in enumerate(calls) if c == f"link set up {TEST_IFACE}")
    stats = next(i for i, c in enumerate(calls) if "-statistics" in c)
    assert up < stats, calls


def test_restart_ms_is_set_while_the_link_is_down(tmp_path):
    # Link parameters are configured on a stopped controller; the script takes the
    # interface down first and must not reorder that.
    bin_dir = fake_ip_bin(tmp_path)
    run_setup_can(bin_dir, TEST_IFACE, "500000")
    calls = ip_calls(tmp_path)
    down = next(i for i, c in enumerate(calls) if c == f"link set {TEST_IFACE} down")
    restart = next(i for i, c in enumerate(calls) if "restart-ms" in c)
    up = next(i for i, c in enumerate(calls) if c == f"link set up {TEST_IFACE}")
    assert down < restart < up, calls


@pytest.mark.parametrize("value", ["100", "250"])
def test_the_operator_is_told_what_restart_ms_was_set_to(tmp_path, value):
    # A bench operator reading the script's output needs to know recovery is armed,
    # because an interface that silently restarts looks identical to one that never
    # went bus-off.
    bin_dir = fake_ip_bin(tmp_path)
    result = run_setup_can(bin_dir, TEST_IFACE, "500000", env={"CAN_RESTART_MS": value})
    assert value in result.stdout + result.stderr
