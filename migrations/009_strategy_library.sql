-- =====================================================
-- Migration: 009_strategy_library.sql
-- Layer 8: strategy library + matcher (technical_execution_plan_v3.docx §11).
--
-- strategies.source_type already supports 'expert_extracted'|'historical_pattern'|
-- 'cfa_framework'|'institutional' (migration 006) — no new table needed for
-- the library itself, just the columns this layer's producers/consumers need.
-- =====================================================

-- Attribution for historical_pattern rows: which real expert_extracted
-- strategies this pattern summarizes (src/strategies/historical_patterns.py).
-- Empty for expert_extracted/cfa_framework/institutional rows — never
-- invention, full attribution to real underlying strategies.
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS source_strategy_ids UUID[] NOT NULL DEFAULT '{}';

-- Chroma sync cursor (src/strategies/embeddings.py) — same idempotent-
-- processing-cursor convention as processed_at/quality_filtered_at/
-- backtest_status elsewhere in this schema.
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS embedded_at TIMESTAMPTZ;

-- Human-readable framework name (e.g. "Yale Endowment Model") for
-- cfa_framework/institutional rows — src/strategies/framework_loader.py's
-- idempotency key (a framework file is only loaded once, matched by this
-- field). NULL for expert_extracted/historical_pattern rows.
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS source_name TEXT;

CREATE INDEX IF NOT EXISTS idx_strategies_pending_embedding ON strategies (quality_verdict)
    WHERE embedded_at IS NULL;

-- =====================================================
-- theme_strategy_matches — ranked candidate strategies per theme
-- (src/strategies/matcher.py). A working/ranking cache the guidance layer
-- reads, not an append-only audit log like themes/claims verdicts —
-- re-running the matcher for a theme deletes its prior rows here and
-- inserts fresh top-N ones.
-- =====================================================
CREATE TABLE IF NOT EXISTS theme_strategy_matches (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    theme_id            UUID NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
    strategy_id         UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,

    semantic_similarity NUMERIC(5,4),   -- cosine similarity, 0-1, from Chroma
    credibility_weight  NUMERIC(5,2),   -- expert's relevant-bucket credibility, or a fixed default for CFA/institutional
    backtest_weight     NUMERIC(5,2),   -- from backtest_record.outperformance_pct when complete, neutral default otherwise
    final_score         NUMERIC(6,4),   -- weighted combination — see src/strategies/matcher.py

    rank                INTEGER NOT NULL,  -- 1 = best match for this theme
    matched_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_theme_strategy_matches_theme ON theme_strategy_matches (theme_id);
