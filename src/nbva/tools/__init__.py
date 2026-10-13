"""Deterministic business tools for the Nile Bites research environment.

Design rule: every business fact returned to the language model comes from here.
The tools are pure functions of (restaurant dataset, order store). They contain
no model calls, no randomness, and no network access, so a given input always
produces byte-identical output. That determinism is what makes database/tool
accuracy a measurable secondary diagnostic rather than a confound.
"""

from .registry import (
    TOOL_REGISTRY,
    ToolCallRecord,
    ToolRegistry,
    build_default_registry,
    get_tool,
    tool_names,
)

__all__ = [
    "TOOL_REGISTRY",
    "ToolCallRecord",
    "ToolRegistry",
    "build_default_registry",
    "get_tool",
    "tool_names",
]
