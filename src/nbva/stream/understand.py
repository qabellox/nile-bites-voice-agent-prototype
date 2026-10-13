"""Incremental understanding over committed hypothesis prefixes.

Runs the existing code-switch analyzer on each committed prefix (language ID
that preserves mixed content instead of forcing one language) and applies an
explicit, configurable readiness policy: how many committed words must exist
before speculative tool preparation may start, and how many before the LLM
may be called. These thresholds are the controlled mechanism that prevents
guessing from insufficient partial information.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UnderstandingSnapshot:
    committed_text: str
    english_pct: float | None = None
    condition: str | None = None
    word_count: int = 0
    ready_for_tools: bool = False
    ready_for_llm: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class IncrementalUnderstanding:
    """Wraps the project's CodeSwitchAnalyzer with a readiness policy."""

    analyzer: Any = None
    min_words_tools: int = 3
    min_words_llm: int = 4

    def on_commit(self, committed_text: str) -> UnderstandingSnapshot:
        text = (committed_text or "").strip()
        words = text.split()
        snap = UnderstandingSnapshot(committed_text=text, word_count=len(words))
        if self.analyzer is not None and text:
            try:
                trace = self.analyzer.analyze(text)
                snap.english_pct = round(float(trace.english_pct), 4)
                snap.detail = {
                    "english_lexical_tokens": trace.english_lexical_tokens,
                    "total_lexical_tokens": trace.total_lexical_tokens,
                }
            except Exception:
                snap.detail = {"analyzer_error": True}
        snap.ready_for_tools = len(words) >= self.min_words_tools
        snap.ready_for_llm = len(words) >= self.min_words_llm
        return snap
