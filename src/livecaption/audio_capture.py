#!/usr/bin/env python3
"""
System-audio capture without external programs.

Uses libpulse-simple (present wherever PulseAudio or PipeWire-Pulse runs) through
ctypes, so the AppImage doesn't depend on the `parec` tool. If libpulse-simple
can't be loaded, falls back to the `parec` command when that exists.

    cap = open_capture("alsa_output....monitor", 16000)
    data = cap.read(4096)      # blocks; exactly 4096 bytes of mono s16le (b"" = stream ended)
    cap.stop(); cap.close()
"""

import ctypes
import ctypes.util
import shutil
import subprocess

PA_SAMPLE_S16LE = 3
PA_STREAM_RECORD = 2
_UNSET = 0xFFFFFFFF


class CaptureError(RuntimeError):
    pass


class _SampleSpec(ctypes.Structure):
    _fields_ = [("format", ctypes.c_int), ("rate", ctypes.c_uint32), ("channels", ctypes.c_uint8)]


class _BufferAttr(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint32) for n in ("maxlength", "tlength", "prebuf", "minreq", "fragsize")]


class SimpleCapture:
    """Record from a PulseAudio source (a sink's ".monitor" captures what you hear)."""

    def __init__(self, device, rate, latency_ms=100):
        name = ctypes.util.find_library("pulse-simple") or "libpulse-simple.so.0"
        self._simple = ctypes.CDLL(name)            # OSError if it isn't installed
        pulse = ctypes.CDLL(ctypes.util.find_library("pulse") or "libpulse.so.0")
        pulse.pa_strerror.restype = ctypes.c_char_p
        pulse.pa_strerror.argtypes = [ctypes.c_int]
        self._strerror = pulse.pa_strerror

        s = self._simple
        s.pa_simple_new.restype = ctypes.c_void_p
        s.pa_simple_new.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                                    ctypes.c_char_p, ctypes.POINTER(_SampleSpec), ctypes.c_void_p,
                                    ctypes.POINTER(_BufferAttr), ctypes.POINTER(ctypes.c_int)]
        s.pa_simple_read.restype = ctypes.c_int
        s.pa_simple_read.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                     ctypes.POINTER(ctypes.c_int)]
        s.pa_simple_free.restype = None
        s.pa_simple_free.argtypes = [ctypes.c_void_p]

        spec = _SampleSpec(PA_SAMPLE_S16LE, rate, 1)
        attr = _BufferAttr(_UNSET, _UNSET, _UNSET, _UNSET, rate * 2 * latency_ms // 1000)
        err = ctypes.c_int(0)
        self._h = s.pa_simple_new(None, b"live-caption", PA_STREAM_RECORD,
                                  device.encode() if device else None, b"captions",
                                  ctypes.byref(spec), None, ctypes.byref(attr), ctypes.byref(err))
        if not self._h:
            raise CaptureError(f"Could not open '{device}': {self._strerror(err.value).decode()}")

    def read(self, nbytes):
        buf = ctypes.create_string_buffer(nbytes)
        err = ctypes.c_int(0)
        if self._simple.pa_simple_read(self._h, buf, nbytes, ctypes.byref(err)) < 0:
            raise CaptureError(self._strerror(err.value).decode())
        return buf.raw

    def stop(self):
        pass  # a blocked read can't be interrupted; the daemon thread ends with the process

    def close(self):
        if self._h:
            self._simple.pa_simple_free(self._h)
            self._h = None


class ParecCapture:
    """Fallback: the `parec` command."""

    def __init__(self, device, rate, latency_ms=100):
        cmd = ["parec", f"--rate={rate}", "--format=s16le", "--channels=1",
               f"--latency-msec={latency_ms}"]
        if device:
            cmd.insert(1, f"--device={device}")
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def read(self, nbytes):
        return self._proc.stdout.read(nbytes)

    def stop(self):
        self._proc.terminate()

    def close(self):
        self.stop()


def open_capture(device, rate, latency_ms=100):
    try:
        return SimpleCapture(device, rate, latency_ms)
    except OSError as e:                      # libpulse-simple missing
        if shutil.which("parec"):
            print(f"[Audio] libpulse-simple unavailable ({e}); using parec")
            return ParecCapture(device, rate, latency_ms)
        raise CaptureError("Neither libpulse-simple nor the 'parec' tool was found. Install "
                           "PulseAudio or PipeWire's PulseAudio support (pipewire-pulse).")
