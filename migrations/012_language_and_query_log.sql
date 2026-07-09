-- 012: transcript language + grounded-chat query log
--
-- (a) content_pieces.language — the ingestion language-fallback fix
--     (2026-07-08) now captures non-English transcripts (most Indian
--     finance channels are Hindi/Hinglish); the captured language code
--     was previously dropped at persistence. Backfilled as 'en' for
--     existing rows: everything ingested before the fix could only have
--     been English (that was exactly the bug).
--
-- (b) query_log — every grounded-chat Q&A is recorded with its sources,
--     same record-keeping posture as portfolio_guidance (the chat
--     describes recorded advisory data to a user; SEBI 5-year retention
--     applies to advisory communications).

ALTER TABLE content_pieces
    ADD COLUMN IF NOT EXISTS language TEXT NOT NULL DEFAULT 'en';

CREATE TABLE IF NOT EXISTS query_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    -- Rows the answer was grounded in (guidance ids, theme ids, claim ids,
    -- expert ids) — assembled by code, not the LLM, mirroring
    -- portfolio_guidance.attribution.
    sources JSONB NOT NULL DEFAULT '{}'::jsonb,
    refused_fresh_advice BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_query_log_user_created
    ON query_log (user_id, created_at DESC);
