"""Vercel entry point for the Nile Bites phone prototype."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nbva.config.loader import load_config
from nbva.phone import create_phone_app

_LOG = logging.getLogger("nbva.phone.deploy")

DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

config = load_config()
config.set("paths.log_dir", "/tmp/nbva-logs")
config.set("logging.audio_dir", "/tmp/nbva-audio")
# A tool-calling turn has to fit the tool-call JSON plus the spoken reply.
# 360 truncated Arabic replies often enough to be worth the extra headroom.
config.set("llm.max_tokens", 500)
config.set("llm.temperature", 0.25)
config.set("llm.timeout_seconds", 35)

# The provider is chosen by environment so switching never needs a code edit.
provider = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
if provider:
    config.set("llm.provider", provider)
provider = str(config.get("llm.provider") or "openai_compatible").strip().lower()

if provider == "google":
    model = (os.environ.get("GOOGLE_MODEL") or "").strip() or str(
        config.get("llm.google.model") or "gemini-2.5-flash"
    )
    if not os.environ.get("GEMINI_API_KEY"):
        _LOG.warning(
            "llm.provider=google but GEMINI_API_KEY is not set; every turn will "
            "fail. Set it in the project environment."
        )
    _LOG.info("llm.provider=google model=%s", model)
else:
    model = os.environ.get("OPENAI_MODEL", "").strip()
    if not model:
        # The model IS the agent's intelligence. Falling back silently to whatever
        # default is in the repo is how the deployed prototype ended up answering
        # worse than the model the owner thought was live. Say it out loud instead.
        _LOG.warning(
            "OPENAI_MODEL is not set; falling back to %r. Set OPENAI_MODEL to the "
            "model you intend to run and check /healthz to confirm it is live.",
            DEFAULT_MODEL,
        )
        model = DEFAULT_MODEL
    if os.environ.get("OPENAI_BASE_URL"):
        config.set("llm.base_url", os.environ["OPENAI_BASE_URL"])
        config.set("llm.openai_compatible.base_url", os.environ["OPENAI_BASE_URL"])
    if os.environ.get("OPENAI_API_KEY"):
        config.set("llm.openai_compatible.api_key_env", "OPENAI_API_KEY")
        config.set("llm.openai_compatible.api_key_fallback", "")
config.set("llm.model", model)
app = create_phone_app(
    config=config,
    memory_path=Path(
        os.environ.get("NBVA_PHONE_MEMORY", "/tmp/nbva-phone-memory.sqlite3")
    ),
)
