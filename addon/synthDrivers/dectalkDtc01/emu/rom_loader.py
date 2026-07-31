"""Locate, validate, and assemble the DTC-01 ROM images.

The DTC-01 main-CPU and DSP ROMs are Digital Equipment Corp / Fonix
copyrighted firmware. This module never bundles ROM bytes -- it only knows
how to recognise a user-supplied dump (by content hash, not filename, so it
tolerates whatever naming convention the user's dump happens to use) and
assemble it into the linear images the emulator cores need.

See DESIGN.md sections 1 and 2 for the byte-interleave layout this mirrors,
taken directly from src/mame/dec/dectalk.cpp's ROM_START(dectalk).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass


class RomValidationError(Exception):
	"""Raised when required ROM chips are missing or fail checksum validation."""


@dataclass(frozen=True)
class RomChunk:
	sha1: str
	size: int
	offset: int  # starting byte offset within the linear image
	label: str  # human-readable chip identity, for error messages only


# 68000 main-CPU program ROM: 16 x 0x4000-byte chips, byte-interleaved in
# pairs (stride 2) into a linear 0x40000-byte big-endian word image.
# v2.0 firmware (first-half tag 23Jul84 / second-half tag 02Jul84).
MAIN_CPU_ROMS: tuple[RomChunk, ...] = (
	RomChunk("e586de03e113683c2534fca1f3f40ba391193044", 0x4000, 0x00000, "23-123e5 @E8"),
	RomChunk("7954bb56b7591f8954403a22d34de31c7d5441ac", 0x4000, 0x00001, "23-119e5 @E22"),
	RomChunk("7724babf4ae5d77c0b4200f608d599058d04b25c", 0x4000, 0x08000, "23-124e5 @E7"),
	RomChunk("af5e4ea0b3631f7d6f16c22e86a33fa2cb520ee0", 0x4000, 0x08001, "23-120e5 @E21"),
	RomChunk("1b60cd71dfa83408b17e13f683b6bf3198c905cc", 0x4000, 0x10000, "23-125e5 @E6"),
	RomChunk("4ad0b00628a90085cd7c78a354256c39fd14db6c", 0x4000, 0x10001, "23-121e5 @E20"),
	RomChunk("e2b2415eec838ddd46094f2fea93fd289dd0caa2", 0x4000, 0x18000, "23-126e5 @E5"),
	RomChunk("92ab22a24484ad0d0f5c8a07347105509999f3ee", 0x4000, 0x18001, "23-122e5 @E19"),
	RomChunk("b5aec0bf37a176ff4d66d6a10357715957662ebd", 0x4000, 0x20000, "23-103e5 @E4"),
	RomChunk("891f3a3b4ce75ef14001257bc8f1f60463a9a7cb", 0x4000, 0x20001, "23-095e5 @E18"),
	RomChunk("4d6808f67cbdd316df23adc8ddf701df57aa854a", 0x4000, 0x28000, "23-104e5 @E3"),
	RomChunk("496c69e52cfa013173f7b9c500ce544a03ad01f7", 0x4000, 0x28001, "23-096e5 @E17"),
	RomChunk("de0c25687bab3ff0c88c98622092e0b58331aa16", 0x4000, 0x30000, "23-105e5 @E2"),
	RomChunk("c450abae0ccf372d7eb87370b8a8c97a45e164d3", 0x4000, 0x30001, "23-097e5 @E16"),
	RomChunk("355348bfc96a04193136cdde3418366e6476c3ca", 0x4000, 0x38000, "23-106e5 @E1"),
	RomChunk("01921e77b46c2d4845023605239c45ffa4a35872", 0x4000, 0x38001, "23-098e5 @E15"),
)
MAIN_CPU_IMAGE_SIZE = 0x40000

# TMS32010 DSP program ROM: the "204/205" pair, which the MAME driver's own
# comments identify as the clean-audio combo (165/166 and 409/410 are noted
# as clipping/silent in some configurations -- avoid them).
DSP_ROMS: tuple[RomChunk, ...] = (
	RomChunk("3136bae243ef48721e21c66fde70dab5fc3c21d0", 0x800, 0x000, "23-205f4 @E70"),
	RomChunk("9409f90f7a397b041e4440341f2d7934cb479285", 0x800, 0x001, "23-204f4 @E69"),
)
DSP_IMAGE_SIZE = 0x1000  # 0x800 words, 2 bytes/word


def _index_dir_by_sha1(rom_dir: str) -> dict[str, bytes]:
	index: dict[str, bytes] = {}
	for name in os.listdir(rom_dir):
		path = os.path.join(rom_dir, name)
		if not os.path.isfile(path):
			continue
		with open(path, "rb") as f:
			data = f.read()
		index[hashlib.sha1(data).hexdigest()] = data
	return index


def _assemble(rom_dir: str, chunks: tuple[RomChunk, ...], image_size: int) -> bytes:
	files_by_sha1 = _index_dir_by_sha1(rom_dir)
	image = bytearray(image_size)
	missing: list[str] = []
	for chunk in chunks:
		data = files_by_sha1.get(chunk.sha1)
		if data is None:
			missing.append(f"{chunk.label} (sha1 {chunk.sha1})")
			continue
		if len(data) != chunk.size:
			missing.append(f"{chunk.label}: found matching hash but size {len(data)} != expected {chunk.size}")
			continue
		for i, b in enumerate(data):
			image[chunk.offset + i * 2] = b
	if missing:
		raise RomValidationError(
			"ROM validation failed, missing/invalid chips:\n  " + "\n  ".join(missing)
		)
	return bytes(image)


def build_main_cpu_image(rom_dir: str) -> bytes:
	"""Assemble the 0x40000-byte 68000 program ROM image from rom_dir."""
	return _assemble(rom_dir, MAIN_CPU_ROMS, MAIN_CPU_IMAGE_SIZE)


def build_dsp_image(rom_dir: str) -> bytes:
	"""Assemble the DSP program ROM image (0x800 big-endian words) from rom_dir.

	NOTE: which chip supplies the high vs. low byte of each word is inferred
	from the same even/odd offset convention used for the (confirmed-correct)
	main CPU ROMs, not independently verified for the DSP region -- see
	DESIGN.md section 8. If the TMS32010 core fails to boot sensibly, check
	this byte order first.
	"""
	return _assemble(rom_dir, DSP_ROMS, DSP_IMAGE_SIZE)


def dsp_words(rom_dir: str) -> list[int]:
	image = build_dsp_image(rom_dir)
	return [
		(image[i] << 8) | image[i + 1]
		for i in range(0, len(image), 2)
	]


def validate_rom_dir(rom_dir: str) -> None:
	"""Raise RomValidationError with a full report if rom_dir is unusable."""
	errors: list[str] = []
	for name, builder in (("main CPU", build_main_cpu_image), ("DSP", build_dsp_image)):
		try:
			builder(rom_dir)
		except RomValidationError as e:
			errors.append(f"[{name}] {e}")
	if errors:
		raise RomValidationError("\n".join(errors))
