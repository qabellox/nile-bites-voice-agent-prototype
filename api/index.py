"""Vercel entry point for the Nile Bites phone prototype."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nbva.config.loader import load_config
from nbva.phone import create_phone_app


config = load_config()
config.set("paths.log_dir", "/tmp/nbva-logs")
config.set("logging.audio_dir", "/tmp/nbva-audio")
if os.environ.get("OPENAI_MODEL"):
    config.set("llm.model", os.environ["OPENAI_MODEL"])
if os.environ.get("OPENAI_BASE_URL"):
    config.set("llm.base_url", os.environ["OPENAI_BASE_URL"])
    config.set("llm.openai_compatible.base_url", os.environ["OPENAI_BASE_URL"])
if os.environ.get("OPENAI_API_KEY"):
    config.set("llm.openai_compatible.api_key_env", "OPENAI_API_KEY")
    config.set("llm.openai_compatible.api_key_fallback", "")
app = create_phone_app(
    config=config,
    memory_path=Path(
        os.environ.get("NBVA_PHONE_MEMORY", "/tmp/nbva-phone-memory.sqlite3")
    ),
)
