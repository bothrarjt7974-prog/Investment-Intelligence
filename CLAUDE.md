# Investment Intelligence — Long-Term Portfolio Advisory System

## Project Purpose

A SEBI RIA-registered long-term investment advisory system. Monitors credible
financial experts across every medium they publish in (YouTube first; blogs,
podcasts, newsletters, columns later), extracts both their directional CLAIMS
(what will happen) and their investment STRATEGIES (how to invest), validates
both against real outcomes, and produces specific portfolio-level allocation
guidance with full source attribution.

**We are investors, not traders.** Users think in years. Output is
portfolio-level allocation guidance (target weights, execution horizons,
tax notes) — never daily trade calls, technical setups, or F&O advice.
Guidance cadence: weekly digest + event-driven + monthly deep review.
Silence ("no action needed") is a valid and frequent output.

**Full attribution, never invention.** Direction comes from extracted expert
claims. Execution methodology comes from the strategy library (expert-stated,
historical patterns, CFA frameworks). Every guidance component traces to a
verifiable source. The AI is a librarian and matchmaker, not an oracle.

## Architecture — 9 layers, build in this order

1. **Ingestion** (`src/ingestion/`) — 5 source patterns (video transcript,
   article scrape, audio transcription, structured API, manual/PDF), all
   producing source-agnostic `content_pieces` rows. YouTube pattern live.
2. **Dual extraction** (`src/extraction/`) — TWO parallel Claude passes per
   content piece: Track A claims (built, v3 prompt), Track B strategies
   (trigger/allocation/sizing/entry/exit schema — to build).
3. **Normalization** (`src/extraction/normalization.py`) — deterministic
   post-processing: entity canonicalization, within-content dedup,
   target_date computation. Built, 30 passing tests. Code, never LLM.
4. **Quality filters** (`src/quality/`) — Filter A scores claims on 5
   reasoning dimensions (built, v3 calibrating). Filter B scores strategies
   on 5 specificity dimensions (to build). Rejects aggressively.
5. **Validation** (`src/validation/`) — Track A: outcome validation of claims
   against market data when target dates pass. Track B: strategy backtesting
   via trigger vocabulary. Credibility scored per expert, per horizon
   (short/long/structural), per medium.
6. **Structural theme synthesis** (`src/synthesis/`) — long-horizon claims
   (12+ months only) combined across experts into themes, weighted by
   credibility and reasoning independence. Opus.
7. **Fundamentals sanity check** (`src/fundamentals/`) — themes verified
   against valuations, earnings trajectory, balance sheet health before
   they can drive guidance. Data from code, judgment from LLM.
8. **Strategy library + matcher** (`src/strategies/`) — unified asset:
   expert-extracted strategies, historical patterns, CFA frameworks.
   Structured filter → embedding match → credibility/backtest weighting.
9. **Guidance + delivery** (`src/guidance/`, `src/delivery/`) — allocation
   guidance generation (theme + strategy + user portfolio), weekly digest,
   event alerts, monitoring, feedback loop.

## Tech Stack

- **Language:** Python 3.11+
- **LLM:** Anthropic Claude API (Sonnet bulk; Opus for synthesis + guidance)
- **DB:** Postgres via Supabase
- **Vector store:** Chroma (MVP) → Pinecone later
- **Embeddings:** OpenAI `text-embedding-3-small`
- **Market data:** yfinance, nsepython, RBI portal, FRED (EOD only — no
  real-time; our world moves in weeks, not minutes)
- **Article extraction:** trafilatura + feedparser (Pattern 2)
- **Audio:** OpenAI Whisper (Pattern 3, when podcasts activate)
- **Backtesting:** pandas/numpy simulation harness + trigger vocabulary table
- **Scheduler:** GitHub Actions cron
- **UI:** Streamlit (early), Next.js (later)
- **Hosting:** Railway or Render

## Code Style

- Type hints on every function signature
- Docstrings explain WHY, not just WHAT
- All Claude API calls route through `src/llm/claude_client.py`
- All DB access routes through `src/db/queries.py`
- All configuration via environment variables, loaded in `src/config/settings.py`
- LLMs do meaning; code does mechanics. Never ask an LLM to enforce a rule
  deterministic code can enforce (naming, dedup, date math, backtest arithmetic)
- Never commit secrets — use `.env` (gitignored)
- Run `pytest` before every commit

## Project Conventions

- One feature per branch: `feat/<component>` or `fix/<bug>`
- All Claude prompts live in `prompts/` as versioned `.md` files — never
  overwrite a version; save as vN+1 with rationale in the header
- One prompt change at a time; re-measure on the same sample before scaling
- Database migrations sequentially numbered in `migrations/`
- Manual review every 10th claim during first 8 weeks (filter calibration)

## Key Commands

```bash
pip install -r requirements.txt
cp .env.example .env                      # then fill in API keys

python -m src.ingestion.run_all           # ingestion pipeline
python -m src.extraction.extractor        # Track A: claim extraction
python -m src.extraction.strategy_extractor  # Track B: strategies (Phase B)
python -m src.quality.screener --max-claims 20  # quality filter (test run)
python -m src.validation.daily_job        # validation cron
python -m src.synthesis.briefing          # theme synthesis
streamlit run app.py                      # UI
pytest                                    # tests
ruff check src/                           # linting
```

## Things to NEVER do

- Never expose the credibility scoring formula — proprietary logic lives in
  `src/internal/`, excluded from any open code sharing
- Never issue trading calls: no intraday/daily signals, no technical setups,
  no options/F&O guidance. Long-term allocation guidance only.
- Never generate guidance without full attribution (direction source,
  strategy source, fundamentals check) — this is both product principle
  and SEBI record-keeping requirement (5-year retention)
- Never let the LLM invent position sizes, targets, or strategies not
  present in the strategy library or expert claims
- Never auto-generate prompts — they require careful crafting and testing
- Never train on user data
- Never store claims/strategies that failed quality filter as if they passed
  (tag, don't delete; downstream queries filter on quality_verdict = 'pass')
- Never commit `.env` or any file containing API keys
- Never retain downloaded media files — extract, then delete immediately

## Decision Log

Capture meaningful decisions in `docs/decision_log.md`. Required for:
- Adding/removing experts or expert sources
- Changing quality filter thresholds or rubrics (either filter)
- Modifying credibility scoring weights
- Adding trigger vocabulary entries for backtesting
- Changing guidance output format or delivery cadence
- Pricing or packaging decisions

## Current Status & MVP Scope

- **Done:** Ingestion (YouTube), claim extraction v3, normalization (tested),
  claim quality filter (v3 calibrating — the current gate)
- **Next:** Finish filter calibration → full backlog screening → Phase B
  (strategy extraction + expert_sources migration) → validation engine
- **MVP scope:** 1 expert fully processed (Akshat, ~750 pieces), then expand
  to 10 experts (7 Indian, 3 US); YouTube first, blogs/podcasts Phase H;
  India + US equities; founder as user zero, then 5-10 beta users
- Do not expand scope until the current phase operates reliably

## RIA / Compliance Notes

- Founder holds/is completing SEBI RIA registration — specific instrument-
  level allocation guidance is in scope
- Every guidance event AND every refusal is logged with full rationale
  (theme, experts, strategy, fundamentals) — 5-year SEBI retention
- Suitability: guidance must respect the user's risk profile, restrictions,
  and tax status stored in `portfolios`

## Enrichment Strategy (visual + Whisper) — design now, build phase two

- **Tier 1 (MVP):** YouTube auto-captions, all content
- **Tier 2 (phase 2):** Whisper re-transcription for old/high-value content
- **Tier 3 (phase 2):** visual frame extraction for chart-heavy content
- Capture is swappable by design (`TranscriptResult` / `VisualData`
  interfaces in `src/ingestion/capture_base.py`); stubs have full plans in
  docstrings; OFF by default via settings flags; ~10x cost — apply surgically
