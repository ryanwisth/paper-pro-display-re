# v4 capture event view (no rebuild): per sweep, W=written R=recheck Z=zeroed by ring position. Usage: capevents4.py CAP.z UPDATE
import sys, zlib, json, collections
raw = zlib.decompress(open(sys.argv[1], 'rb').read()); nl = raw.index(b'\n')
meta = json.loads(raw[:nl]); u = int(sys.argv[2]); P = {f: i for i, f in enumerate(meta['fbs'])}
ev = collections.defaultdict(list); swt = {}
for r in meta['recs']:
    if r.get('u') != u: continue
    if r['kind'] == 'sweep': swt[r['s']] = r['t']
    elif r['kind'] == 'chg': ev[r['s']].append(('Z' if r['zero'] else 'R' if r.get('recheck') else 'W') + str(P[r['fb']]) + ('' if r['zero'] or r.get('stable') or r.get('recheck') else '?'))
S = sorted(swt); t0 = swt[S[0]]; prev = None; out = []
for s in S:
    if not ev.get(s): continue
    gap = '' if prev is None else ' (+%d)' % (s - prev)
    out.append('%d%s %s' % (s, gap, ' '.join(ev[s]))); prev = s
print('\n'.join(out))
# summary: zero events per sweep, write batch sizes
zs = [s for s in S for e in ev.get(s, []) if e[0] == 'Z']; ws = collections.Counter(sum(e[0] == 'W' for e in ev[s]) for s in S if any(e[0] == 'W' for e in ev.get(s, [])))
print('zero events', len(zs), 'sweeps with >1 zero:', sum(1 for s in set(zs) if zs.count(s) > 1), '| write batch sizes', dict(sorted(ws.items())))
print('unstable writes (?):', sum(e.endswith('?') for s in S for e in ev.get(s, [])))
