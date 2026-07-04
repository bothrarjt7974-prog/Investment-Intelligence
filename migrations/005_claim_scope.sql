-- =====================================================
-- Migration: 005_claim_scope.sql
-- Horizon-based scoping (technical plan §7): short-horizon claims are
-- marked out_of_scope for guidance regardless of quality score. They
-- still flow through validation and inform expert credibility — only
-- guidance-facing layers (theme synthesis onward) filter them out.
-- Set deterministically in code (screener), never by the LLM.
-- =====================================================

ALTER TABLE claims ADD COLUMN IF NOT EXISTS scope TEXT DEFAULT 'in_scope';

-- Guidance-facing queries will filter scope='in_scope' AND quality_verdict='pass'
CREATE INDEX IF NOT EXISTS idx_claims_in_scope ON claims (scope) WHERE scope = 'in_scope';
