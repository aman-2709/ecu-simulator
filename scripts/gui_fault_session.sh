#!/usr/bin/env bash
# The owner's two fault checks of design §13 (gui-m3b-graphs-design.md), run by hand: the
# fault-injection test server (scripts/gui_fault_server.py) and a VISIBLE Chrome, both inside a
# private user+network namespace with its own lo and its own vcan0, on the owner's X display.
#
#   scripts/gui_fault_session.sh nonfinite      [options]   "invalid value", a gap, still Live
#   scripts/gui_fault_session.sh state-fault    [options]   "Last known", then Live again
#
# What it touches, and what it does not:
# - It creates one temp dir (mktemp -d) holding Chrome's fresh profile, Chrome's HOME and
#   TMPDIR, and the logs; it removes that dir, and only that dir, on exit.
# - It starts the fault server, the read-only traffic script (state-fault only), a filtered
#   view of the server's log, and Chrome, each in its own session (setsid), and on exit stops
#   exactly those process groups. Never pkill.
# - The host's network, its vcan0 and can0, the owner's own simulator and port, and every
#   other Chrome and profile are never touched: the server and Chrome run in the private
#   network namespace, and the page is the namespace's own loopback.
# - Chrome keeps its sandbox (no --no-sandbox, no --disable-setuid-sandbox). X access control
#   is unchanged (no xhost); DISPLAY and XAUTHORITY are passed through as they are. Chrome runs
#   in a nested user namespace mapped back to the owner's uid, because Chrome will not run as
#   uid 0, which is what `unshare -r` makes the launcher inside. If X or the sandbox fails from
#   the namespace, the launcher stops and prints the error; it never weakens either.
set -euo pipefail

usage() {
    cat <<'EOF'
usage: scripts/gui_fault_session.sh nonfinite|state-fault [options]

  nonfinite      the fault server's --nonfinite SIGNAL:START:END: SIGNAL is nan in the
                 window. No traffic (a 01 05 read while coolant is nan would reach the
                 OBD encoder, DEV-26).
  state-fault    the fault server's --state-fault START:END[:PART]: the snapshot raises in
                 the window. The read-only traffic script runs, so the log keeps moving.

options:
  --window START:END   the fault window, in scenario seconds from the server's start
                       (repeatable; default 40:60 for nonfinite, 40:50 for state-fault)
  --signal PATH        nonfinite: the signal set to nan (default engine.coolant_temp)
  --part vehicle|dtcs  state-fault: fail only that part instead of the full snapshot
  --profile PATH       default docs/examples/ice_drive_cycle_stepped.yaml (the stepped demo)
  --port N             the page's port on the namespace's loopback (default 8080)
  --no-traffic         state-fault: do not start the traffic script
  --devtools-port N    also open Chrome's DevTools port, bound to the namespace's loopback
                       only (nothing on the host can reach it); off by default
  --duration S         stop by itself after S seconds (default: run until the Chrome
                       window is closed or Ctrl-C)
  --keep-logs DIR      copy the logs to DIR before the temp dir is removed
  -h, --help           this text

Stop it by closing the Chrome window or pressing Ctrl-C in this terminal.
EOF
}

CALLER_PWD="$(pwd)"
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
SELF="$ROOT/scripts/gui_fault_session.sh"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
pysleep() { "$PY" -c "import signal, time; signal.signal(signal.SIGINT, signal.SIG_DFL); time.sleep($1)"; }

# ---------------------------------------------------------------- inside the namespace
if [[ "${1:-}" == "--inside" ]]; then
    here="$(readlink /proc/self/ns/net)"
    if [[ -z "${GFS_HOST_NETNS:-}" || "$GFS_HOST_NETNS" == "$here" ]]; then
        echo "gui_fault_session.sh --inside runs only in its own private network namespace" \
             "(host ${GFS_HOST_NETNS:-unknown}, here $here); refusing" >&2
        exit 1
    fi
    WORK="$GFS_WORK"
    echo "$here" > "$WORK/netns"     # the host side's proof that a listed group is still this launcher's
    groups=()                   # process group ids this launcher created, in start order
    chrome="" server=""

    cleanup() {
        local rc=$? i pg alive
        trap - EXIT INT TERM HUP
        echo
        echo "== stopping what this launcher started"
        # Last started first: Chrome, the log view, the traffic, then the server. Each was
        # started with setsid, so its group id is its PID and the group holds its children.
        for (( i=${#groups[@]}-1; i>=0; i-- )); do
            kill -TERM -- "-${groups[i]}" 2>/dev/null || true
        done
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            alive=0
            for pg in "${groups[@]}"; do
                kill -0 -- "-$pg" 2>/dev/null && alive=1
            done
            (( alive )) || break
            pysleep 0.5
        done
        for pg in "${groups[@]}"; do
            if kill -0 -- "-$pg" 2>/dev/null; then
                echo "   group $pg still running after SIGTERM: SIGKILL"
                kill -KILL -- "-$pg" 2>/dev/null || true
            fi
        done
        for pg in "${groups[@]}"; do
            if kill -0 -- "-$pg" 2>/dev/null; then
                echo "   group $pg: STILL RUNNING"
            else
                echo "   group $pg: gone"
            fi
        done
        exit "$rc"
    }
    trap cleanup EXIT
    trap "exit 130" INT TERM HUP

    started() {     # record a group at once, so a failure right after still stops it
        groups+=("$1")
        echo "$1" >> "$WORK/groups"
    }

    blocker() {
        echo
        echo "BLOCKER: $1"
        [[ -n "${2:-}" && -s "$2" ]] && { echo "--- $2 (last 40 lines)"; tail -n 40 "$2"; echo "---"; }
        echo "Nothing was weakened to work around it (no --no-sandbox, no xhost). Stopping."
        exit 3
    }

    ip link set lo up
    ip link add dev vcan0 type vcan
    ip link set up vcan0

    url="http://127.0.0.1:$GFS_PORT/"
    read -r -a fault_args <<< "$GFS_FAULT_ARGS"
    echo "== fault server: --profile $GFS_PROFILE ${fault_args[*]} --api 127.0.0.1:$GFS_PORT (interface: the namespace's vcan0)"
    GUI_DEMO_HOST_NETNS="$GFS_HOST_NETNS" setsid "$PY" scripts/gui_fault_server.py --profile "$GFS_PROFILE" \
        --interface vcan0 --api "127.0.0.1:$GFS_PORT" "${fault_args[@]}" > "$WORK/fault-server.log" 2>&1 &
    server=$!
    started "$server"
    if ! "$PY" - "$server" "$url" <<'PYEOF'
import os, sys, time, urllib.request
pid, url = int(sys.argv[1]), sys.argv[2]
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        sys.exit(1)
    try:
        with urllib.request.urlopen(url + "api/v1/status", timeout=1) as resp:
            if resp.status == 200:
                sys.exit(0)
    except OSError:
        time.sleep(0.2)
sys.exit(1)
PYEOF
    then
        blocker "the fault server did not come up on $url" "$WORK/fault-server.log"
    fi
    origin="$(sed -n 's/.*wall origin \([0-9]*\)\.[0-9]*.*/\1/p' "$WORK/fault-server.log" | head -n 1)"
    if [[ -n "$origin" ]]; then
        echo "   up on $url (pid $server); scenario t = 0 at $(date -d "@$origin" +%T)"
        for w in $GFS_WINDOWS; do
            echo "   fault window [${w%%:*}, ${w#*:}) s: about $(date -d "@$(( origin + ${w%%:*} ))" +%T)" \
                 "to $(date -d "@$(( origin + ${w#*:} ))" +%T)"
        done
    else
        echo "   up on $url (pid $server). The server's \"wall origin\" line was not found in its log, so no"
        echo "   wall-clock times: the fault windows ($GFS_WINDOWS) are scenario seconds from the server's start;"
        echo "   watch for the [server] \"opens\" / \"set to nan\" lines below."
    fi

    # The server's own fault lines ("... opens", "... set to nan ...") as they happen.
    # shellcheck disable=SC2016  # $1 is expanded by the inner bash
    setsid bash -c 'tail -n +1 -F -- "$1" 2>/dev/null | grep --line-buffered -F "fault server:" | sed -u "s/^/   [server] /"' \
        _ "$WORK/fault-server.log" &
    started "$!"

    if [[ "$GFS_TRAFFIC" == 1 ]]; then
        setsid "$PY" scripts/gui_demo_traffic.py --interface vcan0 --rate 4 > "$WORK/traffic.log" 2>&1 &
        started "$!"
        echo "== traffic: scripts/gui_demo_traffic.py --interface vcan0 --rate 4 (pid $!, read-only, namespace's vcan0)"
    fi

    # X from inside the namespace: the abstract X socket belongs to the host's network
    # namespace, so Xlib falls back to the filesystem socket /tmp/.X11-unix/X<n>.
    nested=(unshare --map-user="$GFS_UID" --map-group="$GFS_GID" --)
    if command -v xset > /dev/null; then
        if ! "${nested[@]}" xset q > /dev/null 2> "$WORK/x-check.log"; then
            blocker "X display $DISPLAY is not reachable from the namespace (xset q failed)" "$WORK/x-check.log"
        fi
        echo "== X: xset q on DISPLAY=$DISPLAY from the namespace: ok (XAUTHORITY=${XAUTHORITY:-unset}, unchanged)"
    else
        echo "== X: xset not installed, no pre-check; Chrome's own errors are checked below"
    fi

    mkdir -p "$WORK/chrome-profile" "$WORK/home" "$WORK/tmp"
    # shellcheck disable=SC2054  # the comma is part of --window-size
    chrome_args=(--user-data-dir="$WORK/chrome-profile" --no-first-run --no-default-browser-check
                 --password-store=basic --window-size=1440,900)
    [[ -n "$GFS_DEVTOOLS" ]] && chrome_args+=(--remote-debugging-port="$GFS_DEVTOOLS")
    echo "== chrome: $GFS_CHROME ${chrome_args[*]} --new-window $url"
    # setsid, env and unshare each exec, so $! is Chrome's browser process itself.
    setsid env HOME="$WORK/home" TMPDIR="$WORK/tmp" XDG_CONFIG_HOME="$WORK/home/.config" \
        XDG_CACHE_HOME="$WORK/home/.cache" XDG_DATA_HOME="$WORK/home/.local/share" \
        "${nested[@]}" "$GFS_CHROME" "${chrome_args[@]}" --new-window "$url" > "$WORK/chrome.log" 2>&1 &
    chrome=$!
    started "$chrome"

    # Wait for the page's connection: an established TCP connection to the server's port.
    conns=""
    for _ in $(seq 1 40); do
        kill -0 "$chrome" 2>/dev/null || blocker "Chrome exited at start (pid $chrome)" "$WORK/chrome.log"
        conns="$(ss -Htnp state established "( dport = :$GFS_PORT )" 2>/dev/null || true)"
        [[ -n "$conns" ]] && break
        pysleep 0.5
    done
    if grep -E -i "sandbox|Missing X server|cannot open display|Authorization required" "$WORK/chrome.log" \
            > "$WORK/chrome-errors.log" 2>/dev/null; then
        blocker "Chrome reported sandbox or X errors" "$WORK/chrome-errors.log"
    fi
    [[ -n "$conns" ]] || blocker "Chrome did not connect to $url within 20 s" "$WORK/chrome.log"

    # ---- the proof that the browser runs in the namespace
    chrome_net="$(readlink "/proc/$chrome/ns/net")"
    server_net="$(readlink "/proc/$server/ns/net")"
    echo
    echo "== in-namespace check"
    echo "   host netns:      $GFS_HOST_NETNS  ($GFS_HOST_SOURCE)"
    echo "   launcher netns:  $here  (this launcher, inside unshare -r -n)"
    echo "   fault server:    pid $server, $server_net"
    # uid_map "inside outside count": Chrome's uid, and the launcher's (root here, the owner's uid on the host).
    echo "   chrome browser:  pid $chrome, $chrome_net, user namespace $(readlink "/proc/$chrome/ns/user")" \
         "(uid_map $(tr -s ' ' < "/proc/$chrome/uid_map" | sed 's/^ //'); host uid $GFS_UID)"
    if [[ "$chrome_net" == "$GFS_HOST_NETNS" || "$server_net" == "$GFS_HOST_NETNS" || "$chrome_net" != "$here" ]]; then
        blocker "the network namespace check failed (chrome $chrome_net, server $server_net, host $GFS_HOST_NETNS)"
    fi
    echo "   RESULT: Chrome and the fault server share the namespace's netns, which differs from the host's"
    echo "   page URL: $url (the namespace's own loopback)"
    echo "   listening in the namespace:"
    ss -Htlnp "( sport = :$GFS_PORT )" | sed 's/^/     /'
    echo "   the page's connections to it, in the namespace:"
    while read -r line; do echo "     $line"; done <<< "$conns"
    if [[ -n "$GFS_DEVTOOLS" ]]; then
        echo "   DevTools: 127.0.0.1:$GFS_DEVTOOLS in the namespace; pages:"
        "$PY" -c "import json, urllib.request; [print('     ', p['type'], p['url']) for p in
                  json.load(urllib.request.urlopen('http://127.0.0.1:$GFS_DEVTOOLS/json/list', timeout=2))]" || true
    fi

    # ---- the sandbox, as running: no flag that disables it; renderers seccomp-filtered in
    # their own user and PID namespaces (Chrome's namespace sandbox)
    echo
    echo "== sandbox check"
    if tr '\0' '\n' < "/proc/$chrome/cmdline" | grep -E -q -- "^--(no-sandbox|disable-setuid-sandbox)"; then
        blocker "Chrome's command line disables the sandbox"
    fi
    echo "   chrome's command line: no --no-sandbox, no --disable-setuid-sandbox"
    found=0
    for pid in $(ps -e -o pid=,pgid= | awk -v g="$chrome" '$2 == g {print $1}'); do
        cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
        [[ "$cmd" == *--type=renderer* ]] || continue
        found=1
        echo "   renderer pid $pid: $(grep '^Seccomp:' "/proc/$pid/status" | tr -s '\t ' ' ')," \
             "user ns $(readlink "/proc/$pid/ns/user"), pid ns $(readlink "/proc/$pid/ns/pid")" \
             "(browser: $(readlink "/proc/$chrome/ns/pid"))"
    done
    (( found )) || echo "   no renderer seen yet in Chrome's process group"

    echo
    echo "== running. Watch the page; close the Chrome window or press Ctrl-C here to stop."
    start=$SECONDS
    while kill -0 "$chrome" 2>/dev/null; do
        if ! kill -0 "$server" 2>/dev/null; then
            echo "the fault server exited; see its log"
            tail -n 20 "$WORK/fault-server.log"
            exit 1
        fi
        if [[ -n "$GFS_DURATION" ]] && (( SECONDS - start >= GFS_DURATION )); then
            echo "== --duration $GFS_DURATION s reached"
            break
        fi
        pysleep 1
    done
    kill -0 "$chrome" 2>/dev/null || echo "== the Chrome window was closed"
    exit 0
fi

# ---------------------------------------------------------------- on the host
MODE="" PROFILE="docs/examples/ice_drive_cycle_stepped.yaml" SIGNAL="engine.coolant_temp" PART="" PORT=8080
TRAFFIC="" DEVTOOLS="" DURATION="" KEEP="" WINDOWS=()
die() { echo "gui_fault_session.sh: $*" >&2; exit 2; }
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        nonfinite|state-fault) [[ -z "$MODE" ]] || die "one mode only"; MODE="$1"; shift ;;
        --window) [[ $# -ge 2 ]] || die "--window needs START:END"; WINDOWS+=("$2"); shift 2 ;;
        --signal) [[ $# -ge 2 && "$2" =~ ^[a-z0-9_]+(\.[a-z0-9_]+)+$ ]] || die "--signal needs a signal path like engine.coolant_temp"
                  SIGNAL="$2"; shift 2 ;;
        --part) [[ $# -ge 2 && ( "$2" == vehicle || "$2" == dtcs ) ]] || die "--part takes vehicle or dtcs"
                PART="$2"; shift 2 ;;
        --profile) [[ $# -ge 2 ]] || die "--profile needs a PATH"; PROFILE="$2"; shift 2 ;;
        --port) [[ $# -ge 2 && "$2" =~ ^[1-9][0-9]{0,4}$ && "$2" -le 65535 ]] || die "--port takes 1-65535"
                PORT="$2"; shift 2 ;;
        --no-traffic) TRAFFIC=0; shift ;;
        --devtools-port) [[ $# -ge 2 && "$2" =~ ^[0-9]+$ ]] || die "--devtools-port needs a number"
                         DEVTOOLS="$2"; shift 2 ;;
        --duration) [[ $# -ge 2 && "$2" =~ ^[0-9]+$ ]] || die "--duration needs whole seconds"
                    DURATION="$2"; shift 2 ;;
        --keep-logs) [[ $# -ge 2 && -n "$2" ]] || die "--keep-logs needs a DIR"
                     KEEP="$2"; [[ "$KEEP" == /* ]] || KEEP="$CALLER_PWD/$KEEP"; shift 2 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$MODE" ]] || { usage >&2; exit 2; }
[[ "$MODE" == nonfinite && -n "$PART" ]] && die "--part is for state-fault"
[[ "$MODE" == state-fault && "$SIGNAL" != "engine.coolant_temp" ]] && die "--signal is for nonfinite"
[[ "$MODE" == nonfinite && "$TRAFFIC" == 0 ]] && die "nonfinite never runs traffic; --no-traffic is for state-fault"
[[ ${#WINDOWS[@]} -gt 0 ]] || { [[ "$MODE" == nonfinite ]] && WINDOWS=(40:60) || WINDOWS=(40:50); }
for w in "${WINDOWS[@]}"; do
    [[ "$w" =~ ^[0-9]+:[0-9]+$ ]] || die "--window takes whole seconds START:END, got $w"
done
[[ "$(id -u)" != 0 ]] || die "run it as the desktop user, not root"
[[ -n "${DISPLAY:-}" ]] || die "DISPLAY is not set: run it from the desktop session's terminal"
[[ -f "$PROFILE" ]] || die "no profile $PROFILE"
[[ -x "$PY" ]] || die "no Python at $PY (set PYTHON=)"
CHROME_BIN=""
for name in "${CHROME:-}" google-chrome google-chrome-stable chromium chromium-browser; do
    if [[ -n "$name" ]] && CHROME_BIN="$(command -v "$name")"; then
        break
    fi
    CHROME_BIN=""
done
[[ -n "$CHROME_BIN" ]] || die "no Chrome or Chromium found; set CHROME=/path/to/chrome"
if [[ "$MODE" == nonfinite ]] && ! "$PY" - "$PROFILE" "$SIGNAL" <<'PYEOF'
import sys
from ecu_simulator import app
from ecu_simulator.config import load_profile
signals = app.build_vehicle(app.RuntimeConfig.build(load_profile(sys.argv[1]), "vcan0")).signals
sys.exit(0 if sys.argv[2] in signals else 1)
PYEOF
then
    die "--signal $SIGNAL is not a signal of the vehicle in $PROFILE"
fi
for tool in unshare ip ss setsid env; do
    command -v "$tool" > /dev/null || die "$tool is needed"
done

FAULT_ARGS=()
for w in "${WINDOWS[@]}"; do
    if [[ "$MODE" == nonfinite ]]; then
        FAULT_ARGS+=(--nonfinite "$SIGNAL:$w")
    else
        FAULT_ARGS+=(--state-fault "$w${PART:+:$PART}")
    fi
done
[[ "$MODE" == state-fault && "$TRAFFIC" != 0 ]] && TRAFFIC=1 || TRAFFIC=0

# The host's network namespace, read here before unshare. PID 1's is the reference when it is
# readable (it usually is not for a desktop user); this shell's otherwise.
HOST_NETNS="$(readlink /proc/self/ns/net)"
if init_net="$(readlink /proc/1/ns/net 2>/dev/null)"; then
    HOST_SOURCE="this launcher's on the host before unshare; /proc/1/ns/net is $init_net"
    [[ "$init_net" == "$HOST_NETNS" ]] || die "this shell is not in PID 1's network namespace ($HOST_NETNS vs $init_net)"
else
    HOST_SOURCE="read by this launcher on the host before unshare; /proc/1/ns/net is not readable by this user"
    # Without PID 1 to compare with, at least refuse an already isolated shell: the host's user
    # namespace has the identity map "0 0 4294967295"; a user namespace (unshare -r, a sandbox) does not.
    uid_map="$(tr -s ' ' < /proc/self/uid_map | sed 's/^ //')"
    [[ "$uid_map" == "0 0 4294967295" ]] || die "this shell is already inside a user namespace" \
        "(/proc/self/uid_map is \"$uid_map\", not the host's \"0 0 4294967295\"); run it from a desktop terminal"
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/gui-fault-session.XXXXXX")"
host_cleanup() {
    local rc=$? pg
    trap - EXIT
    # A safety net for a namespace shell killed before its own cleanup: its groups are listed.
    # A listed group is signalled only while one of its members is still in the launcher's private
    # network namespace. A group id reused by anything else meanwhile has no member there (a group
    # id is not reused while any member of the old group lives), so it is left alone.
    if [[ -f "$WORK/groups" && -f "$WORK/netns" ]]; then
        private="$(cat "$WORK/netns")"
        while read -r pg; do
            ours=0
            for pid in $(ps -e -o pid=,pgid= | awk -v g="$pg" '$2 == g {print $1}'); do
                [[ "$(readlink "/proc/$pid/ns/net" 2>/dev/null)" == "$private" ]] && { ours=1; break; }
            done
            if (( ours )); then
                echo "   group $pg outlived the namespace shell (still in $private): SIGKILL"
                kill -KILL -- "-$pg" 2>/dev/null || true
            elif kill -0 -- "-$pg" 2>/dev/null; then
                echo "   group id $pg exists but has no member in $private: not this launcher's, left alone"
            fi
        done < "$WORK/groups"
    fi
    if [[ -n "$KEEP" ]]; then
        mkdir -p "$KEEP"
        cp -- "$WORK"/*.log "$KEEP"/ 2>/dev/null || true
        echo "== logs copied to $KEEP"
    fi
    rm -rf -- "$WORK"
    if [[ -e "$WORK" ]]; then
        echo "== temp dir $WORK could not be removed"
    else
        echo "== removed its temp dir $WORK"
    fi
    exit "$rc"
}
trap host_cleanup EXIT
inner=""
# Ctrl-C and a closed terminal reach the namespace shell directly (same process group); a
# `kill <this pid>` does not, so SIGTERM and SIGHUP are forwarded. Either way the namespace shell
# stops its processes and exits, and this shell then cleans up.
trap ':' INT
trap '[[ -n "$inner" ]] && kill -TERM "$inner" 2>/dev/null || true' TERM HUP

echo "== gui_fault_session.sh $MODE: temp dir $WORK"
echo "   host netns $HOST_NETNS ($HOST_SOURCE)"
set +e
# In the background so the traps above run while it lives; env --default-signal undoes the
# SIGINT/SIGQUIT ignore a background job gets, so Ctrl-C still reaches the namespace shell.
GFS_HOST_NETNS="$HOST_NETNS" GFS_HOST_SOURCE="$HOST_SOURCE" GFS_WORK="$WORK" GFS_PROFILE="$PROFILE" \
GFS_FAULT_ARGS="${FAULT_ARGS[*]}" GFS_WINDOWS="${WINDOWS[*]}" GFS_PORT="$PORT" GFS_TRAFFIC="$TRAFFIC" \
GFS_DEVTOOLS="$DEVTOOLS" GFS_DURATION="$DURATION" GFS_CHROME="$CHROME_BIN" GFS_UID="$(id -u)" GFS_GID="$(id -g)" \
    env --default-signal=INT,QUIT unshare -r -n -- "$SELF" --inside &
inner=$!
while :; do
    wait "$inner"
    rc=$?
    kill -0 "$inner" 2>/dev/null || break      # wait was interrupted by a trap: wait again
done
set -e
exit "$rc"
