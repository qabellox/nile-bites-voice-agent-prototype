"""Probe the real audio path: TTS synthesis, file read, and playback timing.

Not a research measurement. Checks that the instrument can actually make sound.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from nbva.config.loader import load_config  # noqa: E402
from nbva.tts.providers import build_tts  # noqa: E402


def main() -> int:
    config = load_config()
    provider_name = config.get("tts.provider", "edge")
    print(f"configured tts.provider = {provider_name}")

    tts = build_tts(config)
    print("describe:", tts.describe())

    samples = [
        "تمام، اتنين تشيكن برجر وكوكا كولا.",
        "The total is 383 Egyptian pounds, including delivery.",
    ]
    ok = True
    for text in samples:
        started = time.perf_counter()
        result = tts.synthesize(text)
        elapsed = (time.perf_counter() - started) * 1000.0
        if result.error:
            print(f"  FAIL synthesis: {result.error}")
            ok = False
            continue
        audio = result.audio
        dur = len(audio) / result.sample_rate if audio is not None and result.sample_rate else 0.0
        peak = float(np.max(np.abs(audio))) if audio is not None and audio.size else 0.0
        print(
            f"  OK voice={result.voice} sr={result.sample_rate} "
            f"samples={0 if audio is None else len(audio)} "
            f"dur={dur:.2f}s peak={peak:.3f} synth={elapsed:.0f}ms"
        )
        if audio is None or not audio.size:
            ok = False

    # playback check
    print("\nplayback check (this should produce sound):")
    result = tts.synthesize("Nile Bites. Testing one two three.")
    if result.error or result.audio is None:
        print("  skipped, no audio to play:", result.error)
        return 1 if not ok else 0

    from nbva.audio.io import AudioPlayer

    player = AudioPlayer()
    first_sample_at: list[float] = []
    t0 = time.perf_counter()
    player.play(
        result.audio,
        result.sample_rate,
        on_first_sample=lambda: first_sample_at.append(time.perf_counter()),
    )
    total = time.perf_counter() - t0
    if first_sample_at:
        print(f"  played {len(result.audio)/result.sample_rate:.2f}s of audio in {total*1000:.0f} ms")
        print(f"  first sample reached the device after {(first_sample_at[0]-t0)*1000:.1f} ms")
    else:
        ok = False
        print("  FAIL: playback never reported a first sample")

    print("\nRESULT:", "OK" if ok else "PROBLEMS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
