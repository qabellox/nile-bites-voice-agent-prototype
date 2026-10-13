"""ASR adapters: local Whisper, hosted Whisper, typed input, and reference echo.

``faster_whisper`` is the default because it runs locally on CPU with no API key,
which makes the instrument reproducible and usable offline. Its model and
decoding parameters are controlled variables and are recorded in every trial.
"""

from __future__ import annotations

import inspect
import time
from pathlib import Path
from typing import Any

import numpy as np

from .base import ASRResult, resample

# Download sizes for the faster-whisper checkpoints, in MB, so a preflight can
# warn before a first run stalls on a multi-hundred-megabyte download.
_WHISPER_MODEL_MB = {
    "tiny": 75,
    "base": 145,
    "small": 485,
    "medium": 1530,
    "large-v2": 3090,
    "large-v3": 3090,
}


def _approx_model_mb(model: str) -> int:
    return _WHISPER_MODEL_MB.get(str(model).lower(), 500)


class FasterWhisperProvider:
    """Local Whisper through CTranslate2. No network, no API key."""

    name = "faster_whisper"

    def __init__(
        self,
        model: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str = "ar",
        task: str = "transcribe",
        beam_size: int = 1,
        vad_filter: bool = False,
        condition_on_previous_text: bool = False,
        initial_prompt: str | None = None,
        sample_rate: int = 16000,
        num_workers: int = 1,
        download_root: str | None = None,
    ):
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.task = task
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.condition_on_previous_text = condition_on_previous_text
        self.initial_prompt = initial_prompt
        self.sample_rate = sample_rate
        self.num_workers = num_workers
        self.download_root = download_root
        self._model = None
        self.model = model

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            kwargs: dict[str, Any] = {
                "device": self.device,
                "compute_type": self.compute_type,
                "num_workers": self.num_workers,
            }
            if self.download_root:
                kwargs["download_root"] = self.download_root
            self._model = WhisperModel(self.model_name, **kwargs)
        return self._model

    def warmup(self) -> None:
        """Load the model up front so the first trial is not penalised by load time."""
        self._ensure_model()

    @classmethod
    def preflight(cls, config) -> tuple[list[str], list[str]]:
        """Report ``(blockers, advisories)`` for this ASR without loading it.

        Loading ``small`` takes tens of seconds and downloading it takes minutes,
        so a preflight that instantiated the model would be useless: by the time
        it answered, the run would have started. Instead this checks that
        faster-whisper is importable and that the model is already cached, and
        treats a missing download as an advisory rather than a blocker, because
        ``nbva live`` warms the model up on first use.
        """
        section = config.section("asr") or {}
        sub = section.get("faster_whisper") or {}
        model = str(sub.get("model") or "small")
        problems: list[str] = []
        notes: list[str] = []

        try:
            import faster_whisper  # noqa: F401
        except Exception as exc:
            problems.append(
                f"faster-whisper is not installed ({exc}); "
                "install it with `pip install -e .[asr-local]` or change asr.provider"
            )
            return problems, notes

        download_root = sub.get("download_root") or None
        search_roots = [Path(download_root)] if download_root else []
        search_roots += [
            Path.home() / ".cache" / "huggingface" / "hub",
            Path.home() / ".cache" / "whisper",
        ]
        cached = any(
            (root / f"models--Systran--faster-whisper-{model}").exists()
            or (root / f"faster-whisper-{model}").exists()
            for root in search_roots
            if root is not None
        )
        if not cached:
            notes.append(
                f"faster-whisper model {model!r} is not cached locally; "
                f"`nbva live` will download it on first use (~{_approx_model_mb(model)} MB)"
            )
        return problems, notes

    def transcribe(self, audio: np.ndarray, sample_rate: int | None = None) -> ASRResult:
        started = time.perf_counter()
        src_rate = sample_rate or self.sample_rate
        data = np.asarray(audio, dtype=np.float32)
        if src_rate != self.sample_rate:
            data = resample(data, src_rate, self.sample_rate)

        if data.size == 0:
            return ASRResult(
                text="", provider=self.name, model=self.model_name,
                duration_ms=0.0, error="empty audio",
            )

        model = self._ensure_model()
        transcribe_kwargs: dict[str, Any] = {
            "language": self.language or None,
            "task": self.task,
            "beam_size": self.beam_size,
            "vad_filter": self.vad_filter,
            "condition_on_previous_text": self.condition_on_previous_text,
        }
        # A decoding prime (e.g. mixed Arabic/English restaurant speech)
        # biases whisper toward the expected language mix. Off by default.
        if self.initial_prompt:
            transcribe_kwargs["initial_prompt"] = self.initial_prompt
        segments, info = model.transcribe(data, **transcribe_kwargs)
        parts = [seg.text for seg in segments]
        text = "".join(parts).strip()
        return ASRResult(
            text=text,
            language=getattr(info, "language", self.language),
            provider=self.name,
            model=self.model_name,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            raw={"language_probability": float(getattr(info, "language_probability", 0.0) or 0.0),
                 "segments": len(parts)},
        )

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model_name,
            "device": self.device,
            "compute_type": self.compute_type,
            "language": self.language,
            "task": self.task,
            "beam_size": self.beam_size,
            "vad_filter": self.vad_filter,
            "condition_on_previous_text": self.condition_on_previous_text,
            "initial_prompt": self.initial_prompt,
            "sample_rate": self.sample_rate,
        }


class OpenAIWhisperProvider:
    """Hosted Whisper endpoint. Needs OPENAI_API_KEY."""

    name = "openai"

    def __init__(
        self,
        model: str = "whisper-1",
        api_key_env: str = "OPENAI_API_KEY",
        language: str = "ar",
        temperature: float = 0.0,
        prompt: str | None = None,
        sample_rate: int = 16000,
        timeout: float = 60.0,
    ):
        import os

        self.model = model
        self.language = language
        self.temperature = temperature
        self.prompt = prompt
        self.sample_rate = sample_rate
        self.timeout = timeout
        key = os.environ.get(api_key_env, "")
        if not key:
            raise RuntimeError(
                f"{api_key_env} is not set. Use asr.provider=faster_whisper for local "
                f"recognition without a key."
            )
        from openai import OpenAI

        self.client = OpenAI(api_key=key, timeout=timeout, max_retries=0)

    def transcribe(self, audio: np.ndarray, sample_rate: int | None = None) -> ASRResult:
        import io
        import wave as _wave

        started = time.perf_counter()
        src_rate = sample_rate or self.sample_rate
        data = np.asarray(audio, dtype=np.float32)
        if src_rate != self.sample_rate:
            data = resample(data, src_rate, self.sample_rate)
        pcm = np.clip(data, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype(np.int16)

        buf = io.BytesIO()
        with _wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm.tobytes())
        buf.seek(0)

        resp = self.client.audio.transcriptions.create(
            model=self.model,
            file=("utterance.wav", buf, "audio/wav"),
            language=self.language or None,
            temperature=self.temperature,
            prompt=self.prompt,
        )
        return ASRResult(
            text=(getattr(resp, "text", "") or "").strip(),
            # Surface the detected language when the service reports one;
            # fall back to the configured hint (may be "" for auto-detect).
            language=getattr(resp, "language", None) or self.language,
            provider=self.name,
            model=self.model,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "language": self.language,
            "temperature": self.temperature,
            "sample_rate": self.sample_rate,
        }


class ManualProvider:
    """Typed text instead of audio. Development only; never a valid trial."""

    name = "manual"

    def __init__(self, prompt_text: str = "Type what you would say:", sample_rate: int = 16000,
                 model: str = "manual"):
        self.prompt_text = prompt_text
        self.sample_rate = sample_rate
        self.model = model

    def transcribe(self, audio: np.ndarray, sample_rate: int | None = None) -> ASRResult:
        text = input(self.prompt_text).strip()
        return ASRResult(text=text, provider=self.name, model=self.model)

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "sample_rate": self.sample_rate,
            "research_valid": False,
        }


class ReferenceEchoProvider:
    """Returns a caller-supplied reference transcript. Testing harness only."""

    name = "reference_echo"

    def __init__(self, sample_rate: int = 16000, model: str = "reference_echo"):
        self.sample_rate = sample_rate
        self.model = model
        self.next_text: str = ""

    def set_next(self, text: str) -> None:
        self.next_text = text

    def transcribe(self, audio: np.ndarray, sample_rate: int | None = None) -> ASRResult:
        return ASRResult(text=self.next_text, provider=self.name, model=self.model)

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "sample_rate": self.sample_rate,
            "research_valid": False,
        }


PROVIDERS = {
    "faster_whisper": FasterWhisperProvider,
    "openai": OpenAIWhisperProvider,
    "manual": ManualProvider,
    "reference_echo": ReferenceEchoProvider,
}


def build_asr(config):
    provider_name = str(config.get("asr.provider", "faster_whisper")).strip().lower()
    if provider_name not in PROVIDERS:
        raise ValueError(f"unknown asr.provider {provider_name!r}; available: {sorted(PROVIDERS)}")
    section = config.section("asr") or {}
    kwargs: dict[str, Any] = {
        "sample_rate": int(section.get("sample_rate", 16000)),
    }
    # Only the providers that actually recognise a language take the setting.
    # Forcing it onto every provider breaks the offline ones (manual typing,
    # reference echo), which take no language at all.
    if "language" in inspect.signature(PROVIDERS[provider_name].__init__).parameters:
        kwargs["language"] = section.get("language", "ar")
    sub = section.get(provider_name)
    if isinstance(sub, dict):
        accepted = inspect.signature(PROVIDERS[provider_name].__init__).parameters
        kwargs.update(
            {
                k: v
                for k, v in sub.items()
                if (k in accepted and (v is not None or k == "prompt"))
            }
        )
    if provider_name == "faster_whisper":
        kwargs.setdefault("model", "small")
    elif provider_name == "openai":
        kwargs.setdefault("model", "whisper-1")
    return PROVIDERS[provider_name](**kwargs)


def _asr_preflight(config) -> tuple[list[str], list[str]]:
    """Dispatch preflight to the configured provider, if it implements one."""
    provider_name = str(config.get("asr.provider", "faster_whisper")).strip().lower()
    provider = PROVIDERS.get(provider_name)
    hook = getattr(provider, "preflight", None)
    if hook is None:
        return [], []
    return hook(config)


# Exposed as an attribute so callers can probe a provider without building one.
build_asr.preflight = _asr_preflight  # type: ignore[attr-defined]
