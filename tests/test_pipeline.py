"""Scripted end-to-end runs of the whole turn.

The ASR and TTS boundaries are substituted; the code-switching measure, the tool
loop, the order state, the grounding check, the condition evaluation, the latency
markers and the log writer are all the real implementations. That is the point of
these tests: they prove the instrument produces a usable research record without a
model, a network, or an audio device, and they pin what a turn record must
contain before anybody collects data.
"""

from __future__ import annotations

import numpy as np
import pytest

from nbva.agent.app import build_app
from nbva.agent.pipeline import BaselinePipeline
from nbva.agent.prompts import load_system_prompt
from nbva.llm.providers import LLMResponse, ToolCall
from nbva.obs.events import TurnRecord
from nbva.obs.logger import read_session, turns_of
from nbva.research.codeswitch import build_analyzer
from nbva.research.conditions import load_conditions
from nbva.research.wer import wer_config_from_config
from nbva.tts.providers import NullTTSProvider, TTSResult


# -- doubles for the two boundaries a deployment is allowed to vary -------


class ScriptedLLM:
    """Replays a fixed list of responses, recording what it was asked.

    Not a mock of the pipeline: it is a stand-in for a model that happens to have
    already decided its answers. The tool loop, argument validation and state
    transitions are therefore exercised for real.
    """

    name = "scripted_test"

    def __init__(self, responses, model: str = "scripted-test-model"):
        self.model = model
        self._responses = list(responses)
        self._index = 0
        self.seen_messages: list[list[dict]] = []
        self.seen_tools: list = []

    def complete(self, messages, tools=None):
        self.seen_messages.append(list(messages))
        self.seen_tools.append(tools)
        if self._index < len(self._responses):
            reply = self._responses[self._index]
        else:
            reply = LLMResponse(text="تمام.", tool_calls=[], stop_reason="stop")
        self._index += 1
        return reply

    def describe(self):
        return {"provider": self.name, "model": self.model}


class ScriptedTTS:
    """Returns a fixed tone so the audio path runs without a voice service."""

    name = "scripted_tts"

    def __init__(self, sample_rate: int = 24000, ms: int = 300):
        self.sample_rate = sample_rate
        self.ms = ms
        self.calls: list[str] = []

    def synthesize(self, text: str) -> TTSResult:
        self.calls.append(text)
        n = int(self.sample_rate * self.ms / 1000)
        t = np.arange(n, dtype=np.float32) / self.sample_rate
        return TTSResult(
            audio=(0.2 * np.sin(2 * np.pi * 180.0 * t)).astype(np.float32),
            sample_rate=self.sample_rate,
            provider=self.name,
            voice="test",
            duration_ms=self.ms,
        )

    def describe(self):
        return {"provider": self.name, "voice": "test"}


class RecordingPlayer:
    """Stands in for the sound card and fires the real first-sample hook."""

    def __init__(self):
        self.played: list[int] = []
        self.is_playing = False

    def play(self, audio, sample_rate=None, on_first_sample=None):
        self.played.append(len(audio))
        if on_first_sample:
            on_first_sample()
        return 0.0


class SilentASR:
    """Transcribes nothing; typed input is used instead."""

    name = "typed_input"
    model = None
    sample_rate = 16000

    def transcribe(self, audio, sample_rate=None):
        from nbva.asr.providers import ASRResult

        return ASRResult(text="", error=None, language=None)

    def describe(self):
        return {"provider": self.name}


# -- helpers --------------------------------------------------------------


def build_pipeline(config, repo, session, llm, tts=None, player=None):
    return BaselinePipeline(
        session=session,
        asr=SilentASR(),
        llm=llm,
        tts=tts or NullTTSProvider(),
        system_prompt=load_system_prompt(config, "test"),
        analyzer=build_analyzer(config, repo),
        conditions=load_conditions(config),
        wer_config=wer_config_from_config(config),
        enforce_grounding=True,
        max_tool_iterations=6,
        player=player,
    )


def make_session(repo, **kwargs):
    from nbva.agent.session import create_session

    kwargs.setdefault("session_id", "e2e-session")
    return create_session(
        repository=repo,
        experiment_id="TEST-EXP",
        is_research_run=False,
        business_weekday="wednesday",
        **kwargs,
    )


def order_reply(text="تمام، الطلب 290 جنيه.", tool="create_order"):
    return LLMResponse(
        text="",
        tool_calls=[
            ToolCall(
                name="create_order",
                arguments={
                    "lines": [{"item": "NB-BURG-001", "quantity": 1}],
                    "zone": "zamalek",
                },
                call_id="c1",
            )
        ],
        stop_reason="tool_calls",
    )


# -- a single turn --------------------------------------------------------


def test_a_plain_turn_is_measured_and_logged(config, repo):
    session = make_session(repo)
    llm = ScriptedLLM([LLMResponse(text="أهلاً بيك في نايل بايتس.", tool_calls=[])])
    pipeline = build_pipeline(config, repo, session, llm)

    result = pipeline.process_turn(text="عايز برجر", reference_transcript="عايز برجر", speak=False)
    record = result.turn

    assert isinstance(record, TurnRecord)
    assert record.turn_index == 1
    assert record.asr_transcript == "عايز برجر"
    assert record.response_text
    assert record.english_pct == 0.0
    assert record.measured_condition == "C0"


def test_the_measure_uses_the_reference_not_the_asr_output(config, repo):
    # A recognition error must not change the manipulated variable, so the
    # measure is taken from the reference when one exists.
    session = make_session(repo)
    llm = ScriptedLLM([LLMResponse(text="تمام", tool_calls=[])])
    pipeline = build_pipeline(config, repo, session, llm)

    result = pipeline.process_turn(
        text="I want two chicken burgers please",
        reference_transcript="I want two chicken burgers please",
        speak=False,
    )
    record = result.turn
    assert record.codeswitch_measure_source == "reference_transcript"
    assert record.english_pct == 100.0
    assert record.measured_condition == "C4"
    # The typed input is identical to the reference, so WER must be zero.
    assert record.wer == 0.0


def test_a_turn_with_no_reference_falls_back_to_the_asr_text(config, repo):
    session = make_session(repo)
    llm = ScriptedLLM([LLMResponse(text="تمام", tool_calls=[])])
    pipeline = build_pipeline(config, repo, session, llm)

    result = pipeline.process_turn(text="I want a burger", speak=False)
    assert result.turn.codeswitch_measure_source == "asr_transcript"
    assert result.turn.wer is None


def test_a_declared_condition_that_disagrees_is_flagged(config, repo):
    session = make_session(repo)
    llm = ScriptedLLM([LLMResponse(text="تمام", tool_calls=[])])
    pipeline = build_pipeline(config, repo, session, llm)

    result = pipeline.process_turn(
        text="I want two chicken burgers please",
        reference_transcript="I want two chicken burgers please",
        declared_condition="C0",
        speak=False,
    )
    assert result.turn.measured_condition == "C4"
    assert any("disagrees" in w for w in result.turn.condition_warnings)


def test_a_disagreeing_turn_is_flagged_inconsistent_but_still_returned(config, repo):
    """A drifted trial is labelled, not discarded.

    Whether an out-of-band trial counts is a scientific decision, so the
    pipeline records the disagreement and hands the turn back. Silently
    dropping it would remove participant data on the agent's own authority.
    """
    session = make_session(repo)
    llm = ScriptedLLM([LLMResponse(text="تمام", tool_calls=[])])
    pipeline = build_pipeline(config, repo, session, llm)

    result = pipeline.process_turn(
        text="I want two chicken burgers please",
        reference_transcript="I want two chicken burgers please",
        declared_condition="C0",
        speak=False,
    )
    assert result.turn.condition_consistent is False
    assert result.turn.response_text == "تمام"


def test_a_turn_matching_its_declared_condition_is_consistent(config, repo):
    session = make_session(repo)
    llm = ScriptedLLM([LLMResponse(text="تمام", tool_calls=[])])
    pipeline = build_pipeline(config, repo, session, llm)

    result = pipeline.process_turn(
        text="I want two chicken burgers please",
        reference_transcript="I want two chicken burgers please",
        declared_condition="C4",
        speak=False,
    )
    assert result.turn.measured_condition == "C4"
    assert result.turn.condition_consistent is True
    # C4 carries a soft maximum, so 100% English draws a heavy-mixing advisory.
    # That is a note about the band, not a claim that the label is wrong.
    assert not any("disagrees" in w for w in result.turn.condition_warnings)


def test_consistency_is_decided_by_the_bands_not_by_the_warnings(config, repo):
    """Advisories never change the consistency verdict.

    The band set is provisional and has a soft maximum, so warnings fire on
    almost every turn. If a warning could flip consistency, the flag would be
    unreliable for the analysis it exists to feed.
    """
    session = make_session(repo)
    llm = ScriptedLLM([LLMResponse(text="تمام", tool_calls=[])])
    pipeline = build_pipeline(config, repo, session, llm)

    result = pipeline.process_turn(
        text="I want two chicken burgers please",
        reference_transcript="I want two chicken burgers please",
        declared_condition="C4",
        speak=False,
    )
    assert result.turn.condition_warnings  # advisories are present
    assert result.turn.condition_consistent is True  # and did not affect the verdict


def test_an_unbanded_utterance_is_reported_without_crashing(config, repo):
    session = make_session(repo)
    llm = ScriptedLLM([LLMResponse(text="تمام", tool_calls=[])])
    pipeline = build_pipeline(config, repo, session, llm)

    result = pipeline.process_turn(
        text="؟؟؟",
        reference_transcript="؟؟؟",
        declared_condition="C2",
        speak=False,
    )
    assert result.turn.condition_consistent is False
    assert result.turn.measured_condition in (None, "C0")


# -- the tool loop --------------------------------------------------------


def test_a_tool_call_reaches_the_deterministic_layer(config, repo):
    session = make_session(repo)
    llm = ScriptedLLM([order_reply(), LLMResponse(text="تمام، 290 جنيه.", tool_calls=[])])
    pipeline = build_pipeline(config, repo, session, llm)

    result = pipeline.process_turn(text="عايز برجر توصل الزمالك", speak=False)

    assert [c["name"] for c in result.tool_calls] == ["create_order"]
    assert result.final_state is not None
    assert result.final_state["total"] == 160
    assert result.final_state["priced_weekday"] == "wednesday"


def test_the_model_is_offered_the_tool_schemas(config, repo):
    session = make_session(repo)
    llm = ScriptedLLM([LLMResponse(text="تمام", tool_calls=[])])
    build_pipeline(config, repo, session, llm).process_turn(text="hi", speak=False)
    assert llm.seen_tools and llm.seen_tools[0]
    names = {t["function"]["name"] for t in llm.seen_tools[0]}
    assert "create_order" in names


def test_a_tool_refusal_is_surfaced_not_swallowed(config, repo):
    session = make_session(repo)
    llm = ScriptedLLM(
        [
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        name="create_order",
                        arguments={"lines": [{"item": "NB-GRIL-005", "quantity": 1}],
                                   "zone": "zamalek"},
                        call_id="c1",
                    )
                ],
                stop_reason="tool_calls",
            ),
            LLMResponse(text="آسف، ده مش متاح.", tool_calls=[]),
        ]
    )
    pipeline = build_pipeline(config, repo, session, llm)
    result = pipeline.process_turn(text="عايز المشاوي", speak=False)

    assert result.tool_calls[0]["ok"] is False
    assert result.final_state is None
    assert "متاح" in result.response_text


def test_a_tool_loop_is_capped(config, repo):
    session = make_session(repo)
    # A model that keeps asking for tools must not spin forever.
    llm = ScriptedLLM(
        [order_reply() for _ in range(50)]
    )
    pipeline = build_pipeline(config, repo, session, llm)
    pipeline.max_tool_iterations = 3
    result = pipeline.process_turn(text="عايز برجر", speak=False)
    assert len(result.tool_calls) <= 4


def test_tool_time_is_measured_separately_from_generation(config, repo):
    session = make_session(repo)
    llm = ScriptedLLM([order_reply(), LLMResponse(text="تمام", tool_calls=[])])
    pipeline = build_pipeline(config, repo, session, llm)
    record = pipeline.process_turn(text="عايز برجر", speak=False).turn
    assert record.latency.tools_ms is not None
    assert record.latency.tools_ms >= 0
    assert record.latency.nlu_ms is not None


# -- grounding ------------------------------------------------------------


def test_a_number_the_tools_never_returned_is_flagged(config, repo):
    session = make_session(repo)
    llm = ScriptedLLM(
        [
            order_reply(),
            LLMResponse(text="تمام، الطلب 999 جنيه.", tool_calls=[]),
        ]
    )
    pipeline = build_pipeline(config, repo, session, llm)
    record = pipeline.process_turn(text="عايز برجر", speak=False).turn
    assert record.grounding_violations, "an ungrounded price must be reported"


def test_a_number_the_tools_did_return_is_accepted(config, repo):
    session = make_session(repo)
    llm = ScriptedLLM(
        [
            order_reply(),
            LLMResponse(text="تمام، الطلب 160 جنيه.", tool_calls=[]),
        ]
    )
    pipeline = build_pipeline(config, repo, session, llm)
    record = pipeline.process_turn(text="عايز برجر", speak=False).turn
    assert "999" not in " ".join(record.grounding_violations)
    assert record.grounding_violations == []


# -- the audio path -------------------------------------------------------


def test_the_audio_path_stamps_the_latency_endpoint(config, repo):
    session = make_session(repo)
    llm = ScriptedLLM([LLMResponse(text="أهلاً بيك.", tool_calls=[])])
    player = RecordingPlayer()
    tts = ScriptedTTS()
    pipeline = build_pipeline(config, repo, session, llm, tts=tts, player=player)

    record = pipeline.process_turn(text="hi", speak=True).turn
    assert player.played, "audio should have been played"
    assert record.latency.response_audio_start_mono is not None
    assert record.latency.primary_latency_ms is not None
    assert record.latency.primary_latency_ms >= 0


def test_text_mode_leaves_latency_unset_rather_than_faking_it(config, repo):
    session = make_session(repo)
    llm = ScriptedLLM([LLMResponse(text="أهلاً", tool_calls=[])])
    pipeline = build_pipeline(config, repo, session, llm)
    record = pipeline.process_turn(text="hi", speak=False).turn
    # A meaningless latency number is worse than none for a locked metric.
    assert record.latency.primary_latency_ms is None
    assert "latency not measured" in record.notes


def test_an_audio_turn_transcribes_and_measures(config, repo):
    session = make_session(repo)

    class FixedASR(SilentASR):
        name = "scripted_asr"

        def transcribe(self, audio, sample_rate=None):
            from nbva.asr.providers import ASRResult

            return ASRResult(text="I want two burgers", error=None, language="en")

    llm = ScriptedLLM([LLMResponse(text="ok", tool_calls=[])])
    pipeline = BaselinePipeline(
        session=session,
        asr=FixedASR(),
        llm=llm,
        tts=NullTTSProvider(),
        system_prompt="test",
        analyzer=build_analyzer(config, repo),
        conditions=load_conditions(config),
        save_audio=False,
    )
    audio = np.zeros(16000, dtype=np.float32)
    record = pipeline.process_turn(audio=audio, speak=False).turn
    assert record.asr_transcript == "I want two burgers"
    assert record.asr_provider == "scripted_asr"
    assert record.audio_duration_ms == 1000.0


# -- multi-turn -----------------------------------------------------------


def test_history_carries_across_turns(config, repo):
    session = make_session(repo)
    llm = ScriptedLLM(
        [
            order_reply(),
            LLMResponse(text="تمام.", tool_calls=[]),
            LLMResponse(text="غيرت كده.", tool_calls=[]),
        ]
    )
    pipeline = build_pipeline(config, repo, session, llm)
    pipeline.process_turn(text="عايز برجر", speak=False)
    pipeline.process_turn(text="غير الكمية", speak=False)

    assert [h["content"] for h in llm.seen_messages[-1]] != []
    assert session.history[-1].role == "assistant"
    assert [r for r in session.history if r.role == "user"][-1].text == "غير الكمية"


def test_state_persists_between_turns_of_one_trial(config, repo):
    session = make_session(repo)
    llm = ScriptedLLM(
        [
            order_reply(),
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        name="modify_order",
                        arguments={"set_quantities": [
                            {"line": "L1", "quantity": 2}
                        ]},
                        call_id="c1",
                    )
                ],
                stop_reason="tool_calls",
            ),
            LLMResponse(text="تمام.", tool_calls=[]),
        ]
    )
    pipeline = build_pipeline(config, repo, session, llm)
    pipeline.process_turn(text="عايز برجر", speak=False)
    result = pipeline.process_turn(text="خليها اتنين", speak=False)
    assert result.final_state["lines"][0]["quantity"] == 2


# -- the log --------------------------------------------------------------


def test_a_real_session_logger_records_a_whole_turn(config, repo, tmp_path):
    from nbva.obs.logger import SessionLogger, config_fingerprint

    logger = SessionLogger(
        session_id="e2e-log",
        experiment_id="TEST-EXP",
        log_dir=tmp_path,
        is_research_run=False,
        echo_to_console=False,
        save_audio=False,
        config_fingerprint=config_fingerprint(config),
    )
    session = make_session(repo, session_id="e2e-log")
    llm = ScriptedLLM([order_reply(), LLMResponse(text="تمام.", tool_calls=[])])
    pipeline = build_pipeline(config, repo, session, llm)
    pipeline.logger = logger

    logger.log_session_header(mode="test")
    result = pipeline.process_turn(text="عايز برجر", speak=False)
    logger.log_turn(result.turn)
    logger.log_session_footer()

    rows = turns_of(read_session(logger.path))
    assert len(rows) == 1
    row = rows[0]
    assert row["english_pct"] == 0.0
    assert row["tool_call_count"] == 1
    assert row["final_state"]["total"] == 160
    assert row["business_weekday"] == "wednesday"


# -- the app factory ------------------------------------------------------


def test_the_app_builds_with_the_scripted_stack(config, repo, tmp_path):
    # The whole assembly point has to work offline, otherwise no test above can
    # be trusted to reflect the shipped wiring.
    config.set("llm.provider", "scripted")
    config.set("tts.provider", "null")
    config.set("asr.provider", "manual")
    config.set("paths.log_dir", str(tmp_path / "logs"))
    config.set("logging.echo_to_console", False)
    config.set("logging.save_audio", False)

    app = build_app(config, attach_player=False, session_id="app-test")
    described = app.describe()
    assert described["llm"]["provider"] == "scripted"
    assert described["is_research_run"] is False
    assert described["config_fingerprint"]
    assert described["conditions"]["bands"]
    app.logger.log_session_footer()


def test_the_app_reports_the_participant_when_set(config, repo, tmp_path):
    config.set("experiment.participant_id", "P007")
    config.set("llm.provider", "scripted")
    config.set("tts.provider", "null")
    config.set("asr.provider", "manual")
    config.set("paths.log_dir", str(tmp_path / "logs"))
    config.set("logging.echo_to_console", False)

    app = build_app(config, attach_player=False, session_id="app-part")
    assert app.describe()["participant_id"] == "P007"
    app.logger.log_session_footer()


def test_a_text_turn_through_the_app_runs_the_whole_chain(config, repo, tmp_path):
    config.set("llm.provider", "scripted")
    config.set("tts.provider", "null")
    config.set("asr.provider", "manual")
    config.set("paths.log_dir", str(tmp_path / "logs"))
    config.set("logging.echo_to_console", False)
    config.set("logging.save_audio", False)

    app = build_app(config, attach_player=False, session_id="app-turn")
    result = app.pipeline.process_turn(text="عايز برجر", speak=False)
    assert result.turn.session_id == "app-turn"
    assert "scripted" in result.response_text
    app.logger.log_session_footer()
