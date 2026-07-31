# DECtalk DTC-01 for NVDA

An NVDA synthesizer driver that runs the **original 1984 DEC DECtalk DTC-01
firmware** in an emulator. The voice is the real hardware's, produced by the
same 68000 and TMS32010 code that shipped in the box — not a recreation or a
sample set.

> **No firmware is included.** The DTC-01 ROMs are Digital Equipment
> Corporation / Fonix property. You must supply your own dump. See
> [Providing the ROMs](#providing-the-roms).

## Status

**0.5.2 is released** — download the `.nvda-addon` from
[Releases](https://github.com/borris84/dectalk-dtc01/releases/latest). The
add-on checks for later releases itself and offers to install them.

Working and in daily use. Speech, all eight built-in voices, rate, volume,
and the firmware's Design Voice parameters are functional, along with
say-all, index reporting, and rate boost beyond the hardware's own ceiling.

| | |
|---|---|
| Latest release | 0.5.2 |
| Emulation speed | ~10x realtime (native C core) |
| Startup | ~0.5s to first speech |
| Latency | ~50ms typical from request to audio |
| NVDA | 2026.1 (x64); built and tested against 2026.1.1 |

Installed from the published package and verified on NVDA 2026.1.1 AMD64.
The 32-bit core ships in the same package but has not been run on a 32-bit
NVDA.

## Requirements

* NVDA 2025.1 or later (64-bit or 32-bit -- both emulator cores ship in the package)
* Your own DTC-01 v2.0 ROM dump (16 main-CPU chips + the `204`/`205` DSP pair)

## Providing the ROMs

Put the ROM files in:

```
%APPDATA%\nvda\dectalkDtc01\roms\
```

Filenames don't matter — chips are identified by content hash, so whatever
naming your dump uses will work. The driver validates the full set before
starting and refuses to run on an incomplete or altered one. You can also
point `DTC01_ROM_DIR` at a directory instead.

The expected set is the v2.0 firmware (first half tagged 23 Jul 84, second
half 02 Jul 84) with the `23-204f4` / `23-205f4` DSP pair. Exact SHA1s are
listed in [DESIGN.md](DESIGN.md) §2.

## Settings

Beyond the usual rate, volume and voice:

* **Rate boost** — time-compresses the audio so speech can exceed the
  firmware's 350 wpm ceiling, up to ~3x, with pitch unchanged.
* **Pitch, Inflection, Head size, Breathiness, Richness, Smoothness,
  Formant gain** — the firmware's real Design Voice parameters. Each slider
  is *relative to the selected voice*: 50 means that voice's own factory
  default, and the values come from the ROM itself rather than from
  documentation.
* **Formant gain** reduces level inside the synthesis chain rather than on
  the finished audio, so it can clear the clipping that extreme head-size
  settings provoke — something a volume control cannot do.
* **Variable Val** keeps whatever you set on it when you switch away and
  back, matching the slot's purpose on real hardware. The seven fixed voices
  reset to their own defaults.

Line joining during say-all is automatic: wrapped lines are reassembled and
split on real sentence boundaries, so a hard wrap mid-sentence doesn't
produce a pause.

## Building

Needs MSVC (Build Tools are enough) and Python 3.

```
tools\build_native.bat          # builds build\dtc01_x64.dll (and x86)
python tools\make_addon.py      # -> build\dectalkDtc01-<ver>-x64.nvda-addon
```

Packaging refuses to run if any NVDA symbol the add-on imports is missing
from your installed NVDA (`tools/check_nvda_api.py`), and the add-on is
verified to contain no ROM data.

Musashi's opcode tables are generated rather than checked in; the build
script produces them on first run and verifies them.

⚠ That step compiles `m68kmake` with `/Od` **deliberately**. At `/O2` MSVC
miscompiles it: every generated opcode mask loses bit 14, so none carries
the `0xff00` mask Musashi's own table builder scans for as a terminator, the
scan runs off the end of the array, and `m68k_init()` dies with an access
violation before a single instruction executes. It presents as "the DLL
loads but creating a machine crashes", nowhere near the real cause. The
build script checks the generated tables for that mask group and fails
loudly rather than handing you a DLL that crashes at runtime.
`native/musashi/VENDORING.md` has the full detail.

## Development

```
python tools\test_driver_offline.py   # driver logic against the real emulator
python tools\sayall_sim.py            # say-all, with NVDA's real handshake
python tools\compare_native.py        # C core vs the Python reference
python tools\check_no_roms.py         # refuse to commit firmware
```

The pure-Python emulator in `addon/synthDrivers/dectalkDtc01/emu/` is kept as
the readable reference implementation and correctness oracle; the C core in
`native/` is the one fast enough to drive a screen reader.

[DESIGN.md](DESIGN.md) is the working reference — hardware architecture,
verified ROM layout, the DECtalk command language as this firmware actually
implements it, and a log of what was measured against the real ROM (including
several places where the OCR'd manual turned out to be wrong).

## Licence

MIT — see [LICENSE](LICENSE), which also records the provenance of the
vendored Musashi core and the MAME-derived device emulation.
