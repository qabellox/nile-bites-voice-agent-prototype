"""A1 — the reliability/efficiency architecture. NOT IMPLEMENTED YET.

Status (2026-11-18): placeholder. Phase 1 of
``08_Intervention/IMPLEMENTATION_PLAN_A0_A1_P0.md`` makes the architecture
*selectable*; the components land in Phase 2, one at a time, each flag-gated and
observable.

The constructor deliberately raises. Selecting ``architecture.engine: a1`` today
must fail loudly, because the alternative — quietly returning A0 behaviour while
the trial record says ``a1`` — would produce a dataset that is mislabelled before
anyone notices, and no statistical treatment can repair that.

When implemented, this class must:

* accept exactly the keyword arguments ``BaselinePipeline`` accepts, so the
  factory can build both arms identically;
* expose ``session``, ``asr``, ``llm``, ``tts``, ``player``, ``analyzer``,
  ``conditions``, ``system_prompt`` and ``max_tool_iterations`` as plain writable
  attributes, because existing call sites swap providers after construction;
* return the same ``PipelineResult`` shape, including the full ``TurnRecord``;
* record which components were enabled on every turn, so ablations are possible.
"""

from __future__ import annotations

from typing import Any

from .base import A1_COMPONENTS, ENGINE_A1


class A1Engine:
    """Placeholder for the intervention arm. Raises on construction."""

    engine_id = ENGINE_A1
    components: dict[str, bool] = {name: True for name in A1_COMPONENTS}

    def __init__(self, **kwargs: Any) -> None:
        raise NotImplementedError(
            "architecture.engine='a1' is not implemented yet. "
            "The conventional baseline (architecture.engine='a0') is the only "
            "implemented arm. Refusing to fall back to A0 so that an experiment "
            "cannot silently run — and label — the wrong architecture. "
            "See 08_Intervention/IMPLEMENTATION_PLAN_A0_A1_P0.md (Phase 2)."
        )

    def process_turn(self, **kwargs: Any):  # pragma: no cover - unreachable
        raise NotImplementedError("A1Engine is not implemented yet")
