#!/bin/sh
# Device side of run-replay.sh (Phase 3). dry: read-only checks. write: stop xochitl, replay U-Boot's
# boot sequence, hold HOLD s so the operator can look at the panel, then ALWAYS restart xochitl (EXIT trap;
# HUP/INT/TERM exit through it). Progress goes to LOG.sh; the last line is "END ...".
cd /home/root/display-re
M=$1; HOLD=${2:-15}; LOG=$3
if [ "$M" = write ]; then
  trap 'systemctl start xochitl; echo "xochitl start rc=$? pid=$(pidof xochitl)" >> $LOG.sh; echo "END rc=$rc" >> $LOG.sh' EXIT
  trap 'exit 1' HUP INT TERM
  rc=1
  systemctl stop xochitl; echo "xochitl stop rc=$?" > $LOG.sh
  i=0; while pidof xochitl >/dev/null && [ $i -lt 10 ]; do sleep 1; i=$((i+1)); done
  /opt/bin/python3 replay_boot.py write cumulus_frame-1.raw "$LOG"; rc=$?
  echo "replay rc=$rc; holding ${HOLD}s" >> $LOG.sh
  sleep "$HOLD"
else
  /opt/bin/python3 replay_boot.py dry cumulus_frame-1.raw "$LOG"; rc=$?
  echo "END rc=$rc" > $LOG.sh
fi
exit $rc
