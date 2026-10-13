"""Word Error Rate, with configurable normalisation.

WER is the LOCKED primary metric for ASR accuracy. The normalisation policy is
PROVISIONAL and lives in ``config/research.yaml`` under ``research.wer`` so the
researcher can change it without touching code.

The same normalisation is applied to the reference transcript and to the ASR
hypothesis. That symmetry is essential: normalising only the hypothesis would
penalise the ASR for the researcher's formatting choices.

Implementation is the standard Levenshtein alignment over tokens, with
substitutions, deletions and insertions each costing 1, and an explicit
exemption for word-boundary punctuation-only mismatches controlled by config.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .codeswitch import _ARABIC_DIACRITICS, _ARABIC_FOLD, _TATWEEL, normalize_token

_PUNCT_RE = re.compile(r"[^\w\s\u0600-\u06FF]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")

_DIGIT_MAP = str.maketrans("\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669", "0123456789")
_DIGIT_MAP2 = str.maketrans("\u06F0\u06F1\u06F2\u06F3\u06F4\u06F5\u06F6\u06F7\u06F8\u06F9", "0123456789")

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_to_words(n: int) -> list[str]:
    if n < 0:
        return []
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        parts = [_TENS[n // 10]]
        if n % 10:
            parts.append(_ONES[n % 10])
        return parts
    if n < 1000:
        parts = [_ONES[n // 100], "hundred"]
        if n % 100:
            parts.extend(number_to_words(n % 100))
        return parts
    return [str(n)]


@dataclass
class WERConfig:
    lowercase: bool = True
    strip_punctuation: bool = True
    collapse_whitespace: bool = True
    fold_arabic_variants: bool = True
    strip_arabic_diacritics: bool = True
    normalize_digits: bool = True
    spell_out_small_numbers: bool = False
    spell_out_limit: int = 20


@dataclass
class WERResult:
    wer: float
    reference_tokens: list[str]
    hypothesis_tokens: list[str]
    substitutions: int
    deletions: int
    insertions: int
    correct: int
    reference_length: int

    def to_dict(self) -> dict:
        return {
            "wer": round(self.wer, 6),
            "wer_percent": round(self.wer * 100.0, 4),
            "reference_length": self.reference_length,
            "substitutions": self.substitutions,
            "deletions": self.deletions,
            "insertions": self.insertions,
            "correct": self.correct,
            "reference_tokens": self.reference_tokens,
            "hypothesis_tokens": self.hypothesis_tokens,
        }


def normalize_for_wer(text: str, cfg: WERConfig) -> str:
    out = text or ""
    if cfg.strip_arabic_diacritics:
        out = _ARABIC_DIACRITICS.sub("", out)
        out = _TATWEEL.sub("", out)
    out = unicodedata.normalize("NFKC", out)
    if cfg.normalize_digits:
        out = out.translate(_DIGIT_MAP).translate(_DIGIT_MAP2)
    if cfg.lowercase:
        out = out.lower()
    if cfg.strip_punctuation:
        out = _PUNCT_RE.sub(" ", out)
    if cfg.fold_arabic_variants:
        out = out.translate(_ARABIC_FOLD)
    if cfg.collapse_whitespace:
        out = _WS_RE.sub(" ", out)
    return out.strip()


def wer_tokens(text: str, cfg: WERConfig) -> list[str]:
    norm = normalize_for_wer(text, cfg)
    tokens = [t for t in norm.split() if t]
    if cfg.spell_out_small_numbers:
        expanded: list[str] = []
        for tok in tokens:
            if tok.isdigit() and 0 <= int(tok) <= cfg.spell_out_limit:
                expanded.extend(number_to_words(int(tok)))
            else:
                expanded.append(tok)
        tokens = expanded
    return tokens


def _levenshtein(ref: list[str], hyp: list[str]) -> tuple[int, int, int]:
    """Return exact (substitutions, deletions, insertions) counts.

    Standard unit-cost Levenshtein alignment. The distance is computed over the
    full grid and then backtracked from the final cell, because the operation
    counts are only meaningful if they describe the whole alignment path. A
    last-row-only tally silently drops every error that occurs on an earlier
    row, which both inflates the count and invents operations that were never
    taken.

    Memory is O(len(ref) * len(hyp)), which is a few thousand integers for
    utterance-length transcripts and keeps the backtrace exact.
    """
    n, m = len(ref), len(hyp)
    if n == 0:
        return 0, 0, m
    if m == 0:
        return 0, n, 0

    # dist[i][j] = edit distance between ref[:i] and hyp[:j]
    dist = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dist[i][0] = i  # every reference token deleted
    for j in range(1, m + 1):
        dist[0][j] = j  # every hypothesis token inserted

    for i in range(1, n + 1):
        ref_i = ref[i - 1]
        row = dist[i]
        prev_row = dist[i - 1]
        for j in range(1, m + 1):
            if ref_i == hyp[j - 1]:
                row[j] = prev_row[j - 1]
            else:
                row[j] = 1 + min(prev_row[j - 1], prev_row[j], row[j - 1])

    subs = dels = ins = 0
    i, j = n, m
    # Tie-break order is match > deletion > substitution > insertion, fixed so
    # the reported breakdown is deterministic. Several optimal alignments can
    # exist for the same pair; only the total is unique, and the total is what
    # WER uses. Preferring deletion over substitution matches the usual ASR
    # convention of counting an unproduced reference token as a deletion
    # instead of inventing a word to substitute with.
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1] and dist[i][j] == dist[i - 1][j - 1]:
            i -= 1
            j -= 1
        elif i > 0 and dist[i][j] == dist[i - 1][j] + 1:
            dels += 1
            i -= 1
        elif i > 0 and j > 0 and dist[i][j] == dist[i - 1][j - 1] + 1:
            subs += 1
            i -= 1
            j -= 1
        else:
            ins += 1
            j -= 1
    return subs, dels, ins


def compute_wer(reference: str, hypothesis: str, cfg: WERConfig | None = None) -> WERResult:
    cfg = cfg or WERConfig()
    ref = wer_tokens(reference, cfg)
    hyp = wer_tokens(hypothesis, cfg)

    if not ref:
        # An empty reference is a scoring error, not a 0% or 100% WER.
        return WERResult(
            wer=1.0 if hyp else 0.0,
            reference_tokens=ref,
            hypothesis_tokens=hyp,
            substitutions=0,
            deletions=0,
            insertions=len(hyp),
            correct=0,
            reference_length=0,
        )

    subs, dels, ins = _levenshtein(ref, hyp)
    errors = subs + dels + ins
    return WERResult(
        wer=errors / len(ref),
        reference_tokens=ref,
        hypothesis_tokens=hyp,
        substitutions=subs,
        deletions=dels,
        insertions=ins,
        correct=len(ref) - subs - dels,
        reference_length=len(ref),
    )


def wer_config_from_config(config) -> WERConfig:
    section = config.section("research.wer") or {}
    return WERConfig(
        lowercase=bool(section.get("lowercase", True)),
        strip_punctuation=bool(section.get("strip_punctuation", True)),
        collapse_whitespace=bool(section.get("collapse_whitespace", True)),
        fold_arabic_variants=bool(section.get("fold_arabic_variants", True)),
        strip_arabic_diacritics=bool(section.get("strip_arabic_diacritics", True)),
        normalize_digits=bool(section.get("normalize_digits", True)),
        spell_out_small_numbers=bool(section.get("spell_out_small_numbers", False)),
        spell_out_limit=int(section.get("spell_out_limit", 20)),
    )
