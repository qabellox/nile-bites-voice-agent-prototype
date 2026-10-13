"""Controlled experiment runner: baseline vs streaming intervention.

Runs the SAME frozen utterance audio through both architectures and builds
one structured :class:`TrialRecord` per (trial, architecture). Reuses the
locked research instruments (WER, condition evaluation, task assertions,
failure taxonomy) so a record means the same thing regardless of arm.

Trial isolation: every trial gets a FRESH session (hence a fresh order
store and tool registry) while sharing the heavy providers (ASR/LLM/TTS),
so trials cannot leak business state into each other yet model-load cost
is paid once.

Batch audio mode: file runs have no VAD endpointing and no speaker
playback. ``audio_mode`` records exactly what the timestamps mean:
``response_audio_utc`` is when response audio was READY (synthesis done),
not played. Live runs stamp first-sample-to-device instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np

from ..agent.session import create_session
from ..asr.base import resample
from ..obs.events import utc_now_iso
from .codeswitch import CodeSwitchAnalyzer  # noqa: F401  (re-exported for callers)
from .conditions import ConditionSet
from .failure import classify_failure
from .taskdefs import TaskSpec, evaluate_task
from .wer import WERConfig, compute_wer


def load_utterance_audio(path: str | Path, target_rate: int) -> tuple[np.ndarray, int, float]:
    """Read an utterance file; return (mono float32 at target_rate, rate, seconds)."""
    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    data = np.ascontiguousarray(np.asarray(data, dtype=np.float32).reshape(-1))
    if int(sr) != int(target_rate):
        data = resample(data, int(sr), int(target_rate))
    return data, int(target_rate), float(data.size) / float(target_rate)


@dataclass
class TrialRecord:
    experiment_id: str = ""
    architecture: str = ""
    trial_id: str = ""
    task_id: str = ""
    task_family: str = ""
    declared_condition: str | None = None
    measured_condition: str | None = None
    condition_consistent: bool | None = None
    verified_english_pct: float | None = None
    measured_english_pct: float | None = None
    reference_transcript: str = ""
    asr_transcript: str = ""
    wer: float | None = None
    task_success: bool | None = None
    task_score: float | None = None
    failure_category: str | None = None
    failure_detail: dict[str, Any] = field(default_factory=dict)
    speech_end_utc: str | None = None
    response_audio_utc: str | None = None
    audio_mode: str = "batch-synth"
    latency_s: float | None = None
    asr_ms: float | None = None
    llm_ms: float | None = None
    tools_ms: float | None = None
    tts_ms: float | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "architecture": self.architecture,
            "trial_id": self.trial_id,
            "task_id": self.task_id,
            "task_family": self.task_family,
            "declared_condition": self.declared_condition,
            "measured_condition": self.measured_condition,
            "condition_consistent": self.condition_consistent,
            "verified_english_pct": self.verified_english_pct,
            "measured_english_pct": self.measured_english_pct,
            "reference_transcript": self.reference_transcript,
            "asr_transcript": self.asr_transcript,
            "wer": self.wer,
            "task_success": self.task_success,
            "task_score": self.task_score,
            "failure_category": self.failure_category,
            "failure_detail": dict(self.failure_detail),
            "speech_end_utc": self.speech_end_utc,
            "response_audio_utc": self.response_audio_utc,
            "audio_mode": self.audio_mode,
            "latency_s": self.latency_s,
            "asr_ms": self.asr_ms,
            "llm_ms": self.llm_ms,
            "tools_ms": self.tools_ms,
            "tts_ms": self.tts_ms,
            "tool_calls": [dict(c) for c in self.tool_calls],
            "turns": [dict(t) for t in self.turns],
            "error": self.error,
        }


@dataclass
class ExperimentRunner:
    """Runs frozen trials on both architectures with shared providers."""

    app: Any = None
    analyzer: Any = None
    conditions: ConditionSet | None = None
    wer_config: WERConfig | None = None
    experiment_id: str = "unassigned"
    asr_failure_wer: float = 0.5
    latency_failure_seconds: float = 120.0
    trace_dir: Any = None

    def _fresh_session(self):
        src = self.app.session
        return create_session(
            repository=self.app.repository,
            experiment_id=self.experiment_id,
            is_research_run=False,
            participant_id="",
            business_weekday=src.business_weekday,
        )

    def _fresh_pipeline(self, session):
        from ..agent.pipeline import BaselinePipeline

        p = self.app.pipeline
        return BaselinePipeline(
            session=session, asr=p.asr, llm=p.llm, tts=p.tts,
            system_prompt=p.system_prompt, analyzer=p.analyzer,
            conditions=p.conditions, wer_config=p.wer_config,
            enforce_grounding=p.enforce_grounding,
            max_tool_iterations=p.max_tool_iterations,
            player=None, logger=None, save_audio=False, audio_dir=None,
        )

    def _streaming_loop(self, session):
        from ..stream.loop import StreamingTurnLoop

        p = self.app.pipeline
        app_ns = SimpleNamespace(
            session=session,
            pipeline=SimpleNamespace(asr=p.asr, analyzer=p.analyzer,
                                     system_prompt=p.system_prompt),
            llm=p.llm, tts=p.tts,
        )
        return StreamingTurnLoop(app_ns, trace_dir=None)

    def _score_turn(self, reference: str, asr_text: str, declared: str | None):
        wer_val: float | None = None
        try:
            wer_val = round(compute_wer(reference or "", asr_text or "",
                                        self.wer_config).wer, 4)
        except Exception:
            wer_val = None
        measured = measured_pct = consistent = None
        try:
            trace = self.analyzer.analyze(reference or "")
            measured_pct = round(float(trace.english_pct), 4)
            cond = self.conditions.evaluate(measured_pct, declared)
            measured, consistent = cond.measured, cond.consistent
        except Exception:
            pass
        return wer_val, measured, measured_pct, consistent

    def run_baseline_audio(self, task: TaskSpec, audios: list[np.ndarray],
                           trial_id: str) -> dict[str, Any]:
        """One trial through the frozen sequential pipeline (file audio)."""
        from ..agent.pipeline import BaselinePipeline  # noqa: F401 (kept explicit)

        session = self._fresh_session()
        session.begin_trial(task, trial_id)
        pipeline = self._fresh_pipeline(session)
        asr_rate = int(getattr(pipeline.asr, "sample_rate", 16000) or 16000)

        turns: list[dict[str, Any]] = []
        all_tools: list[dict[str, Any]] = []
        last_text = ""
        tts_total = 0.0
        error: str | None = None
        for i, turn in enumerate(task.turns):
            audio = audios[min(i, len(audios) - 1)] if audios else None
            ref = (turn.reference_transcript or turn.utterance or "").strip()
            declared = turn.declared_condition or task.declared_condition
            try:
                res = pipeline.process_turn(
                    audio=np.ascontiguousarray(np.asarray(audio, dtype=np.float32).reshape(-1))
                    if audio is not None else None,
                    text=None if audio is not None else (turn.utterance or ""),
                    reference_transcript=ref or None,
                    declared_condition=declared,
                    task_family=task.family, task_id=task.task_id,
                    trial_id=trial_id, speak=True,
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                break
            lat = getattr(res.turn, "latency", None)
            turns.append({
                "index": i,
                "reference": ref,
                "asr": res.asr_text or "",
                "response": res.response_text or "",
                "tool_calls": [c.get("name") for c in (res.tool_calls or [])],
                "asr_ms": getattr(lat, "asr_ms", None),
                "nlu_ms": getattr(lat, "nlu_ms", None),
                "tools_ms": getattr(lat, "tools_ms", None),
                "tts_ms": getattr(lat, "tts_synthesis_ms", None),
                "error": res.error,
            })
            all_tools.extend(res.tool_calls or [])
            last_text = res.response_text or last_text
            tts_total += float(getattr(lat, "tts_synthesis_ms", 0.0) or 0.0)
            if res.error and error is None:
                error = res.error

        order = session.current_order()
        order_dict = dict(order) if isinstance(order, dict) else None
        if order is not None and order_dict is None:
            try:
                order_dict = {"order_id": order.order_id, "status": order.status,
                              "total": order.total,
                              "lines": [{"item_id": l.item_id, "quantity": l.quantity}
                                        for l in (order.lines or [])]}
            except Exception:
                order_dict = None
        evaluation = evaluate_task(task, order_dict, all_tools, last_text)
        summary = {
            "turns": turns, "tool_calls": all_tools, "response_text": last_text,
            "final_state": order_dict, "evaluation": evaluation,
            "tts_total_ms": round(tts_total, 1), "error": error,
        }
        _ = asr_rate
        return summary

    def _streaming_loop(self, session):
        from types import SimpleNamespace

        from ..stream.loop import StreamingTurnLoop

        p = self.app.pipeline
        return StreamingTurnLoop(
            SimpleNamespace(
                session=session,
                pipeline=SimpleNamespace(asr=p.asr, analyzer=p.analyzer,
                                         system_prompt=p.system_prompt),
                llm=p.llm, tts=p.tts),
            trace_dir=self.trace_dir)

    def run_streaming_audio(self, task: TaskSpec, audios: list[np.ndarray],
                            trial_id: str) -> dict[str, Any]:
        """One trial through the streaming intervention (file audio)."""
        session = self._fresh_session()
        session.begin_trial(task, trial_id)
        loop = self._streaming_loop(session)
        asr_rate = int(getattr(self.app.pipeline.asr, "sample_rate", 16000) or 16000)

        turns: list[dict[str, Any]] = []
        all_tools: list[dict[str, Any]] = []
        last_text = ""
        error: str | None = None
        events: list[dict[str, Any]] = []
        revisions = 0
        first_audio_ms: float | None = None
        endpoint_ms: float | None = None
        for i, turn in enumerate(task.turns):
            audio = audios[min(i, len(audios) - 1)] if audios else np.zeros(0, dtype=np.float32)
            ref = (turn.reference_transcript or turn.utterance or "").strip()
            try:
                out = loop.run_clip(
                    np.ascontiguousarray(np.asarray(audio, dtype=np.float32).reshape(-1)),
                    asr_rate, realtime=False)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                break
            events.extend([{**e, "turn": i} for e in out.get("events", [])])
            revisions += int(out.get("revisions", 0) or 0)
            turns.append({
                "index": i,
                "reference": ref,
                "asr": out.get("final_transcript", ""),
                "response": out.get("response_text", ""),
                "tool_calls": [c.get("name") for c in (out.get("tool_calls") or [])],
                "endpoint_ms": out.get("endpoint_ms"),
                "first_audio_ms": out.get("first_audio_ms"),
                "revisions": out.get("revisions", 0),
            })
            all_tools.extend(out.get("tool_calls", []) or [])
            last_text = out.get("response_text", "") or last_text
            if out.get("first_audio_ms") is not None and first_audio_ms is None:
                first_audio_ms = out["first_audio_ms"]
            if out.get("endpoint_ms") is not None and endpoint_ms is None:
                endpoint_ms = out["endpoint_ms"]

        order = session.current_order()
        order_dict = dict(order) if isinstance(order, dict) else None
        if order is not None and order_dict is None:
            try:
                order_dict = {"order_id": order.order_id, "status": order.status,
                              "total": order.total,
                              "lines": [{"item_id": l.item_id, "quantity": l.quantity}
                                        for l in (order.lines or [])]}
            except Exception:
                order_dict = None
        evaluation = evaluate_task(task, order_dict, all_tools, last_text)
        return {"turns": turns, "tool_calls": all_tools, "response_text": last_text,
                "final_state": order_dict, "evaluation": evaluation,
                "events": events, "revisions": revisions,
                "first_audio_ms": first_audio_ms, "endpoint_ms": endpoint_ms,
                "error": error}

    def run_trial(self, trial, task: TaskSpec,
                  audio_loader: Callable[[str], tuple[np.ndarray, int]]
                  | None = None) -> list[TrialRecord]:
        """Run one trial on both architectures. Returns [baseline, streaming]."""
        paths = trial.audio_files()
        n_turns = max(1, len(task.turns))
        if len(paths) == 1:
            paths = paths * n_turns
        audios: list[np.ndarray] = []
        for n, path in enumerate(paths[:n_turns]):
            if audio_loader is not None:
                data, _sr = audio_loader(path)
            else:
                asr_rate = int(getattr(self.app.pipeline.asr, "sample_rate", 16000) or 16000)
                data, _sr, _dur = load_utterance_audio(path, asr_rate)
            audios.append(data)

        records: list[TrialRecord] = []
        for arch, fn in (("baseline", self.run_baseline_audio),
                         ("streaming", self.run_streaming_audio)):
            trial_id = f"{trial.trial_id}-{arch}"
            try:
                summary = fn(task, audios, trial_id)
                error = summary.get("error")
            except Exception as exc:
                summary = {"turns": [], "tool_calls": [], "response_text": "",
                           "final_state": None,
                           "evaluation": None, "error": f"{type(exc).__name__}: {exc}"}
                error = summary["error"]

            for t in summary["turns"]:
                try:
                    t["wer"] = round(compute_wer(
                        t.get("reference", ""), t.get("asr", ""),
                        self.wer_config).wer, 4)
                except Exception:
                    t["wer"] = None
            refs = [t["reference"] for t in summary["turns"]]
            asrs = [t["asr"] for t in summary["turns"]]
            reference = " / ".join(r for r in refs if r)
            asr_text = " / ".join(a for a in asrs if a)
            declared = trial.declared_condition or task.declared_condition
            wer_val, measured, measured_pct, consistent = self._score_turn(
                reference, asr_text, declared)

            evaluation = summary.get("evaluation")
            success = bool(evaluation.success) if evaluation else False
            score = float(evaluation.score) if evaluation else 0.0

            if arch == "baseline":
                asr_ms = sum(float(t.get("asr_ms") or 0.0) for t in summary["turns"])
                llm_ms = sum(float(t.get("nlu_ms") or 0.0) for t in summary["turns"])
                tools_ms = sum(float(t.get("tools_ms") or 0.0) for t in summary["turns"])
                per_turn_tts = sum(float(t.get("tts_ms") or 0.0) for t in summary["turns"])
                tts_ms = float(summary.get("tts_total_ms", 0.0) or 0.0) or per_turn_tts
                latency_s = round((asr_ms + llm_ms + tts_ms) / 1000.0, 3)
            else:
                asr_ms = llm_ms = tools_ms = None
                tts_ms = None
                first_audio = summary.get("first_audio_ms")
                endpoint = summary.get("endpoint_ms")
                if first_audio is not None and endpoint is not None:
                    latency_s = round(max(0.0, (first_audio - endpoint)) / 1000.0, 3)
                else:
                    latency_s = None

            now = utc_now_iso()
            failure = classify_failure(
                success=success, wer=wer_val,
                asr_failure_threshold=float(getattr(self, "asr_failure_wer", 0.5)),
                latency_seconds=latency_s,
                latency_failure_seconds=float(getattr(self, "latency_failure_seconds", 120.0)),
                tool_calls=summary.get("tool_calls", []),
                final_state_ok=(success or None),
                expected_state=None, actual_state=summary.get("final_state"),
                response_text=summary.get("response_text", ""),
                asr_transcript=asr_text, reference_transcript=reference,
                llm_error=error if error and "LLM" in (error or "") else None,
            )
            records.append(TrialRecord(
                experiment_id=self.experiment_id, architecture=arch,
                trial_id=trial.trial_id, task_id=trial.task_id,
                task_family=getattr(task, "family", ""),
                declared_condition=declared, measured_condition=measured,
                condition_consistent=consistent,
                verified_english_pct=(float(trial.verified_english_pct)
                                      if trial.verified_english_pct is not None else None),
                measured_english_pct=measured_pct,
                reference_transcript=reference, asr_transcript=asr_text,
                wer=wer_val, task_success=success, task_score=round(score, 4),
                failure_category=failure.category,
                failure_detail=failure.to_dict(),
                speech_end_utc=now, response_audio_utc=now,
                audio_mode="batch-synth",
                latency_s=latency_s, asr_ms=asr_ms, llm_ms=llm_ms,
                tools_ms=tools_ms, tts_ms=tts_ms,
                tool_calls=summary.get("tool_calls", []),
                turns=[dict(t) for t in summary["turns"]],
                error=error,
            ))
        return records

    def run_experiment(self, trials, tasks_by_id: dict[str, Any]) -> list[TrialRecord]:
        """Run every trial on both architectures. Trials must pass validation first."""
        records: list[TrialRecord] = []
        for trial in trials:
            task = tasks_by_id.get(trial.task_id)
            if task is None:
                records.append(TrialRecord(
                    experiment_id=self.experiment_id, architecture="both",
                    trial_id=trial.trial_id, task_id=trial.task_id,
                    error=f"unknown task_id {trial.task_id!r}"))
                continue
            records.extend(self.run_trial(trial, task))
        return records
