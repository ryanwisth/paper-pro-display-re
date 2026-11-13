# The Paper Pro `.eink` waveform container

This page describes the waveform files used by the reMarkable Paper Pro's colour e-ink panel (an E Ink Gallery 3 panel), as decoded in this project. It is written from our own analysis. No vendor file is reproduced here; to follow along you need your own copy of the files from reMarkable's public software update (see [method.md](method.md)).

The decoder is [`tools/eink.py`](../tools/eink.py) (about 70 lines, standard library only).

## Summary

A `GAL3_*.eink` file is a standard E Ink **WBF** waveform file wrapped in three thin layers:

```
.eink file
  └─ layer 1: every byte XOR 0x08
      └─ layer 2: 16-byte header
          └─ layer 3: LZ77 token stream
              └─ standard WBF file (CRC-32 and header checksums verify)
```

The OS image ships 314 of these files (one per panel lot and variant). All 314 decode to WBF files whose CRC-32 and both header checksums verify, which is the main evidence that the decoding is right.

## Layer 1: byte mask

Every byte of the file, header included, is XORed with `0x08`. Undo it first.

## Layer 2: container header (16 bytes, after unmasking)

| Offset | Size | Value | Meaning |
|---|---|---|---|
| 0 | u32 big-endian | file length minus 16 | length of the token stream |
| 4 | u32 BE | 1 | constant in every file seen |
| 8 | u32 BE | 0x00020000 | constant in every file seen |
| 12 | u32 BE | 0 | constant in every file seen |

The decoder does not depend on these values; they are listed so other readers can recognise the header.

## Layer 3: LZ77 token stream

The body after the header is a sequence of 4-byte tokens:

| Byte | Meaning |
|---|---|
| 0-1 | back-reference distance, u16 little-endian |
| 2 | copy length, u8 (0 = no copy) |
| 3 | one literal byte |

For each token: copy `length` bytes, one at a time, from `distance` bytes back in the output (copies may overlap their own output, as in classic LZ77), then append the literal. If the stream ends with 3 bytes left over, that last token is a copy with no literal. Any other remainder is an error.

The output is roughly twice the size of the `.eink` file.

## The WBF file inside

The decoded file is the waveform format E Ink panels commonly use. Field names below are the conventional ones for this format, as used in the decoder.

### Main header

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | u32 LE | CRC-32 | over bytes 4 .. file size, with bytes 0-3 treated as zero |
| 4 | u32 LE | file size | matches the decoded length |
| 8 | u32 LE | serial | waveform serial |
| 12 | u8 | run type | |
| 13 | u8 | FPL platform | front-plane laminate platform |
| 14 | u16 LE | FPL lot | |
| 16 | u8 | mode version | |
| 17 | u8 | waveform version | |
| 18 | u8 | waveform subversion | |
| 19 | u8 | waveform type | |
| 20 | u8 | FPL size | |
| 21 | u8 | manufacturer code | |
| 22 | u8 | tuning bias | |
| 23 | u8 | FPL rate | 0 in the panel file we checked |
| 24 | u8 | frame rate | 85, read as the 85 Hz design scan rate |
| 25 | u8 | VCOM shifted | |
| 28 | u24 LE | xwia | offset of an optional name string (length byte, then text) |
| 31 | u8 | checksum 1 | sum of bytes 8-30, mod 256 |
| 32 | u24 LE | wmta | offset of the mode table |
| 35 | u8 | fvsn | |
| 36 | u8 | luts | |
| 37 | u8 | mode count minus 1 | 8, so 9 modes |
| 38 | u8 | temperature range count minus 1 | 8, so 9 bands |
| 39 | u8 | advanced flags | |
| 40 | u8 | eb | |
| 41 | u8 | sb | |
| 47 | u8 | checksum 2 | sum of bytes 32-46, mod 256 |
| 48 | u8 × (bands + 1) | temperature bounds | °C, one more entry than bands |

In the panel file checked on the device, the temperature bounds are 0, 7, 13, 18, 22, 28, 33, 38, 43 and 50 °C, giving nine bands (band 4 = 22-28 °C, the usual room-temperature band).

### Mode and temperature tables

- At `wmta` there is one 4-byte entry per mode: a 24-bit little-endian pointer plus a checksum byte. Each pointer leads to that mode's temperature table.
- Each temperature table holds one 4-byte entry per band, in the same 3 + 1 layout, pointing at a waveform.

So `waveform(mode, band)` = follow `wmta[mode]`, then `table[band]`.

### Per-waveform header (16 bytes, just before each waveform)

| Offset | Size | Meaning |
|---|---|---|
| 0 | 4 | constant `01 80 15 00` |
| 4 | u24 + u8 | pointer to a voltage block, plus a checksum byte (sum of the 3 pointer bytes) |
| 8 | 7 | zero |
| 15 | u8 | sum of bytes 0-14 |

The voltage block it points to is 21 bytes: a u16 tag `0x8001`, eight u16 values in 12.5 mV units, a u16 zero and a checksum byte. Read that way, most modes carry 12 / 6 / 24 V source levels, INIT carries 17 / 10 / 24 V, and the gate pair reads 22 / 20 V. On the device, the rails are set once per boot to 6, 12, 24, -6, -12 and -24 V, and the rendering library does not appear to read these blocks (static analysis). [`tools/wfextra.py`](../tools/wfextra.py) checks every one of these headers and blocks.

### Waveform body

The body is run-length encoded:

- Decoding starts in run mode. In run mode each data byte is followed by a byte holding (repeat count - 1).
- `0xFC` toggles run mode on and off.
- `0xFF` ends the body. The byte after it is a checksum: the sum of the body including the `0xFF`, mod 256.

The decoded body is a whole number of **phases** (frames) of 512 bytes. Each phase is a 32 × 32 grid of 4-bit drive values, low nibble first. Our working reading is `phase[target][source]`: for a pixel going from state `source` to state `target`, the value at that cell is what to drive in that frame. Capture data from the device matched this orientation (see [display-pipeline.md](display-pipeline.md)).

Only seven drive values occur: 0 (no drive), 1, 2, 3 and 12, 13, 14. The second group is the bitwise NOT of the first (14 = ~1, 13 = ~2, 12 = ~3), so they read as the same three levels in the opposite direction.

## Modes and phase counts

Nine modes, named from the vendor's rendering library (static analysis). Phase counts are from the panel file checked on the device:

| # | Mode | Phases per band (bands 0 → 8, cold → hot) | Time at band 4, 85 Hz |
|---|---|---|---|
| 0 | INIT | 161 in every band | about 1.9 s |
| 1 | PEN | 71, 63, 48, 36, 31, 31, 31, 31, 31 | about 0.36 s |
| 2 | STANDARD | 167, 142, 117, 91, 86, 86, 86, 86, 86 | about 1.0 s |
| 3 | TEXT | 99, 92, 63, 44, 39, 39, 39, 39, 39 | about 0.46 s |
| 4 | TEXT_GLR | 71, 63, 48, 36, 31, 31, 31, 31, 31 | about 0.36 s |
| 5 | FAST | 122, 111, 83, 58, 53, 53, 53, 53, 53 | about 0.62 s |
| 6 | BEST | 238, 196, 155, 134, 128, 128, 128, 128, 128 | about 1.5 s |
| 7 | A2 | 11 in every band | about 0.13 s |
| 8 | PMODE | 4 or 5 | under 0.1 s |

Times are phase count divided by 85 Hz. They describe the waveform alone, not end-to-end screen latency.

## Wire codes

The display controller does not receive the 4-bit file values directly. Each pixel gets a 3-bit wire code in the frame buffer. The mapping, read from the library and then confirmed on the panel:

| File value | 0 | 1 | 2 | 3 | 12 | 13 | 14 |
|---|---|---|---|---|---|---|---|
| Wire code | 0 | 5 | 4 | 6 | 1 | 3 | 2 |

Wire code 6 drives toward black: a full frame of code 6 produced the black flash in the first write test. Which voltage each code applies (24 / 12 / 6 V on each side) is an **inference** from the order of the voltage block, not a measurement.

## Verification status

| Claim | How it was checked |
|---|---|
| Three-layer container | All 314 files decode to WBF with valid CRC-32 and header sums |
| Per-waveform header and checksums | `wfextra.py`: all 81 mode × band waveforms pass every check |
| `[target][source]` orientation | Matched by on-device frame captures |
| Mode names | Static analysis of the rendering library |
| Wire-code mapping | Static analysis, then frames drawn on the panel |
| Voltages per drive value | Inference only, not yet verified |
