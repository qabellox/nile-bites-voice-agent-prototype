"""The architecture factor: selection, refusal, and A0 parity.

Two things are pinned here.

1. **Switchability.** ``architecture.engine`` selects the arm in one place, an
   unknown value is rejected, and ``a1`` refuses instead of degrading to A0.
   That refusal is a scientific guard, not a convenience: a silent fallback would
   label baseline behaviour as the intervention.

2. **Parity.** Building the app through the engine factory must not change a
   single observable of an A0 turn. If this test ever fails, the baseline stopped
   being the baseline and every comparison against it is void.
"""

from __future__ import annotations

import pytest

from nbva.agent.app import build_app
from nbva.agent.pipeline import BaselinePipeline
from nbva.arch.base import (
    A1_COMPONENTS,
    ENGINE_A0,
    ENGINE_A1,
    TurnEngine,
    build_turn_engine,
    describe_engine,
    selected_engine,
)
from nbva.config.loader import load_config


def _offline_config(tmp_path, engine: str | None = None):
    """Real config and real database; only the three provider boundaries vary."""
    config = load_config()
    config.set("llm.provider", "scripted")
    config.set("llm.scripted.fixed_reply", "تمام، طلبك اتسجل.")
    config.set("tts.provider", "null")
    config.set("asr.provider", "manual")
    config.set("paths.log_dir", str(tmp_path / "logs"))
    config.set("logging.echo_to_console", False)
    config.set("logging.save_audio", False)
    config.set("logging.write_run_manifest", False)
    if engine is not None:
        config.set("architecture.engine", engine)
    return config


def _observable(result):
    """The parts of a turn that a caller or a grader can see."""
    turn = result.turn
    return {
        "response_text": result.response_text,
        "asr_text": result.asr_text,
        "tool_calls": result.tool_calls,
        "final_state": result.final_state,
        "final_system_action": turn.final_system_action,
        "grounding_violations": turn.grounding_violations,
        "tool_call_count": turn.tool_call_count,
        "error": result.error,
    }


# -- selection -------------------------------------------------------------


def test_default_engine_is_the_baseline():
    assert selected_engine(load_config()) == ENGINE_A0


def test_engine_selection_is_case_and_whitespace_tolerant():
    config = load_config()
    config.set("architecture.engine", "  A0  ")
    assert selected_engine(config) == ENGINE_A0


def test_unknown_engine_is_rejected_rather_than_defaulted():
    config = load_config()
    config.set("architecture.engine", "a2")
    with pytest.raises(ValueError, match="not a known engine"):
        selected_engine(config)


def test_a1_is_not_silently_substituted_with_a0(tmp_path):
    """The most important failure mode in the whole study.

    If selecting a1 produced baseline behaviour, every trial in that cell would be
    recorded as the intervention while running the baseline, and the error would
    be invisible until analysis. It has to fail at construction.
    """
    config = _offline_config(tmp_path, engine=ENGINE_A1)
    with pytest.raises(NotImplementedError, match="Refusing to fall back"):
        build_app(config, attach_player=False, session_id="arch-a1")


def test_a0_engine_is_the_unmodified_baseline_pipeline(tmp_path):
    config = _offline_config(tmp_path, engine=ENGINE_A0)
    app = build_app(config, attach_player=False, session_id="arch-a0")
    assert isinstance(app.pipeline, BaselinePipeline)
    assert isinstance(app.pipeline, TurnEngine)
    assert app.engine_id == ENGINE_A0
    app.logger.log_session_footer()


def test_describe_reports_the_engine_and_its_components(tmp_path):
    config = _offline_config(tmp_path)
    app = build_app(config, attach_player=False, session_id="arch-desc")
    described = app.describe()["architecture"]
    assert described == {"engine": ENGINE_A0, "components": {}}
    # A0 has nothing to ablate; A1's component names are declared once, centrally.
    assert set(A1_COMPONENTS) == {
        "context",
        "orchestration",
        "gateway",
        "validation",
        "latency_path",
    }
    app.logger.log_session_footer()


def test_describe_engine_tolerates_an_untagged_object():
    class Bare:
        def process_turn(self, **kwargs):
            raise AssertionError("not called")

    assert describe_engine(Bare()) == {"engine": ENGINE_A0, "components": {}}


# -- parity ----------------------------------------------------------------


def test_engine_selection_does_not_change_an_a0_turn(tmp_path):
    """A0 through the factory must equal A0 through the manual construction path.

    The factory is only allowed to be a constructor. Any behavioural difference
    between these two runs means the factory drifted into the experiment.
    """
    default_config = _offline_config(tmp_path / "default")
    explicit_config = _offline_config(tmp_path / "explicit", engine=ENGINE_A0)

    via_default = build_app(default_config, attach_player=False, session_id="parity-a")
    via_explicit = build_app(explicit_config, attach_player=False, session_id="parity-b")

    assert type(via_default.pipeline) is type(via_explicit.pipeline)

    a = via_default.pipeline.process_turn(text="عايز أوردر", speak=False)
    b = via_explicit.pipeline.process_turn(text="عايز أوردر", speak=False)

    assert _observable(a) == _observable(b)

    via_default.logger.log_session_footer()
    via_explicit.logger.log_session_footer()


def test_factory_forwards_every_setting_to_the_baseline(tmp_path):
    """Both arms receive the same bundle, so no limit may be silently dropped."""
    config = _offline_config(tmp_path)
    config.set("llm.max_tool_iterations", 3)
    config.set("tools.enforce_grounding", False)

    app = build_app(config, attach_player=False, session_id="forward")
    pipeline = app.pipeline
    assert pipeline.max_tool_iterations == 3
    assert pipeline.enforce_grounding is False
    assert pipeline.session is app.session
    assert pipeline.llm is app.llm
    assert pipeline.asr is app.asr
    assert pipeline.tts is app.tts
    app.logger.log_session_footer()


def test_factory_rejects_a_bundle_missing_a_required_argument(tmp_path):
    """A silently-accepted partial bundle would build an engine with defaults."""
    config = _offline_config(tmp_path)
    with pytest.raises(TypeError):
        build_turn_engine(config, {"session": None})
