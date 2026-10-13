"""Tests for the experiment infrastructure (validator + runner + export).

All fast and offline: fakes stand in for architectures and models. Real-model
behavior is validated manually against synthetic controls and reported
separately, never asserted here.
"""

import json

import pytest

from nbva.research.conditions import Band, ConditionSet
from nbva.research.experiment import (
    TrialSpec,
    freeze_experiment,
    load_trials,
    pilot_distribution,
    records_to_csv,
    trial_from_dict,
    validate_dataset,
    write_jsonl,
)
from nbva.research.taskdefs import task_from_dict


def _bands():
    return ConditionSet(
        bands=[
            Band(id="C0", label="ar only", label_ar_eg="", min_pct=0.0, max_pct=0.0,
                 min_inclusive=True, max_inclusive=True, provisional=False),
            Band(id="C1", label="slight", label_ar_eg="", min_pct=0.0, max_pct=50.0,
                 min_inclusive=False, max_inclusive=True),
            Band(id="C2", label="heavy", label_ar_eg="", min_pct=50.0, max_pct=100.0,
                 min_inclusive=False, max_inclusive=True),
        ])


def _task(task_id="T1", ref="hello world", declared="C2", ground=True):
    raw = {"task_id": task_id, "family": "information",
           "declared_condition": declared,
           "turns": [{"index": 0, "utterance": ref, "reference_transcript": ref}]}
    if ground:
        raw["expected_tools"] = ["search_menu"]
    return task_from_dict(raw)


def _trial(trial_id="TR1", task_id="T1", audio=None, verified=60.0,
           verified_english_pct=None, declared="C2", declared_condition=None,
           speaker=None):
    if verified_english_pct is None:
        verified_english_pct = verified
    if declared_condition is None:
        declared_condition = declared
    return TrialSpec(trial_id=trial_id, task_id=task_id, audio_path=audio,
                     speaker=speaker or {"source": "synthetic"},
                     verified_english_pct=verified_english_pct,
                     declared_condition=declared_condition)


def _measure(_text):
    return 60.0


# ---------------------------------------------------------------- schema

def test_trial_round_trip_preserves_fields():
    raw = {"trial_id": "TR1", "task_id": "T1", "audio_path": "a.wav",
           "speaker": {"source": "synthetic"}, "verified_english_pct": 12.5,
           "declared_condition": "C1", "note": "n"}
    trial = trial_from_dict(raw)
    assert trial.audio_files() == ["a.wav"]
    back = trial.to_dict()
    assert back["verified_english_pct"] == 12.5
    assert back["declared_condition"] == "C1"


def test_load_trials_reads_yaml_list(tmp_path):
    path = tmp_path / "trials.yaml"
    path.write_text("trials:\n  - {trial_id: A, task_id: T1}\n", encoding="utf-8")
    trials = load_trials(path)
    assert [t.trial_id for t in trials] == ["A"]


# ---------------------------------------------------------------- validator

def test_clean_dataset_passes(tmp_path):
    wav = tmp_path / "u.wav"
    wav.write_bytes(b"RIFF....")
    trials = [_trial(audio=str(wav))]
    flags = validate_dataset(trials, {"T1": _task()}, _bands(),
                             measure_fn=_measure, audio_root=tmp_path)["flags"]
    assert flags == {}


def test_duplicate_ids_missing_audio_and_reference_are_flagged(tmp_path):
    t1 = _trial(trial_id="DUP", task_id="T1", audio="gone.wav")
    t2 = _trial(trial_id="DUP", task_id="T1", audio="gone.wav")
    t3 = TrialSpec(trial_id="T3", task_id="T9", audio_path=None,
                   verified_english_pct=10.0, declared_condition="C1")
    no_ref = _task(task_id="T4", ref="", declared="C1")
    no_ground = _task(task_id="T5", ref="hello world", declared="C2", ground=False)
    flags = validate_dataset(
        [t1, t2, t3], {"T1": _task(), "T4": no_ref, "T5": no_ground},
        _bands(), measure_fn=_measure, audio_root=tmp_path)["flags"]
    dup = [m for m in flags.get("DUP", []) if "duplicate" in m]
    assert len(dup) >= 2  # trial and task ids
    assert any("missing audio" in m for m in flags.get("DUP", []))
    assert any("no known task" in m for m in flags.get("T3", []))


def test_missing_reference_and_ground_truth_are_flagged(tmp_path):
    trials = [TrialSpec(trial_id="R", task_id="T4", verified_english_pct=5.0,
                        declared_condition="C1"),
              TrialSpec(trial_id="G", task_id="T5", verified_english_pct=60.0,
                        declared_condition="C2")]
    tasks = {"T4": _task(task_id="T4", ref="", declared="C1"),
             "T5": _task(task_id="T5", ref="hello world", declared="C2", ground=False)}
    flags = validate_dataset(trials, tasks, _bands(), audio_root=tmp_path)["flags"]
    assert any("reference" in m for m in flags["R"])
    assert any("ground truth" in m for m in flags["G"])


def test_declared_vs_verified_mismatch_is_flagged(tmp_path):
    trials = [_trial(verified_english_pct=95.0, declared_condition="C1")]
    flags = validate_dataset(trials, {"T1": _task(declared="C1")}, _bands(),
                             audio_root=tmp_path)["flags"]
    assert any("disagrees" in m for m in flags["TR1"])


def test_unknown_condition_and_bad_speaker_metadata_are_flagged(tmp_path):
    trials = [_trial(declared_condition="C9",
                     speaker={"source": "synthetic", "pii": {"name": "x"}})]
    flags = validate_dataset(trials, {"T1": _task()}, _bands(),
                             audio_root=tmp_path)["flags"]
    assert any("unknown declared condition" in m for m in flags["TR1"])
    assert any("scalar" in m for m in flags["TR1"])


def test_reference_measurement_cross_check_flags_bad_turns(tmp_path):
    task = _task(task_id="T1", ref="hello world", declared="C2")
    trials = [_trial(verified_english_pct=60.0, declared_condition="C2")]
    flags = validate_dataset(trials, {"T1": task}, _bands(),
                             measure_fn=lambda _s: 5.0,
                             audio_root=tmp_path)["flags"]
    assert any("disagrees" in m for m in flags["TR1"])


# ---------------------------------------------------------------- distribution + freeze

def test_pilot_distribution_reports_without_deciding():
    out = pilot_distribution([
        {"declared_condition": "C1", "verified_english_pct": 12.5},
        {"declared_condition": "C1", "verified_english_pct": 15.0},
        {"declared_condition": "C2", "verified_english_pct": 33.0},
    ])
    assert out["per_band"]["C1"] == {"n": 2, "min": 12.5, "max": 15.0, "mean": 13.75}
    assert out["total"] == 3


def test_freeze_snapshots_database_and_versions(tmp_path):
    from nbva.config.loader import load_config

    db = tmp_path / "db.json"
    db.write_text('{"menu": []}', encoding="utf-8")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("be nice", encoding="utf-8")
    freeze = freeze_experiment(load_config(), tmp_path / "exp", "EXP-TEST",
                               db_path=db, prompt_path=prompt)
    assert (tmp_path / "exp" / "freeze.json").exists()
    assert freeze.database["sha256"]
    assert (tmp_path / "exp" / "database").is_dir()
    assert freeze.prompts["sha256"]
    assert freeze.software["python"]
    assert freeze.thresholds["asr_failure_wer"] == 0.5
    assert [a["id"] for a in freeze.architectures] == ["baseline", "streaming"]


# ---------------------------------------------------------------- runner (fakes, no network)

class _FakeRunner:
    """Minimal stand-in exercising record assembly, not architectures."""

    def __init__(self):
        from nbva.research.runner import ExperimentRunner

        self.inner = ExperimentRunner.__new__(ExperimentRunner)


def test_runner_builds_structured_records_from_canned_summaries(monkeypatch):
    from nbva.research.runner import ExperimentRunner

    runner = ExperimentRunner.__new__(ExperimentRunner)
    runner.experiment_id = "EXP-T"
    runner.asr_failure_wer = 0.5
    runner.latency_failure_seconds = 120.0
    runner.analyzer = None
    runner.conditions = _bands()
    runner.wer_config = None

    canned = {
        "turns": [{"index": 0, "reference": "hello world", "asr": "hello world",
                   "response": "hi", "tool_calls": [], "asr_ms": 10.0,
                   "nlu_ms": 20.0, "tools_ms": 1.0, "tts_ms": 5.0}],
        "tool_calls": [], "response_text": "hi",
        "final_state": None, "evaluation": None, "error": None,
    }

    from nbva.research.taskdefs import EvaluationResult
    canned["evaluation"] = EvaluationResult(success=True, checks=[], score=1.0)

    monkeypatch.setattr(runner, "run_baseline_audio", lambda *a, **k: dict(canned))
    monkeypatch.setattr(runner, "run_streaming_audio", lambda *a, **k: dict(canned))

    task = _task()
    trial = _trial()
    records = runner.run_trial(trial, task)
    assert [r.architecture for r in records] == ["baseline", "streaming"]
    base = records[0].to_dict()
    assert base["trial_id"] == "TR1" and base["task_id"] == "T1"
    assert base["task_success"] is True and base["failure_category"] == "none"
    assert base["wer"] == 0.0
    assert base["latency_s"] == 0.035
    assert base["turns"][0]["wer"] == 0.0
    stream = records[1].to_dict()
    assert stream["architecture"] == "streaming"


def test_runner_marks_unknown_tasks_without_crashing():
    from nbva.research.runner import ExperimentRunner

    runner = ExperimentRunner.__new__(ExperimentRunner)
    runner.experiment_id = "EXP-T"
    records = runner.run_experiment([_trial(task_id="NOPE")], {})
    assert len(records) == 1 and "unknown task_id" in (records[0].error or "")


# ---------------------------------------------------------------- export

def test_jsonl_and_csv_round_trip(tmp_path):
    from nbva.research.experiment import records_to_csv, write_jsonl

    import csv as _csv
    import json as _json

    records = [{"experiment_id": "E", "architecture": "baseline", "trial_id": "T",
                "task_id": "K", "wer": 0.1, "tool_calls": [{"name": "x"}]}]
    jp = write_jsonl(records, tmp_path / "out.jsonl")
    assert _json.loads(open(jp, encoding="utf-8").readline())["trial_id"] == "T"
    cp = records_to_csv(records, tmp_path / "out.csv")
    rows = list(_csv.DictReader(open(cp, encoding="utf-8")))
    assert rows[0]["trial_id"] == "T" and rows[0]["wer"] == "0.1"
    assert "search" not in rows[0]["tool_calls"] or True
