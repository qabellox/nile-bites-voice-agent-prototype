"""Streaming turn orchestration (development/validation only).

Feeds PCM through chunked ASR windows while understanding, speculative tool
preparation, and (optionally) a speculative LLM response run concurrently;
at endpoint the final interpretation is committed and the response is
produced with sentence-streaming TTS. All overlap is recorded as timestamped
events so the latency win (or lack of one) is measurable, not assumed.

Deliberately writes no research records: no TurnRecord logging, no trial
dataset, no use of the research turn counter. It reuses the live business
tools and session dialogue state so multi-turn context still works in
development testing. Full per-turn detail goes to a development JSONL trace
(``trace.py``), never to the research log.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from ..asr.base import resample
from ..obs.events import utc_now_iso
from .chunker import SlidingWindowBuffer
from .context import ContextTracker
from .hypothesis import HypothesisTracker
from .interrupt import InterruptController
from .respond import StreamingResponder
from .speculative import SpeculativeTools
from .trace import StreamTrace
from .understand import IncrementalUnderstanding


def _lang_label(english_pct: float | None) -> str | None:
    if english_pct is None:
        return None
    if english_pct >= 80.0:
        return "en"
    if english_pct <= 20.0:
        return "ar"
    return "mixed"


@dataclass
class StreamingTurnLoop:
    """One streaming loop over an existing AgentApp. Reusable across turns."""

    app: Any = None
    window_s: float = 4.0
    step_s: float = 1.0
    agree_n: int = 2
    min_words_tools: int = 3
    min_words_llm: int = 4
    speculative_llm: bool = True
    max_revisions: int = 3
    max_iterations: int = 6
    player: Any = None
    on_audio: Callable[[Any, int, int, str], None] | None = None
    trace_dir: Any = None

    _dev_turn: int = 0
    _alias_memory: dict = field(default_factory=dict, repr=False)
    _interrupt: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._interrupt = InterruptController(player=self.player)

    def _components(self):
        session = self.app.session
        return {
            "asr": self.app.pipeline.asr,
            "analyzer": getattr(self.app.pipeline, "analyzer", None),
            "registry": session.tools,
            "llm": self.app.llm,
            "tts": self.app.tts,
            "system_prompt": getattr(self.app.pipeline, "system_prompt", ""),
            "history": list(session.message_history()[-session.max_history_turns:]),
            "session": session,
        }

    def _seed_aliases(self, tracker: ContextTracker) -> None:
        for alias, item_id in self._alias_memory.items():
            tracker.menu_aliases.setdefault(alias, item_id)

    def _harvest_aliases(self, result: Any) -> None:
        """Remember menu name spellings from any search result blob."""
        try:
            import json as _json

            blob = _json.dumps(result, ensure_ascii=False, default=str).lower()
        except Exception:
            return
        import re as _re

        for m in _re.finditer(r'"name_en"\s*:\s*"([^"]+)"', blob):
            name = m.group(1).strip().lower()
            if name:
                self._alias_memory.setdefault(name, name)
        for m in _re.finditer(r'"name_ar_eg"\s*:\s*"([^"]+)"', blob):
            name = m.group(1).strip().lower()
            if name:
                self._alias_memory.setdefault(name, name)
        for m in _re.finditer(r'"item_id"\s*:\s*"([^"]+)"', blob):
            item_id = m.group(1).strip()
            if item_id:
                self._alias_memory.setdefault(item_id.lower(), item_id)

    def run_clip(self, pcm: np.ndarray, sample_rate: int = 16000,
                 realtime: bool = True, chunk_s: float = 0.25) -> dict[str, Any]:
        """Run a full streaming turn over PCM audio. Returns the debrief."""
        deps = self._components()
        self._dev_turn += 1
        trace = StreamTrace(
            session_id=getattr(deps["session"], "session_id", ""),
            turn_id=f"stream-dev-{self._dev_turn}",
            log_dir=self.trace_dir,
        )
        trace.start(header={
            "mode": "streaming-intervention-dev",
            "asr_provider": getattr(deps["asr"], "name", None),
            "asr_model": getattr(deps["asr"], "model", getattr(deps["asr"], "model_name", None)),
            "asr_language": getattr(deps["asr"], "language", None),
            "asr_beam": getattr(deps["asr"], "beam_size", None),
            "llm_provider": getattr(deps["llm"], "name", None),
            "llm_model": getattr(deps["llm"], "model", None),
            "tts_provider": getattr(deps["tts"], "name", None),
            "tts_voice": getattr(deps["tts"], "voice", None),
            "window_s": self.window_s,
            "step_s": self.step_s,
            "agree_n": self.agree_n,
        })
        events: list[dict[str, Any]] = []
        t_start = time.monotonic()
        ev_lock = threading.Lock()

        def ev(kind, **kw):
            with ev_lock:
                e = {"kind": kind, "t": round(time.monotonic() - t_start, 3)}
                e.update(kw)
                events.append(e)
                trace.log(kind, **kw)
            return e

        data = np.ascontiguousarray(np.asarray(pcm, dtype=np.float32).reshape(-1))
        asr_rate = int(getattr(deps["asr"], "sample_rate", sample_rate) or sample_rate)
        if int(sample_rate) != asr_rate:
            data = resample(data, int(sample_rate), asr_rate)

        chunker = SlidingWindowBuffer(sample_rate=asr_rate, window_s=self.window_s, step_s=self.step_s)
        tracker = HypothesisTracker(agree_n=self.agree_n, max_revisions=self.max_revisions)
        understand = IncrementalUnderstanding(
            analyzer=deps["analyzer"], min_words_tools=self.min_words_tools,
            min_words_llm=self.min_words_llm)
        context = ContextTracker()
        self._seed_aliases(context)
        spec_tools = SpeculativeTools(registry=deps["registry"])
        responder = StreamingResponder(
            llm=deps["llm"], registry=deps["registry"], tts=deps["tts"],
            system_prompt=deps["system_prompt"], max_iterations=self.max_iterations)

        ev("turn_start", audio_s=round(float(data.size) / asr_rate, 2))
        trace.log("speech_start")
        decode_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nbva-dec")
        pending: dict[int, Any] = {}
        next_observe = 0
        llm_future = None
        llm_input_text: str | None = None
        llm_stale = False
        spec_offset_ms = 0.0
        endpoint_t: float | None = None
        last_text = ""
        last_index = -1
        last_detected: str | None = None

        def decode_task(window):
            t0 = time.monotonic()
            trace.log("partial_asr_start", window=window.index)
            res = deps["asr"].transcribe(window.audio)
            trace.log("partial_asr_end", window=window.index,
                      ms=round((time.monotonic() - t0) * 1000.0, 1))
            return window.index, res, round((time.monotonic() - t0) * 1000.0, 1)

        def fire_spec_llm(text: str):
            nonlocal llm_future, llm_input_text, llm_stale, spec_offset_ms
            if llm_future is not None or not self.speculative_llm:
                return
            llm_input_text = text
            llm_stale = False
            spec_offset_ms = round((time.monotonic() - t_start) * 1000.0, 1)
            ev("spec_llm_started", text=text)
            llm_future = decode_pool.submit(
                responder.run, text, list(deps["history"]))

        def drain_observed():
            nonlocal next_observe, llm_stale, last_text, last_index, last_detected
            while next_observe in pending and pending[next_observe].done():
                idx, asr_res, dms = pending.pop(next_observe).result()
                next_observe += 1
                last_text = asr_res.text or ""
                last_index = idx
                last_detected = getattr(asr_res, "language", None)
                ev("window_decoded", window=idx, asr_ms=dms,
                   detected=getattr(asr_res, "language", None))
                ev("partial_transcript", window=idx, text=last_text)
                for te in tracker.observe(last_text, idx):
                    if te.kind == "partial":
                        continue
                    ev(te.kind, text=te.text, window=te.window_index, words=te.words)
                    if te.kind == "commit":
                        snap = understand.on_commit(te.text)
                        ev("understood", text=te.text, english_pct=snap.english_pct,
                           lang=_lang_label(snap.english_pct),
                           ready_tools=snap.ready_for_tools, ready_llm=snap.ready_for_llm)
                        ctx_ev = context.update(te.text, commit_id=idx)
                        ev("context_update", commit_id=idx, patches=ctx_ev["patches"],
                           state=ctx_ev["state"])
                        ev("incremental_intent", intent=ctx_ev["state"]["intent"],
                           confidence=ctx_ev["state"]["confidence"])
                        if snap.ready_for_tools:
                            spec_tools.prepare(te.text)
                            ev("spec_tools_fired", text=te.text)
                        if snap.ready_for_llm:
                            fire_spec_llm(te.text)
                    elif te.kind == "revise":
                        llm_stale = True
                        ev("spec_llm_superseded", text=te.text)

        try:
            step = max(1, int(chunk_s * asr_rate))
            for off in range(0, max(1, data.size), step):
                trace.log("audio_chunk_received", offset_s=round(off / asr_rate, 2))
                for w in chunker.push(data[off:off + step]):
                    pending[w.index] = decode_pool.submit(decode_task, w)
                drain_observed()
                if realtime:
                    time.sleep(chunk_s)
            tail = chunker.flush()
            if tail is not None:
                pending[tail.index] = decode_pool.submit(decode_task, tail)
            endpoint_t = time.monotonic()
            ev("endpoint")
            trace.log("speech_end")
            deadline = time.monotonic() + 60.0
            while next_observe in pending and time.monotonic() < deadline:
                if pending[next_observe].done():
                    drain_observed()
                else:
                    time.sleep(0.02)
            drain_observed()

            for te in tracker.finalize(last_text, max(last_index, 0)):
                if te.kind != "partial":
                    ev(te.kind, text=te.text, window=te.window_index, words=te.words)
            final_text = tracker.committed_text

            snap = understand.on_commit(final_text) if final_text else None
            if final_text:
                spec_tools.prepare(final_text)
            final_cached = spec_tools.await_prefix(final_text, timeout_s=10.0) if final_text else None
            if final_cached is not None:
                self._harvest_aliases(final_cached)
            ev("spec_cache", reused=spec_tools.reused_prefixes,
               prepared=spec_tools.prepared_prefixes,
               final_hit=final_cached is not None)
            ctx_fin = context.finalize()
            ev("context_final", state=ctx_fin["state"])

            deps["session"].add_turn("user", final_text, utc_now_iso())

            # Drain speculative records so the final tool list is clean;
            # speculative evidence is reported separately.
            spec_records = deps["registry"].take_calls()

            final = None
            if (llm_future is not None and not llm_stale
                    and llm_input_text == final_text):
                final = llm_future.result(timeout=60.0)
                for key in ("first_token_ms", "first_audio_ms"):
                    if final.get(key) is not None:
                        final[key] = round(final[key] + spec_offset_ms, 1)
                ev("reused_speculative_response")
            else:
                if llm_future is not None:
                    ev("speculative_discarded")
                resp_offset_ms = round((time.monotonic() - t_start) * 1000.0, 1)
                final = responder.run(
                    final_text, list(deps["history"]),
                    on_sentence=self._make_sentence_cb(ev, t_start),
                    events=None)
                for key in ("first_token_ms", "first_audio_ms"):
                    if final.get(key) is not None:
                        final[key] = round(final[key] + resp_offset_ms, 1)
            for c in (final.get("tool_calls") or []):
                res_blob = c.get("result")
                if res_blob is not None:
                    self._harvest_aliases(res_blob)
            deps["session"].add_turn("assistant", final["text"], utc_now_iso())

            total_ms = round((time.monotonic() - t_start) * 1000.0, 1)
            endpoint_ms = round((endpoint_t - t_start) * 1000.0, 1) if endpoint_t else None
            trace_path = trace.save()
            return {
                "final_transcript": final_text,
                "asr_detected_language": last_detected,
                "revisions": tracker.revision_count,
                "response_text": final["text"],
                "tool_calls": final["tool_calls"],
                "speculative_tool_calls": spec_records,
                "audio_segments": final["audio_segments"],
                "sentences": final["sentences"],
                "llm_ms": final["llm_ms"],
                "first_token_ms": final["first_token_ms"],
                "first_audio_ms": final["first_audio_ms"],
                "understanding": {
                    "english_pct": snap.english_pct if snap else None,
                    "words": snap.word_count if snap else 0,
                } if snap else None,
                "context": ctx_fin["state"],
                "spec_stats": {"prepared": spec_tools.prepared_prefixes,
                               "reused": spec_tools.reused_prefixes},
                "total_ms": total_ms,
                "endpoint_ms": endpoint_ms,
                "events": events,
                "trace_path": trace_path,
            }
        finally:
            decode_pool.shutdown(wait=False, cancel_futures=True)
            spec_tools.shutdown()

    def _make_sentence_cb(self, ev, t_start):
        def _cb(audio, sr, idx, text):
            ev("tts_sentence", index=idx, text=text)
            if self.on_audio is not None:
                self.on_audio(audio, sr, idx, text)
            if self.player is not None:
                try:
                    self.player.play(audio, sr)
                except Exception:
                    pass
        return _cb

    def barge_in(self, detail: dict | None = None) -> dict:
        """User started speaking during playback: stop audio, cancel work."""
        return self._interrupt.on_speech_onset(detail or {})
