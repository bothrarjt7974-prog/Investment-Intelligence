-- =====================================================
-- Migration: 007_themes.sql
-- Layer 6: structural theme synthesis (technical_execution_plan_v3.docx §9).
--
-- Across quality-passed, long-horizon claims (target_horizon_months >= 12)
-- from the last N days, clustered by canonical entity, an Opus pass
-- classifies each claim's reasoning independence relative to the rest of
-- its cluster; src/internal/theme_scoring.py combines that with each
-- expert's credibility into a theme row here.
--
-- New themes for the same entity_cluster_key supersede (not delete) prior
-- active rows — history is preserved for SEBI record-keeping, same
-- tag-don't-delete convention as claims.quality_verdict.
-- =====================================================

CREATE TABLE IF NOT EXISTS themes (
    id                          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Which canonical entity claims were grouped by to form this cluster
    -- (v1 clustering is deterministic entity-match, not semantic/embedding
    -- based — see 2026-07-06 decision log entry).
    entity_cluster_key          TEXT NOT NULL,
    geography                   TEXT,

    -- Synthesis output (Opus, prompts/theme_synthesis_v1.md)
    summary_thesis              TEXT NOT NULL,
    cycle_position              TEXT,
    dissenting_views            TEXT,
    instruments_mentioned       TEXT[] NOT NULL DEFAULT '{}',
    time_horizon_years          NUMERIC(4,1),

    -- Convergence/credibility weighting (src/internal/theme_scoring.py —
    -- proprietary, see src/internal/README.md)
    independence_score          NUMERIC(4,3),   -- 0.000-1.000
    credibility_weighted_score  NUMERIC(5,2),
    conviction_level            TEXT NOT NULL,  -- 'high'|'medium'|'low'

    -- Full attribution — SEBI record-keeping requirement, never invention
    supporting_experts          JSONB NOT NULL DEFAULT '[]',  -- [{expert_id, expert_name, independence_label, credibility_used, claim_ids}]
    source_claim_ids            UUID[] NOT NULL DEFAULT '{}',

    window_start_date           DATE NOT NULL,
    window_end_date             DATE NOT NULL,

    -- 'active' | 'superseded' — a later synthesis run for the same
    -- entity_cluster_key marks the prior row superseded rather than
    -- deleting it.
    status                      TEXT NOT NULL DEFAULT 'active',

    synthesized_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_themes_entity_cluster ON themes (entity_cluster_key);
CREATE INDEX IF NOT EXISTS idx_themes_active ON themes (status) WHERE status = 'active';
