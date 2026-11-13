# Phase 3 writer, tablet side (2026-10-13).
# Plays a frame plan built on the Mac by tools/mkwriter.py: plan.json lists unique frames (f<i>.raw, sha256-checked)
# and sessions (lists of frame indices). Each session: SETCRTC on a zero frame, one vblank wait, one page flip per
# frame (each confirmed by its event), pipe off. Between sessions the pipe is off for HOLD seconds (the image stays).
# Same DRM path and fail-safes as replay_boot.py (write attempt 2, 17:41, SUCCESS): panel lock, wakelock, DRM master,
# SCHED_FIFO 50, SIGALRM watchdog per session, finally = pipe off + fbs removed + master dropped + locks released;
# if the process dies anyway, closing the DRM fd removes its fbs, which turns the pipe off. Never writes rail/VCOM files.
# v2 2026-10-13: a second copy only for frames that play back to back (and the zero frame,
# shown at pipe-on), so each flip still targets a buffer that is not on screen. CmaFree is logged, not enforced: page cache
# can sit in CMA and is migrated on allocation (the 20:10 pushes took CmaFree from 593 to 170 MB), and a failed CREATE_DUMB
# aborts before the pipe is ever on.
# v1 (two copies of everything) is not published.
# Usage: writer.py dry|write PLANDIR LOG [HOLD_SECONDS=20]
import sys, os, json, struct, fcntl, mmap, ctypes, time, signal, hashlib, select, glob, errno

SET_MASTER, DROP_MASTER = 0x641E, 0x641F
GETRES, GETCRTC, SETCRTC, GETCONNECTOR = 0xC04064A0, 0xC06864A1, 0xC06864A2, 0xC05064A7
CREATE_DUMB, MAP_DUMB, DESTROY_DUMB = 0xC02064B2, 0xC01064B3, 0xC00464B4
ADDFB, RMFB, PAGE_FLIP, WAIT_VBLANK = 0xC01C64AE, 0xC00464AF, 0xC01864B0, 0xC018643A
W, H, DEPTH = 405, 1084, 24
LOCK, WAKE, BATT_MIN = '/tmp/epframebuffer.lock', 'display-re', 20
FB_BYTES = 429 * 4096                                           # one dumb buffer (pitch 1620 x 1084, page-rounded)

mode, plan_dir, log_path = sys.argv[1], sys.argv[2], sys.argv[3]
HOLD = float(sys.argv[4]) if len(sys.argv) > 4 else 20
assert mode in ('dry', 'write')
log = open(log_path, 'w', buffering=1)
def say(*a): log.write(' '.join(str(x) for x in a) + '\n')
class Abort(Exception): pass
def need(ok, what):
    if not ok: raise Abort(what)
def io(fd, req, b):
    fcntl.ioctl(fd, req, b, True); return b
def arr(n):
    a = (ctypes.c_uint32 * max(n, 1))(); return a, ctypes.addressof(a)
def sysread(p):
    try: return open(p).read().strip()
    except OSError: return None
def cma_free():
    for ln in open('/proc/meminfo'):
        if ln.startswith('CmaFree:'): return int(ln.split()[1]) * 1024
def temp():
    for hw in glob.glob('/sys/class/hwmon/hwmon*'):
        if sysread(hw + '/name') == 'rm_hwmon_temp': return int(sysread(hw + '/temp1_input')) / 1000

fd = None; crtc_on = False; fbs = []; handles = []; lockf = None; waked = False
def on_alarm(*_): raise Abort('watchdog: session took too long')
signal.signal(signal.SIGALRM, on_alarm)
signal.signal(signal.SIGHUP, signal.SIG_IGN)
def on_term(*_): raise Abort('SIGTERM')
signal.signal(signal.SIGTERM, on_term)
rc = 1
try:
    say('start', time.ctime(), 'mode', mode, 'pid', os.getpid(), 'hold', HOLD)
    # --- preflight (read-only) ---
    plan = json.load(open(os.path.join(plan_dir, 'plan.json')))
    need(plan.get('version') == 1, 'plan version')
    say('plan built', plan['built'], 'source', plan['source'], 'temp window', plan['temp_min'], '-', plan['temp_max'], 'C')
    batt = sysread('/sys/class/power_supply/max1726x_battery/capacity'); t = temp()
    say('battery', batt, '% temp', t, 'C')
    need(batt is not None and int(batt) >= BATT_MIN, 'battery below %d%%' % BATT_MIN)
    t_ok = t is not None and plan['temp_min'] <= t <= plan['temp_max']
    if mode == 'dry' and not t_ok: say('NOTE temperature outside the plan window: a write would abort here; dry checks go on')
    else: need(t_ok, 'temperature outside the plan window')
    data = []
    for f in plan['frames']:
        b = open(os.path.join(plan_dir, f['file']), 'rb').read()
        need(len(b) == W * H * 4 and hashlib.sha256(b).hexdigest() == f['sha256'], 'frame %s size/hash mismatch' % f['file'])
        data.append(b)
    need(not any(data[0]), 'frame 0 must be the zero frame')
    sessions = plan['sessions']
    need(all(0 <= i < len(data) for s in sessions for i in s), 'session index out of range')
    need(all(s[-1] == 0 for s in sessions), 'each session must end on a zero frame')
    say('frames ok:', len(data), 'unique;', ' + '.join(str(len(s)) for s in sessions), 'flips')
    two = {0} | {a for s in sessions for a, b in zip(s, s[1:]) if a == b}       # frames needing a second copy
    nfbs = len(data) + len(two); need_b = nfbs * FB_BYTES; cma = cma_free()
    say('fbs needed %d (%d frames twice), %.0f MB; CmaFree %.0f MB (a floor: movable pages in CMA migrate)'
        % (nfbs, len(two), need_b / 1e6, (cma or 0) / 1e6))
    fd = os.open('/dev/dri/card0', os.O_RDWR | os.O_CLOEXEC)
    res = io(fd, GETRES, bytearray(64)); nfb, nc, ncon, nenc = struct.unpack_from('<4I', res, 32)
    crtcs, pc = arr(nc); cons, pn = arr(ncon); res = bytearray(64)
    struct.pack_into('<QQ', res, 8, pc, pn); struct.pack_into('<II', res, 36, nc, ncon); io(fd, GETRES, res)
    need(nc == 1 and ncon == 1, 'expected 1 crtc + 1 connector, got %d + %d' % (nc, ncon))
    crtc, con = crtcs[0], cons[0]
    gc = bytearray(80); struct.pack_into('<I', gc, 48, con); io(fd, GETCONNECTOR, gc)
    nmodes, nenc2 = struct.unpack_from('<I', gc, 32)[0], struct.unpack_from('<I', gc, 40)[0]
    modes = ctypes.create_string_buffer(68 * max(nmodes, 1)); encs, pe = arr(nenc2)
    gc = bytearray(80); struct.pack_into('<QQ', gc, 0, pe, ctypes.addressof(modes))
    struct.pack_into('<I', gc, 32, nmodes); struct.pack_into('<I', gc, 40, nenc2); struct.pack_into('<I', gc, 48, con)
    io(fd, GETCONNECTOR, gc); connection = struct.unpack_from('<I', gc, 60)[0]
    need(nmodes == 1 and connection == 1, 'connector: %d modes, connection %d' % (nmodes, connection))
    mode_b = bytes(modes.raw[:68]); hd, vd = struct.unpack_from('<H', mode_b, 4)[0], struct.unpack_from('<H', mode_b, 14)[0]
    need((hd, vd) == (W, H), 'mode is %dx%d' % (hd, vd))
    clk, = struct.unpack_from('<I', mode_b, 0); ht, = struct.unpack_from('<H', mode_b, 10); vt, = struct.unpack_from('<H', mode_b, 20)
    say('crtc', crtc, 'connector', con, 'mode %dx%d clk=%dkHz %dx%d (%.2f ms/frame)' % (hd, vd, clk, ht, vt, ht * vt / clk))
    g = bytearray(104); struct.pack_into('<I', g, 12, crtc); io(fd, GETCRTC, g)
    say('crtc now: fb', struct.unpack_from('<I', g, 16)[0])
    if mode == 'dry':
        say('xochitl pid', os.popen('pidof xochitl').read().strip() or 'none')
        say('setsid', os.popen('command -v setsid').read().strip() or 'MISSING')
        say('plan:', (' | hold %gs | ' % HOLD).join('%d flips (%.2f s)' % (len(s), len(s) * ht * vt / clk / 1000) for s in sessions))
        say('DRY RESULT:', 'ok' if t_ok else 'frames ok, but a write now would abort on temperature')
        rc = 0 if t_ok else 3; raise SystemExit
    # --- write ---
    need(not os.popen('pidof xochitl').read().strip(), 'xochitl still running')
    need(struct.unpack_from('<I', g, 16)[0] == 0, 'CRTC busy (pipe on)')
    lockf = open(LOCK, 'a'); fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB); say('panel lock held')
    open('/sys/power/wake_lock', 'w').write(WAKE); waked = True; say('wakelock held')
    fcntl.ioctl(fd, SET_MASTER); say('DRM master')
    def mkfb(b):
        c = bytearray(32); struct.pack_into('<III', c, 0, H, W, 32); io(fd, CREATE_DUMB, c)
        handle, pitch, size = struct.unpack_from('<IIQ', c, 16); handles.append(handle)
        m = bytearray(16); struct.pack_into('<I', m, 0, handle); io(fd, MAP_DUMB, m)
        mm = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=struct.unpack_from('<Q', m, 8)[0])
        row = W * 4
        for y in range(H): mm[y * pitch:y * pitch + row] = b[y * row:(y + 1) * row]
        need(all(mm[y * pitch:y * pitch + row] == b[y * row:(y + 1) * row] for y in (0, 4, H // 2, H - 1)), 'fb readback')
        mm.close()
        a = bytearray(28); struct.pack_into('<7I', a, 0, 0, W, H, pitch, 32, DEPTH, handle); io(fd, ADDFB, a)
        fid = struct.unpack_from('<I', a, 0)[0]; fbs.append(fid); return fid
    copies = [[mkfb(b), mkfb(b)] if u in two else [mkfb(b)] for u, b in enumerate(data)]
    say('fbs', copies)
    os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(50))
    def setcrtc(fid, on):
        c = bytearray(104); conn = (ctypes.c_uint32 * 1)(con)
        struct.pack_into('<QIIIIIII', c, 0, ctypes.addressof(conn) if on else 0, 1 if on else 0, crtc, fid, 0, 0, 0, 1 if on else 0)
        if on: c[36:104] = mode_b
        io(fd, SETCRTC, c)
    def wait_vblank():
        b = bytearray(24); struct.pack_into('<II', b, 0, 1, 1); io(fd, WAIT_VBLANK, b)
        return struct.unpack_from('<I', b, 4)[0]
    retries = []
    def flip(fid, i):
        b = bytearray(24); struct.pack_into('<IIIIQ', b, 0, crtc, fid, 1, 0, i)
        try: io(fd, PAGE_FLIP, b)
        except OSError as e:
            if e.errno != errno.EBUSY: raise
            s = wait_vblank(); retries.append(i); say('flip %d: EBUSY, waited vblank seq %d, retrying once' % (i, s))
            b = bytearray(24); struct.pack_into('<IIIIQ', b, 0, crtc, fid, 1, 0, i); io(fd, PAGE_FLIP, b)
        r, _, _ = select.select([fd], [], [], 0.2); need(r, 'no flip event for frame %d' % i)
        ev = os.read(fd, 4096); typ, ln, ud, sec, usec, seq = struct.unpack_from('<IIQIII', ev, 0)
        need(typ == 2 and ud == i, 'unexpected event type %d data %d' % (typ, ud)); return seq, sec + usec / 1e6
    for n, sess in enumerate(sessions):
        if n:
            say('hold %gs, pipe off' % HOLD); time.sleep(HOLD)
        signal.alarm(10 + len(sess) // 40)
        shown = copies[0][0]
        t0 = time.time(); crtc_on = True; setcrtc(shown, True); say('session %d: pipe on (zero frame) %.1f ms' % (n + 1, (time.time() - t0) * 1e3))
        t0 = time.time(); s = wait_vblank(); say('session %d: vblank after pipe on: seq %d, %.1f ms' % (n + 1, s, (time.time() - t0) * 1e3))
        seqs = []
        for i, u in enumerate(sess):
            fid = copies[u][0] if copies[u][0] != shown else copies[u][1]
            seqs.append(flip(fid, n * 100000 + i)); shown = fid
        setcrtc(0, False); crtc_on = False; signal.alarm(0)
        dts = sorted(b[1] - a[1] for a, b in zip(seqs, seqs[1:]))
        say('session %d: flips %d in %.3f s; frame period ms min %.2f median %.2f max %.2f; EBUSY retries so far %d; pipe off'
            % (n + 1, len(seqs), seqs[-1][1] - seqs[0][1], dts[0] * 1e3, dts[len(dts) // 2] * 1e3, dts[-1] * 1e3, len(retries)))
    say('all sessions done'); rc = 0
except SystemExit: pass
except Exception as e:
    say('ABORT', type(e).__name__, e)
finally:
    signal.alarm(0)
    if crtc_on:
        try: setcrtc(0, False); say('finally: pipe off')
        except Exception as e: say('finally: pipe off FAILED', e)
    for f in fbs:
        try: io(fd, RMFB, bytearray(struct.pack('<I', f)))
        except OSError as e: say('rmfb', f, e)
    for h_ in handles:
        try: io(fd, DESTROY_DUMB, bytearray(struct.pack('<I', h_)))
        except OSError as e: say('destroy', h_, e)
    if fd is not None:
        try: fcntl.ioctl(fd, DROP_MASTER)
        except OSError: pass
        os.close(fd)
    if waked:
        try: open('/sys/power/wake_unlock', 'w').write(WAKE); say('wakelock released')
        except OSError as e: say('wake_unlock', e)
    if lockf: lockf.close(); say('panel lock released')
    say('end', time.ctime(), 'rc', rc)
sys.exit(rc)
