"""Phase 3 writer, Mac side. v1 2026-10-13 (mono); v2 (colour); v3 (draw modes, partial updates, grey swatches).
v3 rebuilds the v1 and v2 frames byte for byte; only v3 is published.
Builds a frame plan for tablet/writer.py from the panel's own waveform file. No reMarkable code runs; the colour path reads
constant tables out of libqsgepaper.so (target-state table 0x690b0; blue-noise matrix 0x633c0 via dither.py) and ct33_std.bin.
Each session: pipe on with a zero frame ... 2 zero frames, pipe off; the image stays during the hold (e-paper is bistable).
Mono (testcard, or a PNG + threshold), as v1:
  session 1: INIT, any state -> white 28 (80 x value 3, 80 x value 12, = U-Boot's clear), then A2 28 -> black 2 on ink
             pixels (10 x value 3); paper gets A2 28 -> 28 (no drive)
  session 2: A2 2 -> 28 on ink (10 x value 12), so every pixel's run nets zero per value
Colour (colourcard, or a PNG + 'colour'):
  session 1: INIT, then DRAW mode 28 -> each pixel's target state. Ink per pixel = slot 0-7 (RGB-cube corner, 4G + 2R + B),
             flat for swatches or from dither.py's colour dither (ct33 + blue noise), or slot 8/9 (grey states, card only);
             slot -> state via the DRAW row of 0x690b0. The build checks that DRAW drives sources 27-31 alike for every
             target used, so INIT's own label (30) is moot.
  session 2 (only with THEN): partial update to a second picture, same DRAW mode, source = the first picture's state per
             pixel; pixels whose state does not change get no drive. No INIT in between.
  last session: the erase with the smallest worst-pixel net drive over the whole run: INIT, DRAW back to its own white,
             or STANDARD back to 28 (plan.json erase_choice).
Pixel groups = distinct state histories (white, picture 1[, picture 2]); one frame per phase holds a code per group.
Frame layout (report 3.1, confirmed on the panel by write attempt 2): 405 x 1084 XRGB8888 words, lines 0-3 spare;
word = 4 columns x 2 rows, code k in bits 3k..3k+2 at row 2(line-4) + (k & 1), column 4 word + (k >> 1).
Value -> wire code (report 3.2): 0:0 1:5 2:4 3:6 12:1 13:3 14:2. Code-6 fills also fill the spare lines (as U-Boot's fill);
every other frame leaves them 0.
Usage: python3 -I mkwriter.py <tools dir> <panel.wbf> <out dir> [testcard | colourcard | image.png] [threshold 0-255 | colour]
Env: BAND (default 4 = 22-28 C, the tablet's usual band); DISPLAY_FILES (default <tools>/../display-files-3.27.3.0);
     DRAW = STANDARD (default) | FAST | PEN, the colour draw mode; THEN = colourcard | image.png, a second picture drawn
     as a partial update; GREYS=1 adds dark grey (state 15) and light grey (state 19) swatches to the colour card;
     FONT = path to a TrueType font for the card text (default: macOS Helvetica Neue).
Writes <out>/f<i>.raw (unique frames, f0 = zero), plan.json (sessions, sha256, temperature window, checks), preview.png
(+ preview2.png with THEN)."""
import sys, os, json, struct, hashlib, time, collections
sys.path.insert(0, sys.argv[1])
import eink, dither
import numpy as np
from PIL import Image, ImageDraw, ImageFont

TOOLS, WBF, OUT = sys.argv[1:4]
SRC = sys.argv[4] if len(sys.argv) > 4 else 'testcard'
ARG5 = sys.argv[5] if len(sys.argv) > 5 else None
COLOUR = SRC == 'colourcard' or ARG5 == 'colour'
THR = int(ARG5) if ARG5 and not COLOUR else 128
REF = int(os.environ.get('BAND', 4))
DF = os.environ.get('DISPLAY_FILES', os.path.join(TOOLS, '..', 'display-files-3.27.3.0'))
THEN = os.environ.get('THEN')
GREYS = os.environ.get('GREYS') == '1'
W, H, SPARE, PW, PH = 405, 1084, 4, 1620, 2160
FB_BYTES = 429 * 4096                                            # one dumb buffer: pitch 1620 x 1084 lines, page-rounded
CODE = {0: 0, 1: 5, 2: 4, 3: 6, 12: 1, 13: 3, 14: 2}
VOLT = {1: 12, 2: 6, 3: 24, 12: -24, 13: -6, 14: -12}          # report 3.4 (Inference); + = toward black
LEVEL = {1: 1, 2: 2, 3: 3, 12: -3, 13: -2, 14: -1}              # plain levels: the cross-check weighting for balance
INIT, PEN, STANDARD, FAST, A2, WHITE, BLACK = 0, 1, 2, 5, 7, 28, 2
NAMES = 'INIT PEN STANDARD TEXT TEXT_GLR FAST BEST A2 PMODE'.split()
DRAW = NAMES.index(os.environ.get('DRAW', 'STANDARD')) if COLOUR else A2
assert DRAW in (PEN, STANDARD, FAST, A2), 'DRAW must be STANDARD, FAST or PEN'
assert not (GREYS and not COLOUR), 'GREYS is a colour-card option'
FONT = os.environ.get('FONT', '/System/Library/Fonts/HelveticaNeue.ttc')   # any TrueType font; the default is macOS's
f = lambda s: ImageFont.truetype(FONT, s)
w = open(WBF, 'rb').read(); hdr = eink.header(w); bounds = hdr['temp_bounds']
_ph = {}
def phases(m, b):
    if (m, b) not in _ph: _ph[m, b] = eink.phases(w, m, b)
    return _ph[m, b]
NDRAW = len(phases(DRAW, REF))

def testcard():
    img = Image.new('L', (PW, PH), 255); d = ImageDraw.Draw(img)
    d.rectangle([0, 0, PW - 1, PH - 1], outline=0, width=1)                       # outermost pixel ring
    d.rectangle([24, 24, PW - 25, PH - 25], outline=0, width=6)
    for x in range(0, PW, 100): d.line([x, 0, x, 40 if x % 500 == 0 else 20], fill=0, width=2)   # top ruler
    for y in range(0, PH, 100): d.line([0, y, 40 if y % 500 == 0 else 20, y], fill=0, width=2)   # left ruler
    for (x, y, t) in ((60, 60, 'TOP LEFT'), (PW - 60, 60, 'TOP RIGHT'), (60, PH - 60, 'BOTTOM LEFT'), (PW - 60, PH - 60, 'BOTTOM RIGHT')):
        d.text((x, y), t, font=f(36), fill=0, anchor=('l' if x < PW / 2 else 'r') + ('a' if y < PH / 2 else 'd'))
    d.text((PW / 2, 330), 'Drawn by our own writer', font=f(110), fill=0, anchor='ma')
    d.text((PW / 2, 480), 'No reMarkable code: frames built on the Mac from the panel\'s waveform file', font=f(40), fill=0, anchor='ma')
    d.text((PW / 2, 540), 'INIT clear, then A2 (10 frames, about 0.12 s); erased the same way', font=f(40), fill=0, anchor='ma')
    d.text((PW / 2, 600), time.strftime('built %Y-%m-%d %H:%M'), font=f(36), fill=0, anchor='ma')
    # fine-detail patches: if the 4x2 word layout were wrong, these would come out scrambled
    pats = [('1 px checker', lambda x, y: (x + y) & 1), ('1 px rows', lambda x, y: y & 1), ('1 px columns', lambda x, y: x & 1),
            ('2 px checker', lambda x, y: (x // 2 + y // 2) & 1), ('4 x 2 blocks', lambda x, y: (x // 4 + y // 2) & 1), ('solid', lambda x, y: 1)]
    px = img.load(); S = 300
    for i, (name, fn) in enumerate(pats):
        x0, y0 = 160 + (i % 3) * 460, 820 + (i // 3) * 480
        for y in range(S):
            for x in range(S):
                if fn(x, y): px[x0 + x, y0 + y] = 0
        d.rectangle([x0 - 2, y0 - 2, x0 + S + 1, y0 + S + 1], outline=0, width=2)
        d.text((x0 + S / 2, y0 + S + 20), name, font=f(36), fill=0, anchor='ma')
    for i, w_ in enumerate((1, 2, 3, 4, 6, 8)):                                    # line weights
        d.line([200, 1900 + i * 30, PW - 200, 1900 + i * 30], fill=0, width=w_)
    return img

# --- colour tables (constant data only) ---
def so_bytes(path, va, size):
    d = open(path, 'rb').read()
    ph, n = struct.unpack_from('<Q', d, 0x20)[0], struct.unpack_from('<H', d, 0x38)[0]
    for t, _, o, v, _, fs, _, _ in (struct.unpack_from('<IIQQQQQQ', d, ph + 56 * i) for i in range(n)):
        if t == 1 and v <= va < v + fs: return d[o + va - v:o + va - v + size]
LIB, CT = os.path.join(DF, 'libqsgepaper.so'), os.path.join(DF, 'ct33_std.bin')   # all four ct33_*.bin are identical
TAB = [list(so_bytes(LIB, 0x690b0, 144)[16 * r:16 * r + 16]) for r in range(9)]   # [mode][slot] -> target state
assert TAB[STANDARD][:12] == [2, 12, 4, 20, 8, 16, 24, 28, 15, 19, 2, 30], 'target table differs from wfextra.py TAB'
assert TAB[FAST][:12] == [2, 13, 5, 21, 9, 17, 25, 28, 15, 19, 0, 30] and TAB[PEN][:12] == [0, 14, 6, 22, 10, 18, 26, 30, 15, 19, 0, 30]
INK = ['black', 'blue', 'red', 'magenta', 'green', 'cyan', 'yellow', 'white', 'dark grey', 'light grey']   # slot = 4G + 2R + B
SLOT_RGB = [((s >> 1 & 1) * 255, (s >> 2 & 1) * 255, (s & 1) * 255) for s in range(8)] + [(85, 85, 85), (170, 170, 170)]
OWN_WHITE = TAB[DRAW][7]                                          # 28 for STANDARD/FAST/A2, 30 for PEN

def dither_into(slot, x0, y0, rgb, M, walk, ct):
    """Colour-dither an (h, w, 3) RGB block into slot[] at (x0, y0); the noise index is the panel pixel position."""
    for yy, row in enumerate(rgb.tolist()):
        for xx, (R, G, B) in enumerate(row):
            slot[y0 + yy, x0 + xx] = dither.colour_slot(M, walk, ct, R, G, B, x0 + xx, y0 + yy) & 15

def ink_mix(R, G, B, M, walk, ct):
    h = collections.Counter(dither.colour_slot(M, walk, ct, R, G, B, x, y) & 15 for y in range(64) for x in range(64))
    return sorted(((n, s) for s, n in h.items()), reverse=True)

def colourcard(M, walk, ct, how):
    """Slot grid 0-9. Labels name the colour each ink should show (chromatic order: report 3.2 + 3.4, confirmed by eye 19:38)."""
    slot = np.full((PH, PW), 7, np.uint8)
    txt = Image.new('L', (PW, PH), 255); d = ImageDraw.Draw(txt)
    def text(x, y, s, size, anchor='la'): d.text((x, y), s, font=f(size), fill=0, anchor=anchor)
    def frame_(x0, y0, x1, y1): d.rectangle([x0 - 3, y0 - 3, x1 + 2, y1 + 2], outline=0, width=3)   # just outside [x0, x1)
    text(PW / 2, 60, 'Colour test card', 96, 'ma')
    text(PW / 2, 180, how, 34, 'ma')
    text(PW / 2, 228, 'No reMarkable code: frames built on the Mac from the panel\'s waveform and colour tables', 34, 'ma')
    # 1. flat inks, in the report's colour order (+ the two grey states)
    inks = (0, 2, 4, 1, 5, 3, 6, 7) + ((8, 9) if GREYS else ())
    if GREYS:
        text(100, 320, '1. Ten inks, flat: eight colours, then the two grey states.', 38)
        step, sw, ls = 142, 126, 32
        while max(f(ls).getlength(INK[s].upper()) for s in inks) > step - 10: ls -= 1
    else:
        text(100, 320, '1. Eight inks, flat. Each name is the colour that ink should show.', 38)
        step, sw, ls = 180, 160, 32
    for i, s in enumerate(inks):
        x0, y0 = 100 + i * step, 390
        slot[y0:y0 + 240, x0:x0 + sw] = s; frame_(x0, y0, x0 + sw, y0 + 240)
        text(x0 + sw // 2, y0 + 262, INK[s].upper(), ls, 'ma'); text(x0 + sw // 2, y0 + 304, f'state {TAB[DRAW][s]}', min(24, ls), 'ma')
    # 2. gradients through the colour dither
    text(100, 780, '2. Dithered gradients (colour dither: ct33 table + blue noise).', 38)
    gw, x0 = 1420, 100
    t = np.linspace(0, 1, gw)
    hue = np.clip(np.stack([np.abs(t * 6 - 3) - 1, 2 - np.abs(t * 6 - 2), 2 - np.abs(t * 6 - 4)], 1), 0, 1)   # pure hue per column
    grey = np.repeat(np.round(t * 255)[:, None], 3, 1)
    text(x0, 845, 'black to white (in colour mode greys are mixed from colour inks)', 30)
    dither_into(slot, x0, 885, np.broadcast_to(grey, (120, gw, 3)).astype(np.uint8), M, walk, ct); frame_(x0, 885, x0 + gw, 1005)
    text(x0, 1035, 'hue: red, yellow, green, cyan, blue, magenta, red', 30)
    dither_into(slot, x0, 1075, np.broadcast_to(np.round(hue * 255), (120, gw, 3)).astype(np.uint8), M, walk, ct); frame_(x0, 1075, x0 + gw, 1195)
    text(x0, 1225, 'hue across; white at the top, full colour in the middle, black at the bottom', 30)
    L = np.linspace(1, 0, 360)[:, None, None]
    blk = np.where(L >= 0.5, hue[None] + (1 - hue[None]) * (2 * L - 1), hue[None] * 2 * L)
    dither_into(slot, x0, 1265, np.round(blk * 255).astype(np.uint8), M, walk, ct); frame_(x0, 1265, x0 + gw, 1625)
    # 3. flat colours through the dither, with the ink mix the table gives
    text(100, 1690, '3. Flat colours, dithered. Under each: % of pixels per ink.', 38)
    text(100, 1740, 'K black, R red, G green, B blue, C cyan, M magenta, Y yellow, W white', 28)
    ab = 'KBRMGCYW'
    for i, (name, rgb) in enumerate((('orange', (255, 160, 0)), ('light blue', (128, 128, 255)), ('mid grey', (128, 128, 128)),
                                     ('light grey', (192, 192, 192)), ('pink', (255, 182, 193)), ('brown', (150, 75, 0)))):
        px0, py0 = 100 + i * 244, 1800
        dither_into(slot, px0, py0, np.broadcast_to(np.array(rgb, np.uint8), (180, 200, 3)), M, walk, ct); frame_(px0, py0, px0 + 200, py0 + 180)
        mix = ink_mix(*rgb, M, walk, ct); parts = [f'{ab[s]}{round(100 * n / 4096)}' for n, s in mix[:3]]
        text(px0 + 100, py0 + 198, name, 30, 'ma'); text(px0 + 100, py0 + 238, ' '.join(parts) + (f' +{len(mix) - 3}' if len(mix) > 3 else ''), 24, 'ma')
    text(PW / 2, 2110, time.strftime('built %Y-%m-%d %H:%M'), 26, 'ma')
    slot[np.asarray(txt) < 128] = 0
    return slot

def fit(img, bg):
    if img.size == (PW, PH): return img
    if img.width > img.height: img = img.rotate(90, expand=True)
    s = min(PW / img.width, PH / img.height); img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    c = Image.new(img.mode, (PW, PH), bg); c.paste(img, ((PW - img.width) // 2, (PH - img.height) // 2)); return c

# --- target state per pixel, per picture ---
if COLOUR:
    M, walk, ct = dither.load(LIB, CT)
    def picture(src, how):
        if src == 'colourcard': slots = colourcard(M, walk, ct, how)
        else:
            rgb = np.asarray(fit(Image.open(src).convert('RGB'), (255, 255, 255))); slots = np.empty((PH, PW), np.uint8)
            dither_into(slots, 0, 0, rgb, M, walk, ct)
        return np.array(TAB[DRAW][:10], np.uint8)[slots]
else:
    def picture(src, how):
        img = fit(testcard() if src == 'testcard' else Image.open(src).convert('L'), 255)
        return np.where(np.asarray(img) < THR, BLACK, WHITE).astype(np.uint8)
secs = f'{NDRAW} frames, about {NDRAW * 0.01177:.1f} s'
pics = [picture(SRC, f'Drawn by our own writer: INIT clear, then {NAMES[DRAW]} from white ({secs})')]
if THEN: pics.append(picture(THEN, f'Partial update from the previous picture: {NAMES[DRAW]} on changed pixels only ({secs}), no INIT'))
K = len(pics)
hist = np.stack([np.full((PH, PW), WHITE, np.uint8)] + pics, -1).reshape(-1, K + 1)   # stage 0 = white after INIT
_u, _inv = np.unique(hist, axis=0, return_inverse=True)
GS = [tuple(int(x) for x in r) for r in _u]; gidx = _inv.reshape(PH, PW)          # pixel groups = distinct state histories
NG = len(GS)

def seq(m, b, fr, to): return [p[to][fr] for p in phases(m, b)]
def net(s, V): return sum(V.get(x, 0) for x in s)
def step_seqs(b, k):
    """Draw k (1..K) at band b: DRAW from stage k-1 to stage k per group. Draw 1 drives every pixel, as the library does from
    white; a partial update (k >= 2) gives pixels whose state does not change no drive at all."""
    n = len(phases(DRAW, b))
    return [[0] * n if k >= 2 and g[k - 1] == g[k] else seq(DRAW, b, g[k - 1], g[k]) for g in GS]
ERASE_MODES = {'INIT': (INIT, None), 'A2': (A2, WHITE), 'STANDARD': (STANDARD, WHITE), NAMES[DRAW]: (DRAW, OWN_WHITE)}
def erase_seqs(b, erase):
    m, to = ERASE_MODES[erase]
    return [seq(INIT, b, WHITE, WHITE) if m == INIT else seq(m, b, g[K], to) for g in GS]
def plan_seqs(b, erase):
    """Every drive sequence the plan uses at band b: INIT (all 32 x 32), each draw per group, the erase per group."""
    return dict(init={tuple(seq(INIT, b, fr, to)) for fr in range(32) for to in range(32)},
                draws=[step_seqs(b, k) for k in range(1, K + 1)], erase=erase_seqs(b, erase))

# --- checks on the waveforms, then the erase decision ---
init_s = seq(INIT, REF, WHITE, WHITE)
assert len(plan_seqs(REF, 'INIT')['init']) == 1, 'INIT depends on the source or target state'
draws = [step_seqs(REF, k) for k in range(1, K + 1)]
row = set(TAB[DRAW][:12])
assert all(g[k] in row for g in GS for k in range(1, K + 1)), 'a target outside the DRAW row of 0x690b0'
first = sorted({g[1] for g in GS})
if COLOUR:
    same_src = all(seq(DRAW, REF, s, t) == seq(DRAW, REF, WHITE, t) for s in range(27, 32) for t in first)
    assert same_src, f'{NAMES[DRAW]} drives white sources 27-31 differently: the state INIT leaves (28 or 30) matters'
    cands = ['INIT', 'STANDARD'] + ([NAMES[DRAW]] if DRAW != STANDARD else [])
    choice = {}
    for c in cands:
        er = erase_seqs(REF, c)
        full_ = [init_s + sum((d[i] for d in draws), []) + er[i] for i in range(NG)]
        nv = [net(s, VOLT) for s in full_]; nl = [net(s, LEVEL) for s in full_]
        choice[c] = dict(worst_volt_frames=max(map(abs, nv)), worst_levels=max(map(abs, nl)),
                         per_history={'>'.join(map(str, g[1:])): [nv[i], nl[i]] for i, g in enumerate(GS)})
    # smaller worst pixel under the inferred volts, then under plain levels; a tie goes to INIT (exactly balanced
    # value by value, source-independent, and no new frames)
    ERASE = min(choice, key=lambda c: (choice[c]['worst_volt_frames'], choice[c]['worst_levels'], c != 'INIT'))
else:
    assert not any(seq(A2, REF, WHITE, WHITE)), 'A2 28->28 drives'
    ERASE, choice = 'A2', None
erase_l = erase_seqs(REF, ERASE)

ref = plan_seqs(REF, ERASE)
same = [b for b in range(len(bounds) - 1) if plan_seqs(b, ERASE) == ref]
lo = REF
while lo - 1 in same: lo -= 1
hi = REF
while hi + 1 in same: hi += 1
# 0.1 C inside each band edge (the library's edge rule, < or <=, is UNKNOWN); never outside the old 5-40 C preflight
tmin, tmax = max(round(bounds[lo] + 0.1, 1), 5), min(round(bounds[hi + 1] - 0.1, 1), 40)

# --- frames ---
_r, _c = np.arange(PH)[:, None], np.arange(PW)[None, :]
U_IDX, U_SH = (_r // 2 + SPARE) * W + _c // 4, 3 * (2 * (_c % 4) + (_r & 1))
_K = 2 * np.arange(4)[None, None, None, :] + np.arange(2)[None, :, None, None]        # k = 2 * column-in-word + row parity
def pack(codes, spare=0):
    """codes: (PH, PW) wire codes -> 405 x 1084 little-endian words."""
    c = codes.astype(np.uint32).reshape(PH // 2, 2, W, 4)                            # [line-4, row parity, word, column in word]
    out = np.empty((H, W), '<u4'); out[:SPARE] = spare; out[SPARE:] = (c << (3 * _K)).sum(axis=(1, 3), dtype=np.uint32)
    return out.tobytes()
def unpack(fr):
    """Independent per-pixel read of a frame (the v1 formula, vectorised)."""
    return (np.frombuffer(fr, '<u4')[U_IDX] >> U_SH) & 7

frames, index, what, luts = [], {}, [], []
def frame(key, lut, spare, desc):
    if key not in index:
        index[key] = len(frames); frames.append(pack(lut[gidx], spare)); what.append(desc); luts.append((lut, spare))
    return index[key]
def fill(code): return frame(('fill', code), np.full(NG, code, np.uint8), 0xDB6DB6 if code == 6 else 0, f'fill code {code}')
Z = fill(0)
def drive(m, seqs, label=''):
    out = []
    for p in range(len(seqs[0])):
        codes = [CODE[s[p]] for s in seqs]
        out.append(fill(codes[0]) if len(set(codes)) == 1 else
                   frame(('map', tuple(codes)), np.array(codes, np.uint8), 0,
                         f'{NAMES[m]}{label} phase {p}, code per group ' + ' '.join(f'{">".join(map(str, g[1:]))}:{c}' for g, c in zip(GS, codes))))
    return out
sessions = [drive(INIT, [init_s] * NG) + drive(DRAW, draws[0]) + [Z, Z]]
for k in range(2, K + 1): sessions.append(drive(DRAW, draws[k - 1], f' partial {k}') + [Z, Z])
sessions.append(drive(ERASE_MODES[ERASE][0], erase_l) + [Z, Z])

# --- self-checks on the frames themselves ---
if ('fill', 6) in index:
    assert frames[index['fill', 6]] == struct.pack('<I', 0xDB6DB6) * (W * H), 'code-6 fill differs from the proven U-Boot fill'
occ = collections.Counter(i for s in sessions for i in s)
cnt = {v: np.zeros((PH, PW), np.int32) for v in (1, 2, 3, 12, 13, 14)}
bad = spare_bad = 0
for i, fr in enumerate(frames):
    dec = unpack(fr); lut, spare = luts[i]
    bad += int((dec != lut[gidx]).sum())                                              # geometry round trip, every pixel
    spare_bad += int((np.frombuffer(fr, '<u4')[:SPARE * W] != spare).sum())
    assert (dec != 7).all(), 'code 7 in a frame'
    for v in cnt: cnt[v] += occ[i] * (dec == CODE[v])
assert bad == 0 and spare_bad == 0, f'{bad} pixels / {spare_bad} spare words decode wrong'
net_v = sum(cnt[v] * VOLT[v] for v in cnt); net_l = sum(cnt[v] * LEVEL[v] for v in cnt)
full = [init_s + sum((d[i] for d in draws), []) + erase_l[i] for i in range(NG)]    # what the waveform file says, per group
assert (net_v == np.array([net(s, VOLT) for s in full])[gidx]).all(), 'net drive from the frames differs from the waveforms'
assert (net_l == np.array([net(s, LEVEL) for s in full])[gidx]).all()
hkey = ['>'.join(map(str, g[1:])) for g in GS]
per_value = {hkey[i]: {v: full[i].count(v) for v in (1, 2, 3, 12, 13, 14)} for i in range(NG)}
if not COLOUR:
    assert not net_v.any() and not net_l.any() and all(p[3] == p[12] and p[1] == p[14] and p[2] == p[13] for p in per_value.values())
if K >= 2:                                                                            # partial update: undriven = unchanged
    for k in range(2, K + 1):
        undriven = [i for i, g in enumerate(GS) if g[k - 1] == g[k]]
        assert all(not any(draws[k - 1][i]) for i in undriven)
        empty = [hkey[i] for i, g in enumerate(GS) if g[k - 1] != g[k] and not any(draws[k - 1][i]) and min(g[k - 1], g[k]) < 27]
        assert not empty, f'changed pixels with an all-zero waveform (a "don\'t care" entry?): {empty}'
two = {Z} | {a for s in sessions for a, b in zip(s, s[1:]) if a == b}             # frames the player needs twice

os.makedirs(OUT, exist_ok=True)
for i, fr in enumerate(frames): open(f'{OUT}/f{i}.raw', 'wb').write(fr)
for old in os.listdir(OUT):                                                             # drop stale frames from an earlier build
    if old.startswith('f') and old.endswith('.raw') and old[1:-4].isdigit() and int(old[1:-4]) >= len(frames): os.remove(f'{OUT}/{old}')
counts = {str(g): int((pics[0] == g).sum()) for g in first}
plan = dict(version=1, built=time.strftime('%Y-%m-%d %H:%M:%S'), kind='colour' if COLOUR else 'mono', source=SRC,
            threshold=None if COLOUR else THR, wbf=os.path.basename(WBF), draw_mode=NAMES[DRAW], erase=ERASE,
            band_ref=REF, bands_same=[lo, hi], temp_min=tmin, temp_max=tmax,
            frames=[dict(file=f'f{i}.raw', sha256=hashlib.sha256(fr).hexdigest(), what=what[i]) for i, fr in enumerate(frames)],
            sessions=sessions, states=counts, fb_bytes_two_copies=2 * len(frames) * W * H * 4, erase_choice=choice,
            checks=dict(geometry_roundtrip_bad=bad, spare_words_bad=spare_bad,
                        net_volt_frames_by_state={hkey[i]: net(full[i], VOLT) for i in range(NG)},
                        net_levels_by_state={hkey[i]: net(full[i], LEVEL) for i in range(NG)},
                        max_abs_net_volt_frames=int(np.abs(net_v).max()), max_abs_net_levels=int(np.abs(net_l).max()),
                        per_value_by_state=per_value))
if K >= 2 or GREYS or DRAW not in (STANDARD, A2):                                    # v3 fields (a v2-style plan keeps v2's field set)
    plan.update(then=THEN, greys=GREYS, fb_count=len(frames) + len(two), fb_bytes=(len(frames) + len(two)) * FB_BYTES,
                stages=[{str(g): int((p == g).sum()) for g in sorted(set(np.unique(p).tolist()))} for p in pics],
                changed_pixels=[int((pics[k] != pics[k - 1]).sum()) for k in range(1, K)])
json.dump(plan, open(f'{OUT}/plan.json', 'w'), indent=1)
for k, p in enumerate(pics):
    name = f'{OUT}/preview.png' if k == 0 else f'{OUT}/preview{k + 1}.png'
    if COLOUR:
        inv = {TAB[DRAW][s]: s for s in range(10)}
        Image.fromarray(np.array([SLOT_RGB[inv[g]] if g in inv else (255, 0, 255) for g in range(33)], np.uint8)[p]).save(name)
    else:
        Image.fromarray(np.where(p == BLACK, 0, 255).astype(np.uint8)).save(name)
print(f'{plan["kind"]}: {len(frames)} unique frames ({len(frames) + len(two)} fbs, {(len(frames) + len(two)) * FB_BYTES / 1e6:.0f} MB on the '
      f'tablet), sessions ' + ' + '.join(str(len(s)) for s in sessions) + f' flips; draw {NAMES[DRAW]}, erase {ERASE}; '
      f'bands {lo}-{hi} identical -> {tmin}-{tmax} C')
print('states (pixels, picture 1):', counts)
if K >= 2: print('changed pixels per partial update:', plan['changed_pixels'], 'of', PW * PH)
print('checks', {k: v for k, v in plan['checks'].items() if k not in ('per_value_by_state', 'net_volt_frames_by_state', 'net_levels_by_state')})
if choice: print('erase choice', {c: (v['worst_volt_frames'], v['worst_levels']) for c, v in choice.items()}, '->', ERASE)
