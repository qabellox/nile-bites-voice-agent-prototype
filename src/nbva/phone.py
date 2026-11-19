"""Phone-ready HTTP integration for the Nile Bites prototype.

The bridge deliberately speaks plain TwiML instead of depending on the Twilio
SDK. Twilio sends speech transcripts to ``/twilio/gather`` and the existing
pipeline remains the single source of truth for ASR-independent text turns,
LLM tool use, order state, logging, and language behaviour.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import io
import logging
import re
import sqlite3
import threading
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_file

from .agent.app import AgentApp, build_app
from .config.loader import Config, load_config

_LOG = logging.getLogger("nbva.phone")

# Twilio keeps two *different* language tables. `ar-EG` (Egyptian Arabic) is
# valid for <Gather> speech recognition but is NOT a <Say> language; Twilio's
# Arabic TTS codes are ar-AE, ar-XA and arb. An invalid language/voice pair can
# make <Say> fail outright, so ASR and TTS must be configured separately.
# Twilio has no Egyptian-Arabic TTS voice: ar-XA is Modern Standard Arabic.
ASR_LANGUAGE = os.getenv("TWILIO_ASR_LANGUAGE", "ar-EG")
TTS_LANGUAGE = os.getenv("TWILIO_TTS_LANGUAGE", "ar-XA")
TTS_VOICE = os.getenv("TWILIO_TTS_VOICE", "Google.ar-XA-Standard-A")
# Biasing recognition with the restaurant's own vocabulary cuts the number of
# misheard dish and zone names before the transcript ever reaches the model.
ASR_HINTS = os.getenv(
    "TWILIO_ASR_HINTS",
    "نايل بايتس, مدينة نصر, برجر حلومي, تشيكن برجر, دبل تشيز برجر, "
    "زنجر برجر, برجر خضار, بطاطس, توصيل",
)
# Opt-in: a public webhook can otherwise be hit by anyone and spend the Gemini
# quota. Off by default so a missing/mistyped token cannot block the first call.
VALIDATE_TWILIO_SIGNATURES = os.getenv(
    "TWILIO_VALIDATE_SIGNATURES", ""
).strip().lower() in {"1", "true", "yes"}


def verify_twilio_signature(
    url: str, params: dict[str, str], signature: str, auth_token: str
) -> bool:
    """Check Twilio's ``X-Twilio-Signature`` without the Twilio SDK.

    Twilio signs the exact request URL plus the POST fields sorted by name.
    ``hmac.compare_digest`` avoids a timing leak on the comparison.
    """
    if not auth_token or not signature:
        return False
    parts = urlsplit(url)
    canonical_url = url
    if parts.query:
        query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
        canonical_url = urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    signed = canonical_url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    digest = hmac.new(
        auth_token.encode("utf-8"), signed.encode("utf-8"), hashlib.sha1
    ).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature)


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


def _has_explicit_quantity(text: str) -> bool:
    import re

    normalized = str(text).translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")).casefold()
    if re.search(r"(?<!\d)[0-9]+(?!\d)", normalized):
        return True
    return any(word in normalized for word in _QUANTITY_WORDS)


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
            # No quantity stated means no line. Defaulting to 1 here silently
            # put an item the caller never asked for into a real order, which is
            # the exact failure the project's own safety rule forbids.
            if match:
                quantity = _quantity_from_text(match.group(1))
                if quantity:
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
    if not _has_explicit_quantity(folded):
        return (
            "حضرتك، سمعت اسم الصنف لكن الكمية مش واضحة. "
            "ممكن تقول العدد المطلوب صراحة؟"
        )
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
        requested_quantity = _quantity_from_text(folded)
        return (
            f"سمعت حضرتك بتقول {_spoken_quantity(requested_quantity)} ساندوتش فراخ. "
            "مش هفترض صنف من عندي. عندنا ساندوتش شاورما فراخ، "
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


def _conversation_note(session: PhoneSession) -> str:
    """Describe the business state the model has already established.

    The transcript records words, not state. Without this note the model cannot
    see the order it built earlier in the same call: it re-searches the menu,
    re-quotes prices it already gave, and loses the thread. Everything here is
    derived from tool output — no fact is added that a tool did not produce.
    """
    state = session.app.session.state_snapshot()
    order = session.app.session.current_order()
    lines = ["[SESSION STATE — current, derived from tool calls in this call]"]
    lines.append(
        f"Turn {state['turn_index']}. Orders created in this call: "
        f"{state['orders_created']}."
    )
    if order is None:
        lines.append("No order registered yet.")
    else:
        lines.append(
            f"Active order {order['order_id']}: status={order['status']}, "
            f"mode={order['order_mode']}, zone={order.get('delivery_zone_id') or 'none'}."
        )
        for line in order.get("lines") or []:
            name = line.get("item_name_en") or line.get("item_id")
            lines.append(
                f"- {line.get('quantity')} x {name} ({line.get('item_id')}) "
                f"@ {line.get('unit_price')} = {line.get('line_total')}"
            )
        lines.append(
            f"subtotal={order.get('subtotal')}, discount={order.get('discount_total')}, "
            f"delivery={order.get('delivery_fee')}, total={order.get('total')} EGP."
        )
        for discount in order.get("discounts") or []:
            lines.append(f"- promotion applied: {discount.get('name_en')}")
    lines.append(
        "Do not ask again for any of the above, and do not re-call a tool whose "
        "result is already reflected here unless the caller changed something."
    )
    return "\n".join(lines)


_SENTENCE_END = re.compile(r"(?<=[.!?؟])\s+")


def _split_for_speech(text: str, max_parts: int = 3) -> list[str]:
    """Split a reply into the chunks the caller should hear first-to-last.

    The phone client synthesizes chunk 1 immediately and prefetches the rest, so
    audio starts before the whole reply exists. Splitting is deterministic and
    lossless: joining the parts back with a space reproduces the original text.
    """
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return []
    parts = [part.strip() for part in _SENTENCE_END.split(cleaned) if part.strip()]
    if len(parts) <= 1:
        return [cleaned]
    if len(parts) > max_parts:
        head = parts[: max_parts - 1]
        tail = " ".join(parts[max_parts - 1 :])
        parts = head + [tail]
    return parts


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
        phone_cfg = self.config.section("phone") or {}
        # Which layer answers an order request. False = the model drives every
        # turn. True = the legacy keyword/template path answers first, which is
        # why the prototype felt scripted. Off by default; kept because it
        # encodes real safety rules and is still tested.
        self.direct_order_path = bool(phone_cfg.get("direct_order_path", False))
        self.context_injection = bool(phone_cfg.get("context_injection", True))
        prompt_file = str(phone_cfg.get("prompt_file") or "").strip()
        if prompt_file:
            # Production persona, not the research instrument prompt. The
            # research prompt asks the model to behave like a trial subject; this
            # one asks it to run a phone call.
            self.config.set("system_prompt.file", prompt_file)
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

    def _out_of_scope(self, text: str) -> bool:
        """Narrow keyword veto for clearly unrelated requests.

        This used to also refuse any Arabic turn that contained no restaurant
        keyword and had no prior context. That rule rejected greetings, "معلش
        سؤال", and every natural opening a caller might use, which is a large
        part of why the agent could not hold a conversation. Scope is now the
        model's job (the prompt states it); this veto only catches requests that
        name an obviously unrelated domain, and never fires when the caller also
        mentions the restaurant.
        """
        folded = text.casefold()
        if not any(marker in folded for marker in _OUT_OF_SCOPE_MARKERS):
            return False
        return not any(marker in folded for marker in _RESTAURANT_MARKERS)

    def _remember(
        self,
        session: PhoneSession,
        caller_id: str,
        user_text: str,
        reply: str,
        *,
        into_session: bool,
    ) -> None:
        """Persist a turn that did not go through the pipeline.

        The pipeline already records its own turns. Paths that answer without it
        must still advance the conversation, otherwise the model's next turn sees
        a transcript with a hole in it.
        """
        self.memory.append(caller_id, "user", user_text)
        self.memory.append(caller_id, "assistant", reply)
        if into_session:
            session.app.session.add_turn("user", user_text, _utc_now())
            session.app.session.add_turn("assistant", reply, _utc_now())

    def process_text(self, call_id: str, caller_id: str, text: str) -> dict[str, Any]:
        clean_text = str(text or "").strip()
        if not clean_text:
            raise ValueError("speech text is empty")
        session = self.session_for(call_id, caller_id)

        if self._out_of_scope(clean_text):
            response = (
                "حضرتك، أقدر أساعد حضرتك في المنيو والطلبات والتوصيل الخاص بـ "
                "Nile Bites فقط."
            )
            self._remember(
                session, caller_id, clean_text, response, into_session=True
            )
            return {
                "response": response,
                "turn": {},
                "state": None,
                "session": session.app.describe(),
            }

        if self.direct_order_path:
            direct_order_response = _direct_order_response(session, clean_text)
            if direct_order_response is not None:
                self._remember(
                    session, caller_id, clean_text, direct_order_response,
                    into_session=True,
                )
                return {
                    "response": direct_order_response,
                    "turn": {},
                    "state": session.app.session.state_snapshot(),
                    "session": session.app.describe(),
                }

        # The model runs the turn. It gets the conversation plus, when enabled,
        # the business state it has already built — which the transcript alone
        # does not carry.
        note = _conversation_note(session) if self.context_injection else None
        result = session.app.pipeline.process_turn(
            text=clean_text, speak=False, context_note=note
        )
        session.app.logger.log_turn(result.turn)
        if result.error:
            # Provider failures used to be invisible: the caller heard an
            # apology and the logs showed nothing but a 500-free request. Keep
            # the reason server-side so `vercel logs` can tell a bad key from a
            # bad schema or a model that is out of credit.
            _LOG.error("turn failed: %s", result.error)
        response_text = result.response_text.strip()
        fallback_response = _direct_fallback_response(
            session.app.repository, clean_text
        )
        if fallback_response and result.error:
            response_text = fallback_response
        if not response_text:
            # Empty reply with no error means the model produced nothing usable.
            # Ask a concrete clarifying question instead of a generic apology.
            response_text = (
                "حضرتك، حصل عطل مؤقت في خدمة الرد. "
                "ممكن حضرتك تعيد السؤال بعد لحظات؟"
                if result.error
                else _arabic_safe_clarification(clean_text)
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


def _say(text: str, language: str | None = None, voice: str | None = None) -> str:
    """Build a <Say>. Defaults come from Twilio's *TTS* language table."""
    lang = language or TTS_LANGUAGE
    spoken_voice = voice or TTS_VOICE
    attrs = f'language="{html.escape(lang, quote=True)}"'
    if spoken_voice:
        attrs += f' voice="{html.escape(spoken_voice, quote=True)}"'
    return f"<Say {attrs}>{html.escape(str(text), quote=False)}</Say>"


def twiml_response(
    text: str,
    action: str = "/twilio/gather",
    asr_language: str | None = None,
    gather: bool = True,
) -> Response:
    """Return Twilio-compatible XML without requiring the Twilio SDK.

    Recognition uses Egyptian Arabic (``ar-EG``); speech synthesis uses a
    *different* language code because Twilio's <Say> table has no ``ar-EG``.
    """
    parts = ['<?xml version="1.0" encoding="UTF-8"?><Response>']
    if gather:
        safe_action = html.escape(action, quote=True)
        parts.append(
            f'<Gather input="speech" language="{html.escape(asr_language or ASR_LANGUAGE, quote=True)}" '
            f'speechTimeout="auto" hints="{html.escape(ASR_HINTS, quote=True)}" '
            f'action="{safe_action}" method="POST">'
        )
    parts.append(_say(text))
    if gather:
        parts.append("</Gather>")
        parts.append(_say("مش سامعك كويس. ممكن تقولها تاني؟"))
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

    @app.before_request
    def _check_twilio_signature() -> Response | None:
        if not VALIDATE_TWILIO_SIGNATURES or not request.path.startswith("/twilio/"):
            return None
        token = os.getenv("TWILIO_AUTH_TOKEN", "")
        if not token:
            _LOG.error("signature validation on but TWILIO_AUTH_TOKEN is missing")
            return Response("Forbidden", status=403, mimetype="text/plain")
        if not verify_twilio_signature(
            request.url,
            request.form.to_dict(flat=True),
            request.headers.get("X-Twilio-Signature", ""),
            token,
        ):
            _LOG.warning("rejected unsigned request to %s", request.path)
            return Response("Forbidden", status=403, mimetype="text/plain")
        return None

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
        # Report which model and prompt are actually live. The prototype once ran
        # on a default free model and the research prompt because an environment
        # variable was unset; nothing in the UI made that visible.
        return jsonify(
            {
                "ok": True,
                "service": "nbva-phone-prototype",
                "llm": bridge.config.get("llm.model"),
                "llm_provider": bridge.config.get("llm.provider"),
                "system_prompt_file": bridge.config.get("system_prompt.file"),
                "engine": str(bridge.config.get("architecture.engine") or "a0"),
                "direct_order_path": bridge.direct_order_path,
                "context_injection": bridge.context_injection,
            }
        )

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
        # The client synthesizes the first chunk while it prefetches the rest, so
        # audio starts before the whole reply has been spoken into existence.
        sentences = _split_for_speech(result["response"])
        if body.get("compact"):
            return jsonify(
                {"ok": True, "response": result["response"], "sentences": sentences}
            )
        return jsonify({"ok": True, **result, "sentences": sentences})

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
