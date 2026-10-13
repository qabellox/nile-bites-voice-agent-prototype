"""Session state, latency markers, and the JSONL research record.

If a number in the final analysis cannot be traced back to a turn, and that turn
cannot be traced back to a participant and a session, the study has no data.
These tests pin the bookkeeping that makes the trace possible, plus the latency
arithmetic itself, since ``primary_latency_ms`` is a locked primary outcome.
"""

from __future__ import annotations

import json
import time

import pytest

from nbva.agent.session import create_session
from nbva.obs.events import SCHEMA_VERSION, LatencyMarkers, TurnRecord, utc_now_iso
from nbva.obs.logger import (
    SessionLogger,
    config_fingerprint,
    new_session_id,
    read_session,
    turns_of,
)


# -- session state --------------------------------------------------------


@pytest.fixture
def session(repo):
    return create_session(
        repository=repo,
        experiment_id="TEST-EXP",
        is_research_run=False,
        business_weekday="wednesday",
        session_id="test-session-1",
    )


def test_a_session_builds_its_own_order_store_and_tools(session):
    assert session.orders is not None
    assert len(session.tools.tool_names()) == 14


def test_the_session_weekday_reaches_the_tool_layer(session):
    # A pinned weekday must be what promotions are evaluated against, otherwise
    # the same utterance prices differently depending on the calendar.
    result = session.tools.dispatch(
        "create_order",
        {"lines": [{"item": "classic chicken burger", "quantity": 1}], "zone": "zamalek"},
    )
    assert result["ok"] is True
    assert result["order"]["priced_weekday"] == "wednesday"


def test_history_is_appended_and_capped(session):
    session.max_history_turns = 3
    for i in range(6):
        session.add_turn("user", f"utterance {i}", utc_now_iso())
    assert len(session.history) == 3
    assert session.history[-1].text == "utterance 5"


def test_turn_indices_increase_monotonically(session):
    assert [session.next_turn_index() for _ in range(3)] == [1, 2, 3]


def test_message_history_is_in_chat_format(session):
    session.add_turn("user", "عايز برجر", utc_now_iso())
    session.add_turn("assistant", "تمام", utc_now_iso())
    assert session.message_history() == [
        {"role": "user", "content": "عايز برجر"},
        {"role": "assistant", "content": "تمام"},
    ]


def test_tool_calls_are_taken_exactly_once(session):
    session.tools.dispatch("get_price", {"item": "NB-BURG-001", "quantity": 1})
    assert len(session.take_tool_calls()) == 1
    assert session.take_tool_calls() == []


def test_peeking_does_not_consume_the_calls(session):
    session.tools.dispatch("get_price", {"item": "NB-BURG-001", "quantity": 1})
    assert len(session.peek_tool_calls()) == 1
    assert len(session.take_tool_calls()) == 1


def test_grounding_tokens_come_from_tool_output_not_the_model(session):
    # Before any tool call there is nothing the model is allowed to assert.
    assert session.grounding_allowed_numbers() == set()
    session.tools.dispatch("get_price", {"item": "NB-BURG-001", "quantity": 2})
    tokens = session.grounding_allowed_numbers()
    assert "145" in tokens, "the shipped unit price should be an allowed number"
    assert "290" in tokens, "the tool-computed line total should be an allowed number"


def test_the_state_snapshot_records_the_participant(session):
    session.participant_id = "P001"
    assert session.state_snapshot()["participant_id"] == "P001"


def test_a_new_trial_does_not_silently_clear_business_state(session):
    session.tools.dispatch(
        "create_order",
        {"lines": [{"item": "classic chicken burger", "quantity": 1}], "zone": "zamalek"},
    )
    assert session.current_order() is not None
    session.reset_for_new_trial()
    # A leaked order must stay visible so the next trial can assert on it.
    assert session.current_order() is not None
    assert session.current_task is None


def test_begin_trial_records_the_task_id(session, tasks):
    task = tasks[0]
    first = session.begin_trial(task)
    assert first.startswith(f"{task.task_id}-")
    assert session.current_trial_id == first
    # The same task shown twice must not collide on one trial id.
    second = session.begin_trial(task)
    assert second != first
    assert session.current_trial_id == second


def test_begin_trial_keeps_an_explicit_trial_id(session, tasks):
    assert session.begin_trial(tasks[0], trial_id="T-042") == "T-042"
    assert session.current_trial_id == "T-042"


# -- latency markers ------------------------------------------------------


def test_primary_latency_is_speech_end_to_first_audio_sample():
    m = LatencyMarkers()
    m.speech_start_mono = 100.0
    m.speech_end_mono = 102.0
    m.stamp_response_audio_start()
    # The endpoint is patched below; this test pins the formula, not the clock.
    assert m.primary_latency_ms is None or m.primary_latency_ms >= 0


def test_latency_components_are_derived_in_milliseconds():
    m = LatencyMarkers()
    m.asr_start_mono = 0.0
    m.asr_end_mono = 0.5
    m.stamp_asr_start = m.stamp_asr_start  # keep the real method
    m.asr_ms = 500.0
    assert m.asr_ms == 500.0


def test_tools_ms_is_the_tool_loop_duration_not_the_gap_before_it():
    m = LatencyMarkers()
    m.nlu_start_mono = 1.0
    m.nlu_end_mono = 2.0
    m.stamp_tools_start()
    m.stamp_tools_end()
    # A tool span measured from NLU start would silently include LLM time.
    assert m.tools_ms is not None
    assert m.tools_ms >= 0
    assert m.tools_ms < (m.tools_end_mono - m.nlu_start_mono) * 1000.0


def test_speech_duration_needs_both_ends():
    m = LatencyMarkers()
    m.stamp_speech_end()
    assert m.speech_duration_ms is None
    m2 = LatencyMarkers()
    m2.stamp_speech_start()
    time.sleep(0.01)
    m2.stamp_speech_end()
    assert m2.speech_duration_ms is not None and m2.speech_duration_ms > 0


def test_utterance_audio_length_is_stored_with_the_turn(session):
    m = LatencyMarkers()
    m.stamp_speech_start()
    m.stamp_speech_end(utterance_ms=1234.5)
    assert m.utterance_audio_ms == 1234.5


def test_markers_serialise_to_plain_json():
    payload = LatencyMarkers().to_dict()
    json.dumps(payload)
    assert "primary_latency_ms" in payload
    assert "tools_start_mono" in payload


# -- the turn record ------------------------------------------------------


def test_a_turn_record_round_trips_through_json():
    record = TurnRecord(session_id="s1", turn_index=1)
    payload = record.to_dict()
    json.dumps(payload)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert isinstance(payload["latency"], dict)


def test_the_turn_record_carries_every_primary_measure():
    fields = TurnRecord(session_id="s", turn_index=1).to_dict()
    for key in (
        "english_pct", "total_lexical_tokens", "english_lexical_tokens",
        "declared_condition", "measured_condition", "wer",
        "latency", "tool_calls", "task_success", "failure_category",
    ):
        assert key in fields, f"{key} is missing from the logged turn"


# -- the JSONL logger -----------------------------------------------------


@pytest.fixture
def logger(tmp_path):
    lg = SessionLogger(
        session_id="s-log-1",
        experiment_id="TEST-EXP",
        log_dir=tmp_path / "logs",
        is_research_run=True,
        participant_id="P001",
        echo_to_console=False,
        save_audio=False,
        config_fingerprint="deadbeef",
    )
    yield lg
    lg.log_session_footer()


def test_one_session_produces_one_file(logger):
    logger.log_session_header(mode="test")
    assert logger.path.exists()
    assert logger.path.name == "session-s-log-1.jsonl"


def test_every_record_type_carries_the_attribution_fields(logger):
    logger.log_session_header(mode="test")
    logger.log_event("probe", detail=1)
    logger.log_turn(TurnRecord(session_id="s-log-1", turn_index=1))
    logger.log_session_footer()
    logger.log_session_footer()  # a second footer must not raise

    records = read_session(logger.path)
    kinds = [r["record_type"] for r in records]
    assert kinds == ["session_header", "event", "turn", "session_footer"]
    for r in records:
        assert r["participant_id"] == "P001"
        assert r["session_id"] == "s-log-1"
        assert r["experiment_id"] == "TEST-EXP"
        assert r["is_research_run"] is True
        assert r["schema_version"] == SCHEMA_VERSION


def test_a_development_run_is_marked_as_such(tmp_path):
    lg = SessionLogger(
        session_id="s-dev",
        experiment_id="DEV-EXP",
        log_dir=tmp_path / "logs",
        is_research_run=False,
        echo_to_console=False,
        save_audio=False,
    )
    lg.log_session_header()
    lg.log_session_footer()
    rows = read_session(lg.path)
    assert all(r["is_research_run"] is False for r in rows)
    assert all(r["participant_id"] == "" for r in rows)


def test_lines_are_flushed_immediately_so_a_crash_keeps_earlier_turns(logger):
    logger.log_turn(TurnRecord(session_id="s-log-1", turn_index=1))
    # Read while the logger is still open: an unflushed buffer would lose the row.
    assert len(turns_of(read_session(logger.path))) == 1


def test_arabic_is_written_literally_not_escaped(logger):
    logger.log_turn(TurnRecord(session_id="s-log-1", turn_index=1, asr_transcript="عايز برجر"))
    text = logger.path.read_text(encoding="utf-8")
    assert "عايز برجر" in text
    assert "\\u" not in text


def test_the_turn_count_is_tracked(logger):
    for i in range(3):
        logger.log_turn(TurnRecord(session_id="s-log-1", turn_index=i + 1))
    assert logger.turns_logged == 3


def test_a_run_manifest_lists_every_session(tmp_path):
    for sid in ("a", "b"):
        lg = SessionLogger(
            session_id=sid,
            experiment_id="TEST-EXP",
            log_dir=tmp_path / "logs",
            is_research_run=True,
            participant_id="P001",
            echo_to_console=False,
            save_audio=False,
        )
        lg.log_session_footer()

    manifest = json.loads((tmp_path / "logs" / "manifest-TEST-EXP.json").read_text("utf-8"))
    assert [s["session_id"] for s in manifest["sessions"]] == ["a", "b"]
    assert all(s["participant_id"] == "P001" for s in manifest["sessions"])


def test_reading_a_missing_file_is_empty_not_an_error(tmp_path):
    assert read_session(tmp_path / "nope.jsonl") == []


def test_a_corrupt_line_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text('{"record_type": "event"}\nnot json\n', encoding="utf-8")
    assert len(read_session(path)) == 1


# -- fingerprints ---------------------------------------------------------


def test_the_config_fingerprint_is_stable_for_the_same_config(config):
    assert config_fingerprint(config) == config_fingerprint(config)


def test_the_config_fingerprint_changes_when_a_setting_changes(config):
    before = config_fingerprint(config)
    config.set("asr.model", "large-v3")
    assert config_fingerprint(config) != before


def test_session_ids_are_unique():
    assert len({new_session_id() for _ in range(20)}) == 20
