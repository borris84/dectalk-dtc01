"""Minimal SCN2681 DUART emulation -- just enough for the DTC-01 v2.0
firmware to configure channel B as the host RS-232 link and move bytes
through it. Register map is the standard, publicly documented SCN2681
layout (Signetics/Philips datasheet); this is not a full-fidelity
emulation of every mode/timer feature, only what's needed to carry text in
and audio-triggering commands out over channel B, plus enough of channel A
and the timer/interrupt registers that the firmware's setup code doesn't
get confused.

Register offsets (word-stepped -- the DTC-01 wires the DUART's A0-A3
select lines to the 68000's A1-A4, so each register sits 2 bytes apart,
low byte only; see DESIGN.md section 3, "a0 not connected"):

  0x00 MR1A/MR2A (R/W, dual-buffered mode register A)
  0x02 SRA (R, status A) / CSRA (W, clock select A)
  0x04 (R, reserved)     / CRA (W, command A)
  0x06 RHRA (R, rx A)    / THRA (W, tx A)
  0x08 IPCR (R, input port change) / ACR (W, aux control)
  0x0A ISR (R, interrupt status)   / IMR (W, interrupt mask)
  0x0C CTU (R) / CTUR (W)
  0x0E CTL (R) / CTLR (W)
  0x10 MR1B/MR2B (R/W)
  0x12 SRB (R) / CSRB (W)
  0x14 (R, reserved) / CRB (W, command B)
  0x16 RHRB (R, rx B) / THRB (W, tx B)  <- this is the host text channel
  0x18 (R, reserved) / (W, reserved)
  0x1A IP (R, input port) / OPCR (W)
  0x1C start-counter (R) / OPR set (W)
  0x1E stop-counter (R) / OPR reset (W)

Status register bits (SRA/SRB): RXRDY=0x01, TXRDY=0x04, TXEMT=0x08.
"""

from __future__ import annotations

from collections import deque
from typing import Callable

SR_RXRDY = 0x01
SR_TXRDY = 0x04
SR_TXEMT = 0x08

ISR_TXRDYA = 0x01
ISR_RXRDYA = 0x02
ISR_COUNTER_READY = 0x08
ISR_TXRDYB = 0x10
ISR_RXRDYB = 0x20
ISR_INPUT_CHANGE = 0x80

CRYSTAL_HZ = 3_686_400  # DESIGN.md section 1


class Channel:
	__slots__ = ("mr1", "mr2", "mr_ptr", "csr", "cr", "rx_queue", "tx_enabled", "rx_enabled")

	def __init__(self):
		self.mr1 = 0
		self.mr2 = 0
		self.mr_ptr = 0  # which of MR1/MR2 the next MR access hits
		self.csr = 0
		self.cr = 0
		self.rx_queue: deque[int] = deque()
		self.tx_enabled = False
		self.rx_enabled = False

	def write_mr(self, value: int) -> None:
		if self.mr_ptr == 0:
			self.mr1 = value
			self.mr_ptr = 1
		else:
			self.mr2 = value

	def write_cr(self, value: int) -> None:
		self.cr = value
		# bits 0-1: rx enable/disable, bits 2-3: tx enable/disable
		if value & 0x01:
			self.rx_enabled = True
		if value & 0x02:
			self.rx_enabled = False
		if value & 0x04:
			self.tx_enabled = True
		if value & 0x08:
			self.tx_enabled = False
		if (value >> 4) & 0x07 == 0x03:  # reset MR pointer command
			self.mr_ptr = 0

	def status(self) -> int:
		sr = 0
		if self.rx_enabled and self.rx_queue:
			sr |= SR_RXRDY
		if self.tx_enabled:
			sr |= SR_TXRDY | SR_TXEMT
		return sr


class SCN2681:
	"""on_tx_b(byte) is called for every byte the firmware transmits on
	channel B (the host link) -- this is where index-mark bytes / status
	responses would appear, if this firmware version emits any (see
	DESIGN.md section 6 -- it doesn't have [:index], but it may still emit
	other status bytes we need to observe).

	irq_cb(bool) is called whenever the combined interrupt output changes,
	to be wired to the 68000's IRQ6 line by the owning machine.
	"""

	__slots__ = (
		"a", "b", "imr", "acr", "opr", "ctur", "ctlr",
		"_counter_running", "_counter_remaining", "_counter_tick_debt",
		"_counter_ready", "_counter_clock_cache",
		"input_port_bits", "_input_port_read_count",
		"_on_tx_b", "_irq_cb", "_irq_state",
	)

	def __init__(self, on_tx_b: Callable[[int], None], irq_cb: Callable[[bool], None]):
		self.a = Channel()
		self.b = Channel()
		self.imr = 0
		self.acr = 0
		self.opr = 0  # output port bits (OP0=RTS, OP2=DTR per DESIGN.md)
		self.ctur = 0
		self.ctlr = 0
		self._counter_running = False
		self._counter_remaining = 0  # clock ticks left until next ready/reload boundary
		self._counter_tick_debt = 0.0  # fractional clock cycles carried between step() calls
		self._counter_ready = False  # latched ISR bit3
		self._counter_clock_cache = 0.0  # cached _compute_counter_clock_hz(), refreshed on ACR write
		# IP0=CTS,IP2=DSR,IP3=RLS default low (see set_input_bit()); IP4
		# default low = "skip self test" ACTIVE (matches the real dipswitch
		# default documented in the driver source); IP5/IP6 are undocumented
		# jumpers the driver author left at their "Open/VCC" (high) default;
		# IP7 doesn't exist as a real pin and reads high.
		self.input_port_bits = 0b11100000
		self._input_port_read_count = 0
		self._on_tx_b = on_tx_b
		self._irq_cb = irq_cb
		self._irq_state = False

	# -- host-side control: simulate the RS-232 handshake lines ----------
	def set_input_bit(self, bit: int, level: bool) -> None:
		"""bit: 0=CTS, 2=DSR, 3=RLS (see DESIGN.md section 1's IP pin
		wiring). The DTC-01 firmware runs a modem-style connect state
		machine on its host port and won't move data until it sees these
		asserted -- our virtual link has no real modem, so the machine
		glue asserts CTS/DSR permanently once the emulator is ready for
		text, emulating an always-connected direct line."""
		if level:
			self.input_port_bits |= 1 << bit
		else:
			self.input_port_bits &= ~(1 << bit)

	def feed_rx_b(self, data: bytes) -> None:
		self.b.rx_queue.extend(data)
		self._update_irq()

	# -- register access ----------------------------------------------------
	def read(self, offset: int) -> int:
		reg = offset & 0x1E
		if reg == 0x00:
			return self.a.mr1 if self.a.mr_ptr == 0 else self.a.mr2
		if reg == 0x02:
			return self.a.status()
		if reg == 0x06:
			if self.a.rx_queue:
				return self.a.rx_queue.popleft()
			return 0
		if reg == 0x08:
			return 0  # IPCR: no pending input changes modeled
		if reg == 0x0A:
			return self._isr()
		if reg == 0x10:
			return self.b.mr1 if self.b.mr_ptr == 0 else self.b.mr2
		if reg == 0x12:
			return self.b.status()
		if reg == 0x16:
			if self.b.rx_queue:
				val = self.b.rx_queue.popleft()
				self._update_irq()
				return val
			return 0
		if reg == 0x1A:
			# Driver source's own documented workaround: "hack to prevent
			# hang when skip self test is shorted" -- on real hardware and
			# in MAME, IP4 must read differently on the second+ read of
			# this port than on the first, or the self-test dispatcher
			# hangs (see DESIGN.md). Bit4 = IP4 = "skip self test" (low =
			# active); force it high after the first read.
			self._input_port_read_count += 1
			value = self.input_port_bits & 0xFF
			if self._input_port_read_count > 1:
				value |= 0x10
			return value
		if reg == 0x1C:
			self.start_counter()
			return (self._counter_remaining >> 8) & 0xFF
		if reg == 0x1E:
			self.stop_counter()
			return self._counter_remaining & 0xFF
		return 0

	def write(self, offset: int, value: int) -> None:
		value &= 0xFF
		reg = offset & 0x1E
		if reg == 0x00:
			self.a.write_mr(value)
		elif reg == 0x02:
			self.a.csr = value
		elif reg == 0x04:
			self.a.write_cr(value)
		elif reg == 0x06:
			pass  # THRA: channel A not used as host link, discard
		elif reg == 0x08:
			self.acr = value
			self._counter_clock_cache = self._compute_counter_clock_hz()
		elif reg == 0x0A:
			self.imr = value
			self._update_irq()
		elif reg == 0x0C:
			self.ctur = value
		elif reg == 0x0E:
			self.ctlr = value
		elif reg == 0x10:
			self.b.write_mr(value)
		elif reg == 0x12:
			self.b.csr = value
		elif reg == 0x14:
			self.b.write_cr(value)
		elif reg == 0x16:
			self._on_tx_b(value)
		elif reg == 0x1A:
			pass  # OPCR: output port config, not modeled
		elif reg == 0x1C:
			self.opr |= value  # "set output port bits"
		elif reg == 0x1E:
			self.opr &= ~value  # "reset output port bits"

	def _isr(self) -> int:
		isr = 0
		if self.a.tx_enabled:
			isr |= ISR_TXRDYA
		if self.a.rx_enabled and self.a.rx_queue:
			isr |= ISR_RXRDYA
		if self._counter_ready:
			isr |= ISR_COUNTER_READY
		if self.b.tx_enabled:
			isr |= ISR_TXRDYB
		if self.b.rx_enabled and self.b.rx_queue:
			isr |= ISR_RXRDYB
		return isr

	def _update_irq(self) -> None:
		active = bool(self._isr() & self.imr)
		if active != self._irq_state:
			self._irq_state = active
			self._irq_cb(active)

	# -- counter/timer -------------------------------------------------------
	# ACR[6:4] select mode/clock per the SCN2681 datasheet. Only the
	# crystal-derived clock sources are modeled (this firmware programs
	# ACR=0xFF -> Timer mode, X1/CLK/16, per DESIGN.md's boot-sequence
	# trace); IP2-pin and TxC-derived sources aren't wired to anything in
	# our emulated environment, so those modes simply never tick.
	def _counter_n(self) -> int:
		n = (self.ctur << 8) | self.ctlr
		return n if n else 0x10000  # 0 means max count (65536) per datasheet

	def _counter_is_timer_mode(self) -> bool:
		return bool((self.acr >> 6) & 1)

	def _counter_period_ticks(self) -> int:
		# Counter mode: ISR fires once after N clock cycles (one-shot).
		# Timer mode: the chip free-runs a square wave, toggling every N
		# cycles -- ISR "ready" fires once per full period, i.e. every 2N.
		n = self._counter_n()
		return 2 * n if self._counter_is_timer_mode() else n

	def _compute_counter_clock_hz(self) -> float:
		sel = (self.acr >> 4) & 0x7
		if sel in (0x3, 0x7):  # Counter or Timer, Crystal/X1 / 16
			return CRYSTAL_HZ / 16
		if sel == 0x6:  # Timer, Crystal/X1 x1 (no divide)
			return CRYSTAL_HZ
		return 0.0

	def start_counter(self) -> None:
		self._counter_remaining = self._counter_period_ticks()
		self._counter_tick_debt = 0.0
		self._counter_running = True

	def stop_counter(self) -> None:
		# Per the SCN2681 command set, Stop Counter/Timer actually halts
		# counting only in Counter mode (one-shot; needs a fresh Start
		# command to re-arm). In Timer mode it just disables the OP3
		# output and acks/clears the pending interrupt -- the free-running
		# countdown itself is untouched, so periodic ISR-ready events keep
		# coming without any further Start command. This firmware's DUART
		# ISR bit-3 handler reads this register unconditionally on every
		# tick as its interrupt-ack (see DESIGN.md section 10) with no
		# corresponding re-arm call anywhere in the ROM, which only makes
		# sense under Timer-mode semantics -- confirmed empirically: with
		# unconditional halt-on-stop, the tick counter fired exactly once
		# and never again.
		self._counter_ready = False
		if not self._counter_is_timer_mode():
			self._counter_running = False
		self._update_irq()

	def step(self, dt_seconds: float) -> None:
		"""Advance the counter/timer by dt_seconds of wall-clock-equivalent
		time; called once per 68000 instruction from machine.py's run loop,
		mirroring how the DSP and DAC sample timers are already stepped."""
		if not self._counter_running:
			return
		hz = self._counter_clock_cache
		if hz <= 0:
			return
		self._counter_tick_debt += dt_seconds * hz
		ticks = int(self._counter_tick_debt)
		if ticks <= 0:
			return
		self._counter_tick_debt -= ticks
		self._counter_remaining -= ticks
		fired = False
		while self._counter_remaining <= 0:
			fired = True
			if self._counter_is_timer_mode():
				self._counter_remaining += self._counter_period_ticks()
			else:
				self._counter_running = False
				self._counter_remaining = 0
				break
		if fired:
			self._counter_ready = True
			self._update_irq()

	def reset(self) -> None:
		self.a = Channel()
		self.b = Channel()
		self.imr = 0
		self.acr = 0
		self._irq_state = False
		self.input_port_bits = 0b11100000
		self._input_port_read_count = 0
		self.ctur = 0
		self.ctlr = 0
		self._counter_running = False
		self._counter_remaining = 0
		self._counter_tick_debt = 0.0
		self._counter_ready = False
		self._counter_clock_cache = 0.0
