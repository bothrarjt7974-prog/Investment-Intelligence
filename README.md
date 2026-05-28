# Investment Intelligence Layer

A synthesized, validated intelligence layer over credible public expert
content on financial markets. Built to help sophisticated investors
understand markets at institutional depth without spending hours daily
consuming expert content.

> **This product produces understanding, not advice.** Output is curated
> intelligence with source accountability, not buy/sell recommendations.

## Status

Pre-MVP. Active development.

## Architecture

Six layers, built in sequence:

1. **Ingestion** — YouTube, blogs, podcasts → Postgres
2. **Extraction** — Claude API extracts structured claims
3. **Quality filter** — 5-dimension screening, aggressive rejection
4. **Validation** — daily verification against real market data
5. **Synthesis** — convergence detection across reasoning chains
6. **Output** — morning briefing + query interface

See [`docs/architecture.md`](docs/architecture.md) for detail.

## Local development

```bash
# Clone and enter
git clone <repo-url>
cd investment-intelligence

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Run migrations against your Supabase Postgres
python -m scripts.run_migrations

# Test the setup
pytest tests/test_setup.py
```

## Daily workflow

```bash
# Manually trigger ingestion
python -m src.ingestion.run_all

# Generate today's morning briefing
python -m src.synthesis.briefing

# Launch the UI
streamlit run app.py
```

## Project layout

```
investment-intelligence/
├── CLAUDE.md              # Project context for Claude Code
├── src/
│   ├── ingestion/         # Content scrapers (YouTube, blogs, podcasts)
│   ├── extraction/        # Claim extraction via Claude API
│   ├── quality/           # 5-dimension quality filter
│   ├── validation/        # Daily validation against market data
│   ├── synthesis/         # Convergence detection + briefing generation
│   ├── db/                # Database access layer
│   ├── llm/               # Claude / OpenAI clients
│   ├── config/            # Settings and configuration
│   └── internal/          # Proprietary scoring logic (not for sharing)
├── prompts/               # Versioned Claude prompts (one .md per prompt)
├── migrations/            # SQL migrations, sequentially numbered
├── tests/                 # Pytest tests
├── scripts/               # One-off scripts and utilities
├── docs/                  # Architecture, decision log, etc.
└── .github/workflows/     # GitHub Actions cron jobs
```

## Conventions

- Type hints on every function
- Docstrings explain WHY, not WHAT
- All prompts versioned as `.md` files in `prompts/`
- Decision log entries for any meaningful product choice (see `docs/decision_log.md`)
- One feature per branch
- `pytest` must pass before commit

## License

Proprietary. All rights reserved.
