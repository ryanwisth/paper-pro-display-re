import sys, json
sys.path.insert(0, sys.argv[1])
import eink
w = eink.decode(open(sys.argv[2], 'rb').read())
open(sys.argv[3] + '/panel.wbf', 'wb').write(w)
print(eink.verify(w))
h = eink.header(w); print({k: v for k, v in h.items()})
names = 'INIT PEN STANDARD TEXT TEXT_GLR FAST BEST A2 PMODE'.split()
b = h['temp_bounds']
print('bands:', [f'{b[i]}-{b[i+1]}' for i in range(h['temps'])])
tab = {}
for m in range(h['modes']):
    row = []
    for t in range(h['temps']):
        p = eink.phases(w, m, t); row.append(len(p))
    tab[names[m]] = row
    print(f'{names[m]:9s}', row)
json.dump(tab, open(sys.argv[3] + '/phase_counts.json', 'w'))
