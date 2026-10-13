"""TTS adapters: Microsoft Edge neural voices, Windows SAPI5, and OpenAI.

Edge is the default because it needs no API key, produces natural bilingual
Arabic/English speech, and a single multilingual voice can speak both languages
in one sentence, which matters when the user code-switches mid-utterance.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

from .base import TTSResult, load_audio_file

# Arabic-Indic digits are understood, but Edge reads them better as ASCII.
_ARABIC_DIGITS = str.maketrans("\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669", "0123456789")


def normalize_for_speech(text: str) -> str:
    out = (text or "").translate(_ARABIC_DIGITS)
    out = re.sub(r"\s+", " ", out).strip()
    return out


class EdgeTTSProvider:
    """Microsoft Edge neural voices via the edge-tts package."""

    name = "edge"

    def __init__(
        self,
        voice: str = "ar-EG-SalmaNeural",
        fallback_voice: str = "en-US-JennyNeural",
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        allow_fallback: bool = True,
        output_format: str = "mp3",
    ):
        self.voice = voice
        self.fallback_voice = fallback_voice
        self.rate = rate
        self.volume = volume
        self.pitch = pitch
        self.allow_fallback = allow_fallback
        self.output_format = output_format
        self._warned = False

    def synthesize(self, text: str, voice: str | None = None) -> TTSResult:
        started = time.perf_counter()
        speech = normalize_for_speech(text)
        if not speech:
            return _empty(self.name, voice or self.voice, "empty text")

        target_voice = voice or self.voice
        try:
            audio, sr, used = self._synthesize_sync(speech, target_voice)
        except Exception as exc:
            if self.allow_fallback and target_voice != self.fallback_voice:
                try:
                    audio, sr, used = self._synthesize_sync(speech, self.fallback_voice)
                    return TTSResult(
                        audio=audio,
                        sample_rate=sr,
                        provider=self.name,
                        voice=used,
                        duration_ms=(time.perf_counter() - started) * 1000.0,
                        warnings=[f"primary voice failed ({exc}); used fallback"],
                    )
                except Exception as exc2:
                    return _empty(self.name, target_voice, f"{exc}; fallback failed: {exc2}")
            return _empty(self.name, target_voice, f"{type(exc).__name__}: {exc}")

        return TTSResult(
            audio=audio,
            sample_rate=sr,
            provider=self.name,
            voice=used,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )

    def _synthesize_sync(self, speech: str, voice: str) -> tuple[np.ndarray, int, str]:
        async def go() -> tuple[np.ndarray, int]:
            import edge_tts

            communicate = edge_tts.Communicate(speech, voice, rate=self.rate, volume=self.volume, pitch=self.pitch)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                path = Path(tmp.name)
            try:
                await communicate.save(str(path))
                data, sr = load_audio_file(path)
                return data, sr
            finally:
                path.unlink(missing_ok=True)

        audio, sr = asyncio.run(go())
        return audio, sr, voice

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "voice": self.voice,
            "fallback_voice": self.fallback_voice,
            "rate": self.rate,
            "volume": self.volume,
            "pitch": self.pitch,
        }


class SapiTTSProvider:
    """Windows SAPI5 via pyttsx3. Fully offline, low latency, robotic voice.

    Useful as a fallback when there is no internet connection, and for latency
    work where synthesis overhead must be minimised and held constant.
    """

    name = "sapi"

    def __init__(self, voice_hint: str | None = None, rate: int = 175):
        self.voice_hint = voice_hint
        self.rate = rate
        self._engine = None
        self._voice_name: str | None = None
        self._warned_no_voice = False

    def _ensure_engine(self):
        if self._engine is None:
            import pyttsx3

            self._engine = pyttsx3.init()
        return self._engine

    def _pick_voice(self) -> None:
        engine = self._ensure_engine()
        try:
            voices = engine.getProperty("voices")
        except Exception:
            return
        for v in voices:
            name = (v.name or "")
            if self.voice_hint and self.voice_hint.lower() in name.lower():
                engine.setProperty("voice", v.id)
                self._voice_name = name
                return
        if self._voice_name is None and voices:
            # Prefer an Arabic voice when one is installed, else the default.
            for v in voices:
                if "arab" in (v.name or "").lower() or "hoda" in (v.name or "").lower():
                    engine.setProperty("voice", v.id)
                    self._voice_name = v.name
                    return

    def synthesize(self, text: str, voice: str | None = None) -> TTSResult:
        started = time.perf_counter()
        speech = normalize_for_speech(text)
        if not speech:
            return _empty(self.name, voice or "sapi", "empty text")
        try:
            engine = self._ensure_engine()
            self._pick_voice()
            engine.setProperty("rate", self.rate)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                path = Path(tmp.name)
            try:
                engine.save_to_file(speech, str(path))
                engine.runAndWait()
                if path.stat().st_size < 128:
                    return _empty(self.name, self._voice_name or "sapi", "SAPI produced no audio")
                data, sr = load_audio_file(path)
            finally:
                path.unlink(missing_ok=True)
        except Exception as exc:
            return _empty(self.name, "sapi", f"{type(exc).__name__}: {exc}")

        return TTSResult(
            audio=data,
            sample_rate=sr,
            provider=self.name,
            voice=self._voice_name or "sapi-default",
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "rate": self.rate, "voice_hint": self.voice_hint}


class OpenAITTSProvider:
    name = "openai"

    def __init__(
        self,
        model: str = "tts-1",
        voice: str = "alloy",
        api_key_env: str = "OPENAI_API_KEY",
        speed: float = 1.0,
    ):
        import os

        self.model = model
        self.voice = voice
        self.speed = speed
        key = os.environ.get(api_key_env, "")
        if not key:
            raise RuntimeError(f"{api_key_env} is not set.")
        from openai import OpenAI

        self.client = OpenAI(api_key=key, max_retries=0)

    def synthesize(self, text: str, voice: str | None = None) -> TTSResult:
        started = time.perf_counter()
        speech = normalize_for_speech(text)
        if not speech:
            return _empty(self.name, voice or self.voice, "empty text")
        try:
            resp = self.client.audio.speech.create(
                model=self.model,
                voice=voice or self.voice,
                input=speech,
                speed=self.speed,
                response_format="mp3",
            )
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                path = Path(tmp.name)
            try:
                path.write_bytes(resp.read())
                data, sr = load_audio_file(path)
            finally:
                path.unlink(missing_ok=True)
        except Exception as exc:
            return _empty(self.name, voice or self.voice, f"{type(exc).__name__}: {exc}")
        return TTSResult(
            audio=data,
            sample_rate=sr,
            provider=self.name,
            voice=voice or self.voice,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "model": self.model, "voice": self.voice, "speed": self.speed}


class NullTTSProvider:
    """No audio. Useful for text-only runs and tests."""

    name = "null"

    def __init__(self):
        self.model = "null"

    def synthesize(self, text: str, voice: str | None = None) -> TTSResult:
        return TTSResult(audio=None, sample_rate=0, provider=self.name, voice="none")

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "research_valid_for_latency": False}


PROVIDERS = {
    "edge": EdgeTTSProvider,
    "sapi": SapiTTSProvider,
    "openai": OpenAITTSProvider,
    "null": NullTTSProvider,
}


def build_tts(config):
    provider_name = str(config.get("tts.provider", "null")).strip().lower()
    if provider_name not in PROVIDERS:
        raise ValueError(f"unknown tts.provider {provider_name!r}; available: {sorted(PROVIDERS)}")
    section = config.section("tts") or {}
    # Only pass what the chosen provider accepts. The offline providers (null,
    # and any future stub) take no voice settings, and forwarding the shipped
    # config to them would make the silent path unusable.
    accepted = inspect.signature(PROVIDERS[provider_name].__init__).parameters
    kwargs: dict[str, Any] = {}
    for key in ("voice", "fallback_voice", "rate", "volume", "pitch", "allow_fallback",
                "output_format", "voice_hint", "api_key_env", "speed"):
        if key in accepted and key in section and section[key] is not None:
            kwargs[key] = section[key]
    if "rate" in kwargs and provider_name == "sapi":
        try:
            kwargs["rate"] = int(str(kwargs["rate"]).lstrip("+").rstrip("%"))
        except (TypeError, ValueError):
            kwargs.pop("rate")
    return PROVIDERS[provider_name](**kwargs)
