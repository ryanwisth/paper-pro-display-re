# Display pipeline, pixel states and timing

What this project learned about how the reMarkable Paper Pro drives its colour e-ink panel. Each item carries its evidence level:

- **Device**: observed or measured on the tablet (frame captures, or frames drawn by our own writer).
- **Static**: read from the public firmware files or the public kernel/U-Boot sources, not exercised on the device.
- **Inference**: a reasoned reading that fits the evidence but was not tested directly.

The waveform file format is on its own page: [waveform-format.md](waveform-format.md).

## 1. The pipeline

```mermaid
flowchart LR
    UI["UI process<br/>(xochitl)"] -->|"pixels + mode"| LIB["vendor rendering library<br/>(dither, waveform lookup)"]
    LIB -->|"one 3-bit wire code<br/>per pixel per frame"| FB["DRM dumb framebuffers<br/>405 x 1084 XRGB8888"]
    FB -->|"page flip,<br/>one buffer per vblank"| LCDIF["LCD interface<br/>(i.MX display engine)"]
    LCDIF --> FPGA["bridge FPGA"] --> PANEL["Gallery 3 panel"]
```

The key result: the panel is driven **frame by frame from ordinary DRM framebuffers**. The vendor library does the e-ink work (choosing the waveform, dithering, sequencing phases) in user space and hands the kernel finished per-frame drive codes. Anything that can build those frames can drive the panel without the vendor library. (Device; the split of work is from Static reading.)

## 2. Pixel geometry

| Fact | Level |
|---|---|
| Panel: 1620 × 2160 pixels | Static, Device |
| Framebuffer: 405 × 1084 words, 32-bit XRGB8888 | Device (DRM probe) |
| Lines 0-3 of each buffer are spare; the image starts at line 4 | Static, Device |
| One 32-bit word carries 8 pixels: a block of 4 columns × 2 rows | Static, Device |
| Code `k` (0-7) sits in bits `3k .. 3k+2` of the word | Static, Device |
| Code `k` of word `w` on buffer line `l` is the pixel at column `4w + (k >> 1)`, row `2(l - 4) + (k & 1)` | Static, Device |

The layout was first solved from the boot-splash frames in the public boot image, then confirmed on the panel: the first test card held 1-pixel checkerboards, 1-pixel rows and columns, and 4 × 2 blocks, which would come out scrambled if the layout were wrong. They did not.

## 3. Update modes and timing

Nine waveform modes; phase counts are in [waveform-format.md](waveform-format.md#modes-and-phase-counts).

- **The design scan rate is 85 Hz** (WBF header byte 24). (Static)
- **A2 is the fast black-and-white mode**: 11 frames (about 0.13 s), two states only (black 2, white 28). (Static; drawn by our writer, Device)
- **Colour modes are much slower**: at room temperature STANDARD takes 86 frames (about 1.0 s), FAST 53, PEN 31, BEST 128. Cold bands are slower still (STANDARD 167 frames at 0-7 °C). (Static)
- **INIT** is a 161-phase full clear that ends at white. It matches the clear U-Boot performs at boot: 80 frames of one drive value, then 80 of the opposite. (Static, Device)
- **The UI uses PEN for pen strokes.** Two independent captures of a pen stroke matched the PEN waveform. (Device)
- **A page turn sends every sampled white pixel through state 29** (a PMODE white touch-up). All modes share that transition, so the page-turn mode could not be identified from the capture. (Device)
- **Load-time phase shift.** The library shifts STANDARD and TEXT_GLR waveforms in time. A capture showed the shift is keyed on the **target** state: pixels going to grey start 6 frames late, and pixels going to black get 5 extra frames of wire code 6 after the 86-frame waveform (100 % match within one capture window, `tools/pswin.py`). (Static, then Device)
- **A library environment setting switches STANDARD to FAST above a temperature threshold** (default read as 33 °C). (Inference from static reading; not tested)

### Charge balance (Static, decoded panel file, band 4)

E-ink pixels should see roughly zero net drive over time, or the panel can suffer image retention. `tools/wfextra.py` sums drive over each mode's own white → black → white round trip, using the inferred voltage per drive value:

| Mode | Round-trip net (volt-frames) | Reading |
|---|---|---|
| PEN, TEXT, TEXT_GLR, A2 | 0 | balanced as a round trip; each single transition is one-sided |
| STANDARD | -12 | close to balanced per transition (median per-transition net 18) |
| BEST | +6 | close to balanced per transition |
| FAST | -150 | not balanced |

This shaped the writer: every run it plays is planned so each pixel's total drive nets to zero or close to it. A first pass wrongly concluded that nothing balanced because it used one black state for all modes; each mode has its own black and white targets (§4).

## 4. Pixel states and colour

- **32 states per pixel** (the waveform grid is 32 × 32). Colour states read as 4 × colour + variant. (Static)
- **Black and white differ per mode**: STANDARD, FAST and BEST use black 2 / white 28; PEN, TEXT and TEXT_GLR use 0 / 30. (Static, from the library's target-state table)
- **Eight inks** are the corners of the RGB cube; slot = 4G + 2R + B, so slot 0 is black and 7 is white. A per-mode table maps each slot to a target state. For STANDARD: black 2, blue 12, red 4, magenta 20, green 8, cyan 16, yellow 24, white 28. (Static)
- **Two greys**: states 15 and 19, dark and light grey, used for luma of roughly 43-127 and 128-212 in grey rendering. Drawn by our writer, they showed as two distinct near-neutral greys between black and white in a photo. (Static, Device)
- **State 31** belongs to a run-time tenth mode not stored in the file. (Static)

## 5. Dither

The library turns RGB into ink slots per pixel. Re-implemented in [`tools/dither.py`](../tools/dither.py) from static analysis:

1. A 33 × 33 × 33 colour table holds, for each grid colour, a cumulative distribution over the 8 inks.
2. Each channel splits into a grid index (value >> 3) and a fraction (value & 7, with 255 counted as 8). The neighbouring grid entries are blended by tetrahedral interpolation.
3. The ink is chosen by comparing the blended distribution with a 64 × 64 blue-noise threshold matrix tiled over the screen.
4. Grey rendering is simpler: a weighted luma (G/4 + R/8 + B/8) plus noise picks black, dark grey, light grey or white.

A consequence: mid-grey in colour mode comes out as about one eighth of each ink. The tool's self-check confirms that, over the 64 × 64 matrix, the ink mix at grid colours matches the table within 0.2 %. The dither has **not yet been verified** pixel for pixel against frames the library produced on the device.

## 6. Kernel path and timing (Device unless marked)

- The UI creates **16 DRM dumb framebuffers** of 405 × 1084 and uses 15 of them as a ring. It writes frames ahead in batches (usually 5, up to all 15 after a pause).
- **Scan-out advances one buffer per vblank**, and each buffer is zeroed right after it is shown.
- The display pipe is **off when idle**; e-paper keeps the image with no power.
- Frame period: our writer's runs flipped one buffer every 11.77 ms (85 Hz). During the UI's own updates the measured vblank spacing varied between 6.4 and 11.4 ms. Why it varies is **not yet known**.
- The DRM vblank sequence counter steps by 2 per frame (cause unknown), so the tools time frames by kernel timestamps.
- **First-flip pitfall.** The device tree sets the LCD interface's `flip-before-wait` option, so a mode-set returns before its flip completes and an immediate page flip fails with `EBUSY`. The fix: wait one vblank after the mode-set and retry `EBUSY` once. (Cause from Static reading of the public kernel source; the fix worked on the device with 0 retries.)
- **Rails** are set once per boot to ±6, ±12 and ±24 V; the library hard-codes them and does not read the voltage blocks in the waveform file. (Device log; Static)
- **Autosleep.** The tablet suspends itself when idle, which froze an early capture. A timed kernel wakelock keeps it awake for the capture window and expires on its own. (Device)

## 7. What this project's own code drew on the panel

Every run used frames built on a Mac from the panel's own waveform file and played by `tablet/replay_boot.py` or `tablet/writer.py`. No vendor code ran during these draws.

| Run | Result |
|---|---|
| Replay of U-Boot's developer-mode boot sequence, attempt 1 | aborted safely on `EBUSY` at the first flip; nothing drawn |
| Same, after the vblank fix | success: 162 flips, 0 retries; black flash, then the boot notice |
| Mono test card: INIT clear, A2 draw, hold, A2 erase | drawn cleanly; geometry patches correct |
| Colour card, FAST draw | drawn |
| Colour card, PEN draw | drawn, exit code 0 |
| Partial update: a test picture, then the colour card over it with no INIT between | drawn, exit code 0; no ghosting seen by eye |
| Colour card plus grey swatches (states 15 and 19), STANDARD draw | drawn; a photo shows two distinct greys |

**Not yet verified:** that each colour swatch matches its label (no check was recorded), saturation and ghosting measured with a camera, and any long-term effect on the panel.

## 8. Still unknown

- The voltage each drive value applies (inferred only).
- Why the vblank period varies, and why the sequence counter steps by 2.
- Some internal dispatch choices in the library, and the meaning of some extension entries in its tables.
- The dither's exact match against library output on the device.
- Colour accuracy of drawn swatches beyond the photographed greys.
