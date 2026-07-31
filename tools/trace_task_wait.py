"""Dev-only: check whether channel B's queue descriptor (base $80328, the
struct passed as A1 into the RX ISR chain, see tools/trace_spc_queue.py
sibling investigation) has a task blocked waiting on it (offset 16 / 0x10)
at various points, and watch the OS ready-queue head ($080000) for writes,
to test the hypothesis that no consumer task is registered to receive
host-channel input at all.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "addon" / "synthDrivers" / "dectalkDtc01"))
from emu.machine import DectalkMachine  # noqa: E402

CHANNEL_B_STRUCT = 0x80328

def main() -> int:
    rom_dir = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "roms_extracted")
    m = DectalkMachine(rom_dir, lambda s: None, lambda b: None)
    bus = m.bus

    ready_q_events = []
    orig_write32 = bus.write32
    def write32(addr, value):
        if 0x080000 <= addr < 0x080010:
            ready_q_events.append((m.cpu.PC, addr, value))
        return orig_write32(addr, value)
    bus.write32 = write32

    def dump(label):
        # offset 16 (0x10) into the channel-B struct: "task waiting" ptr
        waiting = bus.read32(CHANNEL_B_STRUCT + 16)
        print(f"[{label}] channel-B struct+0x10 (waiting task ptr) = {waiting:#010x}")

    m.run_seconds(0.3)
    dump("after boot settle")
    print(f"ready-queue ($080000) writes so far: {len(ready_q_events)}")

    m.duart.feed_rx_b(b"[:np] Hello world.\r")
    m.run_seconds(2.0)
    dump("after feed + 2s run")
    print(f"ready-queue ($080000) writes total: {len(ready_q_events)}")
    for pc, addr, value in ready_q_events:
        print(f"  PC={pc:06X}  write32 [{addr:06X}] = {value:#010x}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
