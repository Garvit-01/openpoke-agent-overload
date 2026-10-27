"""Simplified configuration management."""

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field


def _load_env_file() -> None:
    """Load .env from root directory if present."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.is_file():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, value = stripped.split("=", 1)
                key, value = key.strip(), value.strip().strip("'\"")
                if key and value and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


_load_env_file()


DEFAULT_APP_NAME = "OpenPoke Server"
DEFAULT_APP_VERSION = "0.3.0"


def _env_int(name: str, fallback: int) -> int:
    try:
        return int(os.getenv(name, str(fallback)))
    except (TypeError, ValueError):
        return fallback


class Settings(BaseModel):
    """Application settings with lightweight env fallbacks."""

    # App metadata
    app_name: str = Field(default=DEFAULT_APP_NAME)
    app_version: str = Field(default=DEFAULT_APP_VERSION)

    # Server runtime
    server_host: str = Field(default=os.getenv("OPENPOKE_HOST", "0.0.0.0"))
    server_port: int = Field(default=_env_int("OPENPOKE_PORT", 8001))

    # LLM model selection (each role can be overridden, e.g. a smaller model for the classifier)
    interaction_agent_model: str = Field(default=os.getenv("OPENPOKE_INTERACTION_MODEL", "anthropic/claude-sonnet-5.5"))
    execution_agent_model: str = Field(default=os.getenv("OPENPOKE_EXECUTION_MODEL", "anthropic/claude-sonnet-5.5"))
    execution_agent_search_model: str = Field(default=os.getenv("OPENPOKE_SEARCH_MODEL", "anthropic/claude-sonnet-5.5"))
    summarizer_model: str = Field(default=os.getenv("OPENPOKE_SUMMARIZER_MODEL", "anthropic/claude-sonnet-5.5"))
    email_classifier_model: str = Field(default=os.getenv("OPENPOKE_CLASSIFIER_MODEL", "anthropic/claude-sonnet-5.5"))

    # Agent routing: how many agents the interaction agent sees per turn (fixed, not the whole roster)
    agent_shortlist_retrieved: int = Field(default=_env_int("OPENPOKE_AGENT_SHORTLIST", 8))
    agent_shortlist_recent: int = Field(default=_env_int("OPENPOKE_AGENT_RECENT", 4))
    agent_summary_chars: int = Field(default=_env_int("OPENPOKE_AGENT_SUMMARY_CHARS", 200))

    # Agent lifecycle (idle days before an agent is demoted; agents with active triggers are never demoted)
    agent_dormant_after_days: int = Field(default=_env_int("OPENPOKE_AGENT_DORMANT_DAYS", 14))
    agent_archive_after_days: int = Field(default=_env_int("OPENPOKE_AGENT_ARCHIVE_DAYS", 45))

    # How many past requests an execution agent sees from its own log (0 = all, the old behaviour)
    execution_agent_history_limit: int = Field(default=_env_int("OPENPOKE_EXECUTION_HISTORY_LIMIT", 10))

    # Outbound email guard: "enforce" blocks sends the user has not seen as a draft, "off" disables it
    outbound_guard_mode: str = Field(default=os.getenv("OPENPOKE_OUTBOUND_GUARD", "enforce"))

    # Credentials / integrations
    openrouter_api_key: Optional[str] = Field(default=os.getenv("OPENROUTER_API_KEY"))
    composio_gmail_auth_config_id: Optional[str] = Field(default=os.getenv("COMPOSIO_GMAIL_AUTH_CONFIG_ID"))
    composio_api_key: Optional[str] = Field(default=os.getenv("COMPOSIO_API_KEY"))

    # HTTP behaviour
    cors_allow_origins_raw: str = Field(default=os.getenv("OPENPOKE_CORS_ALLOW_ORIGINS", "*"))
    enable_docs: bool = Field(default=os.getenv("OPENPOKE_ENABLE_DOCS", "1") != "0")
    docs_url: Optional[str] = Field(default=os.getenv("OPENPOKE_DOCS_URL", "/docs"))

    # Summarisation controls
    conversation_summary_threshold: int = Field(default=100)
    conversation_summary_tail_size: int = Field(default=10)

    @property
    def cors_allow_origins(self) -> List[str]:
        """Parse CORS origins from comma-separated string."""
        if self.cors_allow_origins_raw.strip() in {"", "*"}:
            return ["*"]
        return [origin.strip() for origin in self.cors_allow_origins_raw.split(",") if origin.strip()]

    @property
    def resolved_docs_url(self) -> Optional[str]:
        """Return documentation URL when docs are enabled."""
        return (self.docs_url or "/docs") if self.enable_docs else None

    @property
    def summarization_enabled(self) -> bool:
        """Flag indicating conversation summarisation is active."""
        return self.conversation_summary_threshold > 0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
