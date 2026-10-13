"""Incremental semantic/context state for the streaming architecture.

Maintains one evolving state object per turn and *patches* it as committed
hypotheses arrive, instead of rebuilding understanding from scratch per
chunk. Entity resolution is deterministic (menu-seeded keyword matching in
both scripts plus Arabic/English numerals); the LLM remains responsible for
anything rules cannot resolve. Every patch is returned as an event so the
trace shows exactly what changed and why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_AR_NUMERALS = {
    "واحد": 1, "واحدة": 1, "one": 1,
    "اتنين": 2, "اثنين": 2, "two": 2,
    "تلاتة": 3, "ثلاثة": 3, "three": 3,
    "اربعة": 4, "أربعة": 4, "four": 4,
    "خمسة": 5, "five": 5,
}

_ORDER_VERBS = ("عايز", "عاوز", "عايزة", "هات", "هاتي", "اطلب", "want", "order",
                "i want", "id like", "i'd like")
_MODIFY_VERBS = ("خلي", "خلى", "شيل", "شلي", "زود", "ضيف", "من غير", "بدون",
                 "without", "without", "no ", "change", "modify", "remove")
_QUERY_VERBS = ("بكام", "بكم", "عندكم", "عندك", "فيه", "how much", "do you have",
                "is there", "price", "سعر")

_NEGATION = ("من غير", "بدون", "without", "no ", "منغير")


@dataclass
class ContextState:
    intent: str = "unknown"  # order | modify | query | unknown
    items: list[dict[str, Any]] = field(default_factory=list)
    modifications: list[dict[str, Any]] = field(default_factory=list)
    confidence: str = "low"  # low | med | high
    status: str = "partial"  # partial | final

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "items": [dict(i) for i in self.items],
            "modifications": [dict(m) for m in self.modifications],
            "confidence": self.confidence,
            "status": self.status,
        }


@dataclass
class ContextTracker:
    """Deterministic incremental state. Menu aliases seed item matching."""

    menu_aliases: dict[str, str] = field(default_factory=dict)
    state: ContextState = field(default_factory=ContextState)

    def _norm(self, text: str) -> str:
        t = (text or "").lower()
        t = re.sub(r"[\u064B-\u0652\u0670]", "", t)
        return re.sub(r"\s+", " ", t).strip()

    def _find_quantity(self, text: str) -> int | None:
        m = re.search(r"\b(\d+)\b", text)
        if m:
            try:
                return max(1, int(m.group(1)))
            except ValueError:
                pass
        for word, num in _AR_NUMERALS.items():
            if re.search(rf"(^|\s){re.escape(word)}(\s|$)", text):
                return num
        return None

    def _find_item(self, text: str) -> str | None:
        for alias in sorted(self.menu_aliases, key=len, reverse=True):
            if alias and alias in text:
                return self.menu_aliases[alias]
        return None

    def _find_modification(self, text: str) -> dict[str, Any] | None:
        for neg in _NEGATION:
            idx = text.find(neg)
            if idx >= 0:
                target = text[idx + len(neg):].strip().split()
                return {"negation": neg.strip(),
                        "target": " ".join(target[:3])}
        return None

    def update(self, committed_text: str, commit_id: int = 0) -> dict[str, Any]:
        """Patch state from newly committed text. Returns a context_update event."""
        text = self._norm(committed_text)
        patches: list[str] = []
        st = self.state

        lowered = f" {text} "
        if st.intent == "unknown":
            if any(v in lowered for v in _ORDER_VERBS):
                st.intent = "order"
                patches.append("intent=order")
            elif any(v in lowered for v in _MODIFY_VERBS):
                st.intent = "modify"
                patches.append("intent=modify")
            elif any(v in lowered for v in _QUERY_VERBS):
                st.intent = "query"
                patches.append("intent=query")

        item = self._find_item(text)
        qty = self._find_quantity(text)
        if item and not any(i.get("name") == item for i in st.items):
            st.items.append({"name": item, "quantity": qty or 1})
            patches.append(f"item+={item}x{qty or 1}")
        elif item and qty:
            for i in st.items:
                if i.get("name") == item and not i.get("quantity_locked"):
                    if i.get("quantity") != qty:
                        i["quantity"] = qty
                        patches.append(f"item~={item}x{qty}")
                    break

        mod = self._find_modification(text)
        if mod and mod not in st.modifications:
            st.modifications.append(mod)
            patches.append(f"mod+={mod['negation']}:{mod['target']}")
            if st.intent == "unknown":
                st.intent = "modify"
                patches.append("intent=modify")

        has_item = bool(st.items)
        has_intent = st.intent != "unknown"
        st.confidence = ("high" if (has_intent and has_item)
                         else "med" if (has_intent or has_item) else "low")

        return {"kind": "context_update", "commit_id": commit_id,
                "patches": patches, "state": st.to_dict()}

    def finalize(self) -> dict[str, Any]:
        self.state.status = "final"
        return {"kind": "context_final", "state": self.state.to_dict()}

    def reset(self) -> None:
        self.state = ContextState()
