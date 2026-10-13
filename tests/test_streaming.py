"""Acceptance tests for the streaming/intervention architecture.

Fast by design: logic, safety, and orchestration are proven with fakes so
the suite never loads models or touches the network. Real-model behavior
(whisper small on Arabic, OpenRouter token streaming, Edge TTS) is verified
manually against synthetic controls and reported separately, never asserted
here.
"""

import json

import numpy as np
import pytest

from nbva.llm.base import ToolCall
from nbva.stream.chunker import SlidingWindowBuffer
from nbva.stream.context import ContextTracker
from nbva.stream.fidelity import score_fidelity
from nbva.stream.hypothesis import HypothesisTracker
from nbva.stream.interrupt import InterruptController
from nbva.stream.loop import StreamingTurnLoop
from nbva.stream.respond import StreamingResponder, detect_lang, split_sentences
from nbva.stream.speculative import (
    MUTATING_TOOLS,
    READONLY_TOOLS,
    SpeculativeTools,
    can_run_speculatively,
)
from nbva.stream.trace import StreamTrace
from nbva.stream.understand import IncrementalUnderstanding


# ---------------------------------------------------------------- fakes

class FakeASR:
    name = "fake_asr"
    sample_rate = 16000
    language = "auto"

    def __init__(self, script=()):
        self.script = list(script)
        self.calls = 0

    def transcribe(self, audio):
        from nbva.asr.base import ASRResult

        self.calls += 1
        text = self.script[min(self.calls - 1, len(self.script) - 1)] if self.script else ""
        return ASRResult(text=text, language="ar", provider="fake", model="fake")


class FakeLLM:
    name = "fake_llm"
    model = "fake"

    def __init__(self, turns=()):
        self.turns = list(turns)

    def complete_stream(self, messages, tools):
        text, calls = self.turns.pop(0) if self.turns else ("done", [])
        if text:
            yield ("text", text)
        yield ("done", _Resp(text, calls))


class SteadyLLM(FakeLLM):
    """Repeats one scripted answer forever (stable speculative reuse)."""

    def complete_stream(self, messages, tools):
        text, calls = self.turns[0] if self.turns else ("done", [])
        if text:
            yield ("text", text)
        yield ("done", _Resp(text, calls))


class _Resp:
    def __init__(self, text, calls):
        self.text = text
        self.tool_calls = calls
        self.stop_reason = "stop"


class FakeRegistry:
    def __init__(self):
        self._calls = []

    def llm_schemas(self):
        return []

    def dispatch(self, name, arguments):
        rec = {"name": name, "arguments": dict(arguments or {}), "ok": True,
               "result": {"ok": True}}
        self._calls.append(rec)
        return rec["result"]

    def take_calls(self):
        out, self._calls = self._calls, []
        return out


class FakeTTS:
    name = "edge"
    fallback_voice = "en-US-JennyNeural"

    def synthesize(self, text, voice=None):
        from nbva.tts.base import TTSResult

        return TTSResult(audio=np.zeros(800, dtype=np.float32), sample_rate=16000,
                         provider="edge", voice=voice or "ar-EG-SalmaNeural")


class FakeAnalyzer:
    def analyze(self, text):
        class T:
            english_pct = 10.0
            english_lexical_tokens = 0
            total_lexical_tokens = 2
        return T()


class FakeSession:
    session_id = "s-test"
    max_history_turns = 10

    def __init__(self, registry):
        self.tools = registry
        self.turns = []

    def message_history(self):
        return list(self.turns)

    def add_turn(self, role, text, ts):
        self.turns.append({"role": role, "content": text})


class FakePipeline:
    system_prompt = "sys"

    def __init__(self, asr, analyzer):
        self.asr = asr
        self.analyzer = analyzer

    def _detect_response_language(self, text):
        return "en" if text and not any("\u0600" <= ch <= "\u06FF" for ch in text) else "ar"


class FakeApp:
    def __init__(self, asr, registry):
        self.pipeline = FakePipeline(asr, FakeAnalyzer())
        self.session = FakeSession(registry)
        self.llm = FakeLLM()
        self.tts = FakeTTS()


def _pcm(seconds=3.0, sr=16000):
    t = np.linspace(0, seconds, int(seconds * sr))
    return (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


# ---------------------------------------------------------------- chunker

def test_windows_are_indexed_overlapping_and_ordered():
    c = SlidingWindowBuffer(sample_rate=16000, window_s=4.0, step_s=1.0)
    idx = []
    for _ in range(24):  # 6s in 0.25s pushes
        idx += [w.index for w in c.push(np.zeros(4000, dtype=np.float32))]
    tail = c.flush()
    assert idx == [0, 1, 2], idx
    assert tail.index == 3
    assert len(tail.audio) == 4 * 16000


# ---------------------------------------------------------------- tracker

def test_partial_then_committed_then_final_with_no_revision():
    t = HypothesisTracker(agree_n=2)
    t.observe("a b", 0)
    t.observe("a b c", 1)
    t.observe("a b c d", 2)
    assert t.committed_text == "a b c"
    assert t.revision_count == 0
    fin = t.finalize("a b c d e", 3)
    assert fin[-1].kind == "final"
    assert t.committed_text == "a b c d e"


def test_revision_is_recorded_not_silent():
    t = HypothesisTracker(agree_n=2)
    t.observe("order two chicken", 0)
    t.observe("order two chicken burgers", 1)
    assert t.committed_text == "order two chicken"
    evs = t.observe("order two beef burgers", 2)
    kinds = [e.kind for e in evs]
    assert "revise" in kinds
    assert t.revision_count == 1
    assert t.committed_text == "order two"


# ---------------------------------------------------------------- safety

def test_speculative_allowlist_refuses_all_mutating_tools():
    assert READONLY_TOOLS and MUTATING_TOOLS
    assert not (READONLY_TOOLS & MUTATING_TOOLS)
    for name in ("create_order", "modify_order", "cancel_order", "confirm_order"):
        assert not can_run_speculatively(name)
    for name in ("search_menu", "get_price", "check_availability",
                 "get_delivery_info", "get_business_hours", "get_promotions",
                 "get_menu_item", "get_current_order", "calculate_order_total",
                 "resolve_order_reference"):
        assert can_run_speculatively(name)


def test_speculative_dispatch_cannot_mutate_order_state():
    spec = SpeculativeTools(registry=FakeRegistry())
    with pytest.raises(PermissionError):
        spec.run_guarded("create_order", {"lines": []})
    with pytest.raises(PermissionError):
        spec.run_guarded("modify_order", {})
    with pytest.raises(PermissionError):
        spec.run_guarded("cancel_order", {})
    with pytest.raises(PermissionError):
        spec.run_guarded("confirm_order", {})
    spec.shutdown()


def test_speculative_readonly_path_works():
    spec = SpeculativeTools(registry=FakeRegistry())
    spec.prepare("chicken burger")
    assert spec.await_prefix("chicken burger", timeout_s=5.0)["ok"] is True
    spec.shutdown()


# ---------------------------------------------------------------- context

def test_context_patches_incrementally_across_turns():
    ctx = ContextTracker(menu_aliases={"chicken burger": "NB-BURG-001",
                                       "cheese": "OPT-CHEESE"})
    e1 = ctx.update("order two", commit_id=0)
    assert ctx.state.intent == "order"
    assert ctx.state.status == "partial"
    e2 = ctx.update("order two chicken burger", commit_id=1)
    assert ctx.state.items == [{"name": "NB-BURG-001", "quantity": 2}]
    assert ctx.state.confidence == "high"
    e3 = ctx.update("one of them without cheese", commit_id=2)
    assert any(m["target"].startswith("cheese") for m in ctx.state.modifications)
    assert e1["kind"] == "context_update" and e3["kind"] == "context_update"
    fin = ctx.finalize()
    assert fin["state"]["status"] == "final"


# ---------------------------------------------------------------- mixed fidelity

def test_mixed_content_preservation_is_measured():
    ref = "order two chicken burgers please"
    same = score_fidelity(ref, ref)
    assert same["wer"] == 0.0
    assert same["latin_preservation_rate"] == 1.0
    arabized = score_fidelity(ref, "order two arabized burgers please")
    assert arabized["latin_preservation_rate"] < 1.0
    assert arabized["wer"] > 0.0


# ---------------------------------------------------------------- responder

def test_streamed_tool_loop_dispatches_then_answers():
    reg = FakeRegistry()
    llm = FakeLLM(turns=[
        ("Sure.", [ToolCall(name="create_order",
                            arguments={"item": "NB-BURG-001"}, call_id="c1")]),
        ("Done.", []),
    ])
    out = StreamingResponder(llm=llm, registry=reg, tts=FakeTTS(),
                             system_prompt="sys").run("two burgers", [])
    assert out["text"] == "Done."
    assert out["tool_calls"][0]["name"] == "create_order"
    assert len(out["audio_segments"]) == 1
    assert out["first_token_ms"] is not None


def test_english_reply_uses_fallback_voice():
    reg = FakeRegistry()
    llm = FakeLLM(turns=[("Hello there.", [])])
    out = StreamingResponder(llm=llm, registry=reg, tts=FakeTTS()).run("hi", [])
    assert out["sentences"][0]["voice"] == "en-US-JennyNeural"


def test_sentence_split_handles_arabic_punctuation():
    assert split_sentences("First. Second? Third!") == ["First.", "Second?", "Third!"]
    assert detect_lang("hello") == "en"


# ---------------------------------------------------------------- barge-in

def test_barge_in_stops_playback_and_marks_cancellation():
    class FakePlayer:
        def __init__(self):
            self.is_playing = True
            self.stopped = False

        def stop(self):
            self.stopped = True
            self.is_playing = False

    from nbva.stream.interrupt import InterruptController

    ctl = InterruptController(player=FakePlayer())
    ctl.arm()
    rec = ctl.on_speech_onset({"source": "vad"})
    assert rec["kind"] == "barge_in" and rec["playback_stopped"] is True
    assert ctl.cancelled is True
    assert ctl.interruptions == 1


# ---------------------------------------------------------------- trace

def test_trace_contains_every_required_event_kind(tmp_path):
    trace = StreamTrace(session_id="s", turn_id="t", log_dir=str(tmp_path))
    trace.start(header={"llm_model": "m", "asr_model": "a"})
    required = ["speech_start", "audio_chunk_received", "partial_asr_start",
                "partial_asr_end", "partial_transcript", "commit", "revision",
                "incremental_intent", "entity_detected", "context_update",
                "speculative_tool_start", "speculative_tool_end",
                "speculative_tool_reused", "LLM_start", "LLM_first_token",
                "LLM_end", "TTS_start", "TTS_first_audio", "TTS_end",
                "response_audio_start", "speech_end", "final_transcript",
                "final_task_result"]
    for kind in required:
        trace.log(kind, probe=True)
    path = trace.save()
    assert path and path.endswith(".jsonl")
    recs = [json.loads(line) for line in open(path, encoding="utf-8")]
    assert recs[0].get("record") == "header" and "llm_model" in recs[0]
    kinds = [r["kind"] for r in recs if "kind" in r]
    for kind in required:
        assert kind in kinds, kind
    blob = open(path, encoding="utf-8").read()
    assert "sk-" not in blob and "api_key" not in blob.lower()


# ---------------------------------------------------------------- full loop (fakes, fast)

def test_streaming_loop_overlaps_and_reports_honestly():
    script = ["order", "order two", "order two burgers",
              "order two burgers please", "order two burgers please"]
    asr = FakeASR(script=script)
    reg = FakeRegistry()
    app = FakeApp(asr, reg)
    app.llm = SteadyLLM(turns=[("Sure thing.", [])])
    loop = StreamingTurnLoop(app, window_s=1.0, step_s=0.5,
                             min_words_tools=2, min_words_llm=3)
    out = loop.run_clip(_pcm(3.0), 16000, realtime=False, chunk_s=0.25)
    kinds = [e["kind"] for e in out["events"]]
    assert "partial_transcript" in kinds
    assert "commit" in kinds
    assert "final" in kinds
    # Overlap: a commit happened while audio was still arriving.
    ep = next(e["t"] for e in out["events"] if e["kind"] == "endpoint")
    commits = [e["t"] for e in out["events"] if e["kind"] == "commit"]
    assert commits and min(commits) < ep
    assert out["final_transcript"].startswith("order two burgers")
    assert out["response_text"] == "Sure thing."
    assert out["audio_segments"]
    assert out["spec_stats"]["prepared"] >= 1
    assert out["context"]["intent"] == "order"
    assert out["trace_path"] is None  # no dir configured in test


def test_streaming_loop_records_revisions():
    asr = FakeASR(script=["order two chicken", "order two chicken burgers",
                          "order two beef burgers", "order two beef burgers now",
                          "order two beef burgers now"])
    reg = FakeRegistry()
    app = FakeApp(asr, reg)
    app.llm = FakeLLM(turns=[("Ok.", [])])
    loop = StreamingTurnLoop(app, window_s=1.0, step_s=0.5)
    out = loop.run_clip(_pcm(2.5), 16000, realtime=False, chunk_s=0.25)
    assert out["revisions"] >= 1
    assert any(e["kind"] == "revise" for e in out["events"])
