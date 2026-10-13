"""Speech recognition adapter interface.

The pipeline depends only on :class:`ASRProvider`, so the recogniser can be
replaced without touching anything else. ASR model and parameters are controlled
variables in the methodology, so :meth:`ASRProvider.describe` is written into
every trial record: a result can always be traced back to the exact recogniser
that produced it.
"""

from __future__ import annotations

import hashlib
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np


@dataclass
class ASRResult:
    text: str
    language: str | None = None
    provider: str = ""
    model: str = ""
    # Whatever the provider can say about which weights produced this text.
    # Recorded separately from ``model`` because a name can be repointed.
    model_version: str | None = None
    duration_ms: float | None = None
    raw: Any = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


class ASRProvider(Protocol):
    name: str
    model: str
    sample_rate: int

    def transcribe(self, audio: np.ndarray, sample_rate: int | None = None) -> ASRResult:
        ...

    def describe(self) -> dict[str, Any]:
        ...


def write_wav(path: str | Path, audio: np.ndarray, sample_rate: int) -> Path:
    """Persist captured audio so ASR can be re-scored later without re-recording."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = np.asarray(audio)
    if data.dtype != np.int16:
        data = np.clip(data, -1.0, 1.0)
        data = (data * 32767.0).astype(np.int16)
    with wave.open(str(p), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(data.tobytes())
    return p


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}.get(width, np.int16)
    data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if width == 1:
        data = (data - 128) / 128.0
    elif width == 2:
        data = data / 32768.0
    elif width == 4:
        data = data / 2147483648.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, sr


def audio_fingerprint(audio: np.ndarray) -> str:
    """Cheap content hash of the utterance, so logs can prove which audio ran."""
    arr = np.asarray(audio, dtype=np.float32)
    return hashlib.sha256(arr.tobytes()).hexdigest()[:16]


def resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear resampling. Adequate for speech at 16k/44.1k/48k and dependency-free."""
    if src_rate == dst_rate or audio.size == 0:
        return audio
    n_out = int(round(audio.size * dst_rate / src_rate))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    src_idx = np.linspace(0.0, audio.size - 1, num=n_out)
    return np.interp(src_idx, np.arange(audio.size), audio).astype(np.float32)
