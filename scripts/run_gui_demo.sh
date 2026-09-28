#!/usr/bin/env bash
# The live GUI demo in a private user+network namespace: lo up, a private vcan0, the real
# simulator with ice_scenario.yaml and --api 127.0.0.1:8765, the read-only traffic
# generator, and headless Chrome taking screenshots of the page (scripts/gui_demo_capture.py).
# Nothing on the host changes: the host's vcan0 and can0 are never touched, and the page is
# reachable only inside the namespace, so this gives screenshots, not a page to browse.
#
#   scripts/run_gui_demo.sh <outdir>
#
# Every process is stopped on every path: the capture script stops what it started by
# exact PID, and the trap below stops the capture script's whole process group (its own
# session, started with setsid) if anything is left.
set -euo pipefail
if [[ $# -ne 1 ]]; then
    echo "usage: $0 <outdir>" >&2
    exit 2
fi
cd "$(dirname "$0")/.."
mkdir -p "$1"
OUTDIR="$(cd "$1" && pwd)"
PYTHON="${PYTHON:-.venv/bin/python}"
exec unshare -r -n bash -euo pipefail -c '
    ip link set lo up
    ip link add dev vcan0 type vcan
    ip link set up vcan0
    capture=""
    cleanup() {
        trap - EXIT INT TERM
        if [[ -n "$capture" ]] && kill -0 "$capture" 2>/dev/null; then
            kill -TERM -- "-$capture" 2>/dev/null || true
            for _ in 1 2 3 4 5 6 7 8 9 10; do
                kill -0 "$capture" 2>/dev/null || break
                "$0" -c "import time; time.sleep(0.5)"
            done
            kill -KILL -- "-$capture" 2>/dev/null || true
        fi
    }
    trap cleanup EXIT
    trap "exit 130" INT TERM
    setsid "$0" scripts/gui_demo_capture.py "$1" &
    capture=$!
    wait "$capture"
' "$PYTHON" "$OUTDIR"
