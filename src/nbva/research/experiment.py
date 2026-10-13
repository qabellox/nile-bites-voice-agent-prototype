"""Frozen experiment infrastructure: baseline vs streaming intervention.

This module contains ONLY research scaffolding. It does not modify either
architecture, the research question, the code-switching definition, or any
controlled variable. It provides:

* :func:`freeze_experiment` -- snapshot every frozen input (code versions,
  component settings, database copy, prompts, thresholds) into one directory.
* :class:`TrialSpec` -- the experiment-level trial wrapper (task reference +
  frozen audio path + speaker metadata + verified code-switching percentage).
  TheLocked ``TaskSpec`` format itself is untouched.
* :func:`validate_dataset` -- flags dataset problems before any run.
* :func:`pilot_distribution` -- per-band verified-percentage distribution used
  to freeze C0-C4 boundaries AFTER inspecting pilot data. It never chooses
  boundaries itself.
* :func:`write_jsonl` / :func:`records_to_csv` -- reproducible exports.

The locked measure stays: English lexical tokens / total lexical tokens x 100,
computed from the VERIFIED REFERENCE transcript, never from ASR output.
"""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _pip_versions(names: Iterable[str]) -> dict[str, str | None]:
    try:
        from importlib.metadata import version
    except Exception:
        return {n: None for n in names}
    out: dict[str, str | None] = {}
    for name in names:
        try:
            out[name] = version(name)
        except Exception:
            out[name] = None
    return out


# ---------------------------------------------------------------- freeze

@dataclass
class ExperimentFreeze:
    """One frozen experiment configuration. Written to freeze.json."""

    experiment_id: str
    architectures: list[dict[str, Any]] = field(default_factory=list)
    asr: dict[str, Any] = field(default_factory=dict)
    llm: dict[str, Any] = field(default_factory=dict)
    tts: dict[str, Any] = field(default_factory=dict)
    beam: dict[str, Any] = field(default_factory=dict)
    database: dict[str, Any] = field(default_factory=dict)
    prompts: dict[str, Any] = field(default_factory=dict)
    thresholds: dict[str, Any] = field(default_factory=dict)
    software: dict[str, Any] = field(default_factory=dict)
    conditions_snapshot: dict[str, Any] = field(default_factory=dict)
    created_utc: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "created_utc": self.created_utc or _utc_now_iso(),
            "architectures": [dict(a) for a in self.architectures],
            "asr": dict(self.asr),
            "llm": dict(self.llm),
            "tts": dict(self.tts),
            "beam": dict(self.beam),
            "database": dict(self.database),
            "prompts": dict(self.prompts),
            "thresholds": dict(self.thresholds),
            "software": dict(self.software),
            "conditions_snapshot": dict(self.conditions_snapshot),
        }


def snapshot_database(db_path: str | Path, out_dir: str | Path) -> dict[str, Any]:
    """Copy the restaurant database into the freeze dir with a sha256."""
    src = Path(db_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    digest = _sha256_file(src)
    dest = out_dir / f"nile_bites.{digest[:12]}.json"
    shutil.copy2(src, dest)
    return {"source": str(src), "snapshot": str(dest), "sha256": digest}


def freeze_experiment(
    config,
    out_dir: str | Path,
    experiment_id: str,
    db_path: str | Path | None = None,
    prompt_path: str | Path | None = None,
    asr_failure_wer: float = 0.5,
    latency_failure_seconds: float = 120.0,
) -> ExperimentFreeze:
    """Snapshot every frozen input. Reads config, never modifies it."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    asr_section = config.section("asr") or {}
    llm_section = config.section("llm") or {}
    tts_section = config.section("tts") or {}

    def _describe(provider: str, section: dict) -> dict[str, Any]:
        sub = section.get(provider) or {}
        return {
            "provider": provider,
            "model": sub.get("model", section.get("model")),
            "settings": {k: v for k, v in sub.items()},
        }

    asr_provider = str(asr_section.get("provider", "unknown"))
    llm_provider = str(llm_section.get("provider", "unknown"))
    tts_provider = str(tts_section.get("provider", "unknown"))

    db_info: dict[str, Any] = {}
    if db_path:
        db_info = snapshot_database(db_path, out / "database")

    prompts_info: dict[str, Any] = {}
    if prompt_path:
        prompt_path = Path(prompt_path)
        prompts_info = {
            "file": str(prompt_path),
            "sha256": _sha256_file(prompt_path),
        }
        shutil.copy2(prompt_path, out / prompt_path.name)

    try:
        from .conditions import load_conditions

        conditions_snapshot = load_conditions(config).to_dict()
    except Exception:
        conditions_snapshot = {}

    freeze = ExperimentFreeze(
        experiment_id=experiment_id,
        architectures=[
            {"id": "baseline", "description": "sequential: full utterance ASR -> LLM -> tools -> complete TTS -> playback"},
            {"id": "streaming", "description": "intervention: chunked ASR -> partial/committed/final hypotheses -> incremental understanding -> speculative tools -> streaming LLM -> streaming TTS"},
        ],
        asr=_describe(asr_provider, asr_section),
        llm=_describe(llm_provider, llm_section),
        tts=_describe(tts_provider, tts_section),
        beam={"greedy": 1, "beam5": 5, "frozen_default": 1,
              "note": "beam-5 stays opt-in; default unchanged"},
        database=db_info,
        prompts=prompts_info,
        thresholds={"asr_failure_wer": float(asr_failure_wer),
                    "latency_failure_seconds": float(latency_failure_seconds)},
        software={
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": _pip_versions(["faster-whisper", "openai", "edge-tts",
                                       "numpy", "soundfile", "scipy", "flask"]),
        },
        conditions_snapshot=conditions_snapshot,
        created_utc=_utc_now_iso(),
    )
    with open(out / "freeze.json", "w", encoding="utf-8") as fh:
        json.dump(freeze.to_dict(), fh, ensure_ascii=False, indent=2)
    return freeze


# ---------------------------------------------------------------- trials

@dataclass
class TrialSpec:
    """One experiment trial: a task plus its frozen utterance audio.

    ``task_id`` must resolve against the task set; ``audio_path`` (or
    ``audio_paths`` for multi-turn trials) points at the frozen utterance
    recording. ``speaker`` carries only permitted metadata (pilot: synthetic
    voice descriptors; human data requires separate consent handling and is
    out of scope here). ``verified_english_pct`` is measured from the
    VERIFIED REFERENCE transcript with the locked definition.
    """

    trial_id: str
    task_id: str
    audio_path: str | None = None
    audio_paths: list[str] = field(default_factory=list)
    speaker: dict[str, Any] = field(default_factory=dict)
    verified_english_pct: float | None = None
    declared_condition: str | None = None
    note: str = ""

    def audio_files(self) -> list[str]:
        if self.audio_paths:
            return list(self.audio_paths)
        if self.audio_path:
            return [self.audio_path]
        return []

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "task_id": self.task_id,
            "audio_path": self.audio_path,
            "audio_paths": list(self.audio_paths),
            "speaker": dict(self.speaker),
            "verified_english_pct": self.verified_english_pct,
            "declared_condition": self.declared_condition,
            "note": self.note,
        }


def trial_from_dict(raw: dict[str, Any]) -> TrialSpec:
    return TrialSpec(
        trial_id=str(raw.get("trial_id", "")),
        task_id=str(raw.get("task_id", "")),
        audio_path=raw.get("audio_path"),
        audio_paths=list(raw.get("audio_paths") or []),
        speaker=dict(raw.get("speaker") or {}),
        verified_english_pct=(float(raw["verified_english_pct"])
                              if raw.get("verified_english_pct") is not None else None),
        declared_condition=raw.get("declared_condition"),
        note=str(raw.get("note", "")),
    )


def load_trials(path: str | Path) -> list[TrialSpec]:
    import yaml

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    trials = raw.get("trials", raw if isinstance(raw, list) else [])
    return [trial_from_dict(t) for t in trials]


# ---------------------------------------------------------------- validation

def _has_ground_truth(task: Any) -> bool:
    return bool(
        getattr(task, "expected_lines", None)
        or getattr(task, "expected_total", None) is not None
        or getattr(task, "expected_subtotal", None) is not None
        or getattr(task, "expected_delivery_fee", None) is not None
        or getattr(task, "expected_zone_id", None)
        or getattr(task, "expected_order_status", None)
        or getattr(task, "expected_tools", None)
        or getattr(task, "expected_facts", None)
        or getattr(task, "expected_error", None)
        or getattr(task, "expect_no_error", False)
        or getattr(task, "expect_no_order", False)
    )


def validate_dataset(
    trials: list[TrialSpec],
    tasks_by_id: dict[str, Any],
    conditions: Any = None,
    measure_fn: Callable[[str], float] | None = None,
    audio_root: str | Path | None = None,
) -> dict[str, Any]:
    """Flag dataset problems. Pure validation: changes nothing.

    Flags: duplicate trial/task ids, unresolvable task_id, missing reference
    transcript, missing ground truth, missing audio file, declared C-level
    disagreeing with the measured reference percentage, unknown condition id,
    non-scalar speaker metadata.
    """
    flags: dict[str, list[str]] = {}
    seen_trials: set[str] = set()
    seen_tasks: set[str] = set()
    root = Path(audio_root) if audio_root else None

    def add(trial_id: str, msg: str) -> None:
        flags.setdefault(trial_id or "?", []).append(msg)

    for trial in trials:
        tid = trial.trial_id or "?"
        if not trial.trial_id:
            add(tid, "missing trial_id")
        elif trial.trial_id in seen_trials:
            add(tid, f"duplicate trial_id {trial.trial_id!r}")
        else:
            seen_trials.add(trial.trial_id)
        if trial.task_id in seen_tasks:
            add(tid, f"duplicate task_id {trial.task_id!r}")
        else:
            seen_tasks.add(trial.task_id)

        task = tasks_by_id.get(trial.task_id)
        if task is None:
            add(tid, f"task_id {trial.task_id!r} resolves to no known task")
            continue

        turns = getattr(task, "turns", []) or []
        refs = [str(getattr(t, "reference_transcript", "") or "").strip() for t in turns]
        if not any(refs):
            add(tid, "missing reference transcript (all turns empty)")
        if not _has_ground_truth(task):
            add(tid, "missing ground truth (no expected state/tools/facts/error assertions)")

        for audio in trial.audio_files():
            p = Path(audio)
            if root is not None and not p.is_absolute():
                p = root / p
            if not p.exists():
                add(tid, f"missing audio file: {audio}")

        if trial.declared_condition and conditions is not None:
            band = conditions.band(trial.declared_condition)
            if band is None:
                add(tid, f"unknown declared condition {trial.declared_condition!r}")
            elif trial.verified_english_pct is not None and measure_fn is None:
                if not conditions.contains(trial.declared_condition, trial.verified_english_pct):
                    add(tid, f"declared {trial.declared_condition} disagrees with verified "
                             f"{trial.verified_english_pct:.2f}%")
        if trial.verified_english_pct is None:
            add(tid, "missing verified_english_pct (measure from reference before freezing)")

        for key, value in (trial.speaker or {}).items():
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                add(tid, f"speaker metadata field {key!r} is not a scalar")

    # Declared-vs-measured cross-check on references (locked definition).
    if measure_fn is not None and conditions is not None:
        for trial in trials:
            task = tasks_by_id.get(trial.task_id)
            if task is None:
                continue
            for turn in getattr(task, "turns", []) or []:
                ref = str(getattr(turn, "reference_transcript", "") or "").strip()
                if not ref:
                    continue
                pct = float(measure_fn(ref))
                declared = (getattr(turn, "declared_condition", None)
                            or getattr(task, "declared_condition", None)
                            or trial.declared_condition)
                if declared and not conditions.contains(declared, pct):
                    flags.setdefault(trial.trial_id, []).append(
                        f"turn {getattr(turn, 'index', '?')}: declared {declared} "
                        f"disagrees with measured {pct:.2f}% on the reference")

    return {"flags": flags, "clean": not flags,
            "trial_count": len(trials), "flagged_count": len(flags)}


def pilot_distribution(verified: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize verified percentages per declared band.

    Reporting only: per-band count/min/max/mean of verified English
    percentages. Choosing new boundaries stays a researcher decision;
    this output is the evidence that decision inspects.
    """
    by_band: dict[str, list[float]] = {}
    for row in verified:
        band = str(row.get("declared_condition", "?")).upper()
        try:
            by_band.setdefault(band, []).append(float(row["verified_english_pct"]))
        except (TypeError, ValueError):
            continue
    summary: dict[str, Any] = {}
    for band in sorted(by_band):
        values = sorted(by_band[band])
        summary[band] = {
            "n": len(values),
            "min": round(values[0], 2),
            "max": round(values[-1], 2),
            "mean": round(sum(values) / len(values), 2),
        }
    return {"per_band": summary, "total": sum(len(v) for v in by_band.values())}


# ---------------------------------------------------------------- export

def write_jsonl(records: list[dict[str, Any]], path: str | Path) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return str(path)


CSV_COLUMNS = [
    "experiment_id", "architecture", "trial_id", "task_id", "task_family",
    "declared_condition", "measured_condition", "condition_consistent",
    "verified_english_pct", "measured_english_pct",
    "reference_transcript", "asr_transcript", "wer",
    "task_success", "task_score", "failure_category",
    "speech_end_utc", "response_audio_utc", "latency_s",
    "asr_ms", "llm_ms", "tools_ms", "tts_ms",
    "tool_calls", "error",
]


def records_to_csv(records: list[dict[str, Any]], path: str | Path) -> str:
    import csv as _csv

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def _flat(record: dict[str, Any], column: str) -> Any:
        if column in record:
            value = record[column]
        else:
            value = None
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, default=str)
        return value

    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = _csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for record in records:
            writer.writerow({c: _flat(record, c) for c in CSV_COLUMNS})
    return str(path)
