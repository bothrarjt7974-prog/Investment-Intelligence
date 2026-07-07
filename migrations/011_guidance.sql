-- =====================================================
-- Migration: 011_guidance.sql
-- Layer 9: portfolio guidance generation (technical_execution_plan_v3.docx §12).
--
-- users/portfolios/portfolio_guidance did not exist anywhere in this
-- schema before this migration — the plan's "unchanged from v2" note
-- assumed a prior schema version this codebase never actually had.
-- Delivery (weekly digest, broker links, Zerodha integration) is
-- explicitly out of scope for this migration/layer per the 2026-07-07
-- decision log entry — this is guidance generation only.
-- =====================================================

CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    risk_profile    TEXT NOT NULL DEFAULT 'moderate',  -- 'conservative'|'moderate'|'aggressive'
    -- Free text, not a rigid schema — India-specific tax situations (slab,
    -- NRI status, existing LTCG utilized this year, etc.) vary too much
    -- for a fixed set of columns to capture usefully at MVP.
    tax_notes       TEXT,
    -- Sector/instrument exclusions, ESG preferences, etc.
    restrictions    JSONB NOT NULL DEFAULT '[]',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS portfolios (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL DEFAULT 'Primary',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_portfolios_user ON portfolios (user_id);

-- One row per tax lot, not per entity — a second purchase of the same
-- entity at a different date/price is a separate row, matching how real
-- tax-lot accounting and LTCG/STCG holding-period rules actually work
-- (see the Indian LTCG Exemption Harvesting framework, frameworks/india_ltcg_harvesting.yaml).
CREATE TABLE IF NOT EXISTS portfolio_holdings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    portfolio_id    UUID NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    entity          TEXT NOT NULL,  -- canonical, same naming convention as claims.entity/strategies.entity_class
    quantity        NUMERIC(20,6) NOT NULL,
    acquired_at     DATE NOT NULL,
    cost_basis      NUMERIC(20,4),  -- purchase price per unit, for tax-impact estimation
    asset_class     TEXT NOT NULL DEFAULT 'equity',  -- 'equity'|'mutual_fund'|'etf'|'bond'|'gold'|'crypto'|'cash'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_portfolio_holdings_portfolio ON portfolio_holdings (portfolio_id);

-- =====================================================
-- portfolio_guidance — every guidance event AND every refusal
-- (SEBI 5-year record-keeping requirement, CLAUDE.md RIA/Compliance Notes)
-- =====================================================
CREATE TABLE IF NOT EXISTS portfolio_guidance (
    id                          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id                     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    portfolio_id                UUID NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    theme_id                    UUID REFERENCES themes(id),
    matched_strategy_id         UUID REFERENCES strategies(id),  -- NULL for refusals

    guidance_type                TEXT,  -- 'INCREASE_EXPOSURE'|'REDUCE_EXPOSURE'|'INTRODUCE_NEW'|'EXIT'|'MAINTAIN'|'REBALANCE', NULL for refusals
    target_allocation_change    JSONB,
    specific_instruments        JSONB,
    execution_horizon           TEXT,
    position_holding_horizon    TEXT,
    funding_source               TEXT,
    tax_note                    TEXT,
    estimated_tax_impact        NUMERIC(20,2),
    conviction_level            TEXT,
    -- Code-assembled from theme.supporting_experts + the matched
    -- strategy's own source/backtest data — never LLM-invented. See
    -- src/guidance/generator.py::build_attribution.
    attribution                  JSONB,
    risks                        TEXT,

    -- Refusal as a feature (plan §12): every refusal is logged with its
    -- reason, same SEBI-relevant record as a real guidance event.
    is_refusal                  BOOLEAN NOT NULL DEFAULT FALSE,
    refusal_reason               TEXT,

    -- 'active'/'executing' block duplicate guidance on the same
    -- theme+portfolio pair (src/guidance/eligibility.py::check_duplicate_guidance).
    status                       TEXT NOT NULL DEFAULT 'active',  -- 'active'|'executing'|'completed'|'superseded'

    generated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_portfolio_guidance_theme_portfolio
    ON portfolio_guidance (theme_id, portfolio_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_guidance_active
    ON portfolio_guidance (portfolio_id, status) WHERE status IN ('active', 'executing');
