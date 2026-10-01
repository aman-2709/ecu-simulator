#!/usr/bin/env bash
# The live GUI demo in a private user+network namespace: lo up, a private vcan0, the real
# simulator with ice_scenario.yaml and --api 127.0.0.1:8765, the read-only traffic
# generator, and headless Chrome taking screenshots of the page (scripts/gui_demo_capture.py).
# Nothing on the host changes: the host's vcan0 and can0 are never touched, and the page is
# reachable only inside the namespace, so this gives screenshots, not a page to browse.
#
#   scripts/run_gui_demo.sh <outdir>             the M3a set (ice_scenario.yaml)
#   scripts/run_gui_demo.sh --moving <outdir>    the moving-vehicle set
#                                                (docs/examples/ice_drive_cycle_stepped.yaml)
#   scripts/run_gui_demo.sh --m3b <outdir>       the M3b browser checks and cost (design §11.3, §12.2),
#                                                with the fault-injection server (scripts/gui_fault_server.py)
#   scripts/run_gui_demo.sh --m3b-long <outdir>  the M3b bounded-history case, about 11 min
#
# Every process is stopped on every path: the capture script stops what it started by
# exact PID, and the trap below then signals the capture script's whole process group
# (its own session, started with setsid, so the group id is its PID) on every exit, which
# also covers the capture script itself being killed with SIGKILL.
set -euo pipefail
MODE=""
if [[ $# -eq 2 && ( "$1" == "--moving" || "$1" == "--m3b" || "$1" == "--m3b-long" ) ]]; then
    MODE="$1"
    shift
fi
if [[ $# -ne 1 ]]; then
    echo "usage: $0 [--moving | --m3b | --m3b-long] <outdir>" >&2
    exit 2
fi
cd "$(dirname "$0")/.."
mkdir -p "$1"
OUTDIR="$(cd "$1" && pwd)"
PYTHON="${PYTHON:-.venv/bin/python}"
# The capture script refuses to run unless its network namespace differs from this one.
GUI_DEMO_HOST_NETNS="$(readlink /proc/self/ns/net)"
export GUI_DEMO_HOST_NETNS
exec unshare -r -n bash -euo pipefail -c '
    ip link set lo up
    ip link add dev vcan0 type vcan
    ip link set up vcan0
    capture=""
    cleanup() {
        trap - EXIT INT TERM
        # A process group outlives its leader: by now wait has usually reaped the capture
        # script, but if it died hard (SIGKILL, OOM, a crash past its finally) its children
        # are still in the group. So signal the group unconditionally; kill -0 on the group
        # is only the wait for it to empty.
        if [[ -n "$capture" ]]; then
            kill -TERM -- "-$capture" 2>/dev/null || true
            for _ in 1 2 3 4 5 6 7 8 9 10; do
                kill -0 -- "-$capture" 2>/dev/null || break
                "$0" -c "import time; time.sleep(0.5)"
            done
            kill -KILL -- "-$capture" 2>/dev/null || true
        fi
    }
    trap cleanup EXIT
    trap "exit 130" INT TERM
    setsid "$0" scripts/gui_demo_capture.py $2 "$1" &
    capture=$!
    wait "$capture"
' "$PYTHON" "$OUTDIR" "$MODE"
