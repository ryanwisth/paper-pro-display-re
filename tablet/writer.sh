#!/bin/sh
# Device side of run-writer.sh (Phase 3 writer). dry: read-only checks, xochitl keeps running.
# write: stop xochitl, play the plan (session 1, hold HOLD s, session 2), then ALWAYS restart xochitl
# (EXIT trap; HUP/INT/TERM exit through it). Progress goes to LOG.sh; the last line is "END ...".
cd /home/root/display-re/writer
M=$1; PLAN=$2; HOLD=${3:-20}; LOG=$4
if [ "$M" = write ]; then
  trap 'systemctl start xochitl; echo "xochitl start rc=$? pid=$(pidof xochitl)" >> $LOG.sh; echo "END rc=$rc" >> $LOG.sh' EXIT
  trap 'exit 1' HUP INT TERM
  rc=1
  systemctl stop xochitl; echo "xochitl stop rc=$?" > $LOG.sh
  i=0; while pidof xochitl >/dev/null && [ $i -lt 10 ]; do sleep 1; i=$((i+1)); done
  /opt/bin/python3 writer.py write "$PLAN" "$LOG" "$HOLD"; rc=$?
  echo "writer rc=$rc" >> $LOG.sh
  sleep 2
else
  /opt/bin/python3 writer.py dry "$PLAN" "$LOG" "$HOLD"; rc=$?
  echo "END rc=$rc" > $LOG.sh
fi
exit $rc
