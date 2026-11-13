"""Display-order rebuild of a v4 capture, then the capcheck mode check (numpy). Replaces rebuild_v4's ring model.
Measured on cap-181321 (2026-10-13 18:13): xochitl uses 15 ring buffers (37-51; 52 never carries data), writes
frames ahead in batches (mostly 5, up to all 15 after a pause), and scan-out steps ONE buffer per vblank, zeroing
each one as it is shown (214 of 215 zero events fit one fixed offset). So the frame shown at vblank k is ring
buffer (k + off) mod R with the content of its last write BEFORE that sweep (a write seen in the same sweep is a
future frame). Frame index comes from kernel timestamps (median gap); sweeps whose timestamp is 0 (WAIT_VBLANK
right after pipe-on) take their neighbour's index plus the sweep distance.
Blind spot: when the queue is empty (pipe-on, or a full-ring re-render after a pause), xochitl writes the first
frame into the buffer shown at the very next vblank, and it is shown and zeroed before the sweep reads it. On
cap-181321 every pixel that started there was its table entry minus frame 1 (stroke: 15 px; pipe-on `2,2` vs
the `2,2,2` white->29 entry). Rule: when a sweep already holds the NEXT frame, the frame shown at that sweep
is MISS (wildcard), whatever was last read there. The summary prints each pixel group so a wrong rule stays visible.
Usage: capdisp4.py TOOLS WBF CAP.z TEMP_C"""
import sys, zlib, json, collections
import numpy as np
tools, wbf, cap, temp = sys.argv[1:5]; sys.argv = [sys.argv[0], tools]
sys.path.insert(0, tools); import capcheck as cc, eink
raw = zlib.decompress(open(cap, 'rb').read()); nl = raw.index(b'\n')
meta = json.loads(raw[:nl]); body = raw[nl+1:]; pos = 0
for r in meta['recs']: r['data'] = body[pos:pos + r['n']]; pos += r['n']
assert meta.get('version') == 4, 'v4 captures only'
P = {f: i for i, f in enumerate(meta['fbs'])}; ZERO = bytes(len(meta['lines']) * meta['pitch'])
ring = sorted({P[r['fb']] for r in meta['recs'] if r['kind'] == 'chg'}); R = len(ring); RI = {q: i for i, q in enumerate(ring)}
print('ring buffers used: %s (R=%d)' % ([meta['fbs'][q] for q in ring], R))
cont = {P[r['fb']]: (r['data'] or ZERO) for r in meta['recs'] if r['kind'] == 'base'}
out = []
for u in sorted({r['u'] for r in meta['recs'] if r['kind'] != 'base'}):
    sw = {r['s']: r for r in meta['recs'] if r['kind'] == 'sweep' and r['u'] == u}; S = sorted(sw)
    ev = collections.defaultdict(list)
    for r in meta['recs']:
        if r['kind'] == 'chg' and r['u'] == u: ev[r['s']].append(r)
    good = [s for s in S if sw[s]['seq'] is not None and sw[s]['t'] > 1]
    gaps = sorted(sw[b]['t'] - sw[a]['t'] for a, b in zip(good, good[1:]) if b == a + 1); fr = gaps[len(gaps) // 2]
    k = {s: round((sw[s]['t'] - sw[good[0]]['t']) / fr) for s in good}
    for s in S:
        if s not in k:
            prv = max((x for x in k if x < s), default=None); k[s] = k[prv] + s - prv if prv is not None else 0
    offs = collections.Counter((RI[P[r['fb']]] - k[s]) % R for s in S for r in ev[s] if r['zero'])
    print('update %d: frame %.3f ms, %d sweeps, zero-event offsets %s' % (u, fr * 1e3, len(S), dict(offs.most_common(3))))
    if not offs:  # nothing shown with data on the sampled lines
        for s in S:
            for r in ev[s]: cont[P[r['fb']]] = r['data'] or ZERO
        continue
    off = offs.most_common(1)[0][0]; by_k = {k[s]: s for s in S}; blind = 0
    for kk in range(k[S[0]], k[S[-1]] + 1):
        e = ev.get(by_k.get(kk), []); q, q1 = ring[(kk + off) % R], ring[(kk + 1 + off) % R]
        # blind spot: this sweep already holds the NEXT frame (q1), so the queue had run dry and the burst most
        # likely (re)wrote q too, after our last read of it, and q was shown and zeroed unseen -> unknown, not
        # whatever we last read (blank at pipe-on; a stale pre-burst copy at the k259 re-render of cap-181321)
        if any(not r['zero'] and P[r['fb']] == q1 for r in e):
            out.append(dict(u=u, kind='missed', count=1, data=b'')); blind += 1
        else: out.append(dict(u=u, kind='vbl', data=cont[q]))
        for r in e: cont[P[r['fb']]] = r['data'] or ZERO
    print('   blind-spot frames marked MISS: %d' % blind)
W = eink.decode(open(wbf, 'rb').read()); band, tabs = cc.tables(W, float(temp))
res, _ = cc.check(dict(recs=out), W, float(temp))
for r in res:
    print('display-order', {x: v for x, v in r.items() if x not in ('unmatched', 'per_mode')}, 'nonzero modes',
          {m: v for m, v in r['per_mode'].items() if v})
    F, _ = cc.frames_of(dict(recs=out), r['update'], False); d = tabs[cc.NAMES.index(r['best'])][0]
    groups = collections.Counter()
    for p in np.nonzero(((F != 0) & (F != cc.MISS)).any(0))[0]:
        px = tuple(int(x) for x in F[:, p]); i = next(j for j, x in enumerate(px) if x not in (0, cc.MISS))
        s_ = px[i - 1 if i and px[i - 1] == cc.MISS else i:]  # keep one MISS just before the first code
        hit = tuple(e for e in d if e and cc.matches(px, e))
        groups[(hit, ''.join('x' if x == cc.MISS else str(x) for x in s_).rstrip('0x'))] += 1
    for (hit, s_), c in sorted(groups.items(), key=lambda g: (bool(g[0][0]), -g[1]))[:8]:  # unmatched first
        to = sorted({t for e in hit for _, t, _ in d[e]}) if hit else '-'
        print('   %-9s n=%-5d len=%-3d to-states=%s %s' % ('match' if hit else 'UNMATCHED', c, len(s_), to, s_[:60]))
# Phase-shift test (2026-10-13 night; capcheck.ps_*; self-test tools/pstest.py): per update, STANDARD and TEXT_GLR
# with no shift / the library's shift keyed on the source / on the target. The true one has rate ~1 AND timing ~1.
PT = {m: cc.ps_tables(W, band, m) for m in cc.PS_MODES}
for r in res:
    F, _ = cc.frames_of(dict(recs=out), r['update'], False)
    for m, hs in PT.items():
        cells = []
        for h, d in hs.items():
            rate, offs, fit = cc.ps_check(F, d)
            cells.append('%s rate %.4f timing %s %s' % (h, rate, 'n/a' if fit is None else '%.4f' % fit, dict(offs.most_common(2))))
        print('   phase shift %-8s %s' % (cc.NAMES[m], ' | '.join(cells)))
