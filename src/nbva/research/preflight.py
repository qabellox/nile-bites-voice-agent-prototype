"""Pre-run gate for formal (research) collection.

A research run is refused unless the task set it is about to execute is
internally consistent. The reasoning is narrow and mechanical:

* The task set is a **controlled variable** (D1-14). Running an inconsistent one
  means the independent variable is not what the design says it is.
* The mismatch is knowable **before** anyone speaks, because it is a property of
  the authored text, not of the participant.
* Discovering it during analysis is far too late, because by then the data is
  collected and the only remedy is to discard participant time.

This gate therefore blocks a research run and only warns on a development run,
where an inconsistent set is exactly what you want while building.

What this gate deliberately does **not** do: choose band boundaries, require a
minimum number of trials per condition, or discard a trial whose measured
condition drifts from its authored one at run time. All three are scientific
decisions. A drifted trial is recorded with ``condition_consistent: false`` and
kept, so the researcher can decide what it means.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .conditions import load_conditions
from .codeswitch import build_analyzer
from .taskaudit import TaskSetAudit, audit_task_set

# Requirements that block a research run unless the researcher relaxes them.
# Each maps a config key to the value that satisfies it, and to what the setting
# means, so the failure message can say what to do rather than just refusing.
BLOCKING_SETTINGS: tuple[tuple[str, Any, str], ...] = (
    (
        "experiment.business_weekday",
        "auto",
        "promotions and opening hours depend on the day, so an exact expected "
        "total cannot survive a run that follows the calendar",
    ),
)


@dataclass
class GateReport:
    """Outcome of the pre-run gate."""

    passed: bool = True
    enforced: bool = False
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    audit: TaskSetAudit | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "enforced": self.enforced,
            "problems": list(self.problems),
            "warnings": list(self.warnings),
            "audit": self.audit.to_dict() if self.audit else None,
        }


def _is_auto_weekday(value: Any) -> bool:
    return str(value or "").strip().lower() in {"", "auto"}


def preflight_task_set(
    config: Any,
    tasks: Iterable[Any] | None = None,
    repository: Any = None,
) -> GateReport:
    """Check everything about the task set that can be known before a run.

    ``tasks`` is passed in rather than loaded here, so this module stays free of
    any dependency on where the task file lives. ``None`` means no task file was
    supplied and the gate has nothing to audit; an empty list is a failure.
    """
    is_research = bool(config.get("experiment.is_research_run", False))
    report = GateReport(enforced=is_research)

    # -- settings that make a run uninterpretable ---------------------------
    for key, blocking_value, why in BLOCKING_SETTINGS:
        value = config.get(key)
        if key == "experiment.business_weekday" and _is_auto_weekday(value):
            message = (
                f"{key} is {value!r}: {why}. A formal run must pin the weekday, "
                "e.g. --set experiment.business_weekday=wednesday"
            )
            if is_research:
                report.problems.append(message)
            else:
                report.warnings.append(message)

    # ``None`` means no task file was supplied at all, which the gate has
    # nothing to say about. An empty list means the file exists and is empty,
    # which is a failure: the run would collect nothing and look like it worked.
    if tasks is None:
        report.passed = not report.problems
        return report

    tasks = list(tasks)
    if not tasks:
        report.problems.append("the task set is empty, so the run would collect nothing")
        report.passed = False
        return report

    # -- the condition audit ------------------------------------------------
    conditions = load_conditions(config)
    analyzer = build_analyzer(config, repository)
    audit = audit_task_set(tasks, analyzer, conditions)
    report.audit = audit

    if not audit.consistent:
        detail = (
            f"{len(audit.problems)} of {audit.total_turns} turns cannot be used as "
            "written (mismatched "
            f"{len(audit.mismatches)}, unbanded {len(audit.unbanded)}, "
            f"undeclared {len(audit.undeclared)})"
        )
        offenders = ", ".join(
            f"{t.task_id} t{t.turn_index} ({t.declared or 'none'}->{t.measured or 'none'})"
            for t in audit.problems[:6]
        )
        message = f"task set is not condition-consistent: {detail}. First: {offenders}"
        if is_research:
            report.problems.append(message)
        else:
            report.warnings.append(message)

    for note in audit.notes:
        report.warnings.append(note)

    report.passed = not report.problems
    return report


def enforce_preflight(
    report: GateReport,
    stream: Any = None,
    exit_code: int = 2,
) -> None:
    """Stop the process if the gate failed. Advisory runs return normally."""
    out = stream if stream is not None else sys.stderr
    for warning in report.warnings:
        print(f"WARNING: {warning}", file=out)
    if report.passed:
        return
    print("", file=out)
    print("ERROR: refusing to start a research run.", file=out)
    for problem in report.problems:
        print(f"  - {problem}", file=out)
    print(
        "\nThis is a controlled variable, not a runtime detail. Fix it, or set "
        "experiment.is_research_run=false while developing the instrument.",
        file=out,
    )
    raise SystemExit(exit_code)
