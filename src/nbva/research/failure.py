"""Failure taxonomy and automatic classification.

The ten failure categories are fixed by the Day 1 methodology. This module
encodes them as constants and provides a rules-based first pass that assigns a
category from the observable evidence in a trial record.

The classifier is deliberately conservative. When the evidence does not support
a specific category it returns ``OTHER`` with a note rather than guessing, and
the researcher can override by hand. Auto-classification is a starting point for
human coding, not a substitute for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FailureCategory(str, Enum):
    NONE = "none"
    ASR_ERROR = "asr_error"
    INTENT_ERROR = "intent_understanding_error"
    ENTITY_ERROR = "entity_slot_error"
    DATABASE_ERROR = "database_retrieval_error"
    TOOL_CALL_ERROR = "tool_call_error"
    CONTEXT_ERROR = "context_resolution_error"
    ORDER_STATE_ERROR = "order_state_error"
    LATENCY_ERROR = "latency_timeout"
    RESPONSE_GEN_ERROR = "response_generation_error"
    OTHER = "other"


# Human-facing labels, kept out of the enum so the log values stay stable.
CATEGORY_LABELS = {
    FailureCategory.NONE.value: "no failure",
    FailureCategory.ASR_ERROR.value: "1. ASR error",
    FailureCategory.INTENT_ERROR.value: "2. Intent/understanding error",
    FailureCategory.ENTITY_ERROR.value: "3. Entity/slot error",
    FailureCategory.DATABASE_ERROR.value: "4. Database retrieval error",
    FailureCategory.TOOL_CALL_ERROR.value: "5. Tool-call error",
    FailureCategory.CONTEXT_ERROR.value: "6. Context-resolution error",
    FailureCategory.ORDER_STATE_ERROR.value: "7. Order-state error",
    FailureCategory.LATENCY_ERROR.value: "8. Latency/timeout",
    FailureCategory.RESPONSE_GEN_ERROR.value: "9. Response-generation error",
    FailureCategory.OTHER.value: "10. Other",
}

# Tool error codes that indicate the tool layer itself worked correctly, so a
# failure caused by them belongs to the model, not the database.
_MODEL_SIDE_ERRORS = {
    "UNKNOWN_ITEM",
    "UNKNOWN_OPTION",
    "UNKNOWN_ZONE",
    "UNKNOWN_DAY",
    "UNKNOWN_TOOL",
    "NO_MENU_MATCH",
    "BAD_ARGUMENT",
    "UNKNOWN_LINE",
    "NO_CURRENT_ORDER",
}


@dataclass
class FailureReport:
    category: str
    label: str
    confidence: str = "low"
    reasons: list[str] = field(default_factory=list)
    overridden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_category": self.category,
            "failure_category_label": self.label,
            "failure_confidence": self.confidence,
            "failure_reasons": list(self.reasons),
            "failure_overridden": self.overridden,
        }


def ok_report(note: str = "") -> FailureReport:
    return FailureReport(
        category=FailureCategory.NONE.value,
        label=CATEGORY_LABELS[FailureCategory.NONE.value],
        confidence="high",
        reasons=[note] if note else [],
    )


def classify_failure(
    *,
    success: bool,
    wer: float | None,
    asr_failure_threshold: float,
    latency_seconds: float | None,
    latency_failure_seconds: float,
    tool_calls: list[dict[str, Any]],
    final_state_ok: bool | None = None,
    expected_state: dict[str, Any] | None = None,
    actual_state: dict[str, Any] | None = None,
    response_text: str = "",
    asr_transcript: str = "",
    reference_transcript: str = "",
    llm_error: str | None = None,
    tts_error: str | None = None,
    manual_category: str | None = None,
) -> FailureReport:
    """Assign a failure category from the observable evidence of one trial."""
    if manual_category:
        return FailureReport(
            category=manual_category,
            label=CATEGORY_LABELS.get(manual_category, manual_category),
            confidence="manual",
            reasons=["manually assigned"],
            overridden=True,
        )

    if success:
        return ok_report("trial met its task assertions")

    reasons: list[str] = []

    # 1. infrastructure-level failures first: they explain everything else.
    if tts_error:
        return FailureReport(
            category=FailureCategory.RESPONSE_GEN_ERROR.value,
            label=CATEGORY_LABELS[FailureCategory.RESPONSE_GEN_ERROR.value],
            confidence="high",
            reasons=[f"tts error: {tts_error}"],
        )
    if llm_error:
        cat = (
            FailureCategory.INTENT_ERROR.value
            if "timeout" not in llm_error.lower()
            else FailureCategory.LATENCY_ERROR.value
        )
        return FailureReport(
            category=cat,
            label=CATEGORY_LABELS[cat],
            confidence="high",
            reasons=[f"llm error: {llm_error}"],
        )
    if latency_seconds is not None and latency_seconds > latency_failure_seconds:
        return FailureReport(
            category=FailureCategory.LATENCY_ERROR.value,
            label=CATEGORY_LABELS[FailureCategory.LATENCY_ERROR.value],
            confidence="high",
            reasons=[f"latency {latency_seconds:.2f}s exceeds {latency_failure_seconds}s"],
        )

    # 2. ASR failure. If the transcript is wrong, everything downstream is
    #    suspect, so ASR is reported as the primary cause.
    if (
        wer is not None
        and reference_transcript
        and wer > asr_failure_threshold
    ):
        return FailureReport(
            category=FailureCategory.ASR_ERROR.value,
            label=CATEGORY_LABELS[FailureCategory.ASR_ERROR.value],
            confidence="medium",
            reasons=[f"WER {wer:.3f} exceeds the ASR failure threshold {asr_failure_threshold}"],
        )

    # 3. tool-layer failures.
    failed_calls = [c for c in tool_calls if not c.get("ok", True)]
    error_codes = [str(c.get("error_code")) for c in failed_calls if c.get("error_code")]
    db_error_codes = {"DB_ERROR", "SCHEMA_ERROR", "TOOL_EXCEPTION"}
    if any(code in db_error_codes for code in error_codes):
        return FailureReport(
            category=FailureCategory.DATABASE_ERROR.value,
            label=CATEGORY_LABELS[FailureCategory.DATABASE_ERROR.value],
            confidence="high",
            reasons=[f"tool error codes: {error_codes}"],
        )
    if any(code == "UNKNOWN_TOOL" for code in error_codes):
        return FailureReport(
            category=FailureCategory.TOOL_CALL_ERROR.value,
            label=CATEGORY_LABELS[FailureCategory.TOOL_CALL_ERROR.value],
            confidence="high",
            reasons=["the model called a tool that does not exist"],
        )

    # 4. context resolution: the model failed to disambiguate a reference.
    for call in tool_calls:
        result = call.get("result") or {}
        if isinstance(result, dict) and result.get("ambiguous") and not result.get("line_ids_used"):
            return FailureReport(
                category=FailureCategory.CONTEXT_ERROR.value,
                label=CATEGORY_LABELS[FailureCategory.CONTEXT_ERROR.value],
                confidence="medium",
                reasons=[f"reference {result.get('reference')!r} was ambiguous and not resolved"],
            )

    # 5. state mismatch. Distinguish order-state from generic entity errors.
    if final_state_ok is False and expected_state and actual_state:
        diff = state_diff(expected_state, actual_state)
        reasons.append(f"final state mismatch: {diff}")
        cat = FailureCategory.ORDER_STATE_ERROR.value
        if any(k in diff for k in ("total", "delivery_fee", "discount_total")):
            cat = FailureCategory.ORDER_STATE_ERROR.value
        return FailureReport(
            category=cat,
            label=CATEGORY_LABELS[cat],
            confidence="medium",
            reasons=reasons,
        )

    # 6. model-side tool errors point at slot filling.
    if any(code in _MODEL_SIDE_ERRORS for code in error_codes):
        return FailureReport(
            category=FailureCategory.ENTITY_ERROR.value,
            label=CATEGORY_LABELS[FailureCategory.ENTITY_ERROR.value],
            confidence="low",
            reasons=[f"model supplied arguments the tool could not resolve: {error_codes}"],
        )

    # 7. nothing in the order at all -> intent problem.
    if not tool_calls and asr_transcript:
        return FailureReport(
            category=FailureCategory.INTENT_ERROR.value,
            label=CATEGORY_LABELS[FailureCategory.INTENT_ERROR.value],
            confidence="low",
            reasons=["no tool was called for a task that requires one"],
        )

    return FailureReport(
        category=FailureCategory.OTHER.value,
        label=CATEGORY_LABELS[FailureCategory.OTHER.value],
        confidence="low",
        reasons=["no automatic rule matched; needs human coding"],
    )


def state_diff(expected: dict[str, Any], actual: dict[str, Any]) -> str:
    diffs: list[str] = []
    for key in sorted(set(expected) | set(actual)):
        exp = expected.get(key)
        act = actual.get(key)
        if exp != act:
            diffs.append(f"{key}: expected {exp!r}, got {act!r}")
    return "; ".join(diffs) if diffs else "identical"
