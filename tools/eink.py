"""Paper Pro GAL3_*.eink -> standard E Ink WBF.
Layer 1: XOR every byte with 0x08.
Layer 2: 16-byte header, BE u32 (len-16), 1, 0x00020000, 0.
Layer 3: LZ77 tokens of 4 bytes: offset u16 LE, length u8, literal u8.
         A trailing 3-byte token is a copy with no literal.
Result: a WBF file whose CRC32 (first 4 bytes zeroed) and header sums verify."""
import sys, struct, zlib

def decode(raw):
    d = bytes(b ^ 8 for b in raw)
    body = d[16:]
    out = bytearray(); i = 0
    while i + 4 <= len(body):
        off = body[i] | body[i+1] << 8; ln = body[i+2]
        for _ in range(ln): out.append(out[-off])
        out.append(body[i+3]); i += 4
    if len(body) - i == 3:
        off = body[i] | body[i+1] << 8
        for _ in range(body[i+2]): out.append(out[-off])
    elif len(body) != i:
        raise ValueError('odd tail')
    return bytes(out)

def verify(w):
    fs = struct.unpack_from('<I', w, 4)[0]
    return {'len_ok': fs == len(w),
            'crc_ok': zlib.crc32(b'\0' * 4 + w[4:fs]) == struct.unpack_from('<I', w, 0)[0],
            'cs1_ok': sum(w[8:31]) & 255 == w[31],
            'cs2_ok': sum(w[32:47]) & 255 == w[47]}

def header(w):
    u24 = lambda o: w[o] | w[o+1] << 8 | w[o+2] << 16
    h = dict(serial=struct.unpack_from('<I', w, 8)[0], run_type=w[12], fpl_platform=w[13],
             fpl_lot=struct.unpack_from('<H', w, 14)[0], mode_version=w[16], wf_version=w[17],
             wf_subversion=w[18], wf_type=w[19], fpl_size=w[20], mfg_code=w[21], tuning_bias=w[22],
             fpl_rate=w[23], vcom_shifted=w[25], xwia=u24(28), wmta=u24(32), fvsn=w[35], luts=w[36],
             modes=w[37] + 1, temps=w[38] + 1, adv_flags=w[39], eb=w[40], sb=w[41])
    h['temp_bounds'] = list(w[48:48 + h['temps'] + 1])
    x = h['xwia']; h['name'] = w[x+1:x+1+w[x]].decode('latin1') if x else ''
    return h

def mode_temp_ptrs(w):
    """[mode][temp] -> 24-bit offset of that waveform (each pointer is 3 bytes + 1 checksum byte)."""
    h = header(w); u24 = lambda o: w[o] | w[o+1] << 8 | w[o+2] << 16
    modes = [u24(h['wmta'] + 4 * m) for m in range(h['modes'])]
    return [[u24(mt + 4 * t) for t in range(h['temps'])] for mt in modes]

if __name__ == '__main__':
    w = decode(open(sys.argv[1], 'rb').read())
    print(verify(w)); print(header(w))
    for m, row in enumerate(mode_temp_ptrs(w)): print('mode', m, [hex(p) for p in row])
    if len(sys.argv) > 2: open(sys.argv[2], 'wb').write(w)

def unrle(s):
    """WBF waveform body: run-length mode starts ON; 0xFC toggles it; 0xFF ends.
    In run mode each data byte is followed by (repeat count - 1)."""
    out = bytearray(); r = True; i = 0
    while i < len(s):
        b = s[i]
        if b == 0xFC: r = not r; i += 1; continue
        if b == 0xFF: return bytes(out), i
        if r: out += bytes([b]) * (s[i+1] + 1); i += 2
        else: out.append(b); i += 1
    raise ValueError('no 0xFF terminator')

def phases(w, mode, temp):
    """List of phases; each phase is 32x32 drive values, phase[to][from] (working reading).
    Low nibble first. Values seen: 0, 1, 2, 3, 12, 13, 14 (12..14 = bitwise NOT of 3..1)."""
    o, _ = unrle(w[mode_temp_ptrs(w)[mode][temp]:])
    assert len(o) % 512 == 0
    res = []
    for k in range(len(o) // 512):
        v = []
        for b in o[512*k:512*k+512]: v += [b & 15, b >> 4]
        res.append([v[32*i:32*i+32] for i in range(32)])
    return res
