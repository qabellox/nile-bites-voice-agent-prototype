"""Research-grade trace logging for streaming turns (development only).

Every streaming turn writes one JSONL file with enough information to fully
reconstruct what happened: audio reference, every hypothesis event, every
understanding/tools/LLM/TTS timestamp, revisions, and the final outcome.
This is a *development* log, not the research dataset: it never stores API
keys or secrets, only component/model *names* and versions.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StreamTrace:
    """Collects one turn's events; call :meth:`save` at turn end."""

    session_id: str = ""
    turn_id: str = ""
    log_dir: str | Path | None = None

    _t0_mono: float = field(default=0.0, repr=False)
    _events: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _header: dict[str, Any] = field(default_factory=dict, repr=False)

    def start(self, header: dict[str, Any] | None = None) -> None:
        self._t0_mono = time.monotonic()
        self._header = dict(header or {})

    def log(self, kind: str, **payload: Any) -> dict[str, Any]:
        event = {"kind": kind,
                 "t": round(time.monotonic() - self._t0_mono, 3),
                 "utc": _utc_now_iso()}
        event.update(payload)
        self._events.append(event)
        return event

    def save(self) -> str | None:
        """Write header + events as JSONL. Returns the path, or None."""
        if not self.log_dir:
            return None
        path = Path(self.log_dir)
        path.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        fname = f"stream-dev-{self.session_id or 'nosession'}-{self.turn_id or 'noturn'}-{stamp}.jsonl"
        full = path / fname
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"record": "header", **self._header,
                                 "saved_utc": _utc_now_iso()},
                                ensure_ascii=False, default=str) + "\n")
            for event in self._events:
                fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        return str(full)

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)
