"""Dev-only diagnostic: single-step the 68000 one instruction at a time via
the DectalkMachine, printing PC before each step, to pinpoint exactly which
instruction triggers a Unicorn CPU exception."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "addon" / "synthDrivers" / "dectalkDtc01"))

from unicorn.m68k_const import UC_M68K_REG_PC, UC_M68K_REG_SR, UC_M68K_REG_A7  # noqa: E402
from emu.machine import DectalkMachine  # noqa: E402


def main() -> int:
	rom_dir = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "roms_extracted")
	n = int(sys.argv[2]) if len(sys.argv) > 2 else 2000

	m = DectalkMachine(rom_dir, lambda s: None, lambda b: None)

	last_pcs = []
	for i in range(n):
		pc = m.uc.reg_read(UC_M68K_REG_PC)
		last_pcs.append(pc)
		if len(last_pcs) > 20:
			last_pcs.pop(0)
		try:
			m.uc.emu_start(pc, 0, 0, 1)
		except Exception as e:
			print(f"FAULT on step {i}, PC was {pc:#08x}")
			print("last 20 PCs:", [f"{p:#08x}" for p in last_pcs])
			sr = m.uc.reg_read(UC_M68K_REG_SR)
			a7 = m.uc.reg_read(UC_M68K_REG_A7)
			print(f"SR={sr:#06x} A7={a7:#08x}")
			# dump bytes at pc from the ROM image directly for inspection
			data = open(Path(rom_dir).parent / "build" / "maincpu.bin", "rb").read() if False else None
			try:
				code = m.uc.mem_read(pc, 16)
				print("bytes at PC:", code.hex(" "))
			except Exception as e2:
				print("couldn't read mem at PC:", e2)
			raise SystemExit(1)
		m._maybe_service_interrupt()

	print(f"Completed {n} steps without fault. Final PC={m.uc.reg_read(UC_M68K_REG_PC):#08x}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
