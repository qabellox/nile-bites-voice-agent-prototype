"""Run the Nile Bites prototype as a local HTTP/Twilio webhook server.

Local demo:
    python scripts/phone_server.py
    curl -X POST http://127.0.0.1:5000/api/chat ^
      -H "Content-Type: application/json" ^
      -d "{\"call_id\":\"demo\",\"caller_id\":\"demo\",\"text\":\"عندكم برجر؟\"}"

For a phone demo, expose this server through HTTPS (for example with ngrok),
then configure the Twilio number's Voice webhook to ``/twilio/voice``.
Twilio posts speech transcripts to ``/twilio/gather``. No credentials are
stored in this repository.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

from nbva.config.loader import load_config
from nbva.phone import create_phone_app


def _load_private_env(path: str) -> None:
    """Load simple KEY=VALUE deployment settings without printing secrets."""
    env_path = Path(path).expanduser()
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser(description="Nile Bites phone prototype")
    parser.add_argument("--host", default=os.getenv("NBVA_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("NBVA_PORT", "5000")))
    parser.add_argument(
        "--memory",
        default=os.getenv("NBVA_PHONE_MEMORY", "logs/phone_memory.sqlite3"),
        help="SQLite path for durable caller conversation memory",
    )
    parser.add_argument("--config", action="append", default=[])
    parser.add_argument("--set", action="append", default=[])
    args = parser.parse_args()

    # Keep credentials outside the repository. The default is the user's
    # private environment file; NBVA_ENV_FILE can point at a deployment secret.
    env_path = os.environ.get("NBVA_ENV_FILE", str(Path.home() / ".env"))
    _load_private_env(env_path)
    load_dotenv(env_path, override=True)
    config = load_config(args.config)
    if os.environ.get("OPENAI_MODEL"):
        config.set("llm.model", os.environ["OPENAI_MODEL"])
    if os.environ.get("OPENAI_BASE_URL"):
        config.set("llm.base_url", os.environ["OPENAI_BASE_URL"])
        config.set("llm.openai_compatible.base_url", os.environ["OPENAI_BASE_URL"])
    if os.environ.get("OPENAI_API_KEY"):
        config.set("llm.openai_compatible.api_key_env", "OPENAI_API_KEY")
        config.set("llm.openai_compatible.api_key_fallback", "")
    for item in args.set:
        key, value = item.split("=", 1)
        config.set(key.strip(), value)
    app = create_phone_app(config=config, memory_path=args.memory)
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
