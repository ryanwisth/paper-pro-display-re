# Phase-shift test on one frame window of one capture update (2026-10-13, cap-204503). capdisp4's whole-update
# test fails when an update holds several overlapping drives (a page turn = erase + draw); a window that holds whole
# drives (entry + tail) and ends before the next drive answers it. Tail variant: B frames placed after a black
# target's waveform, nb6 of them wire code 6 and the rest 0 (cap-204503 read 5 + 1).
# Usage: python3 -I tools/pswin.py tools <.eink> <cap.z> <temp> <update> <lo> <hi>
import sys, collections
A = list(sys.argv); src = open(A[0].rsplit('/', 1)[0] + '/capdisp4.py').read()
exec(src[:src.index('# Phase-shift test')])  # rebuild + per-update report (prints as capdisp4 does)
U, lo, hi = int(A[5]), int(A[6]), int(A[7]); STD = cc.NAMES.index('STANDARD'); P = eink.phases(W, STD, band)
def table(hyp, nb6):
    d = {}
    for to in range(32):
        for fr in range(32):
            s = [cc.WIRE[P[k][to][fr]] for k in range(len(P))]; k = (fr if hyp == 'src' else to) & ~2
            if hyp != 'none':
                a, b = cc.PS_A[band], cc.PS_B[band]
                if k == 28: s = s + [0] * (a + b)
                elif k == 0: s = [0] * a + s + [6] * min(nb6, b) + [0] * (b - min(nb6, b))
                else: s = [0] * (a + b) + s
            s, lead = cc.strip(s); d.setdefault(s, []).append((fr, to, lead))
    return d
F, _ = cc.frames_of(dict(recs=out), U, False); Fw = F[lo:hi]
print('== window update %d frames %d-%d, band %d, STANDARD %d phases' % (U, lo, hi - 1, band, len(P)))
for hyp, nb6 in (('none', 0), ('src', 6), ('src', 5), ('tgt', 6), ('tgt', 5), ('tgt', 0)):
    rate, offs, fit = cc.ps_check(Fw, table(hyp, nb6))
    print('%-4s code-6 tail %d: rate %.4f timing %s start offsets %s' % (hyp, nb6, rate, 'n/a' if fit is None else '%.4f' % fit, dict(offs.most_common(3))))
