from __future__ import annotations

from pathlib import Path

from nbva.config.loader import load_config
from nbva.phone import ConversationMemory, _quantity_from_text, create_phone_app, twiml_response


def _offline_config():
    config = load_config()
    config.set("llm.provider", "scripted")
    config.set("llm.scripted.fixed_reply", "تمام يا فندم")
    config.set("tts.provider", "null")
    config.set("logging.save_audio", False)
    config.set("logging.write_run_manifest", False)
    config.set("logging.echo_to_console", False)
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
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
    client = app.test_client()
    first = client.post(
        "/api/chat",
        json={
            "call_id": "order",
            "caller_id": "order",
            "text": "عايز برجر حلومي مشوي في مدينة نصر",
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
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
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
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
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
    app = create_phone_app(_offline_config(), tmp_path / "memory.sqlite3")
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
