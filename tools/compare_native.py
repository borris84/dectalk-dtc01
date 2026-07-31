"""Dev-only: cross-check the native C emulator against the pure-Python
reference implementation.

The Python core is the correctness oracle -- it is the one whose output a
human has actually listened to and confirmed as good speech (DESIGN.md
§11-12). This compares the two on identical input.

Important: bit-identical audio is NOT expected. The Python core carries
its own approximate 68000 cycle counts, while the C build uses Musashi's
accurate ones, so the 68000<->DSP interleaving differs slightly and the
DSP's sample stream can land differently. What must match is the logical
behaviour:

  * host TX bytes -- a protocol-level output, timing-insensitive
  * LED / boot state
  * speech envelope: where audio starts, stops, and how loud it is

so those are checked explicitly, and the raw waveform is compared
statistically rather than for exact equality.
"""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "addon" / "synthDrivers" / "dectalkDtc01"))

from emu.machine import DectalkMachine  # noqa: E402
from emu.native import NativeMachine     # noqa: E402

SETTLE_SAMPLES = 5000     # 0.5 s
SPEAK_SAMPLES = 60000     # 6.0 s
TEXT = b"[:np] Hello world.\r"


def to_signed(raw: int) -> int:
    """DAC word -> signed PCM (see DESIGN.md §12)."""
    v = (raw ^ 0x8000) & 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def run_python():
    samples: list[int] = []
    host: list[int] = []
    m = DectalkMachine(str(ROOT / "roms_extracted"),
                       lambda s: samples.append(to_signed(s)),
                       host.append)
    t0 = time.time()
    m.run_seconds(SETTLE_SAMPLES / 10000)
    m.duart.feed_rx_b(TEXT)
    m.run_seconds(SPEAK_SAMPLES / 10000)
    wall = time.time() - t0
    return samples, bytes(host), m.led_state, wall


def run_native():
    m = NativeMachine(str(ROOT / "roms_extracted"))
    t0 = time.time()
    samples = m.run_samples(SETTLE_SAMPLES)
    m.feed_text(TEXT)
    samples += m.run_samples(SPEAK_SAMPLES)
    wall = time.time() - t0
    return samples, m.read_host_tx(), m.led_state, wall, m.unmapped_accesses


def envelope(samples: list[int], block: int = 1000, thresh: int = 200):
    """Return list of (block_index, peak) for blocks with real signal, plus
    the first/last active block -- i.e. where speech actually happens."""
    active = []
    for i in range(0, len(samples), block):
        chunk = samples[i:i + block]
        peak = max((abs(x) for x in chunk), default=0)
        if peak > thresh:
            active.append((i // block, peak))
    first = active[0][0] if active else None
    last = active[-1][0] if active else None
    return active, first, last


def rms(samples: list[int]) -> float:
    if not samples:
        return 0.0
    return (sum(x * x for x in samples) / len(samples)) ** 0.5


def write_wav(path: Path, samples: list[int]) -> None:
    import struct
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(10000)
        w.writeframes(b"".join(struct.pack("<h", s) for s in samples))


def main() -> int:
    print("running native C core ...")
    n_samples, n_host, n_led, n_wall, n_unmapped = run_native()
    print(f"  {len(n_samples)} samples in {n_wall:.2f}s wall "
          f"({len(n_samples)/10000/n_wall:.2f}x realtime), unmapped={n_unmapped}")

    print("running pure-Python reference (slow) ...")
    p_samples, p_host, p_led, p_wall = run_python()
    print(f"  {len(p_samples)} samples in {p_wall:.2f}s wall "
          f"({len(p_samples)/10000/p_wall:.3f}x realtime)")

    print()
    print("=" * 62)
    ok = True

    # --- host TX: must match exactly -------------------------------------
    print(f"host TX  python: {p_host!r}")
    print(f"host TX  native: {n_host!r}")
    if p_host == n_host:
        print("  -> MATCH (exact)")
    else:
        print("  -> MISMATCH  ** logical divergence, investigate **")
        ok = False

    # --- LED --------------------------------------------------------------
    print(f"LED      python: {p_led:#04x}   native: {n_led:#04x}"
          f"   -> {'MATCH' if p_led == n_led else 'MISMATCH'}")
    if p_led != n_led:
        ok = False

    if n_unmapped:
        print(f"  ** native made {n_unmapped} unmapped memory accesses **")
        ok = False

    # --- audio ------------------------------------------------------------
    n = min(len(p_samples), len(n_samples))
    exact = sum(1 for i in range(n) if p_samples[i] == n_samples[i])
    print()
    print(f"audio    samples compared: {n}")
    print(f"  exact-equal samples: {exact} ({100.0*exact/n:.2f}%)"
          "   [not expected to be 100% -- see module docstring]")
    print(f"  python  min={min(p_samples)} max={max(p_samples)} rms={rms(p_samples):.1f}")
    print(f"  native  min={min(n_samples)} max={max(n_samples)} rms={rms(n_samples):.1f}")

    p_act, p_first, p_last = envelope(p_samples)
    n_act, n_first, n_last = envelope(n_samples)
    print(f"  speech blocks (100ms each): python={len(p_act)}  native={len(n_act)}")
    print(f"  first active block: python={p_first}  native={n_first}")
    print(f"  last  active block: python={p_last}   native={n_last}")

    # Envelope agreement is the meaningful audio check.
    if p_first is None or n_first is None:
        print("  -> ** one side produced no speech at all **")
        ok = False
    else:
        if abs(p_first - n_first) <= 2 and abs(p_last - n_last) <= 3:
            print("  -> speech envelope MATCHES (within tolerance)")
        else:
            print("  -> ** speech envelope differs materially **")
            ok = False
        r = rms(n_samples) / rms(p_samples) if rms(p_samples) else 0
        print(f"  rms ratio native/python = {r:.3f}"
              f"  -> {'OK' if 0.8 <= r <= 1.25 else '** level differs **'}")
        if not (0.8 <= r <= 1.25):
            ok = False

    out_dir = ROOT / "build"
    write_wav(out_dir / "compare_python.wav", p_samples)
    write_wav(out_dir / "compare_native.wav", n_samples)
    print()
    print(f"wrote {out_dir/'compare_python.wav'} and {out_dir/'compare_native.wav'}")
    print(f"speedup: native is {(p_wall/n_wall):.1f}x faster than pure Python")
    print("=" * 62)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
