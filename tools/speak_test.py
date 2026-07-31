"""Dev-only test: boot the machine, let it settle into normal operation,
then feed real text over the DUART host channel (as the DECtalk in-line
command language) and see whether it produces genuinely varying audio
(evidence of real synthesis) versus flat silence (evidence something's
still wrong downstream of boot). Dumps a .wav for later inspection.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "addon" / "synthDrivers" / "dectalkDtc01"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from emu.machine import DectalkMachine  # noqa: E402
from wav_writer import WavCollector  # noqa: E402


def main() -> int:
	rom_dir = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "roms_extracted")
	text = sys.argv[2] if len(sys.argv) > 2 else "[:np] Hello world.\r"
	seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0

	collector = WavCollector()
	host_bytes: list[int] = []

	m = DectalkMachine(rom_dir, collector.on_sample, host_bytes.append)

	print("Booting / settling...")
	m.run_seconds(0.3)
	print(f"LED after settle: {m.led_state:#04x}")

	print(f"Feeding text: {text!r}")
	m.duart.feed_rx_b(text.encode("ascii", "replace"))

	m.run_seconds(seconds)

	print(f"Final LED: {m.led_state:#04x}")
	print(f"Host TX bytes received: {len(host_bytes)}")
	if host_bytes:
		print("First host bytes:", bytes(host_bytes[:64]))

	stats = collector.stats()
	print("Audio stats:", stats)

	out_path = str(Path(__file__).resolve().parent.parent / "build" / "speak_test.wav")
	collector.write(out_path)
	print(f"Wrote {out_path}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
