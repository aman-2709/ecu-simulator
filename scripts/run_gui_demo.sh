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
#   scripts/run_gui_demo.sh --m3b-slots <outdir> the M3b four-client variants A, B and C alone
#   scripts/run_gui_demo.sh --m3b-perf <outdir>  the main-thread comparison runs with traces, about 22 min
#   scripts/run_gui_demo.sh --m3b-perf-log <outdir>  the log fix's before / after runs, about 7 min
#                                                (PYTHONPATH=<a tree>/src serves that tree's page;
#                                                GUI_PERF_DIAG=lamp-off|flash-off|both-off is a labelled
#                                                diagnostic with that animation off by a harness stylesheet;
#                                                GUI_PERF_IDLE=1 adds a no-traffic run per viewport;
#                                                GUI_PERF_REPEATS=N following runs per viewport (2);
#                                                GUI_PERF_STATES=1 runs paused / pinned / graphs-hidden
#                                                instead, with the log's mutations counted;
#                                                GUI_PERF_VISIBLE=1 uses a visible Chrome on $DISPLAY,
#                                                GPU and sandbox on, in a nested user namespace)
#   scripts/run_gui_demo.sh --m3b-log <outdir>   the windowed exchange log's cases at a full buffer,
#                                                1440 x 900 and 390 x 844
#
# Every process is stopped on every path: the capture script stops what it started by
# exact PID, and the trap below then signals the capture script's whole process group
# (its own session, started with setsid, so the group id is its PID) on every exit, which
# also covers the capture script itself being killed with SIGKILL.
set -euo pipefail
MODE=""
if [[ $# -eq 2 && ( "$1" == "--moving" || "$1" == "--m3b" || "$1" == "--m3b-long" || "$1" == "--m3b-slots" || "$1" == "--m3b-perf" || "$1" == "--m3b-perf-log" || "$1" == "--m3b-log" ) ]]; then
    MODE="$1"
    shift
fi
if [[ $# -ne 1 ]]; then
    echo "usage: $0 [--moving | --m3b | --m3b-long | --m3b-slots | --m3b-perf | --m3b-perf-log | --m3b-log] <outdir>" >&2
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
