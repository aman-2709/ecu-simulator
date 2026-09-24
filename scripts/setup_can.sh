#!/usr/bin/env bash
# Configure a physical SocketCAN interface (Classical CAN) for the simulator.
#
# Usage: sudo scripts/setup_can.sh <interface> [bitrate]     (default bitrate: 500000)
# Example: sudo scripts/setup_can.sh can0 500000
#
# Environment:
#   CAN_RESTART_MS  automatic bus-off recovery delay in milliseconds (default 100).
#                   0 explicitly disables recovery -- useful while troubleshooting,
#                   when you want a bus-off to stay visible.
#   CAN_TERMINATION ohms for the controller's switchable termination, e.g. 120, or 0
#                   to disable it. Unset by default, meaning termination is not touched.
#                   Ignored with a warning on controllers that do not support it.
#
# Needs root or CAP_NET_ADMIN. The simulator itself runs unprivileged afterwards:
#   ecu-simulator --interface can0
#
# Bitrate and automatic bus-off recovery are set here. CAN FD parameters are not
# configured by this script (CAN FD is a later phase). The kernel ISO-TP module
# loads on demand.
set -euo pipefail

IFACE="${1:-}"
BITRATE="${2:-500000}"

die() { echo "setup_can.sh: $*" >&2; exit 1; }

[[ -n "$IFACE" ]] || die "usage: setup_can.sh <interface> [bitrate]"
command -v ip >/dev/null 2>&1 || die "'ip' (iproute2) not found"
[[ "$IFACE" =~ ^[A-Za-z0-9_.-]{1,15}$ ]] || die "invalid interface name: '$IFACE'"
[[ "$BITRATE" =~ ^[0-9]+$ ]] || die "bitrate must be an integer in bit/s, got '$BITRATE'"

RESTART_MS="${CAN_RESTART_MS:-100}"
[[ "$RESTART_MS" =~ ^[0-9]+$ ]] || die "CAN_RESTART_MS must be an integer in ms, got '$RESTART_MS'"

if [[ -n "${CAN_TERMINATION:-}" ]]; then
    [[ "$CAN_TERMINATION" =~ ^[0-9]+$ ]] || die "CAN_TERMINATION must be an integer in ohms, got '$CAN_TERMINATION'"
fi

ip link show "$IFACE" >/dev/null 2>&1 || die "interface '$IFACE' does not exist (is the CAN adapter connected and its driver loaded?)"

ip link set "$IFACE" down
ip link set "$IFACE" type can bitrate "$BITRATE" || die "cannot set bitrate $BITRATE on $IFACE (not a CAN interface, or driver rejected it)"

# Without this a bus-off leaves the interface down until someone notices, and it presents
# as "the simulator stopped responding" rather than as a bus fault. What keeps automatic
# recovery honest is the `re-started` counter printed below: restarting on a loop through a
# persistent fault would otherwise make a broken bus look healthy.
#
# The value is always stated, including 0. restart-ms is a persistent link property and
# nothing here clears it -- not `ip link set <iface> down`, not setting the bitrate -- so
# omitting the command for 0 would leave whatever a previous run had armed. An operator who
# asks for recovery off while troubleshooting must get it off, not inherit 100 ms from the
# last time somebody ran this script.
ip link set "$IFACE" type can restart-ms "$RESTART_MS" \
    || die "cannot set restart-ms $RESTART_MS on $IFACE"
if [[ "$RESTART_MS" != "0" ]]; then
    echo "setup_can.sh: automatic bus-off recovery armed, restart-ms $RESTART_MS (check the 're-started' counter below)" >&2
else
    echo "setup_can.sh: automatic bus-off recovery explicitly disabled (CAN_RESTART_MS=0); a bus-off will leave $IFACE down until you run 'ip link set $IFACE type can restart'" >&2
fi

# Optional, and guarded. A controller without switchable termination rejects this command,
# and under `set -euo pipefail` that would abort a run which had otherwise succeeded. The
# kernel documentation shows the available values appearing in `ip -details link show` as
# e.g. `termination 120 [ 0, 120 ]` when the controller has them, so ask before setting.
#
# Off unless requested: an adapter and a dongle that each carry a built-in 120 ohm are
# already correctly terminated, and silently adding a third would create the fault this is
# meant to prevent.
if [[ -n "${CAN_TERMINATION:-}" ]]; then
    if ip -details link show "$IFACE" 2>/dev/null | grep -q 'termination '; then
        ip link set "$IFACE" type can termination "$CAN_TERMINATION" \
            || die "cannot set termination $CAN_TERMINATION on $IFACE"
        echo "setup_can.sh: controller termination set to ${CAN_TERMINATION} ohm" >&2
    else
        echo "setup_can.sh: $IFACE does not support switchable termination; terminate the harness physically instead (${CAN_TERMINATION} ohm at each end of the differential pair, two in total). Continuing." >&2
    fi
fi

ip link set up "$IFACE" || die "cannot bring $IFACE up"

# -statistics, not plain -details: `ip link set up` succeeds on an adapter attached to
# nothing, so configuration alone cannot distinguish "the link is up" from "the link is up
# on a working bus". The controller state and the re-started/bus-errors/arbit-lost/
# error-warn/error-pass/bus-off counters can.
ip -details -statistics link show "$IFACE"
