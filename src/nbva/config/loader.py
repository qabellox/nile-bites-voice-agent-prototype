"""Layered YAML configuration with dotted access and environment overrides.

Resolution order (later wins):

1. Packaged defaults live in ``config/*.yaml`` next to the project root.
2. ``NBVA_CONFIG`` environment variable may point at a single override file.
3. ``--config a.yaml --config b.yaml`` files passed on the command line, in order.

Any leaf can be overridden from the environment using the ``NBVA_`` prefix with
``__`` as the nesting separator, e.g. ``NBVA_LLM__PROVIDER=ollama``. This lets the
experiment harness vary model or ASR settings without editing files.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Iterator

import yaml

ENV_PREFIX = "NBVA_"
NESTING_SEP = "__"


def project_root() -> Path:
    """Repository root (the folder holding ``config/`` and ``data/``)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config").is_dir() and (parent / "data").is_dir():
            return parent
    return here.parents[3]


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _coerce(raw: str) -> Any:
    lowered = raw.strip().lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none", ""}:
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    if (raw.startswith("[") and raw.endswith("]")) or (raw.startswith("{") and raw.endswith("}")):
        try:
            return yaml.safe_load(raw)
        except yaml.YAMLError:
            pass
    return raw


def _apply_env_overrides(cfg: dict) -> dict:
    for env_key, env_value in os.environ.items():
        if not env_key.startswith(ENV_PREFIX) or env_key == "NBVA_CONFIG":
            continue
        path = env_key[len(ENV_PREFIX):].lower().split(NESTING_SEP)
        cursor = cfg
        for part in path[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[path[-1]] = _coerce(env_value)
    return cfg


class Config:
    """Read-mostly configuration object with dotted lookup."""

    def __init__(self, data: dict, sources: list[Path] | None = None):
        self._data = data
        self.sources = sources or []

    # -- access ----------------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        cursor: Any = self._data
        for part in dotted.split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                return default
            cursor = cursor[part]
        return copy.deepcopy(cursor)

    def require(self, dotted: str) -> Any:
        sentinel = object()
        value = self.get(dotted, sentinel)
        if value is sentinel:
            raise KeyError(f"missing required config key: {dotted}")
        return value

    def section(self, dotted: str) -> dict:
        value = self.get(dotted, {})
        if not isinstance(value, dict):
            raise TypeError(f"config key {dotted} is not a section")
        return value

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        cursor = self._data
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value

    def __contains__(self, dotted: str) -> bool:
        sentinel = object()
        return self.get(dotted, sentinel) is not sentinel

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def to_dict(self) -> dict:
        return copy.deepcopy(self._data)

    def dump_yaml(self) -> str:
        return yaml.safe_dump(self._data, sort_keys=False, allow_unicode=True)

    # -- paths -----------------------------------------------------------
    def resolve_path(self, dotted: str, default: str | None = None) -> Path:
        raw = self.get(dotted, default)
        if raw is None:
            raise KeyError(f"no path configured at {dotted}")
        p = Path(str(raw))
        if p.is_absolute():
            return p
        return (project_root() / p).resolve()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Config(sources={[str(s) for s in self.sources]})"


def load_config(extra_files: list[str | Path] | None = None) -> Config:
    """Load base config files, then overlay any extra files, then env vars."""
    root = project_root()
    config_dir = root / "config"
    base_files: list[Path] = []
    if config_dir.is_dir():
        base_files = sorted(config_dir.glob("*.yaml"))

    data: dict = {}
    sources: list[Path] = []
    for f in base_files:
        loaded = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise TypeError(f"config file {f} must contain a mapping at top level")
        data = _deep_merge(data, loaded)
        sources.append(f)

    env_file = os.environ.get("NBVA_CONFIG")
    candidates: list[Path] = []
    if env_file:
        candidates.extend(Path(p) for p in env_file.split(os.pathsep) if p)
    if extra_files:
        candidates.extend(Path(p) for p in extra_files)

    for f in candidates:
        if not f.exists():
            raise FileNotFoundError(f"config file not found: {f}")
        loaded = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        data = _deep_merge(data, loaded)
        sources.append(f)

    data = _apply_env_overrides(data)
    return Config(data, sources)
