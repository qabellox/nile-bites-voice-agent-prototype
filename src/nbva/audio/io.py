"""Microphone capture and speaker playback via PortAudio (sounddevice).

The capture stream runs in a background thread and hands fixed-size blocks to a
VAD, so the audio device is never blocked by the language model or the tools. A
slow model therefore cannot drop audio or shift the endpoint, which is what keeps
the latency measurement honest.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from .vad import EnergyVad


class AudioError(RuntimeError):
    pass


@dataclass
class AudioDeviceInfo:
    index: int | None
    name: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float


def list_devices(kind: str = "all") -> list[AudioDeviceInfo]:
    import sounddevice as sd

    out: list[AudioDeviceInfo] = []
    default_in, default_out = sd.default.device
    for idx, dev in enumerate(sd.query_devices()):
        is_input = dev["max_input_channels"] > 0
        is_output = dev["max_output_channels"] > 0
        if kind == "input" and not is_input:
            continue
        if kind == "output" and not is_output:
            continue
        out.append(
            AudioDeviceInfo(
                index=idx,
                name=dev["name"],
                max_input_channels=int(dev["max_input_channels"]),
                max_output_channels=int(dev["max_output_channels"]),
                default_samplerate=float(dev["default_samplerate"]),
            )
        )
    return out


def default_device_name(kind: str) -> str:
    import sounddevice as sd

    idx = sd.default.device[0 if kind == "input" else 1]
    if idx is None:
        return "system default"
    return sd.query_devices(idx)["name"]


class MicrophoneCapture:
    """Continuously reads the microphone and emits completed utterances."""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        block_ms: int = 20,
        device: Any = None,
        vad: EnergyVad | None = None,
        on_utterance: Callable[[np.ndarray], None] | None = None,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.block_ms = block_ms
        self.device = device
        self.vad = vad
        self.on_utterance = on_utterance

        self._stream = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._frames = 0
        self._utterances = 0
        self._error: str | None = None
        self._paused = threading.Event()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        import sounddevice as sd

        if self._running:
            return
        self._frames = 0
        self._utterances = 0
        self._error = None
        self._running = True

        def callback(indata, frames, time_info, status):  # pragma: no cover - realtime
            if status:
                self._error = str(status)
            mono = indata[:, 0] if indata.ndim > 1 else indata
            block = np.asarray(mono, dtype=np.float32).copy()
            self._frames += 1
            if self._paused.is_set():
                return
            if self.vad is not None and self.vad.step(block):
                audio = self.vad.take_utterance()
                if audio.size:
                    self._utterances += 1
                    if self.on_utterance:
                        self.on_utterance(audio)

        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="float32",
                blocksize=int(self.sample_rate * self.block_ms / 1000),
                device=self.device,
                callback=callback,
            )
            self._stream.start()
        except Exception as exc:
            self._running = False
            raise AudioError(
                f"could not open the microphone at {self.sample_rate} Hz: {exc}. "
                f"Try setting audio.input.device explicitly in config/audio.yaml."
            ) from exc

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # -- control -----------------------------------------------------------
    def pause(self) -> None:
        """Stop feeding the VAD, e.g. while the agent is speaking."""
        self._paused.set()

    def resume(self) -> None:
        if self.vad is not None:
            self.vad._reset()
        self._paused.clear()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def utterance_count(self) -> int:
        return self._utterances

    @property
    def status(self) -> str | None:
        return self._error

    def wait_for_speech(self, timeout: float = 30.0) -> bool:
        """Block until the VAD reports the first speech frame after a reset."""
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if self.vad is not None and not self.vad.calibrated:
                time.sleep(0.02)
                continue
            if self.vad is not None and self.vad.state.value == "speech":
                return True
            time.sleep(0.02)
        return False

    def describe(self) -> dict[str, Any]:
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "block_ms": self.block_ms,
            "device": self.device if self.device is not None else "system default",
            "vad": self.vad.describe() if self.vad else None,
        }


class AudioPlayer:
    """Plays float32 numpy audio to the default output device.

    The stream is callback driven rather than write driven, because the latency
    metric needs the exact instant the device starts consuming samples. The
    ``on_first_sample`` callback fires inside the PortAudio callback the first
    time audio is handed over, which is the endpoint of the LOCKED primary
    latency metric.

    Definition note for the methodology: the measured endpoint is "first audio
    sample submitted to the output stream", not "first acoustic wave at the
    speaker". The difference is the device output latency, which is a property
    of the hardware and therefore constant across code-switching conditions.
    """

    def __init__(self, sample_rate: int | None = None, device: Any = None, latency: str = "low"):
        self.sample_rate = sample_rate
        self.device = device
        self.latency = latency
        self._stream = None
        self._playing = False
        # Barge-in support: when set, the PortAudio callback drains silence
        # and ends playback at the next block instead of playing the tail.
        self._stop_requested = threading.Event()

    def play(self, audio: np.ndarray, sample_rate: int | None = None,
             on_first_sample: Callable[[], None] | None = None) -> float:
        """Play audio synchronously. Returns wall time in seconds."""
        import sounddevice as sd

        data = np.ascontiguousarray(np.asarray(audio, dtype=np.float32).reshape(-1))
        if data.size == 0:
            if on_first_sample:
                on_first_sample()
            return 0.0

        sr = int(sample_rate or self.sample_rate or 16000)
        total = data.size
        cursor = {"i": 0, "first": True}
        lock = threading.Lock()
        finished = threading.Event()

        stop_event = self._stop_requested
        stop_event.clear()

        def callback(outdata, frames, time_info, status):  # pragma: no cover - realtime
            with lock:
                if stop_event.is_set():
                    outdata[:] = 0.0
                    cursor["i"] = total
                    finished.set()
                    return
                i = cursor["i"]
                n = min(frames, total - i)
                if n > 0:
                    outdata[:n, 0] = data[i : i + n]
                    if cursor["first"]:
                        cursor["first"] = False
                        if on_first_sample:
                            on_first_sample()
                    cursor["i"] = i + n
                if n < frames:
                    outdata[n:, 0] = 0.0
            if cursor["i"] >= total:
                finished.set()

        started = time.perf_counter()
        try:
            stream = sd.OutputStream(
                samplerate=sr, channels=1, dtype="float32", device=self.device,
                blocksize=0, latency=self.latency, callback=callback,
            )
            with stream:
                self._playing = True
                stream.start()
                finished.wait(timeout=total / float(sr) + 5.0)
                # Let the device drain its buffer so the tail is not clipped.
                time.sleep(0.2)
        except Exception as exc:
            raise AudioError(f"playback failed: {exc}") from exc
        finally:
            self._playing = False

        return time.perf_counter() - started

    @property
    def is_playing(self) -> bool:
        return self._playing

    def stop(self) -> None:
        """Request early end of playback (barge-in). Thread-safe, idempotent."""
        self._stop_requested.set()


def probe_devices() -> dict[str, Any]:
    """Report the audio environment, for the run manifest and for setup help."""
    import sounddevice as sd

    inputs = list_devices("input")
    outputs = list_devices("output")
    return {
        "default_input": default_device_name("input"),
        "default_output": default_device_name("output"),
        "hostapis": [api["name"] for api in sd.query_hostapis()],
        "input_devices": [
            {"index": d.index, "name": d.name, "rate": d.default_samplerate}
            for d in inputs
        ],
        "output_devices": [
            {"index": d.index, "name": d.name, "rate": d.default_samplerate}
            for d in outputs
        ],
    }
