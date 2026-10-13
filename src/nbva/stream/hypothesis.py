"""Partial / committed / final hypothesis tracking with revision support.

Consecutive decode windows overlap, so the same speech is transcribed several
times. A word prefix that survives unchanged across ``agree_n`` consecutive
windows is *committed*: downstream work (understanding, tool prep) may start
on it. The newest window output is always just a *partial* and may still
change. When later speech contradicts a committed prefix, the tracker records
a *revision* instead of silently overwriting, so downstream stages can
re-run on the corrected text within a bounded budget.

Comparison is word-level on whitespace tokens; this keeps mixed Arabic/English
content intact (no language forcing anywhere in this module).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TrackerEvent:
    kind: str  # "partial" | "commit" | "revise" | "final"
    text: str
    window_index: int
    # Words committed so far (for commit events) or revised away (revise).
    words: list[str] = field(default_factory=list)


@dataclass
class HypothesisTracker:
    """Local-agreement commit policy over a stream of window transcripts."""

    agree_n: int = 2
    max_revisions: int = 3

    _history: list[list[str]] = field(default_factory=list, repr=False)
    _committed: list[str] = field(default_factory=list, repr=False)
    _revisions: int = 0
    _finalized: bool = False

    @property
    def committed_text(self) -> str:
        return " ".join(self._committed)

    @property
    def revision_count(self) -> int:
        return self._revisions

    @staticmethod
    def _common_prefix(a: list[str], b: list[str]) -> list[str]:
        out: list[str] = []
        for x, y in zip(a, b):
            if x != y:
                break
            out.append(x)
        return out

    def observe(self, transcript: str, window_index: int) -> list[TrackerEvent]:
        """Feed one window transcript; returns tracker events (often empty)."""
        events: list[TrackerEvent] = []
        if self._finalized:
            return events
        words = (transcript or "").split()
        self._history.append(words)
        if len(self._history) > self.agree_n:
            self._history.pop(0)

        events.append(TrackerEvent(kind="partial", text=transcript or "",
                                   window_index=window_index))

        if len(self._history) < self.agree_n:
            return events

        # Stable prefix = longest common prefix of the last agree_n windows.
        stable = self._history[0]
        for older in self._history[1:]:
            stable = self._common_prefix(stable, older)
            if not stable:
                break

        if len(stable) > len(self._committed):
            # Normal case: the committed prefix grows.
            new_words = stable[len(self._committed):]
            self._committed = list(stable)
            events.append(TrackerEvent(kind="commit", text=" ".join(self._committed),
                                       window_index=window_index, words=new_words))
        elif len(stable) < len(self._committed) and self._revisions < self.max_revisions:
            # Contradiction: later speech changed already-committed words.
            dropped = self._committed[len(stable):]
            self._committed = list(stable)
            self._revisions += 1
            events.append(TrackerEvent(kind="revise", text=" ".join(self._committed),
                                       window_index=window_index, words=dropped))
        return events

    def finalize(self, transcript: str, window_index: int) -> list[TrackerEvent]:
        """Endpoint signal: the full final transcript becomes committed."""
        events = self.observe(transcript, window_index)
        if self._finalized:
            return events
        self._finalized = True
        words = (transcript or "").split()
        if words != self._committed:
            self._committed = words
        events.append(TrackerEvent(kind="final", text=" ".join(self._committed),
                                   window_index=window_index, words=list(self._committed)))
        return events

    def reset(self) -> None:
        self._history = []
        self._committed = []
        self._revisions = 0
        self._finalized = False
