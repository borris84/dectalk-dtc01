"""Dev-only helper: collect DectalkMachine DAC samples and write a .wav for
manual inspection.

The machine's on_audio_sample callback hands us the exact word the real
AD7541 DAC chip would receive (see machine.py's _dsp_pop_outfifo:
'(data & 0xfff0) ^ 0x8000') -- that XOR converts the DSP's natural signed
sample into unsigned offset-binary, which is what a real DAC needs to
produce a bipolar analog output from a unipolar digital code (silence ->
mid-scale code 0x8000, matching the DAC's 0V output point). That is
correct and required for hardware fidelity, but it means the value is
*not* yet usable as signed PCM -- confirmed by an earlier bug here where
silence (mid-scale 0x8000, i.e. what should map to signed 0) was stored
and packed as-is, landing on raw signed value -32768 (the negative rail)
instead of 0. Voiced audio swinging up from that mis-placed baseline
could wrap past +32767 back around to strongly negative, producing loud,
clicky, distorted output -- exactly what over-amplitude/clicking symptoms
in a listening test would point to. XORing 0x8000 a second time undoes
the offset-binary conversion and recovers the DSP's natural signed
sample (silence at 0, properly centered) for standard 16-bit PCM.
"""

from __future__ import annotations

import struct
import wave

DAC_SAMPLE_HZ = 10000


class WavCollector:
	def __init__(self):
		self.samples: list[int] = []

	def on_sample(self, value: int) -> None:
		# Undo the DAC's offset-binary conversion (see module docstring) to
		# recover a properly centered signed 16-bit sample.
		self.samples.append((value ^ 0x8000) & 0xFFFF)

	def write(self, path: str) -> None:
		with wave.open(path, "wb") as w:
			w.setnchannels(1)
			w.setsampwidth(2)
			w.setframerate(DAC_SAMPLE_HZ)
			w.writeframes(b"".join(struct.pack("<H", s) for s in self.samples))

	def stats(self) -> dict:
		if not self.samples:
			return {"count": 0}
		signed = [(s - 0x10000 if s & 0x8000 else s) for s in self.samples]
		return {
			"count": len(signed),
			"min": min(signed),
			"max": max(signed),
			"nonzero_fraction": sum(1 for v in signed if v != 0) / len(signed),
		}
