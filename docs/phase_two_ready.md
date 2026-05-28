# Phase-Two-Ready Changes

This document explains the changes made to accommodate future visual +
Whisper extraction without rebuilding. **You do not build the capability now.**
You only run the migration so the database is ready.

## What changed and why

The goal: design the seams now so phase-two enrichment slots in cleanly.
See `docs/decision_log.md` (entry dated 2026-05-28) for the full rationale.

### Database (run this now)

A new migration `migrations/002_enrichment_ready.sql` adds:

- On `content_pieces`: `transcript_source`, `transcript_confidence`,
  `visual_data`, `has_visual_extraction`, `enrichment_tier`,
  `enrichment_metadata`
- New table `visual_evidence` (empty until phase two)
- On `claims`: `supporting_visual_id`, `spoken_at_seconds`

Run it:

```bash
python -m scripts.run_migrations
```

This is safe — all new fields have defaults, nothing breaks existing code.

### Code (already in place, nothing to run)

- `src/ingestion/capture_types.py` — defines `TranscriptResult` and
  `VisualData`, the consistent shapes every capture method returns
- `src/ingestion/capture_base.py` — abstract `TranscriptCapture` and
  `VisualCapture` interfaces, plus a no-op `NullVisualCapture` for the MVP
- `src/ingestion/youtube_capture.py` — MVP transcript capture implementing
  the interface (assigns lower confidence to pre-2021 videos)
- `src/ingestion/whisper_capture.py` — phase-two stub with full build plan
- `src/ingestion/visual_capture.py` — phase-two stub with full build plan
- `src/ingestion/youtube.py` — `ingest_video` refactored to use the swappable
  capture and store enrichment fields

### Config (already in place)

`src/config/settings.py` and `.env.example` now have enrichment flags, all
default OFF: `enable_whisper`, `enable_visual_extraction`, plus tier-selection
thresholds. Leave them off for the MVP.

## What you do NOT do now

- Do not implement Whisper capture
- Do not implement visual extraction
- Do not download any video/audio files
- Do not turn on the enrichment flags
- Do not spend money on vision AI or Whisper

## When phase two arrives (post-funding)

1. Implement `WhisperCapture.capture()` per its docstring plan
2. Implement `VisualFrameCapture.capture()` per its docstring plan
3. Add a capture-selection step in the orchestrator that picks the method
   based on expert score + content signals + settings flags
4. Add a timestamp-pairing step that links claims to visual_evidence
5. Flip `enable_whisper` / `enable_visual_extraction` on for chosen content
6. Backfill high-value old content with the full treatment

Everything downstream (extraction, quality filter, validation, synthesis)
already works because it only depends on the stored content, not on how it
was captured.
