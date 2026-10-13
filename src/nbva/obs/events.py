"""Turn record schema.

One :class:`TurnRecord` is written per user turn. The field set covers every
quantity the methodology requires, so the log is directly loadable into a
dataframe for statistical analysis without a translation step.

Timestamps are stored three ways on purpose:

* ``*_utc``      wall clock, ISO-8601, for joining across machines.
* ``*_perf``     ``time.perf_counter()``, immune to clock adjustments, used to
                 compute durations inside one process.
* ``*_mono``     ``time.monotonic()``, used for cross-process latency spans.

Durations are precomputed in milliseconds so a reader never has to trust the
arithmetic.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "1.0.0"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def perf_now() -> float:
    return time.perf_counter()


def mono_now() -> float:
    return time.monotonic()


def ms(delta: float | None) -> float | None:
    if delta is None:
        return None
    return round(delta * 1000.0, 3)


@dataclass
class LatencyMarkers:
    """Every timestamp the latency definition needs.

    The LOCKED primary metric is ``speech_end_to_response_audio_start``:

        latency = response_audio_start_mono - speech_end_mono

    Component spans are diagnostic only.
    """

    # --- input side ---
    speech_start_mono: float | None = None
    speech_end_mono: float | None = None
    speech_start_utc: str | None = None
    speech_end_utc: str | None = None
    utterance_audio_ms: float | None = None

    # --- pipeline stages ---
    asr_start_mono: float | None = None
    asr_end_mono: float | None = None
    nlu_start_mono: float | None = None
    nlu_end_mono: float | None = None
    tools_start_mono: float | None = None
    tools_end_mono: float | None = None
    response_text_ready_mono: float | None = None
    tts_synthesis_start_mono: float | None = None
    tts_synthesis_end_mono: float | None = None

    # --- output side: this is the latency endpoint ---
    response_audio_start_mono: float | None = None
    response_audio_start_utc: str | None = None
    response_audio_end_mono: float | None = None

    # --- derived, in milliseconds ---
    primary_latency_ms: float | None = None
    asr_ms: float | None = None
    nlu_ms: float | None = None
    tools_ms: float | None = None
    response_generation_ms: float | None = None
    tts_synthesis_ms: float | None = None
    speech_duration_ms: float | None = None

    def stamp_speech_start(self) -> None:
        self.speech_start_mono = mono_now()
        self.speech_start_utc = utc_now_iso()

    def stamp_speech_end(self, utterance_ms: float | None = None) -> None:
        self.speech_end_mono = mono_now()
        self.speech_end_utc = utc_now_iso()
        self.utterance_audio_ms = utterance_ms
        if self.speech_start_mono is not None:
            self.speech_duration_ms = ms(self.speech_end_mono - self.speech_start_mono)

    def stamp_asr_start(self) -> None:
        self.asr_start_mono = mono_now()

    def stamp_asr_end(self) -> None:
        self.asr_end_mono = mono_now()
        if self.asr_start_mono is not None:
            self.asr_ms = ms(self.asr_end_mono - self.asr_start_mono)

    def stamp_nlu_start(self) -> None:
        self.nlu_start_mono = mono_now()

    def stamp_nlu_end(self) -> None:
        self.nlu_end_mono = mono_now()
        if self.nlu_start_mono is not None:
            self.nlu_ms = ms(self.nlu_end_mono - self.nlu_start_mono)

    def stamp_tools_start(self) -> None:
        self.tools_start_mono = mono_now()

    def stamp_tools_end(self) -> None:
        self.tools_end_mono = mono_now()
        if self.tools_start_mono is not None:
            self.tools_ms = ms(self.tools_end_mono - self.tools_start_mono)

    def stamp_response_text_ready(self) -> None:
        self.response_text_ready_mono = mono_now()
        if self.nlu_end_mono is not None:
            self.response_generation_ms = ms(
                self.response_text_ready_mono - self.nlu_end_mono
            )

    def stamp_tts_synthesis_start(self) -> None:
        self.tts_synthesis_start_mono = mono_now()

    def stamp_tts_synthesis_end(self) -> None:
        self.tts_synthesis_end_mono = mono_now()
        if self.tts_synthesis_start_mono is not None:
            self.tts_synthesis_ms = ms(self.tts_synthesis_end_mono - self.tts_synthesis_start_mono)

    def stamp_response_audio_start(self) -> None:
        """The latency endpoint. Called when the first audio sample is queued."""
        self.response_audio_start_mono = mono_now()
        self.response_audio_start_utc = utc_now_iso()
        if self.speech_end_mono is not None:
            self.primary_latency_ms = ms(
                self.response_audio_start_mono - self.speech_end_mono
            )

    def stamp_response_audio_end(self) -> None:
        self.response_audio_end_mono = mono_now()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TurnRecord:
    """One user turn and the agent's response to it."""

    # --- identity ---
    session_id: str
    turn_index: int
    trial_id: str | None = None
    task_id: str | None = None
    task_family: str | None = None
    experiment_id: str | None = None
    is_research_run: bool = False
    repetition: int = 1
    schema_version: str = SCHEMA_VERSION

    # --- timing ---
    timestamp_utc: str = field(default_factory=utc_now_iso)
    latency: LatencyMarkers = field(default_factory=LatencyMarkers)

    # --- audio ---
    audio_path: str | None = None
    audio_sample_rate: int | None = None
    audio_duration_ms: float | None = None
    tts_audio_path: str | None = None

    # --- language and code-switching ---
    reference_transcript: str | None = None
    asr_transcript: str | None = None
    asr_provider: str | None = None
    asr_model: str | None = None
    asr_language: str | None = None
    # Frozen model and decoding configuration. The provider can be upgraded
    # under the same model name, so the exact settings and any version string
    # the provider reports are stored next to the result. Without them a WER
    # regression cannot be attributed to the model or to the decoding knobs.
    asr_config: dict[str, Any] | None = None
    asr_model_version: str | None = None
    declared_condition: str | None = None
    measured_condition: str | None = None
    english_pct: float | None = None
    english_lexical_tokens: int | None = None
    total_lexical_tokens: int | None = None
    non_english_lexical_tokens: int | None = None
    codeswitch_measure_source: str | None = None
    codeswitch_token_trace: list[dict[str, Any]] | None = None
    condition_warnings: list[str] = field(default_factory=list)
    # Whether the measured band matched the authored one for this trial. A
    # false here is kept, not dropped: deciding whether an out-of-band trial
    # counts is a scientific decision, so the record has to survive to be
    # judged later.
    condition_consistent: bool | None = None

    # --- understanding and tools ---
    llm_provider: str | None = None
    llm_model: str | None = None
    # A hosted model is addressed by a name that can be repointed at different
    # weights without the name changing, so the provider-reported version is
    # recorded separately from the requested model.
    llm_model_version: str | None = None
    llm_config: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_count: int = 0
    tool_error_codes: list[str] = field(default_factory=list)
    database_results: list[Any] = field(default_factory=list)
    grounding_violations: list[str] = field(default_factory=list)

    # --- outcome ---
    final_system_action: str | None = None
    final_state: dict[str, Any] | None = None
    response_text: str | None = None
    response_language_detected: str | None = None

    # --- scoring ---
    task_success: bool | None = None
    task_score: float | None = None
    task_checks: list[dict[str, Any]] = field(default_factory=list)
    state_diff: str | None = None
    wer: float | None = None
    wer_detail: dict[str, Any] | None = None
    context_resolution_correct: bool | None = None
    database_tool_correct: bool | None = None
    success: bool | None = None
    failure_category: str | None = None
    failure_category_label: str | None = None
    failure_confidence: str | None = None
    failure_reasons: list[str] = field(default_factory=list)
    failure_overridden: bool = False

    # --- provenance ---
    business_weekday: str | None = None
    config_fingerprint: str | None = None
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["latency"] = self.latency.to_dict()
        return out
