"""Dev-only boot test: run the full DTC-01 machine (Unicorn M68000 + our
TMS32010 + our DUART + FIFO glue) against the real ROMs and watch the LED
self-test status byte. The driver source's own comment block (see
research/mame_dectalk.cpp lines ~69-113, duplicated in DESIGN.md) documents
what each LED code means -- FFxx = ROM check fail, FExx = RAM check fail,
FD00 = DUART test, FB00 = TMS32010 extensive test. A healthy boot should
move through these without sticking on an FFxx/FExx failure code, and
should eventually start showing the state-machine status bits (bits 0-2)
described in the same comment block rather than an 0xFF/0xFE/0xFD/0xFB
error prefix.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "addon" / "synthDrivers" / "dectalkDtc01"))

from emu.machine import DectalkMachine  # noqa: E402


def main() -> int:
	rom_dir = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "roms_extracted")
	seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0

	audio_samples: list[int] = []
	host_bytes: list[int] = []

	def on_audio(sample: int) -> None:
		audio_samples.append(sample)

	def on_host_tx(byte: int) -> None:
		host_bytes.append(byte)

	m = DectalkMachine(rom_dir, on_audio, on_host_tx)

	led_history: list[tuple[float, int]] = [(0.0, m.led_state)]
	CHUNK = 0.01
	t0 = time.time()
	elapsed = 0.0
	try:
		while elapsed < seconds:
			m.run_seconds(CHUNK)
			elapsed += CHUNK
			if m.led_state != led_history[-1][1]:
				led_history.append((elapsed, m.led_state))
	except Exception as e:
		print(f"EXCEPTION at virtual t={elapsed:.4f}s (wall {time.time()-t0:.1f}s): {e!r}")
		import traceback
		traceback.print_exc()

	wall = time.time() - t0
	print(f"Ran {elapsed:.3f}s of virtual time in {wall:.1f}s wall time ({elapsed/max(wall,1e-9):.4f}x realtime)")
	print(f"Audio samples produced: {len(audio_samples)} (expect ~{int(elapsed*10000)})")
	print(f"Host TX bytes: {len(host_bytes)}")
	print("LED state changes (virtual_t, led_byte):")
	for t, led in led_history[:60]:
		print(f"  t={t:7.4f}  LED={led:#04x}")
	if len(led_history) > 60:
		print(f"  ... and {len(led_history)-60} more changes")

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
