-- =====================================================
-- Migration: 008_fundamentals.sql
-- Layer 7: fundamentals sanity check (technical_execution_plan_v3.docx §10).
--
-- Adds inline verdict columns to `themes` (not a new table — 1:1 with the
-- theme being checked, same convention as claims.quality_verdict). Data
-- (fetched fundamentals snapshots, sector-relative valuation) comes from
-- code (src/fundamentals/data.py); the verdict is either a deterministic
-- balance-sheet veto or an LLM judgment call on top of those numbers
-- (src/fundamentals/checker.py).
-- =====================================================

ALTER TABLE themes ADD COLUMN IF NOT EXISTS fundamentals_verdict TEXT;
-- 'pass'|'downgrade'|'reject'|'not_applicable'|'insufficient_data', NULL until checked

ALTER TABLE themes ADD COLUMN IF NOT EXISTS fundamentals_notes TEXT;
-- LLM rationale, or the deterministic veto note when the balance-sheet
-- hard-reject fires without an LLM call.

ALTER TABLE themes ADD COLUMN IF NOT EXISTS fundamentals_data JSONB;
-- Resolved instruments, fetched snapshots, sector-relative PE — kept for
-- SEBI record-keeping and debugging, not just the final verdict.

ALTER TABLE themes ADD COLUMN IF NOT EXISTS fundamentals_checked_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_themes_pending_fundamentals ON themes (synthesized_at)
    WHERE status = 'active' AND fundamentals_verdict IS NULL;
