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
   content piece: Track A claims (built, v3 prompt active as of Phase B
   kickoff, not yet recalibrated against real output), Track B strategies
   (trigger/allocation/sizing/entry/exit schema — built, `strategy_extraction_v1`
   not yet calibrated against real output).
3. **Normalization** (`src/extraction/normalization.py`) — deterministic
   post-processing: entity canonicalization, within-content dedup,
   target_date computation (claims); entity_class canonicalization,
   within-content dedup (strategies). Code, never LLM.
4. **Quality filters** (`src/quality/`) — Filter A scores claims on 5
   reasoning dimensions (built, v3 calibrating). Filter B scores strategies
   on 5 specificity dimensions (built, `quality_filter_strategy_v1` not yet
   calibrated). Rejects aggressively.
5. **Validation** (`src/validation/`) — Track A: outcome validation of claims
   against market data when target dates pass (built). Track B: strategy
   backtesting via trigger vocabulary (built — 5 of 7 trigger types have
   detectors; valuation-based triggers deferred to Layer 7). Credibility
   scored per expert, per horizon (short/long/structural), per medium.
6. **Structural theme synthesis** (`src/synthesis/`) — long-horizon claims
   (12+ months only) combined across experts into themes, weighted by
   credibility and reasoning independence (built — clusters by canonical
   entity, not embeddings yet; idle until multiple experts are active). Opus.
7. **Fundamentals sanity check** (`src/fundamentals/`) — themes verified
   against valuations, earnings trajectory, balance sheet health before
   they can drive guidance. Data from code, judgment from LLM (built —
   valuation is sector-relative, not vs. own history; balance sheet is a
   deterministic hard veto; idle until themes exist to check).
8. **Strategy library + matcher** (`src/strategies/`) — unified asset:
   expert-extracted strategies, historical patterns, CFA frameworks
   (built — `frameworks/` ships with the loader only, zero seeded content).
   Structured filter → embedding match → credibility/backtest weighting
   (built — Chroma + OpenAI embeddings live, structured filter is exact
   entity_class match not a sector hierarchy; idle until the library has
   real strategies).
9. **Guidance + delivery** (`src/guidance/`, `src/delivery/`) — allocation
   guidance generation (theme + strategy + user portfolio) built —
   deterministic eligibility gates + refusal logging, Opus synthesis for
   the recommendation itself; strongest calibration warning of any prompt
   in this codebase, idle until multi-expert convergence produces a
   medium/high-conviction theme. Delivery (`src/delivery/` — weekly
   digest, event alerts, broker integration) explicitly NOT built —
   deferred, needs a real UI decision.

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
python -m src.validation.backtest --max-strategies 20  # Track B backtest (test run)
python -m src.synthesis.theme_synthesis --max-clusters 5  # theme synthesis (test run)
python -m src.fundamentals.checker --max-themes 5  # fundamentals check (test run)
python -m src.strategies.embeddings --max-strategies 20  # embed strategies into Chroma (test run)
python -m src.strategies.historical_patterns  # detect repeated expert methodology
python -m src.strategies.framework_loader     # load CFA/institutional frameworks from frameworks/
python -m src.strategies.matcher --max-themes 5  # match themes to library strategies (test run)
python -m src.guidance.portfolio_seed          # load real portfolios from portfolios/
python -m src.guidance.generator --max-themes 5  # generate guidance / log refusals (test run)
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
  (tag, don't delete; downstream queries filter on quality_verdict IN
  ('pass', 'partial') — see 2026-07-04 decision log, 'fail' is the only
  excluded verdict)
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

- **Done: all 9 layers have code and passing tests (313 tests).** As of
  the 2026-07-07 full-system review + Layer 9 build, every layer through
  guidance generation has code; Layers 1-7 and part of 8 have run against
  real data (see the 2026-07-07 decision log entries for the complete
  trace and every finding). Active prompts: `claim_extraction_v4`,
  `strategy_extraction_v2`, `quality_filter_v3`, `quality_filter_strategy_v1`,
  `theme_synthesis_v1`, `fundamentals_check_v1`, `guidance_generation_v1` —
  none calibrated at full-backlog scale; `guidance_generation_v1` has never
  run at all yet (see below) and carries the strongest calibration warning
  in this codebase. 27 real themes exist (2026-07-06 backfill catch-up),
  all fundamentals-checked (5 pass, 1 downgrade, 1 reject, 13
  not_applicable, 7 insufficient_data). 1 strategy has a complete real
  backtest. Strategy library seeded with 11 real CFA/institutional
  frameworks (`frameworks/`, 2026-07-07) spanning 6 of 8 strategy types.
  Delivery (`src/delivery/`) is explicitly NOT built — deferred pending a
  real UI decision.
- **Deferred:** expert_sources multi-source migration — pushed to Phase H
  kickoff. Own-history valuation and true multi-quarter earnings-estimate-
  revision tracking — need historical fundamentals data not free to get;
  v1 substitutes sector-relative PE and current growth sign. Embedding-
  based theme clustering (Layer 6 still clusters by exact entity match) —
  Chroma exists now so this is unblocked on infra, just not yet revisited.
  Matcher's structured filter is exact entity_class match plus a
  `broad_equity` fallback, not a full sector hierarchy. Valuation-based
  backtest triggers — need real historical valuation data, not a technical
  proxy. `src/validation/backtest.py` has no on-demand market-data
  backfill (Track A's `ensure_symbol_backfilled` equivalent) — a newly-
  added `ENTITY_SYMBOL_MAP` entry silently reports `insufficient_history`
  until manually backfilled. `ENTITY_SYMBOL_MAP` is missing several
  tickers real themes/claims already reference (`indian_airlines`, AMD,
  BHARTIARTL, INDIGO, SWIGGY, MCDONALDS, KFC, HUL, DABUR, NESTLEIND, IOC)
  — grow it as these surface. Delivery (weekly digest, broker links,
  Zerodha integration) — needs a real UI decision, not started.
- **BLOCKED on one billing/account issue** (found 2026-07-07, still open):
  **OpenAI API key has `insufficient_quota`** — confirmed still failing
  even after a credit top-up (separate account/billing from Anthropic —
  check platform.openai.com specifically). Blocks
  `src/strategies/embeddings.py` and therefore the matcher entirely —
  Layer 8 has never produced a real `theme_strategy_matches` row. Anthropic
  is confirmed resolved (the interrupted `strategy_extraction_v2`
  calibration batch completed cleanly; full 20-piece batch shows 0
  invented trigger types).
- **Reality check on Layer 9:** all 27 existing themes have
  `conviction_level='low'` (single-expert clusters are hard-coded LOW).
  Guidance generation will correctly refuse on every one of them until
  multi-expert convergence exists — this is "silence is a valid and
  frequent output" working as designed, not a bug. Seed your own portfolio
  (`portfolios/`, copy `TEMPLATE.yaml`, real financial data — gitignored
  by default) and run `src.guidance.generator` to exercise the refusal
  path; that's the only real exercise available until then.
- **Full-backlog-run gate checklist** (all must hold before spending on
  the remaining 732 pieces): (a) OpenAI account funded (Anthropic already
  is); (b) `claim_extraction_v4`'s 8%-pass result reviewed and accepted;
  (c) `strategy_extraction_v2`'s zero-invented-types result held across
  the full 20-piece batch (confirmed); (d) founder review of the
  2026-07-07 decision log entries in full.
- **Next:** Once OpenAI is funded — run the matcher for the first time on
  real data (embeddings have never succeeded). Then, once the gate above
  clears — full 732-piece run through `claim_extraction_v4` +
  `strategy_extraction_v2`, full backlog screening, then re-run validator/
  backtest/fundamentals/matcher/guidance at real volume.
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
