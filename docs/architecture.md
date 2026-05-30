# Architecture

This document describes how the six layers of the product fit together.
For the broader product thesis and business strategy, see the execution
document. This file is for engineers (including future you) understanding
the system.

## Data flow

```
[YouTube / Blogs / Podcasts]
            │
            ▼  (src/ingestion/)
    [content_pieces table]   ◄──── raw text + metadata
            │
            ▼  (src/extraction/)
    [claims table — pending quality]
            │
            ▼  (src/quality/)
    [claims table — verdict assigned]
            │
            ▼  (src/synthesis/)        ┌───────────────────────────┐
    [briefings table]                  │ src/validation/ runs daily│
            │                          │ against market_data table │
            ▼                          │ updates expert scores     │
        [UI / API]                     └───────────────────────────┘
```

## Layer responsibilities

### Layer 1 — Ingestion (`src/ingestion/`)

- One submodule per source type: `youtube.py`, `blog.py`, `podcast.py`
- Each submodule exposes an `ingest_<source>(expert_id, ...)` function
- Stores raw content in `content_pieces` table with full text + metadata
- Idempotent — re-running does not duplicate
- Orchestrator: `run_all.py` runs all source ingestions for active experts

### Layer 2 — Extraction (`src/extraction/`)

- Reads unprocessed rows from `content_pieces` (where `processed_at` is null)
- For each, calls Claude API with `claim_extraction_v2` prompt
- Parses returned JSON, validates against expected schema
- Inserts rows into `claims` table with `quality_verdict = null`
- Sets `processed_at` on the source content piece

### Layer 3 — Quality filter (`src/quality/`)

- Reads claims where `quality_verdict is null`
- For each, calls Claude API with `quality_filter_v1` prompt
- Stores the 5-dimension scores + overall verdict on the claim row
- Pass = enters active pool. Partial = lower weight. Fail = excluded.

### Layer 4 — Validation (`src/validation/`)

- Daily cron job at 2am
- Queries claims with `verification_status = 'pending'` AND `target_date <= today`
- Pulls relevant market data from `market_data` table
- Applies validation logic based on claim type:
  - Directional: compare returns over the period
  - Price target: did the price touch the target within window (with 5/10% tolerance)
  - Macro: compare rate/level on claim date vs target date
- Updates `verification_status` and `verification_outcome` on the claim
- Triggers expert credibility score recalculation via `src/internal/scoring.py`

### Layer 5 — Synthesis (`src/synthesis/`)

- `convergence.py` — compares reasoning chains across claims with similar conclusions
- `briefing.py` — generates the morning briefing from filtered + validated claims
- `query.py` — handles ad-hoc user queries against the intelligence corpus
- Uses Claude Opus for final synthesis (higher quality, used selectively)

### Layer 6 — Output (`app.py` and `src/api/`)

- Streamlit UI for early stage (single file `app.py`)
- FastAPI backend for query interface (built later when needed)

## Key tables

| Table                 | Purpose                                                                 |
| --------------------- | ----------------------------------------------------------------------- |
| `experts`             | The people/channels we ingest from, with credibility scores             |
| `content_pieces`      | Raw ingested content (transcripts, articles)                            |
| `claims`              | Structured extracted claims with quality verdicts and validation status |
| `market_data`         | Daily price/macro snapshots used for validation                         |
| `briefings`           | Generated morning intelligence outputs (one per day)                    |
| `decision_influences` | Log of when the product influenced a real investment decision           |

## What lives where

- All Claude API calls → `src/llm/claude_client.py`
- All DB access → `src/db/queries.py`
- All settings → `src/config/settings.py`
- All prompts → `prompts/*.md` (versioned)
- Proprietary scoring → `src/internal/` (not for public sharing)

## Build order

1. Layer 1 (ingestion) → see `src/ingestion/youtube.py` as the working example
2. Layer 2 (extraction) → next to build
3. Layer 3 (quality filter) → blocks before synthesis
4. Layer 4 (validation) → can be built in parallel once schema is set
5. Layer 5 (synthesis) → requires layers 1-3 working
6. Layer 6 (UI) → last, on top of working layers below

Each layer should have its own tests before moving to the next.
