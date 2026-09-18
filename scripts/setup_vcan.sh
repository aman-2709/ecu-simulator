#!/usr/bin/env bash
# Create and bring up a virtual CAN interface for the simulator and its tests.
#
# Usage: sudo scripts/setup_vcan.sh [interface]      (default: vcan0)
#
# Needs root or CAP_NET_ADMIN. The simulator itself runs unprivileged afterwards:
#   ecu-simulator --interface vcan0
# To tear down: sudo ip link delete vcan0
#
# The kernel ISO-TP module (can_isotp, in-tree since Linux 5.10) is loaded
# automatically when the simulator creates its first socket; nothing to insmod.
set -euo pipefail

IFACE="${1:-vcan0}"

die() { echo "setup_vcan.sh: $*" >&2; exit 1; }

command -v ip >/dev/null 2>&1 || die "'ip' (iproute2) not found"
[[ "$IFACE" =~ ^[A-Za-z0-9_.-]{1,15}$ ]] || die "invalid interface name: '$IFACE'"

if ! modprobe vcan 2>/dev/null; then
    [[ -d /sys/module/vcan ]] || die "cannot load the vcan module (are you root? is vcan built for this kernel?)"
fi

if ip link show "$IFACE" >/dev/null 2>&1; then
    echo "$IFACE already exists"
else
    ip link add dev "$IFACE" type vcan || die "cannot create $IFACE (are you root?)"
    echo "created $IFACE"
fi
ip link set up "$IFACE" || die "cannot bring $IFACE up"
ip -details link show "$IFACE"
