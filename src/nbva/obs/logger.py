"""JSONL session logger, run manifest, and console echo.

Design constraints:

* Append-only, flushed per line. A crash mid-session must not lose the turns
  already completed, because those turns may be research data.
* One file per session, so a session can be archived or re-analysed alone.
* A run manifest per experiment pins every controlled variable, so a later
  comparison can prove the conditions really were held constant.
* No dependency on a database or network service.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from .events import SCHEMA_VERSION, TurnRecord, utc_now_iso

_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}


def _reconfigure_utf8() -> None:
    """Arabic output breaks on a cp1252 console. Force UTF-8 where possible."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


class SessionLogger:
    """Writes turn records and events for one session."""

    def __init__(
        self,
        session_id: str,
        experiment_id: str,
        log_dir: str | Path,
        is_research_run: bool = False,
        participant_id: str = "",
        echo_to_console: bool = True,
        console_level: str = "info",
        save_audio: bool = True,
        audio_dir: str | Path | None = None,
        config_fingerprint: str | None = None,
        write_manifest: bool = True,
    ):
        _reconfigure_utf8()
        self.session_id = session_id
        self.experiment_id = experiment_id
        self.is_research_run = is_research_run
        self.participant_id = participant_id
        self.config_fingerprint = config_fingerprint
        self.save_audio = save_audio
        self.console_level = _LEVELS.get(console_level, 20)
        self.echo_to_console = echo_to_console

        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir = Path(audio_dir) if audio_dir else self.log_dir / "audio"
        if save_audio:
            self.audio_dir.mkdir(parents=True, exist_ok=True)

        self.path = self.log_dir / f"session-{session_id}.jsonl"
        self._fh: TextIO = self.path.open("a", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()
        self._turn_count = 0
        self._closed = False
        self._started_utc = utc_now_iso()

        if write_manifest:
            self._write_manifest()

    def _base(self) -> dict[str, Any]:
        """Fields every record in the file must carry.

        JSONL is flat, so a row is only usable if it can be attributed to a
        session and, for research data, to a participant on its own.
        """
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "experiment_id": self.experiment_id,
            "is_research_run": self.is_research_run,
            "participant_id": self.participant_id,
        }

    # -- low level --------------------------------------------------------
    def _emit(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock:
            if self._closed:
                return
            self._fh.write(line + "\n")
            self._fh.flush()

    def log_event(self, event: str, **fields: Any) -> None:
        self._emit(
            {
                "record_type": "event",
                **self._base(),
                "timestamp_utc": utc_now_iso(),
                "event": event,
                **fields,
            }
        )

    def log_session_header(self, **fields: Any) -> None:
        self._emit(
            {
                "record_type": "session_header",
                **self._base(),
                "timestamp_utc": self._started_utc,
                "config_fingerprint": self.config_fingerprint,
                **fields,
            }
        )

    def log_turn(self, record: TurnRecord) -> dict[str, Any]:
        payload = record.to_dict()
        payload.update({"record_type": "turn", **self._base()})
        self._emit(payload)
        self._turn_count += 1
        if self.echo_to_console:
            self._echo_turn(record)
        return payload

    def log_session_footer(self, **fields: Any) -> None:
        if self._closed:
            # The live loop and the CLI can both reach the footer on shutdown.
            # A second footer must not raise on a closed handle, because that
            # would replace a finished session with a traceback.
            return
        self._emit(
            {
                "record_type": "session_footer",
                **self._base(),
                "timestamp_utc": utc_now_iso(),
                "turns_logged": self._turn_count,
                **fields,
            }
        )
        self._closed = True
        with self._lock:
            self._fh.flush()
            self._fh.close()

    # -- console -----------------------------------------------------------
    def _echo_turn(self, r: TurnRecord) -> None:
        lat = r.latency.primary_latency_ms
        lat_txt = f"{lat:8.1f} ms" if lat is not None else "     n/a"
        bits = [
            f"[{r.session_id} t{r.turn_index}]",
            f"ASR: {r.asr_transcript or ''}"[:90],
            f"| en {r.english_pct:.1f}%" if r.english_pct is not None else "| en  n/a",
            f"| lat {lat_txt}",
            f"| {'OK ' if r.success else 'FAIL' if r.success is False else '---'}",
        ]
        if r.task_family:
            bits.insert(1, f"<{r.task_family}>")
        if not r.success and r.failure_category:
            bits.append(f"| {r.failure_category}")
        print(" ".join(bits), flush=True)

    def info(self, message: str, **fields: Any) -> None:
        if self.console_level <= _LEVELS["info"]:
            extra = " ".join(f"{k}={v}" for k, v in fields.items())
            print(f"{message}{(' ' + extra) if extra else ''}", flush=True)

    def warn(self, message: str, **fields: Any) -> None:
        if self.console_level <= _LEVELS["warn"]:
            extra = " ".join(f"{k}={v}" for k, v in fields.items())
            print(f"WARNING: {message}{(' ' + extra) if extra else ''}", flush=True)

    def error(self, message: str, **fields: Any) -> None:
        if self.console_level <= _LEVELS["error"]:
            extra = " ".join(f"{k}={v}" for k, v in fields.items())
            print(f"ERROR: {message}{(' ' + extra) if extra else ''}", file=sys.stderr, flush=True)

    # -- run manifest -------------------------------------------------------
    def _write_manifest(self) -> None:
        manifest_path = self.log_dir / f"manifest-{self.experiment_id}.json"
        controlled = {
            "restaurant_db": self.config_fingerprint,
            "experiment_id": self.experiment_id,
        }
        existing: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = {}
        sessions = existing.get("sessions", [])
        sessions.append(
            {
                "session_id": self.session_id,
                "participant_id": self.participant_id,
                "started_utc": self._started_utc,
                "is_research_run": self.is_research_run,
                "log_file": self.path.name,
                "config_fingerprint": self.config_fingerprint,
            }
        )
        payload = {
            **existing,
            "record_type": "run_manifest",
            "schema_version": SCHEMA_VERSION,
            "experiment_id": self.experiment_id,
            "updated_utc": utc_now_iso(),
            "controlled_variables": controlled,
            "sessions": sessions,
        }
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def audio_path_for(self, turn_index: int, suffix: str = "wav") -> Path:
        return self.audio_dir / f"{self.session_id}_turn{turn_index:03d}.{suffix}"

    @property
    def turns_logged(self) -> int:
        return self._turn_count


def config_fingerprint(config) -> str:
    """Stable hash of the resolved configuration.

    Two sessions with the same fingerprint had the same settings, which is the
    precondition for pooling their data.
    """
    payload = json.dumps(config.to_dict(), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def read_session(path: str | Path) -> list[dict[str, Any]]:
    """Read a JSONL session file back into records."""
    out: list[dict[str, Any]] = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def turns_of(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in records if r.get("record_type") == "turn"]


def new_session_id(prefix: str = "s") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    import random

    return f"{prefix}-{stamp}-{random.randint(1000, 9999)}"
