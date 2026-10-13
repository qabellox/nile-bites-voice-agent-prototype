"""Streaming responder: token-stream LLM tool loop + sentence-stream TTS.

Mirrors the semantics of ``run_tool_loop`` (bounded tool iterations, same
registry) but consumes the model's output as a token stream: each completed
sentence is sent to TTS while the model is still generating the rest, so the
first audio exists before the full reply does. Audio leaves through an
``on_sentence`` callback, which lets one code path serve both queued live
playback and batched HTTP responses (concatenate server-side).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

_SENT_END = re.compile(r"(?<=[.!?؟])\s+")
_HAS_AR = re.compile(r"[\u0600-\u06FF]")
_HAS_LAT = re.compile(r"[A-Za-z]")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_END.split(text or "") if p.strip()]
    return parts or ([text.strip()] if (text or "").strip() else [])


def detect_lang(text: str) -> str | None:
    if not text:
        return None
    ar, lat = bool(_HAS_AR.search(text)), bool(_HAS_LAT.search(text))
    if ar and lat:
        return "mixed"
    if ar:
        return "ar"
    if lat:
        return "en"
    return None


@dataclass
class StreamingResponder:
    llm: Any = None
    registry: Any = None
    tts: Any = None
    system_prompt: str = ""
    max_iterations: int = 6

    def _voice_for(self, text: str) -> str | None:
        if getattr(self.tts, "name", None) == "edge":
            if detect_lang(text) == "en":
                return getattr(self.tts, "fallback_voice", None) or None
        return None

    def _stream_once(self, messages: list[dict[str, Any]], tools, on_token=None):
        """One model call, streamed if the provider supports it."""
        stream_fn = getattr(self.llm, "complete_stream", None)
        if not callable(stream_fn):
            resp = self.llm.complete(messages, tools)
            if on_token and resp.text:
                on_token(resp.text)
            return resp.text, resp.tool_calls, resp.stop_reason
        text_parts: list[str] = []
        calls: list[Any] = []
        stop = None
        for kind, payload in stream_fn(messages, tools):
            if kind == "text":
                text_parts.append(payload)
                if on_token:
                    on_token(payload)
            elif kind == "done":
                calls = payload.tool_calls
                stop = payload.stop_reason
        return "".join(text_parts).strip(), calls, stop

    def run(
        self,
        user_text: str,
        history: list[dict[str, Any]] | None = None,
        on_sentence: Callable[[Any, int, int, str], None] | None = None,
        on_token: Callable[[str], None] | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Run the streamed tool loop. Returns text, tools, audio segments."""
        t_start = time.monotonic()
        messages: list[dict[str, Any]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(history or [])
        messages.append({"role": "user", "content": user_text})
        tools = self.registry.llm_schemas() if self.registry else None

        first_token_at: float | None = None

        def _tok(t: str) -> None:
            nonlocal first_token_at
            if first_token_at is None:
                first_token_at = time.monotonic()
            if on_token:
                on_token(t)

        final_text = ""
        for _ in range(max(1, self.max_iterations)):
            text, calls, _stop = self._stream_once(messages, tools, on_token=_tok)
            if not calls:
                final_text = text
                break
            import json as _json

            messages.append({
                "role": "assistant",
                "content": text or "",
                "tool_calls": [
                    {"id": c.call_id, "type": "function",
                     "function": {"name": c.name,
                                 "arguments": _json.dumps(c.arguments, ensure_ascii=False)}}
                    for c in calls
                ],
            })
            for call in calls:
                result = self.registry.dispatch(call.name, call.arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.call_id or call.name,
                    "name": call.name,
                    "content": _json.dumps(result, ensure_ascii=False, default=str),
                })
        else:
            final_text = final_text or ""

        tool_records = self.registry.take_calls() if self.registry else []
        llm_ms = (time.monotonic() - t_start) * 1000.0

        # Sentence-stream TTS: synthesize each sentence as it completes.
        sentences = split_sentences(final_text)
        segments: list[dict[str, Any]] = []
        first_audio_at: float | None = None
        if self.tts is not None and getattr(self.tts, "name", None) != "null":
            for i, sent in enumerate(sentences):
                if not sent:
                    continue
                try:
                    ove = self._voice_for(sent)
                    res = self.tts.synthesize(sent, **({"voice": ove} if ove else {}))
                except Exception as exc:
                    segments.append({"index": i, "text": sent, "error": f"{type(exc).__name__}: {exc}"})
                    continue
                if res.error or res.audio is None:
                    segments.append({"index": i, "text": sent, "error": res.error or "no audio"})
                    continue
                if first_audio_at is None:
                    first_audio_at = time.monotonic()
                seg = {"index": i, "text": sent, "audio": res.audio,
                       "sample_rate": int(res.sample_rate or 16000), "voice": res.voice}
                segments.append(seg)
                if on_sentence:
                    on_sentence(res.audio, seg["sample_rate"], i, sent)

        if events is not None:
            events.append({"kind": "llm_done", "t": time.monotonic(),
                           "tool_calls": len(tool_records)})
            if first_audio_at is not None:
                events.append({"kind": "first_audio", "t": first_audio_at})

        return {
            "text": final_text,
            "tool_calls": tool_records,
            "sentences": [{"index": s["index"], "text": s["text"],
                           "voice": s.get("voice"), "error": s.get("error")}
                          for s in segments],
            "audio_segments": [(s["audio"], s["sample_rate"]) for s in segments
                               if "audio" in s],
            "llm_ms": round(llm_ms, 1),
            "first_token_ms": round((first_token_at - t_start) * 1000.0, 1)
            if first_token_at else None,
            "first_audio_ms": round((first_audio_at - t_start) * 1000.0, 1)
            if first_audio_at else None,
        }
