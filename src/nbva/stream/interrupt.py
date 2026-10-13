"""Barge-in (interruption) controller.

While the agent is speaking, new user speech must: stop playback, cancel
in-flight LLM/TTS work that is no longer wanted, retain conversation
context, and start a fresh turn. This controller owns the coordination;
audio/VAD specifics stay in ``nbva.audio``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class InterruptController:
    """Coordinates one interruption. Reusable across turns."""

    player: Any = None
    on_interrupted: Callable[[dict[str, Any]], None] | None = None

    _lock: Any = field(default_factory=threading.Lock, repr=False)
    _cancelled: bool = False
    interruptions: int = 0

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def arm(self) -> None:
        """Call when response playback begins."""
        with self._lock:
            self._cancelled = False

    def on_speech_onset(self, detail: dict[str, Any] | None = None) -> dict[str, Any]:
        """User started speaking during playback: stop audio, cancel work."""
        stopped = False
        if self.player is not None and getattr(self.player, "is_playing", False):
            try:
                self.player.stop()
                stopped = True
            except Exception:
                stopped = False
        with self._lock:
            self._cancelled = True
            self.interruptions += 1
        record = {"kind": "barge_in", "t": round(time.monotonic(), 3),
                  "playback_stopped": stopped,
                  "detail": dict(detail or {})}
        if self.on_interrupted is not None:
            try:
                self.on_interrupted(record)
            except Exception:
                pass
        return record

    def disarm(self) -> None:
        with self._lock:
            self._cancelled = False
