-- =====================================================
-- Migration: 001_initial_schema.sql
-- Creates core tables for the investment intelligence layer.
-- =====================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =====================================================
-- EXPERTS — the people/channels we ingest from
-- =====================================================
CREATE TABLE IF NOT EXISTS experts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            TEXT NOT NULL,
    handle          TEXT NOT NULL,                     -- channel ID, RSS handle, etc.
    source_type     TEXT NOT NULL,                     -- 'youtube' | 'blog' | 'podcast'
    geography       TEXT NOT NULL,                     -- 'india' | 'us' | 'global'
    credibility_score        NUMERIC(5,2) DEFAULT 50.00,
    total_claims             INTEGER DEFAULT 0,
    validated_claims         INTEGER DEFAULT 0,
    correct_claims           INTEGER DEFAULT 0,
    sample_size_flag         BOOLEAN DEFAULT TRUE,    -- TRUE if < 20 validated claims
    cycle_test_passed        BOOLEAN DEFAULT FALSE,   -- tested across bull and bear
    is_active                BOOLEAN DEFAULT TRUE,
    notes                    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (handle, source_type)
);

CREATE INDEX idx_experts_active ON experts (is_active);
CREATE INDEX idx_experts_score ON experts (credibility_score DESC);

-- =====================================================
-- CONTENT_PIECES — raw ingested content
-- =====================================================
CREATE TABLE IF NOT EXISTS content_pieces (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    expert_id       UUID NOT NULL REFERENCES experts(id) ON DELETE CASCADE,
    source_url      TEXT NOT NULL,
    title           TEXT,
    published_at    TIMESTAMPTZ NOT NULL,
    raw_text        TEXT NOT NULL,                     -- full transcript/article
    word_count      INTEGER,
    duration_seconds INTEGER,                          -- for video/podcast
    processed_at    TIMESTAMPTZ,                       -- when claims were extracted
    processing_error TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_url)
);

CREATE INDEX idx_content_expert ON content_pieces (expert_id);
CREATE INDEX idx_content_published ON content_pieces (published_at DESC);
CREATE INDEX idx_content_unprocessed ON content_pieces (processed_at) WHERE processed_at IS NULL;

-- =====================================================
-- CLAIMS — structured extracted insights
-- =====================================================
CREATE TABLE IF NOT EXISTS claims (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    content_piece_id UUID NOT NULL REFERENCES content_pieces(id) ON DELETE CASCADE,
    expert_id        UUID NOT NULL REFERENCES experts(id) ON DELETE CASCADE,

    -- Core claim data
    claim_text       TEXT NOT NULL,                    -- specific testable statement
    claim_type       TEXT NOT NULL,                    -- 'macro'|'sector'|'stock_pick'|'strategy'|'overview'
    geography        TEXT NOT NULL,                    -- 'india'|'us'|'global'|'emerging_markets'
    timeframe        TEXT NOT NULL,                    -- 'short'|'medium'|'long'
    strength         TEXT NOT NULL,                    -- 'strong'|'soft'|'opinion'
    entity           TEXT,                             -- specific stock/sector/factor
    direction        TEXT,                             -- 'bullish'|'bearish'|'neutral'|'na'
    reason_given     TEXT,                             -- expert's reasoning

    -- Quality filter
    quality_verdict  TEXT,                             -- 'pass'|'partial'|'fail'
    quality_scores   JSONB,                            -- 5-dim breakdown
    quality_filtered_at TIMESTAMPTZ,

    -- Validation
    claimed_at       TIMESTAMPTZ NOT NULL,             -- when claim was made
    target_date      DATE,                             -- when to validate by
    verification_status TEXT NOT NULL DEFAULT 'pending', -- 'pending'|'correct'|'partial'|'incorrect'|'opinion_only'|'conditional_unresolved'|'data_unavailable'
    verification_outcome JSONB,                        -- numerical details of outcome
    verified_at      TIMESTAMPTZ,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_claims_expert ON claims (expert_id);
CREATE INDEX idx_claims_type ON claims (claim_type);
CREATE INDEX idx_claims_entity ON claims (entity);
CREATE INDEX idx_claims_pending_validation ON claims (target_date)
    WHERE verification_status = 'pending';
CREATE INDEX idx_claims_quality_pass ON claims (quality_verdict)
    WHERE quality_verdict = 'pass';

-- =====================================================
-- MARKET_DATA — daily snapshots used for validation
-- =====================================================
CREATE TABLE IF NOT EXISTS market_data (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    symbol          TEXT NOT NULL,                     -- 'RELIANCE.NS', 'AAPL', 'INR/USD'
    market          TEXT NOT NULL,                     -- 'NSE'|'BSE'|'NASDAQ'|'NYSE'|'FX'|'MACRO'
    snapshot_date   DATE NOT NULL,
    open_price      NUMERIC(20,4),
    high_price      NUMERIC(20,4),
    low_price       NUMERIC(20,4),
    close_price     NUMERIC(20,4),
    volume          BIGINT,
    metadata        JSONB,                             -- PE, debt/equity, etc.
    source          TEXT,                              -- 'yahoo'|'nse'|'rbi'|'fred'
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, snapshot_date)
);

CREATE INDEX idx_market_symbol_date ON market_data (symbol, snapshot_date DESC);

-- =====================================================
-- BRIEFINGS — generated morning intelligence outputs
-- =====================================================
CREATE TABLE IF NOT EXISTS briefings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    briefing_date   DATE NOT NULL,
    briefing_text   TEXT NOT NULL,
    metadata        JSONB,                             -- claims used, confidence scores
    quality_score   NUMERIC(3,1),                      -- founder's daily score 1-5
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (briefing_date)
);

-- =====================================================
-- DECISION_INFLUENCES — track when product influenced a real decision
-- (used to measure product value over time)
-- =====================================================
CREATE TABLE IF NOT EXISTS decision_influences (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    decision_date   DATE NOT NULL,
    briefing_id     UUID REFERENCES briefings(id),
    decision_summary TEXT NOT NULL,
    decision_without_product TEXT,                     -- counterfactual
    outcome_check_date DATE,
    actual_outcome  TEXT,
    helped          BOOLEAN,                           -- evaluated later
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =====================================================
-- Trigger: keep experts.updated_at fresh
-- =====================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_experts_updated_at
    BEFORE UPDATE ON experts
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
