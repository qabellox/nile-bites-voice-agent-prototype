"""Application factory.

One place builds the whole research instrument from configuration. Everything
downstream of :class:`AgentApp` is shared by the live voice loop, the CLI, the
tests and any future batch runner, so there is a single assembly point to change
when the intervention replaces a stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..asr.providers import build_asr
from ..config.loader import Config, load_config, project_root
from ..db.repository import RestaurantRepository
from ..llm.providers import build_provider
from ..obs.logger import SessionLogger, config_fingerprint, new_session_id
from ..research.codeswitch import build_analyzer
from ..research.conditions import ConditionSet, load_conditions
from ..research.wer import wer_config_from_config
from ..tts.providers import build_tts
from .pipeline import BaselinePipeline
from .prompts import load_system_prompt
from .session import Session, create_session

DEFAULT_PROMPTS = {
    "llm": "You are the voice assistant for Nile Bites, a fictional Egyptian restaurant. "
    "You may only state facts returned to you by tools. Speak briefly, in the same "
    "language mixture the customer used.",
}


@dataclass
class AgentApp:
    config: Config
    repository: RestaurantRepository
    session: Session
    pipeline: BaselinePipeline
    logger: SessionLogger
    asr: Any
    llm: Any
    tts: Any
    conditions: ConditionSet
    player: Any = None

    # -- reporting --------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        return {
            "experiment_id": self.session.experiment_id,
            "is_research_run": self.session.is_research_run,
            "participant_id": self.session.participant_id,
            "session_id": self.session.session_id,
            "business_weekday": self.session.business_weekday,
            "restaurant_dataset": {
                "dataset_id": self.repository.data.meta.dataset_id,
                "data_version": self.repository.data.meta.data_version,
                "is_fictional": self.repository.data.meta.IS_FICTIONAL,
                "menu_items": len(self.repository.menu),
            },
            "asr": self.asr.describe(),
            "llm": self.llm.describe(),
            "tts": self.tts.describe(),
            "conditions": self.conditions.to_dict(),
            "log_file": str(self.logger.path),
            "config_fingerprint": self.logger.config_fingerprint,
        }


def build_app(
    config: Config | None = None,
    overrides: dict[str, Any] | None = None,
    session_id: str | None = None,
    attach_player: bool = True,
    logger: SessionLogger | None = None,
) -> AgentApp:
    config = config or load_config()
    if overrides:
        for dotted, value in overrides.items():
            config.set(dotted, value)

    db_path = config.resolve_path("paths.restaurant_db", "data/restaurant/nile_bites.json")
    repository = RestaurantRepository.from_path(db_path)

    experiment_id = str(config.get("experiment.experiment_id", "unassigned"))
    is_research_run = bool(config.get("experiment.is_research_run", False))
    participant_id = str(config.get("experiment.participant_id", "") or "").strip()

    # Weekday used by promotions and hours. Configurable so an order total does
    # not drift with the calendar. "auto" means the real current weekday.
    business_weekday = str(config.get("experiment.business_weekday", "auto")).strip().lower()
    if business_weekday in ("", "auto", "today", "none"):
        import datetime as _dt

        business_weekday = _dt.datetime.now().strftime("%A").lower()

    session = create_session(
        repository=repository,
        experiment_id=experiment_id,
        is_research_run=is_research_run,
        participant_id=participant_id,
        business_weekday=business_weekday,
        session_id=session_id or new_session_id("s"),
    )

    asr = build_asr(config)
    llm = build_provider(config)
    tts = build_tts(config)

    analyzer = build_analyzer(config, repository)
    conditions = load_conditions(config)
    system_prompt = load_system_prompt(config, DEFAULT_PROMPTS["llm"])

    player = None
    if attach_player and str(config.get("tts.provider", "null")) != "null":
        from ..audio.io import AudioPlayer

        out_section = config.section("audio.output") or {}
        player = AudioPlayer(
            sample_rate=out_section.get("sample_rate"),
            device=out_section.get("device"),
            latency=str(out_section.get("latency", "low")),
        )

    if logger is None:
        log_dir = config.resolve_path("paths.log_dir", "logs")
        log_cfg = config.section("logging") or {}
        logger = SessionLogger(
            session_id=session.session_id,
            experiment_id=experiment_id,
            log_dir=log_dir,
            is_research_run=is_research_run,
            participant_id=participant_id,
            echo_to_console=bool(log_cfg.get("echo_to_console", True)),
            console_level=str(log_cfg.get("console_level", "info")),
            save_audio=bool(log_cfg.get("save_audio", True)),
            audio_dir=config.resolve_path("logging.audio_dir", "logs/audio")
            if log_cfg.get("save_audio", True)
            else None,
            config_fingerprint=config_fingerprint(config),
            write_manifest=bool(log_cfg.get("write_run_manifest", True)),
        )

    pipeline = BaselinePipeline(
        session=session,
        asr=asr,
        llm=llm,
        tts=tts,
        system_prompt=system_prompt,
        analyzer=analyzer,
        conditions=conditions,
        wer_config=wer_config_from_config(config),
        enforce_grounding=bool(config.get("tools.enforce_grounding", True)),
        max_tool_iterations=int(config.get("llm.max_tool_iterations", 6)),
        player=player,
        logger=logger,
        save_audio=bool((config.section("logging") or {}).get("save_audio", True)),
        audio_dir=config.resolve_path("logging.audio_dir", "logs/audio")
        if (config.section("logging") or {}).get("save_audio", True)
        else None,
    )

    return AgentApp(
        config=config,
        repository=repository,
        session=session,
        pipeline=pipeline,
        logger=logger,
        asr=asr,
        llm=llm,
        tts=tts,
        conditions=conditions,
        player=player,
    )
