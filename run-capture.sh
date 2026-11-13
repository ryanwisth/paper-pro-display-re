#!/bin/bash
# One-shot passive frame capture (run by hand; see docs/method.md).
# Arms capture.py on the tablet for ARM seconds; stops after 4 screen updates or ARM.
# While armed, the operator turns one page in a notebook with handwriting, then draws one short stroke.
# Read-only on the device: maps the UI's framebuffers, never commits or writes panel state; its one write is a
# timed wakelock (display-re-cap) so autosleep cannot freeze the window.
# v4 (capture.py): sweeps all 16 ring buffers per vblank; STEP=128 -> 8 lines per buffer (~3 ms per sweep).
# Usage: HOST=TABLET_IP WAVEFORM=/path/to/panel.eink [STEP=128] [MAXUPD=4] bash run-capture.sh [ARM_SECONDS=120] [TAG=HHMMSS]   (STEP=64 -> 16 lines, ~4.4 ms sweep)
set -euo pipefail
HOST=${HOST:?set HOST to the tablet address, e.g. HOST=TABLET_IP}; ARM=${1:-120}; TAG=${2:-$(date +%H%M%S)}
D=$(cd "$(dirname "$0")" && pwd)
OUT=/home/root/display-re/cap-$TAG.z; mkdir -p "$D/captures"
scp -o BatchMode=yes -o ConnectTimeout=10 -q "$D/tablet/drmlib.py" "$D/tablet/capture.py" root@$HOST:/home/root/display-re/
ssh -o BatchMode=yes -o ConnectTimeout=10 root@$HOST "cd /home/root/display-re && /opt/bin/python3 capture.py $OUT $ARM ${MAXUPD:-4} ${STEP:-128}" || true
scp -o BatchMode=yes -o ConnectTimeout=10 -q root@$HOST:$OUT root@$HOST:$OUT.log "$D/captures/"
cat "$D/captures/cap-$TAG.z.log"
T=$(grep -o "temp=[0-9.]*" "$D/captures/cap-$TAG.z.log" | sed -n 2p | cut -d= -f2)
# v4: display-order rebuild (tools/capdisp4.py); capcheck.py's own v4 ring rebuild scrambles frame order (cap-181321)
python3 -I "$D/tools/capdisp4.py" "$D/tools" \
  "${WAVEFORM:?set WAVEFORM to the panel .eink file (see docs/method.md)}" \
  "$D/captures/cap-$TAG.z" "${T:-27}"
