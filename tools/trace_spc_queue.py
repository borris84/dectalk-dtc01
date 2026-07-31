"""Dev-only diagnostic: watch writes to the SPC send-queue control block
($082726-$082760, discovered via static disasm of the 0x13Cxx SPC module --
see chat log / DESIGN.md session notes) and to the SPC MMIO region
($09C000-$09C007), logging the PC that performed each write. Used to find,
dynamically, what (if anything) ever enqueues a synthesis buffer for the
SPC feed routine at 0x13C7C to send -- static search found zero absolute
references to the queue head/tail pointers anywhere outside the SPC
module itself, suggesting the producer passes a queue-descriptor pointer
through a register rather than addressing it literally.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "addon" / "synthDrivers" / "dectalkDtc01"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from emu.machine import DectalkMachine  # noqa: E402

WATCH_LO = 0x082700
WATCH_HI = 0x082780


def main() -> int:
    rom_dir = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "roms_extracted")
    text = sys.argv[2] if len(sys.argv) > 2 else "[:np] Hello world.\r"
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0

    m = DectalkMachine(rom_dir, lambda s: None, lambda b: None)
    bus = m.bus
    events = []

    orig_write8 = bus.write8
    orig_write16 = bus.write16

    def logged(addr, value, width):
        if WATCH_LO <= addr < WATCH_HI or 0x09C000 <= addr < 0x09C008:
            events.append((m.cpu.PC, addr, value, width))

    def write8(addr, value):
        logged(addr, value, 8)
        return orig_write8(addr, value)

    def write16(addr, value):
        logged(addr, value, 16)
        return orig_write16(addr, value)

    bus.write8 = write8
    bus.write16 = write16

    print("Booting / settling...")
    m.run_seconds(0.3)
    print(f"LED after settle: {m.led_state:#04x}  events so far: {len(events)}")
    boot_events = len(events)

    print(f"Feeding text: {text!r}")
    m.duart.feed_rx_b(text.encode("ascii", "replace"))

    m.run_seconds(seconds)

    print(f"Final LED: {m.led_state:#04x}")
    print(f"Total watched writes: {len(events)} ({boot_events} during boot, {len(events) - boot_events} after feed)")
    for pc, addr, value, width in events:
        print(f"  PC={pc:06X}  write{width} [{addr:06X}] = {value:#x}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
