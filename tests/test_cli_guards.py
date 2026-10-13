"""CLI guard tests for the live development checks.

The smoke-test modes exist to run the live voice path without a human and
without collecting data. The one property that must not fail is that they can
never run as a research run: a recorded WAV or a 5-second microphone capture
produces a session log that looks exactly like participant speech, and it would
end up in the dataset unless it is refused.
"""

from __future__ import annotations

import pytest

from nbva.cli import _cmd_live_dev, build_parser, main


def _args(argv: list[str]):
    return build_parser().parse_args(argv)


@pytest.fixture
def research_config():
    """A research-mode config built fresh, never the shared session fixture.

    The session-scoped ``config`` fixture must not be mutated: setting
    ``is_research_run`` on it would leak into every later test module and make
    unrelated tests fail depending on run order.
    """
    from nbva.config.loader import load_config

    config = load_config()
    config.set("experiment.is_research_run", True)
    config.set("experiment.participant_id", "P001")
    config.set("experiment.business_weekday", "wednesday")
    return config


# ---------------------------------------------------------------------------
# the guard
# ---------------------------------------------------------------------------


def test_audio_file_mode_is_refused_as_a_research_run(research_config, capsys):
    args = _args(["live", "--audio-file", "some.wav"])
    code = _cmd_live_dev(research_config, args)
    assert code == 2
    err = capsys.readouterr().err
    assert "development checks" in err
    assert "not participant speech" in err


def test_self_test_mode_is_refused_as_a_research_run(research_config, capsys):
    args = _args(["live", "--self-test"])
    assert _cmd_live_dev(research_config, args) == 2
    assert "development checks" in capsys.readouterr().err


def test_the_refusal_names_the_setting_that_caused_it(research_config, capsys):
    """The operator has to know which flag to change, or they will be stuck."""
    args = _args(["live", "--self-test"])
    _cmd_live_dev(research_config, args)
    assert "is_research_run=false" in capsys.readouterr().err


def test_the_modes_are_routed_away_from_the_live_session(monkeypatch):
    """The flags must not fall through into a real session."""
    monkeypatch.setattr(
        "nbva.agent.app.build_app",
        lambda *a, **k: pytest.fail("build_app must not run for a refused dev mode"),
    )
    assert main(["--set", "experiment.is_research_run=true", "live", "--self-test"]) == 2


# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------


def test_the_audio_file_flag_parses():
    args = _args(["live", "--audio-file", "clip.wav", "--no-speak"])
    assert args.audio_file == "clip.wav"
    assert args.no_speak is True
    assert args.self_test is False


def test_the_self_test_flag_parses_with_a_duration():
    args = _args(["live", "--self-test", "--self-test-seconds", "2.5"])
    assert args.self_test is True
    assert args.self_test_seconds == 2.5


def test_a_plain_live_invocation_has_no_dev_mode_flags():
    args = _args(["live"])
    assert args.audio_file is None
    assert args.self_test is False


# ---------------------------------------------------------------------------
# the audit and preflight commands
# ---------------------------------------------------------------------------


def test_audit_exits_nonzero_on_an_inconsistent_set(capsys):
    assert main(["audit"]) == 1
    assert "cannot be used as written" in capsys.readouterr().out


def test_audit_json_output_is_parseable(capsys):
    import json

    main(["audit", "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{"):])
    assert payload["total_turns"] == 33
    assert payload["consistent"] is False


def test_audit_can_be_narrowed_to_one_task(capsys):
    code = main(["audit", "--task-id", "DEV-CTX-01"])
    out = capsys.readouterr().out
    assert code == 1
    assert "turns audited: 3" in out


def test_audit_rejects_an_unknown_task_id(capsys):
    assert main(["audit", "--task-id", "NO-SUCH-TASK"]) == 2
    assert "no task matched" in capsys.readouterr().err


# Port 9 is the discard port, so the connection is refused at once instead of
# waiting out a timeout. These tests must not depend on a real server.
_DEAD_LLM = [
    "--set", "llm.base_url=http://127.0.0.1:9/v1",
    "--set", "llm.timeout=2",
]


@pytest.fixture
def offline_stack(tmp_path):
    """A fully stubbed component stack, so preflight needs no external service.

    The silent TTS has to be named in a file rather than with ``--set``: the
    command line coerces the literal ``null`` to a Python ``None``, while a
    quoted YAML string keeps it as the provider name. That is why the shipped
    test suite quotes it too.
    """
    overlay = tmp_path / "offline.yaml"
    overlay.write_text(
        "asr:\n  provider: reference_echo\n"
        "llm:\n  provider: scripted\n"
        "tts:\n  provider: 'null'\n",
        encoding="utf-8",
    )
    return ["--config", str(overlay)]


def test_preflight_exits_nonzero_while_the_llm_is_unreachable(capsys):
    """The whole point of the command: refuse before a participant is present."""
    assert main([*_DEAD_LLM, "preflight"]) == 1
    out = capsys.readouterr().out
    assert "NOT READY" in out
    assert "BLOCKING" in out
    assert "did not answer" in out


def test_preflight_reports_the_task_set_even_when_a_component_fails(capsys):
    main([*_DEAD_LLM, "preflight"])
    out = capsys.readouterr().out
    assert "turns audited      : 33" in out
    assert "problems           : 16" in out


def test_preflight_never_claims_readiness_in_development_mode(capsys, offline_stack):
    """A clean preflight in dev mode must still say the output is not data.

    Every component is stubbed, so this reaches the READY branch: the point is
    that readiness is not the same thing as permission to collect data.
    """
    assert main([*offline_stack, "preflight"]) == 0
    out = capsys.readouterr().out
    assert "RESULT: READY" in out
    assert "FICTIONAL" in out
    assert "not analysable data" in out


def test_a_ready_preflight_still_carries_the_open_scientific_questions(capsys, offline_stack):
    """Technical readiness must not read as scientific readiness.

    Every component is stubbed and the run is green, yet the bands are still
    provisional and the labels still disagree with the measure. Those advisories
    have to survive a clean verdict, or the verdict reads as permission to
    collect.
    """
    assert main([*offline_stack, "preflight"]) == 0
    out = capsys.readouterr().out
    assert "RESULT: READY" in out
    assert "ADVISORY: condition band boundaries are PROVISIONAL" in out
    assert "not condition-consistent" in out


def test_preflight_still_refuses_a_research_run_on_the_offline_stack(capsys, offline_stack):
    """A fully working stack does not unlock collection.

    The remaining blockers are scientific, not technical: the condition labels
    are wrong and the bands are provisional. A green technical preflight must
    not paper over that.
    """
    code = main(
        [
            "--set", "experiment.is_research_run=true",
            "--set", "experiment.participant_id=P001",
            "--set", "experiment.business_weekday=wednesday",
            *offline_stack,
            "preflight",
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "not condition-consistent" in out
    assert "PROVISIONAL" in out
