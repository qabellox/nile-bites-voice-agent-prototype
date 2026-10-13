"""Mixed-language fidelity harness (development only, not research data).

Scores how faithfully a hypothesis preserves a reference transcript,
with special attention to the failure mode the intervention must avoid:
destroying mixed content by forcing one script (Arabizing English words or
translating Arabic into English). Reuses the research WER implementation as
a pure measuring function; results here are engineering diagnostics and are
never research records.
"""

from __future__ import annotations

from typing import Any

from ..research.codeswitch import has_arabic, has_latin, tokenize
from ..research.wer import compute_wer


def script_tokens(text: str, script: str) -> list[str]:
    out: list[str] = []
    for tok in tokenize(text or ""):
        if script == "latin" and has_latin(tok) and not has_arabic(tok):
            out.append(tok)
        elif script == "arabic" and has_arabic(tok):
            out.append(tok)
    return out


def score_fidelity(reference: str, hypothesis: str) -> dict[str, Any]:
    """Compare hypothesis against reference. All fields are diagnostics."""
    wer_res = compute_wer(reference or "", hypothesis or "")
    ref_latin = script_tokens(reference, "latin")
    hyp_latin = script_tokens(hypothesis, "latin")
    hyp_latin_set = {t.lower() for t in hyp_latin}
    preserved = [t for t in ref_latin if t.lower() in hyp_latin_set]
    ref_ar = script_tokens(reference, "arabic")
    hyp_ar = script_tokens(hypothesis, "arabic")
    return {
        "wer": round(wer_res.wer, 4),
        "wer_detail": wer_res.to_dict(),
        "ref_latin_tokens": ref_latin,
        "hyp_latin_tokens": hyp_latin,
        "latin_preserved": preserved,
        "latin_preservation_rate": round(len(preserved) / len(ref_latin), 4) if ref_latin else None,
        "ref_arabic_count": len(ref_ar),
        "hyp_arabic_count": len(hyp_ar),
    }
