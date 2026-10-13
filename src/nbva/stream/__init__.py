"""Streaming/incremental voice-agent architecture (intervention candidate).

This package implements the overlap-and-streaming design alongside the frozen
sequential baseline (``BaselinePipeline``): chunked ASR windows, a
partial/committed/final hypothesis tracker, incremental understanding,
commit-gated speculative tool preparation, token-streaming LLM responses,
and sentence-streaming TTS.

Development/validation only. Nothing here is part of the research baseline,
and nothing here writes research data.
"""

from .chunker import SlidingWindowBuffer
from .context import ContextState, ContextTracker
from .fidelity import score_fidelity
from .hypothesis import HypothesisTracker
from .interrupt import InterruptController
from .loop import StreamingTurnLoop
from .respond import StreamingResponder, detect_lang, split_sentences
from .speculative import (
    MUTATING_TOOLS,
    READONLY_TOOLS,
    SpeculativeTools,
    can_run_speculatively,
)
from .trace import StreamTrace
from .understand import IncrementalUnderstanding

__all__ = [
    "SlidingWindowBuffer",
    "HypothesisTracker",
    "IncrementalUnderstanding",
    "ContextState",
    "ContextTracker",
    "SpeculativeTools",
    "StreamingResponder",
    "StreamingTurnLoop",
    "InterruptController",
    "StreamTrace",
    "READONLY_TOOLS",
    "MUTATING_TOOLS",
    "can_run_speculatively",
    "detect_lang",
    "split_sentences",
    "score_fidelity",
]
