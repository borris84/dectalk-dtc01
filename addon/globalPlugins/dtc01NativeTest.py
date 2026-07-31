"""DEV-ONLY smoke test: verifies the native DTC-01 emulator actually loads
and runs inside NVDA's own process (x64, Python 3.13).

This is NOT part of the shipping synth driver. It exists to answer one
question before more code is built on top of the native core: does the
DLL load and produce correct speech data under NVDA rather than under the
development interpreter?

It runs once, a few seconds after NVDA starts, on a background thread so
it can never block the screen reader. Results go to the NVDA log AND are
announced through whatever synth is currently active, so there's no need
to go read a log file.

Ships no ROMs (DESIGN.md §0) -- it reads them from a local path, which
must be set below or via the DTC01_ROM_DIR environment variable.
"""

import os
import threading
import time

import globalPluginHandler
from logHandler import log

TAG = "DTC-01 native test"

# Dev ROM location. The shipping addon will get this from NVDA config; this
# test just needs *a* valid ROM set to run against.
DEFAULT_ROM_DIR = r"C:\stuff\projects\dtc-01\roms_extracted"


def _announce(msg):
    """Speak a short result through NVDA's current synth, from the main
    thread (NVDA's speech API is not thread-safe)."""
    try:
        import wx
        import ui
        wx.CallAfter(ui.message, msg)
    except Exception:
        log.exception(f"{TAG}: could not announce result")


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    def __init__(self):
        super().__init__()
        threading.Thread(target=self._run, name="dtc01NativeTest", daemon=True).start()

    def _run(self):
        # Let NVDA finish starting up before we say anything.
        time.sleep(4.0)
        try:
            self._test()
        except Exception:
            log.exception(f"{TAG}: FAILED with an exception")
            _announce("D T C 01 native test failed with an exception. See the NVDA log.")

    def _test(self):
        rom_dir = os.environ.get("DTC01_ROM_DIR", DEFAULT_ROM_DIR)
        log.info(f"{TAG}: starting; rom_dir={rom_dir!r}")

        if not os.path.isdir(rom_dir):
            log.error(f"{TAG}: ROM directory not found: {rom_dir}")
            _announce("D T C 01 native test: ROM directory not found.")
            return

        from synthDrivers.dectalkDtc01.emu.native import NativeMachine

        import sys
        import struct
        log.info(f"{TAG}: python {sys.version}")
        log.info(f"{TAG}: pointer size {struct.calcsize('P') * 8} bit")

        t0 = time.time()
        m = NativeMachine(rom_dir)
        log.info(f"{TAG}: loaded DLL {m.dll_path}")
        log.info(f"{TAG}: core {m.version}")

        m.run_samples(5000)  # boot + settle (0.5 s of emulated time)
        boot_wall = time.time() - t0
        log.info(f"{TAG}: booted in {boot_wall:.2f}s wall, LED={m.led_state:#04x}")

        m.feed_text(b"[:np] Hello from inside N V D A.\r")
        t1 = time.time()
        samples = m.run_samples(60000)  # 6 s of emulated audio
        speak_wall = time.time() - t1

        peak = max((abs(s) for s in samples), default=0)
        host_tx = m.read_host_tx()
        rt = (len(samples) / 10000.0) / speak_wall if speak_wall else 0.0

        log.info(f"{TAG}: produced {len(samples)} samples, peak={peak}, "
                 f"{speak_wall:.2f}s wall -> {rt:.1f}x realtime")
        log.info(f"{TAG}: host TX = {host_tx!r}")
        log.info(f"{TAG}: unmapped accesses = {m.unmapped_accesses} (expect 0)")

        ok = (peak > 1000 and m.unmapped_accesses == 0
              and m.led_state == 0xDA and b"Hello" in host_tx)

        if ok:
            log.info(f"{TAG}: PASS")
            _announce(f"D T C 01 native test passed. {rt:.0f} times realtime.")
        else:
            log.error(f"{TAG}: FAIL (peak={peak}, led={m.led_state:#04x}, "
                      f"unmapped={m.unmapped_accesses}, tx={host_tx!r})")
            _announce("D T C 01 native test failed. See the NVDA log.")

        m.close()
