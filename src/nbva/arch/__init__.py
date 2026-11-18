"""Turn engines: the architecture factor of the experiment.

Two arms exist by design:

``a0``
    Conventional sequential baseline — :class:`~nbva.agent.pipeline.BaselinePipeline`,
    frozen and unchanged.
``a1``
    Reliability/efficiency architecture — context manager, orchestration,
    structured tool gateway, validation layer, latency path.

The package exists so the experiment can select an architecture by configuration
instead of by editing call sites, and so both arms are constructed from the same
providers with the same settings.
"""

from .base import (
    A1_COMPONENTS,
    ENGINE_A0,
    ENGINE_A1,
    KNOWN_ENGINES,
    TurnEngine,
    build_turn_engine,
    describe_engine,
    selected_engine,
)
from .gateway import ToolGateway

__all__ = [
    "A1_COMPONENTS",
    "ENGINE_A0",
    "ENGINE_A1",
    "KNOWN_ENGINES",
    "ToolGateway",
    "TurnEngine",
    "build_turn_engine",
    "describe_engine",
    "selected_engine",
]
