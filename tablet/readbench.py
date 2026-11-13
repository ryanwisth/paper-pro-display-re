# Read-only: time copies from one of xochitl's framebuffers (idle) to size a capture window.
import sys, time
sys.path.insert(0, '/home/root/display-re'); from drmlib import Drm
d = Drm(); fbs = d.fbs(); f = fbs[sorted(fbs)[0]]; mm = f['mm']; p = f['pitch']
for nlines, step in [(1, 1), (8, 1), (8, 128), (17, 64), (34, 32), (136, 8)]:
    t = time.perf_counter(); n = 0
    for _ in range(20):
        b = b''.join(mm[l*p:(l+1)*p] for l in range(0, nlines * step, step)); n += len(b)
    dt = (time.perf_counter() - t) / 20
    print('%3d lines step %3d: %6d B  %.2f ms  %.1f MB/s' % (nlines, step, len(b), dt * 1e3, len(b) / dt / 1e6))
t = time.perf_counter()
for _ in range(50): d.crtc_fb()
print('GETCRTC %.3f ms' % ((time.perf_counter() - t) / 50 * 1e3))
