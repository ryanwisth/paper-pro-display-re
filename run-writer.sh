#!/bin/bash
# Phase 3 writer (2026-10-13 evening; colour plans 2026-10-13 night): play a frame plan built by tools/mkwriter.py.
#   dry   (default) = read-only checks on the tablet; xochitl keeps running.
#   write = stops xochitl; session 1 (INIT clear + draw); HOLD s with the image up; session 2 (erase); restarts
#           xochitl. Runs detached on the tablet (setsid), so an ssh drop cannot leave xochitl stopped.
#   The plan's draw/erase modes are in its plan.json (mono: A2/A2; colourcard: STANDARD/INIT). A plan with a partial update
#   has 3 sessions, so 2 holds of HOLD s each.
#   scp -C: raw frames are mostly repeated words, so compression cuts the push (colour plan 123 MB).
#   Band pick: if PLAN_DIR has no plan.json but PLAN_DIR-b<n> variants exist (one build per temperature band), the
#   tablet's temperature is read first (read-only) and the variant whose window holds it is played; none = stop here.
# Usage: HOST=TABLET_IP bash run-writer.sh [dry|write] [HOLD_SECONDS=20] [PLAN_DIR=writer/plan-testcard]
set -euo pipefail
HOST=${HOST:?set HOST to the tablet address, e.g. HOST=TABLET_IP}; M=${1:-dry}; HOLD=${2:-20}; TAG=$(date +%H%M%S)
D=$(cd "$(dirname "$0")" && pwd); P=${3:-$D/writer/plan-testcard}
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10 root@$HOST"
if [ ! -f "$P/plan.json" ]; then
  T=$($SSH 'for h in /sys/class/hwmon/hwmon*; do [ "$(cat $h/name)" = rm_hwmon_temp ] && cat $h/temp1_input; done; true')
  [ -n "$T" ] || { echo "no rm_hwmon_temp reading from the tablet: stop"; exit 1; }
  P=$(python3 -I -c 'import json, sys
t = int(sys.argv[1]) / 1000
for d in sys.argv[2:]:
    p = json.load(open(d + "/plan.json"))
    if p["temp_min"] <= t <= p["temp_max"]: print(d); break
else: sys.exit("no plan variant covers %.1f C: rebuild with BAND=<n>" % t)' "$T" "$P"-b*)
  echo "tablet at $(awk -v t="$T" 'BEGIN { printf "%.1f", t / 1000 }') C: playing $(basename "$P")"
fi
PN=$(basename "$P"); R=/home/root/display-re/writer; LOG=$R/writer-$M-$TAG.log
$SSH "mkdir -p $R/$PN"
scp -C -o BatchMode=yes -o ConnectTimeout=10 -q "$D/tablet/writer.py" "$D/tablet/writer.sh" root@$HOST:$R/
# Frames go only when the tablet lacks this exact plan.json (the push runs ~1 MB/s over Wi-Fi). plan.json goes last, so
# it marks a complete copy; the player re-checks every frame's sha256 against it before any drive, so a stale or
# partial copy aborts cleanly.
if [ "$($SSH "sha256sum $R/$PN/plan.json 2>/dev/null" | cut -d' ' -f1 || true)" = "$(shasum -a 256 "$P/plan.json" | cut -d' ' -f1)" ]; then
  echo "tablet already holds this plan.json: frames not re-pushed"
else
  scp -C -o BatchMode=yes -o ConnectTimeout=10 -q "$P"/f*.raw "$P"/plan.json root@$HOST:$R/$PN/
fi
if [ "$M" = write ]; then
  $SSH "cd $R && setsid sh writer.sh write $PN $HOLD $LOG </dev/null >/dev/null 2>&1 &"
  NS=$(python3 -I -c 'import json, sys; print(len(json.load(open(sys.argv[1]))["sessions"]))' "$P/plan.json")
  $SSH "i=0; while ! grep -q '^END' $LOG.sh 2>/dev/null && [ \$i -lt $((HOLD * (NS - 1) + 60)) ]; do sleep 1; i=\$((i+1)); done" || true
else
  $SSH "cd $R && sh writer.sh dry $PN $HOLD $LOG" || true
fi
mkdir -p "$D/replays"
scp -C -o BatchMode=yes -o ConnectTimeout=10 -q root@$HOST:$LOG root@$HOST:$LOG.sh "$D/replays/" || true
cat "$D/replays/$(basename $LOG)" "$D/replays/$(basename $LOG).sh" 2>/dev/null
