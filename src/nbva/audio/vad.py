"""Energy-based voice activity detection and endpointing.

The moment the VAD decides the user has finished speaking is the instrument that
measures "end of the user's speech", which is the start of the LOCKED primary
latency metric. That makes the parameters below part of the method, not tuning
knobs: they must be identical across every condition, and they are written into
the run manifest.

The detector is intentionally simple and dependency-free so that its decision is
reproducible and auditable. A neural VAD could be swapped in behind
:func:`build_vad` if the researcher decides to.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from time import perf_counter as _perf_counter
from typing import Any

import numpy as np


def _mono_now() -> float:
    """Monotonic clock used for every timing decision in the audio path."""
    return _perf_counter()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VadState(str, Enum):
    IDLE = "idle"
    SPEECH = "speech"
    SILENCE_AFTER_SPEECH = "silence_after_speech"


@dataclass
class VadConfig:
    start_threshold: float = 0.012
    end_silence_ms: int = 700
    min_speech_ms: int = 250
    max_utterance_ms: int = 15000
    tail_pad_ms: int = 250
    noise_floor_rms: float = 0.004
    auto_calibrate: bool = True
    auto_calibrate_ms: int = 1200

    @classmethod
    def from_config(cls, config) -> "VadConfig":
        section = config.section("audio.vad") or {}
        return cls(
            start_threshold=float(section.get("start_threshold", 0.012)),
            end_silence_ms=int(section.get("end_silence_ms", 700)),
            min_speech_ms=int(section.get("min_speech_ms", 250)),
            max_utterance_ms=int(section.get("max_utterance_ms", 15000)),
            tail_pad_ms=int(section.get("tail_pad_ms", 250)),
            noise_floor_rms=float(section.get("noise_floor_rms", 0.004)),
            auto_calibrate=bool(section.get("auto_calibrate", True)),
            auto_calibrate_ms=int(section.get("auto_calibrate_ms", 1200)),
        )


class EnergyVad:
    """Frame-wise energy VAD with hangover, min-speech, and max-utterance guard."""

    def __init__(self, config: VadConfig, sample_rate: int, block_ms: int = 20):
        self.cfg = config
        self.sample_rate = sample_rate
        self.block_ms = block_ms
        self.block_samples = max(1, int(sample_rate * block_ms / 1000))
        self.state = VadState.IDLE

        self._speech_frames = 0
        self._silence_frames = 0
        self._total_frames = 0
        self._calibrating = config.auto_calibrate
        self._calib_frames = max(1, int(config.auto_calibrate_ms / block_ms))
        self._calib_accum = 0.0
        self._measured_floor: float | None = None
        self._calibration_aborted = False

        self._buffer: list[np.ndarray] = []
        self._pre_roll_frames = max(1, int(300 / block_ms))
        self._pre_roll: list[np.ndarray] = []
        # Monotonic stamp of the frame on which the IDLE -> SPEECH transition
        # happened, plus how many frames of the returned utterance are
        # pre-roll. Together they locate real speech start inside the
        # utterance, which the latency metric needs but the endpoint alone
        # cannot supply.
        self._speech_start_mono: float | None = None
        self._speech_start_utc: str | None = None
        self._utterance_speech_start_mono: float | None = None
        self._utterance_lead_frames: int = 0

    @property
    def utterance_speech_start_mono(self) -> float | None:
        """Monotonic stamp of real speech onset for the last finished utterance."""
        return self._utterance_speech_start_mono

    @property
    def utterance_speech_start_utc(self) -> str | None:
        return self._utterance_speech_start_utc

    @property
    def utterance_lead_ms(self) -> float:
        """How much of the returned utterance was pre-roll silence."""
        return round(self._utterance_lead_frames * self.block_ms, 3)

    # -- calibration -------------------------------------------------------
    def effective_threshold(self) -> float:
        floor = self._measured_floor
        if floor is None:
            return max(self.cfg.start_threshold, self.cfg.noise_floor_rms)
        # Sit above the measured room tone but never below the configured floor.
        return max(self.cfg.start_threshold, floor * 2.5, self.cfg.noise_floor_rms)

    def _update_calibration(self, rms: float) -> None:
        # If the room is already louder than the configured speech threshold,
        # this window is not a noise floor. Continuing would raise the
        # threshold to 2.5x speech and silence the detector for the rest of the
        # session, so calibration is abandoned and the configured values stand.
        if rms >= self.cfg.start_threshold:
            self._calibrating = False
            self._calibration_aborted = True
            return
        self._calib_accum += rms
        self._total_frames += 1
        if self._calib_frames and self._total_frames >= self._calib_frames:
            self._measured_floor = self._calib_accum / self._total_frames
            self._calibrating = False

    # -- main step ---------------------------------------------------------
    def step(self, frame: np.ndarray) -> bool:
        """Feed one block. Returns True when an utterance has been completed.

        When True, :meth:`take_utterance` returns the buffered audio, including
        the tail pad.
        """
        frame = np.asarray(frame, dtype=np.float32).reshape(-1)
        rms = float(np.sqrt(np.mean(np.square(frame)))) if frame.size else 0.0

        if self._calibrating:
            self._update_calibration(rms)
            return False

        threshold = self.effective_threshold()
        is_speech = rms >= threshold

        if self.state is VadState.IDLE:
            self._pre_roll.append(frame)
            if len(self._pre_roll) > self._pre_roll_frames:
                self._pre_roll.pop(0)
            if is_speech:
                self._buffer = list(self._pre_roll)
                # Everything before the final frame is pre-roll, so that is
                # exactly how far into the utterance speech really begins.
                self._utterance_lead_frames = max(0, len(self._buffer) - 1)
                self._speech_start_mono = _mono_now()
                self._speech_start_utc = _utc_now_iso()
                self._pre_roll.clear()
                self._speech_frames = 1
                self._silence_frames = 0
                self.state = VadState.SPEECH
            return False

        # in speech or hangover
        self._buffer.append(frame)

        if is_speech:
            self._speech_frames += 1
            self._silence_frames = 0
        else:
            self._silence_frames += 1

        speech_ms = self._speech_frames * self.block_ms
        silence_ms = self._silence_frames * self.block_ms
        total_ms = len(self._buffer) * self.block_ms

        if speech_ms < self.cfg.min_speech_ms:
            # Too short to be an utterance: discard and go back to idle.
            if silence_ms >= self.cfg.end_silence_ms:
                self._reset()
            return False

        if silence_ms >= self.cfg.end_silence_ms:
            return self._finish()
        if total_ms >= self.cfg.max_utterance_ms:
            return self._finish()
        return False

    def _finish(self) -> bool:
        self.state = VadState.IDLE
        self._speech_frames = 0
        self._silence_frames = 0
        # Hand the onset stamps to the utterance so the caller can subtract the
        # pre-roll when it reconstructs wall-clock timing.
        self._utterance_speech_start_mono = self._speech_start_mono
        self._utterance_speech_start_utc = self._speech_start_utc
        return True

    def _reset(self) -> None:
        self.state = VadState.IDLE
        self._buffer.clear()
        self._pre_roll.clear()
        self._speech_frames = 0
        self._silence_frames = 0
        self._speech_start_mono = None
        self._speech_start_utc = None
        self._utterance_lead_frames = 0

    # -- output -------------------------------------------------------------
    def take_utterance(self) -> np.ndarray:
        """Return the completed utterance, trimming leading silence."""
        if not self._buffer:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(self._buffer)
        self._buffer = []
        self._reset()
        if audio.size:
            peak = float(np.max(np.abs(audio)))
            if peak > 0:
                audio = audio / peak * 0.95
        return trim_leading_silence(
            audio, self.effective_threshold(), sample_rate=self.sample_rate
        )

    def flush(self) -> np.ndarray:
        """Force-finish the current utterance, e.g. on quit."""
        if self._buffer:
            audio = np.concatenate(self._buffer)
            self._reset()
            return audio
        return np.zeros(0, dtype=np.float32)

    @property
    def calibrated(self) -> bool:
        return not self._calibrating

    @property
    def measured_noise_floor(self) -> float | None:
        return self._measured_floor

    def describe(self) -> dict[str, Any]:
        return {
            "algorithm": "energy",
            "sample_rate": self.sample_rate,
            "block_ms": self.block_ms,
            "start_threshold": self.cfg.start_threshold,
            "end_silence_ms": self.cfg.end_silence_ms,
            "min_speech_ms": self.cfg.min_speech_ms,
            "max_utterance_ms": self.cfg.max_utterance_ms,
            "tail_pad_ms": self.cfg.tail_pad_ms,
            "noise_floor_rms": self.cfg.noise_floor_rms,
            "auto_calibrate": self.cfg.auto_calibrate,
            "measured_noise_floor": self._measured_floor,
            "calibration_aborted": self._calibration_aborted,
            "effective_threshold": self.effective_threshold(),
        }


def trim_leading_silence(audio: np.ndarray, threshold: float, max_trim_ms: int = 400,
                         sample_rate: int = 16000) -> np.ndarray:
    """Drop leading silence so the ASR sees the utterance, not the room."""
    if audio.size == 0:
        return audio
    win = max(1, int(sample_rate * 0.01))
    limit = min(audio.size, int(sample_rate * max_trim_ms / 1000))
    idx = 0
    while idx + win <= limit:
        chunk = audio[idx : idx + win]
        rms = float(np.sqrt(np.mean(np.square(chunk))))
        if rms >= threshold:
            break
        idx += win
    if idx >= audio.size:
        # Nothing in the buffer crossed the threshold. Returning an empty array
        # would hand the ASR a zero-length input and turn a quiet recording into
        # a confusing pipeline error, so the buffer is returned untouched.
        return audio
    return audio[idx:]


def build_vad(config, sample_rate: int | None = None, block_ms: int | None = None) -> EnergyVad:
    vad_cfg = VadConfig.from_config(config)
    input_cfg = config.section("audio.input") or {}
    sr = int(sample_rate or input_cfg.get("sample_rate", 16000))
    bm = int(block_ms or input_cfg.get("block_ms", 20))
    return EnergyVad(vad_cfg, sr, bm)
