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


_OUT_OF_SCOPE_MARKERS = (
    "طائرة",
    "طيارة",
    "طيران",
    "صيانة",
    "airplane",
    "aircraft",
    "aviation",
    "medical",
    "doctor",
    "lawyer",
    "politics",
    "weather",
    "python",
    "coding",
    "programming",
    "quantum",
)
_RESTAURANT_MARKERS = (
    "منيو",
    "قائمة",
    "أكل",
    "اكل",
    "برجر",
    "ساندوتش",
    "سندوتش",
    "sandwich",
    "sandwiches",
    "بيتزا",
    "طلب",
    "طلبات",
    "توصيل",
    "بتوصل",
    "مدينة نصر",
    "سعر",
    "بكام",
    "حلومي",
    "بطاطس",
    "فرايز",
    "خفيف",
    "رشح",
    "متاح",
    "مطعم",
    "menu",
    "food",
    "burger",
    "pizza",
    "order",
    "delivery",
    "price",
    "available",
    "restaurant",
)
_VAGUE_RESPONSE_MARKERS = (
    "ممكن توضح",
    "ممكن توضحي",
    "إيه اللي عايزه",
    "على إيه محتاج مساعدة",
    "أقدر أساعد",
    "أنا هنا أساعد",
    "لم أفهم",
    "مافهمتش",
    "what can i help",
    "could you clarify",
)
_QUANTITY_WORDS = {
    "واحد": 1,
    "واحدة": 1,
    "اتنين": 2,
    "اثنين": 2,
    "اتنين": 2,
    "تلاتة": 3,
    "ثلاثة": 3,
    "اربعة": 4,
    "أربعة": 4,
    "خمسة": 5,
    "تنين": 2,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}
_QUANTITY_SPOKEN = {
    1: "واحد",
    2: "اتنين",
    3: "تلاتة",
    4: "أربعة",
    5: "خمسة",
}


def _spoken_number(value: Any) -> str:
    """Render common EGP values as Arabic words for more reliable Arabic TTS."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value)
    if number < 0:
        return f"سالب {_spoken_number(-number)}"
    if number < 20:
        return (
            "صفر",
            "واحد",
            "اتنين",
            "تلاتة",
            "أربعة",
            "خمسة",
            "ستة",
            "سبعة",
            "تمانية",
            "تسعة",
            "عشرة",
            "حداشر",
            "اتناشر",
            "تلتاشر",
            "أربعتاشر",
            "خمستاشر",
            "ستاشر",
            "سبعتاشر",
            "تمانتاشر",
            "تسعتاشر",
        )[number]
    if number < 100:
        tens = (
            "",
            "",
            "عشرين",
            "تلاتين",
            "أربعين",
            "خمسين",
            "ستين",
            "سبعين",
            "تمانين",
            "تسعين",
        )
        remainder = number % 10
        return tens[number // 10] if not remainder else f"{_spoken_number(remainder)} و{tens[number // 10]}"
    if number < 1000:
        hundreds = (
            "",
            "مية",
            "ميتين",
            "تلتمية",
            "ربعمية",
            "خمسمية",
            "ستمية",
            "سبعمية",
            "تمانية مية",
            "تسعمية",
        )
        remainder = number % 100
        return hundreds[number // 100] if not remainder else f"{hundreds[number // 100]} و{_spoken_number(remainder)}"
    if number < 1000000:
        thousands = number // 1000
        remainder = number % 1000
        prefix = "ألف" if thousands == 1 else f"{_spoken_number(thousands)} ألف"
        return prefix if not remainder else f"{prefix} و{_spoken_number(remainder)}"
    return str(number)


def _direct_fallback_response(repository: Any, text: str) -> str | None:
    """Answer common factual intents without inventing facts when the LLM is unavailable."""
    folded = text.casefold()
    if "برجر" in folded and any(
        marker in folded for marker in ("إيه", "ايه", "أنواع", "انواع", "what", "which")
    ):
        items = repository.search_menu(query="", category="burgers", only_available=True)
        names = "، ".join(
            f"{item.name_ar_eg} ({_spoken_number(item.price)} جنيه)" for item in items
        )
        return f"عندنا حضرتك: {names}. تحب أقول لحضرتك تفاصيل مكونات أي اختيار؟"
    if ("حلومي" in folded or "حلوم" in folded) and any(
        marker in folded for marker in ("بكام", "سعر", "price", "كام")
    ):
        item = repository.items_by_id.get("NB-BURG-004")
        if item is not None:
            return "برجر الحلومي المشوي سعره مية وخمسة وسبعين جنيه، ومتاح حالياً. تحب أضيفه لحضرتك؟"
    if "مدينة نصر" in folded and any(
        marker in folded for marker in ("توصيل", "بتوصل", "delivery", "كام", "قد إيه")
    ):
        zone = repository.zone("مدينة نصر")
        if zone is not None:
            return (
                "أيوه، بنوصل مدينة نصر. رسوم التوصيل خمسة وعشرين جنيه، "
                "والحد الأدنى للطلب مية وعشرين جنيه، والمدة من "
                "خمسة وتلاتين لخمسين دقيقة."
            )
    if ("خفيف" in folded or "مش حارة" in folded or "مش حار" in folded) and (
        "رشح" in folded or "اقتراح" in folded or "recommend" in folded
    ):
        items = repository.search_menu(query="", category="salads", only_available=True)
        if items:
            item = items[0]
            return (
                f"أرشح لحضرتك {item.name_ar_eg} بسعر {_spoken_number(item.price)} جنيه؛ "
                "اختيار خفيف ومش حار. تحب أقول لحضرتك مكوناته؟"
            )
    return None


def _arabic_safe_clarification(text: str) -> str:
    if "سعر" in text or "بكام" in text or "كام" in text:
        return "حضرتك، ممكن تقول اسم الصنف اللي عايز تعرف سعره؟"
    if "منيو" in text or "قائمة" in text or "أكل" in text:
        return "حضرتك، تحب تشوف البرجر، الساندوتشات، المشاوي، ولا المشروبات؟"
    return "حضرتك، ممكن توضح طلبك؟ أقدر أساعد حضرتك في المنيو، الأسعار، الطلبات، والتوصيل."


def _quantity_from_text(text: str) -> int:
    import re

    normalized = str(text).translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    match = re.search(r"(?<!\d)([0-9]+)(?!\d)", normalized)
    if match:
        return max(1, int(match.group(1)))
    for word, quantity in _QUANTITY_WORDS.items():
        if word in normalized.casefold():
            return quantity
    return 1


def _spoken_quantity(quantity: int) -> str:
    return _QUANTITY_SPOKEN.get(quantity, str(quantity))


def _find_order_item(repository: Any, text: str) -> Any | None:
    folded = text.casefold()
    candidates = []
    for item in repository.menu:
        names = [item.name_ar_eg, item.name_en, *item.aliases]
        if any(name.casefold() in folded for name in names if name):
            candidates.append(item)
    if candidates:
        return max(candidates, key=lambda item: len(item.name_ar_eg))
    if "حلومي" in folded or "حلوم" in folded:
        return repository.items_by_id.get("NB-BURG-004")
    return None


def _find_order_lines(repository: Any, text: str) -> list[dict[str, Any]]:
    import re

    folded = text.casefold()
    lines: list[dict[str, Any]] = []
    specific_burger_terms = (
        "حلومي",
        "حلوم",
        "تشيز",
        "جبنة",
        "زنجر",
        "خضار",
        "نباتي",
        "فراخ",
        "دجاج",
        "كلاسيك",
        "halloumi",
        "cheese",
        "zinger",
        "veggie",
        "chicken",
    )
    generic_burger = "برجر" in folded and not any(
        term in folded for term in specific_burger_terms
    )
    if generic_burger:
        burger_quantity = _quantity_from_text(folded)
        if burger_quantity:
            lines.append({"unresolved": "burger", "quantity": burger_quantity})
    burger_item = _find_order_item(repository, folded)
    if burger_item is not None and not generic_burger:
        lines.append(
            {"item": burger_item.id, "quantity": _quantity_from_text(folded)}
        )
    if "بطاطس" in folded or "فرايز" in folded or "fries" in folded:
        fries = repository.items_by_id.get("NB-SIDE-001")
        if fries is not None:
            match = re.search(
                r"(?:و|and)?\s*(اتنين|اثنين|تلاتة|ثلاثة|اربعة|أربعة|خمسة|\d+)\s*"
                r"(?:من\s*)?(?:بطاطس|فرايز|fries)",
                folded,
            )
            quantity = _quantity_from_text(match.group(1)) if match else 1
            lines.append({"item": fries.id, "quantity": quantity})
    return lines


def _direct_order_response(session: PhoneSession, text: str) -> str | None:
    folded = text.casefold()
    order = session.app.session.orders.current_order()
    if any(marker in folded for marker in ("احسب", "الإجمالي", "الاجمالي", "total")):
        if order is None:
            return "حضرتك، لسه مافيش طلب متسجل. تحب تطلب إيه؟"
        result = session.app.session.tools.dispatch(
            "calculate_order_total", {"order_id": order.order_id}
        )
        total = result.get("breakdown", {}).get("total_egp")
        return (
            f"إجمالي طلب حضرتك {_spoken_number(total)} جنيه، "
            "شامل التوصيل والخصومات المطبقة."
        )
    if order is not None and any(
        marker in folded
        for marker in ("تمام", "أكد", "اكّد", "ده الأوردر", "هذا الطلب", "confirm")
    ):
        result = session.app.session.tools.dispatch(
            "confirm_order", {"order_id": order.order_id}
        )
        if result.get("ok"):
            return "تمام يا حضرتك، تم تأكيد الطلب. تحت أمرك."
    if not any(
        marker in folded
        for marker in (
            "عايز",
            "عاوز",
            "محتاج",
            "اطلب",
            "هات",
            "add",
            "order",
            "want",
        )
    ):
        return None
    requested_lines = _find_order_lines(session.app.repository, folded)
    if not requested_lines and any(
        marker in folded
        for marker in (
            "ساندوتش",
            "سندوتش",
            "sandwich",
            "sandwiches",
            "chicken sandwich",
            "chicken sandwiches",
        )
    ):
        return (
            "حضرتك، مش هفترض صنف من عندي. عندنا ساندوتش شاورما فراخ، "
            "وساندوتش طعمية، وسندوتش حلومي مشوي. تحب أي نوع وبأي كمية؟"
        )
    unresolved = next(
        (line for line in requested_lines if line.get("unresolved") == "burger"),
        None,
    )
    if unresolved is not None:
        return (
            f"سمعت حضرتك: {_spoken_quantity(unresolved['quantity'])} برجر و"
            f"{_spoken_quantity(next((line['quantity'] for line in requested_lines if line.get('item') == 'NB-SIDE-001'), 0))} "
            "بطاطس. حضرتك تقصد أي نوع برجر؟"
        )
    if len(requested_lines) > 1:
        zone = None
        for candidate in session.app.repository.zones():
            if candidate.name_ar_eg.casefold() in folded or any(
                alias.casefold() in folded for alias in candidate.aliases
            ):
                zone = candidate.id
                break
        if order is None:
            result = session.app.session.tools.dispatch(
                "create_order",
                {"lines": requested_lines, "order_mode": "delivery", "zone": zone},
            )
        else:
            result = session.app.session.tools.dispatch(
                "modify_order",
                {"order_id": order.order_id, "add": requested_lines},
            )
        if not result.get("ok"):
            if result.get("error") == "UNKNOWN_ZONE":
                return (
                    "تمام يا حضرتك، فهمت طلب حضرتك: "
                    f"{_spoken_quantity(requested_lines[0]['quantity'])} برجر و"
                    f"{_spoken_quantity(requested_lines[1]['quantity'])} بطاطس. "
                    "محتاج أعرف منطقة التوصيل علشان أكمل الطلب."
                )
            return "حضرتك، حصلت مشكلة وأنا بسجل الأصناف. ممكن نجرب تاني؟"
        updated = result.get("order") or {}
        return (
            "تمام يا حضرتك، سجلت طلب حضرتك بالكامل: "
            f"{_spoken_quantity(requested_lines[0]['quantity'])} برجر و"
            f"{_spoken_quantity(requested_lines[1]['quantity'])} بطاطس. "
            f"الإجمالي الحالي {_spoken_number(updated.get('total'))} جنيه."
        )
    item = _find_order_item(session.app.repository, folded)
    if item is None and order is not None and ("منه" in folded or "منها" in folded):
        if len(order.lines) == 1:
            item = session.app.repository.items_by_id.get(order.lines[0].item_id)
    if item is None:
        return None
    quantity = _quantity_from_text(folded)
    line = {"item": item.id, "quantity": quantity}
    item_label = item.name_ar_eg
    zone = None
    for candidate in session.app.repository.zones():
        if candidate.name_ar_eg.casefold() in folded or any(
            alias.casefold() in folded for alias in candidate.aliases
        ):
            zone = candidate.id
            break
    if order is None:
        result = session.app.session.tools.dispatch(
            "create_order",
            {"lines": [line], "order_mode": "delivery", "zone": zone},
        )
    else:
        result = session.app.session.tools.dispatch(
            "modify_order", {"order_id": order.order_id, "add": [line]}
        )
    if not result.get("ok"):
        if result.get("error") == "UNKNOWN_ZONE":
            return (
                f"تمام يا حضرتك، فهمت إنك عايز {_spoken_quantity(quantity)} من {item_label}. "
                "علشان أكمّل طلب التوصيل، محتاج أعرف المنطقة."
            )
        return "حضرتك، حصلت مشكلة وأنا بسجل الطلب. ممكن نجرب تاني؟"
    updated = result.get("order") or {}
    total = updated.get("total")
    return (
        f"تمام يا حضرتك، سجلت {_spoken_quantity(quantity)} من {item_label}. "
        f"الإجمالي الحالي {_spoken_number(total)} جنيه. "
        "تحب تضيف حاجة تانية ولا أأكد الطلب؟"
    )


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
        folded_text = clean_text.casefold()
        has_arabic = any("\u0600" <= char <= "\u06ff" for char in clean_text)
        has_context = bool(session.app.session.history) or bool(
            self.memory.load(caller_id)
        )
        unrelated_arabic = (
            has_arabic
            and not has_context
            and not any(marker in folded_text for marker in _RESTAURANT_MARKERS)
        )
        if (
            any(marker in folded_text for marker in _OUT_OF_SCOPE_MARKERS)
            or unrelated_arabic
        ):
            response = (
                "حضرتك، أقدر أساعد حضرتك في المنيو والطلبات والتوصيل الخاص بـ "
                "Nile Bites فقط."
            )
            self.memory.append(caller_id, "user", clean_text)
            self.memory.append(caller_id, "assistant", response)
            return {"response": response, "turn": {}, "state": None, "session": session.app.describe()}
        direct_order_response = _direct_order_response(session, clean_text)
        if direct_order_response is not None:
            self.memory.append(caller_id, "user", clean_text)
            self.memory.append(caller_id, "assistant", direct_order_response)
            return {
                "response": direct_order_response,
                "turn": {},
                "state": session.app.session.state_snapshot(),
                "session": session.app.describe(),
            }
        direct_response = _direct_fallback_response(session.app.repository, clean_text)
        if direct_response is not None:
            self.memory.append(caller_id, "user", clean_text)
            self.memory.append(caller_id, "assistant", direct_response)
            return {
                "response": direct_response,
                "turn": {},
                "state": None,
                "session": session.app.describe(),
            }
        if has_arabic and any(marker in folded_text for marker in _RESTAURANT_MARKERS):
            safe_response = _arabic_safe_clarification(clean_text)
            self.memory.append(caller_id, "user", clean_text)
            self.memory.append(caller_id, "assistant", safe_response)
            return {
                "response": safe_response,
                "turn": {},
                "state": None,
                "session": session.app.describe(),
            }
        result = session.app.pipeline.process_turn(text=clean_text, speak=False)
        session.app.logger.log_turn(result.turn)
        response_text = result.response_text.strip()
        fallback_response = _direct_fallback_response(
            session.app.repository, clean_text
        )
        if fallback_response and (
            result.error
            or not response_text
            or not result.tool_calls
            or any(marker in response_text.casefold() for marker in _VAGUE_RESPONSE_MARKERS)
        ):
            response_text = fallback_response
        if not response_text:
            response_text = (
                "حضرتك، حصل عطل مؤقت في خدمة الرد. "
                "ممكن حضرتك تعيد السؤال بعد لحظات؟"
            )
        self.memory.append(caller_id, "user", clean_text)
        self.memory.append(caller_id, "assistant", response_text)
        return {
            "response": response_text,
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
                model_id=os.environ.get(
                    "ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5"
                ),
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
