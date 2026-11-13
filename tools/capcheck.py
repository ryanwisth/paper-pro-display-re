"""Check a capture.py file against the decoded waveform (numpy).
Per update: per-pixel code sequences (sampled lines) -> align on the first non-zero frame ->
strip trailing zeros -> look up in every mode's table at the capture temperature band."""
import sys, zlib, json, collections
import numpy as np
sys.path.insert(0, sys.argv[1]); import eink
WIRE = {0: 0, 1: 5, 2: 4, 3: 6, 12: 1, 13: 3, 14: 2}
NAMES = 'INIT PEN STANDARD TEXT TEXT_GLR FAST BEST A2 PMODE'.split()
def strip(s):
    s = list(s)
    while s and s[-1] == 0: s.pop()
    i = 0
    while i < len(s) and s[i] == 0: i += 1
    return tuple(s[i:]), i
def tables(W, temp_c):
    h = eink.header(W); b = h['temp_bounds']
    t = max(i for i in range(h['temps']) if b[i] <= temp_c)
    tabs = {}
    for m in range(h['modes']):
        P = eink.phases(W, m, t); d = {}
        for to in range(32):
            for fr in range(32):
                s, lead = strip(WIRE[P[k][to][fr]] for k in range(len(P)))
                d.setdefault(s, []).append((fr, to, lead))
        tabs[m] = (d, len(P))
    return t, tabs
def load(path, drop_unconfirmed=False):
    raw = zlib.decompress(open(path, 'rb').read()); nl = raw.index(b'\n')
    meta = json.loads(raw[:nl]); body = raw[nl+1:]; pos = 0
    for r in meta['recs']:
        r['data'] = body[pos:pos + r['n']]; pos += r['n']
    if meta.get('version') == 4: rebuild_v4(meta, drop_unconfirmed)
    return meta
def partial_wipe(prev, new):
    a, b = np.frombuffer(prev, '<u4'), np.frombuffer(new, '<u4'); dif = a != b
    return bool(dif.any() and (b[dif] == 0).all())
def rebuild_v4(meta, drop_unconfirmed=False):
    """v4 raw sweeps -> v3-style frame records (meta['recs']); per-update counts in meta['v4stats'].
    Rules: a change to all-zero = wipe after display (ignored); a change whose every differing word became 0 =
    wipe caught mid-way (ignored); a change in the newest frame's buffer = that frame finished writing after a
    torn read (replaces it); 1..R/2 ring steps ahead = a new frame, the steps between = MISS (unseen or all-zero
    on the sampled lines); full laps come from timing (median time per ring step; a re-change of the newest
    buffer is a lap only after >= 3/4 ring of time, a buffer behind it is new only if the time covers it); a change behind the newest
    frame = 'odd' (replaces that buffer's latest frame; should stay 0 apart from rechecks, else the ring model is
    wrong). A frame is confirmed (not torn) when its last read was stable on the tablet's re-read or its buffer
    then stayed unchanged for at least one whole sweep; drop_unconfirmed turns the others into MISS."""
    ids = meta['fbs']; R = len(ids); pos = {f: i for i, f in enumerate(ids)}
    cur, sweeps, order, lastrec = {}, {}, [], {}
    for r in meta['recs']:
        if r['kind'] == 'chg':  # each read learns the sweep of its buffer's next change
            if r['fb'] in lastrec: lastrec[r['fb']]['next_s'] = r['s']
            lastrec[r['fb']] = r
    for r in meta['recs']:
        if r['kind'] == 'base': cur[r['fb']] = r['data']
        elif r['kind'] in ('chg', 'sweep'):
            sw = sweeps.setdefault((r['u'], r['s']), dict(chg=[]))
            if r['kind'] == 'chg': sw['chg'].append(r)
            else: sw['t'] = r['t']; order.append((r['u'], r['s']))
    stats = collections.defaultdict(collections.Counter); per_u = collections.defaultdict(list)
    for u, s in order:
        sw = sweeps[(u, s)]; items = []
        for r in sw['chg']:
            prev = cur.get(r['fb'], b''); cur[r['fb']] = r['data']
            if r.get('recheck'): stats[u]['recheck'] += 1
            if r['zero']: stats[u]['wipe'] += 1
            elif prev and partial_wipe(prev, r['data']): stats[u]['partial_wipe'] += 1
            else: items.append((pos[r['fb']], r['data'], r.get('stable') or r.get('next_s', s + 2) >= s + 2))
        per_u[u].append((sw['t'], items))
    def walk(sws, step, out, info, st):
        p = tp = None; last = {}; deltas = []
        for t, items in sws:
            p0 = p
            if p0 is not None:  # ring order from the newest frame: up to R/2 ahead, else behind; all ahead after a long gap
                far = step and (t - tp) / step >= R / 2
                items = sorted(items, key=lambda it: (it[0] - p0) % R if far else (it[0] - p0 + R // 2 - 1) % R - (R // 2 - 1))
            for q, data, ok in items:
                if p is not None:
                    dd = (q - p) % R; est = (t - tp) / step if step else 0
                    if (dd == 0 and est < R * 0.75) or (dd > R // 2 and est < dd - R / 4):  # too soon for the ring to get there
                        if dd and q not in last: st['odd_dropped'] += 1; continue
                        st['torn' if dd == 0 else 'odd'] += 1; out[last[q]] = data; info[last[q]] = ok; continue
                    lap = max(round((est - dd) / R), 1 if dd == 0 else 0)
                    if 0 < dd <= 4 and t > tp: deltas.append((t - tp) / dd)
                    st['laps'] += lap; gap = dd - 1 + R * lap; st['missed'] += gap
                    out.extend([None] * gap); info.extend([None] * gap)
                out.append(data); info.append(ok); last[q] = len(out) - 1; p, tp = q, t; st['new'] += 1
        return deltas
    recs = []
    for u, sws in per_u.items():
        d = walk(sws, None, [], [], collections.Counter())  # pass 1: time per ring step, no lap correction
        step = sorted(d)[len(d) // 2] if d else None; out, info = [], []; walk(sws, step, out, info, stats[u])
        stats[u]['step_ms'] = round(step * 1e3, 2) if step else 0
        for i, ok in enumerate(info):
            if ok is False:
                stats[u]['unconfirmed'] += 1
                if drop_unconfirmed: out[i] = None
        i = 0
        while i < len(out):
            if out[i] is None:
                j = i
                while j < len(out) and out[j] is None: j += 1
                recs.append(dict(u=u, kind='missed', count=j - i, data=b'')); i = j
            else: recs.append(dict(u=u, kind='vbl', data=out[i])); i += 1
    meta['recs'] = recs; meta['v4stats'] = {u: dict(c) for u, c in stats.items()}
def codes(data):
    w = np.frombuffer(data, '<u4')
    return ((w[:, None] >> (3 * np.arange(8, dtype=np.uint32))) & 7).astype(np.uint8).ravel()
MISS = 255  # a frame the capture could not read in time (v2 'missed' records); matches any code
def frames_of(meta, u, with_repeats):
    fr, kinds = [], []
    for r in meta['recs']:
        if r['u'] != u: continue
        if r['kind'] == 'repeat':
            if with_repeats and fr: fr.append(fr[-1]); kinds.append('repeat')
            continue
        if r['kind'] == 'missed':
            for _ in range(r.get('count', 1)): fr.append(None); kinds.append('missed')
            continue
        fr.append(codes(r['data'])); kinds.append(r['kind'])
    n = next(len(f) for f in fr if f is not None)
    return np.stack([np.full(n, MISS, np.uint8) if f is None else f for f in fr]), kinds
def pixel_columns(F):
    """-> Counter{raw per-pixel sequence tuple: pixel count}, active pixels only."""
    act = ((F != 0) & (F != MISS)).any(0)
    uniq, cnt = np.unique(F[:, act].T, axis=0, return_counts=True)
    return {tuple(int(x) for x in row): int(c) for row, c in zip(uniq, cnt)}
def matches(px, e):
    """Does raw pixel sequence px (with MISS wildcards) equal zeros + entry e + zeros for some start?"""
    first = next(i for i, x in enumerate(px) if x not in (0, MISS))
    j = first
    while j > 0 and px[j - 1] == MISS: j -= 1
    for p in range(j, first + 1):
        if p + len(e) > len(px): break
        if any(x not in (0, MISS) for x in px[:p]): break
        if all(px[p + k] in (e[k], MISS) for k in range(len(e))) and all(x in (0, MISS) for x in px[p + len(e):]):
            return True
    return False
def check(meta, W, temp_c, with_repeats=False):
    t, tabs = tables(W, temp_c); res = []
    for u in sorted({r['u'] for r in meta['recs']}):
        F, kinds = frames_of(meta, u, with_repeats)
        cols = pixel_columns(F); active = sum(cols.values())
        per_mode, unmatched = {}, {}
        for m, (d, _) in tabs.items():
            ents = [e for e in d if e]; ok = 0; bad = collections.Counter()
            for px, c in cols.items():
                if MISS not in px:
                    s, _ = strip(px)
                    if s in d: ok += c; continue
                    if not any(MISS == x for x in px): bad[px] += c; continue
                if any(matches(px, e) for e in ents): ok += c
                else: bad[px] += c
            per_mode[NAMES[m]] = ok / active if active else 0; unmatched[NAMES[m]] = bad
        best = max(per_mode, key=per_mode.get)
        res.append(dict(update=u, frames=len(F), kinds=dict(collections.Counter(kinds)), active_px=active,
                        unique_cols=len(cols), band=t, best=best, best_rate=round(per_mode[best], 4),
                        per_mode={k: round(v, 4) for k, v in per_mode.items()}, unmatched=unmatched[best]))
    return res, tabs
if __name__ == '__main__':
    W = eink.decode(open(sys.argv[2], 'rb').read()); meta = load(sys.argv[3]); temp_c = float(sys.argv[4])
    for u, c in meta.get('v4stats', {}).items(): print('v4 rebuild update', u, c)
    if meta.get('version') == 4:  # v4 has no repeat records; report with unconfirmed frames kept, then as MISS
        runs = [('v4-kept', meta, False), ('v4-unconfirmed=MISS', load(sys.argv[3], True), False)]
    else: runs = [('no-repeats', meta, False), ('repeats', meta, True)]
    for label, m, rep in runs:
        res, _ = check(m, W, temp_c, rep)
        for r in res:
            print(label, {k: v for k, v in r.items() if k != 'unmatched'})
            for px, c in r['unmatched'].most_common(3): print('    unmatched n=%d %s' % (c, ''.join('x' if x == MISS else str(x) for x in px)[:160]))
# libqsgepaper's load-time phase shift (2026-10-13 night, read from a disassembly of libqsgepaper.so, 0x50504-0x50b60; skipped when the
# env var SWTCON_NO_PHASE_SHIFT is set, 0x50d74). Modes 2 and 4 (STANDARD, TEXT_GLR; list at 0x5ea28) get, per band,
# A = 0x69140 and B = 0x69168 extra frames. The code tests ONE index of each transition (state & ~2): 28 -> A+B zeros
# after; 0 -> A zeros before and B frames of value 6 after; anything else -> A+B zeros before. Which index it tests
# (source or target) and whether 6 is wire code 6 are UNKNOWN here, so the checker tries both and reads code 6.
PS_MODES, PS_A, PS_B = (2, 4), (0, 0, 0, 0, 0, 24, 32, 40, 50), (6, 6, 6, 6, 6, 0, 0, 0, 0)
def ps_extend(seq, key, band):
    a, b = PS_A[band], PS_B[band]; k = key & ~2
    if k == 28: return list(seq) + [0] * (a + b)
    if k == 0: return [0] * a + list(seq) + [6] * b
    return [0] * (a + b) + list(seq)
def ps_tables(W, band, mode):
    """-> {'none'|'src'|'tgt': table as tables() builds}, the shift keyed on the source or the target state."""
    P = eink.phases(W, mode, band); out = {}
    for hyp in ('none', 'src', 'tgt'):
        d = {}
        for to in range(32):
            for fr in range(32):
                s = [WIRE[P[k][to][fr]] for k in range(len(P))]
                if hyp != 'none': s = ps_extend(s, fr if hyp == 'src' else to, band)
                s, lead = strip(s); d.setdefault(s, []).append((fr, to, lead))
        out[hyp] = d
    return out
def starts(px, e):
    """All start frames p at which px (MISS = wildcard) equals zeros + e + zeros (matches() stops at the first)."""
    first = next(i for i, x in enumerate(px) if x not in (0, MISS)); j = first; out = []
    while j > 0 and px[j - 1] == MISS: j -= 1
    for p in range(j, first + 1):
        if p + len(e) > len(px) or any(x not in (0, MISS) for x in px[:p]): break
        if all(px[p + k] in (e[k], MISS) for k in range(len(e))) and all(x in (0, MISS) for x in px[p + len(e):]):
            out.append(p)
    return out
def ps_check(F, d):
    """-> (match rate, Counter of start offsets, timing fit). A matched pixel's offset = its start frame - the entry's
    lead (MISS frames: every start that matches). A pixel with several candidate offsets (shared sequences, or MISS
    at the start) fits if the commonest single-offset value is among them. Timing fit = share of matched pixels that
    fit (1.0 = one start frame for the whole update, as the table's leads predict)."""
    cols = pixel_columns(F); active = sum(cols.values()); ok = 0; offs, amb = collections.Counter(), collections.Counter()
    ents = [e for e in d if e]
    for px, c in cols.items():
        if MISS not in px:
            s, first = strip(px); hits = [(first, s)] if s in d else []
        else: hits = [(p, e) for e in ents for p in starts(px, e)]
        if not hits: continue
        ok += c; cand = frozenset(p - lead for p, e in hits for _, _, lead in d[e])
        if len(cand) == 1: offs[next(iter(cand))] += c
        else: amb[cand] += c
    top = offs.most_common(1)[0][0] if offs else None; timed = sum(offs.values()) + sum(amb.values())
    fit = (offs[top] + sum(c for k, c in amb.items() if top in k)) / timed if timed and top is not None else None
    return (ok / active if active else 0), offs, fit  # fit None: no pixel pins an offset (one-kind update)
