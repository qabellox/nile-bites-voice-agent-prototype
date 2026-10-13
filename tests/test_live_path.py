"""The live voice path, exercised without a human and without a network.

``LiveVoiceLoop._handle`` is the code a real participant session runs, and until
now nothing tested it: the live loop was only ever reached by opening a
microphone, which no test can do. These tests drive the same handler with a
synthesised clip, so the VAD-derived latency markers, the tool loop, the
condition measure and the session log are all covered by a normal test run.

The ASR and LLM boundaries are substituted. Everything between them is real.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from nbva.agent.live import LiveTurnRequest, LiveVoiceLoop
from nbva.obs.logger import read_session, turns_of


def _clip(path: Path, seconds: float = 1.2, sample_rate: int = 16000) -> Path:
    """A voiced burst inside silence: enough for a timed, logged turn."""
    n = np.zeros(int(sample_rate * seconds), dtype=np.float32)
    seg = (0.2 * np.sin(2 * np.pi * 180 * np.arange(int(sample_rate * 0.6)) / sample_rate)).astype(
        np.float32
    )
    n[int(sample_rate * 0.3): int(sample_rate * 0.3) + len(seg)] = seg * np.hanning(len(seg))
    sf.write(path, n, sample_rate)
    return path


@pytest.fixture
def stub_stack(tmp_path):
    """Config with a stub ASR, a scripted tool plan and a silent TTS.

    The silent TTS is named as a quoted YAML string because the command-line
    setter coerces the literal ``null`` into a Python ``None``, which is not a
    provider name.
    """
    from nbva.config.loader import load_config

    overlay = tmp_path / "live_stub.yaml"
    overlay.write_text(
        "asr:\n  provider: reference_echo\n"
        "llm:\n  provider: scripted\n"
        "  scripted:\n    plan:\n"
        "      - text: ''\n        tool: create_order\n        arguments:\n"
        "          lines:\n"
        "            - item: chicken burger\n              quantity: 2\n"
        "          order_mode: pickup\n"
        "      - text: 'تمام، اتنين تشيكن برجر.'\n"
        "tts:\n  provider: 'null'\n"
        "paths:\n  logs_dir: '" + str(tmp_path / "logs").replace("\\", "/") + "'\n",
        encoding="utf-8",
    )
    return load_config(extra_files=[overlay])


def _run(app, clip_path, sample_rate=16000):
    """Push the clip through the live handler the way a session would."""
    data, rate = sf.read(clip_path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    loop = LiveVoiceLoop(app, speak=False)
    request = LiveTurnRequest(
        audio=np.ascontiguousarray(data, dtype=np.float32),
        speech_start_mono=0.0,
        speech_end_mono=len(data) / float(rate),
    )
    loop._handle(request)
    return loop


# ---------------------------------------------------------------------------
# the path runs
# ---------------------------------------------------------------------------


def test_the_live_handler_processes_a_clip_end_to_end(stub_stack, tmp_path):
    from nbva.agent.app import build_app

    app = build_app(stub_stack, attach_player=False)
    app.asr.set_next("عايز اتنين تشيكن برجر")
    loop = _run(app, _clip(tmp_path / "turn.wav"))

    assert loop.turns_handled == 1
    record = loop.records[0]
    assert record.asr_transcript == "عايز اتنين تشيكن برجر"
    assert "اتنين تشيكن برجر" in record.response_text


def test_the_live_handler_reaches_the_deterministic_tools(stub_stack, tmp_path):
    """A tool call from the live path must land in the real order engine."""
    from nbva.agent.app import build_app

    app = build_app(stub_stack, attach_player=False)
    app.asr.set_next("عايز اتنين تشيكن برجر")
    loop = _run(app, _clip(tmp_path / "turn.wav"))

    names = [c.get("name") for c in loop.records[0].tool_calls]
    assert names == ["create_order"]
    assert not loop.records[0].tool_error_codes


def test_the_live_handler_stamps_the_capture_side_latency(stub_stack, tmp_path):
    from nbva.agent.app import build_app

    app = build_app(stub_stack, attach_player=False)
    app.asr.set_next("عايز اتنين تشيكن برجر")
    loop = _run(app, _clip(tmp_path / "turn.wav", seconds=1.2))
    lat = loop.records[0].latency

    # The capture-side endpoint and duration come from the clip, not from a VAD,
    # so both are exact. The stage spans still have to be present.
    assert lat.utterance_audio_ms == pytest.approx(1200.0, abs=1.0)
    assert lat.speech_duration_ms == pytest.approx(1200.0, abs=1.0)
    assert lat.asr_ms is not None
    assert lat.nlu_ms is not None
    assert lat.tools_ms is not None


def test_the_live_handler_measures_the_condition(stub_stack, tmp_path):
    from nbva.agent.app import build_app

    app = build_app(stub_stack, attach_player=False)
    app.asr.set_next("عايز اتنين تشيكن برجر")
    loop = _run(app, _clip(tmp_path / "turn.wav"))
    record = loop.records[0]

    assert record.measured_condition == "C0"
    assert record.english_pct == 0.0
    assert record.condition_consistent is True


def test_the_live_handler_records_the_vad_state(stub_stack, tmp_path):
    from nbva.agent.app import build_app

    app = build_app(stub_stack, attach_player=False)
    app.asr.set_next("عايز اتنين تشيكن برجر")
    loop = _run(app, _clip(tmp_path / "turn.wav"))
    extra = loop.records[0].extra

    assert extra["latency_endpoint"] == "first_audio_sample_to_device"
    assert "vad_noise_floor" in extra
    assert "vad_calibrated" in extra


def test_a_silent_tts_leaves_the_primary_latency_unset(stub_stack, tmp_path):
    """The locked metric ends at the first audio sample, so silence has none.

    Reporting a number here would be the exact fabrication the latency
    definition exists to prevent.
    """
    from nbva.agent.app import build_app

    app = build_app(stub_stack, attach_player=False)
    app.asr.set_next("عايز اتنين تشيكن برجر")
    loop = _run(app, _clip(tmp_path / "turn.wav"))
    lat = loop.records[0].latency

    assert loop.records[0].response_text
    assert lat.tts_synthesis_ms is None
    assert lat.primary_latency_ms is None


# ---------------------------------------------------------------------------
# the turn is logged
# ---------------------------------------------------------------------------


def test_the_live_handler_writes_a_readable_session(stub_stack, tmp_path):
    from nbva.agent.app import build_app

    app = build_app(stub_stack, attach_player=False)
    app.asr.set_next("عايز اتنين تشيكن برجر")
    loop = _run(app, _clip(tmp_path / "turn.wav"))

    turns = turns_of(read_session(app.logger.path))
    assert len(turns) == 1
    assert turns[0]["asr_transcript"] == "عايز اتنين تشيكن برجر"


def test_the_turn_record_carries_the_consistency_flag(stub_stack, tmp_path):
    """The flag has to survive into the log, not just the in-memory record.

    The analysis reads the log, not the object, so a flag that is only set on
    the dataclass would be invisible where it is needed.
    """
    from nbva.agent.app import build_app

    app = build_app(stub_stack, attach_player=False)
    app.asr.set_next("عايز اتنين تشيكن برجر")
    loop = _run(app, _clip(tmp_path / "turn.wav"))
    assert loop.records[0].condition_consistent is True

    turns = turns_of(read_session(app.logger.path))
    assert turns[0]["condition_consistent"] is True
    assert turns[0]["measured_condition"] == "C0"
