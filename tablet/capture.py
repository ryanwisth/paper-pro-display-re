# Passive frame capture on the Paper Pro (root), v4 2026-10-13. v3 is not published.
# Read-only: maps xochitl's own framebuffers through GETFB + MAP_DUMB. Never takes DRM master, never commits,
# never writes a framebuffer. The only sysfs writes are a timed wakelock (/sys/power/wake_lock, released via
# wake_unlock; the kernel drops it on its own after ARM + GRACE_S + 30 s even if this script dies): kernel
# autosleep froze the dry4 run (5 s took 128 s). Runs at SCHED_FIFO RTPRIO (default 10: above every normal
# xochitl thread, below framegen 98 and vsync-flip 99, so it cannot delay the display; v3 showed this fixes
# the CPU starvation that made v2's reads slow).
# v3 lesson (17:19 run): asking which buffer is on screen (GETCRTC) after each read cost 4 vblank counts, so
# 1-2 ring buffers were skipped per frame (likely the modeset lock xochitl's commits hold; not measured).
# v4 never calls GETCRTC. Once per vblank wake it sweeps the sampled lines of EVERY ring buffer and records,
# raw, each buffer whose bytes changed since its last read (all-zero contents are flagged, not stored), then
# re-reads the changed ones once so a read torn by a write in progress can be told apart.
# No inference on the tablet: frame order is rebuilt offline from ring position (tools/capcheck.py), so the
# rules can change without a new capture. WAIT_VBLANK failing = pipe off = idle; that also splits updates.
# Kernel vblank timestamps are kept; the DRM sequence counter stepped 2 per 11.77 ms frame in the 17:41
# replay, so sequence steps are not frame counts.
# Usage: capture.py OUT ARM_S MAX_UPDATES LINE_STEP   (LINE_STEP 128 -> 8 lines per buffer, ~3 ms per sweep)
import sys, time, zlib, json, os, signal, glob
signal.signal(signal.SIGHUP, signal.SIG_IGN)  # survive an ssh drop; the file still gets written
sys.path.insert(0, '/home/root/display-re'); from drmlib import Drm
out, arm_s, max_upd, step = sys.argv[1], float(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
RAM_CAP = 300 << 20
GRACE_S = 30  # an update still running this long after the window closes is cut off
RTPRIO = int(os.environ.get('RTPRIO', '10'))
if RTPRIO: os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(RTPRIO))
d = Drm(); fbs = d.fbs(); ids = sorted(fbs); f0 = fbs[ids[0]]
pitch, h = f0['pitch'], f0['h']; lines = list(range(step // 2, h, step))  # lines 0-3 are spare
spans = [(l * pitch, (l + 1) * pitch) for l in lines]
mms = [fbs[i]['mm'] for i in ids]; ZERO = bytes(len(spans) * pitch)
def grab(mm): return b''.join(mm[a:b] for a, b in spans)
def ms(v):
    v = sorted(v); return '%.2f/%.2f' % (v[len(v) // 2] / 1000, v[-1] / 1000) if v else '-'
def temp():
    for hw in glob.glob('/sys/class/hwmon/hwmon*'):
        try:
            if open(hw + '/name').read().strip() == 'rm_hwmon_temp':
                return int(open(hw + '/temp1_input').read()) / 1000
        except OSError: pass
    return None
recs, blobs = [], []; used = 0; s = 0
def add(r, data=b''):
    global used
    r['n'] = len(data); recs.append(r); blobs.append(data); used += len(data)
last = [grab(mm) for mm in mms]
for k, b in enumerate(last): add(dict(kind='base', u=0, s=0, fb=ids[k], zero=b == ZERO), b'' if b == ZERO else b)
def sweep(u, seq, t):
    """Read every ring buffer once; record the ones that changed. -> (wall us, cpu us, changed).
    Each new non-zero read is read again at the end of the sweep (~3 ms later): equal = not torn by a write in
    progress (stable=True); different = the newer read is recorded too (recheck=True)."""
    global s
    s += 1; w, c = time.perf_counter_ns(), time.thread_time_ns(); chg = 0; again = []
    for k, mm in enumerate(mms):
        b = grab(mm)
        if b != last[k]:
            last[k] = b; chg += 1; z = b == ZERO; r = dict(kind='chg', u=u, s=s, fb=ids[k], zero=z)
            add(r, b'' if z else b)
            if not z: again.append((k, r))
    for k, r in again:
        b = grab(mms[k])
        if b == last[k]: r['stable'] = True
        else:
            last[k] = b; chg += 1; z = b == ZERO
            add(dict(kind='chg', u=u, s=s, fb=ids[k], zero=z, recheck=True), b'' if z else b)
    rw, rc = (time.perf_counter_ns() - w) // 1000, (time.thread_time_ns() - c) // 1000
    add(dict(kind='sweep', u=u, s=s, seq=seq, t=t, rw=rw, rc=rc, chg=chg)); return rw, rc, chg
def sysw(path, s):  # unbuffered, so a refused write raises here instead of vanishing in a deferred close
    fd = os.open(path, os.O_WRONLY)
    try: os.write(fd, s.encode())
    finally: os.close(fd)
WAKE = 'display-re-cap'
try: sysw('/sys/power/wake_lock', '%s %d' % (WAKE, int((arm_s + GRACE_S + 30) * 1e9))); wl = 'held'
except OSError as e: wl = 'FAILED(%s)' % e  # capture still works while the operator keeps touching the tablet
log = open(out + '.log', 'w', buffering=1)
log.write('armed %s v4 fbs=%s lines=%d temp=%s sched=%d rtprio=%d nonzero_at_arm=%d wakelock=%s\n' % (time.ctime(), ids,
          len(lines), temp(), os.sched_getscheduler(0), os.sched_getparam(0).sched_priority, sum(b != ZERO for b in last), wl))
try:
    upd = 0; idle = 0; t_end = time.time() + arm_s
    while upd < max_upd and time.time() < t_end and used < RAM_CAP:
        try: seq, sec, usec = d.wait_vblank()
        except OSError: idle += 1; time.sleep(0.002); continue  # pipe off: idle
        upd += 1; t0 = time.time(); RW, RC, T = [], [], []; nchg = 0; cut = ''
        while True:
            t = sec + usec / 1e6; rw, rc, chg = sweep(upd, seq, t); RW.append(rw); RC.append(rc); T.append(t); nchg += chg
            if used >= RAM_CAP: cut = ' CUT=ram'; break
            if time.time() > t_end + GRACE_S: cut = ' CUT=grace'; break
            try: seq, sec, usec = d.wait_vblank()
            except OSError: break  # pipe off: update over
        rw, rc, chg = sweep(upd, None, time.monotonic()); nchg += chg  # last frames before pipe-off; DRM stamps are monotonic
        gaps = sorted(b - a for a, b in zip(T, T[1:]))
        log.write('update %d sweeps=%d changes=%d dur=%.3fs temp=%s used=%dMB sweep_wall_ms(med/max)=%s sweep_cpu_ms=%s '
                  'wake_gap_ms(med/max)=%s%s\n' % (upd, len(T), nchg, time.time() - t0, temp(), used >> 20, ms(RW), ms(RC),
                  ms([g * 1e6 for g in gaps]), cut))
    meta = dict(version=4, pitch=pitch, h=h, lines=lines, fbs=ids, recs=recs)
    with open(out, 'wb') as f:
        f.write(zlib.compress(json.dumps(meta).encode() + b'\n' + b''.join(blobs), 1))
    log.write('done %s updates=%d recs=%d idle_polls=%d\n' % (time.ctime(), upd, len(recs), idle))
finally:
    if wl == 'held':
        try: sysw('/sys/power/wake_unlock', WAKE); log.write('wakelock released\n')
        except OSError as e: log.write('wake_unlock failed: %s\n' % e)
