"""Config to component wiring for the ASR, TTS and LLM adapters.

These builders are the boundary where a config key becomes a constructor
argument, and that is where a provider that does not accept a setting turns a
documented offline configuration into a crash.
"""

from __future__ import annotations

import pytest

from nbva.config.loader import load_config


def overlay(tmp_path, body: str):
    path = tmp_path / "overlay.yaml"
    path.write_text(body, encoding="utf-8")
    return load_config(extra_files=[path])


# -- ASR --------------------------------------------------------------------


def test_the_offline_manual_asr_builds_despite_the_language_setting(tmp_path):
    from nbva.asr.providers import build_asr

    cfg = overlay(tmp_path, "asr:\n  provider: manual\n  language: ar\n")
    asr = build_asr(cfg)
    assert asr.describe()["provider"] == "manual"


def test_the_reference_echo_asr_also_builds(tmp_path):
    from nbva.asr.providers import build_asr

    cfg = overlay(tmp_path, "asr:\n  provider: reference_echo\n")
    assert build_asr(cfg).describe()["provider"] == "reference_echo"


def test_faster_whisper_still_receives_the_language(tmp_path):
    from nbva.asr.providers import build_asr

    cfg = overlay(tmp_path, "asr:\n  provider: faster_whisper\n  language: en\n")
    described = build_asr(cfg).describe()
    assert described["language"] == "en"
    assert described["model"] == "small"


def test_the_asr_sample_rate_is_passed_through(tmp_path):
    from nbva.asr.providers import build_asr

    cfg = overlay(tmp_path, "asr:\n  provider: reference_echo\n  sample_rate: 8000\n")
    assert build_asr(cfg).describe()["sample_rate"] == 8000


def test_an_unknown_asr_provider_is_rejected(tmp_path):
    from nbva.asr.providers import build_asr

    cfg = overlay(tmp_path, "asr:\n  provider: nope\n")
    with pytest.raises(ValueError, match="unknown asr.provider"):
        build_asr(cfg)


# -- TTS --------------------------------------------------------------------


def test_the_silent_tts_builds_despite_the_voice_settings(tmp_path):
    from nbva.tts.providers import build_tts

    # The shipped config names an Edge voice; a null provider must not be handed
    # it, or the silent path becomes unusable.
    cfg = overlay(tmp_path, "tts:\n  provider: 'null'\n")
    assert build_tts(cfg).describe()["provider"] == "null"


def test_edge_still_receives_its_voice_settings(tmp_path):
    from nbva.tts.providers import build_tts

    cfg = overlay(tmp_path, "tts:\n  provider: edge\n")
    described = build_tts(cfg).describe()
    assert described["voice"] == "ar-EG-SalmaNeural"
    assert described["fallback_voice"] == "en-US-JennyNeural"


def test_sapi_converts_the_percentage_rate_to_an_integer(tmp_path):
    from nbva.tts.providers import build_tts

    cfg = overlay(tmp_path, "tts:\n  provider: sapi\n  rate: '-10%'\n")
    assert build_tts(cfg).describe()["rate"] == -10


def test_an_unknown_tts_provider_is_rejected(tmp_path):
    from nbva.tts.providers import build_tts

    cfg = overlay(tmp_path, "tts:\n  provider: nope\n")
    with pytest.raises(ValueError, match="unknown tts.provider"):
        build_tts(cfg)


# -- LLM --------------------------------------------------------------------


def test_the_stub_llm_is_refused_for_a_research_run(tmp_path):
    from nbva.llm.providers import build_provider

    cfg = overlay(
        tmp_path,
        "experiment:\n  is_research_run: true\nllm:\n  provider: scripted\n",
    )
    with pytest.raises(ValueError, match="offline stub"):
        build_provider(cfg)


def test_the_stub_llm_is_allowed_for_an_offline_run(tmp_path):
    from nbva.llm.providers import build_provider

    cfg = overlay(
        tmp_path,
        "experiment:\n  is_research_run: false\nllm:\n  provider: scripted\n",
    )
    assert build_provider(cfg).name == "scripted"


def test_the_stub_llm_replays_a_plan_of_tool_steps(tmp_path):
    from nbva.llm.providers import build_provider

    cfg = overlay(
        tmp_path,
        "experiment:\n  is_research_run: false\n"
        "llm:\n  provider: scripted\n"
        "  scripted:\n"
        "    plan:\n"
        "      - {tool: check_availability, arguments: {item: NB-WRAP-002}}\n"
        "      - {text: 'تمام.'}\n",
    )
    provider = build_provider(cfg)

    first = provider.complete([{"role": "user", "content": "عايز طعمية"}])
    assert first.wants_tools
    assert first.tool_calls[0].name == "check_availability"
    assert first.tool_calls[0].arguments == {"item": "NB-WRAP-002"}

    second = provider.complete([{"role": "user", "content": "عايز طعمية"}])
    assert not second.wants_tools
    assert second.text == "تمام."


def test_the_last_plan_step_answers_later_turns(tmp_path):
    from nbva.llm.providers import build_provider

    cfg = overlay(
        tmp_path,
        "experiment:\n  is_research_run: false\n"
        "llm:\n  provider: scripted\n"
        "  scripted:\n"
        "    plan:\n"
        "      - {text: 'إجابة.'}\n",
    )
    provider = build_provider(cfg)
    for _ in range(4):
        assert provider.complete([{"role": "user", "content": "x"}]).text == "إجابة."


def test_the_stub_llm_without_a_plan_calls_no_tools(tmp_path):
    from nbva.llm.providers import build_provider

    cfg = overlay(
        tmp_path,
        "experiment:\n  is_research_run: false\nllm:\n  provider: scripted\n",
    )
    response = build_provider(cfg).complete([{"role": "user", "content": "مرحبا"}])
    assert response.tool_calls == []
    assert "scripted stub" in response.text


# -- google transcript translation ------------------------------------------
#
# Gemini does not accept the OpenAI-shaped transcript the tool loop keeps. It
# needs ``functionCall`` parts on the model turn and ``functionResponse`` parts
# on the user turn that answers them. Flattening both to plain text - the
# previous behaviour - left the model unable to see that its call had been
# answered, which is fatal for a tool-calling agent and invisible in a unit test
# unless the translation itself is pinned.


def test_google_turns_an_assistant_tool_call_into_a_function_call_part():
    from nbva.llm.providers import GoogleProvider

    contents = GoogleProvider._to_contents(
        [
            {"role": "system", "content": "أنت موظف استقبال."},
            {"role": "user", "content": "عايز برجر"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "get_price",
                            "arguments": '{"item": "NB-BURG-001", "quantity": 2}',
                        },
                    }
                ],
            },
        ]
    )

    assert [c["role"] for c in contents] == ["user", "model"]
    call = contents[1]["parts"][0]["functionCall"]
    assert call["name"] == "get_price"
    assert call["args"] == {"item": "NB-BURG-001", "quantity": 2}


def test_google_turns_a_tool_result_into_a_function_response_part():
    from nbva.llm.providers import GoogleProvider

    contents = GoogleProvider._to_contents(
        [
            {"role": "user", "content": "عايز برجر"},
            {
                "role": "tool",
                "name": "get_price",
                "tool_call_id": "c1",
                "content": '{"ok": true, "total": 350}',
            },
        ]
    )

    assert contents[-1]["role"] == "user"
    response = contents[-1]["parts"][0]["functionResponse"]
    assert response["name"] == "get_price"
    assert response["response"] == {"ok": True, "total": 350}


def test_google_wraps_a_non_json_tool_result_instead_of_crashing():
    from nbva.llm.providers import GoogleProvider

    contents = GoogleProvider._to_contents(
        [{"role": "tool", "name": "get_price", "content": "engine error"}]
    )
    assert contents[0]["parts"][0]["functionResponse"]["response"] == {
        "result": "engine error"
    }


def test_google_drops_an_empty_assistant_turn():
    from nbva.llm.providers import GoogleProvider

    contents = GoogleProvider._to_contents(
        [
            {"role": "user", "content": "أهلاً"},
            {"role": "assistant", "content": "", "tool_calls": []},
        ]
    )
    # An empty text part is rejected by the API; the turn must simply vanish.
    assert [c["role"] for c in contents] == ["user"]


def test_google_keeps_text_and_a_call_in_the_same_model_turn():
    from nbva.llm.providers import GoogleProvider

    contents = GoogleProvider._to_contents(
        [
            {
                "role": "assistant",
                "content": "ثانية واحدة.",
                "tool_calls": [
                    {
                        "function": {"name": "search_menu", "arguments": "{}"},
                    }
                ],
            }
        ]
    )
    parts = contents[0]["parts"]
    assert parts[0] == {"text": "ثانية واحدة."}
    assert parts[1]["functionCall"] == {"name": "search_menu", "args": {}}


def test_google_reattaches_a_thought_signature_to_a_replayed_call():
    """Gemini 3 rejects a replayed functionCall without its signature."""
    from nbva.llm.providers import GoogleProvider

    contents = GoogleProvider._to_contents(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {"name": "get_price", "arguments": "{}"},
                    }
                ],
            }
        ],
        {"c1": b"opaque-signature"},
    )
    part = contents[0]["parts"][0]
    assert part["functionCall"]["name"] == "get_price"
    assert part["thought_signature"] == b"opaque-signature"


def test_google_strips_json_schema_keys_that_gemini_rejects():
    """Gemini answers an unknown schema key with a 400 for the whole request."""
    from nbva.llm.providers import GoogleProvider

    raw = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"quantity": {"type": "integer", "minimum": 1}},
                },
            }
        },
    }
    cleaned = GoogleProvider._gemini_parameters(raw)

    assert "additionalProperties" not in str(cleaned)
    assert cleaned["properties"]["lines"]["items"]["properties"]["quantity"] == {
        "type": "integer",
        "minimum": 1,
    }


def test_google_schema_filter_handles_the_real_tool_registry(repo, orders):
    from nbva.llm.providers import GoogleProvider
    from nbva.tools.registry import build_default_registry

    registry = build_default_registry(repo, orders)
    schemas = registry.llm_schemas()
    assert schemas and any("additionalProperties" in str(s) for s in schemas)

    for schema in schemas:
        # ``complete`` sends only the parameters object through the filter.
        cleaned = GoogleProvider._gemini_parameters(schema["function"]["parameters"])
        assert "additionalProperties" not in str(cleaned)
        assert cleaned["type"] == "object"
        assert cleaned["properties"]


def test_the_provider_model_is_used_when_the_top_level_model_is_blank(
    tmp_path, monkeypatch
):
    """``llm.google.model`` must not be dead config behind ``llm.model: ''``."""
    from nbva.llm import providers as module

    seen: dict = {}

    class Recorder(module._BaseProvider):
        name = "recorder"

        def __init__(self, model, **kw):
            super().__init__(model, **kw)
            seen["model"] = model
            seen["kw"] = kw

    monkeypatch.setitem(module.PROVIDERS, "recorder", Recorder)
    cfg = overlay(
        tmp_path,
        "llm:\n"
        "  provider: recorder\n"
        "  model: ''\n"
        "  recorder:\n"
        "    model: gemini-2.5-flash\n"
        "    thinking_budget: 0\n",
    )
    module.build_provider(cfg)

    assert seen["model"] == "gemini-2.5-flash"
    assert seen["kw"]["thinking_budget"] == 0


# -- preflight hooks --------------------------------------------------------
#
# ``nbva preflight`` reports whether a run could start. These tests pin the
# contract that makes it useful: a hook must answer without doing the expensive
# work, and it must distinguish "broken" from "not ready yet".


def test_the_asr_preflight_returns_blockers_and_advisories_separately(tmp_path):
    from nbva.asr.providers import build_asr

    cfg = overlay(tmp_path, "asr:\n  provider: manual\n")
    problems, notes = build_asr.preflight(cfg)
    assert problems == []
    assert isinstance(notes, list)


def test_the_asr_preflight_survives_a_provider_without_a_hook(tmp_path):
    from nbva.asr.providers import build_asr

    cfg = overlay(tmp_path, "asr:\n  provider: reference_echo\n")
    assert build_asr.preflight(cfg) == ([], [])


def test_the_asr_preflight_names_the_model_it_would_download(tmp_path):
    from nbva.asr.providers import build_asr

    cfg = overlay(
        tmp_path,
        "asr:\n  provider: faster_whisper\n  faster_whisper:\n    model: small\n"
        "    download_root: '" + str(tmp_path / "no-such-cache").replace("\\", "/") + "'\n",
    )
    problems, notes = build_asr.preflight(cfg)
    # faster-whisper may or may not be installed; either way the message that
    # matters is the one about the missing download.
    assert any("not cached locally" in n for n in notes)
    assert problems == [] or any("not installed" in p for p in problems)


def test_an_offline_llm_is_always_reachable(tmp_path):
    from nbva.llm.providers import build_provider

    cfg = overlay(
        tmp_path,
        "experiment:\n  is_research_run: false\nllm:\n  provider: scripted\n",
    )
    assert build_provider(cfg).preflight() == (True, None)


def test_an_unreachable_openai_compatible_endpoint_is_reported(tmp_path):
    from nbva.llm.providers import build_provider

    # Port 9 is the discard port, so the connection is refused immediately
    # rather than after a timeout.
    cfg = overlay(
        tmp_path,
        "llm:\n  provider: openai_compatible\n  model: qwen2.5:7b-instruct\n"
        "  base_url: http://127.0.0.1:9/v1\n  timeout: 2\n",
    )
    ok, problem = build_provider(cfg).preflight()
    assert ok is False
    assert "did not answer" in problem
