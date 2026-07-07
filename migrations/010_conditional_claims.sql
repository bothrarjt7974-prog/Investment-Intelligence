-- =====================================================
-- Migration: 010_conditional_claims.sql
-- claim_extraction_v4 (2026-07-07 review): assigns the 'conditional_unresolved'
-- verification_status that has existed in the schema since 001_initial_schema.sql
-- but was never populated — conditional claims ("if RBI cuts, banks re-rate")
-- were being force-validated as if unconditional. See decision log.
-- =====================================================

ALTER TABLE claims ADD COLUMN IF NOT EXISTS is_conditional BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS condition_text TEXT;
