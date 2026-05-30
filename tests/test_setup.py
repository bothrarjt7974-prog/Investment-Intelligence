"""
Sanity-check tests to verify the project is set up correctly.

Run after initial setup:
    pytest tests/test_setup.py
"""

from __future__ import annotations

from pathlib import Path

import anthropic
import pytest


def test_required_files_exist():
    """Verify all critical project files are present."""
    root = Path(__file__).parent.parent
    required = [
        "CLAUDE.md",
        ".env.example",
        ".gitignore",
        "requirements.txt",
        "pyproject.toml",
        "README.md",
        "src/config/settings.py",
        "src/llm/claude_client.py",
        "src/db/queries.py",
        "src/ingestion/youtube.py",
        "src/ingestion/run_all.py",
        "prompts/claim_extraction_v2.md",
        "prompts/quality_filter_v1.md",
        "migrations/001_initial_schema.sql",
        "scripts/seed_experts.py",
        "scripts/run_migrations.py",
    ]
    missing = [p for p in required if not (root / p).exists()]
    assert not missing, f"Missing required files: {missing}"


def test_env_loaded():
    """Verify .env can be loaded and required variables are set."""
    try:
        from src.config.settings import settings

        assert settings.anthropic_api_key, "ANTHROPIC_API_KEY not set"
        assert settings.openai_api_key, "OPENAI_API_KEY not set"
        assert settings.database_url, "DATABASE_URL not set"
        assert settings.youtube_api_key, "YOUTUBE_API_KEY not set"
    except Exception as e:
        pytest.fail(f"Settings could not load: {e}. Did you copy .env.example to .env?")


def test_prompts_have_headers():
    """Every prompt file must have a header comment block."""
    root = Path(__file__).parent.parent
    prompts_dir = root / "prompts"
    for prompt_file in prompts_dir.glob("*_v*.md"):
        content = prompt_file.read_text()
        assert "Purpose:" in content, f"{prompt_file.name} missing Purpose header"
        assert "Used by:" in content, f"{prompt_file.name} missing Used by header"
        assert "Version" in content, f"{prompt_file.name} missing Version header"


@pytest.mark.integration
def test_claude_api_reachable():
    """Integration test — verifies Claude API key works. Skip in CI."""
    from src.llm.claude_client import claude

    # Make a minimal call. This uses real API credits.
    try:
        response = claude.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=20,
            messages=[{"role": "user", "content": "Say 'ok'"}],
        )
    except anthropic.BadRequestError as exc:
        message = str(exc).lower()
        if "credit balance" in message or "insufficient credits" in message:
            pytest.skip("Skipping API integration test because Anthropic account has insufficient credits.")
        raise

    assert response.content, "No response from Claude API"
