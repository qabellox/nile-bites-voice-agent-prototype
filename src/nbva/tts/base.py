"""Text-to-speech adapter interface.

TTS quality is explicitly out of scope as an outcome, but TTS synthesis time sits
inside the primary latency definition, so the provider and voice are controlled
variables and are recorded in every trial.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np


@dataclass
class TTSResult:
    audio: np.ndarray | None
    sample_rate: int
    provider: str = ""
    voice: str = ""
    duration_ms: float | None = None
    audio_path: str | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


class TTSProvider(Protocol):
    name: str

    def synthesize(self, text: str, voice: str | None = None) -> TTSResult:
        ...

    def describe(self) -> dict[str, Any]:
        ...


def _empty(provider: str, voice: str, message: str) -> TTSResult:
    return TTSResult(audio=None, sample_rate=0, provider=provider, voice=voice, error=message)


def load_audio_file(path: str | Path) -> tuple[np.ndarray, int]:
    """Read any format libsndfile understands (wav, mp3, flac, ogg)."""
    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return np.ascontiguousarray(data, dtype=np.float32), int(sr)
