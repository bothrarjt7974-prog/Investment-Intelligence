-- =====================================================
-- Migration: 004_market_data_and_credibility.sql
-- Layer 4 (validation): adds the external-symbol column market_data
-- needs to look up prices with yfinance, plus per-horizon credibility
-- columns on experts for the short/long/structural scoring buckets.
--
-- NOTE: market_data already exists from 001_initial_schema.sql with
-- (symbol, market, snapshot_date, ..., metadata, source). This migration
-- ALTERs it rather than recreating it — we only need fetch_symbol (the
-- external yfinance ticker, distinct from our canonical `symbol`) added.
-- `market` and `metadata` are left in place; unused by the validator but
-- harmless and possibly useful later.
-- =====================================================

ALTER TABLE market_data ADD COLUMN IF NOT EXISTS fetch_symbol TEXT;

-- `market` (e.g. 'NSE'|'NASDAQ'|'FX'|'MACRO') was NOT NULL in 001 but the
-- validator layer doesn't populate it — we identify data source via
-- `fetch_symbol` + `source` instead. Relax rather than force a meaningless
-- value into every row.
ALTER TABLE market_data ALTER COLUMN market DROP NOT NULL;

-- idx_market_symbol_date (symbol, snapshot_date DESC) already exists from 001
-- and covers the same lookup pattern price_on()/price_series() need.

ALTER TABLE experts ADD COLUMN IF NOT EXISTS credibility_short_horizon NUMERIC(5,2) DEFAULT 50;
ALTER TABLE experts ADD COLUMN IF NOT EXISTS credibility_long_horizon NUMERIC(5,2) DEFAULT 50;
ALTER TABLE experts ADD COLUMN IF NOT EXISTS credibility_structural NUMERIC(5,2) DEFAULT 50;

