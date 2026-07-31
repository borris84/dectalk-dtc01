"""Cross-check our M68000 dispatch table against Musashi's authoritative
opcode table (research/musashi_m68k_in.c, MIT-licensed, Karl Stenerud).
This parses the ground-truth (mnemonic, 16-bit mask/match, 68000-validity)
for every documented opcode and diffs it against what our table actually
contains, opcode by opcode -- catching encoding bugs across the whole ISA
at once instead of one manual trace at a time.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "addon" / "synthDrivers" / "dectalkDtc01"))

from emu.m68000_ops import M68000  # noqa: E402

MUSASHI_PATH = Path(__file__).resolve().parent.parent / "research" / "musashi_m68k_in.c"

LINE_RE = re.compile(
	r"^(?P<name>\S+)\s+(?P<size>\S+)\s+(?P<proc>\S+)\s+(?P<ea>\S+)\s+"
	r"(?P<pattern>[01.]{16})\s+(?P<allowed>\S{10})\s+"
	r"(?P<m0>[US.])\s+(?P<m1>[US.])\s+(?P<m2>[US.])\s+(?P<m3>[US.])\s+(?P<m4>[US.])\s"
)


# allowed-ea column position -> (mode, reg-or-None) it represents. A mode/reg
# combo not covered by any letter in the column (when the column isn't all
# dots) is not a real 68000 encoding for that row -- see module docstring.
_EA_LETTERS = ["A", "+", "-", "D", "X", "W", "L", "d", "x", "I"]
_EA_TARGETS = [(2, None), (3, None), (4, None), (5, None), (6, None), (7, 0), (7, 1), (7, 2), (7, 3), (7, 4)]


def _allowed_mode_regs(allowed: str) -> set[tuple[int, int]] | None:
	"""Returns the set of (mode,reg) pairs the allowed-ea column permits, or
	None if the column is all dots (not applicable to this row)."""
	if allowed == "." * 10:
		return None
	pairs: set[tuple[int, int]] = set()
	for i, ch in enumerate(allowed):
		if ch == ".":
			continue
		mode, fixed_reg = _EA_TARGETS[i]
		if fixed_reg is not None:
			pairs.add((mode, fixed_reg))
		else:
			for reg in range(8):
				pairs.add((mode, reg))
	return pairs


def parse_ground_truth() -> dict[int, set[str]]:
	"""Returns {opcode: {mnemonics that match and are valid on 68000}}."""
	result: dict[int, set[str]] = {}
	text = MUSASHI_PATH.read_text(encoding="utf-8", errors="replace")
	in_table = False
	for line in text.splitlines():
		if line.startswith("M68KMAKE_TABLE_START"):
			in_table = True
			continue
		if line.startswith("M68KMAKE_TABLE_END") or line.startswith("XXXXXXXX"):
			if in_table:
				break
			continue
		if not in_table:
			continue
		m = LINE_RE.match(line)
		if not m:
			continue
		if m.group("m0") != "U" and m.group("m0") != "S":
			continue  # not valid on plain 68000
		pattern = m.group("pattern")
		mask = 0
		match = 0
		for i, ch in enumerate(pattern):
			bit = 15 - i
			if ch in "01":
				mask |= 1 << bit
				if ch == "1":
					match |= 1 << bit
		name = m.group("name")
		free_bits = [b for b in range(16) if not (mask & (1 << b))]
		n_free = len(free_bits)
		if n_free > 13:
			continue  # defensive cap; shouldn't happen for real rows

		# If the low 6 bits (a single trailing mode+reg EA field) are free
		# and the allowed-ea column applies, restrict enumeration of just
		# those two sub-fields to what's actually permitted. Mode 0/1
		# (Dn/An direct) are handled by dedicated rows elsewhere and are
		# never part of this column, so always exclude them here too.
		allowed_pairs = _allowed_mode_regs(m.group("allowed"))
		ea_bits_free = set(free_bits) >= {0, 1, 2, 3, 4, 5}

		other_free = [b for b in free_bits if b > 5 or not ea_bits_free]
		if ea_bits_free and allowed_pairs is not None:
			other_free = [b for b in free_bits if b > 5]
			n_other = len(other_free)
			for combo in range(1 << n_other):
				base = match
				for i, b in enumerate(other_free):
					if combo & (1 << i):
						base |= 1 << b
				for mode, reg in allowed_pairs:
					opcode = base | (mode << 3) | reg
					result.setdefault(opcode, set()).add(name)
			continue

		for combo in range(1 << n_free):
			opcode = match
			for i, b in enumerate(free_bits):
				if combo & (1 << i):
					opcode |= 1 << b
			result.setdefault(opcode, set()).add(name)
	return result


def main() -> int:
	ground_truth = parse_ground_truth()
	print(f"Parsed {len(ground_truth)} opcodes with >=1 valid 68000 mnemonic from Musashi table.")

	cpu = M68000(bus=None)  # instructions are never executed here, only the dispatch table is inspected

	missing = []  # ground truth says valid, ours is None

	for opcode in range(0x10000):
		gt = ground_truth.get(opcode)
		mine_present = cpu._opcode_table[opcode] is not None
		if gt and not mine_present:
			missing.append((opcode, sorted(gt)))

	print(f"\nOpcodes valid per ground truth but MISSING from our table: {len(missing)}")
	for opcode, names in missing[:200]:
		print(f"  {opcode:#06x}  {opcode:016b}  expected one of: {names}")
	if len(missing) > 200:
		print(f"  ... and {len(missing) - 200} more")

	# Reverse direction: opcodes we implement that ground truth says are NOT
	# valid 68000 instructions (over-permissive addressing-mode ranges).
	# Line-A (0xA000-0xAFFF) and Line-F (0xF000-0xFFFF) are deliberately
	# filled with emulator-trap handlers -- not a bug, exclude them.
	extra = []
	for opcode in range(0x10000):
		if 0xA000 <= opcode <= 0xAFFF or 0xF000 <= opcode <= 0xFFFF:
			continue
		if cpu._opcode_table[opcode] is not None and opcode not in ground_truth:
			extra.append(opcode)

	print(f"\nOpcodes WE implement but ground truth says are NOT valid 68000 instructions: {len(extra)}")
	for opcode in extra[:200]:
		print(f"  {opcode:#06x}  {opcode:016b}")
	if len(extra) > 200:
		print(f"  ... and {len(extra) - 200} more")

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
