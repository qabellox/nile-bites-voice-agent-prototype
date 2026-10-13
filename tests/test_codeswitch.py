"""Code-switching measurement, condition banding, and failure coding.

The measure is a LOCKED primary outcome: English lexical tokens over total
lexical tokens. These tests pin the arithmetic and the auditable token trail
that lets a reviewer re-check a percentage by hand.
"""

from __future__ import annotations

import pytest

from nbva.research.codeswitch import build_analyzer
from nbva.research.conditions import load_conditions
from nbva.research.failure import CATEGORY_LABELS, FailureCategory, classify_failure

# Placeholder thresholds. They are configurable in config/research.yaml and are
# explicitly provisional; the tests only need them to be self-consistent.
ASR_THRESHOLD = 0.5
LATENCY_THRESHOLD = 10.0


@pytest.fixture
def analyzer(config, repo):
    return build_analyzer(config, repo)


@pytest.fixture
def conditions(config):
    return load_conditions(config)


# -- the measure ----------------------------------------------------------


def test_pure_arabic_is_zero_percent(analyzer):
    assert analyzer.analyze("عايز اتنين تشيكن برجر توصل الزمالك").english_pct == 0.0


def test_pure_english_is_one_hundred_percent(analyzer):
    assert analyzer.analyze("I want two chicken burgers please").english_pct == 100.0


def test_mixed_text_matches_a_hand_count(analyzer):
    # English: I, want, two, chicken, burgers = 5
    # Arabic: عايز, اتنين, توصل, الزمالك = 4   ->   5/9 = 55.56%
    result = analyzer.analyze("I want two chicken burgers عايز اتنين توصل الزمالك")
    assert result.total_lexical_tokens == 9
    assert result.english_lexical_tokens == 5
    assert result.non_english_lexical_tokens == 4
    assert result.english_pct == pytest.approx(55.5555, abs=0.001)


def test_empty_text_is_reported_as_zero(analyzer):
    result = analyzer.analyze("")
    assert result.total_lexical_tokens == 0
    assert result.english_pct == 0.0


def test_numerals_count_as_lexical_tokens(analyzer):
    # السعر / 145 / جنيه / تمام = 4
    result = analyzer.analyze("السعر 145 جنيه، تمام؟")
    assert result.total_lexical_tokens == 4


def test_the_token_trail_can_be_rechecked_by_hand(analyzer):
    result = analyzer.analyze("I want برجر", include_tokens=True)
    assert [t.text for t in result.tokens] == ["I", "want", "برجر"]
    assert [t.token_class for t in result.tokens] == ["english", "english", "arabic"]
    assert sum(1 for t in result.tokens if t.is_english) == 2
    # Every token records why it was classified, so a percentage is auditable.
    assert all(t.reason for t in result.tokens)


def test_arabic_diacritics_do_not_create_extra_tokens(analyzer):
    assert analyzer.analyze("مَطْعَم").total_lexical_tokens == 1


def test_normalisation_folds_the_ya_alef_variants(analyzer):
    assert analyzer.analyze("علي").total_lexical_tokens == analyzer.analyze("على").total_lexical_tokens


def test_lexicon_entries_count_as_english(analyzer):
    result = analyzer.analyze("التكلفة 100 EGP")
    assert result.english_lexical_tokens == 1


# -- condition banding ----------------------------------------------------


def test_condition_ids_are_c0_through_c4(conditions):
    assert conditions.ids() == ["C0", "C1", "C2", "C3", "C4"]


def test_bands_are_ordered_and_do_not_overlap(conditions):
    edges = [(b.min_pct, b.max_pct) for b in conditions.bands]
    assert edges == sorted(edges)
    for (_, low_high), (next_low, _) in zip(edges, edges[1:]):
        assert low_high <= next_low


def test_c0_is_exactly_zero_percent(conditions):
    assert conditions.measure_to_condition(0.0) == "C0"
    # The configured tolerance widens C0 slightly, so use a value well clear of it.
    assert conditions.measure_to_condition(10.0) == "C1"


def test_pure_english_measures_to_c4(conditions, analyzer):
    pct = analyzer.analyze("I want two chicken burgers please").english_pct
    assert conditions.measure_to_condition(pct) == "C4"


def test_evaluate_flags_a_declared_measured_disagreement(conditions, analyzer):
    pct = analyzer.analyze("I want two chicken burgers please").english_pct
    result = conditions.evaluate(pct, declared="C0")
    assert result.consistent is False
    assert any("disagrees" in w for w in result.warnings)


def test_evaluate_accepts_a_matching_declaration(conditions, analyzer):
    pct = analyzer.analyze("I want two chicken burgers please").english_pct
    assert conditions.evaluate(pct, declared="C4").consistent is True


def test_provisional_boundaries_are_always_disclosed(conditions):
    result = conditions.evaluate(50.0, declared=None)
    assert result.warnings
    assert conditions.to_dict()["provisional"] is True


def test_soft_maximum_is_flagged_for_review(conditions):
    # C4 is English-dominant; a trial at 100% still gets reviewed against the
    # conceptual definition rather than silently accepted.
    result = conditions.evaluate(100.0, declared="C4")
    assert any("soft maximum" in w for w in result.warnings)


def test_unknown_declared_condition_is_warned_about(conditions):
    result = conditions.evaluate(50.0, declared="C9")
    assert any("not a known condition" in w for w in result.warnings)


# -- failure coding -------------------------------------------------------


def test_ten_failure_categories_plus_a_none_value():
    assert len(FailureCategory) == 11
    assert len(CATEGORY_LABELS) == 11
    labelled = [c for c in FailureCategory if c is not FailureCategory.NONE]
    assert len(labelled) == 10


def test_a_correct_trial_reports_no_failure():
    report = classify_failure(
        success=True,
        wer=0.0,
        asr_failure_threshold=ASR_THRESHOLD,
        latency_seconds=2.0,
        latency_failure_seconds=LATENCY_THRESHOLD,
        tool_calls=[{"name": "create_order", "result": {"ok": True}}],
    )
    assert report.category is None or report.category == FailureCategory.NONE


def test_asr_failure_is_detected_from_a_high_wer():
    report = classify_failure(
        success=False,
        wer=0.72,
        asr_failure_threshold=ASR_THRESHOLD,
        latency_seconds=2.0,
        latency_failure_seconds=LATENCY_THRESHOLD,
        tool_calls=[],
        asr_transcript="two shirts delivery",
        reference_transcript="I want two chicken burgers please",
    )
    assert report.category == FailureCategory.ASR_ERROR
    assert report.reasons


def test_a_high_wer_without_a_reference_cannot_blame_the_asr():
    # Without a reference there is no evidence of what the ASR got wrong, so the
    # classifier must not silently blame the recognizer.
    report = classify_failure(
        success=False,
        wer=0.72,
        asr_failure_threshold=ASR_THRESHOLD,
        latency_seconds=2.0,
        latency_failure_seconds=LATENCY_THRESHOLD,
        tool_calls=[],
    )
    assert report.category != FailureCategory.ASR_ERROR


def test_a_slow_turn_is_labelled_as_a_latency_failure():
    report = classify_failure(
        success=False,
        wer=0.0,
        asr_failure_threshold=ASR_THRESHOLD,
        latency_seconds=14.0,
        latency_failure_seconds=LATENCY_THRESHOLD,
        tool_calls=[],
    )
    assert report.category == FailureCategory.LATENCY_ERROR


def test_context_resolution_failure_is_detected_from_ambiguity():
    report = classify_failure(
        success=False,
        wer=0.0,
        asr_failure_threshold=ASR_THRESHOLD,
        latency_seconds=2.0,
        latency_failure_seconds=LATENCY_THRESHOLD,
        tool_calls=[
            {
                "name": "resolve_order_reference",
                "result": {"ambiguous": True, "resolved": False, "line_ids": ["L1", "L2"]},
            }
        ],
    )
    assert report.category == FailureCategory.CONTEXT_ERROR


def test_state_tracking_failure_is_detected_from_a_state_mismatch():
    report = classify_failure(
        success=False,
        wer=0.0,
        asr_failure_threshold=ASR_THRESHOLD,
        latency_seconds=2.0,
        latency_failure_seconds=LATENCY_THRESHOLD,
        tool_calls=[],
        final_state_ok=False,
        expected_state={"total": 290},
        actual_state={"total": 330},
    )
    assert report.category == FailureCategory.ORDER_STATE_ERROR


def test_a_healthy_turn_is_not_mislabelled_as_a_latency_failure():
    report = classify_failure(
        success=False,
        wer=0.0,
        asr_failure_threshold=ASR_THRESHOLD,
        latency_seconds=2.0,
        latency_failure_seconds=LATENCY_THRESHOLD,
        tool_calls=[],
    )
    assert report.category != FailureCategory.LATENCY_ERROR


def test_manual_override_is_recorded_as_such():
    report = classify_failure(
        success=False,
        wer=0.0,
        asr_failure_threshold=ASR_THRESHOLD,
        latency_seconds=2.0,
        latency_failure_seconds=LATENCY_THRESHOLD,
        tool_calls=[],
        manual_category="other",
    )
    assert report.category == FailureCategory.OTHER
    assert report.overridden is True
