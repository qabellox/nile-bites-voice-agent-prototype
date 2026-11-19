from __future__ import annotations

import base64
import hashlib
import hmac
from pathlib import Path

from nbva.config.loader import load_config
from nbva.phone import (
    ConversationMemory,
    _conversation_note,
    _find_order_lines,
    _quantity_from_text,
    _normalize_tts_voice,
    _split_for_speech,
    create_phone_app,
    twiml_response,
    verify_twilio_signature,
)


def _offline_config(*, direct_order_path: bool = False):
    config = load_config()
    config.set("llm.provider", "scripted")
    config.set("llm.scripted.fixed_reply", "تمام يا فندم")
    config.set("tts.provider", "null")
    config.set("logging.save_audio", False)
    config.set("logging.write_run_manifest", False)
    config.set("logging.echo_to_console", False)
    # The production default is the model-driven path. Tests that exercise the
    # legacy keyword/template layer have to ask for it explicitly.
    config.set("phone.direct_order_path", direct_order_path)
    return config


def test_memory_round_trip(tmp_path: Path):
    memory = ConversationMemory(tmp_path / "memory.sqlite3")
    memory.append("caller", "user", "عايز برجر")
    memory.append("caller", "assistant", "تمام")
    assert [turn["text"] for turn in memory.load("caller")] == ["عايز برجر", "تمام"]


def test_quantity_parser_handles_arabic_digits_and_number_words():
    assert _quantity_from_text("عايز ٣ برجر") == 3
    assert _quantity_from_text("عايز ٢ بطاطس") == 2
    assert _quantity_from_text("عايز three burgers") == 3


def test_twiml_escapes_text_and_contains_gather():
    response = twiml_response("price < 10 & ask")
    body = response.get_data(as_text=True)
    assert "<Gather" in body
    assert "price &lt; 10 &amp; ask" in body


def test_twiml_uses_separate_valid_languages_for_recognition_and_speech():
    """ar-EG is a Gather language, not a Say language in Twilio.

    <Say> uses Twilio's native ElevenLabs voice so there is no extra HTTP hop
    through our own /api/tts. The voice is an Egyptian Arabic male voice.
    """
    body = twiml_response("أهلاً").get_data(as_text=True)
    assert '<Gather input="speech" language="ar-EG"' in body
    assert '<Say language="ar-XA" voice="ElevenLabs.LXrTqFIgiubkrMkwvOUr">' in body
    assert "language=\"ar-EG\"" not in body.split("</Gather>")[1]
    assert "hints=" in body


def test_tts_voice_accepts_bare_elevenlabs_id_and_explicit_provider():
    """Twilio needs ElevenLabs.<ID>; a bare library ID must be prefixed."""
    assert (
        _normalize_tts_voice("LXrTqFIgiubkrMkwvOUr")
        == "ElevenLabs.LXrTqFIgiubkrMkwvOUr"
    )
    assert (
        _normalize_tts_voice(" ElevenLabs.LXrTqFIgiubkrMkwvOUr ")
        == "ElevenLabs.LXrTqFIgiubkrMkwvOUr"
    )
    # Non-ElevenLabs fallbacks stay untouched.
    assert (
        _normalize_tts_voice("Google.ar-XA-Chirp3-HD-Aoede")
        == "Google.ar-XA-Chirp3-HD-Aoede"
    )
    assert _normalize_tts_voice("") == ""


def test_twilio_tts_voice_is_configurable_without_code_change(tmp_path, monkeypatch):
    """A fallback voice can be selected from Vercel env alone."""
    import nbva.phone as phone

    monkeypatch.setattr(phone, "TTS_VOICE", "Google.ar-XA-Chirp3-HD-Aoede")
    body = phone.twiml_response("أهلاً").get_data(as_text=True)
    assert 'voice="Google.ar-XA-Chirp3-HD-Aoede"' in body


def test_twilio_signature_validation_rejects_forged_webhooks(tmp_path: Path, monkeypatch):
    import nbva.phone as phone

    url = "https://example.test/twilio/gather"
    params = {"CallSid": "CA1", "SpeechResult": "عايز برجر"}
    token = "test-auth-token"
    signed = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    good = base64.b64encode(
        hmac.new(token.encode(), signed.encode(), hashlib.sha1).digest()
    ).decode()

    assert verify_twilio_signature(url, params, good, token)
    assert not verify_twilio_signature(url, params, "bad", token)
    assert not verify_twilio_signature(url, params, good, "other-token")

    monkeypatch.setattr(phone, "VALIDATE_TWILIO_SIGNATURES", True)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", token)
    app = phone.create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
    client = app.test_client()

    # Flask's test client URL is localhost, so re-sign with the exact URL it used.
    local_url = "http://localhost/twilio/gather"
    local_signed = local_url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    local_good = base64.b64encode(
        hmac.new(token.encode(), local_signed.encode(), hashlib.sha1).digest()
    ).decode()
    ok = client.post(
        "/twilio/gather", data=params, headers={"X-Twilio-Signature": local_good}
    )
    denied = client.post(
        "/twilio/gather", data=params, headers={"X-Twilio-Signature": "bad"}
    )
    assert ok.status_code == 200
    assert denied.status_code == 403


def test_phone_api_reuses_call_memory(tmp_path: Path):
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
    client = app.test_client()
    first = client.post(
        "/api/chat",
        json={"call_id": "c1", "caller_id": "+201", "text": "عايز برجر"},
    )
    second = client.post(
        "/api/chat",
        json={"call_id": "c1", "caller_id": "+201", "text": "تمام"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json["response"].startswith("تمام يا فندم")
    assert len(app.config["NBVA_PHONE_BRIDGE"].memory.load("+201")) == 4


def test_twilio_routes_return_xml(tmp_path: Path):
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
    client = app.test_client()
    voice = client.post("/twilio/voice")
    gather = client.post(
        "/twilio/gather",
        data={"CallSid": "CA1", "From": "+201", "SpeechResult": "hello"},
    )
    assert voice.status_code == 200
    assert gather.status_code == 200
    assert voice.content_type.startswith("application/xml")
    assert "<Response>" in gather.get_data(as_text=True)


def test_phone_rejects_unrelated_requests_formally(tmp_path: Path):
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
    response = app.test_client().post(
        "/api/chat",
        json={"call_id": "c1", "caller_id": "+201", "text": "هل تقدروا تعملوا صيانة لطائرة؟"},
    )
    assert response.status_code == 200
    assert "حضرتك" in response.json["response"]
    assert "Nile Bites" in response.json["response"]


def test_phone_handles_arabic_order_without_llm_tool_call(tmp_path: Path):
    app = create_phone_app(
        _offline_config(direct_order_path=True), tmp_path / "memory.sqlite3"
    )
    client = app.test_client()
    first = client.post(
        "/api/chat",
        json={
            "call_id": "order",
            "caller_id": "order",
            "text": "عايز واحد برجر حلومي مشوي في مدينة نصر",
        },
    )
    second = client.post(
        "/api/chat",
        json={"call_id": "order", "caller_id": "order", "text": "احسبلي الإجمالي"},
    )
    third = client.post(
        "/api/chat",
        json={"call_id": "order", "caller_id": "order", "text": "تمام، ده الأوردر بتاعي"},
    )
    assert "سجلت" in first.json["response"]
    assert "ميتين" in first.json["response"]
    assert "ميتين" in second.json["response"]
    assert "تم تأكيد الطلب" in third.json["response"]


def test_phone_preserves_multiple_arabic_order_items(tmp_path: Path):
    app = create_phone_app(
        _offline_config(direct_order_path=True), tmp_path / "memory.sqlite3"
    )
    response = app.test_client().post(
        "/api/chat",
        json={
            "call_id": "multi",
            "caller_id": "multi",
            "text": "عايز اتنين برجر وخمسة بطاطس",
        },
    )
    assert response.status_code == 200
    assert "اتنين برجر" in response.json["response"]
    assert "خمسة بطاطس" in response.json["response"]


def test_phone_does_not_invent_generic_burger_type(tmp_path: Path):
    app = create_phone_app(
        _offline_config(direct_order_path=True), tmp_path / "memory.sqlite3"
    )
    response = app.test_client().post(
        "/api/chat",
        json={
            "call_id": "generic",
            "caller_id": "generic",
            "text": "عايز اتنين برجر وخمسة بطاطس في مدينة نصر",
        },
    )
    assert response.status_code == 200
    assert "اتنين برجر" in response.json["response"]
    assert "خمسة بطاطس" in response.json["response"]
    assert "أي نوع برجر" in response.json["response"]


def test_phone_does_not_substitute_burger_for_chicken_sandwich(tmp_path: Path):
    app = create_phone_app(
        _offline_config(direct_order_path=True), tmp_path / "memory.sqlite3"
    )
    response = app.test_client().post(
        "/api/chat",
        json={
            "call_id": "sandwich",
            "caller_id": "sandwich",
            "text": "I want three chicken sandwiches",
        },
    )
    assert response.status_code == 200
    answer = response.json["response"]
    assert "برجر" not in answer
    assert "أي نوع" in answer


def test_phone_never_assumes_quantity_when_transcript_loses_number(tmp_path: Path):
    app = create_phone_app(
        _offline_config(direct_order_path=True), tmp_path / "memory.sqlite3"
    )
    response = app.test_client().post(
        "/api/chat",
        json={
            "call_id": "missing-quantity",
            "caller_id": "missing-quantity",
            "text": "I want a chicken burger",
        },
    )
    assert response.status_code == 200
    answer = response.json["response"]
    assert "الكمية" in answer
    bridge = app.config["NBVA_PHONE_BRIDGE"]
    assert bridge.session_for(
        "missing-quantity", "missing-quantity"
    ).app.session.orders.current_order() is None


# ---------------------------------------------------------------------------
# Production default: the model drives the turn
# ---------------------------------------------------------------------------


def test_production_default_uses_model_not_keyword_templates(tmp_path: Path):
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
    bridge = app.config["NBVA_PHONE_BRIDGE"]
    assert bridge.direct_order_path is False

    response = app.test_client().post(
        "/api/chat",
        json={
            "call_id": "default",
            "caller_id": "default",
            "text": "عايز واحد برجر حلومي مشوي في مدينة نصر",
        },
    )
    assert response.status_code == 200
    # The reply is the scripted model's output — including its own diagnostic
    # suffix, which only the model path can produce. The keyword template would
    # instead answer with a hardcoded "سجلت ..." confirmation and never call the
    # provider at all.
    assert response.json["response"].startswith("تمام يا فندم")
    assert "scripted stub" in response.json["response"]
    # And the template parser did not silently create an order behind the model.
    assert (
        bridge.session_for("default", "default").app.session.orders.current_order()
        is None
    )


def test_each_turn_costs_exactly_one_model_call(tmp_path: Path, monkeypatch):
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
    bridge = app.config["NBVA_PHONE_BRIDGE"]
    session = bridge.session_for("calls", "+201")
    seen: list[list[dict]] = []
    original = session.app.llm.complete

    def spy(messages, tools=None):
        seen.append(messages)
        return original(messages, tools=tools)

    monkeypatch.setattr(session.app.llm, "complete", spy)

    app.test_client().post(
        "/api/chat",
        json={"call_id": "calls", "caller_id": "+201", "text": "أهلاً"},
    )
    assert len(seen) == 1
    app.test_client().post(
        "/api/chat",
        json={"call_id": "calls", "caller_id": "+201", "text": "عايز برجر"},
    )
    assert len(seen) == 2


def test_greeting_is_answered_not_refused(tmp_path: Path):
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
    response = app.test_client().post(
        "/api/chat",
        json={"call_id": "greet", "caller_id": "greet", "text": "أهلاً، إزيك؟"},
    )
    assert response.status_code == 200
    refusal = "أقدر أساعد حضرتك في المنيو والطلبات والتوصيل"
    assert refusal not in response.json["response"]


def test_phone_uses_production_persona_prompt(tmp_path: Path):
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
    bridge = app.config["NBVA_PHONE_BRIDGE"]
    prompt = bridge.session_for("prompt", "prompt").app.pipeline.system_prompt
    assert "You are the person who answers the phone" in prompt
    # It must NOT be the research instrument prompt, which tells the model it is
    # being evaluated in a trial.
    assert "being evaluated" not in prompt


# ---------------------------------------------------------------------------
# The model sees the state it has already built
# ---------------------------------------------------------------------------


def test_conversation_note_reports_no_order_before_one_exists(tmp_path: Path):
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
    bridge = app.config["NBVA_PHONE_BRIDGE"]
    note = _conversation_note(bridge.session_for("note0", "note0"))
    assert "No order registered yet" in note
    assert "orders created in this call: 0" in note.lower()


def test_conversation_note_carries_order_state(tmp_path: Path):
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
    bridge = app.config["NBVA_PHONE_BRIDGE"]
    session = bridge.session_for("note1", "note1")
    created = session.app.session.orders.create_order(
        lines=[{"item": "NB-BURG-004", "quantity": 2}], zone_id="Z3"
    )
    assert created["ok"] is True
    order = created["order"]
    note = _conversation_note(session)
    assert order["order_id"] in note
    assert "NB-BURG-004" in note
    assert "2 x" in note
    assert str(order["total"]) in note
    assert "Z3" in note


def test_model_receives_session_state_note_on_later_turns(tmp_path: Path, monkeypatch):
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
    bridge = app.config["NBVA_PHONE_BRIDGE"]
    session = bridge.session_for("ctx", "ctx")
    seen: list[list[dict]] = []
    original = session.app.llm.complete

    def spy(messages, tools=None):
        seen.append(messages)
        return original(messages, tools=tools)

    monkeypatch.setattr(session.app.llm, "complete", spy)
    session.app.session.orders.create_order(
        lines=[{"item": "NB-BURG-004", "quantity": 1}], zone_id="Z3"
    )
    app.test_client().post(
        "/api/chat",
        json={"call_id": "ctx", "caller_id": "ctx", "text": "نفس الطلب تاني"},
    )
    notes = [
        message["content"]
        for message in seen[-1]
        if message["role"] == "system"
        and message["content"].startswith("[SESSION STATE")
    ]
    assert notes, "the model was not given the session state note"
    assert "NB-BURG-004" in notes[0]


# ---------------------------------------------------------------------------
# Order-line parsing must never invent a quantity
# ---------------------------------------------------------------------------


def test_fries_line_requires_an_explicit_quantity(tmp_path: Path):
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
    repository = app.config["NBVA_PHONE_BRIDGE"].session_for(
        "fries", "fries"
    ).app.repository
    without = _find_order_lines(repository, "عايز برجر حلومي وبطاطس")
    assert [line for line in without if line.get("item") == "NB-SIDE-001"] == []
    with_quantity = _find_order_lines(repository, "عايز برجر حلومي واتنين بطاطس")
    fries = [line for line in with_quantity if line.get("item") == "NB-SIDE-001"]
    assert fries == [{"item": "NB-SIDE-001", "quantity": 2}]


# ---------------------------------------------------------------------------
# Speech chunking: first audio can start before the reply is finished
# ---------------------------------------------------------------------------


def test_split_for_speech_is_ordered_and_lossless():
    text = "تمام يا فندم. الطلب إجماليه ٢٢٠ جنيه. تحب أأكده؟"
    parts = _split_for_speech(text)
    assert len(parts) == 3
    assert parts[0] == "تمام يا فندم."
    assert " ".join(parts) == " ".join(text.split())
    assert _split_for_speech("   ") == []


def test_chat_endpoint_returns_speech_chunks(tmp_path: Path):
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
    response = app.test_client().post(
        "/api/chat",
        json={"call_id": "chunks", "caller_id": "chunks", "text": "مرحبا", "compact": True},
    )
    assert response.status_code == 200
    payload = response.json
    assert payload["sentences"] == [payload["response"]]


# ---------------------------------------------------------------------------
# Deployed configuration is visible, not assumed
# ---------------------------------------------------------------------------


def test_repo_config_defaults_to_the_model_driven_phone_path():
    config = load_config()
    assert config.get("phone.direct_order_path") is False
    assert config.get("phone.context_injection") is True
    assert str(config.get("phone.prompt_file")).endswith("phone_agent_prompt.md")
    assert str(config.get("architecture.engine") or "a0") == "a0"


def test_health_reports_the_live_model_and_answering_layer(tmp_path: Path):
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
    payload = app.test_client().get("/healthz").json
    assert payload["ok"] is True
    assert payload["llm_provider"] == "scripted"
    assert payload["system_prompt_file"].endswith("phone_agent_prompt.md")
    assert payload["direct_order_path"] is False
    assert payload["context_injection"] is True
