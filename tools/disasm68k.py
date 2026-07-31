"""Minimal dev-only 68000 disassembler for reading DTC-01 firmware traces.
Mnemonic identification reuses the same Musashi ground-truth table already
validated against our CPU core (verify_opcodes.py); operand formatting is
hand-written but kept deliberately simple (readability for debugging, not
production-quality output).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_opcodes import parse_ground_truth  # noqa: E402

_GROUND_TRUTH = None


def _gt():
	global _GROUND_TRUTH
	if _GROUND_TRUTH is None:
		_GROUND_TRUTH = parse_ground_truth()
	return _GROUND_TRUTH


def s16(v):
	v &= 0xFFFF
	return v - 0x10000 if v & 0x8000 else v


def s8(v):
	v &= 0xFF
	return v - 0x100 if v & 0x80 else v


class _Reader:
	def __init__(self, bus, pc):
		self.bus = bus
		self.pc = pc

	def fetch16(self):
		v = self.bus.read16(self.pc)
		self.pc += 2
		return v

	def fetch32(self):
		hi = self.fetch16()
		lo = self.fetch16()
		return (hi << 16) | lo


def _format_ea(r: _Reader, mode: int, reg: int, size_letter: str) -> str:
	if mode == 0:
		return f"D{reg}"
	if mode == 1:
		return f"A{reg}"
	if mode == 2:
		return f"(A{reg})"
	if mode == 3:
		return f"(A{reg})+"
	if mode == 4:
		return f"-(A{reg})"
	if mode == 5:
		disp = s16(r.fetch16())
		return f"{disp}(A{reg})"
	if mode == 6:
		ext = r.fetch16()
		xreg = (ext >> 12) & 7
		xkind = "A" if (ext & 0x8000) else "D"
		xsize = "L" if (ext & 0x0800) else "W"
		disp = s8(ext & 0xFF)
		return f"{disp}({xkind}{xreg}.{xsize},A{reg})"
	if mode == 7:
		if reg == 0:
			return f"${r.fetch16():04X}.W"
		if reg == 1:
			return f"${r.fetch32():08X}.L"
		if reg == 2:
			base = r.pc
			disp = s16(r.fetch16())
			return f"${(base + disp) & 0xFFFFFFFF:06X}(PC)"
		if reg == 3:
			base = r.pc
			ext = r.fetch16()
			xreg = (ext >> 12) & 7
			xkind = "A" if (ext & 0x8000) else "D"
			disp = s8(ext & 0xFF)
			return f"{disp}({xkind}{xreg},PC)"
		if reg == 4:
			if size_letter == "L":
				return f"#${r.fetch32():08X}"
			return f"#${r.fetch16():04X}"
	return "??"


def disassemble(bus, pc: int) -> tuple[str, int]:
	"""Returns (text, next_pc). Best-effort: falls back to raw hex for
	anything the simple formatter doesn't specifically recognize."""
	r = _Reader(bus, pc)
	opcode = r.fetch16()
	names = _gt().get(opcode)
	mnem = sorted(names)[0] if names else "???"

	dreg = (opcode >> 9) & 7
	mode = (opcode >> 3) & 7
	reg = opcode & 7
	dmode = (opcode >> 6) & 7

	try:
		if 0x40C0 <= opcode <= 0x40FF:
			text = f"MOVE SR,{_format_ea(r, mode, reg, 'W')}"
		elif 0x44C0 <= opcode <= 0x44FF:
			text = f"MOVE {_format_ea(r, mode, reg, 'W')},CCR"
		elif 0x46C0 <= opcode <= 0x46FF:
			text = f"MOVE {_format_ea(r, mode, reg, 'W')},SR"
		elif 0x4E60 <= opcode <= 0x4E67:
			text = f"MOVE A{reg},USP"
		elif 0x4E68 <= opcode <= 0x4E6F:
			text = f"MOVE USP,A{reg}"
		elif 0x1000 <= opcode < 0x4000:  # MOVE.B/W/L
			size = {1: "B", 3: "W", 2: "L"}[(opcode >> 12) & 3]
			src = _format_ea(r, mode, reg, size)
			dst = _format_ea(r, dmode, dreg, size)
			text = f"MOVE.{size} {src},{dst}"
		elif mnem == "moveq":
			data = s8(opcode & 0xFF)
			text = f"MOVEQ #{data},D{dreg}"
		elif mnem in ("addq", "subq"):
			data = dreg if dreg else 8
			size = {0: "B", 1: "W", 2: "L"}[(opcode >> 6) & 3]
			ea = _format_ea(r, mode, reg, size)
			text = f"{mnem.upper()}.{size} #{data},{ea}"
		elif mnem in ("bra", "bsr") or (opcode & 0xF000) == 0x6000:
			cc = (opcode >> 8) & 0xF
			ccnames = ["T", "F", "HI", "LS", "CC", "CS", "NE", "EQ", "VC", "VS", "PL", "MI", "GE", "LT", "GT", "LE"]
			disp8 = opcode & 0xFF
			base = r.pc
			if disp8 == 0:
				disp = s16(r.fetch16())
			elif disp8 == 0xFF:
				disp = r.fetch32()
			else:
				disp = s8(disp8)
			target = (base + disp) & 0xFFFFFFFF
			name = "BRA" if cc == 0 else ("BSR" if cc == 1 else f"B{ccnames[cc]}")
			text = f"{name} ${target:06X}"
		elif mnem == "dbcc" or ((opcode & 0xF0F8) == 0x50C8):
			cc = (opcode >> 8) & 0xF
			ccnames = ["T", "F", "HI", "LS", "CC", "CS", "NE", "EQ", "VC", "VS", "PL", "MI", "GE", "LT", "GT", "LE"]
			base = r.pc
			disp = s16(r.fetch16())
			target = (base + disp) & 0xFFFFFFFF
			text = f"DB{ccnames[cc]} D{reg},${target:06X}"
		elif mnem in ("jmp", "jsr"):
			ea = _format_ea(r, mode, reg, "L")
			text = f"{mnem.upper()} {ea}"
		elif mnem == "lea":
			ea = _format_ea(r, mode, reg, "L")
			text = f"LEA {ea},A{dreg}"
		elif mnem == "pea":
			ea = _format_ea(r, mode, reg, "L")
			text = f"PEA {ea}"
		elif mnem in ("clr", "neg", "negx", "not", "tst"):
			size = {0: "B", 1: "W", 2: "L"}[(opcode >> 6) & 3]
			ea = _format_ea(r, mode, reg, size)
			text = f"{mnem.upper()}.{size} {ea}"
		elif mnem in ("cmp", "add", "sub", "and", "or", "eor"):
			opmode = (opcode >> 6) & 7
			size = {0: "B", 1: "W", 2: "L", 4: "B", 5: "W", 6: "L"}.get(opmode, "?")
			ea = _format_ea(r, mode, reg, size)
			if opmode < 4:
				text = f"{mnem.upper()}.{size} {ea},D{dreg}"
			else:
				text = f"{mnem.upper()}.{size} D{dreg},{ea}"
		elif mnem in ("cmpa", "adda", "suba"):
			size = "W" if ((opcode >> 6) & 7) == 3 else "L"
			ea = _format_ea(r, mode, reg, size)
			text = f"{mnem.upper()}.{size} {ea},A{dreg}"
		elif mnem in ("movea",):
			size = "W" if ((opcode >> 12) & 3) == 3 else "L"
			ea = _format_ea(r, mode, reg, size)
			text = f"MOVEA.{size} {ea},A{dreg}"
		elif mnem == "chk":
			ea = _format_ea(r, mode, reg, "W")
			text = f"CHK {ea},D{dreg}"
		elif mnem in ("btst", "bchg", "bclr", "bset"):
			ea = _format_ea(r, mode, reg, "B")
			if opcode & 0x0100:
				text = f"{mnem.upper()} D{dreg},{ea}"
			else:
				bit = r.fetch16() & 0xFF
				text = f"{mnem.upper()} #{bit},{ea}"
		elif mnem in ("movem",):
			maskpos = r.pc
			mask = r.fetch16()
			ea = _format_ea(r, mode, reg, "L")
			text = f"MOVEM #{mask:04X},{ea}"
		elif mnem == "trap":
			text = f"TRAP #{opcode & 0xF}"
		elif mnem in ("rts", "rte", "rtr", "nop", "reset", "illegal"):
			text = mnem.upper()
		elif mnem == "stop":
			imm = r.fetch16()
			text = f"STOP #${imm:04X}"
		elif mnem == "link":
			disp = s16(r.fetch16())
			text = f"LINK A{reg},#{disp}"
		elif mnem == "unlk":
			text = f"UNLK A{reg}"
		elif mnem == "swap":
			text = f"SWAP D{reg}"
		elif mnem == "ext":
			text = f"EXT D{reg}"
		elif mnem in ("moveusp",):
			text = f"MOVE USP,A{reg}" if (opcode & 8) else f"MOVE A{reg},USP"
		elif mnem in ("ori", "andi", "subi", "addi", "eori", "cmpi"):
			size = {0: "B", 1: "W", 2: "L"}[(opcode >> 6) & 3]
			imm = r.fetch32() if size == "L" else r.fetch16()
			ea = _format_ea(r, mode, reg, size)
			text = f"{mnem.upper().replace('I','I')} #${imm:X},{ea}"
		else:
			text = f"{mnem.upper()}"
	except Exception as e:  # noqa: BLE001
		text = f"???({opcode:#06x}, err={e})"

	return f"{text}", r.pc


if __name__ == "__main__":
	# quick self-test: disassemble a range from a rom dir
	sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "addon" / "synthDrivers" / "dectalkDtc01"))
	from emu import rom_loader

	rom_dir = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "roms_extracted")
	start = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x1FE
	count = int(sys.argv[3]) if len(sys.argv) > 3 else 40

	image = rom_loader.build_main_cpu_image(rom_dir)

	class RomBus:
		def read16(self, addr):
			return (image[addr] << 8) | image[addr + 1]

	bus = RomBus()
	pc = start
	for _ in range(count):
		text, next_pc = disassemble(bus, pc)
		print(f"{pc:06X}: {text}")
		pc = next_pc
