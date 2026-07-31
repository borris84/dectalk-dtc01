"""Hand-written Motorola 68000 core.

Why this exists instead of a third-party 68000 emulator: Unicorn's M68K
backend has a documented gap (unicorn-engine/unicorn#1502) that can't
auto-vector internally-generated CPU exceptions (privilege violation,
address error) the DTC-01's mandatory boot self-test relies on, and
machine68k (a direct Musashi binding) has a broken Windows build script.
See DESIGN.md for the full trail. This core targets plain 68000 semantics
(no 68010+ features) against the public Motorola 68000 Programmer's
Reference Manual instruction set -- non-copyrightable architecture facts,
not derived from any particular implementation's source code.

Design mirrors tms32010.py: registers as plain Python ints kept masked to
their natural width, a `bus` object supplying read8/16/32 and write8/16/32
(so the owning machine can wire ROM/RAM/MMIO however it likes, same as the
TMS32010's io_read/io_write), and step()/run() driving execution one
instruction at a time so the caller can interleave with the DSP and check
interrupts between instructions.
"""

from __future__ import annotations

from typing import Callable, Protocol

MASK8 = 0xFF
MASK16 = 0xFFFF
MASK32 = 0xFFFFFFFF

SR_T = 0x8000
SR_S = 0x2000
SR_IPL_MASK = 0x0700
SR_X = 0x0010
SR_N = 0x0008
SR_Z = 0x0004
SR_V = 0x0002
SR_C = 0x0001


def s8(v: int) -> int:
	v &= MASK8
	return v - 0x100 if v & 0x80 else v


def s16(v: int) -> int:
	v &= MASK16
	return v - 0x10000 if v & 0x8000 else v


def s32(v: int) -> int:
	v &= MASK32
	return v - 0x100000000 if v & 0x80000000 else v


class Bus(Protocol):
	def read8(self, addr: int) -> int: ...
	def read16(self, addr: int) -> int: ...
	def read32(self, addr: int) -> int: ...
	def write8(self, addr: int, value: int) -> None: ...
	def write16(self, addr: int, value: int) -> None: ...
	def write32(self, addr: int, value: int) -> None: ...


class BusError(Exception):
	"""Raised by a Bus implementation to signal a real 68000 bus/address
	error -- the core catches this and generates the proper exception frame
	rather than crashing (DESIGN.md notes this is exactly the mechanism the
	Unicorn approach couldn't provide)."""

	def __init__(self, address: int, write: bool, instruction: bool = False):
		self.address = address
		self.write = write
		self.instruction = instruction
		super().__init__(f"bus error @ {address:#x} ({'write' if write else 'read'})")


VEC_RESET_SSP = 0
VEC_RESET_PC = 1
VEC_BUS_ERROR = 2
VEC_ADDRESS_ERROR = 3
VEC_ILLEGAL = 4
VEC_ZERO_DIVIDE = 5
VEC_CHK = 6
VEC_TRAPV = 7
VEC_PRIVILEGE = 8
VEC_TRACE = 9
VEC_LINE_A = 10
VEC_LINE_F = 11
VEC_TRAP_BASE = 32  # TRAP #0-15 -> vectors 32-47
VEC_AUTOVECTOR_BASE = 24  # spurious=24, level1..7 = 25..31


class M68000Core:
	"""Registers, memory access, exceptions, and addressing-mode resolution.
	Instruction decode/execute is supplied by the M68000OpsMixin in
	m68000_ops.py; the combined, usable class is m68000_ops.M68000."""

	def __init__(self, bus: Bus):
		self.bus = bus
		self.D = [0] * 8
		self.A = [0] * 8
		self.PC = 0
		self.SR = 0
		self.ssp = 0
		self.usp = 0
		self.stopped = False
		self.pending_irq_level = 0
		self._opcode_table = self._build_dispatch()

	# -- flags -----------------------------------------------------------
	@property
	def supervisor(self) -> bool:
		return bool(self.SR & SR_S)

	def _set_flags(self, mask: int, value: int) -> None:
		self.SR = (self.SR & ~mask) | (value & mask)

	def nz(self, value: int, size: int) -> int:
		bits = {1: 0x80, 2: 0x8000, 4: 0x80000000}[size]
		flags = 0
		if value & (bits * 2 - 1) == 0:
			flags |= SR_Z
		if value & bits:
			flags |= SR_N
		return flags

	# -- register-size helpers --------------------------------------------
	def get_d(self, n: int, size: int) -> int:
		v = self.D[n]
		if size == 1:
			return v & MASK8
		if size == 2:
			return v & MASK16
		return v & MASK32

	def set_d(self, n: int, size: int, value: int) -> None:
		if size == 1:
			self.D[n] = (self.D[n] & 0xFFFFFF00) | (value & MASK8)
		elif size == 2:
			self.D[n] = (self.D[n] & 0xFFFF0000) | (value & MASK16)
		else:
			self.D[n] = value & MASK32

	def get_a(self, n: int) -> int:
		return self.A[n] & MASK32

	def set_a(self, n: int, value: int) -> None:
		self.A[n] = value & MASK32

	# -- stack pointer bank switching -------------------------------------
	def _enter_supervisor(self) -> None:
		if not self.supervisor:
			self.usp = self.A[7]
			self.A[7] = self.ssp
			self.SR |= SR_S

	def _sr_write(self, value: int) -> None:
		was_super = self.supervisor
		self.SR = value & 0xFFFF
		now_super = self.supervisor
		if was_super and not now_super:
			self.ssp = self.A[7]
			self.A[7] = self.usp
		elif (not was_super) and now_super:
			self.usp = self.A[7]
			self.A[7] = self.ssp

	# -- bus helpers (raise BusError for odd-address word/long access,
	#    matching real 68000 address-error behavior) ----------------------
	def _check_align(self, addr: int, write: bool) -> None:
		if addr & 1:
			raise BusError(addr, write)

	def read8(self, addr: int) -> int:
		return self.bus.read8(addr & MASK32)

	def read16(self, addr: int) -> int:
		self._check_align(addr, False)
		return self.bus.read16(addr & MASK32)

	def read32(self, addr: int) -> int:
		self._check_align(addr, False)
		return self.bus.read32(addr & MASK32)

	def write8(self, addr: int, value: int) -> None:
		self.bus.write8(addr & MASK32, value & MASK8)

	def write16(self, addr: int, value: int) -> None:
		self._check_align(addr, True)
		self.bus.write16(addr & MASK32, value & MASK16)

	def write32(self, addr: int, value: int) -> None:
		self._check_align(addr, True)
		self.bus.write32(addr & MASK32, value & MASK32)

	def fetch16(self) -> int:
		pc = self.PC
		if pc & 1:
			raise BusError(pc, False)
		v = self.bus.read16(pc & MASK32)
		self.PC = (pc + 2) & MASK32
		return v

	def fetch32(self) -> int:
		hi = self.fetch16()
		lo = self.fetch16()
		return ((hi << 16) | lo) & MASK32

	# -- reset --------------------------------------------------------------
	def reset(self) -> None:
		self.ssp = self.read32(0)
		pc = self.read32(4)
		self.A[7] = self.ssp
		self.PC = pc
		self.SR = SR_S | SR_IPL_MASK  # supervisor, IPL=7, trace off
		self.stopped = False

	# -- exceptions --------------------------------------------------------
	def _push32(self, value: int) -> None:
		self.A[7] = (self.A[7] - 4) & MASK32
		self.write32(self.A[7], value)

	def _push16(self, value: int) -> None:
		self.A[7] = (self.A[7] - 2) & MASK32
		self.write16(self.A[7], value)

	def _vector(self, vector_num: int, set_ipl: int | None = None) -> None:
		old_sr = self.SR
		old_pc = self.PC
		self._enter_supervisor()
		self._push32(old_pc)
		self._push16(old_sr)
		self.SR &= ~SR_T
		if set_ipl is not None:
			self.SR = (self.SR & ~SR_IPL_MASK) | ((set_ipl & 7) << 8)
		self.PC = self.read32(vector_num * 4)

	def raise_exception(self, vector_num: int) -> None:
		self._vector(vector_num)

	def _raise_bus_error(self, err: BusError, opcode: int) -> None:
		# Group 0 (bus/address error) uses the extended 7-word stack frame
		# on plain 68000: status word, access address, then the normal
		# SR/PC pair below that.
		old_sr = self.SR
		old_pc = self.PC
		self._enter_supervisor()
		status = 0x0000
		if not err.write:
			status |= 0x0010
		if err.instruction:
			status |= 0x0002
		else:
			status |= 0x0001
		self._push16(opcode & MASK16)
		self._push32(err.address & MASK32)
		self._push16(status)
		self._push32(old_pc)
		self._push16(old_sr)
		self.SR &= ~SR_T
		vec = VEC_ADDRESS_ERROR if (err.address & 1) else VEC_BUS_ERROR
		self.PC = self.read32(vec * 4)

	def service_interrupt(self, level: int) -> None:
		# Autovectored: DTC-01 has no VPA/vectored-interrupt peripherals in
		# our model, only autovector (see DESIGN.md section 1 IRQ map).
		self._vector(VEC_AUTOVECTOR_BASE + level, set_ipl=level)

	# =====================================================================
	# Effective address resolution
	# =====================================================================
	# mode/reg fields per the standard 68000 EA encoding; ea_read/ea_write
	# operate through a small descriptor so callers don't care whether the
	# operand lives in a register or memory.

	class _EA:
		__slots__ = ("kind", "reg", "addr")
		# kind: 'd' data reg, 'a' addr reg, 'm' memory, 'i' immediate(read-only)

		def __init__(self, kind, reg=None, addr=None):
			self.kind = kind
			self.reg = reg
			self.addr = addr

	def resolve_ea(self, mode: int, reg: int, size: int) -> "_EA":
		if mode == 0:
			return self._EA("d", reg=reg)
		if mode == 1:
			return self._EA("a", reg=reg)
		if mode == 2:
			return self._EA("m", addr=self.get_a(reg))
		if mode == 3:
			addr = self.get_a(reg)
			ea = self._EA("m", addr=addr)
			step = 2 if (size == 1 and reg == 7) else size
			self.set_a(reg, addr + step)
			return ea
		if mode == 4:
			step = 2 if (size == 1 and reg == 7) else size
			addr = (self.get_a(reg) - step) & MASK32
			self.set_a(reg, addr)
			return self._EA("m", addr=addr)
		if mode == 5:
			disp = s16(self.fetch16())
			return self._EA("m", addr=(self.get_a(reg) + disp) & MASK32)
		if mode == 6:
			return self._EA("m", addr=self._decode_brief_ext(self.get_a(reg)))
		if mode == 7:
			if reg == 0:
				return self._EA("m", addr=s16(self.fetch16()) & MASK32)
			if reg == 1:
				return self._EA("m", addr=self.fetch32())
			if reg == 2:
				base_pc = self.PC
				disp = s16(self.fetch16())
				return self._EA("m", addr=(base_pc + disp) & MASK32)
			if reg == 3:
				base_pc = self.PC
				return self._EA("m", addr=self._decode_brief_ext(base_pc))
			if reg == 4:
				ea = self._EA("i", addr=self.PC)
				self.PC = (self.PC + (2 if size != 4 else 4)) & MASK32
				return ea
		raise ValueError(f"bad EA mode={mode} reg={reg}")

	def _decode_brief_ext(self, base: int) -> int:
		ext = self.fetch16()
		xreg = (ext >> 12) & 7
		xlong = bool(ext & 0x0800)
		xval = self.get_d(xreg, 4) if not (ext & 0x8000) else self.get_a(xreg)
		xval = xval if xlong else s16(xval & MASK16)
		disp = s8(ext & 0xFF)
		return (base + xval + disp) & MASK32

	def ea_read(self, ea: "_EA", size: int) -> int:
		if ea.kind == "d":
			return self.get_d(ea.reg, size)
		if ea.kind == "a":
			return self.get_a(ea.reg) if size == 4 else (s16(self.get_a(ea.reg)) & (MASK16 if size == 2 else MASK8))
		if ea.kind == "i":
			if size == 1:
				return self.read16(ea.addr) & MASK8
			if size == 2:
				return self.read16(ea.addr)
			return self.read32(ea.addr)
		if size == 1:
			return self.read8(ea.addr)
		if size == 2:
			return self.read16(ea.addr)
		return self.read32(ea.addr)

	def ea_write(self, ea: "_EA", size: int, value: int) -> None:
		if ea.kind == "d":
			self.set_d(ea.reg, size, value)
			return
		if ea.kind == "a":
			self.set_a(ea.reg, s32(s16(value)) if size != 4 else value)
			return
		if size == 1:
			self.write8(ea.addr, value)
		elif size == 2:
			self.write16(ea.addr, value)
		else:
			self.write32(ea.addr, value)

	def ea_address(self, ea: "_EA") -> int:
		if ea.kind != "m":
			raise ValueError("EA has no address (register or immediate operand)")
		return ea.addr

	# =====================================================================
	# Execution
	# =====================================================================
	def step(self) -> int:
		"""Execute one instruction. Returns an approximate cycle count (used
		only for scheduling proportion against the DSP -- see machine.py's
		timing-model caveat, same v1 approximation approach as before)."""
		if self.stopped:
			return 4
		pc_at_fetch = self.PC
		opcode = None
		try:
			opcode = self.fetch16()
			handler = self._opcode_table[opcode]
			if handler is None:
				self.PC = pc_at_fetch
				self._illegal(opcode)
				return 34
			return handler(opcode)
		except BusError as e:
			self.PC = pc_at_fetch
			self._raise_bus_error(e, opcode if opcode is not None else 0)
			return 50

	def run(self, cycles: int) -> int:
		budget = cycles
		while budget > 0:
			budget -= self.step()
		return budget

	def _illegal(self, opcode: int) -> None:
		self.raise_exception(VEC_ILLEGAL)

	# =====================================================================
	# Dispatch table construction -- filled in by _instructions.py mixin
	# =====================================================================
	def _build_dispatch(self):
		table = [None] * 0x10000
		self._install_instructions(table)
		return table

	def _install_instructions(self, table):
		raise NotImplementedError  # provided by m68000_ops.py via monkey-patch/mixin
