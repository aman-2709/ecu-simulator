#!/usr/bin/env bash
# Run the Phase 7 scenario acceptance in a private network namespace, without root.
#
# Same trick as scripts/run_integration_tests.sh: unshare -r -n gives this run its own
# vcan0, so it cannot collide with a simulator already using the host's interface and it
# leaves nothing behind. The run takes a little over two minutes of real time, because it
# drives a real scenario through a real kernel ISO-TP socket.
#
# Usage: scripts/acceptance/run_phase7_acceptance.sh [extra args for the python script]
#   scripts/acceptance/run_phase7_acceptance.sh --trace /tmp/phase7.log --json /tmp/phase7.json
set -euo pipefail

cd "$(dirname "$0")/../.."
PYTHON="${PYTHON:-python}"
if [[ -x .venv/bin/python && "$PYTHON" == "python" ]]; then
    PYTHON=.venv/bin/python
fi
command -v unshare >/dev/null || { echo "unshare (util-linux) not found" >&2; exit 1; }

exec unshare -r -n bash -euo pipefail -c '
    ip link add dev vcan0 type vcan
    ip link set up vcan0
    exec "$0" scripts/acceptance/phase7_scenario_acceptance.py --interface vcan0 "$@"
' "$PYTHON" "$@"
