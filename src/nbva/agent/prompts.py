"""System prompt loading.

The baseline system prompt is a CONTROLLED VARIABLE. It is loaded from a file
rather than embedded in code so that it can be reviewed, versioned, and proven
identical across conditions, and so that changing it is a visible act.
"""

from __future__ import annotations

from pathlib import Path


def load_system_prompt(config, fallback: str) -> str:
    raw = config.get("system_prompt.file")
    if raw:
        from ..config.loader import project_root

        p = Path(str(raw))
        if not p.is_absolute():
            p = project_root() / p
        if p.exists():
            text = p.read_text(encoding="utf-8").strip()
            if text:
                return text
    return fallback
