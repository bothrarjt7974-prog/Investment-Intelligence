# Investment Intelligence Layer

## Project Purpose
Synthesizes credible public expert thinking on financial markets (YouTube,
blogs, podcasts) into rigorously filtered, validated, source-accountable
intelligence.

**This product produces understanding, not advice.** Never frame output as
specific buy/sell recommendations. Always cite sources and credibility scores.

## Architecture — 6 layers, build in this order

1. **Ingestion** (`src/ingestion/`) — content scrapers per source type, store to Postgres
2. **Extraction** (`src/extraction/`) — Claude API extracts structured claims as JSON
3. **Quality filter** (`src/quality/`) — 5-dimension screening, rejects 60–70% of claims
4. **Validation** (`src/validation/`) — daily cron, market data verification, credibility scoring
5. **Synthesis** (`src/synthesis/`) — convergence detection + briefing generation
6. **Output** — morning briefing + query interface (built last, on top of layers 1–5)

## Tech Stack

- **Language:** Python 3.11+
- **LLM:** Anthropic Claude API (Sonnet for bulk extraction, Opus for synthesis)
- **DB:** Postgres via Supabase (free tier OK for MVP)
- **Vector store:** Chroma (local, MVP) → Pinecone later
- **Embeddings:** OpenAI `text-embedding-3-small`
- **Scheduler:** GitHub Actions cron
- **UI:** Streamlit (early), Next.js (later)
- **Hosting:** Railway or Render

## Code Style

- Type hints on every function signature
- Docstrings explain WHY, not just WHAT
- All Claude API calls route through `src/llm/claude_client.py`
- All DB access routes through `src/db/queries.py`
- All configuration via environment variables, loaded in `src/config/settings.py`
- Never commit secrets — use `.env` (gitignored)
- Run `pytest` before every commit

## Project Conventions

- One feature per branch: `feat/<component>` or `fix/<bug>`
- All Claude prompts live in `prompts/` as versioned `.md` files
- Prompt changes require rationale in the file header
- Database migrations are sequentially numbered in `migrations/`
- Manual review every 10th claim during first 8 weeks (calibrate the quality filter)

## Key Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Then fill in API keys

# Run the ingestion pipeline manually
python -m src.ingestion.run_all

# Run the daily validation job
python -m src.validation.daily_job

# Generate today's briefing
python -m src.synthesis.briefing

# Start the Streamlit UI
streamlit run app.py

# Run tests
pytest

# Run linting
ruff check src/
```

## Things to NEVER do

- Never expose the credibility scoring formula in shared code — keep proprietary
  logic in `src/internal/` which is excluded from any open code sharing
- Never auto-generate prompts — they require careful crafting and testing
- Never have the product issue direct buy/sell recommendations
- Never train on user data
- Never store claims that failed quality filter as if they passed
- Never commit `.env` or any file containing API keys

## Decision Log

Capture meaningful product decisions in `docs/decision_log.md`. Required for:
- Adding/removing experts
- Changing quality filter thresholds
- Modifying credibility scoring weights
- Changing synthesis output format
- Pricing or packaging decisions

## Current MVP Scope

- 10 experts (7 Indian, 3 US)
- 3 content types: YouTube, blogs, podcasts
- 2 markets: India and US equities only
- 1 user: the founder

Do not expand scope until MVP is operating reliably.
