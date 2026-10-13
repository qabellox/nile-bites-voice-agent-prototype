"""Task-set audit for the code-switching condition.

The independent variable in this study is code-switching intensity, and every
trial has to belong to exactly one condition. Two things can go wrong:

* A trial is *authored* for one condition and actually measures another. That
  is an authoring error, and it silently mislabels the independent variable.
* A task set over-represents some conditions and starves others, so a
  comparison between conditions has unequal power.

Both are properties of the task file, not of a single run, so both can be
checked before anyone is exposed to a trial.

The point of measuring rather than trusting the ``declared_condition`` field is
that a hand-written label is exactly the thing that cannot be trusted: the
development set shipped with 16 of 33 turns labelled away from the band their
own text measures. This module makes that visible instead of leaving it to be
discovered in the analysis.

**Scope.** This reports consistency and distribution. It does not choose band
boundaries, decide how many trials a condition needs, or decide what to do with
a participant whose speech drifts out of the authored condition. Those are
scientific decisions and belong to the researcher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .conditions import ConditionSet
from .codeswitch import CodeSwitchAnalyzer

# Which field of a task carries the authored condition. A task-level condition
# applies to every turn unless the turn overrides it.


@dataclass
class TurnAudit:
    task_id: str
    turn_index: int
    declared: str | None
    measured: str | None
    english_pct: float
    consistent: bool
    english_lexical_tokens: int
    total_lexical_tokens: int
    utterance: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "turn_index": self.turn_index,
            "declared_condition": self.declared,
            "measured_condition": self.measured,
            "english_pct": self.english_pct,
            "consistent": self.consistent,
            "english_lexical_tokens": self.english_lexical_tokens,
            "total_lexical_tokens": self.total_lexical_tokens,
            "utterance": self.utterance,
        }


@dataclass
class BandSummary:
    condition_id: str
    declared_count: int
    measured_count: int
    measured_min: float | None
    measured_max: float | None
    measured_mean: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "declared_count": self.declared_count,
            "measured_count": self.measured_count,
            "measured_min": self.measured_min,
            "measured_max": self.measured_max,
            "measured_mean": self.measured_mean,
        }


@dataclass
class TaskSetAudit:
    turns: list[TurnAudit] = field(default_factory=list)
    bands: list[BandSummary] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def mismatches(self) -> list[TurnAudit]:
        """Labelled turns that measure into a band other than the declared one.

        Scoped to turns that *are* labelled, so a turn with no declaration is
        reported once, under ``undeclared``, rather than also being called a
        mismatch against a band it never claimed.
        """
        return [
            t
            for t in self.turns
            if t.declared is not None
            and t.measured is not None
            and t.measured != t.declared
        ]

    @property
    def unbanded(self) -> list[TurnAudit]:
        """Declared turns that measure into no band at all.

        Scoped to turns that *are* labelled, so a turn missing both a
        declaration and a measurement counts once, under ``undeclared`` only.
        """
        return [
            t for t in self.turns if t.measured is None and t.declared is not None
        ]

    @property
    def undeclared(self) -> list[TurnAudit]:
        """Turns with no authored condition, so they cannot be attributed.

        Kept separate from ``mismatches`` because "labelled wrong" and "not
        labelled" are different authoring errors, and because overlapping the
        categories would double-count a turn that is both undeclared and
        unbanded.
        """
        return [t for t in self.turns if t.declared is None]

    @property
    def problems(self) -> list[TurnAudit]:
        """Every turn that cannot be used as written, with no double counting."""
        return [*self.mismatches, *self.unbanded, *self.undeclared]

    @property
    def consistent(self) -> bool:
        """True when every turn measures into the band it claims.

        A turn that measures into no band, or that carries no declared band,
        counts as inconsistent: it has no condition, so it cannot contribute to
        any comparison between conditions.
        """
        return not self.problems

    @property
    def total_turns(self) -> int:
        return len(self.turns)

    def measured_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for t in self.turns:
            key = t.measured or "unbanded"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def declared_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for t in self.turns:
            key = t.declared or "undeclared"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_turns": self.total_turns,
            "consistent": self.consistent,
            "problem_count": len(self.problems),
            "mismatch_count": len(self.mismatches),
            "unbanded_count": len(self.unbanded),
            "undeclared_count": len(self.undeclared),
            "declared_counts": self.declared_counts(),
            "measured_counts": self.measured_counts(),
            "bands": [b.to_dict() for b in self.bands],
            "mismatches": [t.to_dict() for t in self.mismatches],
            "unbanded": [t.to_dict() for t in self.unbanded],
            "undeclared": [t.to_dict() for t in self.undeclared],
            "notes": list(self.notes),
        }


def _declared_for(task: Any, turn: Any) -> str | None:
    return getattr(turn, "declared_condition", None) or getattr(
        task, "declared_condition", None
    )


def audit_task_set(
    tasks: Iterable[Any],
    analyzer: CodeSwitchAnalyzer,
    conditions: ConditionSet,
) -> TaskSetAudit:
    """Measure every turn of every task and compare against its declared band.

    A turn with no declared condition is always a problem. There is deliberately
    no switch to relax that: a trial with no condition cannot be attributed to a
    cell, and quietly accepting one would let the design change by accident.
    """
    audit = TaskSetAudit()
    measured_by_band: dict[str, list[float]] = {}
    declared_by_band: dict[str, int] = {}

    for task in tasks:
        for turn in getattr(task, "turns", []) or []:
            utterance = str(getattr(turn, "utterance", "") or "")
            trace = analyzer.analyze(utterance)
            declared = _declared_for(task, turn)
            result = conditions.evaluate(trace.english_pct, declared)
            declared_norm = (
                declared.strip().upper() if isinstance(declared, str) else declared
            ) or None
            # Consistency is decided here, from the three facts, rather than
            # being inferred later from overlapping category filters.
            consistent = (
                declared_norm is not None
                and result.measured is not None
                and result.measured == declared_norm
            )

            row = TurnAudit(
                task_id=str(getattr(task, "task_id", "?")),
                turn_index=int(getattr(turn, "index", 0) or 0),
                declared=declared_norm,
                measured=result.measured,
                english_pct=round(float(trace.english_pct), 4),
                consistent=consistent,
                english_lexical_tokens=int(trace.english_lexical_tokens),
                total_lexical_tokens=int(trace.total_lexical_tokens),
                utterance=utterance,
            )
            audit.turns.append(row)

            if row.measured:
                measured_by_band.setdefault(row.measured, []).append(row.english_pct)
            if row.declared:
                declared_by_band[row.declared] = declared_by_band.get(row.declared, 0) + 1

    for band in conditions.bands:
        values = measured_by_band.get(band.id, [])
        audit.bands.append(
            BandSummary(
                condition_id=band.id,
                declared_count=declared_by_band.get(band.id, 0),
                measured_count=len(values),
                measured_min=round(min(values), 2) if values else None,
                measured_max=round(max(values), 2) if values else None,
                measured_mean=(
                    round(sum(values) / len(values), 2) if values else None
                ),
            )
        )

    _add_notes(audit, conditions)
    return audit


def _add_notes(audit: TaskSetAudit, conditions: ConditionSet) -> None:
    """Flag the structural problems that do not show up as a single mismatch."""
    if conditions.provisional:
        audit.notes.append(
            "condition band boundaries are PROVISIONAL, so a consistent task set is "
            "still not a scientifically valid one"
        )

    counts = audit.measured_counts()
    populated = {k: v for k, v in counts.items() if k != "unbanded" and v > 0}
    if populated and len(populated) < len(conditions.bands):
        missing = [b.id for b in conditions.bands if counts.get(b.id, 0) == 0]
        audit.notes.append(
            "conditions with no trial measured into them: "
            + ", ".join(missing)
            + " - a comparison against those cells has no data"
        )

    # An imbalance is reported, never blocked. How many trials each condition
    # needs is a scientific decision, so this tool says what it is and stops.
    if populated:
        sizes = sorted(populated.values())
        if sizes[-1] >= 2 * sizes[0] and sizes[0] > 0:
            audit.notes.append(
                "conditions are unbalanced by at least 2x "
                f"({', '.join(f'{k}={v}' for k, v in sorted(populated.items()))}); "
                "the researcher decides whether that is acceptable"
            )

    # Which measured bands each declared band actually landed in. This turns a
    # list of individual mismatches into a pattern, which is what tells you
    # whether the labels are off by one band (a labelling problem) or scattered
    # across the whole range (a genuine measurement problem).
    spread: dict[str, dict[str, int]] = {}
    for t in (*audit.mismatches, *audit.unbanded):
        if not t.declared:
            continue
        spread.setdefault(t.declared, {})
        key = t.measured or "none"
        spread[t.declared][key] = spread[t.declared].get(key, 0) + 1
    for declared in sorted(spread):
        targets = ", ".join(
            f"{k} x{v}" for k, v in sorted(spread[declared].items(), key=lambda kv: -kv[1])
        )
        audit.notes.append(f"trials declared {declared} actually measured {targets}")

    _note_adjacency(audit, conditions)

    if audit.undeclared:
        audit.notes.append(
            f"{len(audit.undeclared)} turn(s) declare no condition at all and cannot be "
            "attributed to a cell"
        )


def _note_adjacency(audit: TaskSetAudit, conditions: ConditionSet) -> None:
    """Report whether the wrong turns landed next door or far away.

    Adjacent-band errors point at the labels; distant ones point at the measure.
    That distinction decides who has to fix what, so the tool states it rather
    than leaving the researcher to eyeball 16 rows.
    """
    order = {band.id: i for i, band in enumerate(conditions.bands)}
    adjacent = 0
    distant = 0
    for t in audit.mismatches:
        if t.declared not in order or t.measured not in order:
            continue
        if abs(order[t.declared] - order[t.measured]) == 1:
            adjacent += 1
        else:
            distant += 1
    if not adjacent and not distant:
        return
    if distant == 0:
        audit.notes.append(
            f"all {adjacent} wrong turn(s) landed in an adjacent band, which points at "
            "the declared labels rather than at the measurement"
        )
    elif adjacent == 0:
        audit.notes.append(
            f"all {distant} wrong turn(s) landed more than one band away, which points "
            "at the measurement rather than at the declared labels"
        )
    else:
        audit.notes.append(
            f"{adjacent} wrong turn(s) landed in an adjacent band and {distant} landed "
            "further away, so both the labels and the measurement need review"
        )


def render_audit(audit: TaskSetAudit, show_all: bool = False, max_examples: int = 12) -> str:
    """Human-readable report. ``show_all`` lists matching turns too."""
    lines: list[str] = []
    lines.append("code-switching condition audit")
    lines.append("=" * 72)
    lines.append(f"turns audited: {audit.total_turns}")

    if not audit.turns:
        lines.append("no turns to audit")
        return "\n".join(lines)

    lines.append("")
    lines.append("per-condition distribution")
    lines.append(
        "%-6s %9s %9s %10s %10s %10s"
        % ("cond", "declared", "measured", "min%", "mean%", "max%")
    )
    lines.append("-" * 72)
    for band in audit.bands:
        if not band.declared_count and not band.measured_count:
            continue
        lines.append(
            "%-6s %9d %9d %10s %10s %10s"
            % (
                band.condition_id,
                band.declared_count,
                band.measured_count,
                _fmt(band.measured_min),
                _fmt(band.measured_mean),
                _fmt(band.measured_max),
            )
        )

    if show_all:
        lines.append("")
        lines.append("every turn")
        for t in audit.turns:
            flag = "ok" if t.consistent else "MISMATCH"
            lines.append(
                "%-20s t%-2d %-5s -> %-5s %6.2f%%  %s"
                % (t.task_id, t.turn_index, t.declared or "-", t.measured or "-", t.english_pct, flag)
            )

    problems = audit.problems
    lines.append("")
    lines.append(
        "problems: %d of %d turns  (mismatched %d, unbanded %d, undeclared %d)"
        % (
            len(problems),
            audit.total_turns,
            len(audit.mismatches),
            len(audit.unbanded),
            len(audit.undeclared),
        )
    )
    for t in problems[:max_examples]:
        lines.append(
            "  %-20s t%-2d declared %-5s measured %-5s at %6.2f%%"
            % (
                t.task_id,
                t.turn_index,
                t.declared or "-",
                t.measured or "none",
                t.english_pct,
            )
        )
        lines.append("      %s" % _clip(t.utterance, 68))
    hidden = len(problems) - max_examples
    if hidden > 0:
        lines.append("  ... and %d more (use --all to list every turn)" % hidden)

    if audit.notes:
        lines.append("")
        lines.append("notes")
        for note in audit.notes:
            lines.append("  - %s" % note)

    lines.append("")
    lines.append("=" * 72)
    if audit.consistent:
        lines.append("RESULT: every turn measures into the band it declares")
    else:
        lines.append(
            "RESULT: %d turn(s) cannot be used as written. A research run refuses to "
            "start on this task set." % len(problems)
        )
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "-" if value is None else "%.2f" % value


def _clip(text: str, width: int) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"
