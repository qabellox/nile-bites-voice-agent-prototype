"""Generate the development-only pilot dataset (synthetic control audio).

Matched design: ONE task meaning (order two chicken burgers, pickup) in FIVE
code-switching variants C0-C4. Each reference transcript is MEASURED with the
locked analyzer and asserted into its declared band; the verified percentage
is recorded. Audio is Edge-TTS synthetic speech, clearly labeled as such.

This is DEVELOPMENT infrastructure, not research data: no human speakers,
no participant data, no formal collection.
"""

import asyncio
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import edge_tts

from nbva.config.loader import load_config
from nbva.agent.app import build_app

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "pilot"
AUDIO = OUT / "audio"

# task_id, band, reference, tts voice. Meaning held constant: 2 chicken burgers.
VARIANTS = [
    ("ORD2-C0", "C0", "عايز اتنين برجر فراخ من فضلك", "ar-EG-SalmaNeural"),
    ("ORD2-C1", "C1", "عايز اعمل order لاتنين برجر فراخ من فضلك", "ar-EG-SalmaNeural"),
    ("ORD2-C2", "C2", "عايز اتنين chicken burgers من فضلك", "ar-EG-SalmaNeural"),
    ("ORD2-C3", "C3", "عايز two chicken burgers من فضلك please", "ar-EG-SalmaNeural"),
    ("ORD2-C4", "C4", "Can I order two chicken burgers من فضلك", "en-US-JennyNeural"),
]


async def synth(text: str, voice: str, path: Path) -> None:
    await edge_tts.Communicate(text, voice).save(str(path))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    AUDIO.mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    app = build_app(cfg, attach_player=False)
    analyzer = app.pipeline.analyzer
    conditions = app.pipeline.conditions

    tasks, trials = [], []
    for task_id, band, reference, voice in VARIANTS:
        trace = analyzer.analyze(reference)
        pct = round(float(trace.english_pct), 2)
        result = conditions.evaluate(pct, band)
        status = (
            f"{band}: pct={pct} en={trace.english_lexical_tokens}/{trace.total_lexical_tokens} "
            f"measured={result.measured} consistent={result.consistent}"
        )
        print(status, flush=True)
        if not result.consistent or result.measured != band:
            raise SystemExit(f"ABORT: reference for {task_id} does not land in {band}: {status}")

        wav = AUDIO / f"{task_id.lower()}.wav"
        mp3 = wav.with_suffix(".mp3")
        asyncio.run(synth(reference, voice, mp3))
        import soundfile as sf

        data, sr = sf.read(str(mp3), dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        if sr != 16000:
            import numpy as np

            n = int(round(len(data) * 16000 / sr))
            idx = (np.arange(n) * (len(data) / max(n, 1))).astype(int)
            data = data[np.clip(idx, 0, len(data) - 1)]
        sf.write(str(wav), data.astype("float32"), 16000)
        mp3.unlink(missing_ok=True)
        duration_s = round(len(data) / 16000, 2)

        tasks.append({
            "task_id": task_id,
            "family": "order_creation",
            "declared_condition": band,
            "business_weekday": "monday",
            "turns": [{"index": 0, "utterance": reference,
                       "reference_transcript": reference,
                       "declared_condition": band,
                       "note": "dev-only synthetic control"}],
            "expected_lines": [{"item_id": "NB-BURG-001", "quantity": 2}],
            "expected_order_mode": "pickup",
            "expect_no_error": True,
            "expected_tools": ["create_order"],
            "notes": "DEVELOPMENT ONLY. Synthetic Edge-TTS audio. Same meaning across C0-C4; only the language mix varies.",
        })
        trials.append({
            "trial_id": f"PILOT-{task_id}",
            "task_id": task_id,
            "audio_path": f"data/pilot/audio/{wav.name}",
            "speaker": {"source": "synthetic", "engine": "edge-tts", "voice": voice},
            "verified_english_pct": pct,
            "declared_condition": band,
            "note": "DEVELOPMENT ONLY. Not research data.",
        })
        print(f"  audio: {wav.name} {duration_s}s voice={voice}", flush=True)

    with open(OUT / "tasks.yaml", "w", encoding="utf-8") as fh:
        yaml.safe_dump({"meta": {"kind": "development-pilot",
                                 "meaning": "order two chicken burgers, pickup",
                                 "synthetic": True,
                                 "not_research_data": True},
                        "tasks": tasks}, fh, allow_unicode=True, sort_keys=False)
    with open(OUT / "trials.yaml", "w", encoding="utf-8") as fh:
        yaml.safe_dump({"trials": trials}, fh, allow_unicode=True, sort_keys=False)
    with open(OUT / "README-DEV-ONLY.md", "w", encoding="utf-8") as fh:
        fh.write(
            "# Pilot dataset — DEVELOPMENT ONLY, NOT RESEARCH DATA\n\n"
            "Matched C0–C4 variants of one meaning (order two chicken burgers,\n"
            "pickup), synthesized with Edge TTS. No human speakers, no participant\n"
            "data. Reference transcripts carry analyzer-verified English\n"
            "percentages. Formal data collection has NOT started.\n")
    print(f"Wrote {OUT / 'tasks.yaml'} and {OUT / 'trials.yaml'}", flush=True)


if __name__ == "__main__":
    main()
