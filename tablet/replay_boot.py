# Phase 3, first active panel write.
# Replays U-Boot's exact Developer Mode boot sequence (u-boot board/reMarkable/ferrari/ferrari_splash.c):
#   pipe on scanning a zero frame, 80 frames of 0xdb6db6 (code 6 everywhere), 80 frames of
#   cumulus_frame-1 (SPLASH_DEVWARNING = 1), 2 zero frames, pipe off.
# One page flip per frame, each confirmed by its flip event; the vblank sequence of every event is
# logged so a slipped frame (sequence step != 1) shows up. Uses the panel's own device-tree mode.
# Never writes the rail/VCOM sysfs files. Fail-safes: SIGALRM watchdog -> finally (zero frame, pipe off,
# fbs removed); if the process dies anyway, closing the DRM fd removes its fbs, which turns the pipe off.
# Waits one vblank after SETCRTC before the first flip (write attempt 1, 17:33, got EBUSY there): ferrari's DT sets
# lcdif `flip-before-wait`, whose commit tail skips the vblank wait (lcdif-kms.c), so SETCRTC returns before its
# flip_done, which lcdif_crtc_atomic_begin arms for the next vblank; a nonblocking flip before then fails EBUSY in
# stall_checks (drm_atomic_helper.c). flip() also retries once after one vblank on EBUSY, logging each retry.
# Usage: replay_boot.py dry|write FRAME1_RAW LOG
#   dry   = read-only checks: battery, temperature, frame file hash, connector/mode, CRTC idle. No master.
#   write = run by replay.sh after it stops xochitl; takes the panel lock, a wakelock and DRM master.
import sys, os, struct, fcntl, mmap, ctypes, time, signal, hashlib, select, glob, errno

SET_MASTER, DROP_MASTER = 0x641E, 0x641F
GETRES, GETCRTC, SETCRTC, GETCONNECTOR = 0xC04064A0, 0xC06864A1, 0xC06864A2, 0xC05064A7
CREATE_DUMB, MAP_DUMB, DESTROY_DUMB = 0xC02064B2, 0xC01064B3, 0xC00464B4
ADDFB, RMFB, PAGE_FLIP = 0xC01C64AE, 0xC00464AF, 0xC01864B0
W, H, FILL, N1, N2, N0 = 405, 1084, 0xDB6DB6, 80, 80, 2
FRAME1_SHA = '21951f7606f1198a8de8bb8b351127690040d7aed92c550561eb16898528fbf8'
LOCK, WAKE, BATT_MIN = '/tmp/epframebuffer.lock', 'display-re', 20
DEPTH = 24  # XRGB8888; dry run prints xochitl's own fb depth to confirm
GETFB, GEM_CLOSE, WAIT_VBLANK = 0xC01C64AD, 0x40086409, 0xC018643A

mode, raw_path, log_path = sys.argv[1], sys.argv[2], sys.argv[3]
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
def temp():
    for hw in glob.glob('/sys/class/hwmon/hwmon*'):
        if sysread(hw + '/name') == 'rm_hwmon_temp': return int(sysread(hw + '/temp1_input')) / 1000
def mode_str(m):
    clk, hd, hss, hse, ht, hsk, vd, vss, vse, vt, vs = struct.unpack_from('<I10H', m, 0)
    vr, = struct.unpack_from('<I', m, 24)
    return '%dx%d clk=%dkHz htotal=%d vtotal=%d vrefresh=%d (%.2f ms/frame)' % (hd, vd, clk, ht, vt, vr, ht * vt / clk)

fd = None; crtc_on = False; fbs = []; handles = []; lockf = None; waked = False
def on_alarm(*_): raise Abort('watchdog: sequence took too long')
signal.signal(signal.SIGALRM, on_alarm)
signal.signal(signal.SIGHUP, signal.SIG_IGN)
def on_term(*_): raise Abort('SIGTERM')
signal.signal(signal.SIGTERM, on_term)
rc = 1
try:
    say('start', time.ctime(), 'mode', mode, 'pid', os.getpid())
    # --- preflight (read-only) ---
    batt = sysread('/sys/class/power_supply/max1726x_battery/capacity'); t = temp()
    say('battery', batt, '% temp', t, 'C')
    need(batt is not None and int(batt) >= BATT_MIN, 'battery below %d%%' % BATT_MIN)
    need(t is not None and 5 <= t <= 40, 'temperature outside 5-40 C')
    raw = open(raw_path, 'rb').read()
    need(len(raw) == W * H * 4 and hashlib.sha256(raw).hexdigest() == FRAME1_SHA, 'frame1 file size/hash mismatch')
    fill = struct.pack('<I', FILL) * (W * H)
    say('frames ok: fill 0x%06x, frame1 sha256 %s..' % (FILL, FRAME1_SHA[:12]))
    x = sysread('/sys/class/power_supply/max77963-charger/status'); say('charger', x)
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
    say('crtc', crtc, 'connector', con, 'mode', mode_str(mode_b))
    g = bytearray(104); struct.pack_into('<I', g, 12, crtc); io(fd, GETCRTC, g)
    say('crtc now: fb', struct.unpack_from('<I', g, 16)[0], 'mode_valid', struct.unpack_from('<I', g, 32)[0])
    if mode == 'dry':
        say('xochitl pid', os.popen('pidof xochitl').read().strip() or 'none')
        for fid in range(1, 300):
            b = bytearray(28); struct.pack_into('<I', b, 0, fid)
            try: io(fd, GETFB, b)
            except OSError: continue
            _, w_, h_, p_, bpp_, dep_, hnd = struct.unpack('<7I', b)
            say('xochitl fb %d: %dx%d pitch=%d bpp=%d depth=%d' % (fid, w_, h_, p_, bpp_, dep_))
            if hnd: fcntl.ioctl(fd, GEM_CLOSE, struct.pack('<II', hnd, 0))
            break
        say('setsid', os.popen('command -v setsid').read().strip() or 'MISSING')
        say('plan: setcrtc(zero) -> %d x fill -> %d x frame1 -> %d x zero -> pipe off' % (N1, N2, N0))
        rc = 0; raise SystemExit
    # --- write ---
    need(not os.popen('pidof xochitl').read().strip(), 'xochitl still running')
    need(struct.unpack_from('<I', g, 16)[0] == 0, 'CRTC busy (pipe on)')
    lockf = open(LOCK, 'a'); fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB); say('panel lock held')
    open('/sys/power/wake_lock', 'w').write(WAKE); waked = True; say('wakelock held')
    fcntl.ioctl(fd, SET_MASTER); say('DRM master')
    def mkfb(data):
        c = bytearray(32); struct.pack_into('<III', c, 0, H, W, 32); io(fd, CREATE_DUMB, c)
        handle, pitch, size = struct.unpack_from('<IIQ', c, 16); handles.append(handle)
        m = bytearray(16); struct.pack_into('<I', m, 0, handle); io(fd, MAP_DUMB, m)
        mm = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=struct.unpack_from('<Q', m, 8)[0])
        row = W * 4
        for y in range(H): mm[y * pitch:y * pitch + row] = data[y * row:(y + 1) * row]
        need(all(mm[y * pitch:y * pitch + row] == data[y * row:(y + 1) * row] for y in (0, H // 2, H - 1)), 'fb readback')
        mm.close()
        a = bytearray(28); struct.pack_into('<7I', a, 0, 0, W, H, pitch, 32, DEPTH, handle); io(fd, ADDFB, a)
        fid = struct.unpack_from('<I', a, 0)[0]; fbs.append(fid); return fid, pitch
    # two identical copies of fill and frame1, alternated, so no flip ever targets the buffer on screen
    fb0, pitch = mkfb(bytes(W * H * 4)); fbF = [mkfb(fill)[0] for _ in range(2)]; fb1 = [mkfb(raw)[0] for _ in range(2)]
    say('fbs zero=%d fill=%s frame1=%s pitch=%d depth=%d' % (fb0, fbF, fb1, pitch, DEPTH))
    os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(50))
    signal.alarm(10)
    def setcrtc(fid, on):
        c = bytearray(104); conn = (ctypes.c_uint32 * 1)(con)
        struct.pack_into('<QIIIIIII', c, 0, ctypes.addressof(conn) if on else 0, 1 if on else 0, crtc, fid, 0, 0, 0, 1 if on else 0)
        if on: c[36:104] = mode_b
        io(fd, SETCRTC, c)
    def wait_vblank():
        b = bytearray(24); struct.pack_into('<II', b, 0, 1, 1); io(fd, WAIT_VBLANK, b)  # _DRM_VBLANK_RELATIVE, 1 (as drmlib.py)
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
    t0 = time.time(); crtc_on = True; setcrtc(fb0, True); say('pipe on (zero frame) %.1f ms' % ((time.time() - t0) * 1e3))
    t0 = time.time(); s = wait_vblank(); say('vblank after pipe on: seq %d, %.1f ms' % (s, (time.time() - t0) * 1e3))
    seqs = []
    plan = [fbF[i % 2] for i in range(N1)] + [fb1[i % 2] for i in range(N2)] + [fb0] * N0
    for i, fid in enumerate(plan): seqs.append(flip(fid, i))
    setcrtc(0, False); crtc_on = False; signal.alarm(0)
    steps = [b[0] - a[0] for a, b in zip(seqs, seqs[1:])]
    dts = sorted(b[1] - a[1] for a, b in zip(seqs, seqs[1:]))
    say('flips %d, seq %d..%d, steps!=1: %d %s' % (len(seqs), seqs[0][0], seqs[-1][0], sum(s != 1 for s in steps),
        [(i + 1, s) for i, s in enumerate(steps) if s != 1][:10]))
    say('frame period ms: min %.2f median %.2f max %.2f' % (dts[0] * 1e3, dts[len(dts) // 2] * 1e3, dts[-1] * 1e3))
    say('EBUSY retries: %d %s' % (len(retries), retries[:10]))
    say('pipe off; sequence done'); rc = 0
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
