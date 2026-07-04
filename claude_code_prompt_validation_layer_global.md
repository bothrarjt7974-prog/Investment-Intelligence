# Claude Code Prompt — Build the Validation Layer (Global, Free Data Only)

Copy everything below this line into Claude Code from your repo root.

---

Build the validation layer for this project. Read CLAUDE.md first for project context and conventions. This is Layer 4 of our architecture: it validates investment claims against real market data after their target dates pass, and computes expert credibility scores from the results.

## Scope for this build

GLOBAL scope — validate claims across all geographies (india, us, global, emerging_markets) using only FREE data sources. yfinance is the primary source for everything (Indian stocks, US stocks, global indices, currencies, commodities, crypto). Small manual CSV tables for macro rates that have no free API. If an entity has no free data source, mark its claims data_unavailable — never pay for data in this build, and never force a bad proxy.

Strategy backtesting (Track B) is explicitly OUT of scope for this build — claims outcome validation only.

## What exists already (do not rebuild)

- `src/db/queries.py` — Supabase access helpers (fetch_by_id, insert_one, update_by_id). Add new query functions here, follow existing patterns.
- `src/config/settings.py` — env-based config.
- `claims` table with these relevant columns: id, entity, geography, claim_type, strength, direction, timeframe, target_horizon_months, claimed_at, target_date, quality_verdict, verification_status, verification_outcome (jsonb), verified_at.
- `experts` table with: id, name, credibility_score, total_claims, validated_claims, correct_claims, sample_size_flag.
- Conventions: loguru for logging, pendulum for dates, type hints everywhere, docstrings explain WHY, pytest before commit.

## Build these components

### 1. Database migration — `migrations/004_market_data_and_credibility.sql`

Create market_data table:
- id uuid PK default gen_random_uuid()
- symbol text not null (our canonical entity name, e.g. 'RELIANCE', 'nifty50', 'inr_usd', 'AAPL', 'BTC')
- fetch_symbol text (the external symbol used, e.g. 'RELIANCE.NS', '^NSEI', 'BTC-USD')
- snapshot_date date not null
- open_price, high_price, low_price, close_price numeric
- volume bigint
- source text not null ('yfinance', 'manual')
- fetched_at timestamptz default now()
- UNIQUE (symbol, snapshot_date)
- Index on (symbol, snapshot_date)

Add columns to experts:
- credibility_short_horizon numeric default 50
- credibility_long_horizon numeric default 50
- credibility_structural numeric default 50

### 2. Entity-symbol mapping — `src/validation/entity_symbol_map.py`

A single dict ENTITY_SYMBOL_MAP from our canonical entity names to (fetch_symbol, source) tuples, covering the full global universe:

**Indian stocks** (yfinance, .NS suffix): RELIANCE→RELIANCE.NS, TCS, INFY, HDFCBANK, ICICIBANK, SBIN, ASIANPAINT, ZOMATO, etc. Plus a fallback rule: any uppercase ticker not in the map AND appearing in claims with geography='india' → try appending .NS (log a warning when the fallback fires so we can add explicit entries).

**US stocks** (yfinance, bare ticker): AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, NFLX. Fallback: uppercase ticker with geography='us' → use as-is.

**Crypto** (yfinance): BTC→BTC-USD, ETH→ETH-USD, SOL→SOL-USD.

**Indian indices** (yfinance): nifty50→^NSEI, nifty500→^CRSLDX, nifty_midcap150→NIFTYMIDCAP150.NS, nifty_smallcap250→NIFTYSMLCAP250.NS.

**US/global indices** (yfinance): nasdaq→^IXIC, sp500→^GSPC, qqq→QQQ, dow→^DJI.

**Indian sector proxies** (yfinance NSE sector indices): indian_it→^CNXIT, indian_banking→^NSEBANK, indian_pharma→^CNXPHARMA, indian_auto→^CNXAUTO, indian_fmcg→^CNXFMCG, indian_metals→^CNXMETAL, indian_realestate→^CNXREALTY, indian_energy→^CNXENERGY, indian_midcap→NIFTYMIDCAP150.NS, indian_smallcap→NIFTYSMLCAP250.NS, indian_equity_market→^NSEI.

**US sector proxies** (yfinance sector ETFs — free, liquid, long history): us_tech→XLK, us_software→IGV, us_semiconductors→SOXX, us_banking→KBE, us_healthcare→XLV, us_energy→XLE, us_defense→ITA, us_consumer→XLY, us_equities→^GSPC, us_smallcap→IWM.

**Thematic proxies**: global_ai→BOTZ (imperfect proxy — add comment; alternative is marking unmappable), crypto_sector→BTC-USD (dominant asset as proxy, comment the judgment).

**Currencies** (yfinance): inr_usd→INR=X, jpy_usd→JPY=X, dxy→DX-Y.NYB, eur_usd→EURUSD=X.

**Commodities** (yfinance): gold→GC=F, silver→SI=F, crude_oil→CL=F.

**Manual source** (no free API — small CSV tables): rbi_rate→(None,'manual'), fed_rate→(None,'manual'), india_inflation→(None,'manual'), us_inflation→(None,'manual'), india_gdp→(None,'manual'), us_gdp→(None,'manual').

Note on fed_rate and us_inflation: FRED has a free API but requires an API key signup. Implement manual CSV first (it's 30 rows), and add a TODO comment for optional FRED integration later — do not block this build on an API key.

Export: resolve_entity(entity, geography) -> tuple[str, str] | None. Uses the map first, then the geography-aware ticker fallbacks, returns None for unmappable entities (e.g., us_china_tensions, global_liquidity — abstract macro entities with no price series; add these to an explicit UNMAPPABLE set so they're documented, not silently dropped).

Verify each yfinance symbol empirically during testing — if a sector index symbol fails to return data, mark that entity unmappable with a comment rather than guessing alternative symbols.

### 3. Market data ingestion — `src/validation/market_data.py`

Functions:

backfill_all(start_date='2019-01-01') — for every yfinance-sourced entity in the map: download daily OHLC via yfinance, upsert into market_data (on conflict symbol+snapshot_date do nothing), log per-symbol row counts and gaps. Random 1-2s delays between symbols. Batch inserts (500 rows per call), never row-by-row. Expect roughly 70-100 symbols × ~1,800 trading days ≈ 150-180k rows total.

load_manual_csv(path, symbol) — loads data/manual/*.csv files with columns (date, value) into market_data using close_price=value, source='manual'.

daily_update() — fetches last 7 days for every symbol (overlap tolerates missed cron runs), upserts. Nightly cron entry point.

Create these manual CSVs pre-filled from your knowledge, each with a header comment "verify against official source before production use":
- data/manual/rbi_repo.csv — RBI repo rate changes Jan 2019 → mid-2026 (6.5 down through COVID 4.0, 2022-23 hikes to 6.5, 2025-26 easing cycle)
- data/manual/fed_rate.csv — Fed funds target (upper bound) changes Jan 2019 → mid-2026 (2019 cuts, COVID zero, 2022-23 hikes to 5.5, 2024-26 easing)
- data/manual/india_cpi.csv and data/manual/us_cpi.csv — monthly YoY CPI prints 2019 → mid-2026 (approximate values acceptable, flagged for verification)

CLI: python -m src.validation.market_data --backfill | --daily | --load-manual

### 4. Price lookup helpers — in market_data.py

price_on(symbol, target_date, max_lookback_days=7) -> float | None — exact date close, else nearest PRIOR trading day within lookback (handles weekends + both Indian and US market holidays), else None.

price_series(symbol, start, end) -> list[(date, close)] — for price-target scanning.

return_between(symbol, start, end) -> float | None — percentage return using price_on at both ends.

### 5. Track A validator — `src/validation/validator.py`

Main entry: run_validation(max_claims=None) following the same CLI/loop/logging pattern as src/quality/screener.py.

Fetch claims where: quality_verdict='pass' AND verification_status='pending' (or null) AND target_date < today. Add fetch_validatable_claims(limit) to src/db/queries.py.

Per-claim dispatch logic, in this order:

a) If strength='opinion' OR target_horizon_months is null → verification_status='opinion_only'. No outcome math.

b) If resolve_entity(entity, geography) is None (including entity=null) → 'data_unavailable'.

c) Price-target claims: detect by regex on claim_text for ₹/Rs/$ followed by a number, or 'target of'. Scan price_series from claimed_at to target_date. Touched target → 'correct'. Came within 5% → 'partial'. Else → 'incorrect'. Store method='price_target', detected target, max price reached, pct_of_target in verification_outcome.

d) Everything else: directional validation. Compute return_between(symbol, claimed_at, target_date). direction='bullish' and return > +2% → correct; direction='bearish' and return < -2% → correct; |return| <= 2% → partial regardless of direction; else incorrect. direction='neutral' → correct if |return| <= 5%, partial if <= 10%, else incorrect. direction='na' → 'opinion_only'.

Also compute and store max favorable excursion (MFE) and max adverse excursion (MAE) over the window from price_series in verification_outcome — unused now, scoring v2 will want them.

verification_outcome jsonb must always include: method, symbol, price_at_claim, price_at_target, return_pct, predicted_direction, result, mfe_pct, mae_pct, validated_window_days. Set verified_at=now.

If price data is missing at either endpoint → 'data_unavailable', log, continue. Never crash the batch on one claim.

### 6. Credibility scoring — `src/internal/scoring.py`

Create src/internal/ with __init__.py and a comment block at top of scoring.py: proprietary, never share externally.

recompute_expert_credibility(expert_id) — pull that expert's claims where verification_status in ('correct','partial','incorrect'). Partition into three horizon buckets by target_horizon_months: short <12, long 12-36, structural >36.

Per bucket:
- outcome value: correct=1.0, partial=0.5, incorrect=0.0
- strength weight: strong=1.0, soft=0.5
- recency decay on claimed_at age: <2y→1.0, 2-4y→0.5, >4y→0.25
- weighted hit_rate = sum(outcome*weight) / sum(weight)
- shrinkage: n = count in bucket; shrink = n/(n+20)
- score = 50 + (hit_rate - 0.5) * 100 * shrink, clamped [0,100]
- bucket with 0 claims keeps default 50

Overall credibility_score = average of the three bucket scores weighted by each bucket's claim count. sample_size_flag = true if total validated < 20. Update the experts row including total/validated/correct counts.

recompute_all_experts() called at the end of every run_validation batch.

### 7. Tests — `tests/test_validation.py`

pytest with the market data layer mocked (monkeypatch price_on / price_series / return_between — no network in tests). Cover at minimum:

- Directional bullish correct / incorrect / partial-band (+1.9%) cases
- Bearish correct case; neutral within-band case
- Price-target regex: "target of ₹3,200", "Rs 900 target", "$250 target", and a non-target claim
- Price-target touched / within-5% / missed
- opinion_only for strength=opinion and for direction=na
- data_unavailable when resolve_entity returns None and when price_on returns None
- resolve_entity: mapped entity, india ticker fallback (.NS), us ticker fallback (bare), UNMAPPABLE set member returns None
- Scoring: empty bucket→50; 5/5 correct with n=5 shrinks to ~60 (assert 58-62); mixed outcomes; recency decay; strength weighting; clamping
- price_on prior-trading-day fallback (mock a series with a weekend gap)

### 8. Cron workflow — `.github/workflows/validation.yml`

Nightly at 02:00 IST: market_data --daily, then validator, then a summary log step. Use existing secrets pattern if an ingestion workflow exists; otherwise SUPABASE_URL, SUPABASE_KEY env secrets.

## Execution order

1. Write the migration, ask me to run it in Supabase before proceeding
2. Build entity_symbol_map.py and market_data.py; run a SMALL backfill test (5 symbols spanning India stock, US stock, Indian index, US ETF, currency — 2024 onward) and show me row counts before the full backfill
3. Build validator.py + queries addition
4. Build scoring.py
5. Write and run all tests — everything green before you're done
6. Full backfill from 2019 (expect 10-20 minutes with delays), then a validation dry run with --max-claims 10 and show me the outcomes for sanity-check before the full run

## Hard rules

- No LLM calls anywhere in this layer — pure deterministic code
- FREE data sources only — yfinance and manual CSVs; no paid APIs, no API-key-gated services in this build (FRED is a documented TODO)
- Never force-score an unresolvable claim; the unresolvable statuses exist for a reason
- Never force a bad proxy — if the mapping judgment is questionable (global_ai, crypto_sector), comment the judgment so it can be revisited
- Never delete or overwrite claims data; only update the verification fields
- Batch DB writes; log progress every 25 claims like the screener does
- If uncertain about a yfinance symbol, test it empirically rather than assuming
