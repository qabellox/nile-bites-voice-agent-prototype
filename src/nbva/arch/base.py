"""Turn-engine contract, selection, and construction.

A *turn engine* is the object that takes one user turn and returns one
:class:`~nbva.agent.pipeline.PipelineResult`. Two engines exist in the design:

``a0`` — conventional sequential baseline
    :class:`~nbva.agent.pipeline.BaselinePipeline`, used unchanged: full-utterance
    ASR -> one LLM tool loop -> deterministic tools -> complete TTS -> playback.

``a1`` — reliability/efficiency architecture
    The intervention arm. Not implemented yet; requesting it raises instead of
    silently falling back to A0.

Why a factory and not a subclass hierarchy
------------------------------------------
The architecture factor in the 2 x 2 design must not be confounded with a
difference in ASR, TTS, tools, database, prompt, or iteration limits. Both arms
are therefore built here from the *same* keyword arguments, and the only thing
that varies is which class consumes them. Selection is configuration
(``architecture.engine``), so a run cannot change arms by accident when a call
site is edited.

Why ``a1`` fails instead of degrading
-------------------------------------
An instruction-following engine that quietly returned A0 behaviour while the
record said ``a1`` would produce a dataset that is mislabelled by construction
and cannot be repaired after collection. Refusing is the only safe default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..config.loader import Config

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..agent.pipeline import PipelineResult

ENGINE_A0 = "a0"
ENGINE_A1 = "a1"
KNOWN_ENGINES: tuple[str, ...] = (ENGINE_A0, ENGINE_A1)

#: The A1 components, in the order they are described in
#: ``01_Research_Design/Current_Direction.md`` §4. Declared here once so the
#: ablation switches, the per-turn component record, and the documentation cannot
#: drift apart. Empty for A0 — the baseline has no components to ablate.
A1_COMPONENTS: tuple[str, ...] = (
    "context",
    "orchestration",
    "gateway",
    "validation",
    "latency_path",
)


@runtime_checkable
class TurnEngine(Protocol):
    """The interface an experiment arm must satisfy.

    Deliberately narrow: one method. Everything else the harness needs (session,
    providers, player) is reached through attributes that both engines carry,
    because callers legitimately replace a provider after construction —
    ``app.pipeline.tts = NullTTS()`` in the CLI, ``app.pipeline.asr = ...`` in the
    development UI. An engine that hid its providers behind a wrapper would break
    those call sites and, with them, the existing instrument.
    """

    engine_id: str

    def process_turn(self, **kwargs: Any) -> "PipelineResult":
        ...


def selected_engine(config: Config) -> str:
    """Resolve ``architecture.engine``, defaulting to A0.

    An unknown value is an error rather than a silent fallback: a typo in a run
    configuration must not produce a dataset labelled with an arm that was never
    run.
    """
    raw = config.get("architecture.engine", ENGINE_A0)
    value = str(raw if raw is not None else ENGINE_A0).strip().lower()
    if not value:
        return ENGINE_A0
    if value not in KNOWN_ENGINES:
        raise ValueError(
            f"architecture.engine={value!r} is not a known engine; "
            f"expected one of {KNOWN_ENGINES}"
        )
    return value


def build_turn_engine(config: Config, pipeline_kwargs: dict[str, Any]) -> TurnEngine:
    """Construct the engine selected by ``architecture.engine``.

    ``pipeline_kwargs`` is the full, already-resolved provider/session/logging
    bundle. It is passed through untouched, so the baseline keeps exactly the
    arguments it had before this factory existed.
    """
    engine_id = selected_engine(config)

    if engine_id == ENGINE_A0:
        from ..agent.pipeline import BaselinePipeline

        engine = BaselinePipeline(**pipeline_kwargs)
        # Tag the instance from the outside. ``pipeline.py`` is frozen: the
        # engine's identity has to be recorded, but A0's behaviour must not
        # change, and a class edit there would be a behaviour change nobody
        # asked for.
        engine.engine_id = ENGINE_A0
        return engine

    # ENGINE_A1. Imported lazily so a broken/incomplete A1 module cannot stop the
    # baseline, the CLI, or the production bridge from importing.
    from .a1 import A1Engine

    return A1Engine(**pipeline_kwargs)


def describe_engine(engine: Any) -> dict[str, Any]:
    """Machine-readable identity of an engine, for logs and freeze records."""
    return {
        "engine": str(getattr(engine, "engine_id", ENGINE_A0)),
        "components": dict(getattr(engine, "components", {}) or {}),
    }
