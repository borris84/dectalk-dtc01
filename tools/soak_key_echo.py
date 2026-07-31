"""Dev-only: sustained key-echo soak test with a realistic audio device model.

Chases a *cumulative* degradation reported in real use: with key echo on,
audio gradually becomes "grainy and stretched", as if underrunning, and it
gets worse the longer the session runs.

The fake player used by test_driver_offline.py just sleeps, so it can never
show starvation. This one models an actual output device: a buffer that
drains in real time, blocking the writer when full and recording an
underrun whenever it runs dry while audio was still expected. That's what
"grainy and stretched" sounds like.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import test_driver_offline as T  # noqa: E402

SAMPLE_RATE = 10000
MAX_BUFFER_SECONDS = 0.4   # roughly what a WavePlayer keeps queued


class Device:
    """Audio sink that drains at real time."""

    def __init__(self):
        self.buffered = 0.0          # seconds of audio queued
        self.last = time.monotonic()
        self.underruns = 0
        self.underrunSeconds = 0.0
        self.fed = 0.0
        self.playing = False
        self.stopped = False

    def _advance(self):
        now = time.monotonic()
        dt = now - self.last
        self.last = now
        if not self.playing:
            return
        if self.buffered >= dt:
            self.buffered -= dt
        else:
            # Ran dry mid-utterance: this is the audible glitch.
            shortfall = dt - self.buffered
            self.buffered = 0.0
            self.underruns += 1
            self.underrunSeconds += shortfall
            self.playing = False

    # -- nvwave.WavePlayer-ish API ---------------------------------------
    def feed(self, block):
        self._advance()
        secs = len(block) / 2 / SAMPLE_RATE
        # Block while the device buffer is full, like the real one does.
        while self.buffered + secs > MAX_BUFFER_SECONDS:
            time.sleep(0.002)
            self._advance()
        self.buffered += secs
        self.fed += secs
        self.playing = True

    def idle(self):
        self._advance()

    def stop(self):
        self._advance()
        self.buffered = 0.0
        self.playing = False

    def pause(self, switch):
        pass

    def close(self):
        pass


def main() -> int:
    T.install_stubs()
    os.environ["DTC01_ROM_DIR"] = str(ROOT / "roms_extracted")
    sys.path.insert(0, str(ROOT / "addon"))
    from synthDrivers.dectalkDtc01 import SynthDriver

    synth = SynthDriver()
    synth._machineReady.wait(30)
    time.sleep(2.5)

    device = Device()
    synth._player = device

    text = "the quick brown fox jumps over the lazy dog"
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 0.15
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 200

    print(f"simulating key echo: one character every {interval*1000:.0f} ms, "
          f"{rounds} keystrokes")
    print(f"{'keystroke':>10} {'underruns':>10} {'lost audio':>11} "
          f"{'dirty':>6} {'fed':>8}")

    marks = []
    for i in range(rounds):
        ch = text[i % len(text)]
        synth.cancel()
        synth.speak([ch])
        time.sleep(interval)
        if (i + 1) % 25 == 0:
            marks.append((i + 1, device.underruns, device.underrunSeconds,
                          sum(synth._dirty), device.fed))
            print(f"{i+1:>10} {device.underruns:>10} "
                  f"{device.underrunSeconds:>10.2f}s {sum(synth._dirty):>6} "
                  f"{device.fed:>7.1f}s")

    print()
    if len(marks) >= 2:
        firstHalf = marks[len(marks) // 2 - 1]
        last = marks[-1]
        rate1 = firstHalf[1] / firstHalf[0]
        rate2 = (last[1] - firstHalf[1]) / (last[0] - firstHalf[0])
        print(f"underruns per keystroke: first half {rate1:.3f}, "
              f"second half {rate2:.3f}")
        if rate2 > rate1 * 1.5 and rate2 > 0.05:
            print("  -> DEGRADING over time (matches the reported symptom)")
        elif last[1]:
            print("  -> underruns present but steady, not cumulative")
        else:
            print("  -> no underruns")
    synth.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
