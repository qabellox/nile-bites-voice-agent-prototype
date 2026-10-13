"""The code-switching intensity measure.

LOCKED BY THE METHODOLOGY:

    English lexical-token proportion = English lexical tokens / total lexical tokens * 100

Two properties of the implementation are scientifically load-bearing and are
therefore kept deliberately strict:

1. **Linguistic tokens, not model tokens.** Segmentation is a regex tokenizer
   written for this study. No model tokenizer is ever involved, so the measure
   does not change if the ASR or LLM is swapped.
2. **Auditable.** Every token and its classification is returned in a
   :class:`TokenTrace`, so any proportion quoted in the paper can be recomputed
   by hand from the log file.

The English lexicon is configurable (``research.codeswitch.classification``).
Multi-word English entries such as "coca cola" are matched greedily.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Literal

TokenClass = Literal["english", "arabic", "numeral", "symbol", "other"]

_ARABIC_BLOCK = r"\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF"
_LATIN = r"A-Za-z"
_DIGIT = r"0-9\u0660-\u0669\u06F0-\u06F9"

# A lexical token is a run of Arabic letters, a run of Latin letters, or a number.
# Arabic is additionally allowed to absorb a trailing/leading Arabic particle
# (و, ف, ب, ل, ك) when written attached, which is normal in Egyptian speech.
_TOKEN_RE = re.compile(
    rf"[{_ARABIC_BLOCK}]+|[{_LATIN}]+|[{_DIGIT}]+(?:[.,][{_DIGIT}]+)?"
)

_ARABIC_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
_TATWEEL = re.compile("\u0640")
_ARABIC_FOLD = str.maketrans(
    {
        "\u0623": "\u0627",
        "\u0625": "\u0627",
        "\u0622": "\u0627",
        "\u0671": "\u0627",
        "\u0649": "\u064a",
        "\u0629": "\u0647",
    }
)


def normalize_token(token: str) -> str:
    t = unicodedata.normalize("NFKC", token)
    t = _ARABIC_DIACRITICS.sub("", t)
    t = _TATWEEL.sub("", t)
    return t.strip().lower()


def fold_arabic_token(token: str) -> str:
    return normalize_token(token).translate(_ARABIC_FOLD)


def has_arabic(token: str) -> bool:
    return bool(re.search(f"[{_ARABIC_BLOCK}]", token))


def has_latin(token: str) -> bool:
    return bool(re.search(f"[{_LATIN}]", token))


def is_numeral(token: str) -> bool:
    return bool(re.fullmatch(f"[{_DIGIT}]+(?:[.,][{_DIGIT}]+)?", token))


# ---------------------------------------------------------------------------


@dataclass
class TokenInfo:
    text: str
    normalized: str
    token_class: TokenClass
    is_english: bool
    counted_as_lexical: bool
    reason: str


@dataclass
class TokenTrace:
    tokens: list[TokenInfo] = field(default_factory=list)
    measure: str = "english_lexical_token_proportion"
    total_lexical_tokens: int = 0
    english_lexical_tokens: int = 0
    non_english_lexical_tokens: int = 0
    english_pct: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self, include_tokens: bool = True) -> dict:
        out = {
            "measure": self.measure,
            "total_lexical_tokens": self.total_lexical_tokens,
            "english_lexical_tokens": self.english_lexical_tokens,
            "non_english_lexical_tokens": self.non_english_lexical_tokens,
            "english_pct": round(self.english_pct, 4),
        }
        if self.notes:
            out["notes"] = list(self.notes)
        if include_tokens:
            out["tokens"] = [
                {
                    "text": t.text,
                    "normalized": t.normalized,
                    "class": t.token_class,
                    "is_english": t.is_english,
                    "counted_as_lexical": t.counted_as_lexical,
                    "reason": t.reason,
                }
                for t in self.tokens
            ]
        return out


class EnglishClassifier:
    """Decides whether a token is an English lexical token."""

    def __init__(
        self,
        lexicon: Iterable[str] = (),
        unknown_latin_token_policy: str = "english",
        latin_script_arabic_loanwords: Iterable[str] = (),
    ):
        self.lexicon: set[str] = set()
        for phrase in lexicon:
            norm = normalize_token(phrase)
            if norm:
                self.lexicon.add(norm)
                self.lexicon.update(p for p in norm.split() if p)
        self.multiword: set[str] = {p for p in self.lexicon if " " in p}
        self.unknown_latin_token_policy = unknown_latin_token_policy
        self.loanwords = {normalize_token(w) for w in latin_script_arabic_loanwords}

    def is_english(self, normalized: str) -> tuple[bool, str]:
        if not normalized:
            return False, "empty token"
        if has_arabic(normalized):
            return False, "contains Arabic script"
        if not has_latin(normalized):
            return False, "no Latin letters"
        if normalized in self.loanwords:
            return False, "listed Arabic loanword in Latin script"
        if normalized in self.lexicon:
            return True, "in English lexicon"
        if " " in normalized:
            return False, "multi-word span handled separately"
        if self.unknown_latin_token_policy == "english":
            return True, "Latin script, unknown to lexicon, policy=english"
        if self.unknown_latin_token_policy == "arabic":
            return False, "Latin script, unknown to lexicon, policy=arabic"
        return False, "Latin script, unknown to lexicon, policy=other"

    def known(self, normalized: str) -> bool:
        return normalized in self.lexicon


def tokenize(text: str) -> list[str]:
    """Segment text into candidate lexical tokens, preserving order."""
    if not text:
        return []
    cleaned = _ARABIC_DIACRITICS.sub("", unicodedata.normalize("NFKC", text))
    cleaned = _TATWEEL.sub("", cleaned)
    return _TOKEN_RE.findall(cleaned)


def _merge_multiword(tokens: list[str], classifier: EnglishClassifier) -> list[str]:
    """Greedily merge adjacent English tokens that form a known English phrase.

    "coca cola" counts as one lexical token, not two, when the phrase is in the
    lexicon. Without this, English multiword dish names would inflate the
    denominator unevenly across conditions.
    """
    if not classifier.multiword:
        return tokens
    max_len = max(len(p.split()) for p in classifier.multiword)
    out: list[str] = []
    i = 0
    while i < len(tokens):
        merged = False
        for span in range(min(max_len, len(tokens) - i), 1, -1):
            window = tokens[i : i + span]
            if any(has_arabic(w) for w in window):
                continue
            phrase = normalize_token(" ".join(window))
            if phrase in classifier.multiword:
                out.append(" ".join(window))
                i += span
                merged = True
                break
        if not merged:
            out.append(tokens[i])
            i += 1
    return out


class CodeSwitchAnalyzer:
    """Computes the locked code-switching measure with a full audit trail."""

    def __init__(
        self,
        classifier: EnglishClassifier,
        count_numerals: bool = True,
        store_token_trace: bool = True,
    ):
        self.classifier = classifier
        self.count_numerals = count_numerals
        self.store_token_trace = store_token_trace

    def analyze(self, text: str, include_tokens: bool | None = None) -> TokenTrace:
        raw_tokens = tokenize(text)
        merged = _merge_multiword(raw_tokens, self.classifier)

        trace = TokenTrace()
        total = 0
        english = 0
        for tok in merged:
            norm = normalize_token(tok)
            if is_numeral(norm):
                if self.count_numerals:
                    total += 1
                    trace.tokens.append(
                        TokenInfo(tok, norm, "numeral", False, True, "numeral counted as a lexical token")
                    )
                else:
                    trace.tokens.append(
                        TokenInfo(tok, norm, "numeral", False, False, "numerals not counted")
                    )
                continue

            is_eng, reason = self.classifier.is_english(norm)
            if has_arabic(norm):
                cls: TokenClass = "arabic"
            elif has_latin(norm):
                cls = "english" if is_eng else "other"
            else:
                cls = "other"

            if cls == "other" and is_eng:
                # unknown-token policy of "other" excludes from both sides
                total -= 0
                trace.tokens.append(
                    TokenInfo(tok, norm, "other", False, False, "excluded by policy")
                )
                continue

            total += 1
            if is_eng:
                english += 1
            trace.tokens.append(TokenInfo(tok, norm, cls, is_eng, True, reason))

        trace.total_lexical_tokens = total
        trace.english_lexical_tokens = english
        trace.non_english_lexical_tokens = total - english
        trace.english_pct = (english / total * 100.0) if total else 0.0
        if total == 0:
            trace.notes.append("no lexical tokens found; proportion is undefined and reported as 0.0")
        return trace

    def english_pct(self, text: str) -> float:
        return self.analyze(text, include_tokens=False).english_pct


# ---------------------------------------------------------------------------
# construction helpers
# ---------------------------------------------------------------------------


def _default_lexicon_path(config) -> Path | None:
    raw = config.get("research.codeswitch.classification.lexicon_file")
    if not raw:
        return None
    p = Path(str(raw))
    if p.is_absolute():
        return p
    from ..config.loader import project_root

    return project_root() / p


@lru_cache(maxsize=4)
def _load_lexicon_file(path_str: str) -> tuple[str, ...]:
    p = Path(path_str)
    if not p.exists():
        return ()
    out: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return tuple(out)


def build_analyzer(config, repository=None) -> CodeSwitchAnalyzer:
    """Construct the analyzer from configuration, extending the lexicon with the
    restaurant dataset's own English aliases when asked."""
    cls_cfg = config.section("research.codeswitch.classification") or {}
    entries: list[str] = []
    lex_path = _default_lexicon_path(config)
    if lex_path and lex_path.exists():
        entries.extend(_load_lexicon_file(str(lex_path)))
    else:
        lex_path = None

    if cls_cfg.get("extend_with_dataset_aliases", True) and repository is not None:
        for item in repository.menu:
            if has_latin(item.name_en):
                entries.append(item.name_en)
            for alias in item.aliases:
                if has_latin(alias) and not has_arabic(alias):
                    entries.append(alias)

    classifier = EnglishClassifier(
        lexicon=entries,
        unknown_latin_token_policy=cls_cfg.get("unknown_latin_token_policy", "english"),
        latin_script_arabic_loanwords=cls_cfg.get("latin_script_arabic_loanwords", []) or [],
    )
    return CodeSwitchAnalyzer(
        classifier=classifier,
        count_numerals=bool(config.get("research.codeswitch.count_numerals", True)),
        store_token_trace=bool(config.get("research.codeswitch.store_token_trace", True)),
    )
