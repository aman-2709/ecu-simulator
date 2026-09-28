#!/bin/bash
# usage: shots.sh <mockup dir>
set -e
D="$1"; U="file://$D/index.html"; O="$D/screenshots"
P="$(mktemp -d)"  # throwaway Chrome profile
shot() { google-chrome --headless=new --disable-gpu --no-first-run --hide-scrollbars --user-data-dir=$P --virtual-time-budget=2000 --screenshot="$O/$1" --window-size="$2" "$U$3" 2>/dev/null; }
shot desktop-1440x1000.png 1440,1000 ""
shot desktop-1440x1000-log-tail.png 1440,1000 "#tail"
shot desktop-1440-filtered-paused.png 1440,1000 "#outcome=no_response,unrouted,error&paused"
shot desktop-1440-service-0x19.png 1440,1000 "#service=19"
shot desktop-1440-cleared.png 1440,1000 "#cleared"
shot narrow-390-full.png 390,6200 ""
shot narrow-390-first-screen.png 390,844 ""
