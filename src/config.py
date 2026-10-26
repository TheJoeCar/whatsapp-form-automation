"""Configuration loading (config.yaml + .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class LLMConfig:
    provider: str = "openrouter"
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = "openrouter/openrouter/free"
    api_key_env: str = "OPENROUTER_API_KEY"
    temperature: float = 0.2
    max_retries: int = 3

    def resolve_api_key(self) -> str | None:
        """Read the API key from the configured environment variable."""
        key = os.environ.get(self.api_key_env)
        return key or None


@dataclass
class WhatsAppConfig:
    channel: str = "My Channel"
    poll_interval_seconds: int = 60
    session_dir: Path = PROJECT_ROOT / "data" / "whatsapp_session"
    headless: bool = True
    max_messages_per_poll: int = 25
    selectors: dict = field(default_factory=dict)


@dataclass
class FormsConfig:
    identity_file: Path = PROJECT_ROOT / "requirements.yaml"
    test_data: dict = field(default_factory=dict)
    use_llm_fallback: bool = True


@dataclass
class PipelineConfig:
    state_file: Path = PROJECT_ROOT / "state" / "processed.json"
    log_file: Path = PROJECT_ROOT / "logs" / "pipeline.log"
    dry_run: bool = True


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    whatsapp: WhatsAppConfig = field(default_factory=WhatsAppConfig)
    forms: FormsConfig = field(default_factory=FormsConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)


def _make_path(root: Path, value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (root / p)


def load_config(path: Path | None = None) -> Config:
    """Load config.yaml (defaults used for anything missing) and .env secrets."""
    load_dotenv(PROJECT_ROOT / ".env")
    cfg_path = path or PROJECT_ROOT / "config.yaml"
    raw: dict = {}
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

    llm_raw = raw.get("llm") or {}
    llm = LLMConfig(
        provider=llm_raw.get("provider", LLMConfig.provider),
        base_url=llm_raw.get("base_url", LLMConfig.base_url),
        model=llm_raw.get("model", LLMConfig.model),
        api_key_env=llm_raw.get("api_key_env", LLMConfig.api_key_env),
        temperature=float(llm_raw.get("temperature", LLMConfig.temperature)),
        max_retries=int(llm_raw.get("max_retries", LLMConfig.max_retries)),
    )

    wa_raw = raw.get("whatsapp") or {}
    whatsapp = WhatsAppConfig(
        channel=wa_raw.get("channel", WhatsAppConfig.channel),
        poll_interval_seconds=int(wa_raw.get("poll_interval_seconds", WhatsAppConfig.poll_interval_seconds)),
        session_dir=_make_path(PROJECT_ROOT, wa_raw.get("session_dir", WhatsAppConfig.session_dir)),
        headless=bool(wa_raw.get("headless", WhatsAppConfig.headless)),
        max_messages_per_poll=int(wa_raw.get("max_messages_per_poll", WhatsAppConfig.max_messages_per_poll)),
        selectors=wa_raw.get("selectors") or {},
    )

    forms_raw = raw.get("forms") or {}
    forms = FormsConfig(
        identity_file=_make_path(PROJECT_ROOT, forms_raw.get("identity_file", FormsConfig.identity_file)),
        test_data=forms_raw.get("test_data") or {},
        use_llm_fallback=bool(forms_raw.get("use_llm_fallback", FormsConfig.use_llm_fallback)),
    )

    pl_raw = raw.get("pipeline") or {}
    pipeline = PipelineConfig(
        state_file=_make_path(PROJECT_ROOT, pl_raw.get("state_file", PipelineConfig.state_file)),
        log_file=_make_path(PROJECT_ROOT, pl_raw.get("log_file", PipelineConfig.log_file)),
        dry_run=bool(pl_raw.get("dry_run", PipelineConfig.dry_run)),
    )

    return Config(llm=llm, whatsapp=whatsapp, forms=forms, pipeline=pipeline)