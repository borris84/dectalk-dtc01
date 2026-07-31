"""Dev-only: simulate NVDA's say-all handshake faithfully.

The earlier say-all tests queued every line up front, which quietly made
pipelining look like it worked -- there was always text waiting. Real say
all does **not** do that: it sends one line, then waits for that line's
index callback before sending the next. A driver that needs the next
chunk in order to flush the current one therefore deadlocks against a
synth that only reports an index once the audio has finished, and falls
back to whatever its timeout does.

This reproduces that handshake, so line-break behaviour can be measured
the way the user actually hears it.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import test_driver_offline as T  # noqa: E402


def run_say_all(synth, lines, timeout=30.0):
    """Feed `lines` the way NVDA's say all does: one at a time, each only
    after the previous line's index has been reported. Returns
    (audio_seconds, per-line index latencies)."""
    from test_driver_offline import IndexCommand

    got = threading.Event()
    seen = []

    notifier = sys.modules["synthDriverHandler"].synthIndexReached
    original = notifier.notify

    def notify(**kw):
        original(**kw)
        seen.append((kw.get("index"), time.time()))
        got.set()

    notifier.notify = notify
    sayAll = sys.modules["speech.sayAll"].SayAllHandler
    sayAll.running = True
    try:
        synth._player = T._FakePlayer()
        deadline = time.time() + timeout
        for i, line in enumerate(lines):
            got.clear()
            synth.speak([line, IndexCommand(i + 1)])
            # NVDA holds the next line until this one's index arrives.
            while not got.is_set() and time.time() < deadline:
                time.sleep(0.002)
        # How much audio existed *before* the final line was handed over.
        # A driver that buffers everything and only speaks at the end still
        # produces the right total duration, so total alone cannot tell the
        # two apart -- this is what catches "say all reads the first word
        # and nothing else while the cursor runs to the end".
        midway = len(bytes(synth._player.data)) // 2 / 10000.0
        synth._jobs.join()
        return len(bytes(synth._player.data)) // 2 / 10000.0, seen, midway
    finally:
        sayAll.running = False
        notifier.notify = original


def main() -> int:
    T.install_stubs()
    os.environ["DTC01_ROM_DIR"] = str(ROOT / "roms_extracted")
    sys.path.insert(0, str(ROOT / "addon"))
    from synthDrivers.dectalkDtc01 import SynthDriver

    T._FakePlayer.REALTIME = False
    synth = SynthDriver()
    synth._machineReady.wait(30)
    time.sleep(2.0)

    FULL = ("If you are reading this, then I am probably dead. "
            "This is unfortunate, but it's just the way it goes.")
    LINES = [
        "If you are reading this, then I am",
        "probably dead. This is unfortunate, but it's just",
        "the way it goes.",
    ]

    ideal, _, _ = run_say_all(synth, [FULL])
    print(f"unwrapped, one line          : {ideal:.2f}s   (ideal)")

    dur, seen, midway = run_say_all(synth, LINES)
    breaks = (dur - ideal) / max(len(LINES) - 1, 1) * 1000
    print(f"wrapped, during say all      : {dur:.2f}s   "
          f"(+{breaks:.0f} ms per line break), indexes={[i for i, _ in seen]}")
    print(f"audio already spoken when the last line was sent: {midway:.2f}s")
    if midway < 0.3:
        print("  ** FAIL: nothing was being spoken while lines were arriving --")
        print("     text is being buffered instead of played (say-all reads almost nothing)")
        return 1

    synth.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
