"""The real-time voice conversation loop.

    researcher speaks
    -> microphone captures and endpoint the utterance
    -> pipeline processes it (ASR -> LLM -> tools -> text)
    -> TTS synthesises
    -> audio plays through the speakers
    -> researcher continues

The user never types, never presses an STT button, and never copies text
anywhere. That is a hard requirement of the target experience.

Two measurement details are handled carefully here:

* The latency clock starts at the VAD endpoint (``speech_end``), not when the
  handler thread happens to wake up, so scheduling jitter does not inflate it.
* The microphone is paused during playback so the agent cannot transcribe
  itself. Without this, every trial gets contaminated by the agent's own voice
  and the ASR error rate becomes meaningless.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..audio.io import AudioPlayer, MicrophoneCapture
from ..audio.vad import build_vad
from ..obs.events import LatencyMarkers, TurnRecord, utc_now_iso
from ..research.taskdefs import evaluate_task
from .app import AgentApp


@dataclass
class LiveTurnRequest:
    audio: np.ndarray
    speech_start_mono: float
    speech_end_mono: float


class LiveVoiceLoop:
    """Owns the microphone, the turn queue, and the turn worker."""

    def __init__(self, app: AgentApp, speak: bool = True, max_session_seconds: float = 0.0):
        self.app = app
        self.speak = speak
        self.max_session_seconds = max_session_seconds

        cfg = app.config
        in_cfg = cfg.section("audio.input") or {}
        play_cfg = (cfg.section("tts") or {}).get("playback") or {}
        self.mute_mic_during_playback = bool(play_cfg.get("mute_mic_during_playback", True))
        self.wait_for_completion = bool(play_cfg.get("wait_for_completion", True))

        self.sample_rate = int(in_cfg.get("sample_rate", 16000))
        self.block_ms = int(in_cfg.get("block_ms", 20))
        self.vad = build_vad(cfg, self.sample_rate, self.block_ms)

        self._utterances: "queue.Queue[LiveTurnRequest]" = queue.Queue()
        self._pending_start: float | None = None
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self.mic: MicrophoneCapture | None = None
        self.turns_handled = 0
        self.records: list[TurnRecord] = []

    # -- capture side -----------------------------------------------------
    def _on_utterance(self, audio: np.ndarray) -> None:
        """Called from the PortAudio callback thread. Must stay fast."""
        now = time.monotonic()
        # Prefer the VAD's own IDLE -> SPEECH stamp. Falling back to "now" would
        # report a near-zero speech duration, because the callback only runs once
        # the utterance is already complete.
        start = self.vad.utterance_speech_start_mono
        if start is None or start > now:
            start = self._pending_start if self._pending_start is not None else now
        self._pending_start = None
        self._utterances.put(LiveTurnRequest(audio=audio, speech_start_mono=start, speech_end_mono=now))

    def _on_speech_start(self) -> None:
        # Recorded at the first speech frame so speech duration is real.
        if self._pending_start is None:
            self._pending_start = time.monotonic()

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        in_cfg = self.app.config.section("audio.input") or {}
        self.mic = MicrophoneCapture(
            sample_rate=self.sample_rate,
            channels=int(in_cfg.get("channels", 1)),
            block_ms=self.block_ms,
            device=in_cfg.get("device"),
            vad=self.vad,
            on_utterance=self._on_utterance,
        )
        self.mic.start()
        self._worker = threading.Thread(target=self._run_worker, name="nbva-turns", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        if self.mic is not None:
            self.mic.stop()
        if self._worker is not None:
            self._worker.join(timeout=5.0)

    def wait_for_idle(self, timeout: float = 120.0) -> bool:
        """Block until the queue drains and playback finishes."""
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if self._utterances.empty() and not self._is_playing():
                return True
            time.sleep(0.05)
        return False

    def _is_playing(self) -> bool:
        return bool(self.app.player and self.app.player.is_playing)

    # -- worker -----------------------------------------------------------
    def _run_worker(self) -> None:
        while not self._stop.is_set():
            try:
                request = self._utterances.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._handle(request)
            except Exception as exc:  # never let one turn kill the session
                self.app.logger.error(f"turn failed: {type(exc).__name__}: {exc}")
            finally:
                self._utterances.task_done()

    def _handle(self, request: LiveTurnRequest) -> None:
        app = self.app
        session = app.session

        # The VAD already decided where speech ended. Rebuild the marker from
        # that instant so the primary latency metric is not inflated by queueing.
        lat = LatencyMarkers()
        lat.speech_start_mono = request.speech_start_mono
        lat.speech_end_mono = request.speech_end_mono
        lat.speech_start_utc = utc_now_iso()
        lat.speech_end_utc = utc_now_iso()
        lat.speech_duration_ms = round(
            (request.speech_end_mono - request.speech_start_mono) * 1000.0, 3
        )
        lat.utterance_audio_ms = round(
            len(request.audio) / float(self.sample_rate) * 1000.0, 3
        )

        def on_first_sample() -> None:
            lat.stamp_response_audio_start()

        if self.speak and app.player is not None and self.mute_mic_during_playback:
            if self.mic is not None:
                self.mic.pause()
        try:
            result = app.pipeline.process_turn(
                audio=request.audio,
                reference_transcript=None,
                speak=self.speak,
                on_audio_start=on_first_sample,
            )
        finally:
            if self.speak and app.player is not None and self.mute_mic_during_playback:
                if self.mic is not None:
                    self.mic.resume()

        record = result.turn
        # Keep the capture-side endpoint and duration, but adopt the pipeline's
        # stage timings, which were measured inside the pipeline.
        stage = result.turn.latency
        lat.asr_start_mono, lat.asr_end_mono = stage.asr_start_mono, stage.asr_end_mono
        lat.nlu_start_mono, lat.nlu_end_mono = stage.nlu_start_mono, stage.nlu_end_mono
        lat.tools_start_mono, lat.tools_end_mono = stage.tools_start_mono, stage.tools_end_mono
        lat.response_text_ready_mono = stage.response_text_ready_mono
        lat.tts_synthesis_start_mono = stage.tts_synthesis_start_mono
        lat.tts_synthesis_end_mono = stage.tts_synthesis_end_mono
        lat.asr_ms = stage.asr_ms
        lat.nlu_ms = stage.nlu_ms
        lat.tools_ms = stage.tools_ms
        lat.response_generation_ms = stage.response_generation_ms
        lat.tts_synthesis_ms = stage.tts_synthesis_ms
        lat.response_audio_end_mono = stage.response_audio_end_mono
        record.latency = lat

        record.extra["vad_noise_floor"] = self.vad.measured_noise_floor
        record.extra["vad_calibrated"] = self.vad.calibrated
        record.extra["latency_endpoint"] = "first_audio_sample_to_device"

        app.logger.log_turn(record)
        self.records.append(record)
        self.turns_handled += 1

    # -- convenience for scripted use --------------------------------------
    def say_and_process(self, audio: np.ndarray, **kwargs: Any) -> Any:
        """Process one utterance synchronously. Used by the CLI and tests."""
        return self.app.pipeline.process_turn(audio=audio, speak=self.speak, **kwargs)
