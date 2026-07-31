"""Sonic-based rate boost.

The DTC-01's own rate command tops out at 350 wpm (Owner's Manual, and the
firmware clamps there), which is well short of what experienced screen
reader users often want. Sonic time-compresses the rendered audio without
shifting pitch, so speech can go faster than the 1984 hardware ever could
while still sounding like the hardware.

`sonic.dll` ships with NVDA and is bound here directly through ctypes,
which is the path that has actually been tested against it. NVDA's own
`synthDrivers._sonic` wrapper is kept only as a fallback for the case
where the DLL cannot be located: preferring it turned out to be a
mistake, since an unverified signature there made every call raise and
the failure was swallowed, so rate boost quietly did nothing inside NVDA
while passing every offline test.
"""

from __future__ import annotations

import ctypes
import os
import sys

try:
    from logHandler import log
except ImportError:          # running outside NVDA (offline harness)
    class _NullLog:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
        def debugWarning(self, *a, **k): pass
    log = _NullLog()


def _findSonicDll():
    candidates = []
    # Explicit override: lets the offline harness (and any unusual NVDA
    # layout) point at the DLL directly.
    override = os.environ.get("DTC01_SONIC_DLL")
    if override:
        candidates.append(override)
    exeDir = os.path.dirname(sys.executable or "")
    if exeDir:
        candidates.append(os.path.join(exeDir, "synthDrivers", "sonic.dll"))
        candidates.append(os.path.join(exeDir, "sonic.dll"))
    candidates.append("sonic.dll")  # let the loader search
    for path in candidates:
        try:
            return ctypes.CDLL(path)
        except OSError:
            continue
    return None


class _CtypesSonic:
    """Minimal binding to the parts of sonic we need."""

    def __init__(self, sampleRate, channels=1):
        dll = _findSonicDll()
        if dll is None:
            raise RuntimeError("sonic.dll not found")
        self._dll = dll
        dll.sonicCreateStream.restype = ctypes.c_void_p
        dll.sonicCreateStream.argtypes = [ctypes.c_int, ctypes.c_int]
        dll.sonicDestroyStream.argtypes = [ctypes.c_void_p]
        dll.sonicSetSpeed.argtypes = [ctypes.c_void_p, ctypes.c_float]
        dll.sonicWriteShortToStream.restype = ctypes.c_int
        dll.sonicWriteShortToStream.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16), ctypes.c_int]
        dll.sonicReadShortFromStream.restype = ctypes.c_int
        dll.sonicReadShortFromStream.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16), ctypes.c_int]
        dll.sonicSamplesAvailable.restype = ctypes.c_int
        dll.sonicSamplesAvailable.argtypes = [ctypes.c_void_p]
        dll.sonicFlushStream.argtypes = [ctypes.c_void_p]

        stream = dll.sonicCreateStream(sampleRate, channels)
        if not stream:
            raise RuntimeError("sonicCreateStream failed")
        self._stream = ctypes.c_void_p(stream)
        self._out = (ctypes.c_int16 * 16384)()

    def setSpeed(self, speed):
        self._dll.sonicSetSpeed(self._stream, ctypes.c_float(float(speed)))

    def _read(self):
        chunks = []
        while True:
            got = self._dll.sonicReadShortFromStream(
                self._stream, self._out, len(self._out))
            if got <= 0:
                break
            chunks.append(bytes(memoryview(self._out)[:got].cast("B")))
            if got < len(self._out):
                break
        return b"".join(chunks)

    def process(self, pcm):
        if not pcm:
            return b""
        n = len(pcm) // 2
        buf = (ctypes.c_int16 * n).from_buffer_copy(pcm)
        self._dll.sonicWriteShortToStream(self._stream, buf, n)
        return self._read()

    def flush(self):
        self._dll.sonicFlushStream(self._stream)
        return self._read()

    def close(self):
        if getattr(self, "_stream", None):
            self._dll.sonicDestroyStream(self._stream)
            self._stream = None


class RateBooster:
    """Time-compresses 16-bit mono PCM by `speed`, pitch unchanged.

    A speed of 1.0 is a pass-through and costs nothing, so the driver can
    keep this in the audio path unconditionally.
    """

    def __init__(self, sampleRate):
        self._sampleRate = sampleRate
        self._impl = None
        self._speed = 1.0
        self._broken = False

    def _ensure(self):
        """Create the Sonic stream, preferring the binding we have actually
        exercised.

        The direct ctypes binding goes first because it is verified against
        the real sonic.dll. NVDA's bundled `synthDrivers._sonic` wrapper is
        only a fallback: its exact signatures were never confirmed here, and
        when it was tried first a mismatch made every process() raise, which
        this class then swallowed by passing the audio through untouched --
        rate boost silently did nothing inside NVDA while working perfectly
        in offline tests, which used the ctypes path.
        """
        if self._impl is not None or self._broken:
            return self._impl
        problems = []
        impl = None
        try:
            impl = _CtypesSonic(self._sampleRate, 1)
        except Exception as e:
            problems.append("ctypes sonic.dll: %s" % (e,))
            try:
                from synthDrivers import _sonic  # type: ignore
                _sonic.initialize()
                impl = _NvdaSonic(_sonic, self._sampleRate)
            except Exception as e2:
                problems.append("NVDA _sonic: %s" % (e2,))
        if impl is None:
            self._broken = True
            log.warning("DTC-01: rate boost unavailable (%s); speech will play "
                        "at the firmware's own rate" % "; ".join(problems))
            return None
        impl.setSpeed(self._speed)
        self._impl = impl
        log.info("DTC-01: rate boost using %s" % type(impl).__name__)
        return impl

    @property
    def speed(self):
        return self._speed

    @speed.setter
    def speed(self, value):
        value = max(1.0, min(6.0, float(value)))
        if abs(value - self._speed) < 0.001:
            return
        self._speed = value
        if self._impl is not None:
            self._impl.setSpeed(value)

    @property
    def active(self):
        return self._speed > 1.001 and not self._broken

    def process(self, pcm):
        if not self.active:
            return pcm
        impl = self._ensure()
        if impl is None:
            return pcm
        try:
            return impl.process(pcm)
        except Exception:
            self._broken = True
            log.error("DTC-01: rate boost failed mid-stream and has been "
                      "disabled for this session", exc_info=True)
            return pcm

    def flush(self):
        if not self.active or self._impl is None:
            return b""
        try:
            return self._impl.flush()
        except Exception:
            self._broken = True
            return b""

    def reset(self):
        """Drop anything buffered, e.g. after a cancel."""
        if self._impl is not None:
            try:
                self._impl.flush()
            except Exception:
                pass

    def close(self):
        if self._impl is not None:
            try:
                self._impl.close()
            except Exception:
                pass
            self._impl = None


class _NvdaSonic:
    """Adapter over NVDA's bundled synthDrivers._sonic wrapper."""

    def __init__(self, module, sampleRate):
        self._stream = module.SonicStream(sampleRate, 1)

    def setSpeed(self, speed):
        self._stream.speed = float(speed)

    def _read(self):
        avail = self._stream.samplesAvailable
        if not avail:
            return b""
        data = self._stream.readShort(avail)
        return bytes(data) if not isinstance(data, bytes) else data

    def process(self, pcm):
        n = len(pcm) // 2
        buf = (ctypes.c_int16 * n).from_buffer_copy(pcm)
        self._stream.writeShort(buf, n)
        return self._read()

    def flush(self):
        self._stream.flush()
        return self._read()

    def close(self):
        pass
