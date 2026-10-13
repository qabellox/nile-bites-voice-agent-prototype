"""The WER metric itself.

WER is a LOCKED primary outcome, so these tests are about the arithmetic being
exactly right rather than approximately right. The cases below were chosen
because the previous last-row-only backtrace got several of them wrong.
"""

from __future__ import annotations

import pytest

from nbva.research.wer import WERConfig, compute_wer, number_to_words, wer_tokens


@pytest.mark.parametrize(
    "reference,hypothesis,expected_wer,expected_s,expected_d,expected_i",
    [
        ("a b c d", "a b c d", 0.0, 0, 0, 0),
        ("a b", "a c", 0.5, 1, 0, 0),
        # The regression: an error in the first word used to be invisible.
        ("x b c d", "a b c d", 0.25, 1, 0, 0),
        # Pure deletion and pure insertion are unambiguous.
        ("a b c", "a c", 1 / 3, 0, 1, 0),
        ("a b c", "a b c d", 1 / 3, 0, 0, 1),
        ("a b c d", "w x y z", 1.0, 4, 0, 0),
        ("a", "", 1.0, 0, 1, 0),
        ("", "a b", 1.0, 0, 0, 2),
    ],
)
def test_wer_counts(reference, hypothesis, expected_wer, expected_s, expected_d, expected_i):
    result = compute_wer(reference, hypothesis)
    assert result.wer == pytest.approx(expected_wer)
    assert (result.substitutions, result.deletions, result.insertions) == (
        expected_s,
        expected_d,
        expected_i,
    )


def test_wer_total_is_unique_even_when_the_breakdown_is_not():
    """Several optimal alignments can share a distance; only the total is unique.

    "a b c d e" vs "a x c e f" is reachable as one substitution + one deletion +
    one insertion, and also as three substitutions. Both are 3 errors over 5
    reference tokens, so the WER is 0.6 either way. The test pins the metric and
    the error total, and deliberately does not pin which operations were used.
    """
    result = compute_wer("a b c d e", "a x c e f")
    errors = result.substitutions + result.deletions + result.insertions
    assert errors == 3
    assert result.wer == pytest.approx(0.6)


def test_operation_counts_always_reconstruct_the_distance():
    """subs + dels + ins must equal the independent edit distance, always."""
    cases = [
        ("a b c", "a c"),
        ("a b c d e f", "a x c y f z"),
        ("one two three", "one three four"),
        ("x", "a b c d e"),
    ]
    for reference, hypothesis in cases:
        ref_tokens = wer_tokens(reference, WERConfig())
        hyp_tokens = wer_tokens(hypothesis, WERConfig())
        result = compute_wer(reference, hypothesis)
        total = result.substitutions + result.deletions + result.insertions
        # Brute-force the same unit-cost distance as an independent check.
        rows = len(ref_tokens) + 1
        cols = len(hyp_tokens) + 1
        dist = [[0] * cols for _ in range(rows)]
        for i in range(rows):
            dist[i][0] = i
        for j in range(cols):
            dist[0][j] = j
        for i in range(1, rows):
            for j in range(1, cols):
                if ref_tokens[i - 1] == hyp_tokens[j - 1]:
                    dist[i][j] = dist[i - 1][j - 1]
                else:
                    dist[i][j] = 1 + min(dist[i - 1][j - 1], dist[i - 1][j], dist[i][j - 1])
        assert total == dist[-1][-1], (reference, hypothesis)


def test_correct_count_never_exceeds_reference_length():
    result = compute_wer("a b c d e", "a b c")
    assert result.correct == 3
    assert result.reference_length == 5
    assert 0 <= result.correct <= result.reference_length


def test_wer_is_symmetric_under_normalisation():
    cfg = WERConfig()
    assert wer_tokens("Hello, WORLD!", cfg) == ["hello", "world"]
    assert wer_tokens("مرحبا   بكم", cfg) == ["مرحبا", "بكم"]


def test_arabic_digits_normalise_to_latin():
    assert wer_tokens("order ٣ items", WERConfig()) == ["order", "3", "items"]


def test_arabic_diacritics_are_stripped_when_configured():
    cfg = WERConfig()
    assert wer_tokens("مَطْعَم", cfg) == wer_tokens("مطعم", cfg)


def test_spell_out_small_numbers_is_opt_in():
    cfg = WERConfig(spell_out_small_numbers=True, spell_out_limit=20)
    assert wer_tokens("3 items", cfg) == ["three", "items"]
    # Above the limit the digits stay as digits, so nothing is silently mangled.
    assert wer_tokens("25 items", cfg) == ["25", "items"]


def test_empty_reference_is_flagged_not_scored_zero():
    result = compute_wer("", "something")
    assert result.reference_length == 0
    assert result.wer == 1.0


def test_number_to_words_known_values():
    assert number_to_words(0) == ["zero"]
    assert number_to_words(7) == ["seven"]
    assert number_to_words(20) == ["twenty"]
    assert number_to_words(21) == ["twenty", "one"]
    assert number_to_words(100) == ["one", "hundred"]
    assert number_to_words(145) == ["one", "hundred", "forty", "five"]


def test_to_dict_is_json_ready():
    payload = compute_wer("a b", "a c").to_dict()
    assert payload["wer"] == 0.5
    assert payload["wer_percent"] == 50.0
    assert payload["substitutions"] == 1
