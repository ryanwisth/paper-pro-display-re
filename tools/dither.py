"""Paper Pro colour dither, re-implemented from libqsgepaper.so 3.27.3 (2026-10-13).
Stdlib only. Usage: python3 -I dither.py <libqsgepaper.so> <ct33_*.bin> [R G B ...]
Kernel 0x47f10, called 8 pixels at a time from 0x48a30 / 0x487c0 / 0x4d560:
  grid  = channel >> 3, frac = channel & 7, and 255 counts as frac 8 (cmeq 0xFF)
  ct33  = 33^3 entries of 8 bytes, index R*1089 + G*33 + B; each entry is a cumulative
          distribution over the 8 cube corners (slot = 4G + 2R + B), last byte always 255
  interp: tetrahedral; vertex walk from table 0x5ebb0 (nibbles = axis sets, bit0 B, bit1 G, bit2 R),
          weights (8 - max, max - mid, mid - min, min) -> cumulative values 0..2040
  slot  = number of cumulative values <= t, t = u16 blue-noise matrix 0x633c0[y & 63][x & 63] (0..2039,
          rows padded to 72 with their first 8 values); the 8-bit grey matrix 0x621c0 is the same
          pattern coarsened. R = G = B = 255 also sets flag 0x80.
  0x487c0 variant: pixels with a nonzero mask byte skip the dither and get {10, 8, 9, 11}[(77R+150G+29B) >> 14]
  0x4d560 variant: pixels with a zero mask byte get slot 15.
Slots map to states through the target-state table 0x690b0 (see wfextra.py)."""
import sys, struct, collections

def load(so, ct):
    d = open(so, 'rb').read()
    ph, n = struct.unpack_from('<Q', d, 0x20)[0], struct.unpack_from('<H', d, 0x38)[0]
    segs = [struct.unpack_from('<IIQQQQQQ', d, ph + 56 * i) for i in range(n)]
    def at(va, size):
        for t, _, o, v, _, fs, _, _ in segs:
            if t == 1 and v <= va < v + fs: return d[o + va - v:o + va - v + size]
    u = struct.unpack('<4608H', at(0x633c0, 9216))
    return [u[r * 72:r * 72 + 64] for r in range(64)], at(0x5ebb0, 8), open(ct, 'rb').read()

def colour_slot(M, walk, ct, R, G, B, x, y):
    g = [c >> 3 for c in (R, G, B)]
    f = [(c & 7) + (c == 255) for c in (R, G, B)]
    fr, fg, fb = f
    w = walk[4 * (fg > fb) | 1 * (fb > fr) | 2 * (fr > fg)]
    hi, lo = max(f), min(f); mid = sum(f) - hi - lo
    verts = [0, w & 15, w >> 4, 7]                       # axis sets: bit0 B, bit1 G, bit2 R
    cum = [0] * 8
    for v, wt in zip(verts, (8 - hi, hi - mid, mid - lo, lo)):
        if not wt: continue
        i = ((g[0] + (v >> 2 & 1)) * 1089 + (g[1] + (v >> 1 & 1)) * 33 + g[2] + (v & 1)) * 8
        for k in range(8): cum[k] += wt * ct[i + k]
    t = M[y & 63][x & 63]
    return sum(c <= t for c in cum) | (0x80 if R == G == B == 255 else 0)

def grey_mask_slot(R, G, B):
    return (10, 8, 9, 11)[(77 * R + 150 * G + 29 * B) >> 14]

if __name__ == '__main__':
    M, walk, ct = load(sys.argv[1], sys.argv[2])
    NAMES = 'K B R M G C Y W'.split()                   # slot = 4G + 2R + B
    def mix(R, G, B):
        h = collections.Counter(colour_slot(M, walk, ct, R, G, B, x, y) & 15 for y in range(64) for x in range(64))
        return ' '.join(f'{NAMES[s]}{100 * h[s] / 4096:.0f}%' for s in range(8) if h[s])
    # self-check: at grid colours the slot mix over the 64x64 matrix must equal the ct33 entry
    bad = 0
    for R in range(0, 257, 32):
        for G in range(0, 257, 64):
            for B in range(0, 257, 128):
                r, gg, b = (min(c, 255) for c in (R, G, B))
                e = ct[((r >> 3) + (r == 255)) * 1089 * 8 + ((gg >> 3) + (gg == 255)) * 33 * 8 + ((b >> 3) + (b == 255)) * 8:][:8]
                h = collections.Counter(colour_slot(M, walk, ct, r, gg, b, x, y) & 15 for y in range(64) for x in range(64))
                exp = [e[0]] + [e[k] - e[k - 1] for k in range(1, 8)]
                bad += any(abs(h[s] / 4096 - exp[s] / 255) > 0.002 for s in range(8))
    print(f'self-check: grid colours whose dithered mix differs from ct33 by > 0.2 %: {bad}/90')
    rgbs = [tuple(map(int, sys.argv[i:i + 3])) for i in range(3, len(sys.argv), 3)] or \
        [(255, 255, 255), (0, 0, 0), (128, 128, 128), (192, 192, 192), (255, 0, 0), (128, 128, 255), (255, 160, 0)]
    for c in rgbs: print(f'{str(c):16s} {mix(*c)}')
