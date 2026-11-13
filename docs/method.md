# Method

How the findings were reached, what came from public files versus the device, and how the tools fit together.

## Ground rules followed

- **Own device, developer mode.** All device work used a Paper Pro owned by the author, in reMarkable's official developer mode, which gives a root shell over SSH. No security mechanism was bypassed or weakened.
- **Read-only first.** Every device script has a `dry` mode that only reads. Active writes to the panel came only after the read-only phase had produced a decoded waveform, a solved pixel layout and a working capture checker.
- **Nothing outside the home folder** on the device. Scripts live in `/home/root/display-re/`, and the UI is always restarted by an exit trap.
- **No vendor code runs during a draw.** Frames are built on a computer from the panel's waveform file; the tablet script only plays them.

## Phase 1: public firmware only (no device)

reMarkable publishes full OS update files. The work started from the public 3.27.3.0 update for the Paper Pro (`remarkable-production-image-3.27.3.0-ferrari-public.swu`). Its root filesystem image and boot partitions hold the display files: the 314 `GAL3_*.eink` waveforms, the colour tables, the FPGA bitstream and the rendering library.

From these files alone:

| Result | Tool |
|---|---|
| Read files out of the ext4 root image without mounting it | `tools/ext4r.py` |
| Parse the device tree for display settings | `tools/fdt.py` |
| Map ELF sections and cross-reference constant tables in the rendering library | `tools/elfr.py`, `tools/xref.py` |
| Solve the framebuffer pixel layout from U-Boot's boot-splash frames | (analysis; result in display-pipeline.md §2) |
| Decode the `.eink` container to WBF and verify all 314 files | `tools/eink.py`, `tools/panelwf.py` |
| Read mode names, wire codes, target-state tables and the dither from the library | static analysis; results encoded in `wfextra.py`, `dither.py`, `mkwriter.py` |
| Per-waveform headers, voltage blocks and charge balance | `tools/wfextra.py` |

`xref.py` expects a disassembly listing you produce yourself with standard tools; no listing is included here.

The library was only read; it was never run for any result in this repository.

## Phase 2: passive capture on the device

Goal: check the static reading against what the UI really sends to the panel, without changing anything.

- `tablet/drmprobe.py` lists the display pipe and the framebuffers the UI created. As root, a plain DRM client can look up those buffers (`GETFB`) and map them read-only (`MAP_DUMB` + `mmap`); no library injection is needed.
- `tablet/readbench.py` and `tablet/dumpfbs.py` size the read cost and snapshot buffers.
- `tablet/capture.py` (version 4) sweeps sampled lines of every ring buffer once per vblank, records each change, and re-reads it so a read torn by a write in progress can be told apart. It runs at a real-time priority below the display threads and holds a timed wakelock.
- On the computer, `tools/capdisp4.py` rebuilds display order from the ring and checks every captured pixel sequence against every mode's table (`tools/capcheck.py`). `tools/pswin.py` tests the phase-shift hypotheses on one window of an update.

Self-tests: `tools/capsynth.py`, `tools/capsynth4.py` and `tools/pstest.py` synthesise captures (random transitions, phase shifts, torn reads, a blind spot) and confirm the checker recovers the right mode and hypothesis. The v4 ring self-test passed 24 of 24 cases.

Capture went through four versions. Versions 1-3 missed frames: the ring buffers are zeroed right after display, and asking which buffer was on screen after each read cost several vblanks. Version 4 avoids that query and sweeps the whole ring instead.

## Phase 3: active writes

1. **Boot-sequence replay** (`tablet/replay_boot.py`, `run-replay.sh`). The first write reproduced exactly what U-Boot does in developer mode: a zero frame, 80 frames of code 6, 80 frames of the boot notice, two zero frames, pipe off. Choosing a sequence the panel already receives at every boot kept the first write low-risk. The frame data for the boot notice comes from the public boot image and is **not distributed**; `run-replay.sh` takes its path in `SPLASH_FRAME`.
2. **Writer** (`tools/mkwriter.py` on the computer, `tablet/writer.py` on the device, `run-writer.sh`). `mkwriter.py` reads the panel's waveform file and builds a *plan*: the unique frames plus the sessions that play them, with SHA-256 hashes and the temperature window the plan is valid for. `writer.py` re-checks every hash before any drive, takes DRM master, flips one frame per vblank, and always turns the pipe off and frees its buffers, even on a watchdog timeout.

Writer design choices and why:

| Choice | Reason |
|---|---|
| Build frames off-device | the tablet only plays verified data; no rendering code runs there |
| One plan per temperature band, picked from the tablet's sensor at run time | waveforms are tuned per band; a plan built for 25 °C must not play at 10 °C |
| Mono: INIT clear, A2 draw, hold, A2 erase | every pixel's drive nets to zero per value |
| Colour: INIT, then STANDARD, FAST or PEN from white; erase chosen by smallest worst-pixel net drive | measured charge balance (display-pipeline.md §3); INIT won (6 vs 12 volt-frames) |
| Greys left out of the default card | going 28 → 15 nets +210 volt-frames |
| Second copy only for frames played back to back | each flip targets a buffer not on screen, with less contiguous memory |
| Never write rail or VCOM controls | the panel's power settings stay as the OS set them |

## What you need to reproduce this

None of the following is included in this repository:

- The public OS update file named above, and the display files inside it (waveforms, colour tables, rendering library). The tools take their paths as arguments or environment variables.
- A Paper Pro in developer mode, with a Python 3 interpreter on the device at `/opt/bin/python3` (not part of the stock OS).
- Your panel's waveform file. The OS picks it from the panel identifiers in the device configuration; the matching file name appears in the system journal.

## Device safety

Wrong waveforms, unbalanced drive or interrupted sessions can leave ghost images or damage an e-ink panel. The write path in this repository was exercised on one device, at room temperature, for a handful of short runs. Use it at your own risk.
