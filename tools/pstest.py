"""Self-test for capcheck.ps_check: synthetic frames with the library's phase shift keyed on the source or the target,
random from/to states over black, white, the colour slots and the two greys, a common start 3 frames in, 2 % MISS plus the whole first drive frame MISS (the blind spot).
The true hypothesis must match 100 % with one start offset (timing fit 1.0); the others must fail on rate or offsets.
Usage: pstest.py TOOLS WBF|EINK"""
import sys, random
import numpy as np
tools, wbf = sys.argv[1:3]; sys.argv = [sys.argv[0], tools]; sys.path.insert(0, tools)
import capcheck as cc, eink
raw = open(wbf, 'rb').read(); W = raw if eink.verify(raw)['crc_ok'] else eink.decode(raw)  # .wbf or .eink
random.seed(7); ok = True
ST = [0, 2, 28, 30, 4, 12, 20, 8, 16, 24, 15, 19]
for band in (4, 5):
    for mode in cc.PS_MODES:
        P = eink.phases(W, mode, band); T = cc.ps_tables(W, band, mode)
        for truth in ('src', 'tgt'):
            px = [(random.choice(ST), random.choice(ST)) for _ in range(3000)]
            seqs = [cc.ps_extend([cc.WIRE[P[k][to][fr]] for k in range(len(P))], fr if truth == 'src' else to, band)
                    for fr, to in px]
            n = max(map(len, seqs)) + 6; F = np.zeros((n, len(px)), np.uint8)
            for j, s in enumerate(seqs): F[3:3 + len(s), j] = s
            F[np.random.default_rng(band).random(F.shape) < 0.02] = cc.MISS
            F[3] = cc.MISS  # the capture's blind spot: the first drive frame unseen
            res = {h: cc.ps_check(F, d) for h, d in T.items()}
            for h, (rate, offs, fit) in res.items():
                fits = rate > 0.999 and fit is not None and fit > 0.999 and offs.most_common(1)[0][0] == 3
                print('band %d %-8s truth %s  hyp %-4s rate %.4f timing fit %s offsets %s %s' % (band, cc.NAMES[mode],
                      truth, h, rate, fit if fit is None else round(fit, 4), dict(offs.most_common(3)), 'FITS' if fits else ''))
                if (h == truth) != fits: ok = False; print('   ^ WRONG')
print('PSTEST', 'PASS' if ok else 'FAIL')
