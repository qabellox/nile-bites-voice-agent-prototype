"""Language-understanding adapter interface.

The baseline pipeline depends only on this interface, so any ASR, LLM or TTS
implementation can be swapped without touching the pipeline, the tools, or the
logging. That modularity is a requirement of the methodology: the intervention
must be able to change one component at a time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
import json
import time

from ..tools.registry import ToolRegistry


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str = ""


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None
    stop_reason: str | None = None
    error: str | None = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        ...

    def describe(self) -> dict[str, Any]:
        ...


def run_tool_loop(
    provider: LLMProvider,
    registry: ToolRegistry,
    messages: list[dict[str, Any]],
    max_iterations: int = 6,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Drive provider -> tool -> provider until the model answers in prose.

    Returns ``(final_text, transcript_messages, tool_call_records, error)``.

    The loop is bounded by ``max_iterations`` so a model that keeps calling tools
    cannot hang a live voice session.
    """
    convo = list(messages)
    error: str | None = None

    for _ in range(max(1, max_iterations)):
        response = None
        last_exception: Exception | None = None
        for attempt in range(3):
            try:
                response = provider.complete(convo, registry.llm_schemas())
                break
            except Exception as exc:  # provider failure must not kill the session
                last_exception = exc
                message = str(exc).lower()
                transient = (
                    "429" in message
                    or "rate limit" in message
                    or "too many requests" in message
                    or "timeout" in message
                    or "temporarily unavailable" in message
                    or " 500" in message
                    or " 502" in message
                    or " 503" in message
                    or " 504" in message
                )
                if not transient or attempt == 2:
                    break
                time.sleep(1.0 * (attempt + 1))
        if response is None:
            assert last_exception is not None
            return "", convo, registry.take_calls(), (
                f"{type(last_exception).__name__}: {last_exception}"
            )

        if response.error:
            return response.text, convo, registry.take_calls(), response.error

        if not response.wants_tools:
            return response.text, convo, registry.take_calls(), error

        convo.append(
            {
                "role": "assistant",
                "content": response.text or "",
                "tool_calls": [
                    {
                        "id": c.call_id,
                        "type": "function",
                        # The API requires arguments as a JSON string; the
                        # in-memory ToolCall holds a dict, so serialize here.
                        # Passing the dict through caused a 400 on the second
                        # loop iteration and an empty final answer.
                        "function": {
                            "name": c.name,
                            "arguments": json.dumps(c.arguments, ensure_ascii=False),
                        },
                    }
                    for c in response.tool_calls
                ],
            }
        )

        for call in response.tool_calls:
            result = registry.dispatch(call.name, call.arguments)
            convo.append(
                {
                    "role": "tool",
                    "tool_call_id": call.call_id or call.name,
                    "name": call.name,
                    "content": _as_tool_content(result),
                }
            )

    error = f"tool loop exceeded {max_iterations} iterations"
    return "", convo, registry.take_calls(), error


def _as_tool_content(result: Any) -> str:
    import json

    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)
