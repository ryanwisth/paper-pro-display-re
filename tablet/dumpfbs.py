# Read-only: copy every framebuffer the UI created into one zlib file.
import sys, zlib, struct, json
sys.path.insert(0, '/home/root/display-re'); from drmlib import Drm
d = Drm(); fbs = d.fbs(); hdr = {'crtc_fb': d.crtc_fb(), 'fbs': {}}
blobs = []
for fid, f in sorted(fbs.items()):
    raw = f['mm'][:]; hdr['fbs'][fid] = dict(w=f['w'], h=f['h'], pitch=f['pitch'], n=len(raw)); blobs.append(raw)
z = zlib.compress(json.dumps(hdr).encode() + b'\n' + b''.join(blobs), 6)
open(sys.argv[1], 'wb').write(z); print(hdr['crtc_fb'], len(blobs), len(z))
