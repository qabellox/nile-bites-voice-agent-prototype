"""Overlapping-window framing over a growing PCM buffer.

Streaming ASR works by re-decoding a sliding window of recent audio as new
speech arrives, instead of waiting for the utterance to finish. Each call to
:meth:`SlidingWindowBuffer.push` appends PCM and reports whether a new decode
window is due, so callers can overlap decoding window N+1 with understanding
of window N.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class DecodeWindow:
    """One slice of audio offered to the ASR for (re-)decoding."""

    audio: np.ndarray
    sample_rate: int
    # Monotonic index of this window (0, 1, 2, ...).
    index: int
    # Start offset of the window within the full utterance, in seconds.
    start_s: float
    # Total audio received so far, in seconds (diagnostic only).
    buffered_s: float


@dataclass
class SlidingWindowBuffer:
    """Accumulates mono PCM and emits overlapping decode windows.

    ``window_s`` is how much audio each decode sees; ``step_s`` is how much
    new audio must arrive before the next decode is due. Overlap
    (``window_s - step_s``) gives the stability policy repeated looks at the
    same speech, which is what lets a prefix become *committed*.
    """

    sample_rate: int = 16000
    window_s: float = 4.0
    step_s: float = 1.0

    _buf: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32), repr=False)
    _next_due_sample: int = 1  # first window fires once window_s is buffered
    _window_index: int = 0

    def __post_init__(self) -> None:
        self._buf = np.ascontiguousarray(self._buf, dtype=np.float32)
        self._next_due_sample = int(self.window_s * self.sample_rate)

    @property
    def buffered_s(self) -> float:
        return float(self._buf.size) / float(self.sample_rate)

    def push(self, pcm: np.ndarray) -> list[DecodeWindow]:
        """Append PCM (any length, mono or stereo) and return due windows."""
        data = np.asarray(pcm, dtype=np.float32)
        if data.ndim > 1:
            data = data.mean(axis=1)
        data = np.ascontiguousarray(data.reshape(-1))
        if data.size:
            self._buf = np.concatenate([self._buf, data])
        return self._drain_due()

    def _drain_due(self) -> list[DecodeWindow]:
        out: list[DecodeWindow] = []
        window_n = int(self.window_s * self.sample_rate)
        while self._buf.size >= self._next_due_sample and window_n > 0:
            start = max(0, self._next_due_sample - window_n)
            out.append(
                DecodeWindow(
                    audio=self._buf[start:self._next_due_sample].copy(),
                    sample_rate=self.sample_rate,
                    index=self._window_index,
                    start_s=start / float(self.sample_rate),
                    buffered_s=self.buffered_s,
                )
            )
            self._window_index += 1
            self._next_due_sample += int(self.step_s * self.sample_rate)
        return out

    def flush(self) -> DecodeWindow | None:
        """Emit one final window covering the tail (endpoint signal)."""
        if self._buf.size == 0:
            return None
        window_n = int(self.window_s * self.sample_rate)
        start = max(0, self._buf.size - window_n)
        win = DecodeWindow(
            audio=self._buf[start:].copy(),
            sample_rate=self.sample_rate,
            index=self._window_index,
            start_s=start / float(self.sample_rate),
            buffered_s=self.buffered_s,
        )
        self._window_index += 1
        return win

    def reset(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self._next_due_sample = int(self.window_s * self.sample_rate)
        self._window_index = 0
