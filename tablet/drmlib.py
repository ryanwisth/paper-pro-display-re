# Read-only DRM helpers (root). Opens card0 as a plain client; never takes master or sets state.
import os, struct, fcntl, mmap, ctypes
# Priority is set by capture.py (v3: SCHED_FIFO 10, below xochitl's framegen 98 / vsync-flip 99). Never nice it:
# nice 10 slowed the 17:04 capture; nice 0 still read 4-6 vblanks late at 17:14.
GETRES, GETCRTC, GETFB, GEM_CLOSE = 0xC04064A0, 0xC06864A1, 0xC01C64AD, 0x40086409
MAP_DUMB, WAIT_VBLANK = 0xC01064B3, 0xC018643A
class Drm:
    def __init__(self):
        self.fd = os.open('/dev/dri/card0', os.O_RDWR | os.O_CLOEXEC)
        cb = (ctypes.c_uint32 * 4)(); req = bytearray(64)
        struct.pack_into('<Q', req, 8, ctypes.addressof(cb)); struct.pack_into('<I', req, 36, 4)
        self.io(GETRES, req); self.crtc = cb[0]
    def io(self, req, b):
        fcntl.ioctl(self.fd, req, b, True); return b
    def crtc_fb(self):
        c = bytearray(104); struct.pack_into('<I', c, 12, self.crtc); self.io(GETCRTC, c)
        return struct.unpack_from('<I', c, 16)[0]
    def fbs(self, hi=300):
        out = {}
        for fid in range(1, hi):
            b = bytearray(28); struct.pack_into('<I', b, 0, fid)
            try: self.io(GETFB, b)
            except OSError: continue
            _, w, h, pitch, bpp, depth, handle = struct.unpack('<7I', b)
            m = bytearray(16); struct.pack_into('<I', m, 0, handle); self.io(MAP_DUMB, m)
            off = struct.unpack_from('<Q', m, 8)[0]
            mm = mmap.mmap(self.fd, pitch * h, mmap.MAP_SHARED, mmap.PROT_READ, offset=off)
            out[fid] = dict(w=w, h=h, pitch=pitch, bpp=bpp, handle=handle, mm=mm)
        return out
    def wait_vblank(self):
        """Blocks until the next vblank; returns (seq, sec, usec). Raises OSError if the CRTC is off."""
        b = bytearray(24); struct.pack_into('<II', b, 0, 1, 1)  # _DRM_VBLANK_RELATIVE, 1
        self.io(WAIT_VBLANK, b); return struct.unpack_from('<IIqq', b, 0)[1:]
