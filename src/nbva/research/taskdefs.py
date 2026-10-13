"""Task definitions and assertion-based evaluation.

The task format is designed so that grading never depends on the system under
test. A trial declares what must be true of the final business state, and the
evaluator checks the deterministic order/database state against those
assertions. Wording of the response is not graded, so the metric measures whether
the agent did the task, not whether it spoke well.

Task families are the seven fixed by the methodology:
information, price_availability, delivery, order_creation, order_modification,
multi_turn_context, combined.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

TASK_FAMILIES = [
    "information",
    "price_availability",
    "delivery",
    "order_creation",
    "order_modification",
    "multi_turn_context",
    "combined",
]

TaskFamily = Literal[
    "information",
    "price_availability",
    "delivery",
    "order_creation",
    "order_modification",
    "multi_turn_context",
    "combined",
]


@dataclass
class TurnSpec:
    """One user turn inside a trial."""

    index: int
    utterance: str = ""
    reference_transcript: str = ""
    # Optional per-turn condition, used in multi-turn context tasks.
    declared_condition: str | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "utterance": self.utterance,
            "reference_transcript": self.reference_transcript or self.utterance,
            "declared_condition": self.declared_condition,
            "note": self.note,
        }


@dataclass
class TaskSpec:
    """A trial definition: one task under one code-switching condition."""

    task_id: str
    family: TaskFamily
    declared_condition: str | None
    turns: list[TurnSpec] = field(default_factory=list)
    # Weekday the business rules run under for this trial. Promotions and hours
    # depend on it, so a task that expects an exact total must pin it or the
    # expectation breaks on a different day.
    business_weekday: str | None = None
    # Expected final state assertions, checked against the real order state.
    expected_lines: list[dict[str, Any]] = field(default_factory=list)
    expected_total: int | None = None
    expected_subtotal: int | None = None
    expected_delivery_fee: int | None = None
    expected_zone_id: str | None = None
    expected_order_status: str | None = None
    # "delivery" or "pickup". Needed to replay a pickup trial, which has no zone
    # and so cannot be driven from expected_zone_id alone.
    expected_order_mode: str | None = None
    # Evidence the customer gives during the trial, e.g. a student id that
    # unlocks a gated promotion. It is what the replay harness passes to
    # create_order, so a gated promotion can be exercised end to end.
    student_id: str | None = None
    expect_no_order: bool = False
    # A trial whose correct behaviour is a refusal, e.g. an order below the
    # zone minimum or an item that is out of stock. The agent should surface the
    # error instead of inventing a workaround.
    expected_error: str | None = None
    expect_no_error: bool = False
    # Expected facts the response must convey, checked as substrings/numbers.
    expected_facts: list[dict[str, Any]] = field(default_factory=list)
    expected_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    notes: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def utterance_text(self) -> str:
        return " ".join(t.utterance for t in self.turns).strip()

    @property
    def reference_text(self) -> str:
        return " ".join((t.reference_transcript or t.utterance) for t in self.turns).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "family": self.family,
            "declared_condition": self.declared_condition,
            "business_weekday": self.business_weekday,
            "turns": [t.to_dict() for t in self.turns],
            "expected_lines": self.expected_lines,
            "expected_total": self.expected_total,
            "expected_subtotal": self.expected_subtotal,
            "expected_delivery_fee": self.expected_delivery_fee,
            "expected_zone_id": self.expected_zone_id,
            "expected_order_status": self.expected_order_status,
            "expected_order_mode": self.expected_order_mode,
            "student_id": self.student_id,
            "expect_no_order": self.expect_no_order,
            "expected_error": self.expected_error,
            "expect_no_error": self.expect_no_error,
            "expected_facts": self.expected_facts,
            "expected_tools": self.expected_tools,
            "forbidden_tools": self.forbidden_tools,
            "notes": self.notes,
            "tags": self.tags,
        }


def _turn_from_raw(raw: dict[str, Any], index: int) -> TurnSpec:
    return TurnSpec(
        index=int(raw.get("index", index)),
        utterance=str(raw.get("utterance", "")),
        reference_transcript=str(raw.get("reference_transcript", "")),
        declared_condition=raw.get("declared_condition"),
        note=str(raw.get("note", "")),
    )


def task_from_dict(raw: dict[str, Any]) -> TaskSpec:
    family = str(raw.get("family", "")).strip()
    if family not in TASK_FAMILIES:
        raise ValueError(
            f"task {raw.get('task_id')!r} has family {family!r}; "
            f"must be one of {TASK_FAMILIES}"
        )
    turns_raw = raw.get("turns") or []
    if not turns_raw:
        raise ValueError(f"task {raw.get('task_id')!r} has no turns")
    return TaskSpec(
        task_id=str(raw["task_id"]),
        family=family,  # type: ignore[arg-type]
        declared_condition=raw.get("declared_condition"),
        business_weekday=raw.get("business_weekday"),
        turns=[_turn_from_raw(t, i + 1) for i, t in enumerate(turns_raw)],
        expected_lines=list(raw.get("expected_lines", []) or []),
        expected_total=raw.get("expected_total"),
        expected_subtotal=raw.get("expected_subtotal"),
        expected_delivery_fee=raw.get("expected_delivery_fee"),
        expected_zone_id=raw.get("expected_zone_id"),
        expected_order_status=raw.get("expected_order_status"),
        expected_order_mode=raw.get("expected_order_mode"),
        student_id=raw.get("student_id"),
        expect_no_order=bool(raw.get("expect_no_order", False)),
        expected_error=raw.get("expected_error"),
        expect_no_error=bool(raw.get("expect_no_error", False)),
        expected_facts=list(raw.get("expected_facts", []) or []),
        expected_tools=list(raw.get("expected_tools", []) or []),
        forbidden_tools=list(raw.get("forbidden_tools", []) or []),
        notes=str(raw.get("notes", "")),
        tags=list(raw.get("tags", []) or []),
    )


def load_tasks(path: str | Path) -> list[TaskSpec]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    items = raw.get("tasks") or []
    if not isinstance(items, list):
        raise ValueError(f"{path}: 'tasks' must be a list")
    return [task_from_dict(t) for t in items]


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------


@dataclass
class EvaluationResult:
    success: bool
    checks: list[dict[str, Any]]
    state_diff: str = ""
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_success": self.success,
            "task_score": round(self.score, 4),
            "state_diff": self.state_diff,
            "task_checks": self.checks,
        }


def _actual_lines(order: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not order:
        return []
    return [
        {
            "item_id": l.get("item_id"),
            "quantity": l.get("quantity"),
            "options": sorted(o.get("option_id") for o in l.get("options", []) or []),
        }
        for l in order.get("lines", []) or []
    ]


def evaluate_task(
    spec: TaskSpec,
    final_order: dict[str, Any] | None,
    tool_calls: list[dict[str, Any]],
    response_text: str = "",
) -> EvaluationResult:
    """Check the trial's assertions against the observed deterministic state."""
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, expected: Any = None, actual: Any = None) -> None:
        checks.append({"check": name, "passed": bool(ok), "expected": expected, "actual": actual})

    called = [str(c.get("name")) for c in tool_calls]

    # A trial can assert that the deterministic layer refused, e.g. an order
    # below the zone minimum. That refusal is the correct outcome, so the
    # check looks at the tool result rather than at a successful order.
    if spec.expected_error:
        errors = [
            str((c.get("result") or {}).get("error"))
            for c in tool_calls
            if (c.get("result") or {}).get("error")
        ]
        add(
            "expected_error_raised",
            spec.expected_error in errors,
            expected=spec.expected_error,
            actual=errors,
        )
    elif spec.expect_no_error:
        errors = [
            str((c.get("result") or {}).get("error"))
            for c in tool_calls
            if (c.get("result") or {}).get("error")
        ]
        add("no_tool_error", not errors, expected=[], actual=errors)

    for name in spec.expected_tools:
        add(f"tool_called:{name}", name in called, expected=name, actual=called)
    for name in spec.forbidden_tools:
        add(f"tool_not_called:{name}", name not in called, expected=f"not {name}", actual=called)

    if spec.expect_no_order:
        add("no_order_created", not final_order, expected=None, actual=bool(final_order))
    else:
        add("order_exists", final_order is not None, expected="an order", actual=bool(final_order))

    if final_order:
        if spec.expected_order_status:
            add(
                "order_status",
                final_order.get("status") == spec.expected_order_status,
                expected=spec.expected_order_status,
                actual=final_order.get("status"),
            )
        if spec.expected_zone_id:
            add(
                "delivery_zone",
                final_order.get("delivery_zone_id") == spec.expected_zone_id,
                expected=spec.expected_zone_id,
                actual=final_order.get("delivery_zone_id"),
            )
        if spec.expected_order_mode:
            add(
                "order_mode",
                final_order.get("order_mode") == spec.expected_order_mode,
                expected=spec.expected_order_mode,
                actual=final_order.get("order_mode"),
            )
        if spec.expected_total is not None:
            add(
                "order_total",
                final_order.get("total") == spec.expected_total,
                expected=spec.expected_total,
                actual=final_order.get("total"),
            )
        if spec.expected_subtotal is not None:
            add(
                "order_subtotal",
                final_order.get("subtotal") == spec.expected_subtotal,
                expected=spec.expected_subtotal,
                actual=final_order.get("subtotal"),
            )
        if spec.expected_delivery_fee is not None:
            add(
                "delivery_fee",
                final_order.get("delivery_fee") == spec.expected_delivery_fee,
                expected=spec.expected_delivery_fee,
                actual=final_order.get("delivery_fee"),
            )
        if spec.expected_lines:
            actual = _actual_lines(final_order)
            add(
                "order_lines",
                _lines_match(spec.expected_lines, actual),
                expected=spec.expected_lines,
                actual=actual,
            )

    for fact in spec.expected_facts:
        kind = str(fact.get("type", "contains"))
        if kind == "contains":
            needle = str(fact.get("value", ""))
            add(
                f"fact_contains:{needle}",
                needle.lower() in (response_text or "").lower(),
                expected=needle,
                actual=None,
            )
        elif kind == "number":
            value = str(fact.get("value", "")).strip()
            add(
                f"fact_number:{value}",
                _contains_number(response_text, value),
                expected=value,
                actual=None,
            )
        elif kind == "regex":
            pattern = str(fact.get("value", ""))
            import re

            add(
                f"fact_regex:{pattern}",
                bool(re.search(pattern, response_text or "", flags=re.IGNORECASE)),
                expected=pattern,
                actual=None,
            )

    passed = sum(1 for c in checks if c["passed"])
    total = len(checks)
    success = total > 0 and passed == total
    return EvaluationResult(
        success=success,
        checks=checks,
        score=(passed / total) if total else 0.0,
    )


def _lines_match(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> bool:
    """Compare line sets. Order-insensitive; options order-insensitive."""
    def key(line: dict[str, Any]) -> tuple:
        return (
            str(line.get("item_id")),
            int(line.get("quantity", 0) or 0),
            tuple(sorted(str(o) for o in (line.get("options") or []))),
        )

    return sorted(map(key, expected)) == sorted(map(key, actual))


def _contains_number(text: str, value: str) -> bool:
    import re

    if not text:
        return False
    cleaned = value.replace(",", "")
    patterns = [re.escape(cleaned)]
    if cleaned.isdigit():
        n = int(cleaned)
        if n >= 1000:
            patterns.append(rf"{n // 1000}\s*[,٬]?\s*{n % 1000:03d}")
    return any(re.search(p, text) for p in patterns)
