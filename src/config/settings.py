"""
Application configuration.

All environment variables are loaded and validated here.
Import `settings` from this module rather than reading os.environ directly.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Anthropic ---
    anthropic_api_key: str = Field(..., description="Claude API key")
    claude_model_bulk: str = Field(
        default="claude-sonnet-4-6",
        description="Model used for bulk claim extraction and classification",
    )
    claude_model_synthesis: str = Field(
        default="claude-opus-4-7",
        description="Model used for final synthesis and briefing generation",
    )

    # --- OpenAI ---
    openai_api_key: str = Field(..., description="OpenAI key, used for embeddings only")
    embedding_model: str = Field(default="text-embedding-3-small")

    # --- Database ---
    supabase_url: str = Field(...)
    supabase_key: str = Field(...)
    database_url: str = Field(...)

    # --- External APIs ---
    youtube_api_key: str = Field(...)
    alpha_vantage_key: str = Field(default="")
    fred_api_key: str = Field(default="")

    # --- App ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    environment: Literal["development", "staging", "production"] = Field(
        default="development"
    )

    # --- Optional ---
    sentry_dsn: str = Field(default="")


@lru_cache
def get_settings() -> Settings:
    """Return cached settings. Call this rather than instantiating Settings directly."""
    return Settings()  # type: ignore[call-arg]


# Convenience export — import this in modules
settings = get_settings()
