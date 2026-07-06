-- =====================================================
-- Migration: 006_strategies.sql
-- Phase B, Track B: strategy extraction (technical_execution_plan_v3 §5, §14).
--
-- Adds the `strategies` table — the Track B extraction output and the seed
-- of the strategy library (Layer 7). Mirrors the `claims` table's shape
-- (quality_verdict/quality_scores/scope columns follow the same convention
-- established in 001_initial_schema.sql and 005_claim_scope.sql) so the
-- quality-filter and scope-derivation pattern can be reused directly.
--
-- Also adds independent Track B processing-cursor columns on
-- content_pieces. These are NOT part of the expert_sources refactor the
-- v3 plan bundles into Phase B — that migration is deferred until a second
-- ingestion pattern actually activates (Phase H). strategies_processed_at
-- is needed regardless, because Track B runs as its own pass over the same
-- content_pieces Track A already processed, and must not share (and
-- clobber) Track A's processed_at/processing_error state.
-- =====================================================

CREATE TABLE IF NOT EXISTS strategies (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Provenance — which of the four Layer 7 sources this row came from.
    -- Only 'expert_extracted' is populated by Track B; the other three
    -- values are reserved for historical-pattern detection and manual
    -- CFA/institutional framework encoding (Layer 7, not built yet).
    source_type             TEXT NOT NULL DEFAULT 'expert_extracted',
    content_piece_id        UUID REFERENCES content_pieces(id) ON DELETE CASCADE,
    expert_id               UUID REFERENCES experts(id) ON DELETE CASCADE,

    -- Claims from the same content piece this strategy operationalizes.
    -- Resolved by code (entity match against claims.entity for the same
    -- content_piece_id), never by the LLM — see strategy_extractor.py.
    -- Empty array, not null, when there's no linked claim (a standalone
    -- strategy is equally valid per the plan).
    linked_claim_ids        UUID[] NOT NULL DEFAULT '{}',

    -- Core strategy data
    strategy_text           TEXT NOT NULL,
    strategy_type           TEXT NOT NULL,   -- 'allocation'|'entry_methodology'|'exit_methodology'|'position_sizing'|'rebalancing'|'risk_management'|'full_playbook'|'framework'
    trigger_conditions      JSONB,           -- when this strategy applies (market regime, macro setup, valuation condition)
    entity_class            TEXT,            -- normalized via normalize_entity() — sector/asset-class/geography/instrument
    allocation_rule         TEXT,            -- how much — percentages, weights, ranges
    position_sizing         TEXT,            -- per-position caps, concentration limits
    entry_methodology       TEXT,            -- lump sum, DCA over N months, on pullback
    holding_horizon_months  INTEGER,         -- stated minimum/target hold
    exit_conditions         TEXT,            -- when to trim or exit
    risk_framework          TEXT,            -- stated risk controls or failure modes
    reason_given            TEXT,            -- expert's stated rationale for the methodology

    -- Quality filter (Filter B — src/quality/strategy_screener.py)
    quality_verdict         TEXT,            -- 'pass'|'partial'|'fail'
    quality_scores          JSONB,           -- 5-dim breakdown (trigger_specificity, quantification, completeness, scale_invariance, failure_awareness)
    quality_filtered_at     TIMESTAMPTZ,

    -- Guidance-scope, same convention as claims.scope (005_claim_scope.sql):
    -- short-horizon/trading strategies are out_of_scope for guidance but
    -- still count toward expert credibility.
    scope                   TEXT NOT NULL DEFAULT 'in_scope',

    -- Backtesting (Layer 4 Track B — not built yet; columns reserved so
    -- the strategy library can filter/display before backtesting ships)
    backtest_record         JSONB,
    backtest_status         TEXT NOT NULL DEFAULT 'pending',  -- 'pending'|'complete'|'insufficient_history'

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_strategies_content_piece ON strategies (content_piece_id);
CREATE INDEX IF NOT EXISTS idx_strategies_expert ON strategies (expert_id);
CREATE INDEX IF NOT EXISTS idx_strategies_unfiltered ON strategies (quality_verdict)
    WHERE quality_verdict IS NULL;
CREATE INDEX IF NOT EXISTS idx_strategies_in_scope ON strategies (scope)
    WHERE scope = 'in_scope';

-- =====================================================
-- content_pieces — independent Track B processing cursor
-- =====================================================
ALTER TABLE content_pieces ADD COLUMN IF NOT EXISTS strategies_processed_at TIMESTAMPTZ;
ALTER TABLE content_pieces ADD COLUMN IF NOT EXISTS strategies_processing_error TEXT;

CREATE INDEX IF NOT EXISTS idx_content_strategies_unprocessed ON content_pieces (strategies_processed_at)
    WHERE strategies_processed_at IS NULL;
