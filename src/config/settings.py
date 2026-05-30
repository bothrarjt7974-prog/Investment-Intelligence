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
    youtube_proxy_url: str = Field(default="", description="Optional HTTP(S) proxy for YouTube transcript requests")
    youtube_proxy_username: str = Field(default="", description="Optional Webshare-style proxy username")
    youtube_proxy_password: str = Field(default="", description="Optional Webshare-style proxy password")
    youtube_proxy_retries: int = Field(default=5, description="Retry count when YouTube blocks a transcript request")
    youtube_request_delay_seconds: float = Field(
        default=1.0,
        description="Delay between transcript requests to reduce YouTube 429 throttling",
    )
    alpha_vantage_key: str = Field(default="")
    fred_api_key: str = Field(default="")

    # --- App ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    environment: Literal["development", "staging", "production"] = Field(
        default="development"
    )

    # --- Enrichment flags (PHASE TWO — all default OFF in MVP) ---
    # When false, the system uses only cheap YouTube auto-captions.
    # Flip these on in phase two to enable richer (and costlier) capture.
    enable_whisper: bool = Field(
        default=False,
        description="Re-transcribe audio with Whisper instead of using auto-captions",
    )
    enable_visual_extraction: bool = Field(
        default=False,
        description="Extract charts/tables/data from video frames via vision AI",
    )
    # Tier-selection thresholds (used in phase two to decide which content
    # earns expensive enrichment). Ignored while enrichment flags are off.
    whisper_min_expert_score: float = Field(
        default=65.0,
        description="Only Whisper-transcribe content from experts above this score",
    )
    visual_min_expert_score: float = Field(
        default=70.0,
        description="Only visual-extract content from experts above this score",
    )
    visual_min_number_density: int = Field(
        default=8,
        description="Only visual-extract if transcript mentions at least this many numbers",
    )
    # Whisper execution mode: 'api' (pay per minute) or 'local' (run on own GPU)
    whisper_mode: Literal["api", "local"] = Field(default="api")
    # Vision model to use for frame extraction in phase two
    vision_model: str = Field(default="claude-opus-4-7")

    # --- Optional ---
    sentry_dsn: str = Field(default="")


@lru_cache
def get_settings() -> Settings:
    """Return cached settings. Call this rather than instantiating Settings directly."""
    return Settings()  # type: ignore[call-arg]


# Convenience export — import this in modules
settings = get_settings()
