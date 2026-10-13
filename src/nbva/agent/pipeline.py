"""The baseline pipeline.

    User speech
    -> ASR
    -> Language understanding / LLM (with deterministic business tools)
    -> Business database + tools
    -> Response generation
    -> TTS
    -> User

Every stage is an injected object implementing a small interface, so any stage
can be replaced without touching the others. That is what the intervention will
need: a targeted architecture will change or add stages, and must be able to do
so against the same tools, state, logging and evaluation.

The pipeline also stamps the latency markers. The primary metric is the locked
definition:

    latency = response_audio_start - speech_end

where ``speech_end`` is the VAD endpoint and ``response_audio_start`` is when the
first audio sample is queued for playback.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..asr.base import ASRProvider, audio_fingerprint, write_wav
from ..llm.base import LLMProvider, run_tool_loop
from ..obs.events import LatencyMarkers, TurnRecord, utc_now_iso
from ..research.codeswitch import CodeSwitchAnalyzer
from ..research.conditions import ConditionSet
from ..research.wer import WERConfig, compute_wer
from ..tts.base import TTSProvider
from .session import Session

# Numbers that look like prices, times or counts in the response text.
_MONEY_RE = re.compile(r"\b(\d{1,5})\s*(?:جنيه|EGP|LE|le|pounds?)?\b", re.IGNORECASE)
_TIME_RE = re.compile(r"\b(\d{1,3})\s*(?:دقيقة|دقايق|minutes?|mins?)\b", re.IGNORECASE)


@dataclass
class PipelineResult:
    turn: TurnRecord
    response_text: str = ""
    asr_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    final_state: dict[str, Any] | None = None
    error: str | None = None


class BaselinePipeline:
    def __init__(
        self,
        session: Session,
        asr: ASRProvider,
        llm: LLMProvider,
        tts: TTSProvider,
        system_prompt: str,
        analyzer: CodeSwitchAnalyzer,
        conditions: ConditionSet,
        wer_config: WERConfig | None = None,
        enforce_grounding: bool = True,
        max_tool_iterations: int = 6,
        player: Any = None,
        logger: Any = None,
        save_audio: bool = True,
        audio_dir: str | Path | None = None,
    ):
        self.session = session
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.system_prompt = system_prompt
        self.analyzer = analyzer
        self.conditions = conditions
        self.wer_config = wer_config or WERConfig()
        self.enforce_grounding = enforce_grounding
        self.max_tool_iterations = max_tool_iterations
        self.player = player
        self.logger = logger
        self.save_audio = save_audio
        self.audio_dir = Path(audio_dir) if audio_dir else None

    # ------------------------------------------------------------------
    def process_turn(
        self,
        audio: np.ndarray | None = None,
        text: str | None = None,
        reference_transcript: str | None = None,
        declared_condition: str | None = None,
        task_family: str | None = None,
        task_id: str | None = None,
        trial_id: str | None = None,
        speech_end_already_stamped: bool = False,
        speech_start_already_stamped: bool = False,
        speak: bool = True,
        extra: dict[str, Any] | None = None,
        on_audio_start: Any = None,
    ) -> PipelineResult:
        """Run one full turn. Either ``audio`` or ``text`` must be supplied.

        ``on_audio_start`` is invoked the instant the first audio sample reaches
        the output device. That instant is the endpoint of the locked primary
        latency metric, so the live loop supplies a callback to capture the real
        device moment rather than the moment playback was requested.
        """
        if audio is None and text is None:
            raise ValueError("process_turn needs either audio or text")

        lat = LatencyMarkers()
        if not speech_start_already_stamped:
            lat.stamp_speech_start()
        if not speech_end_already_stamped:
            lat.stamp_speech_end(
                utterance_ms=(len(audio) / float(self.asr.sample_rate) * 1000.0)
                if audio is not None and getattr(self.asr, "sample_rate", None)
                else None
            )

        turn_index = self.session.next_turn_index()
        record = TurnRecord(
            session_id=self.session.session_id,
            turn_index=turn_index,
            trial_id=trial_id or self.session.current_trial_id,
            task_id=task_id or getattr(self.session.current_task, "task_id", None),
            task_family=task_family or getattr(self.session.current_task, "family", None),
            experiment_id=self.session.experiment_id,
            is_research_run=self.session.is_research_run,
            reference_transcript=reference_transcript,
            business_weekday=self.session.business_weekday,
            extra=dict(extra or {}),
        )
        record.latency = lat
        self.session.take_tool_calls()

        # ---- ASR ----
        asr_text = ""
        asr_error: str | None = None
        if audio is not None and audio.size:
            if self.save_audio and self.audio_dir:
                path = self.audio_dir / f"{self.session.session_id}_turn{turn_index:03d}.wav"
                try:
                    write_wav(path, audio, int(self.asr.sample_rate))
                    record.audio_path = str(path)
                except Exception:
                    pass
            record.audio_sample_rate = int(self.asr.sample_rate)
            record.audio_duration_ms = round(len(audio) / float(self.asr.sample_rate) * 1000.0, 2)
            record.extra["audio_sha256_16"] = audio_fingerprint(audio)

            lat.stamp_asr_start()
            asr_result = self.asr.transcribe(audio)
            lat.stamp_asr_end()
            asr_text = asr_result.text or ""
            asr_error = asr_result.error
            record.asr_provider = getattr(self.asr, "name", None)
            record.asr_model = getattr(self.asr, "model", None)
            record.asr_language = asr_result.language
            # Freeze the decoding configuration alongside the result. A provider
            # can change behaviour under an unchanged model name, and a later
            # WER regression is uninterpretable without this.
            record.asr_config = _describe_component(self.asr)
            record.asr_model_version = asr_result.model_version
        else:
            lat.stamp_asr_start()
            lat.stamp_asr_end()
            asr_text = text or ""
            record.asr_provider = "typed_input"
            record.asr_model = None

        if self._should_lowercase():
            asr_text = asr_text.lower()
        record.asr_transcript = asr_text
        if asr_error:
            record.notes = f"asr error: {asr_error}"

        # ---- code-switching measure, on the REFERENCE when available ----
        # The manipulated variable is a property of what was said, so it is
        # measured on the reference transcript when one exists. Measuring the
        # ASR output instead would confound condition with recognition error.
        measure_source = "reference_transcript"
        measure_text = reference_transcript or ""
        if not measure_text:
            measure_source = "asr_transcript"
            measure_text = asr_text
        trace = self.analyzer.analyze(measure_text)
        record.codeswitch_measure_source = measure_source
        record.english_pct = round(trace.english_pct, 4)
        record.english_lexical_tokens = trace.english_lexical_tokens
        record.total_lexical_tokens = trace.total_lexical_tokens
        record.non_english_lexical_tokens = trace.non_english_lexical_tokens
        if self.analyzer.store_token_trace:
            record.codeswitch_token_trace = trace.to_dict(include_tokens=True).get("tokens")

        cond = self.conditions.evaluate(
            trace.english_pct, declared=declared_condition
        )
        record.measured_condition = cond.measured
        if declared_condition is None:
            record.declared_condition = cond.declared
        record.condition_warnings = list(cond.warnings)
        record.condition_consistent = bool(cond.consistent)

        # ---- WER ----
        if reference_transcript:
            wer = compute_wer(reference_transcript, asr_text, self.wer_config)
            record.wer = round(wer.wer, 6)
            record.wer_detail = wer.to_dict()

        # ---- understanding + tools ----
        self.session.add_turn("user", asr_text, utc_now_iso())
        lat.stamp_nlu_start()
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self.session.message_history()[-self.session.max_history_turns :])
        lat.stamp_tools_start()
        response_text, _, tool_calls, llm_error = run_tool_loop(
            self.llm, self.session.tools, messages, self.max_tool_iterations
        )
        lat.stamp_nlu_end()
        lat.stamp_tools_end()
        # The tool loop interleaves model calls with dispatches, so the wall time
        # between the two stamps also contains generation. Sum the per-call
        # durations the registry recorded to get actual tool execution time.
        lat.tools_ms = round(
            sum(float(c.get("duration_ms") or 0.0) for c in tool_calls), 3
        )
        lat.stamp_response_text_ready()

        record.llm_provider = getattr(self.llm, "name", None)
        record.llm_model = getattr(self.llm, "model", None)
        record.llm_config = _describe_component(self.llm)
        # Hosted endpoints are addressed by a name whose weights can change
        # underneath it, so the reported version is kept separate from the
        # requested model name.
        record.llm_model_version = getattr(self.llm, "model_version", None)
        record.tool_calls = tool_calls
        record.tool_call_count = len(tool_calls)
        record.tool_error_codes = [
            str(c.get("error_code")) for c in tool_calls if c.get("error_code")
        ]
        record.database_results = [
            c.get("result") for c in tool_calls if str(c.get("name", "")).startswith(
                ("get_", "search_", "check_", "calculate_", "resolve_")
            )
        ]
        record.response_text = response_text
        record.response_language_detected = self._detect_response_language(response_text)

        self.session.add_turn("assistant", response_text, utc_now_iso())

        # ---- grounding check ----
        if self.enforce_grounding and response_text:
            record.grounding_violations = self._check_grounding(response_text, tool_calls)

        # ---- final state ----
        final_state = self.session.current_order()
        record.final_state = final_state
        record.final_system_action = self._summarize_action(tool_calls, final_state)

        # ---- TTS + playback ----
        # Route the reply to the voice matching its language: an Arabic voice
        # reading English (or vice versa) sounds wrong, so English replies use
        # the configured English fallback voice. Mixed replies stay on the
        # default voice, which is chosen to handle both languages.
        tts_voice: str | None = None
        if getattr(self.tts, "name", None) == "edge":
            if self._detect_response_language(response_text) == "en":
                tts_voice = getattr(self.tts, "fallback_voice", None) or None
        tts_kwargs: dict[str, Any] = {}
        if tts_voice is not None:
            try:
                accepts_voice = "voice" in inspect.signature(self.tts.synthesize).parameters
            except (TypeError, ValueError):
                accepts_voice = False
            if accepts_voice:
                tts_kwargs["voice"] = tts_voice
        tts_error: str | None = None
        if speak and response_text and getattr(self.tts, "name", None) != "null":
            lat.stamp_tts_synthesis_start()
            tts_result = self.tts.synthesize(response_text, **tts_kwargs)
            lat.stamp_tts_synthesis_end()
            tts_error = tts_result.error
            if tts_error:
                record.notes = (record.notes + f" | tts error: {tts_error}").strip(" |")
            elif tts_result.audio is not None and self.player is not None:
                # Stamp the latency endpoint at the true first sample when the
                # caller supplied a hook, otherwise at the moment of playback.
                hook = on_audio_start or lat.stamp_response_audio_start
                try:
                    self.player.play(
                        tts_result.audio,
                        tts_result.sample_rate,
                        on_first_sample=hook,
                    )
                    lat.stamp_response_audio_end()
                except Exception as exc:
                    record.notes = (record.notes + f" | playback error: {exc}").strip(" |")
        else:
            # No audio path: latency is not measurable, so it is left unset
            # rather than filled with a meaningless number.
            record.notes = (record.notes + " | no audio output; latency not measured").strip(" |")

        error = llm_error or asr_error or tts_error
        return PipelineResult(
            turn=record,
            response_text=response_text,
            asr_text=asr_text,
            tool_calls=tool_calls,
            final_state=final_state,
            error=error,
        )

    # ------------------------------------------------------------------
    def _should_lowercase(self) -> bool:
        return False  # controlled by config in the caller if ever needed

    def _detect_response_language(self, text: str) -> str | None:
        if not text:
            return None
        has_ar = bool(re.search(r"[\u0600-\u06FF]", text))
        has_lat = bool(re.search(r"[A-Za-z]", text))
        if has_ar and has_lat:
            return "mixed"
        if has_ar:
            return "ar"
        if has_lat:
            return "en"
        return None

    def _check_grounding(
        self, response_text: str, tool_calls: list[dict[str, Any]]
    ) -> list[str]:
        """Flag numbers in the response that no tool ever returned.

        This is a safety net for the rule "the model must not invent business
        facts". It cannot prove the absence of an error, but a violation here is
        a real and logged symptom of a hallucinated price or time.
        """
        if not tool_calls:
            # No tool output at all: any number in the reply is unsourced.
            return self._numbers_in(response_text)

        allowed: set[str] = set()
        import json

        for call in tool_calls:
            blob = json.dumps(call.get("result"), ensure_ascii=False, default=str)
            for m in re.findall(r"\d+", blob):
                allowed.add(m.lstrip("0") or "0")
                allowed.add(m)

        violations: list[str] = []
        for num in self._numbers_in(response_text):
            if num in allowed:
                continue
            if num.lstrip("0") in allowed:
                continue
            violations.append(num)
        return violations

    def _numbers_in(self, text: str) -> list[str]:
        found: list[str] = []
        for pattern in (_MONEY_RE, _TIME_RE):
            for m in pattern.finditer(text or ""):
                val = m.group(1)
                norm = val.lstrip("0") or "0"
                if norm not in found:
                    found.append(norm)
        return found

    def _summarize_action(
        self, tool_calls: list[dict[str, Any]], final_state: dict[str, Any] | None
    ) -> str:
        if not tool_calls:
            return "no_tool_called"
        for call in tool_calls:
            if str(call.get("name")) in ("create_order", "modify_order", "cancel_order", "confirm_order") and call.get("ok"):
                return f"{call['name']}_ok"
        last = tool_calls[-1]
        return f"{last.get('name')}_{'ok' if last.get('ok') else 'error'}"


def _describe_component(component: Any) -> dict[str, Any] | None:
    """Snapshot a provider's own description for the trial record.

    Falls back to a minimal identity when a provider has no ``describe``, so a
    custom provider added later still leaves usable provenance rather than
    silently logging nothing.
    """
    describe = getattr(component, "describe", None)
    if callable(describe):
        try:
            snapshot = describe()
        except Exception:
            snapshot = None
        if isinstance(snapshot, dict):
            return dict(snapshot)
    return {
        "provider": getattr(component, "name", None),
        "model": getattr(component, "model", None),
    }
