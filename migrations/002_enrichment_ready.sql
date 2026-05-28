-- =====================================================
-- Migration: 002_enrichment_ready.sql
--
-- Adds fields and tables to accommodate FUTURE visual + Whisper
-- extraction (phase two). These stay empty/default during the MVP.
-- The point is to avoid a schema migration during a busy build later.
--
-- Nothing in the MVP populates these beyond defaults.
-- =====================================================

-- =====================================================
-- content_pieces — enrichment fields
-- =====================================================

-- How the transcript was obtained.
--   'youtube_auto' = YouTube auto-captions (MVP default, cheap, lower quality on old videos)
--   'youtube_manual' = creator-uploaded captions (higher quality when present)
--   'whisper' = re-transcribed with OpenAI Whisper (phase two, high quality)
ALTER TABLE content_pieces
    ADD COLUMN IF NOT EXISTS transcript_source TEXT NOT NULL DEFAULT 'youtube_auto';

-- Confidence in transcript accuracy, 0.00–1.00.
-- MVP default 0.70 for auto-captions. Whisper would set ~0.95.
-- Old (pre-2021) auto-captions should be set lower (~0.50) at ingestion time.
ALTER TABLE content_pieces
    ADD COLUMN IF NOT EXISTS transcript_confidence NUMERIC(3,2) NOT NULL DEFAULT 0.70;

-- Structured data extracted from video frames (charts, tables, overlays).
-- Empty in MVP. Populated by phase-two visual extraction.
ALTER TABLE content_pieces
    ADD COLUMN IF NOT EXISTS visual_data JSONB;

-- Whether visual frame extraction has been run on this piece.
ALTER TABLE content_pieces
    ADD COLUMN IF NOT EXISTS has_visual_extraction BOOLEAN NOT NULL DEFAULT FALSE;

-- Which enrichment tier this content received.
--   'tier1_transcript' = transcript only (MVP default)
--   'tier2_whisper'    = Whisper re-transcription added
--   'tier3_full'       = Whisper + visual frame extraction
ALTER TABLE content_pieces
    ADD COLUMN IF NOT EXISTS enrichment_tier TEXT NOT NULL DEFAULT 'tier1_transcript';

-- Raw audio/video processing metadata (download size, duration processed, etc.)
ALTER TABLE content_pieces
    ADD COLUMN IF NOT EXISTS enrichment_metadata JSONB;

CREATE INDEX IF NOT EXISTS idx_content_enrichment_tier
    ON content_pieces (enrichment_tier);
CREATE INDEX IF NOT EXISTS idx_content_needs_enrichment
    ON content_pieces (has_visual_extraction)
    WHERE has_visual_extraction = FALSE;

-- =====================================================
-- visual_evidence — extracted on-screen data (phase two)
-- =====================================================
-- Each row is one piece of visual evidence extracted from a video frame:
-- a chart, a table, a text overlay. Linked to a content piece and a
-- timestamp so it can be paired with the spoken claim at that moment.
CREATE TABLE IF NOT EXISTS visual_evidence (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    content_piece_id UUID NOT NULL REFERENCES content_pieces(id) ON DELETE CASCADE,

    timestamp_seconds INTEGER NOT NULL,          -- where in the video this appeared
    visual_type     TEXT NOT NULL,               -- 'chart'|'table'|'text_overlay'|'slide'
    extracted_data  JSONB NOT NULL,              -- structured content read from the frame
    source_watermark TEXT,                       -- e.g. 'Screener.in', 'Trendlyne' if visible
    vision_model    TEXT,                        -- which model extracted it
    confidence      NUMERIC(3,2),                -- vision model's confidence

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_visual_content ON visual_evidence (content_piece_id);
CREATE INDEX IF NOT EXISTS idx_visual_timestamp ON visual_evidence (content_piece_id, timestamp_seconds);

-- =====================================================
-- claims — link to supporting visual evidence (phase two)
-- =====================================================
-- A claim extracted from the transcript can point to the visual evidence
-- that backs it, matched by timestamp. Null in MVP.
ALTER TABLE claims
    ADD COLUMN IF NOT EXISTS supporting_visual_id UUID REFERENCES visual_evidence(id);

-- Also store the timestamp in the source video where this claim was spoken,
-- so timestamp-matching to visual evidence is possible later.
ALTER TABLE claims
    ADD COLUMN IF NOT EXISTS spoken_at_seconds INTEGER;
