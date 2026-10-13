"""Concrete LLM adapters.

Every adapter exposes the same :class:`~nbva.llm.base.LLMProvider` interface and
is selected by ``llm.provider`` in ``config/llm.yaml``. Adding a provider means
adding one class and one registry entry; the pipeline is untouched.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from .base import LLMResponse, ToolCall


def _env_key(name: str | None, fallback: str = "") -> str:
    if not name:
        return fallback
    return os.environ.get(name, fallback)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class _BaseProvider:
    name = "base"

    def __init__(self, model: str, temperature: float = 0.0, max_tokens: int = 400,
                 top_p: float = 1.0, timeout: float = 60.0, **extra: Any):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.timeout = timeout
        self.extra = extra

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            **{k: v for k, v in self.extra.items() if k != "client"},
        }

    def preflight(self) -> tuple[bool, str | None]:
        """Can this provider actually answer? Returns ``(ok, problem)``.

        Back-compatible pair form of :meth:`preflight_status`. ``unverified``
        is deliberately not reported as failure here; the CLI surfaces it as an
        advisory through ``preflight_status`` instead.
        """
        state, detail = self.preflight_status()
        return state != "blocked", detail

    def preflight_status(self) -> tuple[str, str | None]:
        """Report readiness as one of ``ready`` / ``unverified`` / ``blocked``.

        The three states are kept apart on purpose. A hosted provider whose
        credentials have not been supplied yet is not a broken
        installation - it is a decision that has not been made. Collapsing
        that into "failed" would train everyone to ignore preflight output,
        and collapsing it into "ready" would let a run start that cannot
        possibly answer.
        """
        # Offline providers have no endpoint to check, so they are genuinely
        # ready by construction.
        return "ready", None


# ---------------------------------------------------------------------------


class OpenAIProvider(_BaseProvider):
    name = "openai"
    #: Set by subclasses that talk to an OpenAI-shaped HTTP endpoint.
    api_key_env: str = "OPENAI_API_KEY"
    sdk_module: str = "openai"

    def __init__(self, model: str, api_key_env: str = "OPENAI_API_KEY",
                 base_url: str | None = None, **kw: Any):
        super().__init__(model, **kw)
        self.api_key = _env_key(api_key_env, "")
        self.base_url = base_url
        if not self.api_key:
            raise RuntimeError(
                f"{api_key_env} is not set. Export it, or set llm.provider to a "
                f"local option such as openai_compatible (Ollama)."
            )
        from openai import OpenAI

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=base_url,
            timeout=self.timeout,
        )

    def preflight_status(self) -> tuple[str, str | None]:
        """Check credentials and reachability without choosing a model for anyone.

        No default model is assumed: the researcher picks the hosted model, so
        this only reports whether the configured one is present and reachable.
        """
        if not self.api_key:
            return "unverified", (
                f"{self.api_key_env} is not set, so {self.name} cannot be reached. "
                "This is a pending decision, not a fault: set the key when a hosted "
                "provider has been chosen."
            )
        try:
            models = self.client.models.list()
            available = [getattr(m, "id", "") for m in getattr(models, "data", [])]
        except Exception as exc:
            return "blocked", f"{self.name} endpoint did not answer: {type(exc).__name__}: {exc}"
        if self.model and available and self.model not in available:
            return "blocked", (
                f"{self.name} answered but model {self.model!r} is not in its list. "
                f"Available: {', '.join(sorted(available)[:12])}"
            )
        return "ready", None

    def complete(self, messages, tools=None) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message
        calls: list[ToolCall] = []
        # Extract tool calls from the response message.
        # The OpenAI client may populate msg.tool_calls when the model
        # decides to use a tool; we capture any that are present.
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                args: dict[str, Any] = {}
                raw_args = getattr(tc.function, "arguments", "") or "{}"
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {"_unparsed": raw_args}
                calls.append(ToolCall(name=tc.function.name, arguments=args, call_id=tc.id or ""))
        return LLMResponse(
            text=(msg.content or "").strip(),
            tool_calls=calls,
            raw=resp,
            stop_reason=choice.finish_reason,
        )

    def complete_stream(self, messages, tools=None):
        """Stream a completion, yielding ``(kind, payload)`` events.

        Events: ``("text", str)`` for content deltas as they arrive, then a
        single ``("done", LLMResponse)`` with the fully assembled response
        (text + parsed tool calls, same shape as :meth:`complete`). Streaming
        lets downstream stages (sentence splitter -> TTS) start before the
        model finishes, instead of waiting for the whole reply.
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        stream = self.client.chat.completions.create(**kwargs)
        text_parts: list[str] = []
        # Tool-call deltas arrive fragmented; accumulate per index.
        tc_buf: dict[int, dict[str, Any]] = {}
        stop_reason: str | None = None
        for chunk in stream:
            if not getattr(chunk, "choices", None):
                continue
            choice = chunk.choices[0]
            if getattr(choice, "finish_reason", None):
                stop_reason = choice.finish_reason
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            content = getattr(delta, "content", None)
            if content:
                text_parts.append(content)
                yield ("text", content)
            for tc in getattr(delta, "tool_calls", None) or []:
                idx = getattr(tc, "index", 0) or 0
                entry = tc_buf.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if getattr(tc, "id", None):
                    entry["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        entry["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        entry["arguments"] += fn.arguments
        calls: list[ToolCall] = []
        for idx in sorted(tc_buf):
            entry = tc_buf[idx]
            args: dict[str, Any] = {}
            try:
                args = json.loads(entry["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {"_unparsed": entry["arguments"]}
            calls.append(ToolCall(name=entry["name"], arguments=args,
                                  call_id=entry["id"] or ""))
        yield ("done", LLMResponse(
            text="".join(text_parts).strip(),
            tool_calls=calls,
            raw=None,
            stop_reason=stop_reason,
        ))


class OpenAICompatibleProvider(OpenAIProvider):
    """Ollama, vLLM, LM Studio, llama.cpp server, or any OpenAI-shaped endpoint."""

    name = "openai_compatible"

    def __init__(self, model: str, base_url: str = "http://localhost:11434/v1",
                 api_key_env: str | None = None, api_key_fallback: str = "not-needed",
                 **kw: Any):
        # Bypass the parent key check: local servers usually need a dummy key.
        _BaseProvider.__init__(self, model, **kw)
        self.base_url = base_url
        self.api_key = _env_key(api_key_env, api_key_fallback) or api_key_fallback
        from openai import OpenAI

        self.client = OpenAI(
            api_key=self.api_key, base_url=base_url, timeout=self.timeout, max_retries=0
        )

    def describe(self) -> dict[str, Any]:
        return {**super().describe(), "base_url": self.base_url}

    def preflight_status(self) -> tuple[str, str | None]:
        """Check the server answers and that it has actually loaded the model.

        A local server being up is not enough: Ollama serves a ``/models`` list
        before the weights are resident, so a run can pass a naive reachability
        check and then stall on the first turn while the model loads. This asks
        for the list and confirms the configured model is present.
        """
        try:
            models = [m.id for m in self.client.models.list()]
        except Exception as exc:
            return "blocked", f"{self.base_url} did not answer ({type(exc).__name__}: {exc})"

        if not models:
            return "blocked", f"{self.base_url} is up but serves no models; load one first"
        if self.model not in models:
            return "blocked", (
                f"model {self.model!r} is not loaded at {self.base_url}; "
                f"available: {', '.join(sorted(models))}"
            )
        return "ready", None


class AnthropicProvider(_BaseProvider):
    name = "anthropic"

    def __init__(self, model: str, api_key_env: str = "ANTHROPIC_API_KEY", **kw: Any):
        kw.setdefault("max_tokens", 800)
        super().__init__(model, **kw)
        self.api_key = _env_key(api_key_env, "")
        if not self.api_key:
            raise RuntimeError(f"{api_key_env} is not set.")
        import anthropic

        self.client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout)

    def preflight_status(self) -> tuple[str, str | None]:
        if not self.api_key:
            return "unverified", (
                f"{self.api_key_env} is not set, so anthropic cannot be reached. "
                "This is a pending decision, not a fault."
            )
        import importlib.util

        if importlib.util.find_spec("anthropic") is None:
            return "blocked", (
                "ANTHROPIC_API_KEY is set but the 'anthropic' package is not installed. "
                "Install it with: pip install anthropic"
            )
        try:
            self.client.models.list()
        except Exception as exc:
            return "blocked", f"anthropic endpoint did not answer: {type(exc).__name__}: {exc}"
        return "ready", None

    def complete(self, messages, tools=None) -> LLMResponse:
        system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
        convo = [
            {"role": m["role"], "content": _strip_anthropic_tools(m)}
            for m in messages
            if m.get("role") != "system"
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": convo,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ]
        resp = self.client.messages.create(**kwargs)
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(getattr(block, "text", ""))
            elif btype == "tool_use":
                calls.append(
                    ToolCall(
                        name=block.name,
                        arguments=dict(block.input or {}),
                        call_id=getattr(block, "id", "") or "",
                    )
                )
        return LLMResponse(
            text="\n".join(p for p in text_parts if p).strip(),
            tool_calls=calls,
            raw=resp,
            stop_reason=getattr(resp, "stop_reason", None),
        )


def _strip_anthropic_tools(message: dict[str, Any]) -> dict[str, Any]:
    out = {"role": message.get("role", "user"), "content": message.get("content", "")}
    if message.get("role") == "assistant" and message.get("tool_calls"):
        blocks: list[dict[str, Any]] = []
        if message.get("content"):
            blocks.append({"type": "text", "text": message["content"]})
        for tc in message["tool_calls"]:
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "input": tc["function"]["arguments"],
                }
            )
        out["content"] = blocks
    elif message.get("role") == "tool":
        out["content"] = [
            {
                "type": "tool_result",
                "tool_use_id": message.get("tool_call_id", ""),
                "content": str(message.get("content", "")),
            }
        ]
    return out


class GoogleProvider(_BaseProvider):
    name = "google"

    def __init__(self, model: str, api_key_env: str = "GEMINI_API_KEY", **kw: Any):
        super().__init__(model, **kw)
        self.api_key = _env_key(api_key_env, "")
        if not self.api_key:
            raise RuntimeError(f"{api_key_env} is not set.")
        from google import genai
        from google.genai import types

        self._types = types
        self.client = genai.Client(api_key=self.api_key)

    def preflight_status(self) -> tuple[str, str | None]:
        if not self.api_key:
            return "unverified", (
                f"{self.api_key_env} is not set, so google cannot be reached. "
                "This is a pending decision, not a fault."
            )
        try:
            self.client.models.list()
        except Exception as exc:
            return "blocked", f"google endpoint did not answer: {type(exc).__name__}: {exc}"
        return "ready", None

    def complete(self, messages, tools=None) -> LLMResponse:
        system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
        contents = [
            {"role": "model" if m.get("role") == "assistant" else "user",
             "parts": [{"text": str(m.get("content", ""))}]}
            for m in messages
            if m.get("role") != "system"
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "contents": contents,
            "config": {
                "temperature": self.temperature,
                "max_output_tokens": self.max_tokens,
            },
        }
        if system:
            kwargs["config"]["system_instruction"] = system
        if tools:
            kwargs["config"]["tools"] = [
                {
                    "function_declarations": [
                        {
                            "name": t["function"]["name"],
                            "description": t["function"]["description"],
                            "parameters": t["function"]["parameters"],
                        }
                        for t in tools
                    ]
                }
            ]
        resp = self.client.models.generate_content(**kwargs)
        text = getattr(resp, "text", "") or ""
        calls: list[ToolCall] = []
        for fc in getattr(resp, "function_calls", None) or []:
            calls.append(ToolCall(name=fc.name, arguments=dict(fc.args or {}), call_id=fc.id or ""))
        return LLMResponse(text=text.strip(), tool_calls=calls, raw=resp)


class ScriptedProvider(_BaseProvider):
    """Deterministic offline stand-in with NO language model behind it.

    It exists so the pipeline, tools, state, logging and tests can be exercised
    with zero network and zero keys. It must never be used to collect research
    data; the logger stamps the provider name into every record so any accidental
    use is visible in the data, and `build_provider` refuses it outright when
    `experiment.is_research_run` is true.

    Two modes:
      * no plan: one fixed reply, no tools.
      * plan: a list of steps replayed in order, one per `complete()` call, so a
        task can be driven end to end offline. A step is either
        ``{"tool": name, "arguments": {...}}`` or ``{"text": "..."}``. Tool
        steps make the loop call the deterministic layer and come back for the
        next step, which is what lets state assertions be exercised.
    """

    name = "scripted"

    def __init__(
        self,
        model: str = "scripted-stub",
        fixed_reply: str | None = None,
        plan: list[dict[str, Any]] | None = None,
        **kw: Any,
    ):
        super().__init__(model, **kw)
        self.fixed_reply = fixed_reply or "تمام."
        self.plan = [dict(step) for step in (plan or [])]
        self._call_count = 0

    def _last_user_text(self, messages) -> str:
        for m in reversed(messages):
            if m.get("role") == "user":
                return str(m.get("content", ""))
        return ""

    def complete(self, messages, tools=None) -> LLMResponse:
        self._call_count += 1
        user_text = self._last_user_text(messages)
        step: dict[str, Any] | None = None
        if self.plan:
            # The final text step answers every later turn, so a plan does not
            # have to be as long as the conversation.
            step = self.plan[min(self._call_count - 1, len(self.plan) - 1)]

        if step is not None and step.get("tool"):
            name = str(step["tool"])
            return LLMResponse(
                text=str(step.get("text", "")),
                tool_calls=[
                    ToolCall(
                        name=name,
                        arguments=dict(step.get("arguments") or {}),
                        call_id=str(step.get("call_id") or f"scripted-{self._call_count}"),
                    )
                ],
                raw={"scripted": True, "step": self._call_count, "tool": name},
                stop_reason="tool_calls",
            )

        if step is not None and "text" in step:
            return LLMResponse(
                text=str(step["text"]),
                tool_calls=[],
                raw={"scripted": True, "step": self._call_count},
                stop_reason="stop",
            )

        return LLMResponse(
            text=f"{self.fixed_reply} [scripted stub, no model called; heard: {user_text[:60]}]",
            tool_calls=[],
            raw={"scripted": True, "call_index": self._call_count},
            stop_reason="stop",
        )


PROVIDERS = {
    "openai": OpenAIProvider,
    "openai_compatible": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
    "google": GoogleProvider,
    "scripted": ScriptedProvider,
}

# Adapters with no language model behind them. They exist for tests and offline
# pipeline checks and must never back a real trial, because the methodology
# requires a model that actually produces the response.
STUB_PROVIDERS = {"scripted"}


def build_provider(config) -> _BaseProvider:
    provider_name = str(config.get("llm.provider", "scripted")).strip().lower()
    if provider_name not in PROVIDERS:
        raise ValueError(
            f"unknown llm.provider {provider_name!r}; available: {sorted(PROVIDERS)}"
        )
    if provider_name in STUB_PROVIDERS and _as_bool(config.get("experiment.is_research_run")):
        raise ValueError(
            f"llm.provider {provider_name!r} is an offline stub with no model behind it and "
            "cannot be used for a research run; set experiment.is_research_run=false or "
            "choose a real provider"
        )
    cls = PROVIDERS[provider_name]
    section = config.section("llm") or {}
    kwargs: dict[str, Any] = {
        "temperature": float(section.get("temperature", 0.0)),
        "max_tokens": int(section.get("max_tokens", 400)),
        "top_p": float(section.get("top_p", 1.0)),
        "timeout": float(section.get("timeout_seconds", 60)),
    }
    provider_section = section.get(provider_name) or {}
    if isinstance(provider_section, dict):
        for key in ("api_key_env", "base_url", "api_key_fallback", "fixed_reply", "plan"):
            if key in provider_section:
                kwargs[key] = provider_section[key]
    return cls(model=str(section.get("model", "unknown")), **kwargs)
