"""Dev-only smoke test: run the TMS32010 core against the real DSP ROM with
no 68000/FIFO attached (BIO tied low = "no input available", IO ports are
no-ops) and confirm it executes sane, non-crashing, non-runaway code -- i.e.
it should settle into some kind of wait/poll loop rather than jumping into
the weeds. This does not validate audio correctness (needs the full 68000 +
FIFO harness for that, task #4) -- it only validates the CPU core itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "addon" / "synthDrivers" / "dectalkDtc01"))

from emu import rom_loader  # noqa: E402
from emu.tms32010 import TMS32010  # noqa: E402


def main() -> int:
	rom_dir = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "roms_extracted")
	words = rom_loader.dsp_words(rom_dir)

	io_reads: list[tuple[int, int]] = []
	io_writes: list[tuple[int, int]] = []

	def io_read(port: int) -> int:
		io_reads.append((port, 0))
		return 0

	def io_write(port: int, value: int) -> None:
		io_writes.append((port, value))

	def bio_read() -> int:
		return 0  # no input fifo data available -- BIOZ should never take

	dsp = TMS32010(words, io_read, io_write, bio_read)
	dsp.set_reset_line(False)

	pc_trace: list[int] = []
	total_cycles = 0
	STEPS = 200_000
	for i in range(STEPS):
		pc_trace.append(dsp.PC)
		cycles = dsp.step()
		total_cycles += cycles

	print(f"Executed {STEPS} instructions, {total_cycles} cycles")
	print(f"Final PC={dsp.PC:#06x} ACC={dsp.ACC:#010x} STR={dsp.STR:#06x} "
		f"AR0={dsp.AR[0]:#06x} AR1={dsp.AR[1]:#06x} T={dsp.Treg:#06x} P={dsp.Preg:#010x}")

	# Report the PC "footprint": the set of addresses visited in the last
	# 2000 steps. A healthy wait-loop should show a SMALL tight set of PCs
	# (it's polling BIO/IN in a short loop) rather than a huge scattered
	# range (which would suggest we've decoded into garbage / the weeds).
	tail = pc_trace[-2000:]
	unique_tail = sorted(set(tail))
	print(f"Unique PCs in final 2000 steps: {len(unique_tail)}")
	if len(unique_tail) <= 40:
		print("PCs:", [f"{p:#06x}" for p in unique_tail])
	else:
		print(f"PC range: {min(unique_tail):#06x} .. {max(unique_tail):#06x} (too many to list -- possibly not settled into a tight loop)")

	print(f"IO reads: {len(io_reads)}, IO writes: {len(io_writes)}")
	if io_writes:
		print("Sample IO writes (first 10):", io_writes[:10])

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
