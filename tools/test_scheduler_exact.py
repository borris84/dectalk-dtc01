"""Prove the integer scheduler matches exact rational timing, and that the
floating-point one it replaced did not.

The DTC-01's DAC is clocked by hardware at a fixed 10kHz and the DSP at
exactly half the 68000's clock, so both schedules are exact rationals in
units of 68000 cycles. dtc01.c used to accumulate *seconds* in a double,
which made a sample boundary land one instruction early or late whenever
rounding tipped a comparison that should have been exact.

This checks the arithmetic in isolation -- no emulator, no ROMs -- against
Fraction, which is exact by construction. Run it after touching the
scheduler in native/dtc01.c or emu/machine.py.
"""

from __future__ import annotations

import random
from fractions import Fraction

M68K_HZ = 10_000_000
DSP_CYCLE_HZ = 5_000_000
DAC_SAMPLE_HZ = 10_000
M68K_PER_DSP = M68K_HZ // DSP_CYCLE_HZ      # 2
M68K_PER_DAC = M68K_HZ // DAC_SAMPLE_HZ     # 1000


def dac_float(cycle_counts):
	"""The old scheme: accumulate seconds in a double."""
	period = 1.0 / DAC_SAMPLE_HZ
	debt = 0.0
	out = []
	for i, cyc in enumerate(cycle_counts):
		debt += cyc / M68K_HZ
		while debt >= period:
			debt -= period
			out.append(i)
	return out


def dac_int(cycle_counts):
	"""The new scheme: accumulate 68000 cycles in an integer."""
	debt = 0
	out = []
	for i, cyc in enumerate(cycle_counts):
		debt += cyc
		while debt >= M68K_PER_DAC:
			debt -= M68K_PER_DAC
			out.append(i)
	return out


def dac_exact(cycle_counts):
	"""Ground truth: the same algorithm in exact rational arithmetic."""
	period = Fraction(1, DAC_SAMPLE_HZ)
	debt = Fraction(0)
	out = []
	for i, cyc in enumerate(cycle_counts):
		debt += Fraction(cyc, M68K_HZ)
		while debt >= period:
			debt -= period
			out.append(i)
	return out


def dsp_int(cycle_counts):
	"""DSP spend per instruction, integer scheme (tms_run overshoot ignored
	here -- this checks the divide/carry, which is what changed)."""
	debt = 0
	out = []
	for cyc in cycle_counts:
		debt += cyc
		spend = debt // M68K_PER_DSP
		debt -= spend * M68K_PER_DSP
		out.append(spend)
	return out


def dsp_exact(cycle_counts):
	debt = Fraction(0)
	out = []
	for cyc in cycle_counts:
		debt += Fraction(cyc, M68K_PER_DSP)
		spend = int(debt)
		debt -= spend
		out.append(spend)
	return out


def main() -> int:
	rng = random.Random(20260801)
	# 68000 instruction timings: mostly 4-20 cycles, occasionally much longer.
	counts = [rng.choice((4, 6, 8, 8, 10, 12, 12, 14, 16, 20, 34, 40, 70))
			  for _ in range(400_000)]
	total = sum(counts)
	print(f"  {len(counts):,} instructions, {total:,} 68000 cycles "
		  f"({total / M68K_HZ:.2f}s emulated)")

	exact = dac_exact(counts)
	inte = dac_int(counts)
	flt = dac_float(counts)

	ok = True
	if inte == exact:
		print(f"  DAC integer  == exact rational   ({len(exact):,} samples)  PASS")
	else:
		d = sum(1 for a, b in zip(inte, exact) if a != b)
		print(f"  DAC integer  != exact rational   {d} of {len(exact)} differ  FAIL")
		ok = False

	if flt == exact:
		print("  DAC float    == exact rational   (no jitter found in this sample)")
	else:
		d = sum(1 for a, b in zip(flt, exact) if a != b)
		first = next(i for i, (a, b) in enumerate(zip(flt, exact)) if a != b)
		print(f"  DAC float    != exact rational   {d:,} of {len(exact):,} samples "
			  f"land on a different instruction (first at sample {first:,})")
		print("      ^ this is the jitter the integer scheduler removes")

	di, de = dsp_int(counts), dsp_exact(counts)
	if di == de:
		print(f"  DSP integer  == exact rational   ({sum(di):,} DSP cycles)  PASS")
	else:
		print(f"  DSP integer  != exact rational   FAIL")
		ok = False

	print("\n  " + ("all exactness checks passed" if ok else "FAILURES ABOVE"))
	return 0 if ok else 1


if __name__ == "__main__":
	raise SystemExit(main())
