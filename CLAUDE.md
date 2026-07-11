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
python -m src.validation.validator        # validation cron (Track A outcomes + credibility)
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

- **Done: the full pipeline has run end-to-end on real multi-expert data
  (339 tests).** 2026-07-08: an ingestion language-fallback bug fix
  (`youtube_capture.py` — non-English transcripts were silently dropped)
  unblocked all 10 experts; 1,000 content pieces ingested, 135 new
  multi-expert pieces extracted (Hindi extraction verified clean),
  screened (claims: 21% pass / 73% partial), synthesized. **Multi-expert
  convergence exists**: 5 medium-conviction themes (us_tech_ai,
  indian_it, nifty50, sp500, inr_usd). Layer 8 runs on **free local
  embeddings** (Chroma bundled MiniLM — `EMBEDDING_PROVIDER=local`
  default; OpenAI billing no longer blocks anything). **First real
  guidance events generated 2026-07-08/09** against the founder's real
  seeded portfolio (`portfolios/founder.yaml`, gitignored) — including
  REDUCE_EXPOSURE on indian_it holdings and correct refusals. Grounded
  chat interface live in Streamlit (`src/synthesis/query.py`, Q&A logged
  to `query_log`) plus portfolio view/add UI. Active prompts:
  `claim_extraction_v4`, `strategy_extraction_v2`, `quality_filter_v3`,
  `quality_filter_strategy_v1`, `theme_synthesis_v1`,
  `fundamentals_check_v1`, `guidance_generation_v1`,
  `grounded_query_v1`. `guidance_generation_v1` has now run for real but
  is still iteration ~1 of a budgeted 15-25 — founder line-by-line
  review required before any output is acted on.
- **Funnel fixes 2026-07-09** (see decision log): synthesis now takes
  any quality-passed long-horizon claim whose **target_date is still in
  the future** (was: claimed within 90 days — that discarded 69% of
  eligible claims); synthesizable input 100 → 179 claims, medium themes
  5 → 7, us_tech_ai at 5 experts. Index/macro entities
  (`INDEX_MACRO_ENTITIES` in `src/guidance/eligibility.py`) are
  guidance-eligible with `not_applicable` fundamentals + an explicit
  attribution note — company fundamentals categorically don't exist for
  an index; `reject` still always blocks; sector themes still need
  pass/downgrade. Equity indices now get a REAL check
  (`fetch_index_valuation`, src/fundamentals/data.py): tracker-ETF
  trailing PE vs documented fair bands (deterministic pass/downgrade,
  never reject) — the carve-out now only covers FX/commodity/crypto.
- **Guardrails added 2026-07-09** (see decision log): deterministic
  sizing gate (`src/guidance/sizing.py` — LLM sizing clamped to the
  strategy's allocation_rule or a risk-profile cap table; invented
  instruments reject the whole response; enforcement recorded in
  `attribution.sizing_source`); entity hierarchy clustering
  (`src/synthesis/entity_hierarchy.py` — child tickers/sectors fold into
  parent clusters so convergence is counted honestly); extraction junk
  handling (`non_financial_content` / `too_short` tags, no wasted API
  calls on Shorts); fundamentals `not_applicable` accepted from the LLM
  + `PRIVATE_ENTITIES` short-circuit; `ENTITY_SYMBOL_MAP` +40 tickers.
- **Deferred:** expert_sources multi-source migration (Phase H).
  Own-history valuation / earnings-estimate-revision tracking (needs
  paid historical fundamentals). Embedding-based cluster *suggestion*
  (hierarchy map is the human-approved allowlist). Valuation-based
  backtest triggers. Track B on-demand market-data backfill. OpenAI
  embeddings (quality upgrade only — flip `EMBEDDING_PROVIDER=openai`
  and delete `data/chroma/` to re-embed once funded). Delivery
  (weekly digest, alerts, broker integration) — still not started; auth
  is required before any beta user touches the chat.
- **Data posture:** founder chose to keep improving on the small dataset
  before major extraction spend. Remaining big-ticket items, each gated
  on explicit founder sign-off: (a) old-content backfill per expert —
  the highest-leverage spend, because claims with passed target dates
  validate immediately and make credibility scores real (every expert
  currently sits at the neutral default 50); (b) deeper per-expert
  ingestion (new experts have 15-20 pieces vs Akshat's 866); (c) the
  ~702-piece Akshat backlog.
- **Placeholder data warning:** `portfolios/founder.yaml` acquisition
  dates are approximations (statement only gave LTCG/STCG splits) — the
  tax-timing reasoning in guidance is only as accurate as those dates.
  Replace with real purchase dates.
- **MVP scope:** 10 experts live (7 Indian, 3 US); YouTube first,
  blogs/podcasts Phase H; India + US equities; founder as user zero,
  then 5-10 beta users. Do not expand scope until the current phase
  operates reliably.

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
