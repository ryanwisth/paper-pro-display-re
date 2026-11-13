# Read-only DRM probe for the Paper Pro: lists CRTCs and the framebuffers other clients
# created (GETFB, root gets handles via CAP_SYS_ADMIN). Never sets master, modes or planes.
import os, struct, fcntl, sys
GETRES, GETCRTC, GETFB, GEM_CLOSE = 0xC04064A0, 0xC06864A1, 0xC01C64AD, 0x40086409
fd = os.open('/dev/dri/card0', os.O_RDWR | os.O_CLOEXEC)
def io(req, buf):
    b = bytearray(buf); fcntl.ioctl(fd, req, b, True); return b
r = io(GETRES, bytes(64)); nf, nc, nconn, nenc = struct.unpack_from('<4I', r, 32)
crtcs = (bytearray(4 * nc)); import ctypes
cb = (ctypes.c_uint32 * max(nc, 1))(); req = bytearray(64)
struct.pack_into('<Q', req, 8, ctypes.addressof(cb)); struct.pack_into('<I', req, 36, nc)
r = io(GETRES, req); print('res: fbs(own)=%d crtcs=%d connectors=%d encoders=%d' % (nf, nc, nconn, nenc))
for i in range(nc):
    c = bytearray(104); struct.pack_into('<I', c, 12, cb[i]); c = io(GETCRTC, c)
    cid, fbid, x, y, gs, mv = struct.unpack_from('<6I', c, 12)
    clk, hd, hss, hse, ht, hsk, vd, vss, vse, vt, vs = struct.unpack_from('<I10H', c, 36)
    vr, = struct.unpack_from('<I', c, 60); name = bytes(c[72:104]).split(b'\0')[0]
    print('crtc %d fb=%d mode_valid=%d %dx%d htotal=%d vtotal=%d clk=%dkHz vrefresh=%d %s' % (cid, fbid, mv, hd, vd, ht, vt, clk, vr, name))
found = []
for fid in range(1, int(sys.argv[1]) if len(sys.argv) > 1 else 300):
    b = bytearray(28); struct.pack_into('<I', b, 0, fid)
    try: fcntl.ioctl(fd, GETFB, b, True)
    except OSError: continue
    _, w, h, pitch, bpp, depth, handle = struct.unpack('<7I', b)
    found.append(fid); print('fb %d: %dx%d pitch=%d bpp=%d depth=%d handle=%d' % (fid, w, h, pitch, bpp, depth, handle))
    if handle: io(GEM_CLOSE, struct.pack('<II', handle, 0))
print('found', len(found))
