"""M68000 instruction set: builds the M68000Core's opcode dispatch table.

Organized by the 68000's own top-level opcode groups (bits 15-12), which is
how the Motorola Programmer's Reference Manual itself organizes the
instruction set -- these are public architecture facts, not derived from
any particular implementation. Each _install_* method below populates a
slice of the 65536-entry table by iterating the relevant bit fields rather
than writing out every case by hand.
"""

from __future__ import annotations

from .m68000 import (
	M68000Core, BusError, s8, s16, s32,
	MASK8, MASK16, MASK32,
	SR_T, SR_S, SR_IPL_MASK, SR_X, SR_N, SR_Z, SR_V, SR_C,
	VEC_ILLEGAL, VEC_ZERO_DIVIDE, VEC_CHK, VEC_TRAPV, VEC_PRIVILEGE,
	VEC_LINE_A, VEC_LINE_F, VEC_TRAP_BASE,
)

SIZE_BYTE, SIZE_WORD, SIZE_LONG = 1, 2, 4
_STD_SIZE = {0: SIZE_BYTE, 1: SIZE_WORD, 2: SIZE_LONG}
_MOVE_SIZE = {1: SIZE_BYTE, 3: SIZE_WORD, 2: SIZE_LONG}
_SIZE_MASK = {1: MASK8, 2: MASK16, 4: MASK32}
_SIZE_MSB = {1: 0x80, 2: 0x8000, 4: 0x80000000}
_SIZE_BITS = {1: 8, 2: 16, 4: 32}

# EA mode/reg combos that are valid as a *destination* (no An-direct unless
# the instruction says so, no immediate, no PC-relative).
def _alterable(mode: int, reg: int) -> bool:
	if mode == 7 and reg in (2, 3, 4):
		return False
	return True


def _data_alterable(mode: int, reg: int) -> bool:
	return mode != 1 and _alterable(mode, reg)


class M68000OpsMixin:
	# -- shared flag helpers ------------------------------------------------
	def _flags_add(self, a: int, b: int, r: int, size: int) -> int:
		msb = _SIZE_MSB[size]
		mask = _SIZE_MASK[size]
		a &= mask; b &= mask; r &= mask
		c = 1 if (a + b) > mask else 0
		v = 1 if (~(a ^ b) & (a ^ r)) & msb else 0
		flags = 0
		if r == 0:
			flags |= SR_Z
		if r & msb:
			flags |= SR_N
		if v:
			flags |= SR_V
		if c:
			flags |= SR_C | SR_X
		return flags

	def _flags_sub(self, a: int, b: int, r: int, size: int, with_x: bool = True) -> int:
		msb = _SIZE_MSB[size]
		mask = _SIZE_MASK[size]
		a &= mask; b &= mask; r &= mask
		c = 1 if a < b else 0
		v = 1 if ((a ^ b) & (a ^ r)) & msb else 0
		flags = 0
		if r == 0:
			flags |= SR_Z
		if r & msb:
			flags |= SR_N
		if v:
			flags |= SR_V
		if c:
			flags |= SR_C | (SR_X if with_x else 0)
		return flags

	def _flags_logic(self, r: int, size: int) -> int:
		msb = _SIZE_MSB[size]
		mask = _SIZE_MASK[size]
		r &= mask
		flags = 0
		if r == 0:
			flags |= SR_Z
		if r & msb:
			flags |= SR_N
		return flags  # V=C=0

	def _set_nzvc(self, flags: int, keep_x: bool = False) -> None:
		mask = SR_N | SR_Z | SR_V | SR_C | (0 if keep_x else SR_X)
		self.SR = (self.SR & ~mask) | (flags & mask)

	def _cond_true(self, cc: int) -> bool:
		sr = self.SR
		n = bool(sr & SR_N); z = bool(sr & SR_Z); v = bool(sr & SR_V); c = bool(sr & SR_C)
		return {
			0: True, 1: False,  # T, F
			2: (not c) and (not z),  # HI
			3: c or z,  # LS
			4: not c,  # CC
			5: c,  # CS
			6: not z,  # NE
			7: z,  # EQ
			8: not v,  # VC
			9: v,  # VS
			10: not n,  # PL
			11: n,  # MI
			12: n == v,  # GE
			13: n != v,  # LT
			14: (n == v) and (not z),  # GT
			15: z or (n != v),  # LE
		}[cc]

	# =====================================================================
	def _install_instructions(self, table):
		self._install_move(table)
		self._install_moveq(table)
		self._install_group0(table)
		self._install_group4(table)
		self._install_group5(table)
		self._install_group6(table)
		self._install_group8(table)
		self._install_group9_d(table)
		self._install_groupb(table)
		self._install_groupc(table)
		self._install_groupe(table)
		# Line-A (0xA000-0xAFFF) and Line-F (0xF000-0xFFFF) are permanently
		# unassigned in the base 68000 ISA (reserved for line-1010/1111
		# emulator traps, e.g. FPU coprocessor instructions on later chips
		# the DTC-01 doesn't have) -- any opcode in these ranges must raise
		# its own dedicated vector, not the generic illegal-instruction one.
		for op in range(0xA000, 0xB000):
			if table[op] is None:
				table[op] = self._make_line_trap(VEC_LINE_A)
		for op in range(0xF000, 0x10000):
			if table[op] is None:
				table[op] = self._make_line_trap(VEC_LINE_F)

	def _make_line_trap(self, vector_num):
		def op(opcode):
			self.PC = (self.PC - 2) & MASK32
			self.raise_exception(vector_num)
			return 34
		return op

	# -- MOVE / MOVEA -----------------------------------------------------
	def _install_move(self, table):
		for size_bits, size in _MOVE_SIZE.items():
			for dreg in range(8):
				for dmode in range(8):
					if dmode == 7 and dreg > 1:
						# destination must be alterable: mode7 only allows
						# abs.w(0)/abs.l(1), not pc-relative/immediate.
						continue
					for smode in range(8):
						for sreg in range(8):
							if smode == 7 and sreg > 4:
								continue
							if dmode == 1 and size == SIZE_BYTE:
								continue  # MOVEA has no byte form
							if smode == 1 and size == SIZE_BYTE:
								continue  # byte-sized access to an address register isn't meaningful
							opcode = (0 << 14) | (size_bits << 12) | (dreg << 9) | (dmode << 6) | (smode << 3) | sreg
							table[opcode] = self._make_move(size, dmode, dreg, smode, sreg)

	def _make_move(self, size, dmode, dreg, smode, sreg):
		is_movea = dmode == 1

		def op(opcode):
			src = self.resolve_ea(smode, sreg, size)
			val = self.ea_read(src, size)
			if is_movea:
				self.set_a(dreg, s32(s16(val) if size == SIZE_WORD else s32(val)))
			else:
				dst = self.resolve_ea(dmode, dreg, size)
				self.ea_write(dst, size, val)
				self._set_nzvc(self._flags_logic(val, size), keep_x=True)
			return 8
		return op

	# -- MOVEQ --------------------------------------------------------------
	def _install_moveq(self, table):
		for reg in range(8):
			for data in range(256):
				opcode = (0b0111 << 12) | (reg << 9) | (0 << 8) | data
				table[opcode] = self._make_moveq(reg, data)

	def _make_moveq(self, reg, data):
		value = s8(data) & MASK32

		def op(opcode):
			self.D[reg] = value
			self._set_nzvc(self._flags_logic(value, SIZE_LONG), keep_x=True)
			return 4
		return op

	# -- Group 0: immediate ops, bit ops, MOVEP ---------------------------
	def _install_group0(self, table):
		# ORI/ANDI/SUBI/ADDI/EORI/CMPI #imm,<ea>  --  0000 ooo0 ss mmm rrr
		op_names = {0b000: "or", 0b001: "and", 0b010: "sub", 0b011: "add", 0b101: "eor", 0b110: "cmp"}
		for ooo, name in op_names.items():
			for ss, size in _STD_SIZE.items():
				for mode in range(8):
					for reg in range(8):
						if mode == 1:
							continue  # no An-direct for these
						if mode == 7 and reg >= 2:
							# immediate-as-destination is never valid; the CCR/SR
							# forms (mode=7,reg=4) are separate fixed-opcode
							# instructions handled below.
							continue
						opcode = (ooo << 9) | (ss << 6) | (mode << 3) | reg
						table[opcode] = self._make_imm_op(name, size, mode, reg)
		# ORI/ANDI/EORI to CCR (byte) and SR (word)
		table[0x003C] = self._make_imm_to_ccr("or")
		table[0x007C] = self._make_imm_to_sr("or")
		table[0x023C] = self._make_imm_to_ccr("and")
		table[0x027C] = self._make_imm_to_sr("and")
		table[0x0A3C] = self._make_imm_to_ccr("eor")
		table[0x0A7C] = self._make_imm_to_sr("eor")

		# BTST/BCHG/BCLR/BSET dynamic (bit# in Dn) -- 0000 rrr1 oo mmm rrr
		# Verified against Musashi's allowed-ea columns: BTST dynamic allows
		# mode7 reg0-4 (abs.w/abs.l/pc-disp/pc-idx/immediate -- it's
		# read-only, so even immediate is legal); BCHG/BCLR/BSET (dynamic
		# AND static) only allow mode7 reg0-1 (abs.w/abs.l) since they
		# write back and PC-relative/immediate destinations aren't
		# writable; BTST static allows mode7 reg0-3 (no immediate).
		for dreg in range(8):
			for oo, bname in ((0, "btst"), (1, "bchg"), (2, "bclr"), (3, "bset")):
				max_reg7 = 4 if bname == "btst" else 1
				for mode in range(8):
					for reg in range(8):
						if mode == 1:
							continue
						if mode == 7 and reg > max_reg7:
							continue
						opcode = (dreg << 9) | (1 << 8) | (oo << 6) | (mode << 3) | reg
						table[opcode] = self._make_bitop(bname, ("d", dreg), mode, reg)
		# BTST/BCHG/BCLR/BSET static (bit# immediate) -- 0000 1000 oo mmm rrr
		for oo, bname in ((0, "btst"), (1, "bchg"), (2, "bclr"), (3, "bset")):
			max_reg7 = 3 if bname == "btst" else 1
			for mode in range(8):
				for reg in range(8):
					if mode == 1:
						continue
					if mode == 7 and reg > max_reg7:
						continue
					opcode = (0b1000 << 8) | (oo << 6) | (mode << 3) | reg
					table[opcode] = self._make_bitop(bname, ("imm", None), mode, reg)

		# MOVEP  0000 rrr1 oo001 aaa
		for dreg in range(8):
			for oo in range(4):
				for areg in range(8):
					opcode = (dreg << 9) | (1 << 8) | (oo << 6) | (1 << 3) | areg
					table[opcode] = self._make_movep(dreg, oo, areg)

	def _make_imm_op(self, name, size, mode, reg):
		def op(opcode):
			imm = self.fetch32() if size == SIZE_LONG else self.fetch16()
			imm &= _SIZE_MASK[size]
			ea = self.resolve_ea(mode, reg, size)
			a = self.ea_read(ea, size)
			if name == "or":
				r = a | imm
				self.ea_write(ea, size, r); self._set_nzvc(self._flags_logic(r, size), keep_x=True)
			elif name == "and":
				r = a & imm
				self.ea_write(ea, size, r); self._set_nzvc(self._flags_logic(r, size), keep_x=True)
			elif name == "eor":
				r = a ^ imm
				self.ea_write(ea, size, r); self._set_nzvc(self._flags_logic(r, size), keep_x=True)
			elif name == "add":
				r = (a + imm) & _SIZE_MASK[size]
				self.ea_write(ea, size, r); self._set_nzvc(self._flags_add(a, imm, r, size))
			elif name == "sub":
				r = (a - imm) & _SIZE_MASK[size]
				self.ea_write(ea, size, r); self._set_nzvc(self._flags_sub(a, imm, r, size))
			elif name == "cmp":
				r = (a - imm) & _SIZE_MASK[size]
				self._set_nzvc(self._flags_sub(a, imm, r, size), keep_x=True)
			return 8
		return op

	def _make_imm_to_ccr(self, name):
		def op(opcode):
			imm = self.fetch16() & MASK8
			ccr = self.SR & 0xFF
			r = {"or": ccr | imm, "and": ccr & imm, "eor": ccr ^ imm}[name]
			self.SR = (self.SR & 0xFF00) | (r & 0xFF)
			return 20
		return op

	def _make_imm_to_sr(self, name):
		def op(opcode):
			imm = self.fetch16() & MASK16
			if not self.supervisor:
				self.PC = (self.PC - 4) & MASK32
				self.raise_exception(VEC_PRIVILEGE)
				return 34
			r = {"or": self.SR | imm, "and": self.SR & imm, "eor": self.SR ^ imm}[name]
			self._sr_write(r)
			return 20
		return op

	def _make_bitop(self, name, bitsrc, mode, reg):
		def op(opcode):
			if bitsrc[0] == "d":
				bitnum = self.get_d(bitsrc[1], SIZE_LONG)
			else:
				bitnum = self.fetch16() & 0xFF
			size = SIZE_LONG if mode == 0 else SIZE_BYTE
			bitnum %= 32 if size == SIZE_LONG else 8
			ea = self.resolve_ea(mode, reg, size)
			val = self.ea_read(ea, size)
			bit = (val >> bitnum) & 1
			self._set_flags(SR_Z, 0 if bit else SR_Z)
			if name == "btst":
				return 4
			newval = val
			if name == "bchg":
				newval = val ^ (1 << bitnum)
			elif name == "bclr":
				newval = val & ~(1 << bitnum)
			elif name == "bset":
				newval = val | (1 << bitnum)
			self.ea_write(ea, size, newval)
			return 8
		return op

	def _make_movep(self, dreg, oo, areg):
		word_to_reg = oo == 0
		long_to_reg = oo == 1
		word_from_reg = oo == 2

		def op(opcode):
			disp = s16(self.fetch16())
			addr = (self.get_a(areg) + disp) & MASK32
			if oo in (0, 1):
				n = 2 if oo == 0 else 4
				val = 0
				for i in range(n):
					val = (val << 8) | self.read8((addr + i * 2) & MASK32)
				self.set_d(dreg, SIZE_WORD if oo == 0 else SIZE_LONG, val)
			else:
				n = 2 if oo == 2 else 4
				val = self.get_d(dreg, SIZE_WORD if oo == 2 else SIZE_LONG)
				for i in range(n):
					shift = (n - 1 - i) * 8
					self.write8((addr + i * 2) & MASK32, (val >> shift) & 0xFF)
			return 16
		return op

	# -- Group 4: misc ------------------------------------------------------
	def _install_group4(self, table):
		for ss, size in _STD_SIZE.items():
			for mode in range(8):
				for reg in range(8):
					if mode == 7 and reg > 1:
						continue
					if mode == 1:
						continue
					# Verified against Musashi's m68k_in.c opcode table:
					# negx=0x4000, clr=0x4200, neg=0x4400, not=0x4600
					# (bits11-8 = 0000/0010/0100/0110 respectively).
					table[0x4000 | (ss << 6) | (mode << 3) | reg] = self._make_unary("negx", size, mode, reg)
					table[0x4200 | (ss << 6) | (mode << 3) | reg] = self._make_unary("clr", size, mode, reg)
					table[0x4400 | (ss << 6) | (mode << 3) | reg] = self._make_unary("neg", size, mode, reg)
					table[0x4600 | (ss << 6) | (mode << 3) | reg] = self._make_unary("not", size, mode, reg)
					if mode != 1:  # TST: 0x4A00, no An-direct
						table[0x4A00 | (ss << 6) | (mode << 3) | reg] = self._make_tst(size, mode, reg)
		# MOVE from SR: word, destination must be alterable (mode=7 only
		# reg 0/1: abs.W/abs.L).
		for mode in range(8):
			for reg in range(8):
				if mode == 1:
					continue
				if mode == 7 and reg > 1:
					continue
				table[(0b0100000011 << 6) | (mode << 3) | reg] = self._make_move_from_sr(mode, reg)
		# MOVE to CCR / MOVE to SR: word, these READ <ea> as a source, so
		# unlike MOVE FROM SR above, immediate (mode=7,reg=4) and
		# PC-relative (reg=2,3) are valid -- only An-direct (mode=1) isn't.
		for mode in range(8):
			for reg in range(8):
				if mode == 1:
					continue
				if mode == 7 and reg > 4:
					continue
				table[(0b0100010011 << 6) | (mode << 3) | reg] = self._make_move_to_ccr(mode, reg)
				table[(0b0100011011 << 6) | (mode << 3) | reg] = self._make_move_to_sr(mode, reg)
		# NBCD, TAS, PEA, MOVEM, EXT, SWAP, LINK, UNLK, TRAP, TRAPV, RTE, RTS,
		# RTR, JSR, JMP, ILLEGAL, RESET, NOP, STOP, MOVE USP, CHK, LEA
		# NBCD/TAS: Musashi allowed-ea "A+-DXWL..." -- modes 2,3,4,5,6 plus
		# mode7 reg0-1 (abs.w/abs.l) only; mode 1 (An-direct) and
		# mode7 reg2-4 (pc-relative/immediate) are never valid.
		for mode in (0, 2, 3, 4, 5, 6, 7):
			for reg in range(8):
				if mode == 7 and reg > 1:
					continue
				table[(0b0100100000 << 6) | (mode << 3) | reg] = self._make_nbcd(mode, reg)
				table[(0b0100101011 << 6) | (mode << 3) | reg] = self._make_tas(mode, reg)
		# PEA/JSR/JMP/LEA all take a "control" addressing mode (Musashi:
		# allowed-ea "A..DXWLdx." -- excludes Dn/An-direct, postincrement,
		# predecrement, and immediate; only modes that yield a plain
		# address are valid).
		for mode in (2, 5, 6, 7):
			for reg in range(8):
				if mode == 7 and reg > 3:
					continue
				table[(0b0100100001 << 6) | (mode << 3) | reg] = self._make_pea(mode, reg)
		for dr in range(2):
			for szbit in range(2):
				for mode in range(8):
					for reg in range(8):
						if mode in (0, 1):
							continue
						# Verified against Musashi: predecrement (mode 4) is
						# for store direction (dr=0) only -- pushing
						# registers; postincrement (mode 3) is for load
						# direction (dr=1) only -- popping. This was
						# previously backwards.
						if dr == 0 and mode == 3:
							continue
						if dr == 1 and mode == 4:
							continue
						if mode == 7 and reg > (1 if dr == 0 else 3):
							continue
						opcode = (0b010010001 << 7) | (dr << 10) | (szbit << 6) | (mode << 3) | reg
						table[opcode] = self._make_movem(dr, SIZE_LONG if szbit else SIZE_WORD, mode, reg)
		# EXT.W=0x4880, EXT.L=0x48C0 (Musashi m68k_in.c: differ by bit6, not
		# bit3 -- mode field is always 000, only the reg and the size bit vary).
		for sizebit in (0, 1):
			for reg in range(8):
				table[0x4880 | (sizebit << 6) | reg] = self._make_ext(sizebit, reg)
		for reg in range(8):
			table[0x4840 | reg] = self._make_swap(reg)
		for reg in range(8):
			table[0x4E50 | reg] = self._make_link(reg)
			table[0x4E58 | reg] = self._make_unlk(reg)
			table[0x4E60 | reg] = self._make_move_usp(True, reg)
			table[0x4E68 | reg] = self._make_move_usp(False, reg)
		for vec in range(16):
			table[0x4E40 | vec] = self._make_trap(vec)
		table[0x4E71] = self._make_nop()
		table[0x4E70] = self._make_reset()
		table[0x4E72] = self._make_stop()
		table[0x4E73] = self._make_rte()
		table[0x4E75] = self._make_rts()
		table[0x4E76] = self._make_trapv()
		table[0x4E77] = self._make_rtr()
		table[0x4AFC] = self._make_illegal_insn()
		for mode in (2, 5, 6, 7):
			for reg in range(8):
				if mode == 7 and reg > 3:
					continue
				table[(0b0100111010 << 6) | (mode << 3) | reg] = self._make_jsr(mode, reg)
				table[(0b0100111011 << 6) | (mode << 3) | reg] = self._make_jmp(mode, reg)
		for dreg in range(8):
			# CHK's source is a full data-addressing EA (Musashi: "d" row
			# for Dn-direct plus a general "A+-DXWLdxI" row) -- includes
			# Dn-direct (mode 0) and immediate (mode 7, reg 4), unlike the
			# alterable-destination instructions elsewhere in this group.
			for mode in range(8):
				if mode == 1:
					continue
				for reg in range(8):
					if mode == 7 and reg > 4:
						continue
					table[(0b0100 << 12) | (dreg << 9) | (0b110 << 6) | (mode << 3) | reg] = self._make_chk(dreg, mode, reg)
		for dreg in range(8):
			for mode in (2, 5, 6, 7):
				for reg in range(8):
					if mode == 7 and reg > 3:
						continue
					table[(0b0100 << 12) | (dreg << 9) | (0b111 << 6) | (mode << 3) | reg] = self._make_lea(dreg, mode, reg)

	def _make_unary(self, name, size, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, size)
			if name == "clr":
				self.ea_write(ea, size, 0)
				self._set_nzvc(SR_Z, keep_x=True)
				return 8
			a = self.ea_read(ea, size)
			if name == "not":
				r = (~a) & _SIZE_MASK[size]
				self.ea_write(ea, size, r)
				self._set_nzvc(self._flags_logic(r, size), keep_x=True)
			elif name == "neg":
				r = (0 - a) & _SIZE_MASK[size]
				self.ea_write(ea, size, r)
				self._set_nzvc(self._flags_sub(0, a, r, size))
			elif name == "negx":
				x = 1 if (self.SR & SR_X) else 0
				r = (0 - a - x) & _SIZE_MASK[size]
				self.ea_write(ea, size, r)
				flags = self._flags_sub(0, a + x, r, size)
				if r != 0:
					flags &= ~SR_Z
				else:
					flags = (flags & ~SR_Z) | (SR_Z if (self.SR & SR_Z) else 0)
				self._set_nzvc(flags)
			return 6
		return op

	def _make_tst(self, size, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, size)
			val = self.ea_read(ea, size)
			self._set_nzvc(self._flags_logic(val, size), keep_x=True)
			return 4
		return op

	def _make_move_from_sr(self, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, SIZE_WORD)
			self.ea_write(ea, SIZE_WORD, self.SR)
			return 8
		return op

	def _make_move_to_ccr(self, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, SIZE_WORD)
			val = self.ea_read(ea, SIZE_WORD)
			self.SR = (self.SR & 0xFF00) | (val & 0xFF)
			return 12
		return op

	def _make_move_to_sr(self, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, SIZE_WORD)
			val = self.ea_read(ea, SIZE_WORD)
			if not self.supervisor:
				self.raise_exception(VEC_PRIVILEGE)
				return 34
			self._sr_write(val)
			return 12
		return op

	def _make_nbcd(self, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, SIZE_BYTE)
			a = self.ea_read(ea, SIZE_BYTE)
			x = 1 if (self.SR & SR_X) else 0
			lo = 0 - (a & 0xF) - x
			hi = 0 - (a >> 4)
			if lo < 0:
				lo += 10; hi -= 1
			if hi < 0:
				hi += 10
			r = ((hi & 0xF) << 4) | (lo & 0xF)
			r &= 0xFF
			self.ea_write(ea, SIZE_BYTE, r)
			flags = 0
			if r != 0:
				pass
			else:
				flags |= SR_Z if (self.SR & SR_Z) else 0
			if a != 0 or x:
				self._set_flags(SR_C | SR_X, SR_C | SR_X)
			return 6
		return op

	def _make_tas(self, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, SIZE_BYTE)
			a = self.ea_read(ea, SIZE_BYTE)
			self._set_nzvc(self._flags_logic(a, SIZE_BYTE), keep_x=True)
			self.ea_write(ea, SIZE_BYTE, a | 0x80)
			return 14
		return op

	def _make_pea(self, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, SIZE_LONG)
			addr = self.ea_address(ea)
			self._push32(addr)
			return 12
		return op

	def _make_movem(self, dr, size, mode, reg):
		def op(opcode):
			mask = self.fetch16()
			if dr == 0:  # register to memory
				if mode == 4:  # predecrement: register order reversed, mask bit0=A7..bit15=D0
					addr = self.get_a(reg)
					for i in range(16):
						if mask & (1 << i):
							regnum = 15 - i
							addr -= size
							val = self.get_a(regnum - 8) if regnum >= 8 else self.get_d(regnum, size)
							if size == SIZE_LONG:
								self.write32(addr & MASK32, val)
							else:
								self.write16(addr & MASK32, val & MASK16)
					self.set_a(reg, addr & MASK32)
				else:
					ea = self.resolve_ea(mode, reg, size)
					addr = self.ea_address(ea) if ea.kind == "m" else 0
					for i in range(16):
						if mask & (1 << i):
							val = self.get_d(i, size) if i < 8 else self.get_a(i - 8)
							if size == SIZE_LONG:
								self.write32(addr & MASK32, val)
							else:
								self.write16(addr & MASK32, val & MASK16)
							addr += size
			else:  # memory to register
				if mode == 3:  # postincrement
					addr = self.get_a(reg)
					for i in range(16):
						if mask & (1 << i):
							if size == SIZE_LONG:
								val = self.read32(addr & MASK32)
							else:
								val = s32(s16(self.read16(addr & MASK32)))
							if i < 8:
								self.D[i] = val & MASK32
							else:
								self.A[i - 8] = val & MASK32
							addr += size
					self.set_a(reg, addr & MASK32)
				else:
					ea = self.resolve_ea(mode, reg, size)
					addr = self.ea_address(ea)
					for i in range(16):
						if mask & (1 << i):
							if size == SIZE_LONG:
								val = self.read32(addr & MASK32)
							else:
								val = s32(s16(self.read16(addr & MASK32)))
							if i < 8:
								self.D[i] = val & MASK32
							else:
								self.A[i - 8] = val & MASK32
							addr += size
			return 12
		return op

	def _make_ext(self, opmode, reg):
		def op(opcode):
			if opmode == 0b000:  # word<-byte
				v = s8(self.get_d(reg, SIZE_BYTE)) & MASK16
				self.set_d(reg, SIZE_WORD, v)
				self._set_nzvc(self._flags_logic(v, SIZE_WORD), keep_x=True)
			else:  # long<-word
				v = s16(self.get_d(reg, SIZE_WORD)) & MASK32
				self.set_d(reg, SIZE_LONG, v)
				self._set_nzvc(self._flags_logic(v, SIZE_LONG), keep_x=True)
			return 4
		return op

	def _make_swap(self, reg):
		def op(opcode):
			v = self.D[reg] & MASK32
			r = ((v & 0xFFFF) << 16) | (v >> 16)
			self.D[reg] = r
			self._set_nzvc(self._flags_logic(r, SIZE_LONG), keep_x=True)
			return 4
		return op

	def _make_link(self, reg):
		def op(opcode):
			disp = s16(self.fetch16())
			self._push32(self.get_a(reg))
			self.set_a(reg, self.A[7])
			self.set_a(7, (self.get_a(7) + disp) & MASK32)
			return 16
		return op

	def _make_unlk(self, reg):
		def op(opcode):
			self.set_a(7, self.get_a(reg))
			val = self.read32(self.A[7])
			self.A[7] = (self.A[7] + 4) & MASK32
			self.set_a(reg, val)
			return 12
		return op

	def _make_move_usp(self, to_usp, reg):
		def op(opcode):
			if not self.supervisor:
				self.raise_exception(VEC_PRIVILEGE)
				return 34
			if to_usp:
				self.usp = self.get_a(reg)
			else:
				self.set_a(reg, self.usp)
			return 4
		return op

	def _make_trap(self, vec):
		def op(opcode):
			self.raise_exception(VEC_TRAP_BASE + vec)
			return 34
		return op

	def _make_nop(self):
		def op(opcode):
			return 4
		return op

	def _make_reset(self):
		def op(opcode):
			if not self.supervisor:
				self.raise_exception(VEC_PRIVILEGE)
				return 34
			if hasattr(self.bus, "on_cpu_reset_instruction"):
				self.bus.on_cpu_reset_instruction()
			return 132
		return op

	def _make_stop(self):
		def op(opcode):
			if not self.supervisor:
				self.raise_exception(VEC_PRIVILEGE)
				return 34
			sr = self.fetch16()
			self._sr_write(sr)
			self.stopped = True
			return 4
		return op

	def _make_rte(self):
		def op(opcode):
			if not self.supervisor:
				self.raise_exception(VEC_PRIVILEGE)
				return 34
			sr = self.read16(self.A[7]); self.A[7] = (self.A[7] + 2) & MASK32
			pc = self.read32(self.A[7]); self.A[7] = (self.A[7] + 4) & MASK32
			self._sr_write(sr)
			self.PC = pc & MASK32
			return 20
		return op

	def _make_rts(self):
		def op(opcode):
			pc = self.read32(self.A[7]); self.A[7] = (self.A[7] + 4) & MASK32
			self.PC = pc & MASK32
			return 16
		return op

	def _make_trapv(self):
		def op(opcode):
			if self.SR & SR_V:
				self.raise_exception(VEC_TRAPV)
				return 34
			return 4
		return op

	def _make_rtr(self):
		def op(opcode):
			ccr = self.read16(self.A[7]); self.A[7] = (self.A[7] + 2) & MASK32
			pc = self.read32(self.A[7]); self.A[7] = (self.A[7] + 4) & MASK32
			self.SR = (self.SR & 0xFF00) | (ccr & 0xFF)
			self.PC = pc & MASK32
			return 20
		return op

	def _make_illegal_insn(self):
		def op(opcode):
			self.PC = (self.PC - 2) & MASK32
			self.raise_exception(VEC_ILLEGAL)
			return 34
		return op

	def _make_jsr(self, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, SIZE_LONG)
			addr = self.ea_address(ea)
			self._push32(self.PC)
			self.PC = addr
			return 16
		return op

	def _make_jmp(self, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, SIZE_LONG)
			self.PC = self.ea_address(ea)
			return 8
		return op

	def _make_chk(self, dreg, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, SIZE_WORD)
			bound = s16(self.ea_read(ea, SIZE_WORD))
			val = s16(self.get_d(dreg, SIZE_WORD))
			if val < 0:
				self._set_flags(SR_N, SR_N)
				self.raise_exception(VEC_CHK)
			elif val > bound:
				self._set_flags(SR_N, 0)
				self.raise_exception(VEC_CHK)
			return 10
		return op

	def _make_lea(self, dreg, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, SIZE_LONG)
			self.set_a(dreg, self.ea_address(ea))
			return 4
		return op

	# -- Group 5: ADDQ/SUBQ/Scc/DBcc ---------------------------------------
	def _install_group5(self, table):
		for data in range(8):
			for is_sub in range(2):
				for ss, size in _STD_SIZE.items():
					for mode in range(8):
						for reg in range(8):
							if mode == 7 and reg > 1:
								continue
							if mode == 1 and size == SIZE_BYTE:
								continue  # ADDQ/SUBQ.B to An isn't valid (byte access to an address register)
							opcode = (0b0101 << 12) | (data << 9) | (is_sub << 8) | (ss << 6) | (mode << 3) | reg
							table[opcode] = self._make_addq(data if data else 8, bool(is_sub), size, mode, reg)
		for cc in range(16):
			for mode in range(8):
				for reg in range(8):
					if mode == 1:
						if True:
							table[(0b0101 << 12) | (cc << 8) | (0b11 << 6) | (1 << 3) | reg] = self._make_dbcc(cc, reg)
						continue
					if mode == 7 and reg > 1:
						continue
					table[(0b0101 << 12) | (cc << 8) | (0b11 << 6) | (mode << 3) | reg] = self._make_scc(cc, mode, reg)

	def _make_addq(self, data, is_sub, size, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, size)
			a = self.ea_read(ea, size)
			if is_sub:
				r = (a - data) & _SIZE_MASK[size]
			else:
				r = (a + data) & _SIZE_MASK[size]
			self.ea_write(ea, size, r)
			if ea.kind == "a":
				return 8  # ADDQ/SUBQ to An: no flags affected
			flags = self._flags_sub(a, data, r, size) if is_sub else self._flags_add(a, data, r, size)
			self._set_nzvc(flags)
			return 4
		return op

	def _make_scc(self, cc, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, SIZE_BYTE)
			self.ea_write(ea, SIZE_BYTE, 0xFF if self._cond_true(cc) else 0x00)
			return 4
		return op

	def _make_dbcc(self, cc, reg):
		def op(opcode):
			disp = s16(self.fetch16())
			base_pc = (self.PC - 2) & MASK32
			if not self._cond_true(cc):
				cnt = s16(self.get_d(reg, SIZE_WORD))
				cnt = (cnt - 1) & MASK16
				self.set_d(reg, SIZE_WORD, cnt)
				if s16(cnt) != -1:
					self.PC = (base_pc + disp) & MASK32
			return 10
		return op

	# -- Group 6: Bcc/BSR/BRA -------------------------------------------------
	def _install_group6(self, table):
		for cc in range(16):
			for disp8 in range(256):
				opcode = (0b0110 << 12) | (cc << 8) | disp8
				table[opcode] = self._make_bcc(cc, disp8)

	def _make_bcc(self, cc, disp8):
		def op(opcode):
			base_pc = self.PC
			if disp8 == 0:
				disp = s16(self.fetch16())
			elif disp8 == 0xFF:
				disp = s32(self.fetch32())
			else:
				disp = s8(disp8)
			target = (base_pc + disp) & MASK32
			if cc == 1:  # BSR
				self._push32(self.PC)
				self.PC = target
				return 18
			if cc == 0 or self._cond_true(cc):  # BRA or true Bcc
				self.PC = target
				return 10
			return 8
		return op

	# -- Group 8: OR / DIVU / DIVS / SBCD ----------------------------------
	def _install_group8(self, table):
		for dreg in range(8):
			for opmode in range(8):
				for mode in range(8):
					for reg in range(8):
						if mode == 7 and reg > 4:
							continue
						opcode = (0b1000 << 12) | (dreg << 9) | (opmode << 6) | (mode << 3) | reg
						if opmode == 0b011:
							if mode == 1:
								continue  # DIVU source can't be An-direct
							table[opcode] = self._make_divu(dreg, mode, reg)
						elif opmode == 0b111:
							if mode == 1:
								continue  # DIVS source can't be An-direct
							table[opcode] = self._make_divs(dreg, mode, reg)
						elif opmode == 0b100 and mode == 0:
							table[opcode] = self._make_sbcd(("d", reg), dreg)
						elif opmode == 0b100 and mode == 1:
							table[opcode] = self._make_sbcd(("predec", reg), dreg)
						elif opmode in (0b000, 0b001, 0b010):
							if mode == 1:
								continue
							table[opcode] = self._make_or_to_dn(dreg, _STD_SIZE[opmode], mode, reg)
						elif opmode in (0b100, 0b101, 0b110):
							size = _STD_SIZE[opmode - 4]
							if not _alterable(mode, reg) or mode in (0, 1):
								continue
							table[opcode] = self._make_or_to_ea(dreg, size, mode, reg)

	def _make_or_to_dn(self, dreg, size, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, size)
			src = self.ea_read(ea, size)
			r = (self.get_d(dreg, size) | src) & _SIZE_MASK[size]
			self.set_d(dreg, size, r)
			self._set_nzvc(self._flags_logic(r, size), keep_x=True)
			return 4
		return op

	def _make_or_to_ea(self, dreg, size, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, size)
			dst = self.ea_read(ea, size)
			r = (dst | self.get_d(dreg, size)) & _SIZE_MASK[size]
			self.ea_write(ea, size, r)
			self._set_nzvc(self._flags_logic(r, size), keep_x=True)
			return 8
		return op

	def _make_divu(self, dreg, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, SIZE_WORD)
			divisor = self.ea_read(ea, SIZE_WORD) & MASK16
			if divisor == 0:
				self.raise_exception(VEC_ZERO_DIVIDE)
				return 38
			dividend = self.get_d(dreg, SIZE_LONG)
			q, r = divmod(dividend, divisor)
			if q > 0xFFFF:
				self._set_flags(SR_V, SR_V)
				return 10
			self.D[dreg] = ((r & 0xFFFF) << 16) | (q & 0xFFFF)
			self._set_nzvc(self._flags_logic(q, SIZE_WORD), keep_x=True)
			return 140
		return op

	def _make_divs(self, dreg, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, SIZE_WORD)
			divisor = s16(self.ea_read(ea, SIZE_WORD))
			if divisor == 0:
				self.raise_exception(VEC_ZERO_DIVIDE)
				return 38
			dividend = s32(self.get_d(dreg, SIZE_LONG))
			q = int(dividend / divisor)
			r = dividend - q * divisor
			if q > 32767 or q < -32768:
				self._set_flags(SR_V, SR_V)
				return 10
			self.D[dreg] = ((r & 0xFFFF) << 16) | (q & 0xFFFF)
			self._set_nzvc(self._flags_logic(q & MASK16, SIZE_WORD), keep_x=True)
			return 158
		return op

	def _make_sbcd(self, src, dreg):
		def op(opcode):
			if src[0] == "d":
				a = self.get_d(src[1], SIZE_BYTE)
			else:
				addr = (self.get_a(src[1]) - 1) & MASK32
				self.set_a(src[1], addr)
				a = self.read8(addr)
			if src[0] == "d":
				b = self.get_d(dreg, SIZE_BYTE)
			else:
				addr = (self.get_a(dreg) - 1) & MASK32
				self.set_a(dreg, addr)
				b = self.read8(addr)
			x = 1 if (self.SR & SR_X) else 0
			lo = (b & 0xF) - (a & 0xF) - x
			hi = (b >> 4) - (a >> 4)
			c = 0
			if lo < 0:
				lo += 10; hi -= 1
			if hi < 0:
				hi += 10; c = 1
			r = ((hi & 0xF) << 4) | (lo & 0xF)
			r &= 0xFF
			if src[0] == "d":
				self.set_d(dreg, SIZE_BYTE, r)
			else:
				self.write8(self.get_a(dreg), r)
			if r != 0:
				self._set_flags(SR_Z, 0)
			if c:
				self._set_flags(SR_C | SR_X, SR_C | SR_X)
			else:
				self._set_flags(SR_C | SR_X, 0)
			return 6
		return op

	# -- Groups 9 (SUB/SUBX/SUBA) and D (ADD/ADDX/ADDA) --------------------
	def _install_group9_d(self, table):
		for base, is_add in ((0b1001, False), (0b1101, True)):
			for dreg in range(8):
				for opmode in range(8):
					for mode in range(8):
						for reg in range(8):
							if mode == 7 and reg > 4:
								continue
							opcode = (base << 12) | (dreg << 9) | (opmode << 6) | (mode << 3) | reg
							if opmode in (0b011, 0b111):
								size = SIZE_WORD if opmode == 0b011 else SIZE_LONG
								table[opcode] = self._make_a_op_addr(dreg, size, mode, reg, is_add)
							elif opmode in (0b000, 0b001, 0b010):
								if mode == 1 and opmode == 0:
									continue
								size = _STD_SIZE[opmode]
								if mode in (0, 1) and self._is_x_form(mode, reg, dreg, opmode):
									table[opcode] = self._make_x_op(dreg, size, mode, reg, is_add)
								else:
									table[opcode] = self._make_dn_op(dreg, size, mode, reg, is_add, to_ea=False)
							elif opmode in (0b100, 0b101, 0b110):
								size = _STD_SIZE[opmode - 4]
								if mode in (0, 1):
									table[opcode] = self._make_x_op(dreg, size, mode, reg, is_add)
								elif _alterable(mode, reg):
									table[opcode] = self._make_dn_op(dreg, size, mode, reg, is_add, to_ea=True)

	def _is_x_form(self, mode, reg, dreg, opmode):
		return False  # ADD/SUB (opmode 0-2, mode 0/1) are plain Dn ops, not ADDX/SUBX; ADDX/SUBX are opmode 4-6

	def _make_dn_op(self, dreg, size, mode, reg, is_add, to_ea):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, size)
			if to_ea:
				a = self.ea_read(ea, size)
				b = self.get_d(dreg, size)
				r = ((a + b) if is_add else (a - b)) & _SIZE_MASK[size]
				self.ea_write(ea, size, r)
				flags = self._flags_add(a, b, r, size) if is_add else self._flags_sub(a, b, r, size)
				self._set_nzvc(flags)
			else:
				b = self.ea_read(ea, size)
				a = self.get_d(dreg, size)
				r = ((a + b) if is_add else (a - b)) & _SIZE_MASK[size]
				self.set_d(dreg, size, r)
				flags = self._flags_add(a, b, r, size) if is_add else self._flags_sub(a, b, r, size)
				self._set_nzvc(flags)
			return 4
		return op

	def _make_x_op(self, dreg, size, mode, reg, is_add):
		predecrement = mode == 1

		def op(opcode):
			if predecrement:
				addr_s = (self.get_a(reg) - size) & MASK32
				self.set_a(reg, addr_s)
				addr_d = (self.get_a(dreg) - size) & MASK32
				self.set_a(dreg, addr_d)
				a = self.read8(addr_s) if size == 1 else (self.read16(addr_s) if size == 2 else self.read32(addr_s))
				b = self.read8(addr_d) if size == 1 else (self.read16(addr_d) if size == 2 else self.read32(addr_d))
			else:
				a = self.get_d(reg, size)
				b = self.get_d(dreg, size)
			x = 1 if (self.SR & SR_X) else 0
			r = ((b + a + x) if is_add else (b - a - x)) & _SIZE_MASK[size]
			if predecrement:
				addr_d = self.get_a(dreg)
				if size == 1:
					self.write8(addr_d, r)
				elif size == 2:
					self.write16(addr_d, r)
				else:
					self.write32(addr_d, r)
			else:
				self.set_d(dreg, size, r)
			flags = self._flags_add(b, a + x, r, size) if is_add else self._flags_sub(b, a + x, r, size)
			if r != 0:
				flags &= ~SR_Z
			else:
				flags = (flags & ~SR_Z) | (SR_Z if (self.SR & SR_Z) else 0)
			self._set_nzvc(flags)
			return 4
		return op

	def _make_a_op_addr(self, dreg, size, mode, reg, is_add):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, size)
			val = self.ea_read(ea, size)
			val = s32(s16(val)) if size == SIZE_WORD else s32(val)
			a = self.get_a(dreg)
			r = ((a + val) if is_add else (a - val)) & MASK32
			self.set_a(dreg, r)
			return 8
		return op

	# -- Group B: CMP/CMPA/CMPM/EOR ------------------------------------------
	def _install_groupb(self, table):
		for dreg in range(8):
			for opmode in range(8):
				for mode in range(8):
					for reg in range(8):
						if mode == 7 and reg > 4:
							continue
						opcode = (0b1011 << 12) | (dreg << 9) | (opmode << 6) | (mode << 3) | reg
						if opmode in (0b011, 0b111):
							table[opcode] = self._make_cmpa(dreg, SIZE_WORD if opmode == 3 else SIZE_LONG, mode, reg)
						elif opmode in (0b000, 0b001, 0b010):
							if opmode == 0b000 and mode == 1:
								continue  # CMP.B against An isn't valid (byte access to an address register)
							table[opcode] = self._make_cmp(dreg, _STD_SIZE[opmode], mode, reg)
						elif opmode in (0b100, 0b101, 0b110):
							size = _STD_SIZE[opmode - 4]
							if mode == 1:
								table[opcode] = self._make_cmpm(dreg, size, reg)
							elif _data_alterable(mode, reg):
								table[opcode] = self._make_eor(dreg, size, mode, reg)

	def _make_cmp(self, dreg, size, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, size)
			b = self.ea_read(ea, size)
			a = self.get_d(dreg, size)
			r = (a - b) & _SIZE_MASK[size]
			self._set_nzvc(self._flags_sub(a, b, r, size), keep_x=True)
			return 4
		return op

	def _make_cmpa(self, dreg, size, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, size)
			val = self.ea_read(ea, size)
			b = s32(s16(val)) if size == SIZE_WORD else s32(val)
			a = self.get_a(dreg)
			r = (a - b) & MASK32
			self._set_nzvc(self._flags_sub(a, b & MASK32, r, SIZE_LONG), keep_x=True)
			return 6
		return op

	def _make_cmpm(self, dreg, size, reg):
		def op(opcode):
			addr_s = self.get_a(reg)
			self.set_a(reg, addr_s + size)
			addr_d = self.get_a(dreg)
			self.set_a(dreg, addr_d + size)
			b = self.read8(addr_s) if size == 1 else (self.read16(addr_s) if size == 2 else self.read32(addr_s))
			a = self.read8(addr_d) if size == 1 else (self.read16(addr_d) if size == 2 else self.read32(addr_d))
			r = (a - b) & _SIZE_MASK[size]
			self._set_nzvc(self._flags_sub(a, b, r, size), keep_x=True)
			return 12
		return op

	def _make_eor(self, dreg, size, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, size)
			a = self.ea_read(ea, size)
			r = (a ^ self.get_d(dreg, size)) & _SIZE_MASK[size]
			self.ea_write(ea, size, r)
			self._set_nzvc(self._flags_logic(r, size), keep_x=True)
			return 8
		return op

	# -- Group C: AND / MULU / MULS / ABCD / EXG ----------------------------
	def _install_groupc(self, table):
		for dreg in range(8):
			for opmode in range(8):
				for mode in range(8):
					for reg in range(8):
						if mode == 7 and reg > 4:
							continue
						opcode = (0b1100 << 12) | (dreg << 9) | (opmode << 6) | (mode << 3) | reg
						if opmode == 0b011:
							if mode == 1:
								continue  # MULU source can't be An-direct
							table[opcode] = self._make_mulu(dreg, mode, reg)
						elif opmode == 0b111:
							if mode == 1:
								continue  # MULS source can't be An-direct
							table[opcode] = self._make_muls(dreg, mode, reg)
						elif opmode == 0b100 and mode == 0:
							# Verified against Musashi: abcd's fixed bits are
							# "1100...100000..." -> bits8-6=100, not 101.
							table[opcode] = self._make_abcd(("d", reg), dreg)
						elif opmode == 0b100 and mode == 1:
							table[opcode] = self._make_abcd(("predec", reg), dreg)
						elif opmode in (0b000, 0b001, 0b010):
							if mode == 1:
								continue
							table[opcode] = self._make_and_to_dn(dreg, _STD_SIZE[opmode], mode, reg)
						elif opmode in (0b100, 0b101, 0b110):
							if mode in (1,):
								continue
							size = _STD_SIZE[opmode - 4]
							if _alterable(mode, reg) and mode != 0:
								table[opcode] = self._make_and_to_ea(dreg, size, mode, reg)
		# EXG
		for rx in range(8):
			for ry in range(8):
				table[(0b1100 << 12) | (rx << 9) | (0b101000 << 3) | ry] = self._make_exg("dd", rx, ry)
				table[(0b1100 << 12) | (rx << 9) | (0b101001 << 3) | ry] = self._make_exg("aa", rx, ry)
				table[(0b1100 << 12) | (rx << 9) | (0b110001 << 3) | ry] = self._make_exg("da", rx, ry)

	def _make_and_to_dn(self, dreg, size, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, size)
			src = self.ea_read(ea, size)
			r = (self.get_d(dreg, size) & src) & _SIZE_MASK[size]
			self.set_d(dreg, size, r)
			self._set_nzvc(self._flags_logic(r, size), keep_x=True)
			return 4
		return op

	def _make_and_to_ea(self, dreg, size, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, size)
			dst = self.ea_read(ea, size)
			r = (dst & self.get_d(dreg, size)) & _SIZE_MASK[size]
			self.ea_write(ea, size, r)
			self._set_nzvc(self._flags_logic(r, size), keep_x=True)
			return 8
		return op

	def _make_mulu(self, dreg, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, SIZE_WORD)
			a = self.ea_read(ea, SIZE_WORD) & MASK16
			b = self.get_d(dreg, SIZE_WORD) & MASK16
			r = (a * b) & MASK32
			self.D[dreg] = r
			self._set_nzvc(self._flags_logic(r, SIZE_LONG), keep_x=True)
			return 70
		return op

	def _make_muls(self, dreg, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, SIZE_WORD)
			a = s16(self.ea_read(ea, SIZE_WORD))
			b = s16(self.get_d(dreg, SIZE_WORD))
			r = (a * b) & MASK32
			self.D[dreg] = r
			self._set_nzvc(self._flags_logic(r, SIZE_LONG), keep_x=True)
			return 70
		return op

	def _make_abcd(self, src, dreg):
		def op(opcode):
			if src[0] == "d":
				a = self.get_d(src[1], SIZE_BYTE)
				b = self.get_d(dreg, SIZE_BYTE)
			else:
				addr_s = (self.get_a(src[1]) - 1) & MASK32
				self.set_a(src[1], addr_s)
				addr_d = (self.get_a(dreg) - 1) & MASK32
				self.set_a(dreg, addr_d)
				a = self.read8(addr_s)
				b = self.read8(addr_d)
			x = 1 if (self.SR & SR_X) else 0
			lo = (a & 0xF) + (b & 0xF) + x
			hi = (a >> 4) + (b >> 4)
			c = 0
			if lo > 9:
				lo -= 10; hi += 1
			if hi > 9:
				hi -= 10; c = 1
			r = ((hi & 0xF) << 4) | (lo & 0xF)
			r &= 0xFF
			if src[0] == "d":
				self.set_d(dreg, SIZE_BYTE, r)
			else:
				self.write8(self.get_a(dreg), r)
			if r != 0:
				self._set_flags(SR_Z, 0)
			self._set_flags(SR_C | SR_X, (SR_C | SR_X) if c else 0)
			return 6
		return op

	def _make_exg(self, kind, rx, ry):
		def op(opcode):
			if kind == "dd":
				self.D[rx], self.D[ry] = self.D[ry], self.D[rx]
			elif kind == "aa":
				self.A[rx], self.A[ry] = self.A[ry], self.A[rx]
			else:
				dv = self.D[rx]; av = self.A[ry]
				self.D[rx] = av; self.A[ry] = dv
			return 6
		return op

	# -- Group E: shifts/rotates --------------------------------------------
	def _install_groupe(self, table):
		names = {0b00: "as", 0b01: "ls", 0b10: "rox", 0b11: "ro"}
		for cnt_reg in range(8):
			for direction in range(2):  # 0=right,1=left
				for ss in range(3):
					size = _STD_SIZE[ss]
					for ir in range(2):  # 0=immediate count/data reg specified by cnt_reg field, 1=count in Dn
						for kind in range(4):
							opcode = (0b1110 << 12) | (cnt_reg << 9) | (direction << 8) | (ss << 6) | (ir << 5) | (kind << 3)
							for dreg in range(8):
								table[opcode | dreg] = self._make_shift_reg(names[kind], direction, size, ir, cnt_reg, dreg)
		for kind in range(4):
			for direction in range(2):
				for mode in range(2, 8):
					for reg in range(8):
						if mode == 7 and reg > 1:
							continue
						opcode = (0b1110 << 12) | (kind << 9) | (direction << 8) | (0b11 << 6) | (mode << 3) | reg
						table[opcode] = self._make_shift_mem(names[kind], direction, mode, reg)

	def _make_shift_reg(self, name, direction, size, ir, cnt_reg, dreg):
		def op(opcode):
			count = (self.get_d(cnt_reg, SIZE_LONG) % 64) if ir else (cnt_reg if cnt_reg else 8)
			val = self.get_d(dreg, size)
			r, flags = self._shift(name, direction, size, val, count)
			self.set_d(dreg, size, r)
			self._set_nzvc(flags, keep_x=(count == 0 and name in ("as", "ls")))
			return 6 + 2 * count
		return op

	def _make_shift_mem(self, name, direction, mode, reg):
		def op(opcode):
			ea = self.resolve_ea(mode, reg, SIZE_WORD)
			val = self.ea_read(ea, SIZE_WORD)
			r, flags = self._shift(name, direction, SIZE_WORD, val, 1)
			self.ea_write(ea, SIZE_WORD, r)
			self._set_nzvc(flags)
			return 8
		return op

	def _shift(self, name, direction, size, val, count):
		mask = _SIZE_MASK[size]
		msb = _SIZE_MSB[size]
		bits = _SIZE_BITS[size]
		v = val & mask
		x = 1 if (self.SR & SR_X) else 0
		c = 0
		overflow = False
		if count == 0:
			c = x
			r = v
		else:
			if name == "as":
				if direction:  # left
					orig_sign = v & msb
					for _ in range(count):
						c = 1 if (v & msb) else 0
						v = (v << 1) & mask
						if (v & msb) != orig_sign:
							overflow = True
					r = v
				else:
					for _ in range(count):
						c = v & 1
						sign = v & msb
						v = (v >> 1) | sign
					r = v & mask
				x = c
			elif name == "ls":
				if direction:
					for _ in range(count):
						c = 1 if (v & msb) else 0
						v = (v << 1) & mask
					r = v
				else:
					for _ in range(count):
						c = v & 1
						v = v >> 1
					r = v & mask
				x = c
			elif name == "ro":
				if direction:
					for _ in range(count):
						c = 1 if (v & msb) else 0
						v = ((v << 1) | c) & mask
					r = v
				else:
					for _ in range(count):
						c = v & 1
						v = (v >> 1) | (c << (bits - 1))
					r = v & mask
			else:  # rox
				if direction:
					for _ in range(count):
						newx = 1 if (v & msb) else 0
						v = ((v << 1) | x) & mask
						x = newx
					c = x
					r = v
				else:
					for _ in range(count):
						newx = v & 1
						v = (v >> 1) | (x << (bits - 1))
						x = newx
					c = x
					r = v & mask
		flags = 0
		if r == 0:
			flags |= SR_Z
		if r & msb:
			flags |= SR_N
		if c:
			flags |= SR_C
		if name == "as" and overflow:
			flags |= SR_V
		if name in ("as", "ls", "rox") and x:
			flags |= SR_X
		return r, flags


class M68000(M68000OpsMixin, M68000Core):
	"""Combined, usable M68000: registers/memory/exceptions from
	M68000Core plus the instruction set from M68000OpsMixin. Mixin listed
	first so its concrete _install_instructions overrides Core's abstract
	stub per Python's MRO."""
