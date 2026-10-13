"""Tests for the condition audit and the pre-run research gate.

The audit is tested against the shipped development task set rather than a
synthetic one, because the finding that motivated this module is a property of
that file: hand-written ``declared_condition`` labels drift from what the text
measures. A test built only from clean fixtures would pass forever and prove
nothing about the failure mode.
"""

from __future__ import annotations

import io

import pytest

from nbva.config.loader import load_config
from nbva.research.conditions import load_conditions
from nbva.research.codeswitch import build_analyzer
from nbva.research.preflight import GateReport, enforce_preflight, preflight_task_set
from nbva.research.taskaudit import audit_task_set, render_audit
from nbva.research.taskdefs import TaskSpec, TurnSpec


@pytest.fixture
def audit(config, repo, tasks):
    return audit_task_set(tasks, build_analyzer(config, repo), load_conditions(config))


# ---------------------------------------------------------------------------
# the audit itself
# ---------------------------------------------------------------------------


def test_audit_covers_every_turn(audit, tasks):
    assert audit.total_turns == sum(len(t.turns) for t in tasks)


def test_shipped_task_set_has_mismatches(audit):
    """The development set is known to be mislabelled.

    This is a regression guard in both directions: if the labels are ever fixed,
    this test fails and the number is updated, which is the intended prompt to
    re-baseline the development fixtures.
    """
    assert not audit.consistent
    assert len(audit.mismatches) + len(audit.unbanded) == 16


def test_mismatch_is_reported_with_both_bands(audit):
    row = next(t for t in audit.mismatches if t.task_id == "DEV-CTX-01" and t.turn_index == 1)
    assert row.declared == "C1"
    assert row.measured == "C0"
    assert row.english_pct == 0.0
    assert row.consistent is False


def test_declared_and_measured_counts_differ(audit):
    declared = audit.declared_counts()
    measured = audit.measured_counts()
    assert declared == {"C0": 8, "C1": 7, "C2": 9, "C3": 5, "C4": 4}
    assert measured["C0"] > declared["C0"]
    assert measured["C2"] < declared["C2"]


def test_spread_under_attribution_above_declared_lands_in_a_lower_band(audit):
    """Below the author's intent is a systematic problem, not noise.

    Every C0 turn measures 0%, and the C1 turns declared C1 that fail measure
    0% too, so the shortfall is entirely on one side. A symmetric scatter would
    point at the measure; a one-sided shortfall points at the labels.
    """
    c1_failures = [t for t in audit.turns if t.declared == "C1" and not t.consistent]
    assert c1_failures
    assert all(t.measured == "C0" for t in c1_failures)


def test_notes_report_provisional_boundaries_and_confusion(audit):
    joined = " ".join(audit.notes)
    assert "PROVISIONAL" in joined
    assert "unbalanced" in joined
    assert "actually measured" in joined


def test_band_summary_has_bounds(audit):
    c0 = next(b for b in audit.bands if b.condition_id == "C0")
    assert c0.measured_count > 0
    assert c0.measured_min == 0.0
    assert c0.measured_max == 0.0


def test_render_reports_counts_and_verdict(audit):
    text = render_audit(audit)
    assert "turns audited: 33" in text
    assert "problems: 16 of 33 turns" in text
    assert "cannot be used as written" in text


def test_render_show_all_lists_every_turn(audit):
    text = render_audit(audit, show_all=True)
    assert "every turn" in text
    for row in audit.turns:
        assert row.task_id in text


# ---------------------------------------------------------------------------
# clean fixtures
# ---------------------------------------------------------------------------


def _task(task_id: str, utterance: str, declared: str | None) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        family="test",
        declared_condition=declared,
        turns=[TurnSpec(index=1, utterance=utterance, reference_transcript=utterance)],
    )


def test_a_correctly_labelled_task_set_is_consistent(config, repo):
    # Both texts are taken from the shipped set, where the audit already
    # measured 0.00% and 100.00%. Inventing "clean" text by hand is how the
    # original labelling mistake happened in the first place.
    clean = [
        _task("T-C0", "عايز اتنين تشيكن برجر", "C0"),
        _task("T-C4", "Order two chicken burgers and one Coke for Zamalek", "C4"),
    ]
    result = audit_task_set(clean, build_analyzer(config, repo), load_conditions(config))
    assert result.consistent
    assert result.mismatches == []
    assert "every turn measures into the band it declares" in render_audit(result)


def test_missing_declared_condition_counts_as_inconsistent(config, repo):
    """An unlabelled turn is a problem, reported under its own category."""
    unlabelled = [_task("T-X", "I want two chicken burgers", None)]
    result = audit_task_set(unlabelled, build_analyzer(config, repo), load_conditions(config))
    assert not result.consistent
    assert len(result.undeclared) == 1
    assert result.undeclared[0].declared is None
    # It measures into C4, but it never claimed C4, so calling that a
    # "mismatch" would blame the band instead of the missing label.
    assert result.undeclared[0].measured == "C4"
    assert result.mismatches == []


def test_a_missing_declaration_is_always_a_problem(config, repo):
    """There is deliberately no way to relax this.

    A trial with no condition cannot be attributed to a cell, so accepting one
    would let the design change by accident. The absence of a switch is the
    guarantee, so it is asserted rather than assumed.
    """
    import inspect

    unlabelled = [_task("T-X", "I want two chicken burgers", None)]
    result = audit_task_set(unlabelled, build_analyzer(config, repo), load_conditions(config))
    assert not result.consistent
    assert len(result.undeclared) == 1
    assert result.undeclared[0].declared is None
    assert "require_declared" not in inspect.signature(audit_task_set).parameters


def test_adjacent_errors_are_reported_as_a_labelling_problem(config, repo):
    """All-neighbour errors must be named as a label problem, not a measure one.

    This is the diagnostic that decides who fixes what, so it is pinned: a
    one-band error everywhere means the task file is mislabelled, whereas
    scattered errors would mean the measure is wrong.
    """
    # Declared C1, but this is pure English so it measures C4: two bands away.
    far = [_task("T-FAR", "I want two chicken burgers please", "C1")]
    result = audit_task_set(far, build_analyzer(config, repo), load_conditions(config))
    assert any("points at the measurement" in n for n in result.notes), result.notes

    near = [_task("T-NEAR", "عايز اتنين برجر", "C1")]
    result = audit_task_set(near, build_analyzer(config, repo), load_conditions(config))
    assert any("rather than at the measurement" in n for n in result.notes), result.notes

    # A mix must say both need looking at, not pick a side.
    mixed = [*far, *near]
    result = audit_task_set(mixed, build_analyzer(config, repo), load_conditions(config))
    assert any("both the labels and the measurement need review" in n for n in result.notes)


def test_the_problem_categories_do_not_overlap(config, repo):
    """A turn must never be counted twice.

    A turn can be both undeclared and unbanded. If the categories overlapped,
    the headline count would exceed the real number of unusable turns, and the
    gate message would misstate how much work a fix needs.
    """
    blank = _task("T-BLANK", "؟؟؟", None)
    result = audit_task_set([blank], build_analyzer(config, repo), load_conditions(config))
    seen = [id(t) for t in (*result.mismatches, *result.unbanded, *result.undeclared)]
    assert len(seen) == len(set(seen))
    assert len(result.problems) == len({id(t) for t in result.problems})


def test_turn_level_condition_overrides_the_task_level_one(config, repo):
    task = _task("T-MIX", "I want two chicken burgers and one Coke", "C0")
    task.turns[0].declared_condition = "C4"
    result = audit_task_set([task], build_analyzer(config, repo), load_conditions(config))
    assert result.consistent
    assert result.turns[0].declared == "C4"


def test_empty_task_set_reports_nothing_to_audit(config, repo):
    result = audit_task_set([], build_analyzer(config, repo), load_conditions(config))
    assert result.total_turns == 0
    assert "no turns to audit" in render_audit(result)


def test_audit_serialises_for_json_output(audit):
    payload = audit.to_dict()
    assert payload["total_turns"] == 33
    assert payload["consistent"] is False
    assert len(payload["mismatches"]) == len(audit.mismatches)
    assert set(payload["measured_counts"]) >= {"C0", "C4"}


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


@pytest.fixture
def research_config():
    """A research-mode config.

    Loaded fresh rather than taken from the session-scoped ``config`` fixture:
    the gate tests below flip ``is_research_run`` and ``business_weekday``, and
    mutating the shared object would leak those settings into every later test
    module, so unrelated tests would fail depending on run order.
    """
    config = load_config()
    config.set("experiment.is_research_run", True)
    config.set("experiment.participant_id", "P001")
    config.set("experiment.business_weekday", "wednesday")
    return config


@pytest.fixture
def dev_config():
    """A development-mode config, fresh for the same reason as ``research_config``."""
    config = load_config()
    config.set("experiment.is_research_run", False)
    return config


def test_research_run_is_blocked_by_an_inconsistent_task_set(research_config, repo, tasks):
    report = preflight_task_set(research_config, tasks=tasks, repository=repo)
    assert report.enforced is True
    assert not report.passed
    assert any("not condition-consistent" in p for p in report.problems)


def test_development_run_is_only_warned(dev_config, repo, tasks):
    report = preflight_task_set(dev_config, tasks=tasks, repository=repo)
    assert report.enforced is False
    assert report.passed is True
    assert any("not condition-consistent" in w for w in report.warnings)


def test_research_run_proceeds_on_a_consistent_task_set(research_config, repo):
    clean = [_task("T-C4", "I want two chicken burgers and one Coke please", "C4")]
    report = preflight_task_set(research_config, tasks=clean, repository=repo)
    assert report.passed is True
    assert report.problems == []


def test_auto_business_weekday_blocks_a_research_run(research_config, repo):
    research_config.set("experiment.business_weekday", "auto")
    report = preflight_task_set(research_config, tasks=None, repository=repo)
    assert not report.passed
    assert any("business_weekday" in p for p in report.problems)


def test_auto_business_weekday_is_only_advisory_in_development(dev_config):
    dev_config.set("experiment.business_weekday", "auto")
    report = preflight_task_set(dev_config, tasks=None)
    assert report.passed is True
    assert any("business_weekday" in w for w in report.warnings)


def test_empty_task_set_blocks_even_in_development(dev_config):
    report = preflight_task_set(dev_config, tasks=[])
    assert not report.passed
    assert any("empty" in p for p in report.problems)


def test_no_task_file_leaves_the_gate_open(research_config):
    report = preflight_task_set(research_config, tasks=None)
    assert report.audit is None
    assert all("not condition-consistent" not in p for p in report.problems)


def test_provisional_boundaries_are_always_advisory(research_config, repo, tasks):
    report = preflight_task_set(research_config, tasks=tasks, repository=repo)
    assert any("PROVISIONAL" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# enforcement
# ---------------------------------------------------------------------------


def test_enforce_raises_on_failure():
    report = GateReport(passed=False, enforced=True, problems=["task set is broken"])
    with pytest.raises(SystemExit) as excinfo:
        enforce_preflight(report, stream=io.StringIO())
    assert excinfo.value.code == 2


def test_enforce_returns_silently_on_success():
    report = GateReport(passed=True, enforced=True, warnings=["advisory only"])
    assert enforce_preflight(report, stream=io.StringIO()) is None


def test_enforce_prints_warnings_before_failing():
    report = GateReport(
        passed=False,
        enforced=True,
        problems=["bad set"],
        warnings=["bands are provisional"],
    )
    sink = io.StringIO()
    with pytest.raises(SystemExit):
        enforce_preflight(report, stream=sink)
    err = sink.getvalue()
    assert "WARNING: bands are provisional" in err
    assert "ERROR: refusing to start a research run" in err
    assert "bad set" in err
