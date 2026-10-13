"""Command line interface for the Nile Bites research instrument.

    python -m nbva live          # real-time voice conversation (the target experience)
    python -m nbva chat          # typed conversation, same pipeline, no microphone
    python -m nbva check         # validate config, database, tools, audio
    python -m nbva preflight     # would a run start? models, audio, task consistency
    python -m nbva audit         # declared vs measured code-switching condition
    python -m nbva devices       # list microphones and speakers
    python -m nbva config        # print the fully resolved configuration
    python -m nbva measure       # show the code-switching measure for a string
    python -m nbva session       # run scripted trials from the task file
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python src/nbva/cli.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from .config.loader import load_config  # noqa: E402
from .obs.events import utc_now_iso  # noqa: E402


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _apply_common(args) -> None:
    _force_utf8()
    extra = list(getattr(args, "config", None) or [])
    overrides: dict = {}
    for item in getattr(args, "set", None) or []:
        if "=" not in item:
            raise SystemExit(f"--set expects key=value, got {item!r}")
        key, value = item.split("=", 1)
        overrides[key.strip()] = value
    load_config(extra)


# ---------------------------------------------------------------------------


def cmd_live(args) -> int:
    from .agent.app import build_app
    from .agent.live import LiveVoiceLoop

    config = load_config(list(args.config or []))
    for item in args.set or []:
        key, value = item.split("=", 1)
        try:
            parsed = __import__("json").loads(value)
        except (ValueError, TypeError):
            parsed = value
        config.set(key.strip(), parsed)

    if args.audio_file or args.self_test or args.mic_check:
        return _cmd_live_dev(config, args)

    print("Nile Bites voice agent - live mode")
    print("FICTIONAL RESEARCH DATA. Press Ctrl+C to stop.\n")
    _require_participant_id(config)
    _enforce_task_set_gate(config)
    app = build_app(config)
    desc = app.describe()
    print(f"  session      : {desc['session_id']}")
    print(f"  experiment   : {desc['experiment_id']} (research run: {desc['is_research_run']})")
    if desc["participant_id"]:
        print(f"  participant  : {desc['participant_id']}")
    print(f"  business day : {desc['business_weekday']}")
    print(f"  ASR          : {desc['asr'].get('provider')} / {desc['asr'].get('model')}")
    print(f"  LLM          : {desc['llm'].get('provider')} / {desc['llm'].get('model')}")
    print(f"  TTS          : {desc['tts'].get('provider')} / {desc['tts'].get('voice')}")
    print(f"  log          : {desc['log_file']}\n")

    if hasattr(app.asr, "warmup"):
        print("  loading ASR model (first run downloads it)...")
        app.asr.warmup()
        print("  ASR ready.\n")

    loop = LiveVoiceLoop(app, speak=not args.no_speak, max_session_seconds=args.max_seconds)
    app.logger.log_session_header(
        mode="live",
        asr=app.asr.describe(),
        llm=app.llm.describe(),
        tts=app.tts.describe(),
        conditions=app.conditions.to_dict(),
        business_weekday=app.session.business_weekday,
    )

    try:
        loop.start()
    except Exception as exc:
        app.logger.error(f"could not start the microphone: {exc}")
        print(f"\nERROR: {exc}", file=sys.stderr)
        print("Run `python -m nbva devices` to list inputs, then set "
              "audio.input.device in config/audio.yaml.", file=sys.stderr)
        return 2

    print("Listening. Speak to the agent. Ctrl+C to quit.\n")
    try:
        import time

        started = time.time()
        while True:
            time.sleep(0.25)
            if args.max_seconds and (time.time() - started) > args.max_seconds:
                break
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        loop.stop()
        app.logger.log_session_footer(turns=loop.turns_handled)
    print(f"turns handled: {loop.turns_handled}")
    print(f"log: {app.logger.path}")
    return 0


def _load_task_file(config) -> list:
    from .research.taskdefs import load_tasks

    tasks_path = config.resolve_path("paths.tasks_file", "data/tasks/tasks.yaml")
    if not tasks_path.exists():
        print(f"task file not found: {tasks_path}", file=sys.stderr)
        raise SystemExit(2)
    return load_tasks(tasks_path)


def _enforce_task_set_gate(config, tasks=None) -> None:
    """Refuse a research run whose task set is not condition-consistent.

    Advisory on development runs, where an inconsistent set is normal while the
    set is being authored.
    """
    from .research.preflight import enforce_preflight, preflight_task_set

    report = preflight_task_set(config, tasks=_load_task_file(config) if tasks is None else tasks)
    enforce_preflight(report)


def _cmd_live_dev(config, args) -> int:
    """Exercise the live voice path without collecting any research data.

    These two modes exist because the parts most likely to be wrong - VAD
    boundaries, ASR, latency markers, TTS playback, session logging - are only
    reachable through the live loop, and the live loop normally writes a session
    log that looks like participant data. Both refuse to run as a research run
    so a smoke test can never end up in the dataset.
    """
    if bool(config.get("experiment.is_research_run", False)):
        print(
            "ERROR: --audio-file and --self-test are development checks and write a "
            "session log that is not participant speech.\n"
            "Run them with experiment.is_research_run=false.",
            file=sys.stderr,
        )
        return 2

    from .agent.app import build_app
    from .agent.live import LiveVoiceLoop

    app = build_app(config)
    print("live-path check (development only, NOT participant data)")
    print("=" * 60)
    print(f"  ASR: {app.asr.describe().get('provider')} / {app.asr.describe().get('model')}")
    print(f"  LLM: {app.llm.describe().get('provider')} / {app.llm.describe().get('model')}")
    print(f"  TTS: {app.tts.describe().get('provider')}")
    print(f"  log: {app.logger.path}\n")

    if args.mic_check:
        return _run_live_mic_check(app, args)

    if args.self_test:
        return _run_live_self_test(app, args)

    return _run_live_audio_file(app, args)


def _run_live_self_test(app, args) -> int:
    """Open the microphone, confirm it captures, then close. No turn is run."""
    import time

    from .agent.live import LiveVoiceLoop
    from .audio.io import probe_devices

    info = probe_devices()
    if not info.get("default_input"):
        print("ERROR: no default input device", file=sys.stderr)
        return 2
    print(f"  default input : {info['default_input']}")

    loop = LiveVoiceLoop(app, speak=False)
    try:
        loop.start()
    except Exception as exc:
        print(f"\nFAILED: could not open the microphone: {exc}", file=sys.stderr)
        print("Run `python -m nbva devices` and set audio.input.device.", file=sys.stderr)
        return 2

    seconds = float(args.self_test_seconds)
    try:
        print(f"  capturing {seconds:.1f}s (say something to test VAD)...")
        time.sleep(seconds)
    except KeyboardInterrupt:
        pass
    finally:
        loop.stop()

    vad = loop.vad
    print("\n" + "=" * 60)
    print("  microphone open : yes")
    print(f"  vad calibrated  : {vad.calibrated}")
    print(f"  noise floor     : {vad.measured_noise_floor}")
    print(f"  turns handled   : {loop.turns_handled}")
    if loop.turns_handled == 0:
        print("  note: no speech detected, which is expected in a quiet room")
    print("RESULT: OK")
    print(f"log: {app.logger.path}")
    return 0


def _run_live_audio_file(app, args) -> int:
    """Push a recorded utterance through the live loop's turn handler.

    This is the highest-value check available without a human: it runs the same
    ``_handle`` path as a live session, so the VAD-derived latency markers, real
    ASR, the tool loop and TTS are all exercised against recorded audio.
    """
    import time

    import numpy as np
    import soundfile as sf

    from .agent.live import LiveTurnRequest, LiveVoiceLoop
    from .asr.base import resample

    path = args.audio_file
    if not Path(path).exists():
        print(f"ERROR: audio file not found: {path}", file=sys.stderr)
        return 2

    data, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    target_rate = _input_sample_rate(app)
    data = resample(np.ascontiguousarray(data, dtype=np.float32), int(sample_rate), target_rate)
    if data.size == 0:
        print(f"ERROR: {path} decoded to zero samples", file=sys.stderr)
        return 2

    # A stub ASR transcribes nothing, so the expected words have to be handed
    # to it directly. With a real ASR this becomes the expected output, which
    # the check then compares against.
    expected = args.reference_text or None
    if expected and hasattr(app.asr, "set_next"):
        app.asr.set_next(expected)

    loop = LiveVoiceLoop(app, speak=not args.no_speak)
    request = LiveTurnRequest(
        audio=data,
        # A file has no VAD, so the endpoint comes from the sample count. The
        # latency markers stay internally consistent; only their absolute
        # origin is synthetic, which is why the mode is labelled a check and
        # not a measurement.
        speech_start_mono=time.monotonic(),
        speech_end_mono=time.monotonic() + data.size / float(target_rate),
    )
    app.logger.log_session_header(
        mode="live-audio-file",
        asr=app.asr.describe(),
        llm=app.llm.describe(),
        tts=app.tts.describe(),
        audio_file=str(path),
        development_only=True,
    )
    try:
        loop._handle(request)
    except Exception as exc:
        app.logger.error(f"turn failed: {type(exc).__name__}: {exc}")
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    record = loop.records[0]
    lat = record.latency
    pct = "n/a" if record.english_pct is None else f"{record.english_pct:.1f}%"
    print("\n" + "=" * 60)
    print(f"  duration      : {lat.utterance_audio_ms} ms of audio")
    print(f"  asr text      : {record.asr_transcript!r}")
    print(f"  response      : {record.response_text!r}")
    print(f"  tool calls    : {len(record.tool_calls)}")
    print(f"  asr ms        : {lat.asr_ms}")
    print(f"  nlu ms        : {lat.nlu_ms}")
    print(f"  tts ms        : {lat.tts_synthesis_ms}")
    print(f"  condition     : {record.measured_condition or 'none'} at {pct} english")
    print(f"  consistent    : {record.condition_consistent}")

    # The turn completing is the check. Whether the words are right is a
    # separate question, and a stub ASR is expected to return none.
    ok = lat.utterance_audio_ms is not None
    if not record.asr_transcript:
        print("\n  note: ASR returned no text.")
        if hasattr(app.asr, "set_next"):
            print("        this provider is a stub: pass --reference-text to feed it words")
        else:
            print("        the model may not be loaded, or the clip may contain no speech")
        ok = False
    if expected and record.asr_transcript and record.asr_transcript.strip() != expected.strip():
        print(f"\n  MISMATCH: expected {expected!r}, got {record.asr_transcript!r}")
        ok = False

    print("RESULT:", "OK" if ok else "PROBLEMS FOUND")
    app.logger.log_session_footer(turns=loop.turns_handled)
    print(f"log: {app.logger.path}")
    return 0 if ok else 1


def _run_live_mic_check(app, args) -> int:
    """Prove the whole chain with a real microphone as the input device.

    This is the only check that exercises the genuine article: PortAudio opens
    the microphone, the VAD finds a speech onset and endpoint in live audio, the
    turn worker runs, and the response is synthesised and played. Every earlier
    check either stubbed the ASR or pushed a file at the handler, so none of them
    could prove the capture side works.

    A stand-in speaker is simulated by playing a known utterance through the
    speakers while the microphone is open. The words therefore arrive as genuine
    acoustic input - the same path a participant's voice takes - without needing
    a person in the loop. It is a substitute for the human, not a mock of the
    signal: nothing is injected after the audio device.
    """
    import time

    from .agent.live import LiveVoiceLoop

    phrase = str(args.mic_check_text or "").strip()
    if not phrase:
        print("ERROR: --mic-check needs --mic-check-text", file=sys.stderr)
        return 2

    player = app.player
    if player is None:
        print("ERROR: --mic-check needs an output device to play the stand-in speaker", file=sys.stderr)
        return 2

    loop = LiveVoiceLoop(app, speak=not args.no_speak)
    try:
        loop.start()
    except Exception as exc:
        print(f"\nFAILED: could not open the microphone: {exc}", file=sys.stderr)
        print("Run `python -m nbva devices` and set audio.input.device.", file=sys.stderr)
        return 2

    app.logger.log_session_header(
        mode="live-mic-check",
        asr=app.asr.describe(),
        llm=app.llm.describe(),
        tts=app.tts.describe(),
        development_only=True,
        mic_check_text=phrase,
    )

    print(f"  microphone open, VAD armed (noise floor calibrating)...")
    # The VAD needs a moment of room tone to learn what silence looks like,
    # otherwise it treats the first syllables as noise.
    time.sleep(1.0)

    print(f"  playing stand-in utterance through the speakers: {phrase!r}")
    spoken = app.tts.synthesize(phrase)
    if spoken is None or spoken.audio is None or len(spoken.audio) == 0:
        loop.stop()
        print(f"\nFAILED: could not synthesise the stand-in utterance: {spoken.error!r}", file=sys.stderr)
        return 1
    player.play(spoken.audio, spoken.sample_rate)

    # The turn is detected asynchronously by the VAD and handled by the worker,
    # so wait for the record rather than handling it here.
    deadline = time.time() + float(args.mic_check_timeout)
    while time.time() < deadline and not loop.records:
        time.sleep(0.1)
    # Give the pipeline (ASR, LLM, TTS) room to finish after detection.
    if loop.records:
        loop.wait_for_idle(timeout=max(5.0, float(args.mic_check_timeout)))
    else:
        loop.wait_for_idle(timeout=5.0)
    try:
        loop.stop()
    except Exception:
        pass

    print("\n" + "=" * 60)
    if not loop.records:
        print("  RESULT: FAILED - no speech was detected on the microphone.")
        print("  The capture chain did not produce an utterance. Check that the")
        print("  speakers are audible to the chosen input device, or speak aloud")
        print("  while it runs. Use --mic-check-seconds style timing via")
        print("  `--self-test` to confirm capture alone.")
        app.logger.log_session_footer(turns=0)
        print(f"log: {app.logger.path}")
        return 1

    record = loop.records[0]
    lat = record.latency
    print(f"  vad endpoint   : {lat.utterance_audio_ms} ms of speech captured")
    print(f"  asr text       : {record.asr_transcript!r}")
    print(f"  response       : {record.response_text!r}")
    print(f"  tool calls     : {len(record.tool_calls)}")
    print(f"  asr ms         : {lat.asr_ms}")
    print(f"  primary latency: {lat.primary_latency_ms} ms")
    pct = "n/a" if record.english_pct is None else f"{record.english_pct:.1f}%"
    print(f"  condition      : {record.measured_condition or 'none'} at {pct} english")

    ok = bool(record.asr_transcript)
    if not ok:
        print("\n  PROBLEM: real ASR produced no transcript for detected speech.")
        print("  If asr.language is pinned to a language the speaker did not use,")
        print("  Whisper decodes into that language and the transcript is unusable.")
    app.logger.log_session_footer(turns=loop.turns_handled)
    print("\nRESULT:", "OK - real microphone audio traversed the full chain" if ok else "PROBLEMS FOUND")
    print(f"log: {app.logger.path}")
    return 0 if ok else 1


def _input_sample_rate(app) -> int:
    in_cfg = app.config.section("audio.input") or {}
    return int(in_cfg.get("sample_rate", 16000))


def _require_participant_id(config) -> None:
    """Refuse to start a formal run whose rows cannot be attributed.

    ``is_research_run`` only changes the label on a record. If a research run
    also has no participant id, the resulting JSONL looks like research data but
    cannot be pooled by participant or excluded as an outlier later, so the run
    is stopped here rather than discovered during analysis.
    """
    if not bool(config.get("experiment.is_research_run", False)):
        return
    participant_id = str(config.get("experiment.participant_id", "") or "").strip()
    if participant_id:
        return
    print(
        "ERROR: experiment.is_research_run is true but experiment.participant_id is empty.\n"
        "Set a pseudonymous participant id, e.g. --set experiment.participant_id=P001\n"
        "or keep experiment.is_research_run=false while developing the instrument.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def cmd_chat(args) -> int:
    from .agent.app import build_app

    config = load_config(list(args.config or []))
    for item in args.set or []:
        key, value = item.split("=", 1)
        config.set(key.strip(), value)

    _require_participant_id(config)
    app = build_app(config, attach_player=False)
    app.pipeline.player = None
    app.pipeline.tts = _null_tts()
    print("Nile Bites - typed mode (same pipeline, no microphone, no audio output)")
    print("FICTIONAL RESEARCH DATA. Type 'quit' to exit.\n")
    app.logger.log_session_header(mode="chat", asr=app.asr.describe(), llm=app.llm.describe(),
                                  tts=app.tts.describe())

    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in ("quit", "exit", "bye"):
            break
        result = app.pipeline.process_turn(text=line, speak=False)
        app.logger.log_turn(result.turn)
        print(f"nbva> {result.response_text}")
        if result.final_state and result.final_state.get("lines"):
            brief = ", ".join(
                f"{l['quantity']}x {l['item_name_en']}" for l in result.final_state["lines"]
            )
            print(f"     [order {result.final_state['order_id']}: {brief} "
                  f"= {result.final_state['total']} EGP]")
    app.logger.log_session_footer()
    print(f"log: {app.logger.path}")
    return 0


def _null_tts():
    from .tts.providers import NullTTSProvider

    return NullTTSProvider()


def cmd_check(args) -> int:
    from .agent.app import build_app

    config = load_config(list(args.config or []))
    for item in args.set or []:
        key, value = item.split("=", 1)
        config.set(key.strip(), value)

    ok = True
    print("Nile Bites research instrument - self check")
    print("=" * 60)

    # config
    print(f"\n[1/6] configuration\n  sources: {[str(s) for s in config.sources]}")
    print(f"  experiment_id: {config.get('experiment.experiment_id')}")
    print(f"  is_research_run: {config.get('experiment.is_research_run')}")

    # database
    print("\n[2/6] restaurant database")
    try:
        from .db.repository import RestaurantRepository

        repo = RestaurantRepository.from_path(
            config.resolve_path("paths.restaurant_db", "data/restaurant/nile_bites.json")
        )
        print(f"  items: {len(repo.menu)}")
        print(f"  categories: {len(repo.categories())}")
        print(f"  zones: {len(repo.zones())}")
        print(f"  promotions: {len(repo.active_promotions())}")
        print(f"  FICTIONAL: {repo.data.meta.IS_FICTIONAL}")
    except Exception as exc:
        ok = False
        print(f"  FAILED: {exc}")

    # tools
    print("\n[3/6] deterministic tools")
    try:
        from .db.orders import OrderStore
        from .tools.registry import build_default_registry

        orders = OrderStore(repo, "check")
        reg = build_default_registry(repo, orders)
        r = reg.dispatch("get_price", {"item": "chicken burger", "quantity": 2})
        print(f"  tools: {len(reg.tool_names())}")
        print(f"  get_price(chicken burger x2) = {r.get('line_total_egp')} EGP")
        if r.get("line_total_egp") != 290:
            ok = False
            print("  FAILED: unexpected price")
    except Exception as exc:
        ok = False
        print(f"  FAILED: {exc}")

    # measure
    print("\n[4/6] code-switching measure")
    try:
        from .research.codeswitch import build_analyzer

        an = build_analyzer(config, repo)
        for sample in [
            "عايز اتنين تشيكن برجر من غير جبنة",
            "I want اتنين chicken burgers من غير cheese",
        ]:
            tr = an.analyze(sample)
            print(f"  {tr.english_pct:6.2f}%  en={tr.english_lexical_tokens} "
                  f"total={tr.total_lexical_tokens}  {sample[:50]}")
    except Exception as exc:
        ok = False
        print(f"  FAILED: {exc}")

    # components
    print("\n[5/6] pipeline components")
    try:
        from .asr.providers import build_asr
        from .llm.providers import build_provider
        from .tts.providers import build_tts

        asr = build_asr(config)
        print(f"  ASR: {asr.describe()}")
    except Exception as exc:
        print(f"  ASR unavailable: {exc}")
    try:
        llm = build_provider(config)
        print(f"  LLM: {llm.describe()}")
    except Exception as exc:
        print(f"  LLM unavailable: {exc}")
    try:
        tts = build_tts(config)
        print(f"  TTS: {tts.describe()}")
    except Exception as exc:
        print(f"  TTS unavailable: {exc}")

    # audio
    print("\n[6/6] audio devices")
    try:
        from .audio.io import probe_devices

        info = probe_devices()
        print(f"  default input : {info['default_input']}")
        print(f"  default output: {info['default_output']}")
    except Exception as exc:
        print(f"  audio unavailable: {exc}")

    print("\n" + "=" * 60)
    print("RESULT:", "OK" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


def cmd_devices(args) -> int:
    from .audio.io import list_devices

    _force_utf8()
    print("Inputs:")
    for d in list_devices("input"):
        print(f"  [{d.index:>2}] {d.name}  ({d.max_input_channels} ch @{d.default_samplerate:.0f})")
    print("\nOutputs:")
    for d in list_devices("output"):
        print(f"  [{d.index:>2}] {d.name}  ({d.max_output_channels} ch @{d.default_samplerate:.0f})")
    print("\nSet audio.input.device / audio.output.device in config/audio.yaml by index.")
    return 0


def cmd_config(args) -> int:
    _force_utf8()
    config = load_config(list(args.config or []))
    if args.key:
        value = config.get(args.key, None)
        print(f"{args.key} = {value!r}")
    else:
        print(config.dump_yaml())
    return 0


def cmd_measure(args) -> int:
    from .db.repository import RestaurantRepository
    from .research.codeswitch import build_analyzer

    _force_utf8()
    config = load_config(list(args.config or []))
    repo = RestaurantRepository.from_path(
        config.resolve_path("paths.restaurant_db", "data/restaurant/nile_bites.json")
    )
    analyzer = build_analyzer(config, repo)
    text = " ".join(args.text)
    trace = analyzer.analyze(text)
    print(f"text: {text}")
    print(f"English lexical tokens : {trace.english_lexical_tokens}")
    print(f"Total lexical tokens   : {trace.total_lexical_tokens}")
    print(f"English proportion     : {trace.english_pct:.2f}%")
    if args.tokens:
        print("\ntokens:")
        for t in trace.tokens:
            mark = "EN " if t.is_english else "   "
            print(f"  {mark} {t.text:<20} [{t.token_class}] {t.reason}")
    return 0


def cmd_session(args) -> int:
    from .agent.app import build_app
    from .research.taskdefs import evaluate_task, load_tasks

    _force_utf8()
    config = load_config(list(args.config or []))
    for item in args.set or []:
        key, value = item.split("=", 1)
        config.set(key.strip(), value)
    _require_participant_id(config)
    _enforce_task_set_gate(config)
    app = build_app(config, attach_player=False)
    app.pipeline.player = None
    app.pipeline.tts = _null_tts()

    tasks_path = config.resolve_path("paths.tasks_file", "data/tasks/tasks.yaml")
    if not tasks_path.exists():
        print(f"task file not found: {tasks_path}")
        return 1
    tasks = load_tasks(tasks_path)
    if args.task_id:
        tasks = [t for t in tasks if t.task_id in set(args.task_id)]
        if not tasks:
            print(f"no task matched {args.task_id}")
            return 1

    print(f"running {len(tasks)} scripted trial(s) in text mode")
    print("FICTIONAL RESEARCH DATA. Latency is NOT measured in text mode.\n")
    app.logger.log_session_header(mode="scripted", asr=app.asr.describe(), llm=app.llm.describe(),
                                  tasks=[t.task_id for t in tasks])

    summary: list[tuple[str, bool, float]] = []
    for task in tasks:
        app.session.reset_for_new_trial()
        trial_id = app.session.begin_trial(task)
        day = app.session.business_weekday
        print(f"--- {task.task_id} [{task.family}] condition={task.declared_condition} "
              f"weekday={day} ---")
        tool_calls: list[dict] = []
        last_text = ""
        for turn in task.turns:
            result = app.pipeline.process_turn(
                text=turn.utterance,
                reference_transcript=turn.reference_transcript or turn.utterance,
                declared_condition=turn.declared_condition or task.declared_condition,
                task_family=task.family,
                task_id=task.task_id,
                trial_id=trial_id,
                speak=False,
            )
            record = result.turn
            if record.wer is not None:
                record.extra["typed_input_wer"] = record.wer
            app.logger.log_turn(record)
            # The tool loop drains the registry itself and hands the records
            # back on the result, so read them from there rather than from the
            # session, which is always empty by this point.
            tool_calls.extend(result.tool_calls)
            last_text = result.response_text
            print(f"  you > {turn.utterance}")
            print(f"  nbva> {result.response_text}")
            for call in result.tool_calls:
                print(f"       tool {call.get('name')} ok={not call.get('error_code')}")

        order = app.session.current_order()
        print(f"  final state: {order}")

        # The assertions are evaluated here rather than inside the pipeline so a
        # trial is scored against the state the deterministic layer actually
        # ended in, not against what the model claimed.
        evaluation = evaluate_task(task, order, tool_calls, last_text)
        failed = [c for c in evaluation.checks if not c["passed"]]
        for check in failed:
            print(f"  FAILED {check['check']}: expected {check['expected']!r}, "
                  f"got {check['actual']!r}")
        verdict = "PASS" if evaluation.success else "FAIL"
        print(f"  {verdict} score={evaluation.score:.2f} "
              f"({len(evaluation.checks) - len(failed)}/{len(evaluation.checks)} checks)\n")
        summary.append((task.task_id, evaluation.success, evaluation.score))

        app.logger.log_event(
            "task_evaluated",
            trial_id=trial_id,
            task_id=task.task_id,
            business_weekday=day,
            success=evaluation.success,
            score=evaluation.score,
            checks=evaluation.checks,
        )

    app.logger.log_session_footer()
    passed = sum(1 for _, ok, _ in summary if ok)
    print(f"trials: {passed}/{len(summary)} passed their assertions")
    print(f"log: {app.logger.path}")
    return 0 if passed == len(summary) else 1


# ---------------------------------------------------------------------------


def cmd_audit(args) -> int:
    """Report declared vs measured condition for every authored turn."""
    from .research.taskaudit import audit_task_set, render_audit
    from .research.conditions import load_conditions
    from .research.codeswitch import build_analyzer

    config = load_config(list(args.config or []))
    for item in args.set or []:
        key, value = item.split("=", 1)
        config.set(key.strip(), value)

    repository = None
    try:
        from .db.repository import RestaurantRepository

        repository = RestaurantRepository.from_path(
            config.resolve_path("paths.restaurant_db", "data/restaurant/nile_bites.json")
        )
    except Exception as exc:
        print(f"note: menu unavailable ({exc}); measuring against the built-in list")

    tasks = _load_task_file(config)
    if args.task_id:
        tasks = [t for t in tasks if t.task_id in set(args.task_id)]
        if not tasks:
            print(f"no task matched {args.task_id}", file=sys.stderr)
            return 2

    conditions = load_conditions(config)
    audit = audit_task_set(tasks, build_analyzer(config, repository), conditions)
    print(render_audit(audit, show_all=args.all))

    if args.json:
        print()
        print(json.dumps(audit.to_dict(), indent=2, ensure_ascii=False))
    return 0 if audit.consistent else 1


def cmd_preflight(args) -> int:
    """Check whether a formal run could start, without collecting anything.

    ``nbva check`` proves the instrument is internally coherent. This proves the
    machine is ready: the model loads, the endpoint answers, the audio devices
    open, and the task set is condition-consistent. It writes no session log and
    records no trial.
    """
    from .asr.providers import build_asr
    from .db.repository import RestaurantRepository
    from .llm.providers import build_provider
    from .research.preflight import preflight_task_set
    from .tts.providers import build_tts

    config = load_config(list(args.config or []))
    for item in args.set or []:
        key, value = item.split("=", 1)
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            parsed = value
        config.set(key.strip(), parsed)

    _force_utf8()
    is_research = bool(config.get("experiment.is_research_run", False))
    blocking: list[str] = []
    advisory: list[str] = []

    print("Nile Bites preflight - can a run start?")
    print("=" * 64)
    print(f"  research run : {is_research}")
    print(f"  experiment   : {config.get('experiment.experiment_id')}")
    print(f"  participant  : {config.get('experiment.participant_id') or '(none)'}")
    print(f"  business day : {config.get('experiment.business_weekday')}")

    try:
        repository = RestaurantRepository.from_path(
            config.resolve_path("paths.restaurant_db", "data/restaurant/nile_bites.json")
        )
    except Exception as exc:
        repository = None
        blocking.append(f"restaurant database unusable: {exc}")

    # -- task set consistency (the hard gate) --------------------------------
    print("\n[task set]")
    try:
        tasks = _load_task_file(config)
    except SystemExit:
        tasks = []
    report = preflight_task_set(config, tasks=tasks, repository=repository)
    audit = report.audit
    if audit is not None:
        print(f"  turns audited      : {audit.total_turns}")
        print(f"  declared counts    : {audit.declared_counts()}")
        print(f"  measured counts    : {audit.measured_counts()}")
        print(f"  problems           : {len(audit.problems)} "
              f"(mismatched {len(audit.mismatches)}, unbanded {len(audit.unbanded)}, "
              f"undeclared {len(audit.undeclared)})")
        for note in audit.notes:
            print(f"  note               : {note}")
    blocking.extend(report.problems)
    advisory.extend(report.warnings)

    # -- models and endpoints ------------------------------------------------
    print("\n[components]")
    if hasattr(build_asr, "preflight"):
        asr_problems, asr_notes = build_asr.preflight(config)
        blocking.extend(asr_problems)
        advisory.extend(asr_notes)
    try:
        asr = build_asr(config)
        detail = asr.describe()
        print(f"  asr  : {detail.get('provider')} / {detail.get('model')}")
    except Exception as exc:
        blocking.append(f"ASR unavailable: {exc}")

    try:
        llm = build_provider(config)
        detail = llm.describe()
        print(f"  llm  : {detail.get('provider')} / {detail.get('model')}")
        if hasattr(llm, "preflight_status"):
            status, detail_msg = llm.preflight_status()
            if status == "ready":
                pass  # no advisory, no block
            elif status == "unverified":
                advisory = f"LLM provider status: unverified — {detail_msg}"
                print(f"WARNING: {advisory}", file=sys.stderr)
                blocking.append(advisory)
            elif status == "blocked":
                blocking.append(f"LLM preflight blocked: {detail_msg}")
        elif hasattr(llm, "preflight"):
            ok, problem = llm.preflight()
            if not ok:
                blocking.append(f"LLM preflight failed: {problem}")
        else:
            # Legacy providers that only expose describe() — treat as ready
            # so the run can start; the decision is on the researcher.
            pass
    except Exception as exc:
        blocking.append(f"LLM unavailable: {exc}")

    try:
        tts = build_tts(config)
        print(f"  tts  : {tts.describe().get('provider')}")
    except Exception as exc:
        blocking.append(f"TTS unavailable: {exc}")

    # -- audio ---------------------------------------------------------------
    print("\n[audio]")
    try:
        from .audio.io import probe_devices

        info = probe_devices()
        print(f"  input  : {info.get('default_input')}")
        print(f"  output : {info.get('default_output')}")
        if not info.get("default_input"):
            blocking.append("no default input device")
    except Exception as exc:
        blocking.append(f"audio unavailable: {exc}")

    # -- verdict -------------------------------------------------------------
    print("\n" + "=" * 64)
    for item in dict.fromkeys(advisory):
        print(f"ADVISORY: {item}")
    for item in dict.fromkeys(blocking):
        print(f"BLOCKING: {item}")

    if blocking:
        print("\nRESULT: NOT READY - a formal run would not start cleanly")
        return 1
    print("\nRESULT: READY")
    if is_research:
        print("  formal collection still requires the pending research decisions to be signed off")
    else:
        print("  development mode: output is FICTIONAL and is not analysable data")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nbva",
        description="Nile Bites voice agent - research instrument for Egyptian Arabic-English "
        "code-switching studies. The restaurant data is fictional.",
    )
    p.add_argument("--config", action="append", help="extra config file (repeatable)")
    p.add_argument("--set", action="append", help="override a config key, e.g. --set llm.model=gpt-4o")
    sub = p.add_subparsers(dest="command", required=True)

    live = sub.add_parser("live", help="real-time voice conversation with microphone and speakers")
    live.add_argument("--no-speak", action="store_true", help="process speech but stay silent")
    live.add_argument("--max-seconds", type=int, default=0, help="stop after N seconds (0 = no limit)")
    live.add_argument(
        "--audio-file",
        help="development check: push a recorded utterance through the live turn handler",
    )
    live.add_argument(
        "--self-test",
        action="store_true",
        help="development check: open the microphone, confirm capture, close. No turn is run",
    )
    live.add_argument(
        "--self-test-seconds", type=float, default=5.0, help="how long --self-test listens"
    )
    live.add_argument(
        "--mic-check",
        action="store_true",
        help=(
            "end-to-end check: speak TEXT through the speakers while the microphone is "
            "open, then run the detected utterance through ASR, LLM, tools and TTS"
        ),
    )
    live.add_argument(
        "--mic-check-text",
        default="I want two chicken burgers and a large cola please.",
        help="utterance --mic-check plays through the speakers as a stand-in speaker",
    )
    live.add_argument(
        "--mic-check-timeout", type=float, default=60.0, help="give up waiting for speech"
    )
    live.add_argument(
        "--reference-text",
        help="words a stub ASR should return, or the expected output to compare against",
    )
    live.set_defaults(func=cmd_live)

    chat = sub.add_parser("chat", help="typed conversation, same pipeline, no audio")
    chat.set_defaults(func=cmd_chat)

    check = sub.add_parser("check", help="validate config, database, tools, measure, audio")
    check.set_defaults(func=cmd_check)

    pre = sub.add_parser(
        "preflight", help="check whether a run could start: models, audio, task consistency"
    )
    pre.set_defaults(func=cmd_preflight)

    aud = sub.add_parser(
        "audit", help="compare each authored turn's declared condition with its measured one"
    )
    aud.add_argument("--all", action="store_true", help="list every turn, not just mismatches")
    aud.add_argument("--json", action="store_true", help="also print the audit as JSON")
    aud.add_argument("--task-id", action="append", help="audit only these task ids")
    aud.set_defaults(func=cmd_audit)

    dev = sub.add_parser("devices", help="list microphones and speakers")
    dev.set_defaults(func=cmd_devices)

    cfg = sub.add_parser("config", help="print the resolved configuration")
    cfg.add_argument("--key", help="print a single dotted key")
    cfg.set_defaults(func=cmd_config)

    meas = sub.add_parser("measure", help="compute the code-switching measure for a string")
    meas.add_argument("text", nargs="+")
    meas.add_argument("--tokens", action="store_true", help="show the per-token classification")
    meas.set_defaults(func=cmd_measure)

    sess = sub.add_parser("session", help="run scripted trials from the task file")
    sess.add_argument("--task-id", action="append", help="run only these task ids")
    sess.set_defaults(func=cmd_session)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
