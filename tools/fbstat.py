import sys, zlib, json, collections, struct
raw = zlib.decompress(open(sys.argv[1], 'rb').read())
nl = raw.index(b'\n'); hdr = json.loads(raw[:nl]); body = raw[nl+1:]
pos = 0
for fid, f in sorted(hdr['fbs'].items(), key=lambda x: int(x[0])):
    b = body[pos:pos+f['n']]; pos += f['n']
    words = struct.unpack('<%dI' % (len(b)//4), b)
    wc = collections.Counter(words)
    hi = collections.Counter(w >> 24 for w in words)
    codes = collections.Counter()
    for w, n in wc.items():
        for k in range(8): codes[(w >> (3*k)) & 7] += n
    # rows (lines) that are not uniform
    pitchw = f['pitch'] // 4
    nonuni = [ln for ln in range(f['h']) if len(set(words[ln*pitchw:(ln+1)*pitchw])) > 1]
    print(fid, 'top words', [(hex(w), n) for w, n in wc.most_common(4)], 'hi', dict(hi.most_common(3)),
          'codes', dict(sorted(codes.items())), 'nonuniform lines', len(nonuni), nonuni[:3], nonuni[-3:])
