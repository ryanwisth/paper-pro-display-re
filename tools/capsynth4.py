"""Self-test for capcheck's v4 rebuild: synthesize a v4 capture from the decoded waveform and a simulated ring.
Pixels as capsynth.py (random from/to states, phase shift 0..3). Frame f is written into ring buffer f % 16 over
WRITE_MS (random 1-6 ms) starting at f*T; shown LEAD frames later; wiped (if WIPE) one frame after that over WIPE_MS. Sweeps
come once per frame, read the 16 buffers one after another (BUF_MS each), randomly skip, and once go blind for
BLIND frames. A read during a write or wipe is torn: words up to the writer's progress new, the rest old. Each new
non-zero read is read again after the sweep, as capture.py v4 does (stable / recheck records).
Usage: capsynth4.py TOOLS WBF OUT MODE WIPE(0|1) [SEED]"""
import sys, zlib, json, struct, random, hashlib
sys.path.insert(0, sys.argv[1]); import eink
from capcheck import WIRE
W = eink.decode(open(sys.argv[2], 'rb').read()); mode, wipe = int(sys.argv[4]), int(sys.argv[5])
random.seed(int(sys.argv[6]) if len(sys.argv) > 6 else 1)
P = eink.phases(W, mode, 4); R, IDS = 16, list(range(37, 53))
T, WIPE_MS, BUF_MS, LEAD, SKIP, BLIND = 11.77e-3, 1e-3, 0.2e-3, 2, 0.15, 5
states = [0, 2, 28, 30]; npx = 405 * 8 * 2  # 2 lines of 405 words x 8 codes
pix = [(random.choice(states), random.choice(states), random.randrange(4)) for _ in range(npx)]
nfr = len(P) + 4; frames = []
for f in range(nfr):
    codes = [WIRE[P[f - sh][to][fr]] if 0 <= f - sh < len(P) else 0 for fr, to, sh in pix]
    frames.append(struct.pack('<%dI' % (npx // 8), *(sum(codes[8*i + j] << (3*j) for j in range(8)) for i in range(npx // 8))))
ZERO = bytes(len(frames[0]))
ev = {k: [] for k in range(R)}  # buffer -> [(start, end, content)] in time order
for f in range(nfr):
    ev[f % R].append((f * T, f * T + random.uniform(1e-3, 6e-3), frames[f]))
    if wipe: ev[f % R].append(((f + LEAD + 1) * T, (f + LEAD + 1) * T + WIPE_MS, ZERO))
for k in ev: ev[k].sort(key=lambda e: e[0])
def content(k, x):
    old = ZERO
    for a, b, c in ev[k]:
        if x < a: break
        if x < b:  # torn: the writer has reached a point proportional to elapsed time
            cut = 4 * int(len(c) // 4 * (x - a) / (b - a)); return c[:cut] + old[cut:]
        old = c
    return old
recs, blobs, last = [], [], [ZERO] * R
def add(r, data=b''): r['n'] = len(data); recs.append(r); blobs.append(data)
for k in range(R): add(dict(kind='base', u=0, s=0, fb=IDS[k], zero=True))
blind_at = random.randrange(10, nfr - 10); s = 0
for j in range(nfr + LEAD + 3):
    if random.random() < SKIP or blind_at <= j < blind_at + BLIND: continue
    s += 1; t0 = j * T + 0.5e-3; chg = 0; again = []
    for k in range(R):
        b = content(k, t0 + k * BUF_MS)
        if b != last[k]:
            last[k] = b; chg += 1; r = dict(kind='chg', u=1, s=s, fb=IDS[k], zero=b == ZERO); add(r, b'' if b == ZERO else b)
            if b != ZERO: again.append((k, r))
    for i, (k, r) in enumerate(again):
        b = content(k, t0 + (R + i) * BUF_MS)
        if b == last[k]: r['stable'] = True
        else: last[k] = b; chg += 1; add(dict(kind='chg', u=1, s=s, fb=IDS[k], zero=b == ZERO, recheck=True), b'' if b == ZERO else b)
    add(dict(kind='sweep', u=1, s=s, seq=2 * j, t=t0, rw=0, rc=0, chg=chg))
meta = dict(version=4, pitch=1620, h=1084, lines=[64, 192], fbs=IDS, recs=recs,
            truth=[hashlib.md5(f).hexdigest()[:8] for f in frames])  # for debugging the rebuild
open(sys.argv[3], 'wb').write(zlib.compress(json.dumps(meta).encode() + b'\n' + b''.join(blobs), 1))
print('frames', nfr, 'sweeps', s, 'records', len(recs), 'blind at frame', blind_at)
