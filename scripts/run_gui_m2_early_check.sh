#!/usr/bin/env bash
# The GUI M2 early check (decisions/0010 §9.2) in a private user+network namespace:
# lo up, a private vcan0, candump, the simulator and the tester. Nothing on the host changes.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
exec unshare -r -n bash -euo pipefail -c '
    ip link set lo up
    ip link add dev vcan0 type vcan
    ip link set up vcan0
    exec "$0" scripts/gui_m2_early_check.py "$@"
' "$PYTHON" "$@"
