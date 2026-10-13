"""Configuration loading, overrides, and the reproducibility guard rails.

The config is part of the experimental record: every run must be able to say
which model, which latency definition, and which business weekday produced a
turn. These tests pin the behaviour the rest of the instrument relies on.
"""

from __future__ import annotations

import pytest

from nbva.config.loader import ENV_PREFIX, NESTING_SEP, load_config


# -- lookup ---------------------------------------------------------------


def test_dotted_lookup_reads_nested_values(config):
    assert config.get("asr.provider") == "faster_whisper"
    assert config.get("llm.model") == "qwen2.5:7b-instruct"


def test_a_missing_key_returns_the_default(config):
    assert config.get("nope.not.here", "fallback") == "fallback"


def test_require_raises_on_a_missing_key(config):
    with pytest.raises(KeyError):
        config.require("nope.not.here")


def test_membership_uses_dotted_paths(config):
    assert "asr.provider" in config
    assert "asr.nonsense" not in config


def test_a_non_mapping_section_is_rejected(config):
    with pytest.raises(TypeError):
        config.section("asr.provider")


def test_set_then_get_round_trips(config):
    config.set("experiment.operator_note", "trial-1")
    assert config.get("experiment.operator_note") == "trial-1"


def test_mutating_a_returned_value_does_not_leak_into_the_config(config):
    section = config.section("experiment")
    section["injected"] = True
    assert "experiment.injected" not in config


def test_to_dict_returns_an_independent_copy(config):
    snapshot = config.to_dict()
    snapshot["asr"]["provider"] = "tampered"
    assert config.get("asr.provider") == "faster_whisper"


def test_dump_yaml_is_unicode_safe(config):
    text = config.dump_yaml()
    assert "asr" in text


# -- the layered load order ----------------------------------------------


def test_all_base_config_files_are_loaded(config):
    names = {p.name for p in config.sources}
    assert {
        "main.yaml", "asr.yaml", "llm.yaml", "tts.yaml",
        "audio.yaml", "conditions.yaml", "research.yaml", "logging.yaml",
    } <= names


def test_an_extra_file_overrides_the_base(tmp_path):
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("experiment:\n  business_weekday: thursday\n", encoding="utf-8")
    overlaid = load_config(extra_files=[overlay])
    assert overlaid.get("experiment.business_weekday") == "thursday"


def test_an_overlay_does_not_lose_untouched_base_keys(tmp_path):
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("experiment:\n  business_weekday: thursday\n", encoding="utf-8")
    overlaid = load_config(extra_files=[overlay])
    assert overlaid.get("asr.provider") == "faster_whisper"


def test_the_env_prefix_and_nesting_separator_are_stable():
    # Changing these silently breaks every documented override.
    assert ENV_PREFIX == "NBVA_"
    assert NESTING_SEP == "__"


# -- reproducibility guard rails ------------------------------------------


def test_the_default_run_is_not_a_research_run(config):
    assert config.get("experiment.is_research_run") is False


def test_a_participant_code_is_required_when_it_is_a_research_run(tmp_path):
    overlay = tmp_path / "research.yaml"
    overlay.write_text(
        "experiment:\n  is_research_run: true\n  participant_id: ''\n", encoding="utf-8"
    )
    cfg = load_config(extra_files=[overlay])
    # The CLI refuses to start a formal run without a participant id; the same
    # check is what stops unattributable rows in the dataset.
    from nbva.cli import _require_participant_id

    with pytest.raises(SystemExit):
        _require_participant_id(cfg)


def test_a_research_run_with_a_participant_id_is_accepted(tmp_path):
    overlay = tmp_path / "research.yaml"
    overlay.write_text(
        "experiment:\n  is_research_run: true\n  participant_id: P001\n", encoding="utf-8"
    )
    cfg = load_config(extra_files=[overlay])
    from nbva.cli import _require_participant_id

    _require_participant_id(cfg)


def test_business_weekday_must_be_a_real_weekday(config):
    weekdays = {
        "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday",
    }
    value = str(config.get("experiment.business_weekday")).strip().lower()
    assert value == "auto" or value in weekdays


def test_auto_weekday_is_the_only_unsafe_value(config):
    # Pinned weekdays are required for formal runs; 'auto' is a dev convenience.
    assert config.get("experiment.business_weekday") == "auto"


# -- paths ----------------------------------------------------------------


def test_resolve_path_returns_an_existing_file(config):
    db_path = config.resolve_path("paths.restaurant_db")
    assert db_path.exists()


def test_resolve_path_raises_when_unset(config):
    with pytest.raises(KeyError):
        config.resolve_path("paths.does_not_exist")


# -- adapter settings -----------------------------------------------------


def test_the_sampled_rate_matches_the_asr_rate(config):
    assert config.get("audio.input.sample_rate") == config.get("asr.sample_rate")


def test_the_asr_model_is_a_real_faster_whisper_size(config):
    assert config.get("asr.faster_whisper.model") in {"tiny", "base", "small", "medium", "large-v3"}


def test_conditions_are_configured_as_c0_to_c4(config):
    assert [b["id"] for b in config.get("conditions.bands")] == ["C0", "C1", "C2", "C3", "C4"]


def test_condition_bands_are_ordered(config):
    edges = [(b["min_pct"], b["max_pct"]) for b in config.get("conditions.bands")]
    assert edges == sorted(edges)


def test_condition_boundaries_are_flagged_provisional(config):
    assert config.get("conditions.provisional") is True
    # C0 is the one band fixed by the measure's definition, not a placeholder.
    provisional_flags = [b.get("provisional", True) for b in config.get("conditions.bands")]
    assert provisional_flags == [False, True, True, True, True]
