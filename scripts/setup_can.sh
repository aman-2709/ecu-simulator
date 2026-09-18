#!/usr/bin/env bash
# Configure a physical SocketCAN interface (Classical CAN) for the simulator.
#
# Usage: sudo scripts/setup_can.sh <interface> [bitrate]     (default bitrate: 500000)
# Example: sudo scripts/setup_can.sh can0 500000
#
# Needs root or CAP_NET_ADMIN. The simulator itself runs unprivileged afterwards:
#   ecu-simulator --interface can0
#
# Only the bitrate is set here. CAN FD parameters are not configured by this
# script (CAN FD is a later phase). The kernel ISO-TP module loads on demand.
set -euo pipefail

IFACE="${1:-}"
BITRATE="${2:-500000}"

die() { echo "setup_can.sh: $*" >&2; exit 1; }

[[ -n "$IFACE" ]] || die "usage: setup_can.sh <interface> [bitrate]"
command -v ip >/dev/null 2>&1 || die "'ip' (iproute2) not found"
[[ "$IFACE" =~ ^[A-Za-z0-9_.-]{1,15}$ ]] || die "invalid interface name: '$IFACE'"
[[ "$BITRATE" =~ ^[0-9]+$ ]] || die "bitrate must be an integer in bit/s, got '$BITRATE'"

ip link show "$IFACE" >/dev/null 2>&1 || die "interface '$IFACE' does not exist (is the CAN adapter connected and its driver loaded?)"

ip link set "$IFACE" down
ip link set "$IFACE" type can bitrate "$BITRATE" || die "cannot set bitrate $BITRATE on $IFACE (not a CAN interface, or driver rejected it)"
ip link set up "$IFACE" || die "cannot bring $IFACE up"
ip -details link show "$IFACE"
