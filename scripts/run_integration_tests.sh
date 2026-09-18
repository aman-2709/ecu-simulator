#!/usr/bin/env bash
# Run the vcan integration tests without root.
#
# Creates a private user+network namespace (unshare -r -n), adds a vcan0 inside it
# and runs pytest there. Nothing on the host changes; the namespace disappears when
# pytest exits. Requires the kernel to auto-load the vcan and can_isotp modules
# (Ubuntu 24.04 does; GitHub-hosted Azure kernels lack can_isotp).
#
# Usage: scripts/run_integration_tests.sh [pytest args...]
#   scripts/run_integration_tests.sh                 # tests/integration
#   scripts/run_integration_tests.sh -k vin -vv
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"
if [[ -x .venv/bin/python && "$PYTHON" == "python" ]]; then
    PYTHON=.venv/bin/python
fi
command -v unshare >/dev/null || { echo "unshare (util-linux) not found" >&2; exit 1; }

exec unshare -r -n bash -euo pipefail -c '
    ip link add dev vcan0 type vcan
    ip link set up vcan0
    exec "$0" -m pytest tests/integration -m vcan "$@"
' "$PYTHON" "$@"
