"""Phone-ready HTTP integration for the Nile Bites prototype.

The bridge deliberately speaks plain TwiML instead of depending on the Twilio
SDK. Twilio sends speech transcripts to ``/twilio/gather`` and the existing
pipeline remains the single source of truth for ASR-independent text turns,
LLM tool use, order state, logging, and language behaviour.
"""

from __future__ import annotations

import html
import io
import sqlite3
import threading
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_file

from .agent.app import AgentApp, build_app
from .config.loader import Config, load_config


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationMemory:
    """Small durable transcript memory keyed by caller identity.

    This stores dialogue context, not invented customer facts. The live
    ``Session`` remains authoritative for order state and is reused for the
    duration of a call. On a later call, recent transcript turns are restored
    so references such as "the same one" remain understandable without
    pretending that old orders are still active.
    """

    def __init__(self, path: str | Path, max_turns: int = 12) -> None:
        self.path = Path(path)
        self.max_turns = max(1, int(max_turns))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with sqlite3.connect(self.path) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    caller_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_turns_caller
                ON conversation_turns(caller_id, timestamp_utc)
                """
            )

    def load(self, caller_id: str) -> list[dict[str, str]]:
        with self._lock, sqlite3.connect(self.path) as db:
            rows = db.execute(
                """
                SELECT role, text, timestamp_utc
                FROM conversation_turns
                WHERE caller_id = ?
                ORDER BY timestamp_utc DESC
                LIMIT ?
                """,
                (caller_id, self.max_turns),
            ).fetchall()
        rows.reverse()
        return [
            {"role": str(role), "text": str(text), "timestamp_utc": str(timestamp)}
            for role, text, timestamp in rows
        ]

    def append(self, caller_id: str, role: str, text: str) -> None:
        with self._lock, sqlite3.connect(self.path) as db:
            db.execute(
                """
                INSERT INTO conversation_turns(caller_id, role, text, timestamp_utc)
                VALUES (?, ?, ?, ?)
                """,
                (caller_id, role, text, _utc_now()),
            )


@dataclass
class PhoneSession:
    app: AgentApp
    caller_id: str


class PhoneBridge:
    """Build and retain application sessions for Twilio calls and local demos."""

    def __init__(
        self,
        config: Config | None = None,
        memory_path: str | Path = "logs/phone_memory.sqlite3",
    ) -> None:
        self.config = config or load_config()
        self.memory = ConversationMemory(memory_path)
        self._sessions: dict[str, PhoneSession] = {}
        self._lock = threading.Lock()

    def session_for(self, call_id: str, caller_id: str) -> PhoneSession:
        with self._lock:
            existing = self._sessions.get(call_id)
            if existing is not None:
                return existing
            app = build_app(self.config, attach_player=False)
            for turn in self.memory.load(caller_id):
                app.session.add_turn(turn["role"], turn["text"], turn["timestamp_utc"])
            session = PhoneSession(app=app, caller_id=caller_id)
            self._sessions[call_id] = session
            return session

    def process_text(self, call_id: str, caller_id: str, text: str) -> dict[str, Any]:
        clean_text = str(text or "").strip()
        if not clean_text:
            raise ValueError("speech text is empty")
        session = self.session_for(call_id, caller_id)
        result = session.app.pipeline.process_turn(text=clean_text, speak=False)
        session.app.logger.log_turn(result.turn)
        self.memory.append(caller_id, "user", clean_text)
        self.memory.append(caller_id, "assistant", result.response_text)
        return {
            "response": result.response_text,
            "turn": result.turn.to_dict(),
            "state": result.final_state,
            "session": session.app.describe(),
        }

    def end_call(self, call_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(call_id, None)
        if session is not None:
            session.app.logger.log_session_footer()


def _say(text: str, language: str = "ar-EG") -> str:
    safe = html.escape(str(text), quote=False)
    return f'<Say language="{html.escape(language, quote=True)}">{safe}</Say>'


def twiml_response(
    text: str,
    action: str = "/twilio/gather",
    language: str = "ar-EG",
    gather: bool = True,
) -> Response:
    """Return Twilio-compatible XML without requiring the Twilio SDK."""
    parts = ['<?xml version="1.0" encoding="UTF-8"?><Response>']
    if gather:
        safe_action = html.escape(action, quote=True)
        parts.append(
            f'<Gather input="speech" language="{language}" speechTimeout="auto" '
            f'action="{safe_action}" method="POST">'
        )
    parts.append(_say(text, language))
    if gather:
        parts.append("</Gather>")
        parts.append(_say("مش سامعك كويس. ممكن تقولها تاني؟", language))
        parts.append(
            f'<Redirect method="POST">{html.escape(action, quote=False)}</Redirect>'
        )
    parts.append("</Response>")
    return Response("".join(parts), mimetype="application/xml")


def create_phone_app(
    config: Config | None = None,
    memory_path: str | Path = "logs/phone_memory.sqlite3",
) -> Flask:
    """Create the local demo and Twilio webhook application."""
    bridge = PhoneBridge(config=config, memory_path=memory_path)
    app = Flask(__name__)
    app.config["NBVA_PHONE_BRIDGE"] = bridge
    ui_path = Path(__file__).resolve().parents[2] / "ui.html"

    @app.get("/")
    def ui() -> Response:
        if not ui_path.is_file():
            return Response("Nile Bites agent is online.", mimetype="text/plain")
        return Response(ui_path.read_text(encoding="utf-8"), mimetype="text/html")

    @app.after_request
    def cors(response: Response) -> Response:
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        return response

    @app.get("/healthz")
    def health() -> Response:
        return jsonify({"ok": True, "service": "nbva-phone-prototype"})

    @app.post("/api/chat")
    def chat() -> Response:
        body = request.get_json(silent=True) or {}
        try:
            result = bridge.process_text(
                str(body.get("call_id") or "local-demo"),
                str(body.get("caller_id") or "local-demo"),
                str(body.get("text") or ""),
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if body.get("compact"):
            return jsonify({"ok": True, "response": result["response"]})
        return jsonify({"ok": True, **result})

    @app.post("/api/tts")
    def tts() -> Response:
        body = request.get_json(silent=True) or {}
        text = str(body.get("text") or "").strip()
        if not text:
            return jsonify({"ok": False, "error": "speech text is empty"}), 400
        try:
            from elevenlabs import ElevenLabs, VoiceSettings

            api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("ELEVENLABS_API_KEY is not configured")
            client = ElevenLabs(api_key=api_key)
            audio = client.text_to_speech.convert(
                voice_id=os.environ.get(
                    "ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL"
                ),
                text=text,
                model_id="eleven_flash_v2_5",
                output_format="mp3_22050_32",
                voice_settings=VoiceSettings(
                    stability=0.42,
                    similarity_boost=0.78,
                    style=0.38,
                    use_speaker_boost=True,
                ),
            )
            payload = b"".join(audio)
            if not payload:
                raise RuntimeError("ElevenLabs returned empty audio")
            return send_file(
                io.BytesIO(payload),
                mimetype="audio/mpeg",
                as_attachment=False,
                download_name="reply.mp3",
            )
        except Exception as exc:
            return jsonify({"ok": False, "error": f"ElevenLabs TTS failed: {exc}"}), 502

    @app.post("/twilio/voice")
    def twilio_voice() -> Response:
        return twiml_response(
            "أهلاً بيك في نايل بايتس. أقدر أساعدك في المنيو والطلبات والتوصيل. "
            "قولّي تحب إيه؟"
        )

    @app.post("/twilio/gather")
    def twilio_gather() -> Response:
        call_id = str(request.form.get("CallSid") or "twilio-call")
        caller_id = str(request.form.get("From") or "unknown-caller")
        speech = str(request.form.get("SpeechResult") or "").strip()
        if not speech:
            return twiml_response("معلش، ماوصلنيش كلام. حاول تاني.")
        try:
            result = bridge.process_text(call_id, caller_id, speech)
        except Exception as exc:
            app.logger.exception("phone turn failed")
            return twiml_response(
                "حصلت مشكلة مؤقتة وأنا بحاول أفهم الطلب. ممكن تعيد كلامك؟"
            )
        return twiml_response(str(result["response"]))

    @app.post("/twilio/hangup")
    def twilio_hangup() -> Response:
        bridge.end_call(str(request.form.get("CallSid") or "twilio-call"))
        return Response("", status=204)

    return app
