#!/bin/bash
# Phase 3, first active panel write: replay U-Boot's exact
# Developer Mode boot sequence (zero frame, 80 x code 6, 80 x cumulus_frame-1, 2 x zero, pipe off).
#   dry   (default) = read-only checks on the tablet; xochitl keeps running.
#   write = stops xochitl, replays, holds HOLD s (screen keeps the image; pipe off), restarts xochitl.
# The write runs detached on the tablet (setsid), so an ssh drop cannot leave xochitl stopped.
# Usage: HOST=TABLET_IP SPLASH_FRAME=/path/to/cumulus_frame-1.raw bash run-replay.sh [dry|write] [HOLD_SECONDS=15]
set -euo pipefail
HOST=${HOST:?set HOST to the tablet address, e.g. HOST=TABLET_IP}; M=${1:-dry}; HOLD=${2:-15}; TAG=$(date +%H%M%S)
D=$(cd "$(dirname "$0")" && pwd); R=/home/root/display-re; LOG=$R/replay-$M-$TAG.log
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10 root@$HOST"
scp -o BatchMode=yes -o ConnectTimeout=10 -q "$D/tablet/replay_boot.py" "$D/tablet/replay.sh" "${SPLASH_FRAME:?set SPLASH_FRAME to cumulus_frame-1.raw (not distributed, see docs/method.md)}" root@$HOST:$R/
if [ "$M" = write ]; then
  $SSH "cd $R && setsid sh replay.sh write $HOLD $LOG </dev/null >/dev/null 2>&1 &"
  $SSH "i=0; while ! grep -q '^END' $LOG.sh 2>/dev/null && [ \$i -lt $((HOLD + 60)) ]; do sleep 1; i=\$((i+1)); done" || true
else
  $SSH "cd $R && sh replay.sh dry 0 $LOG" || true
fi
mkdir -p "$D/replays"
scp -o BatchMode=yes -o ConnectTimeout=10 -q root@$HOST:$LOG root@$HOST:$LOG.sh "$D/replays/" || true
cat "$D/replays/$(basename $LOG)" "$D/replays/$(basename $LOG).sh" 2>/dev/null
