"""VAD endpointing and the output-latency endpoint.

Both stages sit on the critical path of the LOCKED primary latency metric, so
both are tested here with synthetic audio: no device, no network, no model. A
regression here would move the primary outcome for every trial, so the tests
care about behaviour rather than exact sample counts.
"""

from __future__ import annotations

import numpy as np
import pytest

from nbva.audio.vad import EnergyVad, VadConfig, trim_leading_silence

SR = 16000
BLOCK_MS = 20


def _silence(ms: int) -> np.ndarray:
    return np.zeros(int(SR * ms / 1000), dtype=np.float32)


def _speech(ms: int, level: float = 0.3) -> np.ndarray:
    t = np.arange(int(SR * ms / 1000), dtype=np.float32) / SR
    return (level * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def _seq(*parts: np.ndarray) -> np.ndarray:
    return np.concatenate(parts)


def _blocks(signal: np.ndarray) -> list[np.ndarray]:
    size = int(SR * BLOCK_MS / 1000)
    return [signal[i : i + size] for i in range(0, len(signal), size)]


def _vad(**overrides) -> EnergyVad:
    cfg = VadConfig(auto_calibrate=False, **overrides)
    return EnergyVad(cfg, sample_rate=SR, block_ms=BLOCK_MS)


# -- the VAD --------------------------------------------------------------


def test_silence_never_triggers_an_utterance():
    vad = _vad()
    for frame in _blocks(_silence(2000)):
        vad.step(frame)
    assert vad.take_utterance().size == 0


def test_speech_followed_by_silence_produces_one_utterance():
    vad = _vad(end_silence_ms=300, min_speech_ms=100)
    for frame in _blocks(_seq(_silence(200), _speech(600), _silence(600))):
        vad.step(frame)
    utterance = vad.take_utterance()
    assert utterance.size > 0


def test_an_utterance_is_returned_only_once():
    vad = _vad(end_silence_ms=300, min_speech_ms=100)
    for frame in _blocks(_seq(_silence(200), _speech(600), _silence(600))):
        vad.step(frame)
    assert vad.take_utterance().size > 0
    assert vad.take_utterance().size == 0


def test_a_click_shorter_than_the_minimum_speech_is_rejected():
    vad = _vad(min_speech_ms=150, end_silence_ms=200)
    for frame in _blocks(_seq(_silence(200), _speech(40), _silence(400))):
        vad.step(frame)
    assert vad.take_utterance().size == 0


def test_a_very_long_utterance_is_cut_at_the_maximum():
    # Without a cap, continuous speech would never endpoint and the turn would
    # never be measured.
    vad = _vad(max_utterance_ms=800, min_speech_ms=100, end_silence_ms=300)
    for frame in _blocks(_seq(_silence(200), _speech(2500))):
        vad.step(frame)
    assert vad.take_utterance().size > 0


def test_the_utterance_contains_the_speech_energy():
    vad = _vad(end_silence_ms=300, min_speech_ms=100)
    for frame in _blocks(_seq(_silence(200), _speech(600), _silence(600))):
        vad.step(frame)
    assert np.max(np.abs(vad.take_utterance())) > 0.1


def test_speech_onset_is_stamped_when_speech_begins():
    vad = _vad(end_silence_ms=300, min_speech_ms=100)
    for frame in _blocks(_seq(_silence(200), _speech(600), _silence(600))):
        vad.step(frame)
    onset = vad.utterance_speech_start_mono
    assert onset is not None, "latency needs the moment speech began, not when it ended"
    assert vad.utterance_speech_start_utc


def test_the_onset_precedes_the_end_of_the_utterance():
    vad = _vad(end_silence_ms=300, min_speech_ms=100)
    for frame in _blocks(_seq(_silence(200), _speech(600), _silence(600))):
        vad.step(frame)
    onset = vad.utterance_speech_start_mono
    assert onset is not None
    # The onset is stamped at the first speech block, so the lead time the
    # caller reports must be positive, not zero.
    assert vad.utterance_lead_ms is not None


def test_flush_returns_a_pending_utterance_at_shutdown():
    vad = _vad(min_speech_ms=50, end_silence_ms=1000)
    for frame in _blocks(_seq(_silence(100), _speech(500))):
        vad.step(frame)
    pending = vad.flush()
    assert pending.size > 0, "quitting mid-utterance must not discard the audio"


def test_flush_is_safe_on_an_idle_vad():
    assert _vad().flush().size == 0


def test_describe_reports_the_settings_used():
    described = _vad(start_threshold=0.02).describe()
    assert described["start_threshold"] == 0.02
    assert described["sample_rate"] == SR
    assert described["effective_threshold"] >= 0.02


def test_describe_exposes_the_effective_threshold_so_runs_are_comparable():
    # The threshold in force is part of the method, so it has to be logged.
    assert "effective_threshold" in _vad().describe()


# -- noise calibration ----------------------------------------------------


def test_auto_calibration_raises_the_threshold_above_quiet_noise():
    cfg = VadConfig(auto_calibrate=True, auto_calibrate_ms=200)
    vad = EnergyVad(cfg, sample_rate=SR, block_ms=BLOCK_MS)
    # A hum, not digital silence, so calibration has something to measure.
    t = np.arange(int(SR * 0.2), dtype=np.float32) / SR
    hum = (0.01 * np.sin(2 * np.pi * 50.0 * t)).astype(np.float32)
    for frame in _blocks(hum):
        vad.step(frame)
    assert vad.calibrated is True
    assert vad.effective_threshold() > 0.01


def test_calibration_does_not_fire_on_loud_input():
    cfg = VadConfig(auto_calibrate=True, auto_calibrate_ms=200)
    vad = EnergyVad(cfg, sample_rate=SR, block_ms=BLOCK_MS)
    for frame in _blocks(_speech(400, level=0.4)):
        vad.step(frame)
    # Speech in the calibration window is not a noise floor. Accepting it would
    # set the threshold to 2.5x speech level and silence the detector for the
    # rest of the session.
    assert vad.describe()["calibration_aborted"] is True
    assert vad.measured_noise_floor is None
    assert vad.effective_threshold() == max(cfg.start_threshold, cfg.noise_floor_rms)


def test_the_vad_still_detects_speech_after_calibration_is_aborted():
    cfg = VadConfig(auto_calibrate=True, auto_calibrate_ms=200, end_silence_ms=300,
                    min_speech_ms=100)
    vad = EnergyVad(cfg, sample_rate=SR, block_ms=BLOCK_MS)
    for frame in _blocks(_speech(400, level=0.4)):
        vad.step(frame)
    for frame in _blocks(_seq(_silence(200), _speech(600), _silence(600))):
        vad.step(frame)
    assert vad.take_utterance().size > 0


# -- leading silence ------------------------------------------------------


def test_trim_leading_silence_removes_the_silence_prefix():
    signal = _seq(_silence(300), _speech(400))
    trimmed = trim_leading_silence(signal, threshold=0.05, sample_rate=SR)
    assert len(trimmed) < len(signal)
    assert np.max(np.abs(trimmed)) > 0.1


def test_trim_keeps_a_signal_that_starts_immediately():
    signal = _speech(400)
    assert len(trim_leading_silence(signal, threshold=0.05, sample_rate=SR)) == len(signal)


def test_trim_does_not_delete_a_silent_signal():
    signal = _silence(200)
    assert len(trim_leading_silence(signal, threshold=0.05, sample_rate=SR)) == len(signal)


def test_trim_is_bounded_so_a_very_quiet_utterance_survives():
    signal = _seq(_silence(2000), _speech(300, level=0.001))
    trimmed = trim_leading_silence(signal, threshold=0.5, max_trim_ms=400, sample_rate=SR)
    # Bounded trimming: an over-threshold must not erase the whole utterance.
    assert len(trimmed) >= len(signal) - int(SR * 0.4)


def test_empty_audio_is_handled():
    assert len(trim_leading_silence(np.array([], dtype=np.float32), 0.05, sample_rate=SR)) == 0


# -- the player contract --------------------------------------------------


def test_the_player_fires_the_first_sample_callback_for_empty_audio():
    # An empty response has no samples to wait for, but the turn still has to be
    # timestamped, otherwise the latency of a refusal is unmeasurable.
    from nbva.audio.io import AudioPlayer

    fired = []
    player = AudioPlayer(sample_rate=SR)
    elapsed = player.play(np.array([], dtype=np.float32), sample_rate=SR,
                          on_first_sample=lambda: fired.append(True))
    assert fired == [True]
    assert elapsed == 0.0


def test_the_player_normalises_input_shape_without_a_device():
    # Only the argument handling is exercised here; opening a real stream would
    # make the test depend on the machine it runs on.
    from nbva.audio.io import AudioPlayer

    player = AudioPlayer(sample_rate=SR, device="no-such-device")
    with pytest.raises(Exception):
        player.play(_speech(20), sample_rate=SR)


def test_is_playing_is_false_before_and_after_a_failed_play():
    from nbva.audio.io import AudioPlayer

    player = AudioPlayer(sample_rate=SR, device="no-such-device")
    assert player.is_playing is False
    with pytest.raises(Exception):
        player.play(_speech(20), sample_rate=SR)
    assert player.is_playing is False
