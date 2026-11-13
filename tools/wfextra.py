"""Paper Pro WBF extras (2026-10-13). Stdlib only.
Usage: python3 -I wfextra.py <tools dir> <panel.wbf or .eink> [band]
Prints:
  1. per-waveform header check + voltage block per mode/band
     (16-byte header sits BEFORE each waveform body: 01 80 15 00, 3-byte ptr + sum,
      7 zero bytes, sum of bytes 0..14; the byte after each 0xFF is sum(body incl. 0xFF);
      the ptr leads to a 21-byte block: u16 0x8001, 8 x u16 in 12.5 mV units, u16 0, sum byte)
  2. round-trip charge balance with each mode's own black/white targets (table 0x690b0)
  3. per-transition |net| statistics over the states the library can produce
Voltage per drive value is *Inference*: block order = file values 1,2,3 -> 12, 6, 24 V
(so wire codes 4,5,6 -> 6,12,24 V and 3,2,1 -> the mirrored side)."""
import sys, struct, collections, statistics
sys.path.insert(0, sys.argv[1])
import eink

raw = open(sys.argv[2], 'rb').read()
w = eink.decode(raw) if sys.argv[2].endswith('.eink') else raw
band = int(sys.argv[3]) if len(sys.argv) > 3 else 4
h = eink.header(w); P = eink.mode_temp_ptrs(w)
NAMES = 'INIT PEN STANDARD TEXT TEXT_GLR FAST BEST A2 PMODE'.split()
# target-state table rows from libqsgepaper.so 0x690b0, slots 0..11 (12..15 unused = 32)
TAB = {'PEN': [0,14,6,22,10,18,26,30,15,19,0,30], 'STANDARD': [2,12,4,20,8,16,24,28,15,19,2,30],
       'TEXT': [0,30,15,19,0,30], 'TEXT_GLR': [0,30,15,19,0,30],
       'FAST': [2,13,5,21,9,17,25,28,15,19,0,30], 'BEST': [2,12,4,20,8,16,24,28,15,19,2,28], 'A2': [2,28]}
V = {1: 12, 2: 6, 3: 24, 14: -12, 13: -6, 12: -24}   # + = toward black (code 6 -> black, write attempt 2)

print('== 1. headers and voltage blocks; bands', h['temp_bounds'])
st = collections.Counter(); n = 0
for m in range(h['modes']):
    rows = []
    for t in range(h['temps']):
        p = P[m][t]; r = w[p-16:p]; _, end = eink.unrle(w[p:]); n += 1
        st['body_sum'] += w[p+end+1] == sum(w[p:p+end+1]) & 255
        st['hdr_fixed'] += r[:4] == b'\x01\x80\x15\x00'
        st['hdr_ptr_sum'] += sum(r[4:7]) & 255 == r[7]
        st['hdr_zero'] += not any(r[8:15])
        st['hdr_sum'] += sum(r[:15]) & 255 == r[15]
        x = r[4] | r[5] << 8 | r[6] << 16; b = w[x:x+21]
        st['blk_sum'] += sum(b[:20]) & 255 == b[20]
        u = struct.unpack_from('<10H', b); v = [f'{k / 80:g}' for k in u[1:9]]
        rows.append(f'{u[0]:#06x} A({v[0]},{v[1]},{v[2]}) B({v[3]},{v[4]},{v[5]}) gate({v[6]},{v[7]})'
                    + (f' w9={u[9]}' if u[9] else ''))
    name = NAMES[m] if h['modes'] == 9 else str(m)
    print(f'{name:9s}', ' | '.join(f'{k} (t{",".join(str(i) for i, o in enumerate(rows) if o == k)})'
                                   for k in dict.fromkeys(rows)))
print({k: f'{v}/{n}' for k, v in st.items()})
if h['modes'] != 9: sys.exit()

ph = {NAMES[m]: eink.phases(w, m, band) for m in range(9)}
def seq(m, fr, to): return [f[to][fr] for f in ph[m]]          # phases are [target][source]
def imp(s): return sum(V.get(x, 0) for x in s)
def lev(s):
    d = [0, 0, 0]
    for x in s:
        if x: d[{1: 0, 14: 0, 2: 1, 13: 1, 3: 2, 12: 2}[x]] += 1 if x < 4 else -1
    return d

print(f'\n== 2. round trip white(28) -> own black -> own white, band {band}, volt-frames (+ = toward black)')
for m, tg in TAB.items():
    b, wt = tg[0], tg[7] if len(tg) > 7 else tg[1]
    a, c = seq(m, 28, b), seq(m, b, wt)
    print(f'{m:9s} 28>{b}: {imp(a):5d}  {b}>{wt}: {imp(c):5d}  net {imp(a)+imp(c):5d}  '
          f'per-value-pair net [1/14, 2/13, 3/12] {[x + y for x, y in zip(lev(a), lev(c))]}')

print(f'\n== 3. per-transition net over producible states, band {band}')
produced = sorted({s for v in TAB.values() for s in v} | {29, 31})
for m, tg in TAB.items():
    nets, tot = [], []
    for fr in produced:
        for to in sorted(set(tg)):
            s = seq(m, fr, to)
            if fr == to or not any(s): continue
            nets.append(imp(s)); tot.append(sum(abs(V.get(x, 0)) for x in s))
    print(f'{m:9s} n={len(nets):3d} median |net| {statistics.median(map(abs, nets)):5.0f} V-frames, '
          f'median |net|/total {statistics.median(abs(a) / b for a, b in zip(nets, tot)):.2f}, max {max(map(abs, nets))}')
