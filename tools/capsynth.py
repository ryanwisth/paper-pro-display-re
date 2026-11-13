"""Self-test for capcheck: synthesize a capture from the decoded waveform (mode 2, band for 27 C),
random from/to states per pixel, random phase shift 0..3, then pack it like capture.py does."""
import sys, zlib, json, struct, random
sys.path.insert(0, sys.argv[1]); import eink
from capcheck import WIRE
W = eink.decode(open(sys.argv[2], 'rb').read()); mode = int(sys.argv[4])
P = eink.phases(W, mode, 4); random.seed(1)
states = [0, 2, 28, 30]; npx = 1620 * 8 * 2 // 8 * 8  # 2 lines of 405 words x 8 codes
pix = [(random.choice(states), random.choice(states), random.randrange(4)) for _ in range(405 * 8 * 2)]
nfr = len(P) + 4; recs = []; blobs = []
for f in range(nfr):
    codes = []
    for fr, to, sh in pix:
        k = f - sh; codes.append(WIRE[P[k][to][fr]] if 0 <= k < len(P) else 0)
    words = [sum(codes[8*i + j] << (3*j) for j in range(8)) for i in range(len(codes) // 8)]
    b = struct.pack('<%dI' % len(words), *words); blobs.append(b)
    recs.append(dict(u=1, kind='vbl', fb=37 + f % 16, t=f / 85, seq=f, n=len(b)))
meta = dict(pitch=1620, h=1084, lines=[0, 8], fbs=list(range(37, 53)), recs=recs)
open(sys.argv[3], 'wb').write(zlib.compress(json.dumps(meta).encode() + b'\n' + b''.join(blobs), 1))
